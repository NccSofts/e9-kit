#!/usr/bin/env python3
"""
revisar.py — grava o desfecho de uma op NO ALVO: entrou como veio, foi consertada ou descartada.

É o número que o gate não dá. O harness sabe se o gate ficou verde; só quem revisou e mergeou sabe
se a entrega entrou como o operador deixou. Quem grava é quem revisa, na hora do merge — é
julgamento humano, registrado no lugar certo, e não dedução do git.

Uso (onde o repositório do kit é editado; depois, commit da pasta da tarefa):
  python orchestrator/revisar.py --op ops/<op> --como-veio --commit-alvo <sha>
  python orchestrator/revisar.py --op ops/<op> --consertado "o que foi mudado" --commit-alvo <sha>
  python orchestrator/revisar.py --op ops/<op> --descartado "por quê"

Grava `revisao` no metrics.json e acrescenta (ou troca) a seção "## Revisão no alvo" no AAR.md, sem
regerar o resto: o AAR pode ter análise escrita à mão. Stdlib apenas.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")      # console do Windows é cp1252
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

DESFECHOS = {"como_veio": "entrou no alvo como o operador entregou",
             "consertado": "entrou no alvo depois de conserto na revisão",
             "descartado": "não entrou no alvo"}
# A seção de revisão (trocada a cada gravação) e o lembrete que o harness deixa no AAR (some quando
# a revisão é gravada — o pedido foi atendido).
_SECAO = re.compile(r"\n## (?:Revisão no alvo|Próximo passo — revisão no alvo)\n.*?(?=\n## |\Z)", re.DOTALL)


def revisar(op_dir: Path, desfecho: str, nota: str = "", commit_alvo: str | None = None,
            por: str = "sessão do alvo") -> dict:
    if desfecho not in DESFECHOS:
        raise ValueError(f"desfecho inválido: {desfecho}")
    if desfecho != "como_veio" and not nota.strip():
        raise ValueError("consertado e descartado pedem a nota: o que mudou, ou por que não entrou")
    mp = op_dir / "metrics.json"
    m = json.loads(mp.read_text(encoding="utf-8"))
    rev = {"desfecho": desfecho, "nota": nota.strip(), "commit_alvo": commit_alvo, "por": por,
           "em": datetime.now(timezone.utc).date().isoformat()}
    m["revisao"] = rev
    mp.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    aar = op_dir / "AAR.md"
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
