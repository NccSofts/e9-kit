"""
calibracao.py — quanto o AI code review acerta, e o que ele teria segurado (v0.13 e v0.14, 2026-11-24).

Duas medidas, porque "o parecer é pista, não prova" precisava virar número:

- **Calibração (v0.13).** Depois da revisão, o owner marca cada achado do reviewer como `real`, `falso`
  ou `parcial` (certo no ponto, errado no motivo), e conta os defeitos que a revisão dele achou e o
  reviewer não (`perdidos`):

      python orchestrator/revisar.py --op ops/<op> --calibrar "1=real,2=falso" --perdidos 1

  Precisão = reais / julgados (parcial vale meio); cobertura = reais / (reais + perdidos).

- **Veto em sombra (v0.14).** O reviewer continua sem aprovar nem barrar nada. Em cada run, o harness
  grava o que ele TERIA segurado se o veto estivesse ligado — veredito REPROVA, ou achado marcado
  crítico pelo REVIEW.md do target — e o dashboard compara com o que o owner fez na revisão
  (consertou ou descartou = barrou). É o modo de observação do Claude Approvals: a assimetria que
  justifica o veto é que vetar é a direção segura e aprovar a perigosa, mas ligá-lo de verdade é
  decisão do owner, depois de ver os números. Parecer de antes da v0.13 não tem `critico`; nele, a
  severidade alta faz as vezes, e o registro diz que a regra foi essa.

Stdlib apenas.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

JULGAMENTOS = ("real", "falso", "parcial")
_PESO = {"real": 1.0, "parcial": 0.5, "falso": 0.0}


# --- veto em sombra -------------------------------------------------------------------------

def sombra(rv: dict | None) -> dict | None:
    """O que o veto teria feito com este parecer. None se não houve parecer (indisponível, desligado)."""
    if not rv or rv.get("veredito") not in ("APROVA", "RESSALVA", "REPROVA"):
        return None
    achados = rv.get("achados") or []
    com_critico = any("critico" in a for a in achados) or rv.get("review_md") is not None
    regra = ("REPROVA, ou achado crítico pelo REVIEW.md" if com_critico else
             "REPROVA, ou achado de severidade alta (parecer anterior à v0.13, sem `critico`)")
    graves = [a for a in achados if (a.get("critico") if com_critico else a.get("severidade") == "alta")]
    motivos = (["veredito REPROVA"] if rv["veredito"] == "REPROVA" else []) + [
        f"{a.get('arquivo')}:{a.get('linha')} — {str(a.get('problema', ''))[:120]}" for a in graves]
    return {"modo": "sombra", "teria_segurado": bool(motivos), "motivos": motivos, "regra": regra}


def parecer_da_op(d: Path, m: dict) -> tuple[dict | None, str | None]:
    """O parecer que vale para a op: o do run, se houve; senão o da calibração por commit mais recente
    que respondeu. Devolve (parecer, origem)."""
    rv = m.get("reviewer")
    if rv and rv.get("veredito"):
        return rv, "run"
    for nome in ("reviewer-calibracao.json", "revisor-calibracao.json"):
        try:
            lista = json.loads((Path(d) / nome).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for x in reversed(lista if isinstance(lista, list) else []):
            if isinstance(x, dict) and x.get("veredito"):
                return x, f"calibração do commit {x.get('commit')}"
    return None, None


def comparar(pares: list[tuple[dict | None, dict | None]]) -> dict:
    """A matriz do veto em sombra × a revisão do owner, sobre (veto_sombra, review) de cada run."""
    q = {"segurou_e_owner_barrou": 0, "segurou_e_entrou_como_veio": 0,
         "deixou_e_owner_barrou": 0, "deixou_e_entrou_como_veio": 0, "sem_revisao": 0, "com_parecer": 0}
    for veto, rev in pares:
        if not veto:
            continue
        q["com_parecer"] += 1
        desfecho = (rev or {}).get("desfecho")
        if desfecho not in ("como_veio", "consertado", "descartado"):
            q["sem_revisao"] += 1
            continue
        barrou = desfecho != "como_veio"
        chave = ("segurou" if veto["teria_segurado"] else "deixou") + (
            "_e_owner_barrou" if barrou else "_e_entrou_como_veio")
        q[chave] += 1
    return q


# --- calibração -----------------------------------------------------------------------------

def ler_julgamentos(texto: str) -> dict[int, str]:
    """`"1=real,2=falso,3=parcial"` → {1: "real", 2: "falso", 3: "parcial"}. Recusa o resto."""
    out: dict[int, str] = {}
    for parte in (p.strip() for p in (texto or "").split(",") if p.strip()):
        m = re.fullmatch(r"(\d+)\s*=\s*(\w+)", parte)
        if not m or m.group(2) not in JULGAMENTOS:
            raise ValueError(f"julgamento inválido {parte!r}: use N=real, N=falso ou N=parcial")
        out[int(m.group(1))] = m.group(2)
    return out                                       # vazio vale: parecer sem achado, só os perdidos


def registrar(d: Path, m: dict, julgamentos: dict[int, str], perdidos: int, nota: str = "",
              por: str = "owner", em: str | None = None) -> dict:
    """Monta o registro de calibração da op contra o parecer que vale para ela. Levanta ValueError se o
    índice não existe no parecer."""
    rv, origem = parecer_da_op(d, m)
    if rv is None:
        raise ValueError("a op não tem parecer do reviewer para calibrar")
    achados = rv.get("achados") or []
    for i in julgamentos:
        if not 1 <= i <= len(achados):
            raise ValueError(f"achado {i} não existe: o parecer tem {len(achados)} (1 a {len(achados)})")
    if not julgamentos and achados:
        raise ValueError(f"--calibrar vazio, e o parecer tem {len(achados)} achado(s) para julgar")
    if perdidos < 0:
        raise ValueError("--perdidos não pode ser negativo")
    return {"origem": origem, "veredito": rv.get("veredito"), "modelo": rv.get("modelo"),
            "achados": [{"i": i, "julgamento": julgamentos[i], "severidade": achados[i - 1].get("severidade"),
                         "problema": str(achados[i - 1].get("problema", ""))[:160]} for i in sorted(julgamentos)],
            "nao_julgados": len(achados) - len(julgamentos), "perdidos": perdidos,
            "nota": nota.strip(), "por": por, "em": em}


def placar(cals: list[dict]) -> dict:
    """Precisão e cobertura somadas sobre os registros de calibração das ops."""
    pesos = [_PESO[a["julgamento"]] for c in cals for a in c.get("achados") or []]
    reais = sum(pesos)
    perdidos = sum(int(c.get("perdidos") or 0) for c in cals)
    return {"ops": len(cals), "julgados": len(pesos), "reais": reais, "perdidos": perdidos,
            "precisao": round(reais / len(pesos), 2) if pesos else None,
            "cobertura": round(reais / (reais + perdidos), 2) if (reais + perdidos) else None}


def linhas(veto: dict | None, cal: dict | None) -> list[str]:
    out = []
    if veto:
        out += ["## Veto em sombra (v0.14 — só registra; o veto não está ligado)", "",
                f"- **{'Teria segurado' if veto['teria_segurado'] else 'Teria deixado passar'}** a entrega"
                f" (regra: {veto['regra']})."]
        out += [f"  - {x}" for x in veto["motivos"]]
        out.append("")
    if cal:
        cont = {j: sum(1 for a in cal["achados"] if a["julgamento"] == j) for j in JULGAMENTOS}
        out += ["## Calibração do reviewer (v0.13)", "",
                f"- Julgados: {cont['real']} real, {cont['parcial']} parcial, {cont['falso']} falso"
                + (f", {cal['nao_julgados']} sem julgamento" if cal.get("nao_julgados") else "")
                + f" · perdidos pelo reviewer: {cal['perdidos']} · por {cal['por']} em {cal.get('em')}."]
        out += [f"  - {a['i']}. **{a['julgamento']}** — {a['problema']}" for a in cal["achados"]]
        if cal.get("nota"):
            out.append(f"- {cal['nota']}")
        out.append("")
    return out


def trocar_secao(texto: str, titulo: str, novas: list[str]) -> str:
    """Troca (ou acrescenta no fim) a seção que começa por `## <titulo>`, sem mexer no resto do run
    report, que pode ter análise escrita à mão."""
    sem = re.sub(r"\n## " + re.escape(titulo) + r"[^\n]*\n.*?(?=\n## |\Z)", "", texto, flags=re.DOTALL)
    return sem.rstrip("\n") + ("\n\n" + "\n".join(novas) if novas else "") + "\n"
