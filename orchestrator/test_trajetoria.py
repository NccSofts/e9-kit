#!/usr/bin/env python3
"""test_trajetoria.py — v0.11: a varredura do caminho do agent acha os padrões já vistos em runs reais,
não acusa o que só parece com eles, e só informa (o rc não muda). Roda com pytest ou direto."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_e9_run as t  # noqa: E402
import trajetoria as tj  # noqa: E402


def _bash(cmd, i="tu1", **inp):
    return json.dumps({"type": "assistant", "message": {"id": "m" + i, "content": [
        {"type": "tool_use", "id": i, "name": "Bash", "input": {"command": cmd, **inp}}]}})


def _ferr(nome, i="tu9", **inp):
    return json.dumps({"type": "assistant", "message": {"id": "m" + i, "content": [
        {"type": "tool_use", "id": i, "name": nome, "input": inp}]}})


def _task(sub, tid, **kw):
    return json.dumps({"type": "system", "subtype": sub, "task_id": tid, **kw})


def _padroes(*linhas, **kw):
    return {a["padrao"]: a["n"] for a in tj.varrer("\n".join(linhas), **kw)}


def test_segundo_plano_jogado_pelo_timeout_e_o_relatorio_aguardando():
    """Caso real: a suíte foi para segundo plano sozinha, e o relatório encerrou 'Aguardando notificações'."""
    r = _padroes(_bash("PYTHONPATH=src .venv/bin/pytest -q 2>&1 | tail -10", "a", timeout=300000),
                 _task("task_started", "b1", tool_use_id="a", description="Full test suite"),
                 _task("task_updated", "b1", patch={"is_backgrounded": True}),
                 json.dumps({"type": "result", "result": "Aguardando notificações da suíte."}),
                 timeout_min_ms=600000)
    assert r == {"segundo_plano": 2, "timeout_curto": 1}, r


def test_teste_em_paralelo_com_outro_ainda_no_fundo_e_nao_depois_que_ele_acabou():
    vivo = [_bash(".venv/bin/pytest -q", "a"), _task("task_started", "b1", tool_use_id="a"),
            _task("task_updated", "b1", patch={"is_backgrounded": True})]
    assert _padroes(*vivo, _bash(".venv/bin/pytest -q tests/x.py", "c")).get("teste_em_paralelo") == 1
    acabou = vivo + [_task("task_notification", "b1", status="completed")]
    assert "teste_em_paralelo" not in _padroes(*acabou, _bash(".venv/bin/pytest -q tests/x.py", "c"))
    duas = json.dumps({"type": "assistant", "message": {"id": "m", "content": [
        {"type": "tool_use", "id": "x", "name": "Bash", "input": {"command": "pytest a"}},
        {"type": "tool_use", "id": "y", "name": "Bash", "input": {"command": "pytest b"}}]}})
    assert _padroes(duas) == {"teste_em_paralelo": 1}


def test_o_que_so_parece_teste_nao_e_teste():
    """Visto na calibração: `ps aux | grep pytest` contava como rodada de teste."""
    assert not tj.roda_teste("ps aux | grep pytest | grep -v grep")
    assert not tj.roda_teste("until ! pgrep -f pytest; do sleep 5; done")
    assert tj.roda_teste("CITATO_X=1 PYTHONPATH=src /v/bin/pytest -q tests/a.py 2>&1 | tail")
    assert tj.roda_teste("cd w && python3 -m unittest tests.test_x")


def test_acceptance_aberto_por_edit_e_por_shell_mas_nao_por_rodar_ou_ler():
    ac = ["tests/db/test_aceite_x.py"]
    assert _padroes(_ferr("Edit", file_path="/w/tests/db/test_aceite_x.py"), acceptance=ac) == {"acceptance_aberto": 1}
    assert _padroes(_bash("sed -i 's/a/b/' tests/db/test_aceite_x.py"), acceptance=ac) == {"acceptance_aberto": 1}
    assert _padroes(_bash("echo x > tests/db/test_aceite_x.py"), acceptance=ac) == {"acceptance_aberto": 1}
    # o que a calibração acusava por engano: rodar o teste com 2>&1, e apagar OUTRO arquivo antes
    assert _padroes(_bash("pytest -q tests/db/test_aceite_x.py 2>&1 | tail -30"), acceptance=ac) == {}
    assert _padroes(_bash("rm tests/db/scratch.py; pytest tests/db/test_aceite_x.py"), acceptance=ac) == {}
    assert _padroes(_ferr("Read", file_path="/w/tests/db/test_aceite_x.py"), acceptance=ac) == {}
    assert _padroes(_bash("cp tests/db/test_aceite_x.py /tmp/copia.py"), acceptance=ac) == {}


def test_rede_para_fora_mas_nao_localhost_nem_texto_de_heredoc():
    assert _padroes(_bash("curl -s https://api.exemplo.org/v1/1")) == {"rede": 1}
    assert _padroes(_bash(".venv/bin/pip install requests")) == {"rede": 1}
    assert _padroes(_ferr("WebFetch", url="https://exemplo.org")) == {"rede": 1}
    assert _padroes(_bash("curl -s http://127.0.0.1:8000/saude")) == {}
    assert _padroes(_bash("python3 - <<'EOF'\ns = 'https://exemplo.org/x'\nEOF")) == {}


def test_varredura_nunca_levanta_e_limpo_e_lista_vazia():
    assert tj.varrer("lixo\n{não é json\n[1]\n") == []
    assert tj.linhas([]) == ["## Trajetória do agent (v0.11 — só informa, não muda o rc)", "",
                             "- Nenhum dos padrões conhecidos no caminho do agent.", ""]


def test_run_com_segundo_plano_grava_a_trajetoria_e_o_rc_nao_muda():
    eventos = [_bash("sleep 1 &", "a", run_in_background=True),
               json.dumps({"type": "result", "is_error": False,
                           "result": "fiz\n## Desvios da spec\nnenhum"})]
    code = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n')\n"
            + "".join(f"print({e!r})\n" for e in eventos))
    rc, m, _ = t._live(code)
    assert rc == 0, m["status"]
    assert [a["padrao"] for a in m["trajetoria"]] == ["segundo_plano"], m["trajetoria"]
    rep = (t._ULTIMA_OP / "report.md").read_text(encoding="utf-8")
    assert "## Trajetória do agent" in rep and "**segundo_plano** ×1" in rep


def test_dashboard_mostra_a_trajetoria():
    import test_monitor as tv
    base, d = tv._ops([tv.RESULT], metrics={"status": "OK", "rc": 0, "trajetoria": [
        {"padrao": "rede", "o_que": "x", "n": 1, "exemplos": ["$ curl"]}]})
    assert tv.v.aar(tv.v._metrics(d))["trajetoria"][0]["padrao"] == "rede"
    assert tv.v.placar()["trajetoria"] == {"varridos": 1, "com_achado": 1}
    assert "Trajetória do agent" in tv.v.PAGINA


def test_run_antigo_e_varrido_pelo_dashboard_sem_reescrever_o_metrics():
    import test_monitor as tv
    base, d = tv._ops([_bash("pytest -q &", "a")], metrics={"status": "OK", "rc": 0})
    antes = (d / "metrics.json").read_text(encoding="utf-8")
    a = tv.v.aar(tv.v._metrics(d))
    assert a["trajetoria_retroativa"] and a["trajetoria"][0]["padrao"] == "segundo_plano", a["trajetoria"]
    assert (d / "metrics.json").read_text(encoding="utf-8") == antes, "o histórico não se reescreve"
    assert tv.v.placar()["trajetoria"] == {"varridos": 1, "com_achado": 1}


if __name__ == "__main__":
    falhas = 0
    for nome, f in sorted(globals().items()):
        if nome.startswith("test_") and callable(f):
            try:
                f(); print(f"  ok   {nome}")
            except AssertionError as e:
                falhas += 1; print(f"  FAIL {nome} — {e}")
    sys.exit(1 if falhas else 0)
