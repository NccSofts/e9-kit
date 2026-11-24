#!/usr/bin/env python3
"""test_evals.py — v0.15: os evals do harness escolhem a base certa da op, reexecutam numa cópia com
branch e worktree próprias, apagam o que criaram no target, param no teto de custo e gravam o resumo que
o dashboard lê. O agent é falso; nenhum caso gasta crédito. Roda com pytest ou direto."""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_e9_run as t  # noqa: E402 — o ambiente de teste (sem aviso, sem reviewer, slots próprios)
import evals  # noqa: E402

e9 = t.e9
AGENTE = ("from pathlib import Path; import json, subprocess\n"
          "base = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()\n"
          "Path('a.py').write_text('x = 1\\n')\n"
          "print(json.dumps({'type': 'result', 'is_error': False, 'total_cost_usd': 0.5,\n"
          "                  'result': 'base ' + base + '\\n## Desvios da spec\\nnenhum'}))\n")


def _cenario():
    """Um target com dois commits (a op original partiu do primeiro) e a pasta de ops com a op."""
    base = Path(tempfile.mkdtemp())
    repo = base / "repo"
    e9.run(["git", "init", "-q", "-b", "main", str(repo)])
    for msg in ("c1", "c2"):
        e9.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                "--allow-empty", "-m", msg])
    _, c1 = e9.run(["git", "-C", str(repo), "rev-parse", "HEAD~1"])
    fake = base / "fake.py"
    fake.write_text(AGENTE, encoding="utf-8")
    ops = base / "ops"
    op = ops / "2026-11-01-original"
    op.mkdir(parents=True)
    (op / "spec.md").write_text("# spec\n", encoding="utf-8")
    (op / "op.json").write_text(json.dumps({
        "op": "original", "target_repo": str(repo), "branch": "feat/original",
        "worktree": str(base / "wts" / "original"), "plan_gate": False, "expected_artifacts": ["a.py"],
        "agent": {"cmd_template": f'"{sys.executable}" "{fake}"', "timeout_s": 30},
        "gate": {"test_cmd": "true"}}), encoding="utf-8")
    (op / "metrics.json").write_text(json.dumps({
        "status": "OK — gate verde", "rc": 0, "base_commit": c1.strip(), "wall_clock_s": 600,
        "agent": {"total_cost_usd": 1.2}, "review": {"desfecho": "como_veio"}}), encoding="utf-8")
    smoke = ops / "2026-11-01-smoke-x"
    smoke.mkdir()
    (smoke / "op.json").write_text("{}", encoding="utf-8")
    (smoke / "metrics.json").write_text(json.dumps({"status": "OK"}), encoding="utf-8")
    evals.OPS, evals.EVALS = ops, base / "evals"
    return base, repo, c1.strip()


def test_listar_tira_treino_e_diz_a_base():
    base, repo, c1 = _cenario()
    lista = evals.elegiveis()
    assert [e["op"] for e in lista] == ["2026-11-01-original"], "smoke não é amostra"
    assert lista[0]["elegivel"] and lista[0]["base"] == c1 and lista[0]["original"]["rc"] == 0


def test_base_pelo_pai_do_commit_ou_pela_data():
    base, repo, c1 = _cenario()
    _, c2 = e9.run(["git", "-C", str(repo), "rev-parse", "HEAD"])
    cfg = {"target_repo": str(repo)}
    assert evals.base_da_op(Path("."), cfg, {"commit_sha": c2.strip()})[0] == c1
    b, origem = evals.base_da_op(Path("."), cfg, {"started_at": "2099-01-01T00:00:00"})
    assert b == c2.strip() and "main em" in origem
    assert evals.base_da_op(Path("."), {"target_repo": "/nao/existe"}, {})[0] is None


def test_eval_roda_na_base_da_op_limpa_o_target_e_grava_o_resumo():
    base, repo, c1 = _cenario()
    res = evals.rodar(["2026-11-01-original"], live=True, teto_usd=5, ev_id="t1")
    item = res["itens"][0]
    assert item["eval"]["rc"] == 0, item
    m = json.loads((base / "evals" / "t1" / "2026-11-01-original" / "metrics.json").read_text(encoding="utf-8"))
    assert c1 in m["agent"]["result_text"], "a worktree do eval nasce no commit de base da op original"
    assert m["base_commit"] == c1
    assert item["limpeza"] == ["worktree removida", "branch apagada"], item.get("limpeza")
    _, branches = e9.run(["git", "-C", str(repo), "branch", "--list", "feat/e9-eval-*"])
    assert not branches.strip(), "nada do eval fica no target"
    g = res["agregado"]
    assert g == {"rodaram": 1, "rc0_original": 1, "rc0_eval": 1, "mesmo_rc": 1, "piorou": [], "melhorou": [],
                 "custo_original": 1.2, "custo_eval": 0.5, "min_original": 10.0,
                 "min_eval": item["eval"]["min"]}, g
    gravado = json.loads((base / "evals" / "t1.json").read_text(encoding="utf-8"))
    assert gravado["id"] == "t1" and gravado["agregado"]["rodaram"] == 1


