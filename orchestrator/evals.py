#!/usr/bin/env python3
"""
evals.py — os evals do próprio harness (v0.15, 2026-11-24): reexecuta ops reais, de resultado conhecido,
contra o commit de base delas, e compara com o run original.

O harness mede cada run, e nada media o próprio HARNESS: uma troca de modelo, de briefing ou de guardrail podia
piorar o conjunto sem ninguém ver, porque cada op voa uma vez só. O *AI-native SDLC playbook* da
Anthropic pede uma suíte de 20 a 50 tarefas reais, rodada quando o harness muda; as suas são as ops que
já rodaram, com spec, acceptance tests, o resultado do gate e o desfecho na revisão. Este script:

  1. escolhe as ops elegíveis: run real (não treino nem smoke), com o `--dry-run` passando hoje e com o
     commit de base conhecido (o `base_commit` do metrics, desde a v0.15; senão o pai do commit da
     entrega; senão o `main` do target no início do run original);
  2. copia cada uma para `evals/<id>/<op>/` (spec, op.json, acceptance), com branch, worktree e base
     próprios do eval, sem aviso no celular;
  3. roda o `e9_run.py --live` em cada cópia, uma por vez, e para quando o custo passa do teto;
  4. apaga a worktree e a branch do eval no target (nada fica para mergear: é medida, não entrega);
  5. grava `evals/<id>.json` com o run original ao lado do eval — rc, custo, tempo, acceptance, gate e o
     "entrou como veio" do original — e o dashboard mostra o mais recente.

É caro (um run de verdade por op), então só roda sob demanda.

Uso (na máquina onde vivem os clones dos targets):
  python3 orchestrator/evals.py --listar
  python3 orchestrator/evals.py --op <op> --op <outra> --dry-run
  python3 orchestrator/evals.py --op <op> --op <outra> --live --teto-usd 5

Stdlib apenas.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compat  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
OPS = RAIZ / "ops"
EVALS = RAIZ / "evals"
E9_RUN = Path(__file__).resolve().parent / "e9_run.py"
TETO_USD = 5.0


def _git(args, cwd) -> tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout + p.stderr).strip()


def _json(p: Path) -> dict:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _treino(d: Path, cfg: dict) -> bool:
    return bool(cfg.get("treino")) or "smoke" in d.name


def resumo_do_run(m: dict) -> dict:
    """O que se compara de um run: rc, custo (agente + plano), tempo, gate, acceptance e revisão."""
    m = compat.metrics(m)
    rc = m.get("rc")
    if rc is None and m.get("status"):               # run de antes da v0.4.2: o rc vem do status
        s = m["status"].lower()
        rc = (0 if s.startswith("ok") else 3 if ("gate vermelho" in s or "overwatch vermelho" in s)
              else 4 if "commit falhou" in s else None)
    custo = (m.get("agent") or {}).get("total_cost_usd")
    if (m.get("plan") or {}).get("custo") is not None:
        custo = (custo or 0) + m["plan"]["custo"]
    ac = m.get("acceptance") or {}
    return {"rc": rc, "custo": round(custo, 2) if isinstance(custo, (int, float)) else None,
            "min": round(m["wall_clock_s"] / 60, 1) if m.get("wall_clock_s") else None,
            "gate": m.get("gate_pass"), "acceptance": ac.get("veredito"),
            "desfecho": (m.get("review") or {}).get("desfecho"),
            "plano": (m.get("plan") or {}).get("veredito"),
            "veto_sombra": (m.get("veto_sombra") or {}).get("teria_segurado"),
            "status": (m.get("status") or "")[:200]}


def base_da_op(d: Path, cfg: dict, m: dict) -> tuple[str | None, str]:
    """(commit de base, de onde veio). None se não der para saber — a op fica fora da amostra."""
    if m.get("base_commit"):
        return m["base_commit"], "base_commit do metrics"
    repo = cfg.get("target_repo")
    if not repo or not Path(repo).is_dir():
        return None, "target_repo ausente nesta máquina"
    if m.get("commit_sha"):
        rc, out = _git(["rev-parse", m["commit_sha"] + "^"], repo)
        if rc == 0:
            return out.splitlines()[-1], f"pai do commit da entrega {m['commit_sha']}"
    ini = m.get("started_at")
    if ini:
        rc, out = _git(["rev-list", "-1", f"--before={ini}", "main"], repo)
        if rc == 0 and out:
            return out.splitlines()[-1], f"main em {ini}"
    return None, "sem commit nem data do run original"


def dry_run(op_dir: Path) -> tuple[bool, str]:
    p = subprocess.run([sys.executable, str(E9_RUN), "--op", str(op_dir), "--dry-run"], cwd=RAIZ,
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    ok = p.returncode == 0 and "validação OK" in p.stdout
    return ok, "ok" if ok else (p.stderr.strip() or p.stdout.strip())[-200:]


def elegiveis(validar=True) -> list[dict]:
    """A amostra possível: cada op real com o que se sabe dela, e o motivo de quem fica fora."""
    saida = []
    for d in sorted(p for p in OPS.iterdir() if p.is_dir()):
        m = _json(d / "metrics.json")
        cfg = compat.cfg(_json(d / "op.json"))
        if not m.get("status") or not cfg or _treino(d, cfg):
            continue
        base, origem = base_da_op(d, cfg, m)
        ok, motivo = dry_run(d) if validar else (True, "não validado")
        saida.append({"op": d.name, "base": base, "base_origem": origem,
                      "elegivel": bool(base) and ok, "motivo": None if base and ok else (origem if not base else motivo),
                      "com_acceptance": compat.acceptance_dir(d).is_dir(), "original": resumo_do_run(m)})
    return saida


def _slug(nome: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", nome.lower())[-40:].strip("-")


def preparar(op: str, ev_id: str, base: str) -> Path:
    """A cópia da op para o eval: spec, op.json e acceptance; branch, worktree e base próprios."""
    src = OPS / op
    dst = EVALS / ev_id / op
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(compat.spec_path(src), dst / compat.spec_path(src).name)
    ac = compat.acceptance_dir(src)
    if ac.is_dir():
        shutil.copytree(ac, dst / ac.name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    bruto = _json(src / "op.json")
    wt_pai = Path(compat.cfg(bruto).get("worktree", "/tmp/x")).parent
    bruto.update({"branch": f"feat/e9-eval-{ev_id}-{_slug(op)}",
                  "worktree": str(wt_pai / f"eval-{ev_id}-{_slug(op)}"),
                  "base_ref": base, "aviso": False, "eval": {"id": ev_id, "op_original": op}})
    (dst / "op.json").write_text(json.dumps(bruto, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
                                 newline="\n")
    return dst


def limpar(dst: Path) -> list[str]:
    """Tira a worktree e a branch do eval do target: o eval mede, não entrega."""
    cfg = compat.cfg(_json(dst / "op.json"))
    repo, wt, br = cfg.get("target_repo"), cfg.get("worktree"), cfg.get("branch", "")
    feito = []
    if not br.startswith("feat/e9-eval-"):           # só a branch que o próprio eval criou
        return feito
    if wt and Path(wt).exists():
        rc, out = _git(["worktree", "remove", "--force", wt], repo)
        feito.append(f"worktree {'removida' if rc == 0 else 'NÃO removida: ' + out[:120]}")
    rc, out = _git(["branch", "-D", br], repo)
    feito.append(f"branch {'apagada' if rc == 0 else 'NÃO apagada: ' + out[:120]}")
    return feito


def comparar(itens: list[dict]) -> dict:
    """O agregado: quantas deram rc 0 no original e no eval, custo e tempo somados nas que rodaram."""
    rodaram = [i for i in itens if i.get("eval")]
    def soma(lado, k):
        v = [i[lado][k] for i in rodaram if isinstance(i[lado].get(k), (int, float))]
        return round(sum(v), 2) if v else None
    return {"rodaram": len(rodaram),
            "rc0_original": sum(1 for i in rodaram if i["original"]["rc"] == 0),
            "rc0_eval": sum(1 for i in rodaram if i["eval"]["rc"] == 0),
            "mesmo_rc": sum(1 for i in rodaram if i["original"]["rc"] == i["eval"]["rc"]),
            "piorou": [i["op"] for i in rodaram if i["original"]["rc"] == 0 and i["eval"]["rc"] != 0],
            "melhorou": [i["op"] for i in rodaram if i["original"]["rc"] != 0 and i["eval"]["rc"] == 0],
            "custo_original": soma("original", "custo"), "custo_eval": soma("eval", "custo"),
            "min_original": soma("original", "min"), "min_eval": soma("eval", "min")}


def rodar(ops: list[str], *, live: bool, teto_usd: float, manter: bool = False, ev_id: str | None = None) -> dict:
    ev_id = ev_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    todas = {e["op"]: e for e in elegiveis()}
    _, harness = _git(["rev-parse", "--short", "HEAD"], RAIZ)
    res = {"id": ev_id, "harness": harness, "inicio": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "modo": "live" if live else "dry-run", "teto_usd": teto_usd, "itens": []}
    gasto = 0.0
    for op in ops:
        e = todas.get(op)
        item = {"op": op, "original": (e or {}).get("original"), "base": (e or {}).get("base")}
        res["itens"].append(item)
        if not e or not e["elegivel"]:
            item["fora"] = (e or {}).get("motivo") or "não é uma op real com metrics nesta máquina"
            continue
        if gasto >= teto_usd:
            item["fora"] = f"teto de US$ {teto_usd:.2f} atingido antes desta op"
            continue
        dst = preparar(op, ev_id, e["base"])
        ok, motivo = dry_run(dst)
        if not ok:
            item["fora"] = f"a cópia não passa no dry run: {motivo}"
            continue
        if not live:
            item["pronta"] = str(dst.relative_to(RAIZ)) if dst.is_relative_to(RAIZ) else str(dst)
            continue
        print(f"[E-9 EVAL] {op} @ {e['base'][:10]} …", flush=True)
        p = subprocess.run([sys.executable, str(E9_RUN), "--op", str(dst), "--live"], cwd=RAIZ,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        (dst / "e9_run.log").write_text(p.stdout[-20000:] + p.stderr[-5000:], encoding="utf-8")
        item["eval"] = resumo_do_run(_json(dst / "metrics.json")) if (dst / "metrics.json").exists() else {
            "rc": p.returncode, "status": "sem metrics.json"}
        item["eval"]["rc_processo"] = p.returncode
        gasto += item["eval"].get("custo") or 0
        if not manter:
            item["limpeza"] = limpar(dst)
        print(f"[E-9 EVAL] {op}: original rc {item['original']['rc']} → eval rc {item['eval']['rc']}", flush=True)
    res["fim"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    res["agregado"] = comparar(res["itens"])
    if live:
        EVALS.mkdir(exist_ok=True)
        (EVALS / f"{ev_id}.json").write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n",
                                             encoding="utf-8", newline="\n")
    return res


def ultimo() -> dict | None:
    """O eval mais recente gravado, para o dashboard (`E9_EVALS_DIR` troca a pasta, nos testes)."""
    pasta = Path(os.environ.get("E9_EVALS_DIR") or EVALS)
    arqs = sorted(pasta.glob("*.json")) if pasta.is_dir() else []
    return _json(arqs[-1]) if arqs else None


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="E-9 — evals do harness: reexecuta ops reais e compara")
    ap.add_argument("--listar", action="store_true", help="a amostra elegível e quem fica fora")
    ap.add_argument("--op", action="append", default=[], help="op a reexecutar (repetível)")
    ap.add_argument("--todas", action="store_true", help="todas as elegíveis")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--live", action="store_true")
    ap.add_argument("--teto-usd", type=float, default=TETO_USD)
    ap.add_argument("--manter", action="store_true", help="não apaga worktree e branch do eval")
    a = ap.parse_args(argv)
    if a.listar:
        lista = elegiveis()
        for e in lista:
            o = e["original"]
            print(f"{'✅' if e['elegivel'] else '—'} {e['op']}: rc {o['rc']} · US$ {o['custo']} · {o['min']} min"
                  + (f" · base {e['base'][:10]} ({e['base_origem']})" if e["base"] else "")
                  + ("" if e["elegivel"] else f" · fora: {e['motivo']}"))
        print(f"\n{sum(e['elegivel'] for e in lista)} elegíveis de {len(lista)} runs reais; custo original "
              f"somado US$ {sum(e['original']['custo'] or 0 for e in lista if e['elegivel']):.2f}")
        return 0
    ops = [e["op"] for e in elegiveis(validar=False)] if a.todas else a.op
    if not ops:
        ap.error("diga --op, --todas ou --listar")
    res = rodar(ops, live=a.live, teto_usd=a.teto_usd, manter=a.manter)
    print(json.dumps(res["agregado"], ensure_ascii=False))
    for i in res["itens"]:
        print(f"  {i['op']}: " + (i.get("fora") or i.get("pronta") or
                                   f"rc {i['original']['rc']} → {i['eval']['rc']}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
