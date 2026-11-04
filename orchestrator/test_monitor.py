#!/usr/bin/env python3
"""test_monitor.py — o Monitor E-9 só lê, não sai da pasta de ops, não engasga e só escuta nesta máquina.

Roda com `python3 orchestrator/test_monitor.py` (stdlib) ou pytest.
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")   # console do Windows é cp1252
except Exception:
    pass

_spec = importlib.util.spec_from_file_location("monitor", Path(__file__).with_name("monitor.py"))
v = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v)

RESULT = json.dumps({"type": "result", "is_error": False, "num_turns": 3,
                     "total_cost_usd": 0.1, "result": "Fiz a.py. Gate verde: 10 passed."})
ACAO = json.dumps({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "Edit", "input": {"file_path": "/w/a.py"}},
    {"type": "text", "text": "Vou rodar o gate."}]}})
ERRO = json.dumps({"type": "user", "message": {"content": [
    {"type": "tool_result", "is_error": True, "content": "permissão negada"}]}})


def _ops(linhas, *, metrics=None):
    base = Path(tempfile.mkdtemp())
    d = base / "2026-10-25-teste"
    d.mkdir()
    (d / "agent.jsonl").write_text("\n".join(linhas) + "\n", encoding="utf-8", newline="\n")
    if metrics is not None:
        (d / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    os.environ["E9_OPS_DIR"] = str(base)
    return base, d


def test_eventos_de_cada_tipo():
    assert v.eventos(ACAO) == [{"tipo": "acao", "texto": "Edit /w/a.py"},
                               {"tipo": "fala", "texto": "Vou rodar o gate."}]
    assert v.eventos(ERRO)[0]["tipo"] == "erro"
    fim = v.eventos(RESULT)[0]
    assert fim["tipo"] == "fim" and "10 passed" in fim["relatorio"]
    assert v.eventos("lixo") == [] and v.eventos("[1,2]") == []


def test_feed_consome_so_linha_inteira_e_avanca():
    _, d = _ops([ACAO])
    with (d / "agent.jsonl").open("a", encoding="utf-8", newline="\n") as h:
        h.write(RESULT[:20])                                   # linha pela metade
    r = v.ler_feed(d, 0)
    assert len(r["eventos"]) == 2
    tam_inteiro = len((ACAO + "\n").encode("utf-8"))
    assert r["pos"] == tam_inteiro
    assert v.ler_feed(d, r["pos"])["eventos"] == []        # a meia linha espera


def test_feed_recomeca_se_o_transcript_encolheu():
    _, d = _ops([ACAO])
    r = v.ler_feed(d, 10_000)
    assert r["pos"] > 0 and len(r["eventos"]) == 2


def test_listar_ops_e_status_do_harness():
    _, d = _ops([ACAO, RESULT], metrics={"status": "OK — verde", "gate": {"passed": 10}})
    ops = v.listar_ops()
    assert ops[0]["op"] == d.name and ops[0]["status"] == "OK — verde"
    assert ops[0]["rotulo"] == "VERDE" and ops[0]["rc"] == 0
    assert v.ler_feed(d, 0)["aar"]["gate"] == {"passed": 10}


VERMELHO = "ABORT — gate vermelho (branch deixada p/ revisão, sem commit-como-concluído)"


def test_gate_vermelho_nao_vira_barrada():
    """Gate vermelho não pode aparecer como BARRADA, embora o status contenha "commit"."""
    assert v.classificar(VERMELHO)["rotulo"] == "VERMELHO" and v.classificar(VERMELHO)["rc"] == 3
    barrada = v.classificar("ABORT — gate verde, mas commit falhou: arquivo(s) alterado(s) fora")
    assert barrada["rotulo"] == "BARRADA" and barrada["rc"] == 4


def test_classificar_cada_desfecho_com_o_codigo():
    casos = {
        "OK — gate verde, commitado na branch": ("VERDE", 0),
        "ABORT — linha de base VERMELHA: o gate já falha no main": ("BASE", 7),
        "ABORT — worktree não está limpa antes do voo: [...]": ("BASE", 8),
        "ABORT — o aceite já PASSA antes do agente (nada foi despachado)": ("ACCEPTANCE", 9),
        "ABORT — aceite alterado pelo agente: ['t.py']": ("ACCEPTANCE", 10),
        "ABORT — gate verde, mas o aceite não passou depois do agente": ("ACCEPTANCE", 10),
        "ABORT — agente terminou em erro (is_error): 401": ("FALHA", 6),
        "ABORT — agente estourou o teto de tempo (TIMEOUT), sem gate nem commit": ("FALHA", 5),
    }
    for status, (rotulo, rc) in casos.items():
        c = v.classificar(status)
        assert (c["rotulo"], c["rc"]) == (rotulo, rc), (status, c)
    assert v.classificar(VERMELHO, rc=99)["rc"] == 99          # o rc gravado pelo harness vence
    assert v.classificar(VERMELHO, voando=True)["rotulo"] == "RODANDO"


def test_op_abortada_antes_do_agente_aparece_no_quadro():
    """Linha de base vermelha ou aceite que já passa não deixam transcript, só o metrics.json."""
    base, _ = _ops([ACAO])
    d = base / "2026-10-28-abortou-na-base"
    d.mkdir()
    (d / "metrics.json").write_text(json.dumps({"status": "ABORT — linha de base VERMELHA", "rc": 7}),
                                    encoding="utf-8")
    nomes = {o["op"]: o for o in v.listar_ops()}
    assert nomes[d.name]["rotulo"] == "BASE" and nomes[d.name]["rc"] == 7
    assert v.ler_feed(d, 0)["aar"]["rotulo"] == "BASE"


def test_evento_leva_a_hora_do_transcript():
    com_hora = json.dumps({**json.loads(ACAO), "timestamp": "2026-10-25T14:00:30.432Z"})
    assert all(e["ts"] == "2026-10-25T14:00:30.432Z" for e in v.eventos(com_hora))
    assert "ts" not in v.eventos(ACAO)[0]


def test_acao_herda_a_hora_do_ultimo_resultado_e_atravessa_lotes():
    """No stream-json só o evento `user` traz timestamp; a ação e a fala, não: herdam a última vista,
    senão o log mostraria a hora de agora em todas as linhas."""
    ok = json.dumps({"type": "user", "timestamp": "2026-10-27T19:47:40.120Z", "message": {"content": [
        {"type": "tool_result", "is_error": False, "content": "ok"}]}})
    _, d = _ops([ACAO, ok, ACAO])
    r = v.ler_feed(d, 0)
    assert "ts" not in r["eventos"][0]                               # antes de qualquer hora
    assert r["eventos"][2]["ts"] == "2026-10-27T19:47:40.120Z" and r["ts"] == r["eventos"][2]["ts"]
    with (d / "agent.jsonl").open("a", encoding="utf-8", newline="\n") as h:
        h.write(ACAO + "\n")
    r2 = v.ler_feed(d, r["pos"], r["ts"])                            # o lote seguinte herda
    assert r2["eventos"][0]["ts"] == "2026-10-27T19:47:40.120Z"
    assert v.ler_feed(d, r["pos"], "<script>")["eventos"][0].get("ts") is None   # ts do navegador é validado


def test_fase_e_o_log_mais_novo():
    """O STATUS dizia "agente despachado" até durante a linha de base."""
    _, d = _ops([ACAO])
    for i, nome in enumerate(("agent.jsonl", "baseline.log")):    # re-run: a base é a mais nova
        (d / nome).touch()
        os.utime(d / nome, (1000 + i, 1000 + i))
    assert v.fase_atual(d)["texto"].startswith("linha de base")
    (d / "gate.log").touch()
    os.utime(d / "gate.log", (2000, 2000))
    assert v.fase_atual(d)["texto"].startswith("gate") and v.fase_atual(d)["desde"] is None


def test_fase_com_cronometro_vem_do_fases_json():
    _, d = _ops([ACAO])
    (d / "fases.json").write_text(json.dumps([
        {"fase": "preparo", "inicio": "2026-10-28T20:00:00Z", "teto_s": None},
        {"fase": "agente", "inicio": "2026-10-28T20:06:10Z", "teto_s": 5400}]), encoding="utf-8")
    f = v.fase_atual(d)
    assert f["etapa"] == "agente" and f["desde"] == "2026-10-28T20:06:10Z" and f["teto_s"] == 5400
    e = v.etapas([{"fase": "preparo", "inicio": "2026-10-28T20:00:00Z"},
                  {"fase": "base", "inicio": "2026-10-28T20:00:01Z", "teto_s": 1800},
                  {"fase": "agente", "inicio": "2026-10-28T20:06:10Z", "teto_s": 5400},
                  {"fase": "fim", "inicio": "2026-10-28T20:40:10Z"}])
    assert e == [{"fase": "base", "s": 369, "teto_s": 1800}, {"fase": "agente", "s": 2040, "teto_s": 5400}]


def test_recusa_de_permissao_tem_tipo_proprio_e_tokens_sem_duplicar():
    recusa = json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True,
        "content": "This Bash command contains multiple operations. The following part requires approval: grep"}]}})
    assert v.eventos(recusa)[0]["tipo"] == "recusa" and v.eventos(ERRO)[0]["tipo"] == "erro"
    for texto in ("Claude requested permissions to read from /x/t.py, but you haven't granted it yet.",
                  "cat in '/x/t.py' was blocked. For security, Claude Code may only concatenate files"):
        ev = json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "is_error": True, "content": texto}]}})
        assert v.eventos(ev)[0]["tipo"] == "recusa", texto
    bloco = {"type": "assistant", "message": {"id": "msg_1", "usage": {"output_tokens": 120},
             "content": [{"type": "text", "text": "a"}]}}
    _, d = _ops([json.dumps(bloco), json.dumps(bloco), recusa])     # a mesma mensagem, dois blocos
    assert v.ler_feed(d, 0)["uso"] == {"msg_1": 120}


def test_aar_traz_a_prova_e_os_desvios():
    rel = "Fiz.\n\n## Desvios da spec\n- a spec dizia X → fiz Y → porque Z\n\n## Fim\nok"
    a = v.aar({"status": VERMELHO, "rc": 3, "gate": {"failed": 3, "passed": 912},
               "gate_pass": False, "branch": "feat/x", "divergencias": ["d1"],
               "operator": {"result_text": rel, "total_cost_usd": 6.48, "num_turns": 105,
                            "duration_ms": 1956000}, "wall_clock_s": 2754})
    assert a["rotulo"] == "VERMELHO" and a["gate"]["failed"] == 3 and a["divergencias"] == ["d1"]
    assert a["desvios"] == "- a spec dizia X → fiz Y → porque Z"
    assert a["agente_min"] == 32.6 and a["parede_min"] == 45.9 and a["commit"] is None
    assert v.desvios("sem a seção") is None


def test_placar_conta_so_run_real_e_le_a_revisao():
    base = Path(tempfile.mkdtemp())
    os.environ["E9_OPS_DIR"] = str(base)
    def op(nome, m):
        d = base / nome
        d.mkdir()
        (d / "metrics.json").write_text(json.dumps(m), encoding="utf-8")
    op("2026-10-24-a", {"status": "OK — verde", "rc": 0, "gate": {"passed": 1}, "wall_clock_s": 600,
                        "operator": {"total_cost_usd": 1.5}, "revisao": {"desfecho": "como_veio"}})
    op("2026-10-24-b", {"status": VERMELHO, "rc": 3, "gate": {"failed": 1}, "divergencias": ["x"],
                        "wall_clock_s": 1800, "operator": {"total_cost_usd": 2.5},
                        "revisao": {"desfecho": "consertado"}})
    op("2026-10-24-c", {"status": "ABORT — linha de base VERMELHA", "rc": 7})
    op("2026-10-24-e", {"status": "OK — gate verde, commitado na branch"})   # antes da v0.4.2: sem rc
    op("2026-10-28-smoke-x", {"status": "OK", "rc": 0, "operator": {"total_cost_usd": 9}})  # treino
    p = v.placar()
    assert p["voos"] == 4 and p["desfechos"] == {"VERDE": 2, "VERMELHO": 1, "BASE": 1}
    assert p["revisao"] == {"como_veio": 1, "consertado": 1, "descartado": 0, "sem": 2}
    assert p["custo_total"] == 4.0 and p["custo_medio"] == 2.0 and p["voo_medio_min"] == 20.0
    assert p["divergiu"] == 1 and p["com_gate"] == 2
    assert p["verde_de_primeira"] == 2 and p["revoos"] == 0, "o run sem rc gravado conta pelo status"
    op("2026-10-25-d", {"status": "OK — verde", "rc": 0, "previous_runs": [{"rc": 3}]})   # verde no re-run
    p2 = v.placar()
    assert p2["desfechos"]["VERDE"] == 3 and p2["verde_de_primeira"] == 2 and p2["revoos"] == 1
    assert {o["op"]: o["revisao"] for o in v.listar_ops()}["2026-10-24-a"] == "como_veio"


def test_pagina_escapa_o_relatorio_antes_do_markdown():
    """O relatório vem do agente: o markdown da página escapa o HTML antes de marcar."""
    assert "const esc=" in v.PAGINA and "function md(" in v.PAGINA
    corpo_md = v.PAGINA.split("function md(", 1)[1].split("function fato(", 1)[0]
    assert "inl(" in corpo_md and "innerHTML" not in corpo_md


def test_nao_sai_da_pasta_de_ops():
    """O nome vem da listagem; caminho montado pelo navegador nunca é aberto."""
    _ops([ACAO])
    for ruim in ("..", "../..", "/etc", "2026-10-25-teste/../..", "", "nao-existe"):
        assert v._op_valida(ruim) is None, ruim


def test_servidor_http_ponta_a_ponta():
    _, d = _ops([ACAO, RESULT])
    srv = v.ThreadingHTTPServer(("127.0.0.1", 0), v.Handler)
    porta = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{porta}"
        html = urllib.request.urlopen(base + "/").read().decode("utf-8")
        assert "<title>Monitor E-9</title>" in html and "o agent trabalhando" in html
        ops = json.loads(urllib.request.urlopen(base + "/api/ops").read())
        assert ops[0]["op"] == d.name
        feed = json.loads(urllib.request.urlopen(f"{base}/api/feed?op={d.name}&pos=0").read())
        assert [e["tipo"] for e in feed["eventos"]] == ["acao", "fala", "fim"]
        try:
            urllib.request.urlopen(base + "/api/feed?op=..&pos=0")
            raise AssertionError("devia ser 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()


def test_escuta_so_nesta_maquina_por_padrao():
    """A página mostra o código e os comandos do agent: abrir para a rede é escolha explícita."""
    assert v.argumentos([]).host == "127.0.0.1" and v.argumentos([]).porta == 8417
    assert v.argumentos(["--host", "0.0.0.0"]).host == "0.0.0.0"


def test_pagina_sem_nada_de_outra_instalacao():
    """Nada de faixa de máquina, de canal de aviso nem de hora militar: a página é de quem roda o kit."""
    for palavra in ("AO <b>", "Aviso no", "Zulu", "DTG", "fábrica"):
        assert palavra not in v.PAGINA, palavra


if __name__ == "__main__":
    tests = [f for k, f in sorted(globals().items()) if k.startswith("test_")]
    ok = fail = 0
    for t in tests:
        try:
            t(); print(f"  ok   ✅ {t.__name__}"); ok += 1
        except AssertionError as e:
            print(f"  FAIL ❌ {t.__name__} — {e}"); fail += 1
    print(f"\nRESULTADO: {ok} ok, {fail} falha(s)")
    sys.exit(1 if fail else 0)