def test_teto_de_custo_para_antes_da_proxima_op():
    base, repo, c1 = _cenario()
    res = evals.rodar(["2026-11-01-original", "2026-11-01-original"], live=True, teto_usd=0.4, ev_id="t2")
    assert "eval" in res["itens"][0] and "teto" in res["itens"][1]["fora"], res["itens"]


def test_dry_run_prepara_a_copia_sem_voar_e_nao_grava_resumo():
    base, repo, c1 = _cenario()
    res = evals.rodar(["2026-11-01-original", "nao-existe"], live=False, teto_usd=5, ev_id="t3")
    assert res["itens"][0]["pronta"].endswith("2026-11-01-original") and "fora" in res["itens"][1]
    cfg = json.loads((base / "evals" / "t3" / "2026-11-01-original" / "op.json").read_text(encoding="utf-8"))
    assert cfg["branch"].startswith("feat/e9-eval-t3-") and cfg["base_ref"] == c1 and cfg["aviso"] is False
    assert not (base / "evals" / "t3.json").exists()


def test_status_antigo_do_overwatch_vira_rc3_no_eval_e_no_dashboard():
    """Até a v0.9 o harness escrevia "overwatch vermelho"; a troca de nomes deixou essas ops sem rc."""
    st = "ABORT — overwatch vermelho (branch deixada p/ revisão, sem commit-como-concluído)"
    assert evals.resumo_do_run({"status": st})["rc"] == 3
    import test_monitor as tv
    assert tv.v.classificar(st)["rotulo"] == "VERMELHO" and tv.v.classificar(st)["rc"] == 3


def test_comparar_marca_quem_piorou():
    g = evals.comparar([{"op": "a", "original": {"rc": 0}, "eval": {"rc": 13}},
                        {"op": "b", "original": {"rc": 3}, "eval": {"rc": 0}}, {"op": "c", "original": {"rc": 0}}])
    assert g["piorou"] == ["a"] and g["melhorou"] == ["b"] and g["rodaram"] == 2


def test_dashboard_mostra_o_eval_mais_recente():
    import test_monitor as tv
    d = Path(tempfile.mkdtemp())
    (d / "20261101-0000.json").write_text(json.dumps({"id": "velho"}), encoding="utf-8")
    (d / "20261124-1200.json").write_text(json.dumps({"id": "novo", "agregado": {}}), encoding="utf-8")
    os.environ["E9_EVALS_DIR"] = str(d)
    try:
        assert evals.ultimo()["id"] == "novo"
    finally:
        os.environ.pop("E9_EVALS_DIR")
    assert "Evals do harness" in tv.v.PAGINA and "api/evals" in tv.v.PAGINA


def test_copia_em_eval_nao_marca_a_op_original_em_voo():
    """A cópia do eval tem o nome da op original, e a original aparecia EM VOO."""
    import test_monitor as tv
    base, d = tv._ops([tv.RESULT], metrics={"status": "OK", "rc": 0})
    orig = tv.v._runs_vivos
    tv.v._runs_vivos = lambda: [Path(base).resolve().parent / "evals" / "20261124-1544" / d.name]
    try:
        assert tv.v.em_voo() == set() and tv.v.em_eval() == [f"20261124-1544/{d.name}"]
        tv.v._runs_vivos = lambda: [d.resolve()]
        assert tv.v.em_voo() == {d.name} and tv.v.em_eval() == []
    finally:
        tv.v._runs_vivos = orig


if __name__ == "__main__":
    falhas = 0
    for nome, f in sorted(globals().items()):
        if nome.startswith("test_") and callable(f):
            try:
                f(); print(f"  ok   {nome}")
            except AssertionError as e:
                falhas += 1; print(f"  FAIL {nome} — {e}")
    sys.exit(1 if falhas else 0)
