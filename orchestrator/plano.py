"""
plano.py — o gate de plano (v0.12, 2026-11-24): o agent diz que arquivos vai tocar ANTES de tocar.

Até a v0.11, arquivo fora do escopo só aparecia no commit: o gate verde e o commit recusado (rc 4)
depois de uma hora de agent. Agora o run tem uma fase de plano antes da implementação:
o agent lê o que precisar, não edita nada, e devolve a lista de arquivos que pretende criar, mudar ou
apagar. O harness confere três coisas, e qualquer uma para o run com **rc 13**, antes da hora de
implementação:

  1. a worktree não mudou durante o plano (o plano é só leitura);
  2. o plano veio, num bloco JSON legível (`{"arquivos": [...]}`);
  3. cada arquivo do plano cabe em `expected_artifacts` ou `optional_artifacts`, e nenhum é
     acceptance test (que o agent nunca edita).

Plano que esquece um artefato obrigatório não para: vira aviso no run report, porque o commit ainda o
cobra (rc 4) e o agent pode descobrir o caminho no meio. A implementação RETOMA a sessão do plano
(`claude --resume <id> -p`), e o recon não se paga duas vezes; se o `cmd_template` não tiver um
`claude -p` para retomar, ela começa nova, com o plano aprovado anexado ao briefing.

Ligado por padrão; `"plan_gate": false` no op.json desliga. Stdlib apenas.
"""
from __future__ import annotations

import fnmatch
import json
import re

RC_PLANO = 13
TIMEOUT_S = 20 * 60

INSTRUCAO = """

============================================================
# FASE DE PLANO (v0.12) — antes de implementar

Esta primeira rodada é SÓ DE PLANO. Leia o que precisar (Read, Grep, Glob, e os comandos liberados que
só leem), e NÃO crie, edite, apague nem formate arquivo nenhum: o E-9 confere a worktree depois, e
qualquer mudança para o run.

Termine a resposta com um bloco JSON, exatamente neste formato, listando TODO arquivo que você vai
criar, mudar ou apagar na implementação, com o caminho relativo à raiz da worktree:

```json
{"arquivos": ["caminho/um.py", "caminho/dois.py"], "porque": "uma frase por arquivo, se ajudar"}
```

O E-9 confere a lista contra o escopo do commit:
{escopo}
Arquivo fora dele para o run aqui, antes da implementação, e não depois de uma hora de trabalho.
Acceptance tests não entram na lista: eles não se editam. Se a intent pede um arquivo fora do escopo,
diga isso no plano (escalate) em vez de listá-lo. Aprovado o plano, a implementação continua nesta
mesma sessão.
"""

CONTINUA = """# IMPLEMENTAÇÃO (v0.12)

O E-9 conferiu o seu plano e ele cabe no escopo:
{lista}

Implemente agora, nesta mesma sessão, tudo o que o briefing pede. As regras do briefing continuam
valendo: o portão inteiro em primeiro plano antes do relatório, nada em segundo plano, e a seção
`## Desvios da spec` no fim. Precisar de um arquivo fora do plano, mas dentro do escopo, é permitido:
declare nos desvios. Fora do escopo, o commit é recusado.
"""

ANEXO_SEM_RETOMAR = """

============================================================
# PLANO APROVADO (v0.12)

Numa rodada anterior, você planejou tocar estes arquivos, e o E-9 conferiu que cabem no escopo:
{lista}
"""


def ligado(cfg: dict) -> bool:
    return cfg.get("plan_gate", True) is not False


def escopo(cfg: dict) -> str:
    obr = cfg.get("expected_artifacts") or []
    opc = cfg.get("optional_artifacts") or []
    return ("- obrigatórios (cada padrão tem de casar): " + ", ".join(f"`{p}`" for p in obr) + "\n"
            + ("- opcionais: " + ", ".join(f"`{p}`" for p in opc) + "\n" if opc else ""))


def instrucao(cfg: dict) -> str:
    return INSTRUCAO.replace("{escopo}", escopo(cfg))


