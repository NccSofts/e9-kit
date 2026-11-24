"""
owner.py — o tempo do owner: os minutos de spec e de revisão de cada op (v0.10, 2026-11-24).

O harness mede o agent (custo, turnos, minutos) e as próprias etapas, e não media o tempo que costuma
ser o gargalo: o de quem escreve a spec e o de quem revisa a entrega. Sem esse número, "o gargalo saiu
do código" é afirmação, não medida. Quem registra é o owner, porque só ele sabe:

  - spec: no `op.json`, `"owner": {"spec_min": 25}`, ou depois, `revisar.py --op ops/<op> --spec-min 25`;
  - revisão: `revisar.py --op ops/<op> --como-veio --revisao-min 12` (ou só `--revisao-min`).

Grava `owner_time` no metrics.json e a seção "## Tempo do owner" no run report. Stdlib apenas.
"""
from __future__ import annotations

import re

SECAO = "## Tempo do owner"
_SECAO_RE = re.compile(r"\n## Tempo do owner[^\n]*\n.*?(?=\n## |\Z)", re.DOTALL)


def minutos(valor, nome: str) -> float:
    """Minutos como número não negativo; recusa o resto (o owner digita, e "25min" não é número)."""
    try:
        v = float(valor)
    except (TypeError, ValueError):
        raise ValueError(f"{nome} tem de ser um número de minutos, veio {valor!r}") from None
    if v < 0 or v != v:
        raise ValueError(f"{nome} não pode ser negativo, veio {valor!r}")
    return round(v, 1)


def do_op_json(cfg: dict) -> dict:
    """O `owner.spec_min` do op.json, se houver. Valor inválido é recusa: o harness sai antes de voar."""
    o = cfg.get("owner")
    if o is None:
        return {}
    if not isinstance(o, dict):
        raise ValueError("`owner` no op.json é um objeto, como {\"spec_min\": 25}")
    return {"spec_min": minutos(o["spec_min"], "owner.spec_min")} if "spec_min" in o else {}


def agente_min(m: dict) -> float | None:
    ms = (m.get("agent") or {}).get("duration_ms")
    return round(ms / 60000, 1) if isinstance(ms, (int, float)) else None


def linhas(m: dict) -> list[str]:
    """A seção do run report. Vazia se o owner ainda não registrou nada."""
    t = m.get("owner_time") or {}
    spec, rev = t.get("spec_min"), t.get("review_min")
    if spec is None and rev is None:
        return []
    def f(v):
        return f"{v:g} min" if v is not None else "não registrado"
    total = sum(v for v in (spec, rev) if v is not None)
    ag = agente_min(m)
    run = round(m["wall_clock_s"] / 60, 1) if m.get("wall_clock_s") else None
    out = [SECAO + " (v0.10)", "",
           f"- Spec: {f(spec)} · revisão: {f(rev)} · owner no total: **{total:g} min**.",
           f"- Agente: {f(ag)} · run inteiro: {f(run)}."]
    if ag:
        out.append(f"- Para cada minuto de agente, {total / ag:.1f} min do owner"
                   + (" (falta a revisão)" if rev is None else "") + ".")
    return out + [""]


def trocar_secao(texto: str, m: dict) -> str:
    """Troca (ou acrescenta) a seção no run report, sem mexer no resto: ele pode ter análise à mão."""
    sem = _SECAO_RE.sub("", texto).rstrip("\n")
    novas = linhas(m)
    return sem + ("\n\n" + "\n".join(novas) if novas else "") + "\n"


def placar(voos: list[dict]) -> dict:
    """Os números do dashboard: o que o owner gastou nas ops em que registrou, ao lado do agente."""
    com = [v for v in voos if (v.get("owner_time") or {})]
    spec = [v["owner_time"]["spec_min"] for v in com if v["owner_time"].get("spec_min") is not None]
    rev = [v["owner_time"]["review_min"] for v in com if v["owner_time"].get("review_min") is not None]
    ag = [a for a in (agente_min(v) for v in com) if a is not None]
    return {"ops": len(com), "spec_min": round(sum(spec), 1), "spec_n": len(spec),
            "review_min": round(sum(rev), 1), "review_n": len(rev), "agente_min": round(sum(ag), 1)}
