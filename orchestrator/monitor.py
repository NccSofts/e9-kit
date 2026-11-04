#!/usr/bin/env python3
"""
monitor.py — o Monitor E-9: o agent trabalhando, numa página do navegador, na sua máquina.

Lê o `agent.jsonl` de cada op em `ops/` (a saída `stream-json` do `claude -p` que o harness grava)
e mostra, ao vivo, o que o agent faz: arquivos lidos e editados, comandos, comentários, erros,
recusas de permissão e o relatório final. Quando o run termina, abre o run report na ordem da
prova: o veredito e o quality gate primeiro, o relatório do agent por último.

Uso:  python3 orchestrator/monitor.py              → http://127.0.0.1:8417
      python3 orchestrator/monitor.py --porta 9000
Rotas: /  (a página)  ·  /api/ops  ·  /api/placar  ·  /api/feed?op=<pasta>&pos=<byte>&ts=<último>

Por padrão escuta só em 127.0.0.1: a página mostra o código e os comandos do agent, e não deve
aparecer para a rede. `--host 0.0.0.0` abre para a rede local, por sua conta.

Stdlib apenas. Nunca escreve nada: só lê `ops/<op>/agent.jsonl` (ou o `operator.jsonl` das ops
antigas), `fases.json` e `metrics.json`, e o nome da op vem da listagem da pasta, nunca de um
caminho montado com o que o navegador mandou. O run em andamento é detectado pelo /proc, então
só em Linux e WSL; no Windows puro a página abre, mas não marca o run como em andamento.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compat  # noqa: E402 — os nomes antigos das ops de antes de 03/11

OPS = Path(__file__).resolve().parent.parent / "ops"
LOTE_MAX = 400 * 1024          # bytes por resposta: o navegador não engasga num transcript grande
TEXTO_MAX = 600


def _ops_dir() -> Path:
    return Path(os.environ.get("E9_OPS_DIR") or OPS)


def em_voo() -> set[str]:
    """Pastas de op com um `e9_run.py --live` vivo agora (lido do /proc, sem shell)."""
    vivas: set[str] = set()
    proc = Path("/proc")
    if not proc.is_dir():
        return vivas
    for p in proc.iterdir():
        if not p.name.isdigit():
            continue
        try:
            args = (p / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        partes = [a.decode("utf-8", "replace") for a in args if a]
        if any(a.endswith("e9_run.py") for a in partes) and "--live" in partes and "--op" in partes:
            i = partes.index("--op")
            if i + 1 < len(partes):
                vivas.add(Path(partes[i + 1]).name)
    return vivas


# O que cada desfecho quer dizer, com o código de saída do harness e o nome de mercado. A ordem
# das regras importa: "gate vermelho (... sem commit-como-concluído)" contém "commit", e
# procurar "commit" antes de "vermelho" mostraria runs de gate vermelho como BARRADA, que manda
# procurar a causa no lugar errado.
DESFECHOS = {
    "voo": ("RODANDO", None, "a op está rodando agora"),
    "verde": ("VERDE", 0, "gate verde e commit na branch — aguarda a revisão (code review) e o merge"),
    "aceite": ("ACCEPTANCE", None, "o aceite (acceptance tests) não cumpriu o contrato: já passava, nem rodou, "
               "foi mexido ou não passou inteiro"),
    "base": ("BASE", None, "a linha de base (baseline) já falhava no main, ou a worktree estava suja — "
             "o agente nem foi chamado"),
    "falha": ("FALHA", None, "o agente terminou em erro ou estourou o teto de tempo"),
    "vermelho": ("VERMELHO", 3, "o gate (quality gate) reprovou a entrega — ler o fim do gate e os desvios"),
    "barrada": ("BARRADA", 4, "gate verde, mas a árvore testada não é a que seria commitada: "
                "arquivo alterado fora do escopo"),
    "abort": ("ABORT", None, "a op parou antes do fim"),
    "sem": ("SEM run report", None, "não há metrics.json desta op"),
}
_RC_INFERIDO = (("aceite não instalado", 9), ("já passa", 9), ("não rodou como teste", 9),
                ("aceite alterado", 10), ("o aceite", 10), ("linha de base", 7),
                ("não está limpa", 8), ("timeout", 5), ("is_error", 6))


def classificar(status: str | None, voando: bool = False, rc: int | None = None) -> dict:
    """Desfecho de uma op a partir do status do harness: classe, rótulo, código e significado."""
    s = (status or "").lower()
    if voando:
        chave = "voo"
    elif not s:
        chave = "sem"
    elif s.startswith("ok"):
        chave = "verde"
    elif "aceite" in s:
        chave = "aceite"
    elif "linha de base" in s or "não está limpa" in s:
        chave = "base"
    elif "agente" in s and ("erro" in s or "timeout" in s):
        chave = "falha"
    elif "gate vermelho" in s:
        chave = "vermelho"
    elif "commit falhou" in s:
        chave = "barrada"
    else:
        chave = "abort"
    rotulo, rc_fixo, significado = DESFECHOS[chave]
    if rc is None:
        rc = rc_fixo
        if rc is None and chave not in ("voo", "sem"):
            rc = next((n for chave_rc, n in _RC_INFERIDO if chave_rc in s), None)
    return {"classe": chave, "rotulo": rotulo, "rc": rc, "significado": significado}


_ARQUIVOS_DA_OP = ("agent.jsonl", "operator.jsonl", "metrics.json", "baseline.log",
                   "acceptance-base.log", "aceite-base.log", "gate.log", "overwatch.log",
                   "acceptance-entrega.log", "aceite-entrega.log")


def _metrics(d: Path) -> dict:
    try:
        return compat.metrics(json.loads((d / "metrics.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def listar_ops() -> list[dict]:
    """As ops que já voaram, a mais recente primeiro. Desde 28/10 conta também quem só tem o
    metrics.json: a op que aborta antes do agente (linha de base, aceite que já passa) não deixa
    transcript, e era justamente ela que sumia do quadro."""
    base, vivas = _ops_dir(), em_voo()
    saida = []
    if not base.is_dir():
        return saida
    for d in base.iterdir():
        if not d.is_dir():
            continue
        tempos = [(d / f).stat().st_mtime for f in _ARQUIVOS_DA_OP if (d / f).exists()]
        if not tempos and d.name not in vivas:
            continue
        m = _metrics(d)
        status = m.get("status")
        saida.append({"op": d.name, "mtime": max(tempos or [time.time()]), "em_voo": d.name in vivas,
                      "status": status, "revisao": (m.get("review") or {}).get("desfecho"),
                      **classificar(status, d.name in vivas, m.get("rc"))})
    # A op rodando primeiro, depois pelo nome, que começa pela data. Pelo mtime a ordem mudava sozinha:
    # gravar a revisão de uma op antiga (revisar.py) a jogava para o topo do quadro.
    saida.sort(key=lambda o: (o["em_voo"], o["op"]), reverse=True)
    return saida


# A fase do run, pelo log que está sendo escrito: cada etapa do harness grava o seu, e o mais novo
# diz onde a op está (para run de harness sem `fases.json`). Sem isso o STATUS diria "agent
# trabalhando" também durante a baseline, em que agent nenhum foi chamado.
_FASES = (("baseline.log", "linha de base (baseline): o gate no main, antes do agente"),
          ("acceptance-base.log", "aceite na base: tem de falhar antes do agente"),
          ("agent.jsonl", "agente trabalhando"),
          ("gate.log", "gate (quality gate): conferindo a entrega"),
          ("acceptance-entrega.log", "aceite na entrega: tem de passar inteiro"))


_TEXTO_FASE = {"preparo": "conferindo a worktree", "base": _FASES[0][1], "aceite_base": _FASES[1][1],
               "agente": _FASES[2][1], "operador": _FASES[2][1], "gate": _FASES[3][1], "aceite_entrega": _FASES[4][1],
               "reviewer": "AI code review de outro modelo lendo a entrega",
               "commit": "commit na branch (só os arquivos do escopo)", "fim": "gravando o run report"}


def fase_atual(pasta: Path) -> dict:
    """A etapa em curso: {texto, desde, teto_s}. Desde a v0.5 o harness grava `fases.json` com o
    começo e o teto de cada etapa, e o Monitor mostra o cronômetro; sem ele (run de harness antigo),
    vale o log mais novo, sem cronômetro."""
    try:
        fases = json.loads((pasta / "fases.json").read_text(encoding="utf-8"))
        f = fases[-1]
        return {"texto": _TEXTO_FASE.get(f["fase"], f["fase"]), "desde": f.get("inicio"),
                "teto_s": f.get("teto_s"), "etapa": f["fase"]}
    except (OSError, ValueError, LookupError, TypeError, AttributeError):
        pass
    vistos = [((pasta / f).stat().st_mtime, txt) for f, txt in _FASES if (pasta / f).exists()]
    return {"texto": max(vistos)[1] if vistos else "montando a worktree", "desde": None,
            "teto_s": None, "etapa": None}


def _op_valida(nome: str) -> Path | None:
    """Só aceita um nome que exista na listagem: nada de `..`, barra ou caminho absoluto."""
    for o in listar_ops():
        if o["op"] == nome:
            return _ops_dir() / nome
    return None


def _curto(s: str) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= TEXTO_MAX else s[:TEXTO_MAX] + "…"


def _ferramenta(nome: str, inp: dict) -> str:
    if nome in ("Read", "Write", "Edit", "MultiEdit"):
        return f"{nome} {inp.get('file_path', '')}"
    if nome == "Bash":
        return "$ " + str(inp.get("command", ""))
    if nome in ("Grep", "Glob"):
        return f"{nome} {inp.get('pattern', '')} {inp.get('path', '')}".strip()
    return f"{nome} {json.dumps(inp, ensure_ascii=False)}"


def eventos(linha: str) -> list[dict]:
    """Uma linha do stream-json → zero ou mais eventos para a tela. Nunca levanta."""
    try:
        ev = json.loads(linha)
    except ValueError:
        return []
    if not isinstance(ev, dict):
        return []
    saida = _eventos_do(ev, ev.get("type"))
    if ev.get("timestamp"):                  # a hora real do evento, quando o transcript traz
        for e in saida:
            e["ts"] = ev["timestamp"]
    return saida


# Recusa de permissão em `claude -p`: ninguém aprova, o comando não roda e o agent segue sem
# saber por quê. Numa tarefa real foram 14, todas com "requires approval"; depois apareceram mais
# duas formas, que passavam como erro comum: a leitura fora da pasta liberada ("requested
# permissions ... haven't granted it yet") e o comando bloqueado ("was blocked. For security...").
_RECUSA = ("requires approval", "has been denied", "requested permissions", "haven't granted",
           "was blocked. for security")


def _uso(ln: str) -> tuple[str, int] | None:
    """(id da mensagem, tokens de saída) de um evento `assistant`. A mesma mensagem chega em vários
    eventos, um por bloco de conteúdo, com o mesmo `usage`: quem soma deduplica pelo id."""
    if '"usage"' not in ln:
        return None
    try:
        ev = json.loads(ln)
        msg = ev.get("message") or {}
        if ev.get("type") != "assistant" or not msg.get("id"):
            return None
        return msg["id"], int((msg.get("usage") or {}).get("output_tokens") or 0)
    except (ValueError, AttributeError, TypeError):
        return None


def _eventos_do(ev: dict, t) -> list[dict]:
    saida: list[dict] = []
    if t == "assistant":
        for b in (ev.get("message") or {}).get("content") or []:
            if b.get("type") == "tool_use":
                saida.append({"tipo": "acao", "texto": _curto(_ferramenta(b.get("name", ""), b.get("input") or {}))})
            elif b.get("type") == "text" and (b.get("text") or "").strip():
                saida.append({"tipo": "fala", "texto": _curto(b["text"])})
    elif t == "user":
        for b in (ev.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                c = b.get("content")
                c = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                recusa = any(r in c.lower() for r in _RECUSA)
                saida.append({"tipo": "recusa" if recusa else "erro", "texto": _curto(c)})
    elif t == "result":
        saida.append({"tipo": "fim", "erro": bool(ev.get("is_error")),
                      "texto": f"fim — {ev.get('num_turns')} turnos · US$ {ev.get('total_cost_usd')}",
                      "relatorio": (ev.get("result") or "")[:8000]})
    return saida


_TS_OK = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z?$")


def _ts_da_linha(ln: str) -> str | None:
    """O `timestamp` de topo de uma linha do stream-json, se houver."""
    if '"timestamp"' not in ln:
        return None
    try:
        ts = json.loads(ln).get("timestamp")
    except (ValueError, AttributeError):
        return None
    return ts if isinstance(ts, str) and _TS_OK.match(ts) else None


def ler_feed(pasta: Path, pos: int, ts: str | None = None) -> dict:
    """Eventos novos a partir do byte `pos`. Só consome linha inteira (a última pode estar sendo
    escrita); devolve a posição seguinte e, terminada a op, o status do harness.

    A hora: no stream-json só os eventos `user` (resultado de ferramenta) trazem `timestamp`; a
    ação e a fala do agente não. Cada evento sem hora herda a última vista — a do resultado
    anterior, o instante em que o agente retomou —, e ela atravessa os lotes pelo `ts` que o
    navegador devolve na pergunta seguinte."""
    jl = pasta / "agent.jsonl"
    if not jl.exists():                # op de antes de 03/11
        jl = pasta / "operator.jsonl"
    evs: list[dict] = []
    novo = pos
    ultimo = ts if ts and _TS_OK.match(ts) else None
    uso: dict[str, int] = {}
    try:
        tam = jl.stat().st_size
        if pos > tam:                 # o transcript recomeçou (re-run da mesma op)
            pos = novo = 0
            ultimo = None
        with open(jl, "rb") as h:
            h.seek(pos)
            bloco = h.read(LOTE_MAX)
        fim = bloco.rfind(b"\n")
        if fim >= 0:
            for ln in bloco[: fim + 1].decode("utf-8", "replace").splitlines():
                ultimo = _ts_da_linha(ln) or ultimo
                if u := _uso(ln):
                    uso[u[0]] = max(uso.get(u[0], 0), u[1])
                for e in eventos(ln):
                    if ultimo and "ts" not in e:
                        e["ts"] = ultimo
                    evs.append(e)
            novo = pos + fim + 1
    except OSError:
        pass
    voando = pasta.name in em_voo()
    m = _metrics(pasta)               # o harness grava ao fim; num re-run, é o do run anterior
    return {"eventos": evs, "pos": novo, "ts": ultimo, "uso": uso, "em_voo": voando,
            "fase": fase_atual(pasta) if voando else None,
            "aar": aar(m) if m and not voando else None}


def desvios(relatorio: str) -> str | None:
    """A seção `## Desvios da spec` do relatório do agente, sem o título; None se não houver."""
    m = re.search(r"^(#{1,4})\s*" + compat.SECAO_DESVIOS + r"\s*$(.*?)(?=^#{1,4}\s|\Z)", relatorio or "",
                  re.IGNORECASE | re.MULTILINE | re.DOTALL)
    return m.group(2).strip() if m else None


def _treino(d: Path) -> bool:
    """Run de treino (smoke em repo descartável) não entra no placar: mede o harness, não o agente."""
    try:
        cfg = json.loads((d / "op.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    return bool(cfg.get("treino")) or "smoke" in d.name


def placar() -> dict:
    """O placar dos runs (engineering metrics): só runs reais, o desfecho no gate e no target, o
    custo e o tempo. "Entrou como veio" sai do campo `review` (orchestrator/revisar.py), gravado
    por quem revisou: é julgamento de quem faz o merge, não dedução do git."""
    base = _ops_dir()
    voos = []
    for d in (sorted(base.iterdir()) if base.is_dir() else []):
        m = _metrics(d) if d.is_dir() else {}
        if m.get("status") and not _treino(d):
            voos.append(m)
    n = len(voos)
    custos = [v["agent"]["total_cost_usd"] for v in voos
              if (v.get("agent") or {}).get("total_cost_usd") is not None]
    tempos = [v["wall_clock_s"] for v in voos if v.get("wall_clock_s")]
    com_gate = [v for v in voos if v.get("gate") is not None]
    rot: dict[str, int] = {}
    for v in voos:
        r = classificar(v.get("status"), False, v.get("rc"))["rotulo"]
        rot[r] = rot.get(r, 0) + 1
    # Verde DE PRIMEIRA: o primeiro run da op (o mais antigo em previous_runs) deu rc 0. O rc
    # passa por classificar(): run de harness antigo não tem `rc` gravado, só o status.
    def _rc(x: dict):
        return classificar(x.get("status"), False, x.get("rc"))["rc"]
    primeira = sum(1 for v in voos if _rc((v.get("previous_runs") or [None])[0] or v) == 0)
    revoos = sum(1 for v in voos if v.get("previous_runs"))
    rev = {"como_veio": 0, "consertado": 0, "descartado": 0, "sem": 0}
    for v in voos:
        rev[(v.get("review") or {}).get("desfecho") or "sem"] += 1
    return {"voos": n, "desfechos": rot, "revisao": rev, "verde_de_primeira": primeira, "revoos": revoos,
            "custo_total": round(sum(custos), 2), "custo_medio": round(sum(custos) / len(custos), 2) if custos else None,
            "voo_medio_min": round(sum(tempos) / len(tempos) / 60, 1) if tempos else None,
            "divergiu": sum(1 for v in com_gate if v.get("divergencias")), "com_gate": len(com_gate)}


def etapas(fases: list) -> list[dict]:
    """Quanto cada etapa levou, da marca dela até a seguinte (v0.5). Preparo e fim não contam."""
    saida = []
    for f, prox in zip(fases, fases[1:]):
        try:
            ini = datetime.fromisoformat(f["inicio"].replace("Z", "+00:00"))
            fim = datetime.fromisoformat(prox["inicio"].replace("Z", "+00:00"))
        except (KeyError, ValueError, AttributeError):
            continue
        if f.get("fase") not in ("preparo", "fim"):
            saida.append({"fase": f["fase"], "s": int((fim - ini).total_seconds()), "teto_s": f.get("teto_s")})
    return saida


def aar(m: dict) -> dict:
    m = compat.metrics(m)
    """O run report (post-mortem) para a tela, NA ORDEM da prova: o veredito e o
    gate primeiro, o relatório do agente por último. É a lição dos primeiros runs reais — o relatório
    é declaração, o gate é a prova — posta na hierarquia da página."""
    op = m.get("agent") or {}
    relatorio = op.get("result_text") or ""
    return {
        **classificar(m.get("status"), False, m.get("rc")),
        "status": m.get("status"),
        "branch": m.get("branch"),
        "commit": m.get("commit_sha"),
        "arquivos": m.get("arquivos") or [],
        "baseline": {"pass": m.get("baseline_pass"), "gate": m.get("baseline_gate")},
        "gate": m.get("gate"),
        "gate_pass": m.get("gate_pass"),
        "divergencias": m.get("divergencias") or [],
        "aceite": m.get("acceptance"),
        "revisor": m.get("reviewer"),
        "desvios": desvios(relatorio),
        "custo": op.get("total_cost_usd"),
        "turnos": op.get("num_turns"),
        "tokens": [op.get("input_tokens"), op.get("output_tokens")],
        "agente_min": round(op["duration_ms"] / 60000, 1) if op.get("duration_ms") else None,
        "parede_min": round(m["wall_clock_s"] / 60, 1) if m.get("wall_clock_s") else None,
        "restos_mortos": len(m.get("restos_mortos") or []),
        "etapas": etapas(m.get("fases") or []),
        "revisao": m.get("review"),
        "relatorio": relatorio[:12000],
    }


PAGINA = r"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Monitor E-9</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Crect width='24' height='24' rx='4' fill='%23151a0f'/%3E%3Cg fill='none' stroke='%23ffb000' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M12 3v18'/%3E%3Cpath d='M6 4v5a6 6 0 0 0 12 0V4'/%3E%3Cpath d='M9 21h6'/%3E%3C/g%3E%3C/svg%3E">
<style>
:root{--bg:#0b0d08;--painel:#12160d;--borda:#2f3a1f;--oliva:#4b5320;--caqui:#c3b091;--fg:#d8d2bd;--mut:#7d7a68;
--ambar:#ffb000;--verde:#7ecb5f;--vermelho:#e5533d;--ciano:#8fc9c0}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--fg)}
body{font:13px/1.45 ui-monospace,"Cascadia Mono",Consolas,Menlo,monospace;
 background-image:repeating-linear-gradient(0deg,transparent 0 23px,rgba(75,83,32,.10) 23px 24px),repeating-linear-gradient(90deg,transparent 0 23px,rgba(75,83,32,.10) 23px 24px)}
.cx{text-transform:uppercase;letter-spacing:.14em}
header{position:sticky;top:0;z-index:2;background:linear-gradient(180deg,#151a0f,#0f130b);border-bottom:2px solid var(--oliva);padding:10px 16px}
.topo{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.insignia{font-size:22px;color:var(--ambar);border:2px solid var(--ambar);border-radius:4px;width:38px;height:38px;display:flex;align-items:center;justify-content:center}
h1{margin:0;font-size:15px;color:var(--caqui)}h1 b{color:var(--ambar)}
.sub{font-size:10.5px;color:var(--mut)}
.dtg{margin-left:auto;text-align:right}.dtg .v{color:var(--ambar);font-size:16px;letter-spacing:.08em}.dtg .r{font-size:10px;color:var(--mut)}
.faixa{margin-top:8px;font-size:10px;color:var(--mut);display:flex;gap:16px;flex-wrap:wrap}
.faixa span b{color:var(--fg);font-weight:normal}
main{display:grid;grid-template-columns:minmax(230px,300px) 1fr;gap:12px;padding:12px 16px 60px}
@media (max-width:760px){main{grid-template-columns:1fr}.dtg{margin-left:0;text-align:left}}
.painel{background:var(--painel);border:1px solid var(--borda);border-radius:3px}
.painel h2{margin:0;padding:7px 10px;font-size:11px;color:var(--caqui);border-bottom:1px solid var(--borda);background:#161b10;display:flex;justify-content:space-between}
#missoes{max-height:56vh;overflow:auto}
.m{padding:8px 10px;border-bottom:1px solid #1c2214;cursor:pointer}.m:hover{background:#181e11}.m.sel{background:#1d2413;border-left:3px solid var(--ambar);padding-left:7px}
.m .n{color:var(--fg);word-break:break-all}.m .s{margin-top:3px;font-size:10.5px}
.tag{display:inline-block;padding:1px 6px;border:1px solid currentColor;border-radius:2px;font-size:10px;letter-spacing:.12em;margin-right:6px}
.voo{color:var(--ambar)}.verde{color:var(--verde)}.vermelho{color:var(--vermelho)}.falha{color:var(--vermelho)}.cinza{color:var(--mut)}
.pisca{animation:p 1.2s steps(2,start) infinite}@keyframes p{to{visibility:hidden}}
#sitrep{padding:10px;font-size:12px;border-bottom:1px solid var(--borda)}
#sitrep .l{color:var(--mut);font-size:10px}#sitrep .x{word-break:break-word}
#log{padding:4px 10px 10px;max-height:72vh;overflow:auto}
.e{padding:5px 0;border-bottom:1px dashed #1f2617;white-space:pre-wrap;word-break:break-word;display:grid;grid-template-columns:auto auto 1fr;gap:8px;align-items:start}
.e .h{color:var(--mut);font-size:10.5px}.e .k{font-size:10px;letter-spacing:.12em;padding:0 4px;border:1px solid currentColor;border-radius:2px}
.acao .k{color:var(--ciano)}.acao .t{color:#c7e0dc}.radio .k{color:var(--caqui)}.contato .k,.contato .t{color:var(--vermelho)}
.recusa .k,.recusa .t{color:var(--ambar)}.estourando{color:var(--vermelho)}
.fim .k{color:var(--verde)}.fim.ruim .k{color:var(--vermelho)}.fim .t{color:var(--fg)}
.rel{grid-column:1/-1;margin:6px 0 2px;padding:8px;border:1px solid var(--borda);background:#0e110a;color:var(--fg);white-space:pre-wrap}
.col{display:flex;flex-direction:column;gap:12px;min-width:0}
.placar{padding:8px 10px;font-size:11.5px;line-height:1.6}.placar b{color:var(--fg);font-weight:normal}
.placar .ok{color:var(--verde)}.placar .mau{color:var(--vermelho)}.placar .mut{color:var(--mut)}
.m .rv{font-size:10px;color:var(--mut);margin-left:4px}
#aar{border-bottom:1px solid var(--borda)}#aar:empty{display:none}
.veredito{padding:10px;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;border-bottom:1px solid #1c2214}
.veredito .sig{color:var(--fg)}.veredito .rc{color:var(--mut)}
.fatos{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1px;background:#1c2214}
.fato{background:var(--painel);padding:8px 10px}.fato .l{color:var(--caqui);font-size:10px;margin-bottom:3px}
.fato .x{word-break:break-word}.fato .ok{color:var(--verde)}.fato .mau{color:var(--vermelho)}.fato .mut{color:var(--mut)}
.prova{border-left:3px solid var(--ambar)}
details.dep{padding:6px 10px 10px}details.dep summary{cursor:pointer;color:var(--caqui);font-size:10.5px}
.md{color:var(--fg);margin-top:6px}.md h3,.md h4,.md h5{margin:10px 0 4px;color:var(--caqui);font-size:12px}
.md p{margin:4px 0}.md ul,.md ol{margin:4px 0;padding-left:18px}.md code{color:var(--ciano)}
.md table{border-collapse:collapse;margin:6px 0}.md td,.md th{border:1px solid var(--borda);padding:2px 6px}
footer{position:fixed;bottom:0;left:0;right:0;padding:5px 16px;background:#0f130b;border-top:1px solid var(--borda);font-size:10px;color:var(--mut)}
#atual,.seta{display:none}
/* Tela estreita: o log é o que importa durante o run. O topo encolhe para uma linha, a lista de
   runs vira uma barra com a op atual que abre a lista ao toque, cada linha do log usa a largura
   toda, e o rodapé sai. Nada daqui vale acima de 760 px. */
@media (max-width:760px){
 body{font-size:13.5px}
 header{padding:8px 12px}
 .insignia{width:30px;height:30px;font-size:17px}
 h1{font-size:14px}.sub,.faixa,.dtg .r{display:none}
 .dtg{margin-left:auto;text-align:right}.dtg .v{font-size:13px}
 main{padding:8px 10px 16px;gap:8px}
 .painel h2{cursor:pointer;padding:9px 10px}
 .seta{display:inline}
 #painelmis:not(.aberto) #missoes{display:none}
 #painelmis.aberto #atual{display:none}
 #atual{display:block;border-bottom:0}
 #missoes{max-height:55vh}
 .m{padding:10px}
 #log{max-height:none;overflow:visible;padding:2px 10px 10px}
 .e{grid-template-columns:auto 1fr;gap:4px 8px;padding:8px 0}
 .e .t{grid-column:1/-1}.e .k{justify-self:start}
 #sitrep{font-size:12.5px}
 footer{display:none}
}
</style></head><body>
<header>
 <div class="topo">
  <div class="insignia">🔱</div>
  <div><h1 class="cx"><b>Monitor</b> E-9</h1><div class="sub cx">o agent trabalhando · ao vivo</div></div>
  <div class="dtg"><div class="v" id="dtg">——:——:——</div><div class="r cx">hora local</div></div>
 </div>
 <div class="faixa cx"><span>Só <b>leitura</b></span><span>Policy <b>branch only</b></span><span>Quem decide <b>o gate</b></span><span id="lig">Conexão <b>…</b></span></div>
</header>
<main>
 <div class="col">
 <section class="painel" id="painelplacar"><h2 class="cx"><span>Placar dos runs</span><span class="mut" id="placarn">—</span></h2><div id="placar" class="placar">…</div></section>
 <section class="painel" id="painelmis"><h2 class="cx" id="cabmis"><span>Runs <span class="seta">▾</span></span><span id="nops">—</span></h2><div id="atual" class="m sel"></div><div id="missoes"></div></section>
 </div>
 <section class="painel"><h2 class="cx"><span>Log do agent</span><span id="opsel">—</span></h2>
  <div id="sitrep"><div class="l cx">STATUS</div><div class="x" id="sitx">aguardando…</div></div>
  <div id="aar"></div>
  <div id="log"></div></section>
</main>
<footer class="cx">Monitor E-9 · só leitura · policy: branch only · nunca push no target · quem diz que está pronto é o gate</footer>
<script>
const MESES=['JAN','FEV','MAR','ABR','MAI','JUN','JUL','AGO','SET','OUT','NOV','DEZ'];
const p2=n=>String(n).padStart(2,'0');
function dtg(d){return p2(d.getHours())+':'+p2(d.getMinutes())+':'+p2(d.getSeconds())}
function zulu(){return dtg(new Date())}
setInterval(()=>{document.getElementById('dtg').textContent=dtg(new Date())},1000);document.getElementById('dtg').textContent=dtg(new Date());
// O desfecho vem classificado do servidor (classificar() em monitor.py), com rc e significado.
const COR={voo:'voo',verde:'verde',vermelho:'vermelho',barrada:'vermelho',aceite:'vermelho',base:'vermelho',falha:'falha'};
function estado(o){return[COR[o.classe]||'cinza',o.rotulo||'SEM run report']}
let op=null,pos=0,fixo=false,seguir=true,ultimas=[],aarFeito='',ultTs=null,uso={},recusas=0,faseAgora=null;
const mmss=s=>{s=Math.max(0,Math.round(s));return(s>=3600?Math.floor(s/3600)+':'+p2(Math.floor(s%3600/60)):Math.floor(s/60))+':'+p2(s%60)};
// O cronômetro da etapa, contra o teto dela (v0.5: o harness grava início e teto em fases.json).
function desenharFase(){const el=document.getElementById('fasex');if(!el||!faseAgora)return;const f=faseAgora;let t=esc(f.texto||'');
 if(f.desde){const s=(Date.now()-new Date(f.desde))/1000;const perto=f.teto_s&&s>f.teto_s*0.8;
  t+=' · <span class="'+(perto?'estourando':'')+'">'+mmss(s)+(f.teto_s?' de '+mmss(f.teto_s):'')+'</span>'}
 const tok=Object.values(uso).reduce((a,b)=>a+b,0);if(tok)t+=' · '+tok+' tokens de saída';
 if(recusas)t+=' · <span class="estourando">'+recusas+' recusa(s) de permissão</span>';el.innerHTML=t}
setInterval(desenharFase,1000);
const log=document.getElementById('log'),mis=document.getElementById('missoes'),caixa=document.getElementById('aar');
// A hora real do evento (o transcript traz timestamp); sem ela, a hora em que a linha chegou.
function hora(ts){if(!ts)return zulu();const d=new Date(ts);if(isNaN(d))return zulu();const h=new Date();
 const dia=d.toDateString()===h.toDateString()?'':p2(d.getDate())+MESES[d.getMonth()]+' ';
 return dia+dtg(d)}
// Markdown mínimo do relatório. Escapa o HTML ANTES de qualquer marcação: o texto vem do agente.
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function inl(s){return esc(s).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>')}
function md(txt){const out=[];let lista=null,tab=null;
 const fecha=()=>{if(lista){out.push('</'+lista+'>');lista=null}if(tab){out.push('</table>');tab=null}};
 for(const ln of String(txt||'').split('\n')){let m;
  if(/^\s*\|.*\|\s*$/.test(ln)){if(/^\s*\|[\s:|-]+\|\s*$/.test(ln))continue;
   if(!tab){fecha();out.push('<table>');tab=1}out.push('<tr>'+ln.trim().slice(1,-1).split('|').map(c=>'<td>'+inl(c.trim())+'</td>').join('')+'</tr>');continue}
  if(tab){out.push('</table>');tab=null}
  if(m=ln.match(/^(#{1,6})\s+(.*)/)){fecha();const n=Math.min(5,m[1].length+2);out.push('<h'+n+'>'+inl(m[2])+'</h'+n+'>');continue}
  if(m=ln.match(/^\s*[-*]\s+(.*)/)){if(lista!=='ul'){fecha();out.push('<ul>');lista='ul'}out.push('<li>'+inl(m[1])+'</li>');continue}
  if(m=ln.match(/^\s*\d+[.)]\s+(.*)/)){if(lista!=='ol'){fecha();out.push('<ol>');lista='ol'}out.push('<li>'+inl(m[1])+'</li>');continue}
  if(/^\s*(---+|===+)\s*$/.test(ln)){fecha();continue}
  fecha();if(ln.trim())out.push('<p>'+inl(ln)+'</p>')}
 fecha();return out.join('')}
function fato(rot,html,cls){return '<div class="fato'+(cls?' '+cls:'')+'"><div class="l cx">'+esc(rot)+'</div><div class="x">'+html+'</div></div>'}
function gateTxt(g){g=g||{};const p=[];for(const k of['ruff','mypy'])if(g[k])p.push(k+' '+esc(g[k]));
 for(const k of['passed','failed','errors'])if(g[k]!=null)p.push('<span class="'+(k==='passed'?'ok':'mau')+'">'+g[k]+' '+k+'</span>');return p.join(' · ')}
function desenharAar(a){const chave=JSON.stringify(a)+'|'+recusas;if(chave===aarFeito)return;aarFeito=chave;
 if(!a){caixa.innerHTML='';return}
 const [c,txt]=estado(a);let h='<div class="veredito"><span class="tag cx '+c+'">'+esc(txt)+'</span>'
  +(a.rc!=null?'<span class="rc cx">rc '+a.rc+'</span>':'')+'<span class="sig">'+esc(a.significado)+'</span></div><div class="fatos">';
 const gok=a.gate_pass;
 h+=fato('Gate · a prova (quality gate)',a.gate||gok!=null?'<span class="'+(gok?'ok':'mau')+'">'+(gok?'VERDE':'VERMELHO')+'</span>'+(a.gate?' — '+gateTxt(a.gate):''):'<span class="mut">não rodou</span>','prova');
 const b=a.baseline||{};h+=fato('Linha de base (baseline)',b.pass==null?'<span class="mut">não rodou</span>':(b.pass?'<span class="ok">verde no main</span>':'<span class="mau">vermelha no main</span>')+(b.gate?' — '+gateTxt(b.gate):''));
 h+=fato('Relatório × gate',a.divergencias.length?a.divergencias.map(d=>'<div class="mau">⚠ '+esc(d)+'</div>').join(''):'<span class="ok">o relatório não contradiz o gate</span>');
 const cont=c=>Object.entries(c||{}).filter(([k])=>k!=='erro_de_coleta').map(([k,n])=>n+' '+k).join(', ')||'—';
 const ac=a.aceite;h+=fato('Acceptance tests × entrega',!ac?'<span class="mut">a op não declarou aceite</span>':
  'antes do agente: <span class="mau">'+esc(cont(ac.base))+'</span>'+(ac.entrega?' → depois: <span class="'+(ac.entrega_rc===0?'ok':'mau')+'">'+esc(cont(ac.entrega))+'</span>':'')
  +(ac.mexidos?'<div class="mau">mexidos: '+esc(ac.mexidos.join(', '))+'</div>':'')+(ac.veredito?'<div>'+esc(ac.veredito)+'</div>':''));
 const rr=a.revisor;if(rr)h+=fato('AI code review de outro modelo (só informa)',!rr.veredito?'<span class="mut">'+esc(rr.estado||'—')+'</span>':
  '<span class="'+(rr.veredito==='APROVA'?'ok':'mau')+'">'+esc(rr.veredito)+'</span> <span class="mut">'+esc(rr.modelo||'')
  +(rr.custo!=null?' · US$ '+Number(rr.custo).toFixed(2):'')+'</span>'+(rr.resumo?'<div>'+esc(rr.resumo)+'</div>':'')
  +((rr.achados||[]).length?'<ul style="margin:4px 0;padding-left:18px">'+rr.achados.map(x=>'<li><b>'+esc(x.severidade)+'</b> <code>'
  +esc(x.arquivo+':'+x.linha)+'</code> — '+esc(x.problema)+' <span class="mut">'+esc(x.por_que)+'</span></li>').join('')+'</ul>':''));
 h+=fato('Desvios declarados',a.desvios?'<div class="md">'+md(a.desvios)+'</div>':'<span class="mau">o agente não declarou a seção de desvios</span>');
 const cu=[];if(a.custo!=null)cu.push('US$ '+Number(a.custo).toFixed(2));if(a.turnos!=null)cu.push(a.turnos+' turnos');
 if(a.agente_min!=null)cu.push('agente '+a.agente_min+' min');if(a.parede_min!=null)cu.push('run '+a.parede_min+' min');
 if(a.tokens&&a.tokens[1]!=null)cu.push(a.tokens[1]+' tokens de saída');if(a.restos_mortos)cu.push(a.restos_mortos+' resto(s) morto(s)');
 if(recusas)cu.push(recusas+' recusa(s) de permissão');
 h+=fato('Custo e tempo',cu.length?esc(cu.join(' · ')):'<span class="mut">sem medição</span>');
 if(a.etapas&&a.etapas.length)h+=fato('Etapas',a.etapas.map(e=>{const s=e.teto_s&&e.s>e.teto_s*0.8?'mau':'';
  return esc(e.fase.replace('_',' '))+' <span class="'+s+'">'+mmss(e.s)+'</span>'+(e.teto_s?'<span class="mut">/'+mmss(e.teto_s)+'</span>':'')}).join(' · '));
 h+=fato('Entrega',(a.commit?'commit <b>'+esc(a.commit)+'</b> em <code>'+esc(a.branch||'')+'</code>':'<span class="mau">sem commit</span> · <code>'+esc(a.branch||'')+'</code>')
  +(a.arquivos.length?'<ul style="margin:4px 0;padding-left:18px">'+a.arquivos.map(f=>'<li><code>'+esc(f)+'</code></li>').join('')+'</ul>':''));
 const rv=a.revisao;h+=fato('Revisão no target',rv?('<span class="'+(rv.desfecho==='como_veio'?'ok':rv.desfecho==='descartado'?'mau':'')+'">'
  +esc({como_veio:'entrou como o agente entregou',consertado:'entrou depois de conserto',descartado:'não entrou'}[rv.desfecho]||rv.desfecho)+'</span>'
  +(rv.commit_alvo?' · <code>'+esc(rv.commit_alvo)+'</code>':'')+(rv.nota?'<div>'+esc(rv.nota)+'</div>':''))
  :'<span class="mut">sem revisão gravada (orchestrator/revisar.py)</span>');
 h+='</div>';
 if(a.relatorio)h+='<details class="dep"><summary class="cx">Relatório do agente · a declaração (abrir)</summary><div class="md">'+md(a.relatorio)+'</div></details>';
 caixa.innerHTML=h}
function linha(cls,k,txt,rel,ts){const d=document.createElement('div');d.className='e '+cls;
 const h=document.createElement('span');h.className='h';h.textContent=hora(ts);
 const kk=document.createElement('span');kk.className='k cx';kk.textContent=k;
 const t=document.createElement('span');t.className='t';t.textContent=txt;d.append(h,kk,t);
 if(rel){const r=document.createElement('div');r.className='rel';r.textContent=rel;d.appendChild(r)}log.appendChild(d)}
function desenhar(l){mis.innerHTML='';document.getElementById('nops').textContent=l.length+' ops';
 for(const o of l){const[c,txt]=estado(o);const d=document.createElement('div');d.className='m'+(o.op===op?' sel':'');
  const n=document.createElement('div');n.className='n';n.textContent=o.op;
  const s=document.createElement('div');s.className='s';const tg=document.createElement('span');tg.className='tag cx '+c+(c==='voo'?' pisca':'');tg.textContent=txt;s.appendChild(tg);
  if(o.revisao){const rv=document.createElement('span');rv.className='rv cx';rv.textContent={como_veio:'entrou como veio',consertado:'consertada no alvo',descartado:'descartada'}[o.revisao]||o.revisao;s.appendChild(rv)}
  d.append(n,s);d.onclick=()=>{fixo=true;painel.classList.remove('aberto');trocar(o.op)};mis.appendChild(d);
  if(o.op===op){atual.innerHTML='';atual.append(n.cloneNode(true),s.cloneNode(true))}}}
const painel=document.getElementById('painelmis'),atual=document.getElementById('atual');
document.getElementById('cabmis').onclick=()=>painel.classList.toggle('aberto');
atual.onclick=()=>painel.classList.add('aberto');
const pct=(a,b)=>b?Math.round(100*a/b)+'%':'—';
async function placar(){try{const p=await(await fetch('api/placar')).json();const d=p.desfechos||{},rv=p.revisao||{};
 document.getElementById('placarn').textContent=p.voos+' runs reais';
 const brl=v=>v==null?'—':'US$ '+Number(v).toFixed(2).replace('.',',');
 document.getElementById('placar').innerHTML=
  '<div>gate verde e commit: <b class="ok">'+(d.VERDE||0)+'</b> de '+p.voos+' ('+pct(d.VERDE||0,p.voos)+') · vermelho '+(d.VERMELHO||0)+' · barrada '+(d.BARRADA||0)+'</div>'
  +'<div>verde de primeira: <b class="ok">'+(p.verde_de_primeira??'—')+'</b> de '+p.voos+(p.revoos?' · '+p.revoos+' com re-run':'')+'</div>'
  +'<div>entrou no target como veio: <b class="ok">'+rv.como_veio+'</b> · consertada '+rv.consertado+' · descartada '+rv.descartado+(rv.sem?' · <span class="mau">sem revisão '+rv.sem+'</span>':'')+'</div>'
  +'<div>relatório × gate divergiu: <b class="'+(p.divergiu?'mau':'ok')+'">'+p.divergiu+'</b> de '+p.com_gate+'</div>'
  +'<div>custo '+brl(p.custo_total)+' · média '+brl(p.custo_medio)+' · run médio '+(p.voo_medio_min??'—')+' min</div>'}catch(e){}}
async function ops(){placar();try{const r=await fetch('api/ops');ultimas=await r.json();
 if(!fixo&&ultimas.length){const a=(ultimas.find(o=>o.em_voo)||ultimas[0]).op;if(a!==op)trocar(a)}desenhar(ultimas);
 document.getElementById('lig').innerHTML='Conexão <b>OK</b>'}catch(e){document.getElementById('lig').innerHTML='Conexão <b style="color:var(--vermelho)">PERDIDA</b>'}}
function trocar(o){op=o;pos=0;ultTs=null;uso={};recusas=0;faseAgora=null;log.innerHTML='';aarFeito='';caixa.innerHTML='';document.getElementById('opsel').textContent=o;desenhar(ultimas);linha('radio','spec','═══ '+o+' ═══')}
window.addEventListener('scroll',()=>{},{passive:true});log.onscroll=()=>{seguir=log.scrollTop+log.clientHeight>=log.scrollHeight-30};
async function puxar(){if(!op)return;try{const r=await fetch('api/feed?op='+encodeURIComponent(op)+'&pos='+pos+(ultTs?'&ts='+encodeURIComponent(ultTs):''));if(!r.ok)return;const d=await r.json();
 if(d.pos<pos){log.innerHTML='';uso={};recusas=0}pos=d.pos;ultTs=d.ts||ultTs;
 for(const[k,n]of Object.entries(d.uso||{}))uso[k]=Math.max(uso[k]||0,n);
 for(const e of d.eventos){if(e.tipo==='fim')linha('fim'+(e.erro?' ruim':''),'FIM',e.texto+' · o run report está no topo',null,e.ts);
  else if(e.tipo==='acao')linha('acao','AÇÃO',e.texto,null,e.ts);else if(e.tipo==='erro')linha('contato','ERRO',e.texto,null,e.ts);
  else if(e.tipo==='recusa'){recusas++;linha('recusa','RECUSA',e.texto,null,e.ts)}else linha('radio','FALA',e.texto,null,e.ts)}
 const x=document.getElementById('sitx');
 if(d.em_voo){faseAgora=d.fase;if(!document.getElementById('fasex'))x.innerHTML='<span class="tag cx voo pisca">RODANDO</span><span id="fasex"></span>';desenharFase();desenharAar(null)}
 else if(d.aar){x.innerHTML='<span class="cx" style="color:var(--caqui)">run terminado</span> · '+esc(d.aar.status||'');desenharAar(d.aar)}
 else{x.textContent='sem run report ainda';desenharAar(null)}
 if(d.eventos.length&&seguir)log.scrollTop=log.scrollHeight}catch(e){}}
ops();setInterval(ops,15000);setInterval(puxar,3000);puxar();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "e9-monitor"

    def _json(self, obj, code=200):
        corpo = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):  # noqa: N802 — nome da stdlib
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            corpo = PAGINA.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
        elif u.path == "/api/ops":
            self._json(listar_ops())
        elif u.path == "/api/placar":
            self._json(placar())
        elif u.path == "/api/feed":
            q = parse_qs(u.query)
            pasta = _op_valida((q.get("op") or [""])[0])
            if pasta is None:
                return self._json({"erro": "op desconhecida"}, 404)
            try:
                pos = max(0, int((q.get("pos") or ["0"])[0]))
            except ValueError:
                pos = 0
            self._json(ler_feed(pasta, pos, (q.get("ts") or [None])[0]))
        else:
            self._json({"erro": "não encontrado"}, 404)

    def log_message(self, fmt, *args):  # sem log por requisição: a página pergunta a cada 3 s
        pass


def argumentos(argv=None):
    ap = argparse.ArgumentParser(description="Monitor E-9 — o agent trabalhando, ao vivo (só leitura)")
    ap.add_argument("--porta", type=int, default=8417)
    ap.add_argument("--host", default="127.0.0.1",
                    help="padrão 127.0.0.1 (só esta máquina); 0.0.0.0 abre para a rede local")
    return ap.parse_args(argv)


def main():
    a = argumentos()
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"[Monitor E-9] ATENÇÃO: escutando em {a.host} — a página mostra o código e os comandos "
              "do agent para quem alcançar esta porta.", flush=True)
    try:
        servidor = ThreadingHTTPServer((a.host, a.porta), Handler)
    except OSError as e:
        print(f"[Monitor E-9] não consegui abrir a porta {a.porta} ({e.strerror}). Outro programa já a usa?"
              f" Tente outra: --porta {a.porta + 1}", file=sys.stderr, flush=True)
        sys.exit(1)
    endereco = "127.0.0.1" if a.host == "0.0.0.0" else a.host
    print(f"[Monitor E-9] {time.strftime('%F %T')} em http://{endereco}:{a.porta}"
          f" · ops em {_ops_dir()} · Ctrl+C encerra", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
