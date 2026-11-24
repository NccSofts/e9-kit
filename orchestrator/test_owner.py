#!/usr/bin/env python3
"""test_owner.py — v0.10: o tempo do owner (spec e revisão) chega ao metrics, ao run report e ao
Monitor, e número inválido é recusado antes de gravar. Roda com pytest ou direto."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_e9_run as t  # noqa: E402 — o _live, o repo descartável e o agente falso
import owner  # noqa: E402

e9 = t.e9


def _op_com_metrics(m):
    d = Path(tempfile.mkdtemp())
    (d / "metrics.json").write_text(json.dumps(m), encoding="utf-8")
    (d / "report.md").write_text("# Run report\n\n3. Por quê? → análise à mão\n", encoding="utf-8")
    return d


def test_spec_min_do_op_json_vai_ao_metrics_e_ao_report():
    rc, m, _ = t._live(t.AGENTE_CUMPRE, extra={"owner": {"spec_min": 25}})
    assert rc == 0, m
    assert m["owner_time"] == {"spec_min": 25.0}
    rep = (t._ULTIMA_OP / "report.md").read_text(encoding="utf-8")
    assert "## Tempo do owner" in rep and "Spec: 25 min" in rep and "revisão: não registrado" in rep


def test_owner_invalido_no_op_json_barra_no_dry_run():
    for ruim in ({"spec_min": "25min"}, {"spec_min": -3}, "25"):
        op = t._mk_op({"owner": ruim})
        with t.blocks():
            e9.load_op(op)


def test_revisar_grava_os_minutos_e_troca_a_secao():
    rv = t._revisar()
    d = _op_com_metrics({"status": "OK", "rc": 0, "agent": {"duration_ms": 600000}})
    assert rv.main(["--op", str(d), "--spec-min", "20"]) == 0
    assert rv.main(["--op", str(d), "--como-veio", "--commit-alvo", "abc", "--revisao-min", "15"]) == 0
    m = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
    assert m["owner_time"]["spec_min"] == 20 and m["owner_time"]["review_min"] == 15
    assert m["review"]["desfecho"] == "como_veio"
    rep = (d / "report.md").read_text(encoding="utf-8")
    assert rep.count("## Tempo do owner") == 1 and "análise à mão" in rep
    assert "owner no total: **35 min**" in rep and "3.5 min do owner" in rep


def test_revisar_recusa_minuto_invalido_sem_gravar_nada():
    rv = t._revisar()
    d = _op_com_metrics({"status": "OK", "rc": 0})
    antes = (d / "metrics.json").read_text(encoding="utf-8")
    assert rv.main(["--op", str(d), "--como-veio", "--revisao-min", "dez"]) == 2
    assert (d / "metrics.json").read_text(encoding="utf-8") == antes, "nada gravado: nem o desfecho"
    assert rv.main(["--op", str(d)]) == 2, "sem desfecho e sem minutos não há o que gravar"


def test_placar_soma_so_quem_registrou():
    voos = [{"owner_time": {"spec_min": 20, "review_min": 10}, "agent": {"duration_ms": 1200000}},
            {"owner_time": {"spec_min": 5}}, {"agent": {"duration_ms": 60000}}]
    assert owner.placar(voos) == {"ops": 2, "spec_min": 25, "spec_n": 2, "review_min": 10,
                                  "review_n": 1, "agente_min": 20.0}


def test_Monitor_mostra_o_tempo_do_owner():
    import test_monitor as tv
    base, d = tv._ops([tv.RESULT], metrics={"status": "OK — gate verde", "rc": 0,
                                            "owner_time": {"spec_min": 30, "review_min": 8}})
    assert tv.v.aar(tv.v._metrics(d))["owner"] == {"spec_min": 30, "review_min": 8}
    p = tv.v.placar()
    assert p["owner"]["spec_min"] == 30 and p["owner"]["review_min"] == 8
    assert "Tempo do owner" in tv.v.PAGINA and "tempo do owner" in tv.v.PAGINA


if __name__ == "__main__":
    falhas = 0
    for nome, f in sorted(globals().items()):
        if nome.startswith("test_") and callable(f):
            try:
                f(); print(f"  ok   {nome}")
            except AssertionError as e:
                falhas += 1; print(f"  FAIL {nome} — {e}")
    sys.exit(1 if falhas else 0)
