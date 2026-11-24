#!/usr/bin/env python3
"""
intent.py — do intent à spec (v0.16, 2026-11-24): o owner escreve meia página, o Claude rascunha a
`spec.md`, o `op.json` e os acceptance tests, e o rascunho só voa depois que o owner aprova.

O gargalo costuma ser o owner escrevendo a spec (o tempo que a v0.10 passou a medir). O
whitepaper de spec-driven development do Google e o *AI-native SDLC playbook* da Anthropic dizem o
mesmo: o humano fixa a intenção e o critério; o modelo transforma isso em especificação verificável, e
o humano revisa. Aqui:

  1. o owner escreve `ops/<op>/intent.md` (`process/INTENT-template.md`): Target, Problema, Resultado
     esperado, Restrições, Perguntas em aberto;
  2. `intent.py --op ops/<op>` roda o `claude -p` com o intent, o template da spec, a policy, os
     guardrails e o REVIEW.md do target, e o `op.json` da op mais recente do mesmo target como molde
     (cabeamento: venv, gate, workspace). Ele LÊ o código do target e ESCREVE só na pasta da op;
  3. o harness confere de fora: o target não mudou (git status antes e depois) e a pasta da op só ganhou
     `spec.md`, `op.json` e `acceptance/`; o rascunho passa pelo `e9_run.py --dry-run` (policy,
     guardrails, traceability FR × teste). Se não passar, o mesmo agent recebe o erro e corrige, até
     `--tentativas` vezes;
  4. o `op.json` sai com `"draft": true`, e o `e9_run.py --live` RECUSA rascunho (rc 1). O owner lê,
     corrige o que quiser e aprova: `intent.py --op ops/<op> --aprovar --spec-min 15`, que tira o
     `draft`, grava os minutos de spec (v0.10) e confere o dry run de novo.

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
import owner  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
OPS = RAIZ / "ops"
TARGETS = RAIZ / "targets"
E9_RUN = Path(__file__).resolve().parent / "e9_run.py"
SECOES = ("Problema", "Resultado esperado", "Restrições", "Perguntas em aberto")
OBRIGATORIAS = ("Problema", "Resultado esperado")
MODELO = "claude-opus-5-5"
TIMEOUT_S = 30 * 60
# Onde o agent do rascunho pode escrever, relativo à pasta da op. O resto é recusado.
PERMITIDOS = ("spec.md", "op.json", "acceptance/")
# O comando do rascunho: lê o target (--add-dir), escreve na pasta da op (cwd), sem Bash.
CMD = ('claude -p "$(cat {brief})" --output-format stream-json --verbose --add-dir {repo} '
       '--permission-mode acceptEdits --allowedTools Read Grep Glob Write Edit '
       '--disallowedTools Bash --model {modelo}')


def ler_intent(path: Path) -> dict:
    """As seções do intent.md e o Target. Levanta ValueError se faltar o que é obrigatório."""
    texto = Path(path).read_text(encoding="utf-8")
    m = re.search(r"^\*\*Target:\*\*\s*`?([\w.-]+)`?", texto, re.MULTILINE)
    secoes = {}
    for nome in SECOES:
        s = re.search(r"^##\s*" + re.escape(nome) + r"\s*$(.*?)(?=^##\s|\Z)", texto, re.MULTILINE | re.DOTALL)
        secoes[nome] = s.group(1).strip() if s else ""
    faltam = [n for n in OBRIGATORIAS if not secoes[n]]
    if faltam:
        raise ValueError(f"o intent.md não tem {', '.join(faltam)} (seção vazia ou ausente)")
    if not m or m.group(1).startswith("<"):
        raise ValueError("o intent.md não diz o **Target:** (o nome do arquivo em targets/)")
    return {"target": m.group(1), "secoes": secoes, "texto": texto}


def molde(target: str, exceto: Path | None = None) -> tuple[Path | None, dict]:
    """O `op.json` da op mais recente do mesmo target, como molde do cabeamento (venv, gate, workspace)."""
    for d in sorted((p for p in OPS.iterdir() if p.is_dir() and p != exceto), reverse=True):
        try:
            cfg = json.loads((d / "op.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if compat.cfg(cfg).get("target") == target and not cfg.get("draft"):
            return d, cfg
    return None, {}


def _ler(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def briefing(op_dir: Path, intent: dict, molde_dir: Path | None, molde_cfg: dict, repo: str) -> str:
    alvo = intent["target"]
    return "\n".join([
        "# RASCUNHO DE SPEC (E-9 kit, v0.16)", "",
        "Você vai transformar o INTENT do owner, abaixo, numa op do harness pronta para revisão. Você não",
        "implementa nada: escreve a especificação e os testes que provarão a implementação.", "",
        f"- Leia o código do target em `{repo}` (só leitura: o E-9 confere com git status, e mudar qualquer",
        "  arquivo dele recusa o rascunho). Leia o que o intent toca antes de escrever: o que o código já faz",
        "  muda o que a spec pede (numa op real, ler antes evitou refazer uma nova tentativa que já existia).",
        "- Escreva SÓ na pasta atual (a da op): `spec.md`, `op.json` e `acceptance/`. Nada mais.",
        "- `spec.md` no formato do SPEC-TEMPLATE abaixo, com: uma seção `## Perguntas ao owner` logo no topo,",
        "  com cada pergunta em aberto do intent e cada decisão que você teve de supor (marque `SUPOSIÇÃO:`);",
        "  a seção `## Requirements` com `- **FR-001** — ...`; a seção `## Guardrails` com UMA linha por",
        "  guardrail do target (`[ID] aplica: como` ou `[ID] não se aplica: porquê`).",
        "- `acceptance/<caminho no target>/test_aceite_<nome>.py`: um teste por requirement, com o ID no nome",
        "  (`def test_fr_001_...`), que FALHA no código de hoje e passa quando a op estiver feita. Importe o",
        "  código novo DENTRO de cada teste (import no topo quebra a coleta). Requirement que nenhum teste",
        "  prova diz na própria linha: `(sem aceite: porquê)`.",
        "- `op.json`: comece do MOLDE abaixo (cabeamento do target: target_repo, venv, gate, workspace,",
        "  worktree_link, agent.cmd_template) e troque op, branch (`feat/...`), worktree, commit.subject,",
        "  acceptance.cmd, expected_artifacts e optional_artifacts. Ponha `\"draft\": true` e",
        "  `\"risk\": \"baixo|medio|alto\"` (regra de negócio ou segurança é alto). Nada de `git push`.",
        "- O escopo do commit (expected + optional) tem de ser o escopo de verdade: o gate de plano recusa",
        "  arquivo fora dele.",
        "- Não responda as perguntas em aberto pelo owner: devolva-as.", "",
        "Ao terminar, responda com um resumo curto: os requirements, os arquivos que escreveu, e as perguntas.",
        "", "=" * 60, "# INTENT DO OWNER", "", intent["texto"],
        "", "=" * 60, "# SPEC-TEMPLATE", "", _ler(RAIZ / "process" / "SPEC-template.md"),
        "", "=" * 60, "# POLICY", "", _ler(RAIZ / "process" / "POLICY.md"),
        "", "=" * 60, f"# GUARDRAILS DO TARGET ({alvo})", "", _ler(TARGETS / f"{alvo}.md") or "(nenhum)",
        "", "=" * 60, "# REVIEW.md DO TARGET", "", _ler(TARGETS / f"{alvo}.review.md") or "(nenhum)",
        "", "=" * 60, f"# MOLDE DO op.json ({molde_dir.name if molde_dir else 'nenhuma op anterior deste target'})", "",
        json.dumps(molde_cfg, indent=2, ensure_ascii=False) if molde_cfg else "(escreva do zero)", ""])


def _estado(repo: str) -> str:
    p = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=repo, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    return p.stdout


def _arquivos(op_dir: Path) -> set[str]:
    return {p.relative_to(op_dir).as_posix() for p in op_dir.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}


def fora_do_permitido(antes: set[str], depois: set[str]) -> list[str]:
    novos = depois - antes
    return sorted(a for a in novos if not any(a == p or a.startswith(p) for p in PERMITIDOS)
                  and not a.startswith("_rascunho"))


def dry_run(op_dir: Path) -> tuple[bool, str]:
    p = subprocess.run([sys.executable, str(E9_RUN), "--op", str(op_dir), "--dry-run"], cwd=RAIZ,
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    ok = p.returncode == 0 and "validação OK" in p.stdout
    return ok, "ok" if ok else (p.stderr.strip() or p.stdout.strip())[-1500:]


def _resultado(saida: str) -> dict:
    for ln in reversed(saida.splitlines()):
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "result":
            return ev
    return {}


def _rodar(cmd: str, cwd: Path, log: Path, timeout: int) -> dict:
    with open(log, "a", encoding="utf-8") as h:
        p = subprocess.run(cmd, shell=True, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        h.write(p.stdout + p.stderr)
    return _resultado(p.stdout)


def rascunhar(op_dir: Path, *, repo: str | None = None, modelo: str = MODELO, tentativas: int = 2,
              cmd: str = CMD, timeout: int = TIMEOUT_S) -> dict:
    """O rascunho: brief → claude -p → conferência de fora → dry run, repetindo com o erro."""
    op_dir = Path(op_dir).resolve()
    intent = ler_intent(op_dir / "intent.md")
    if (op_dir / "spec.md").exists() or (op_dir / "op.json").exists():
        raise ValueError("a op já tem spec.md ou op.json: o rascunho só nasce numa pasta com o intent")
    md, mc = molde(intent["target"], op_dir)
    repo = repo or compat.cfg(mc).get("target_repo")
    if not repo or not Path(repo).is_dir():
        raise ValueError(f"não achei o clone do target ({repo!r}): diga --repo")
    brief = op_dir / "_rascunho_brief.txt"
    brief.write_text(briefing(op_dir, intent, md, mc, repo), encoding="utf-8")
    log = op_dir / "_rascunho.jsonl"
    r = {"target": intent["target"], "molde": md.name if md else None, "modelo": modelo, "tentativas": [],
         "custo": 0.0}
    antes_repo, antes_op = _estado(repo), _arquivos(op_dir)
    sessao = None
    for n in range(1, max(1, tentativas) + 1):
        c = cmd.format(brief=str(brief), repo=repo, modelo=modelo)
        if sessao:
            c = c.replace("claude -p", f"claude --resume {sessao} -p", 1)
        ev = _rodar(c, op_dir, log, timeout)
        sessao = ev.get("session_id") if re.fullmatch(r"[0-9a-fA-F-]{8,64}", str(ev.get("session_id") or "")) else None
        r["custo"] = round(r["custo"] + (ev.get("total_cost_usd") or 0), 4)
        t = {"n": n, "is_error": ev.get("is_error"), "resumo": (ev.get("result") or "")[-1500:]}
        r["tentativas"].append(t)
        if _estado(repo) != antes_repo:
            r["veredito"] = "RECUSADO: o rascunho mexeu no target (git status mudou) — confira o clone à mão"
            return r
        fora = fora_do_permitido(antes_op, _arquivos(op_dir))
        if fora:
            r["veredito"] = f"RECUSADO: o rascunho escreveu fora de spec.md, op.json e acceptance/: {fora}"
            return r
        if ev.get("is_error") or not ev:
            r["veredito"] = "RECUSADO: o agent do rascunho terminou em erro ou sem resultado"
            return r
        ok, motivo = (False, "o rascunho não escreveu spec.md e op.json") if not (
            (op_dir / "spec.md").exists() and (op_dir / "op.json").exists()) else dry_run(op_dir)
        t["dry_run"] = motivo
        if ok:
            break
        brief.write_text("# CORREÇÃO DO RASCUNHO (E-9)\n\nO dry run do harness recusou o rascunho:\n\n```\n"
                         + motivo + "\n```\n\nCorrija os arquivos da pasta da op (só eles) e responda de "
                         "novo com o resumo.\n", encoding="utf-8")
    try:
        cfg = json.loads((op_dir / "op.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = None
    if cfg is not None and cfg.get("draft") is not True:          # o rascunho é rascunho, diga o agent o que disser
        cfg["draft"] = True
        (op_dir / "op.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
                                        newline="\n")
    ultima = r["tentativas"][-1]
    r["veredito"] = ("RASCUNHO PRONTO para revisão: dry run ok, `\"draft\": true` no op.json"
                     if ultima.get("dry_run") == "ok" else
                     f"RASCUNHO COM FURO: o dry run segue recusando depois de {len(r['tentativas'])} tentativa(s)")
    r["em"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (op_dir / "_rascunho.json").write_text(json.dumps(r, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
                                           newline="\n")
    return r


def aprovar(op_dir: Path, spec_min=None) -> tuple[bool, str]:
    """O owner revisou: tira o `draft`, grava os minutos de spec e confere o dry run de novo."""
    op_dir = Path(op_dir)
    p = op_dir / "op.json"
    cfg = json.loads(p.read_text(encoding="utf-8"))
    if not cfg.get("draft"):
        return False, "a op não está em rascunho"
    cfg.pop("draft", None)
    if spec_min is not None:
        cfg["owner"] = {**(cfg.get("owner") or {}), "spec_min": owner.minutos(spec_min, "--spec-min")}
    rasc = op_dir / "_rascunho.json"
    if rasc.exists():
        r = json.loads(rasc.read_text(encoding="utf-8"))
        cfg["_rascunho"] = {"modelo": r.get("modelo"), "custo": r.get("custo"), "tentativas": len(r.get("tentativas") or []),
                            "em": r.get("em"), "aprovado_em": datetime.now(timezone.utc).date().isoformat()}
    p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    ok, motivo = dry_run(op_dir)
    return ok, "aprovada: o dry run passa e o --live aceita" if ok else f"aprovada, mas o dry run recusa: {motivo}"


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="E-9 — do intent.md à spec, ao op.json e aos acceptance tests")
    ap.add_argument("--op", required=True, help="pasta da op, com o intent.md")
    ap.add_argument("--repo", help="o clone do target a ler (padrão: o target_repo do molde)")
    ap.add_argument("--modelo", default=MODELO)
    ap.add_argument("--tentativas", type=int, default=2)
    ap.add_argument("--aprovar", action="store_true", help="o owner revisou: tira o draft")
    ap.add_argument("--spec-min", help="com --aprovar: os minutos do owner na spec (v0.10)")
    a = ap.parse_args(argv)
    try:
        if a.aprovar:
            ok, msg = aprovar(Path(a.op), a.spec_min)
            print(f"[E-9 INTENT] {msg}")
            return 0 if ok else 1
        r = rascunhar(Path(a.op), repo=a.repo, modelo=a.modelo, tentativas=a.tentativas)
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        print(f"[E-9 INTENT] recusado: {e}", file=sys.stderr)
        return 2
    print(f"[E-9 INTENT] {r['veredito']} · {len(r['tentativas'])} tentativa(s) · US$ {r['custo']}")
    print(r["tentativas"][-1].get("resumo", "")[-800:])
    return 0 if r["veredito"].startswith("RASCUNHO PRONTO") else 1


if __name__ == "__main__":
    sys.exit(main())
