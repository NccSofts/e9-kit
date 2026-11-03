#!/usr/bin/env python3
"""
revisar.py — grava o desfecho de uma op NO TARGET: entrou como veio, foi consertada ou descartada.

É o número que o gate não dá. O harness sabe se o gate ficou verde; só quem revisou e mergeou sabe se a
entrega entrou como o agente deixou. Quem grava é quem revisa, na hora do merge — é julgamento humano.

Uso:
  python3 orchestrator/revisar.py --op ops/<op> --como-veio --commit-alvo <sha>
  python3 orchestrator/revisar.py --op ops/<op> --consertado "o que foi mudado" --commit-alvo <sha>
  python3 orchestrator/revisar.py --op ops/<op> --descartado "por quê"

Grava `review` no metrics.json e acrescenta (ou troca) a seção "## Revisão no alvo" no run report, sem
regerar o resto: o run report pode ter análise escrita à mão. Stdlib apenas.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compat  # noqa: E402 — os nomes antigos das ops de antes da v0.9

try:
    sys.stdout.reconfigure(encoding="utf-8")      # console do Windows é cp1252
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

DESFECHOS = {"como_veio": "entrou no alvo como o agente entregou",
             "consertado": "entrou no alvo depois de conserto na revisão",
             "descartado": "não entrou no alvo"}
# A seção de revisão (trocada a cada gravação) e o lembrete que o harness deixa no run report (some quando
# a revisão é gravada — o pedido foi atendido).
_SECAO = re.compile(r"\n## (?:Revisão no alvo|Próximo passo — revisão no alvo)\n.*?(?=\n## |\Z)", re.DOTALL)


def revisar(op_dir: Path, desfecho: str, nota: str = "", commit_alvo: str | None = None,
            por: str = "sessão do alvo") -> dict:
    if desfecho not in DESFECHOS:
        raise ValueError(f"desfecho inválido: {desfecho}")
    if desfecho != "como_veio" and not nota.strip():
        raise ValueError("consertado e descartado pedem a nota: o que mudou, ou por que não entrou")
    mp = op_dir / "metrics.json"
    m = compat.metrics(json.loads(mp.read_text(encoding="utf-8")))
    rev = {"desfecho": desfecho, "nota": nota.strip(), "commit_alvo": commit_alvo, "por": por,
           "em": datetime.now(timezone.utc).date().isoformat()}
    m["review"] = rev
    mp.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    aar = compat.report_path(op_dir)
    if aar.exists():
        secao = ("\n## Revisão no alvo\n\n"
                 f"- **Desfecho:** {DESFECHOS[desfecho]}"
                 + (f" — `{commit_alvo}`" if commit_alvo else "") + ".\n"
                 + (f"- **O que mudou na revisão:** {rev['nota']}\n" if rev["nota"] else "")
                 + f"- Registrado por {por} em {rev['em']}.\n")
        texto = aar.read_text(encoding="utf-8")
        texto = _SECAO.sub("", texto).rstrip("\n") + "\n" + secao
        aar.write_text(texto, encoding="utf-8", newline="\n")
    return rev


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="E-9 — grava o desfecho de uma op no alvo")
    ap.add_argument("--op", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--como-veio", action="store_true")
    g.add_argument("--consertado", metavar="NOTA")
    g.add_argument("--descartado", metavar="NOTA")
    ap.add_argument("--commit-alvo")
    ap.add_argument("--por", default="sessão do alvo")
    a = ap.parse_args(argv)
    desfecho = "como_veio" if a.como_veio else "consertado" if a.consertado else "descartado"
    try:
        rev = revisar(Path(a.op), desfecho, a.consertado or a.descartado or "", a.commit_alvo, a.por)
    except (OSError, ValueError) as e:
        print(f"[E-9 REVISAR] recusado: {e}", file=sys.stderr)
        return 2
    print(f"[E-9 REVISAR] {Path(a.op).name}: {DESFECHOS[rev['desfecho']]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
