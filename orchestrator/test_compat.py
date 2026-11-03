"""Troca de nomenclatura de 03/11: as ops antigas continuam legíveis pelos nomes novos."""
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location("compat", Path(__file__).with_name("compat.py"))
compat = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compat)

OPS = Path(__file__).resolve().parent.parent / "ops"


def test_op_json_antigo_vira_nomes_novos_e_o_novo_vence():
    cfg = compat.cfg({"alvo": "exemplo", "aceite": {"cmd": "x"}, "operator": {"model": "m"},
                      "overwatch": {"test_cmd": "t"}, "revisor": False, "branch": "feat/x"})
    assert cfg == {"target": "exemplo", "acceptance": {"cmd": "x"}, "agent": {"model": "m"},
                   "gate": {"test_cmd": "t"}, "reviewer": False, "branch": "feat/x"}
    assert compat.cfg({"target": "novo", "alvo": "velho"})["target"] == "novo"
    assert compat.cfg({"alvo": "velho", "target": "novo"})["target"] == "novo"


def test_metrics_antigo_vira_nomes_novos():
    m = compat.metrics({"aceite": {"a": 1}, "rastreio": [], "revisao": {"desfecho": "como_veio"},
                        "operator": {"total_cost_usd": 1}, "overwatch_pass": True, "gate": {"passed": 2}})
    assert set(m) == {"acceptance", "traceability", "review", "agent", "gate_pass", "gate"}


def test_arquivos_da_op_novo_primeiro_antigo_depois():
    d = Path(tempfile.mkdtemp())
    assert compat.spec_path(d).name == "spec.md"            # nenhum: o caminho do novo
    (d / "OPORD.md").write_text("x", encoding="utf-8")
    assert compat.spec_path(d).name == "OPORD.md"
    (d / "spec.md").write_text("x", encoding="utf-8")
    assert compat.spec_path(d).name == "spec.md"
    (d / "aceite").mkdir()
    assert compat.acceptance_dir(d).name == "aceite"
    (d / "AAR.md").write_text("x", encoding="utf-8")
    assert compat.report_path(d).name == "AAR.md"


def test_todas_as_ops_do_repo_tem_spec_e_metrics_legiveis():
    """As ops reais do repositório (escritas com os nomes antigos) continuam achadas."""
    for op in sorted(p for p in OPS.iterdir() if (p / "op.json").exists()):
        assert compat.spec_path(op).exists(), op.name
        cfg = compat.cfg(json.loads((op / "op.json").read_text(encoding="utf-8")))
        assert "alvo" not in cfg and "operator" not in cfg, op.name
        if (op / "metrics.json").exists():
            m = compat.metrics(json.loads((op / "metrics.json").read_text(encoding="utf-8")))
            assert "aceite" not in m and "revisao" not in m, op.name
