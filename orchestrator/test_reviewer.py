#!/usr/bin/env python3
"""
test_reviewer.py — o revisor de outro modelo (v0.6).

Fixa as três fronteiras do revisor: o diff sai mascarado, o pedido exige provedor
sem retenção, e o relatório do agente e o `.env` nunca vão junto. Nenhum teste chama o OpenRouter:
o `chamador` é falso. Roda com pytest ou direto (`python orchestrator/test_reviewer.py`).
"""
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import urllib.error
from pathlib import Path

_spec = importlib.util.spec_from_file_location("reviewer", Path(__file__).with_name("reviewer.py"))
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

PARECER = {"veredito": "RESSALVA", "resumo": "faz o pedido, com um except largo",
           "achados": [{"severidade": "alta", "arquivo": "src/a.py", "linha": 12,
                        "problema": "except Exception", "por_que": "rotula erro do DJEN como BrasilAPI"}]}


def _resposta(parecer=PARECER, cost=0.42):
    return {"choices": [{"message": {"content": json.dumps(parecer)}}],
            "usage": {"cost": cost, "prompt_tokens": 1000, "completion_tokens": 200},
            "provider": "OpenAI"}


# --- máscara ----------------------------------------------------------------------

def test_mascara_cnj_cnpj_cpf_email():
    t = ("proc 1234567-89.2024.8.26.0100 e 12345678920248260100; cnpj 12.345.678/0001-90 e "
         "12345678000190; cpf 123.456.789-09 e 12345678909; mail fulano.tal@exemplo.com.br")
    out, n = rv.mascarar(t)
    assert n == {"CNJ": 2, "CNPJ": 2, "CPF": 2, "EMAIL": 1}, n
    for vazado in ("1234567-89", "12345678920248260100", "0001-90", "12345678000190",
                   "456.789", "12345678909", "fulano"):
        assert vazado not in out, vazado


def test_mascara_nao_come_numero_curto():
    out, n = rv.mascarar("LIMITE = 1500; porta 8417; ano 2026")
    assert n == {} and out == "LIMITE = 1500; porta 8417; ano 2026"


def test_cnj_nao_vira_cnpj_mais_cpf():
    out, n = rv.mascarar("12345678920248260100")
    assert out == "<CNJ>" and n == {"CNJ": 1}, (out, n)


# --- o pedido -----------------------------------------------------------------------

def test_pedido_exige_zdr_sem_coleta_e_esquema_estrito():
    corpo, info = rv.montar_pedido("openai/x", "spec", "TRAVAS", "+linha", {"passed": 3})
    assert corpo["provider"] == {"zdr": True, "data_collection": "deny", "require_parameters": True}
    assert "allow_fallbacks" not in corpo["provider"] or corpo["provider"]["allow_fallbacks"] is False
    rf = corpo["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"]["additionalProperties"] is False
    assert corpo["model"] == "openai/x" and info["diff_cortado"] is False


def test_pedido_mascarado_em_tudo_que_sai():
    corpo, info = rv.montar_pedido("m", "cliente 12.345.678/0001-90", "", "+ x = '1234567-89.2024.8.26.0100'", None)
    texto = json.dumps(corpo, ensure_ascii=False)
    assert "0001-90" not in texto and "1234567-89" not in texto
    assert info["mascarados"] == {"CNJ": 1, "CNPJ": 1}


def test_diff_grande_e_cortado_e_avisado():
    corpo, info = rv.montar_pedido("m", "", "", "+" + "a" * (rv.LIMITE_DIFF + 10), None)
    assert info["diff_cortado"] is True and "CORTADO" in corpo["messages"][1]["content"]


# --- o parecer -----------------------------------------------------------------------

def test_parecer_valido_e_custo_gravado():
    r = rv.revisar("m", "spec", "", "+x", chave="k", chamador=lambda corpo, chave: _resposta())
    assert r["estado"] == "ok" and r["veredito"] == "RESSALVA" and r["custo"] == 0.42
    assert r["achados"][0]["linha"] == 12 and r["tokens"] == {"entrada": 1000, "saida": 200}


def test_parecer_fora_do_esquema_vira_indisponivel():
    r = rv.revisar("m", "O", "", "+x", chave="k",
                   chamador=lambda c, k: _resposta({"veredito": "TALVEZ", "resumo": "", "achados": []}))
    assert r["estado"].startswith("indisponível") and "veredito" not in r


def test_http_erro_nao_levanta_e_nao_repete_sem_zdr():
    pedidos = []

    def cai(corpo, chave):
        pedidos.append(corpo)
        raise urllib.error.HTTPError(rv.URL, 404, "no", {}, io.BytesIO(
            b'{"error": {"message": "No endpoints found matching your data policy"}}'))
    r = rv.revisar("m", "O", "", "+x", chave="k", chamador=cai)
    assert r["estado"].startswith("indisponível: HTTP 404") and "data policy" in r["estado"]
    assert len(pedidos) == 1, "falha de ZDR não pode virar segunda tentativa sem a restrição"


def test_sem_chave_e_sem_diff_nao_chamam():
    def nunca(c, k):
        raise AssertionError("não devia chamar")
    assert rv.revisar("m", "O", "", "+x", chave="", chamador=nunca)["estado"] == "sem chave"
    assert rv.revisar("m", "O", "", "  \n", chave="k", chamador=nunca)["estado"] == "sem diff"


def test_desligado_por_env_e_por_op():
    import os
    antes = os.environ.get("E9_SEM_REVISOR")
    try:
        os.environ.pop("E9_SEM_REVISOR", None)
        assert rv.ligado({}) and not rv.ligado({"revisor": False})
        os.environ["E9_SEM_REVISOR"] = "1"
        assert not rv.ligado({})
    finally:
        if antes is None:
            os.environ.pop("E9_SEM_REVISOR", None)
        else:
            os.environ["E9_SEM_REVISOR"] = antes
    assert rv.modelo_de({}) == rv.MODELO_PADRAO
    assert rv.modelo_de({"revisor": {"modelo": "openai/outro"}}) == "openai/outro"


