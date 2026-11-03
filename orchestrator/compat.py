"""compat.py — os nomes antigos do E-9, num lugar só (troca de nomenclatura de 2026-11-03).

Até a v0.8.4 o E-9 falava militar: OPORD, AAR, ROE, operador, overwatch, travas do alvo, aceite.
Desde a v0.9 fala SDLC, em inglês: spec, run report, policy, agent, quality gate, guardrails,
acceptance tests. As ops escritas antes (no kit, até a v0.5) ficam como foram escritas, e é este módulo
que as deixa legíveis: o harness e o `revisar.py` leem pelos nomes novos e chamam as funções
daqui, que traduzem o que vier no formato velho. Ninguém mais conhece os nomes antigos.
"""
from __future__ import annotations

from pathlib import Path

# op.json: chave antiga → chave nova
CFG_RENOMES = {
    "alvo": "target",
    "aceite": "acceptance",
    "revisor": "reviewer",
    "operator": "agent",
    "overwatch": "gate",
}

# metrics.json: chave antiga → chave nova (só as que carregam termo do mapa)
METRICS_RENOMES = {
    "aceite": "acceptance",
    "rastreio": "traceability",
    "revisor": "reviewer",
    "revisao": "review",
    "operator": "agent",
    "operator_ok": "agent_ok",
    "operator_rc": "agent_rc",
    "overwatch_pass": "gate_pass",
    "overwatch_tail": "gate_tail",
    "voos_anteriores": "previous_runs",
}

# arquivos e pastas da op: novo primeiro, antigo depois
SPEC = ("spec.md", "OPORD.md")
REPORT = ("report.md", "AAR.md")
ACCEPTANCE = ("acceptance", "aceite")


def _renomear(d: dict, mapa: dict) -> dict:
    """Cópia de `d` com as chaves antigas trocadas pelas novas; se as duas existirem, a nova vale."""
    novo = {}
    for k, v in d.items():
        alvo = mapa.get(k, k)
        if alvo in novo and k != alvo:
            continue                      # a chave nova já veio; a antiga não sobrescreve
        novo[alvo] = v
    return novo


def cfg(d: dict) -> dict:
    return _renomear(d, CFG_RENOMES)


def metrics(d: dict) -> dict:
    return _renomear(d, METRICS_RENOMES)


def _primeiro(op_dir: Path, nomes: tuple[str, ...]) -> Path:
    for nome in nomes:
        if (Path(op_dir) / nome).exists():
            return Path(op_dir) / nome
    return Path(op_dir) / nomes[0]


def spec_path(op_dir: Path) -> Path:
    """`spec.md`, ou o `OPORD.md` de uma op antiga; sem nenhum dos dois, o caminho do novo."""
    return _primeiro(op_dir, SPEC)


def report_path(op_dir: Path) -> Path:
    """`report.md`, ou o `AAR.md` de uma op antiga; sem nenhum dos dois, o caminho do novo."""
    return _primeiro(op_dir, REPORT)


def acceptance_dir(op_dir: Path) -> Path:
    return _primeiro(op_dir, ACCEPTANCE)


# seções da spec: o título novo e o antigo
SECAO_REQUIREMENTS = r"(?:Requirements|Requisitos)"
SECAO_GUARDRAILS = r"(?:Guardrails|Travas do alvo)"
SECAO_DESVIOS = r"Desvios da (?:spec|OPORD)"
