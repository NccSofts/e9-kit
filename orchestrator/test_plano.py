#!/usr/bin/env python3
"""test_plano.py — v0.12: o gate de plano para o run ANTES da implementação (rc 13) quando o plano sai
do escopo, não vem, inclui acceptance test ou mexe na worktree; aprovado, a implementação retoma a
sessão do plano. Roda com pytest ou direto."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_e9_run as t  # noqa: E402
import plano  # noqa: E402

e9 = t.e9
SID = "0b1c2d3e-aaaa-bbbb-cccc-1234567890ab"

# O agent falso: na fase de plano devolve o plano que o teste mandou (e, se pedido, mexe na worktree);
# na implementação escreve a.py. Grava os argumentos de cada chamada para o teste conferir o --resume.
FALSO = r'''
import json, sys
from pathlib import Path
cfg = json.loads(Path(__file__).with_name("comportamento.json").read_text(encoding="utf-8"))
brief = Path(sys.argv[-1]).read_text(encoding="utf-8")
log = Path(__file__).with_name("chamadas.json")
chamadas = json.loads(log.read_text(encoding="utf-8")) if log.exists() else []
chamadas.append({"argv": sys.argv[1:-1], "fase": "plano" if "FASE DE PLANO" in brief else "impl",
                 "brief_tem_plano": "PLANO APROVADO" in brief or "IMPLEMENTAÇÃO (v0.12)" in brief})
log.write_text(json.dumps(chamadas), encoding="utf-8")
if "FASE DE PLANO" in brief:
    if cfg.get("mexe"):
        Path("b.py").write_text("y = 1\n")
    print(json.dumps({"type": "result", "is_error": False, "session_id": cfg.get("sid"),
                      "num_turns": 3, "total_cost_usd": 0.02, "result": cfg["resposta"]}))
else:
    Path("a.py").write_text("x = 1\n")
    print(json.dumps({"type": "result", "is_error": False, "result": "fiz\n## Desvios da spec\nnenhum"}))
'''


def _run(resposta, *, mexe=False, com_claude_p=True, extra=None, aceite=None, sid=SID):
    base = Path(tempfile.mkdtemp())
    fake = base / "fake.py"
    fake.write_text(FALSO, encoding="utf-8")
    (base / "comportamento.json").write_text(json.dumps({"resposta": resposta, "mexe": mexe, "sid": sid}),
                                             encoding="utf-8")
    meio = " claude -p " if com_claude_p else " "
    op_extra = {"plan_gate": True, "optional_artifacts": ["docs/"],
                "operator": {"cmd_template": f'"{sys.executable}" "{fake}"{meio}{{brief_file}}', "timeout_s": 30},
                **(extra or {})}
    rc, m, wt = t._live("", extra=op_extra, aceite=aceite)
    chamadas = json.loads((base / "chamadas.json").read_text(encoding="utf-8"))
    return rc, m, wt, chamadas


def _plano(*arquivos):
    return "Li o código.\n```json\n" + json.dumps({"arquivos": list(arquivos)}) + "\n```"


def test_plano_no_escopo_aprova_e_a_implementacao_retoma_a_sessao():
    rc, m, wt, ch = _run(_plano("a.py", "docs/notas.md"))
    assert rc == 0, m["status"]
    assert [c["fase"] for c in ch] == ["plano", "impl"], ch
    assert ch[0]["argv"][-2:] == ["claude", "-p"], ch[0]
    assert ch[1]["argv"][-4:] == ["claude", "--resume", SID, "-p"], "a implementação retoma a sessão do plano"
    assert ch[1]["brief_tem_plano"]
    assert m["plan"]["veredito"].startswith("plano aprovado") and m["plan"]["retomada"] is True
    assert m["plan"]["custo"] == 0.02 and m["plan"]["fora"] == []
    assert [f["fase"] for f in m["fases"]][:4] == ["preparo", "base", "plano", "agente"]
    rep = (t._ULTIMA_OP / "report.md").read_text(encoding="utf-8")
    assert "## Gate de plano (v0.12)" in rep and "retomou a sessão" in rep


def test_plano_fora_do_escopo_para_com_rc13_sem_implementar():
    rc, m, wt, ch = _run(_plano("a.py", "src/outro.py"))
    assert rc == plano.RC_PLANO == 13, m["status"]
    assert [c["fase"] for c in ch] == ["plano"], "a implementação não pode ter rodado"
    assert not (wt / "a.py").exists()
    assert m["plan"]["fora"] == ["src/outro.py"] and "ABORT (plano)" in m["status"]
    assert m["committed"] is False


def test_plano_que_mexe_na_worktree_para():
    rc, m, _, ch = _run(_plano("a.py"), mexe=True)
    assert rc == 13 and "mexeu na worktree" in m["status"], m["status"]
    assert len(ch) == 1


def test_sem_plano_legivel_para():
    rc, m, _, ch = _run("Vou fazer a.py e pronto.")
    assert rc == 13 and "não devolveu o plano" in m["status"], m["status"]
    assert m["plan"]["fim_da_resposta"].endswith("pronto.")


def test_acceptance_test_no_plano_para():
    rc, m, _, _ = _run(_plano("a.py", "tests_aceite/check_aceite.py"), extra=t.ACEITE_CMD, aceite=t._aceite())
    assert rc == 13 and m["plan"]["acceptance"] == ["tests_aceite/check_aceite.py"], m["status"]


def test_plano_que_esquece_obrigatorio_so_avisa():
    rc, m, _, _ = _run(_plano("docs/notas.md"))
    assert rc == 0, m["status"]
    assert m["plan"]["faltam"] == ["a.py"]
    assert "plano não cobre" in (t._ULTIMA_OP / "report.md").read_text(encoding="utf-8")


def test_sem_claude_p_para_retomar_a_implementacao_comeca_nova_com_o_plano_anexado():
    rc, m, _, ch = _run(_plano("a.py"), com_claude_p=False)
    assert rc == 0, m["status"]
    assert m["plan"]["retomada"] is False and "--resume" not in ch[1]["argv"]
    assert ch[1]["brief_tem_plano"], "o plano aprovado vai ao briefing novo"


def test_plan_gate_desligado_voa_como_antes():
    rc, m, _ = t._live(t.AGENTE_CUMPRE)                     # o _mk_op dos testes traz plan_gate false
    assert rc == 0 and "plan" not in m


def test_ler_entende_caminho_absoluto_e_sem_cerca():
    assert plano.ler('{"arquivos": ["/w/x/a.py", "./b.py", "a.py"]}', "/w/x") == ["a.py", "b.py"]
    assert plano.ler("texto\n```json\n{\"arquivos\": [1]}\n```") is None
    assert plano.ler("nada") is None
    assert plano.retomar('claude -p "$(cat b)" --model m', SID) == f'claude --resume {SID} -p "$(cat b)" --model m'
    assert plano.retomar("claude -p x", "id; rm -rf /") is None, "id de sessão só com hexadecimal e hífen"


def test_dashboard_rotula_o_rc13_e_mostra_o_plano():
    import test_monitor as tv
    c = tv.v.classificar("ABORT (plano) — plano recusado — fora do escopo: ['x.py']")
    assert c["rotulo"] == "PLANO" and c["rc"] == 13, c
    base, d = tv._ops([tv.RESULT], metrics={"status": "OK", "rc": 0,
                                            "plan": {"veredito": "plano aprovado", "arquivos": ["a.py"]}})
    assert tv.v.aar(tv.v._metrics(d))["plano"]["arquivos"] == ["a.py"]


def test_feed_troca_do_plano_para_a_implementacao():
    import test_monitor as tv
    base = Path(tempfile.mkdtemp())
    d = base / "2026-11-24-plano"
    d.mkdir()
    (d / "plan.jsonl").write_text(tv.ACAO + "\n", encoding="utf-8", newline="\n")
    import os
    os.environ["E9_OPS_DIR"] = str(base)
    r = tv.v.ler_feed(d, 0)
    assert r["arquivo"] == "plan.jsonl" and r["eventos"]
    (d / "agent.jsonl").write_text(tv.RESULT + "\n", encoding="utf-8", newline="\n")
    r2 = tv.v.ler_feed(d, r["pos"], arq="plan.jsonl")
    assert r2["arquivo"] == "agent.jsonl" and r2["eventos"][0]["tipo"] == "fim", "recomeça do zero no arquivo novo"


if __name__ == "__main__":
    falhas = 0
    for nome, f in sorted(globals().items()):
        if nome.startswith("test_") and callable(f):
            try:
                f(); print(f"  ok   {nome}")
            except AssertionError as e:
                falhas += 1; print(f"  FAIL {nome} — {e}")
    sys.exit(1 if falhas else 0)
