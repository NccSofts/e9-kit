#!/usr/bin/env python3
"""
trajetoria.py — a varredura da trajetória do agent no `agent.jsonl` (v0.11, 2026-11-24).

O gate e os acceptance tests julgam o FIM do run: a árvore entregue. Este módulo lê o CAMINHO — o
stream-json do `claude -p`, evento por evento — atrás dos padrões que já custaram runs de verdade e que
a árvore final não mostra:

  - `segundo_plano`: comando que foi para segundo plano (pedido com `run_in_background`, `&`, `nohup`, ou
    jogado pelo Claude Code quando passou do `timeout`), ou o relatório que encerra "aguardando
    notificações". Em `claude -p` o aviso de "terminou" nunca chega.
  - `timeout_curto`: teste pedido ao Bash com `timeout` abaixo do que o harness mediu na baseline — a
    causa de o comando ir sozinho para segundo plano (v0.5.1).
  - `acceptance_aberto`: Edit, Write ou comando de shell que escreve num arquivo de acceptance test. O
    sha256 só vê o estado final: o agent pode editar e desfazer, e o hash passa.
  - `teste_em_paralelo`: duas rodadas de teste ao mesmo tempo (duas na mesma mensagem, ou uma nova com
    outra ainda em segundo plano), disputando o banco de teste.
  - `rede`: WebFetch, WebSearch, ou comando que sai para a rede (curl, wget, pip install, git fetch...),
    fora de localhost. O agent trabalha com o que está na worktree.

**Só informa**: vai ao run report, ao metrics.json (`trajetoria`) e ao dashboard, e
não muda o rc. Determinística, sem modelo, e nunca levanta. Calibrada em 24 transcripts de runs reais:
acusa os cinco runs em que esses padrões tinham custado o run e cala nos outros dezenove.

Uso avulso, para varrer runs que já aconteceram:
  python3 orchestrator/trajetoria.py --op ops/<op> [--op ops/<outra> ...]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PADROES = {
    "segundo_plano": "comando em segundo plano, ou relatório 'aguardando' — o aviso nunca chega em claude -p",
    "timeout_curto": "teste com timeout abaixo do tempo medido do gate — vai sozinho para segundo plano",
    "acceptance_aberto": "acceptance test aberto para edição (o sha256 só vê o estado final)",
    "teste_em_paralelo": "duas rodadas de teste ao mesmo tempo, no mesmo banco",
    "rede": "chamada de rede para fora da máquina",
}
EXEMPLOS_MAX = 3

_AGUARDA = re.compile(r"\b(aguardando|esperando)\b.{0,40}\b(notifica|termin|resultado|background|segundo plano)"
                      r"|waiting for (the )?(background|notification|task)", re.IGNORECASE)
_EDICAO = ("Edit", "Write", "MultiEdit", "NotebookEdit")
_LOCAL = re.compile(r"(localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)")
_SEPARA = re.compile(r"\|\||&&|;|\||\n")
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$")
_PREFIXOS = ("sudo", "timeout", "nice", "ionice", "env", "time", "exec", "command")
_FUNDO = re.compile(r"(^|[;&|]\s*)(nohup|setsid)\b|(?<![&>|])&\s*($|;|\))")
_FIM = {"completed", "killed", "failed", "stopped", "done"}


def _sem_heredoc(cmd: str) -> str:
    """O conteúdo de um heredoc é texto, não comando (um script Python com URL dentro não é rede)."""
    return cmd.split("<<", 1)[0]


def _trechos(cmd: str) -> list[list[str]]:
    """O comando quebrado nos trechos que o shell roda (`&&`, `||`, `;`, `|`), cada um como lista de
    palavras sem as atribuições de ambiente e os prefixos (`sudo`, `timeout 600`...). O que importa é a
    cabeça de cada trecho, onde mora o programa: `ps aux | grep pytest` não roda teste."""
    saida = []
    for t in _SEPARA.split(_sem_heredoc(cmd)):
        ws = t.replace("(", " ").replace(")", " ").split()
        while ws and (_ENV.match(ws[0]) or ws[0] in _PREFIXOS or re.fullmatch(r"-?\d+[smh]?", ws[0])):
            ws.pop(0)
        if ws:
            saida.append(ws)
    return saida


def _cabeca(ws: list[str]) -> str:
    return ws[0].rsplit("/", 1)[-1]


def roda_teste(cmd: str) -> bool:
    for ws in _trechos(cmd):
        c, resto = _cabeca(ws), " ".join(ws[1:])
        if c in ("pytest", "py.test", "tox", "nox"):
            return True
        if c.startswith("python") and re.search(r"-m\s+(pytest|unittest)\b|manage\.py\s+test\b", resto):
            return True
        if (c in ("npm", "pnpm", "yarn") and re.match(r"(run\s+)?test\b", resto)) or \
                (c in ("go", "cargo", "make") and re.match(r"test\b", resto)):
            return True
    return False


def sai_para_a_rede(cmd: str) -> bool:
    for ws in _trechos(cmd):
        c, resto = _cabeca(ws), " ".join(ws[1:])
        if c in ("curl", "wget", "http", "https", "nc", "ssh", "scp", "rsync"):
            alvos = [w for w in ws[1:] if not w.startswith("-")]
            if not alvos or any(not _LOCAL.search(w) for w in alvos):
                return True
        if c in ("pip", "pip3", "uv", "npm", "pnpm", "yarn", "apt", "apt-get", "gem", "cargo") and \
                re.match(r"(pip\s+)?(install|add|i)\b", resto):
            return True
        if c.startswith("python") and re.search(r"-m\s+pip\s+install\b", resto):
            return True
        if c == "git" and re.match(r"(clone|fetch|pull|push|ls-remote)\b", resto):
            return True
    return False


def edita(cmd: str, aceites: list[str]) -> bool:
    """Um trecho do comando que escreve num acceptance test: redireção para ele (`> x`, não `2>&1`), ou
    um programa que altera arquivo (sed -i, rm, tee, truncate, git checkout/restore; mv e cp quando ele
    é o destino) com ele nos argumentos. Ler, rodar e copiar DE um acceptance test não conta."""
    corpo = _sem_heredoc(cmd)
    for a in aceites:
        if re.search(r"(?<![\d&])>>?\s*['\"]?\S*" + re.escape(a), corpo):
            return True
    for ws in _trechos(cmd):
        c, args = _cabeca(ws), ws[1:]
        citados = [w for w in args if any(w.strip("'\"").endswith(a) for a in aceites)]
        if not citados:
            continue
        if c in ("rm", "truncate", "tee", "chmod") or (c == "sed" and any(w.startswith("-i") for w in args)) \
                or (c == "perl" and any(re.match(r"-\w*i", w) for w in args)) \
                or (c == "git" and args[:1] and args[0] in ("checkout", "restore", "rm", "mv")):
            return True
        if c in ("mv", "cp", "install", "ln") and args and args[-1] in citados:
            return True
    return False


def _eventos(texto: str):
    for ln in texto.splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if isinstance(ev, dict):
            yield ev


def _ferramentas(ev: dict) -> list[dict]:
    if ev.get("type") != "assistant":
        return []
    msg = ev.get("message")
    blocos = msg.get("content") if isinstance(msg, dict) else None
    return [b for b in blocos or [] if isinstance(b, dict) and b.get("type") == "tool_use"]


def _curto(s: str) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= 160 else s[:160] + "…"


def varrer(texto: str, *, acceptance=(), timeout_min_ms: int | None = None) -> list[dict]:
    """Os achados, um por padrão que apareceu: {padrao, o_que, n, exemplos}. Lista vazia = limpo."""
    achados: dict[str, dict] = {}

    def marca(padrao: str, exemplo: str) -> None:
        a = achados.setdefault(padrao, {"padrao": padrao, "o_que": PADROES[padrao], "n": 0, "exemplos": []})
        a["n"] += 1
        if len(a["exemplos"]) < EXEMPLOS_MAX:
            a["exemplos"].append(_curto(exemplo))

    aceites = [a for a in acceptance if a]
    descricao: dict[str, str] = {}                    # task_id → comando (evento task_started)
    comando_do_uso: dict[str, str] = {}               # tool_use_id → comando do Bash
    pedidos_fundo: set[str] = set()                   # tool_use_id de teste pedido em segundo plano
    teste_no_fundo: set[str] = set()                  # tarefas de teste em segundo plano, ainda vivas
    try:
        for ev in _eventos(texto):
            sub = str(ev.get("subtype") or "")
            if ev.get("type") == "system" and sub.startswith("task_"):
                tid = str(ev.get("task_id") or "")
                patch = ev.get("patch") if isinstance(ev.get("patch"), dict) else {}
                if sub == "task_started":
                    # a `description` é o rótulo que o agent deu ("Full test suite") nas versões novas do
                    # CLI; o comando de verdade vem do tool_use que abriu a tarefa
                    descricao[tid] = (comando_do_uso.get(str(ev.get("tool_use_id") or ""))
                                      or str(ev.get("description") or ""))
                    if str(ev.get("tool_use_id") or "") in pedidos_fundo and roda_teste(descricao[tid]):
                        teste_no_fundo.add(tid)
                elif sub == "task_updated" and patch.get("is_backgrounded"):
                    marca("segundo_plano", f"foi para segundo plano: {descricao.get(tid) or tid}")
                    if roda_teste(descricao.get(tid, "")):
                        teste_no_fundo.add(tid)
                if sub == "task_notification" or str(patch.get("status") or "") in _FIM:
                    teste_no_fundo.discard(tid)
                continue
            if ev.get("type") == "result":
                m = _AGUARDA.search(str(ev.get("result") or ""))
                if m:
                    marca("segundo_plano", f"relatório final: \"…{m.group(0)}…\"")
                continue
            testes_na_msg = []
            for b in _ferramentas(ev):
                nome, inp = b.get("name", ""), b.get("input") or {}
                if not isinstance(inp, dict):
                    continue
                if nome in ("WebFetch", "WebSearch"):
                    marca("rede", f"{nome} {inp.get('url') or inp.get('query') or ''}")
                if nome in _EDICAO:
                    caminho = str(inp.get("file_path") or inp.get("notebook_path") or "")
                    if any(caminho.endswith(a) for a in aceites):
                        marca("acceptance_aberto", f"{nome} {caminho}")
                if nome != "Bash":
                    continue
                cmd = str(inp.get("command") or "")
                comando_do_uso[str(b.get("id") or "")] = cmd
                teste = roda_teste(cmd)
                fundo = bool(inp.get("run_in_background")) or bool(_FUNDO.search(_sem_heredoc(cmd)))
                if fundo:
                    marca("segundo_plano", f"$ {cmd}")
                if teste:
                    if teste_no_fundo:
                        marca("teste_em_paralelo", f"teste com outro ainda em segundo plano: $ {cmd}")
                    if fundo:
                        pedidos_fundo.add(str(b.get("id") or ""))
                    else:
                        testes_na_msg.append(cmd)
                    t = inp.get("timeout")
                    if timeout_min_ms and isinstance(t, (int, float)) and t < timeout_min_ms:
                        marca("timeout_curto", f"timeout {int(t)} ms < {timeout_min_ms} ms: $ {cmd}")
                if aceites and edita(cmd, aceites):
                    marca("acceptance_aberto", f"$ {cmd}")
                if sai_para_a_rede(cmd):
                    marca("rede", f"$ {cmd}")
            if len(testes_na_msg) > 1:
                marca("teste_em_paralelo", f"{len(testes_na_msg)} testes na mesma mensagem: $ {testes_na_msg[0]}")
    except Exception as e:                       # noqa: BLE001 — a varredura nunca derruba o run
        return [{"padrao": "erro_da_varredura", "o_que": f"{type(e).__name__}: {e}"[:200], "n": 0,
                 "exemplos": []}]
    return [achados[p] for p in PADROES if p in achados]


def linhas(achados: list[dict] | None) -> list[str]:
    """A seção do run report."""
    if achados is None:
        return []
    out = ["## Trajetória do agent (v0.11 — só informa, não muda o rc)", ""]
    if not achados:
        return out + ["- Nenhum dos padrões conhecidos no caminho do agent.", ""]
    for a in achados:
        out.append(f"- ⚠️ **{a['padrao']}** ×{a['n']} — {a['o_que']}.")
        out += [f"  - `{x.replace('`', chr(39))}`" for x in a["exemplos"]]
    return out + [""]


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="E-9 — varre a trajetória de runs já feitos")
    ap.add_argument("--op", action="append", required=True, help="pasta da op (repetível)")
    a = ap.parse_args(argv)
    for op in map(Path, a.op):
        jl = op / "agent.jsonl" if (op / "agent.jsonl").exists() else op / "operator.jsonl"
        if not jl.exists():
            print(f"{op.name}: sem transcript")
            continue
        aceite = []
        for pasta in ("acceptance", "aceite"):
            if (op / pasta).is_dir():
                aceite += [p.relative_to(op / pasta).as_posix() for p in (op / pasta).rglob("*.py")]
        r = varrer(jl.read_text(encoding="utf-8", errors="replace"), acceptance=aceite)
        print(f"{op.name}: " + (", ".join(f"{x['padrao']}×{x['n']}" for x in r) or "limpo"))
        for x in r:
            for e in x["exemplos"]:
                print(f"    {x['padrao']}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