# --- o diff da worktree ----------------------------------------------------------------

def _g(repo, *a):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                   check=True, capture_output=True)


def test_diff_leva_alterado_e_novo_e_nunca_o_env():
    d = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", "-b", "main", str(d)], check=True)
    (d / "a.py").write_text("x = 1\n", encoding="utf-8")
    (d / "ligado.cfg").write_text("segredo-ligado\n", encoding="utf-8")
    _g(d, "add", "a.py", "ligado.cfg")
    _g(d, "commit", "-q", "-m", "init")
    base = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (d / "a.py").write_text("x = 2\n", encoding="utf-8")
    (d / "ligado.cfg").write_text("segredo-ligado-2\n", encoding="utf-8")
    (d / "novo.py").write_text("y = 3\n", encoding="utf-8")
    (d / ".env").write_text("CHAVE=segredo-env\n", encoding="utf-8")
    (d / "sub").mkdir()
    (d / "sub" / ".env.local").write_text("CHAVE=segredo-sub\n", encoding="utf-8")
    diff = rv.diff_da_worktree(d, base, ["ligado.cfg"])
    assert "+x = 2" in diff and "+y = 3" in diff and "b/novo.py" in diff
    assert "segredo" not in diff, diff


def test_diff_depois_do_commit_continua_contra_a_base():
    d = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", "-b", "main", str(d)], check=True)
    _g(d, "commit", "-q", "--allow-empty", "-m", "init")
    base = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (d / "a.py").write_text("x = 1\n", encoding="utf-8")
    _g(d, "add", "a.py")
    _g(d, "commit", "-q", "-m", "voo")
    assert "+x = 1" in rv.diff_da_worktree(d, base)
    assert "+x = 1" in rv.diff_do_commit(d, "HEAD")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = fail = 0
    for t in tests:
        try:
            t(); print(f"  ok   ✅ {t.__name__}"); ok += 1
        except Exception as e:                   # noqa: BLE001
            print(f"  FAIL ❌ {t.__name__} — {type(e).__name__}: {e}"); fail += 1
    print(f"\nRESULTADO: {ok} ok, {fail} falha(s)")
    sys.exit(1 if fail else 0)


def test_sobrecarga_repete_o_mesmo_pedido_e_registra_o_motivo():
    """Visto num run real: o provedor ZDR respondeu 200 com `error` de "high demand"; o motivo sumia."""
    sobrecarga = {"error": {"message": "The system is currently experiencing high demand"}}
    pedidos, esperas = [], []

    def chamador(corpo, chave):
        pedidos.append(corpo)
        return sobrecarga if len(pedidos) < 3 else _resposta()

    r = rv.revisar("m", "O", "", "+x", chave="k", chamador=chamador, esperar=esperas.append)
    assert r["estado"] == "ok" and r["tentativas"] == 3 and esperas == [30, 90]
    assert all(p["provider"]["zdr"] for p in pedidos) and pedidos[0] == pedidos[2]

    r = rv.revisar("m", "O", "", "+x", chave="k", chamador=lambda c, k: sobrecarga,
                   esperar=lambda s: None)
    assert r["estado"].startswith("indisponível") and "high demand" in r["estado"]

    limite = {"error": {"message": "openai/gpt-6-astra is temporarily rate-limited upstream."}}
    seq = [limite, _resposta()]
    r = rv.revisar("m", "O", "", "+x", chave="k", chamador=lambda c, k: seq.pop(0),
                   esperar=lambda s: None)
    assert r["estado"] == "ok" and r["tentativas"] == 2

    outro = {"error": {"message": "invalid request"}}
    chamadas = []
    r = rv.revisar("m", "O", "", "+x", chave="k",
                   chamador=lambda c, k: chamadas.append(1) or outro, esperar=lambda s: None)
    assert len(chamadas) == 1 and "invalid request" in r["estado"]


def test_diff_manda_arquivo_apagado_so_pelo_nome():
    wt = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", "-b", "main", str(wt)], check=True)
    (wt / "grande.html").write_text("linha\n" * 5000, encoding="utf-8")
    subprocess.run(["git", "-C", str(wt), "add", "grande.html"], check=True)
    subprocess.run(["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", "i"], check=True)
    (wt / "grande.html").unlink()
    d = rv.diff_da_worktree(wt, "HEAD")
    assert "grande.html" in d and "deleted file" in d and d.count("-linha") == 0, d[:300]


def test_http_429_tambem_e_sobrecarga_e_repete():
    """Visto num run real: o 429 do OpenRouter chega como HTTPError, não como corpo com `error`."""
    chamadas = []

    def chamador(corpo, chave):
        chamadas.append(1)
        if len(chamadas) == 1:
            raise urllib.error.HTTPError(
                "u", 429, "x", {}, io.BytesIO(json.dumps({"error": {
                    "message": "could not verify available credits in time. Retry shortly."}}).encode()))
        return _resposta()

    r = rv.revisar("m", "O", "", "+x", chave="k", chamador=chamador, esperar=lambda s: None)
    assert r["estado"] == "ok" and r["tentativas"] == 2

    def recusa(corpo, chave):
        raise urllib.error.HTTPError("u", 401, "x", {}, io.BytesIO(b"{}"))

    assert rv.revisar("m", "O", "", "+x", chave="k", chamador=recusa,
                      esperar=lambda s: None)["estado"] == "indisponível: HTTP 401"