def _lista(arquivos) -> str:
    return "\n".join(f"- `{a}`" for a in arquivos) or "- (nenhum arquivo)"


def continua(arquivos) -> str:
    return CONTINUA.replace("{lista}", _lista(arquivos))


def anexo_sem_retomar(arquivos) -> str:
    return ANEXO_SEM_RETOMAR.replace("{lista}", _lista(arquivos))


_BLOCO = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def ler(texto: str, worktree: str | None = None) -> list[str] | None:
    """A lista de arquivos do ÚLTIMO bloco JSON com `arquivos` no texto; None se não houver. Caminho
    absoluto na worktree vira relativo; `./` sai."""
    candidatos = _BLOCO.findall(texto or "")
    if not candidatos:                                # sem cerca de código: o último {...} da resposta
        m = re.findall(r"\{[^{}]*\"arquivos\"[^{}]*\}", texto or "", re.DOTALL)
        candidatos = m
    for bruto in reversed(candidatos):
        try:
            obj = json.loads(bruto)
        except ValueError:
            continue
        lista = obj.get("arquivos") if isinstance(obj, dict) else None
        if isinstance(lista, list) and all(isinstance(x, str) for x in lista):
            base = (worktree or "").rstrip("/") + "/"
            out = []
            for a in lista:
                a = a.strip()
                if worktree and a.startswith(base):
                    a = a[len(base):]
                while a.startswith("./"):
                    a = a[2:]
                if a and a not in out:
                    out.append(a)
            return out
    return None


def _casa(caminho: str, padrao: str) -> bool:
    """O mesmo critério do commit (`stage_and_commit`): glob, ou o padrão contido no caminho."""
    return fnmatch.fnmatch(caminho, padrao) or padrao in caminho


def julgar(cfg: dict, arquivos: list[str], aceite=()) -> dict:
    """{arquivos, fora, acceptance, faltam}: o que sai do escopo e o que é acceptance test param o run;
    o padrão obrigatório que nenhum arquivo do plano cobre só avisa."""
    obr = cfg.get("expected_artifacts") or []
    escopo_todo = [*obr, *(cfg.get("optional_artifacts") or [])]
    ac = set(aceite)
    return {"arquivos": arquivos,
            "acceptance": [a for a in arquivos if a in ac],
            "fora": [a for a in arquivos if a not in ac and not any(_casa(a, p) for p in escopo_todo)],
            "faltam": [p for p in obr if not any(_casa(a, p) for a in arquivos)]}


def retomar(cmd: str, sessao: str | None) -> str | None:
    """O comando da implementação retomando a sessão do plano: `claude -p` → `claude --resume <id> -p`.
    None se não houver sessão ou o comando não tiver um `claude -p` para retomar."""
    if not sessao or not re.fullmatch(r"[0-9a-fA-F-]{8,64}", sessao) or "claude -p" not in cmd:
        return None
    return cmd.replace("claude -p", f"claude --resume {sessao} -p", 1)


def linhas(p: dict | None) -> list[str]:
    """A seção do run report."""
    if not p:
        return []
    out = ["## Gate de plano (v0.12)", ""]
    if p.get("veredito"):
        out.append(f"- **{p['veredito']}**")
    if p.get("arquivos") is not None:
        out.append("- Plano: " + (", ".join(f"`{a}`" for a in p["arquivos"]) or "nenhum arquivo") + ".")
    for chave, txt in (("fora", "⚠️ **Fora do escopo:**"), ("acceptance", "⚠️ **Acceptance test no plano:**"),
                       ("faltam", "Aviso — padrão obrigatório que o plano não cobre:")):
        if p.get(chave):
            out.append(f"- {txt} " + ", ".join(f"`{a}`" for a in p[chave]) + ".")
    custo = p.get("custo")
    out.append(f"- Fase de plano: {p.get('turnos')} turnos"
               + (f" · US$ {custo:.2f}" if isinstance(custo, (int, float)) else "")
               + (" · implementação retomou a sessão" if p.get("retomada") else
                  " · implementação em sessão nova" if p.get("retomada") is False else "") + ".")
    return out + [""]
