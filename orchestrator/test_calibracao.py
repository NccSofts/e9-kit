#!/usr/bin/env python3
"""test_calibracao.py — v0.13 e v0.14: o REVIEW.md do target, a profundidade da revisão, o corte dos
detalhes, a calibração julgada pelo owner e o veto em sombra, que registra e nunca muda o rc.
Nenhum caso chama a rede: o `chamador` do reviewer é falso. Roda com pytest ou direto."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_e9_run as t  # noqa: E402
import calibracao as cal  # noqa: E402
import reviewer as rv  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
DIFF_PEQUENO = "diff --git a/src/x.py b/src/x.py\n--- a/src/x.py\n+++ b/src/x.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n"


def _resposta(veredito, achados, custo=0.01):
    return {"choices": [{"message": {"content": json.dumps(
        {"veredito": veredito, "resumo": "r", "achados": achados})}}], "usage": {"cost": custo}}


def _achado(sev="media", critico=False, passe="bugs", problema="p"):
    return {"severidade": sev, "critico": critico, "passe": passe, "arquivo": "src/x.py", "linha": 1,
            "problema": problema, "por_que": "q"}


def test_review_md_le_limite_e_caminhos_sensiveis():
    r = rv.ler_review_md(RAIZ / "targets" / "exemplo.review.md")
    assert r["nome"] == "exemplo.review.md" and r["limite"] == 3
    assert "tests/**" in r["sensiveis"] and "## Crítico" in r["texto"]
    padrao = rv.ler_review_md(RAIZ / "process" / "REVIEW-template.md")
    assert padrao["limite"] == 5 and padrao["sensiveis"]
    assert rv.ler_review_md(RAIZ / "nao-existe.md")["nome"] is None


def test_profundidade_declarada_pelo_owner_e_senao_pelo_tamanho():
    review = {"sensiveis": ["src/app/migrations/**"], "texto": "", "limite": 5}
    assert rv.profundidade_de({"risk": "alto"}, DIFF_PEQUENO, review)[0] == "fundo"
    assert rv.profundidade_de({"risk": "baixo"}, DIFF_PEQUENO * 200, review)[0] == "rapido"
    assert rv.profundidade_de({}, DIFF_PEQUENO, review)[0] == "rapido"
    grande = DIFF_PEQUENO + "".join(f"+linha {i}\n" for i in range(100))
    assert rv.profundidade_de({}, grande, review)[0] == "padrao"
    sens = DIFF_PEQUENO.replace("src/x.py", "src/app/migrations/v1.py")
    p, motivo = rv.profundidade_de({}, sens, review)
    assert p == "padrao" and "sensível" in motivo, "pequeno, mas em caminho sensível, nunca é rápido"


def test_fundo_faz_um_pedido_por_passe_e_o_veredito_e_o_pior():
    pedidos = []

    def chamador(corpo, chave):
        pedidos.append(corpo["messages"][0]["content"])
        n = len(pedidos)
        return _resposta("APROVA" if n != 2 else "REPROVA", [_achado(passe="bugs")] if n == 2 else [])
    r = rv.revisar("m", "spec", "", DIFF_PEQUENO, chave="k", chamador=chamador, profundidade="fundo",
                   review={"texto": "## Crítico\n- x", "limite": 5, "nome": "t.review.md"})
    assert len(pedidos) == 3 and "só o passe 'seguranca'" in pedidos[1]
    assert r["veredito"] == "REPROVA" and r["achados"][0]["passe"] == "seguranca", r
    assert r["custo"] == 0.03 and r["review_md"] == "t.review.md"


def test_o_review_md_vai_ao_pedido_e_o_rapido_avisa_do_limite():
    corpos = []
    rv.revisar("m", "spec", "", DIFF_PEQUENO, chave="k", profundidade="rapido",
               chamador=lambda c, k: corpos.append(c) or _resposta("APROVA", []),
               review={"texto": "## Crítico\n- o dado vaza", "limite": 5, "nome": "x"})
    assert "o dado vaza" in corpos[0]["messages"][1]["content"]
    assert "PASSE RÁPIDO" in corpos[0]["messages"][0]["content"]


def test_detalhes_cortados_no_limite_mas_nunca_o_critico():
    achados = [_achado("baixa", problema=f"d{i}") for i in range(6)] + [_achado("media", critico=True)]
    r = rv.revisar("m", "s", "", DIFF_PEQUENO, chave="k", chamador=lambda c, k: _resposta("RESSALVA", achados),
                   review={"texto": "", "limite": 2, "nome": None})
    assert len(r["achados"]) == 3 and r["detalhes_cortados"] == 4
    assert r["achados"][0]["critico"] is True


def test_veto_em_sombra_segura_reprova_ou_critico_e_nunca_muda_o_rc():
    assert cal.sombra({"veredito": "APROVA", "achados": [], "review_md": "x"})["teria_segurado"] is False
    assert cal.sombra({"veredito": "REPROVA", "achados": [], "review_md": "x"})["teria_segurado"] is True
    v = cal.sombra({"veredito": "RESSALVA", "review_md": "x", "achados": [_achado("alta", critico=False),
                                                                       _achado("media", critico=True)]})
    assert v["teria_segurado"] and len(v["motivos"]) == 1, "com REVIEW.md, vale o crítico, não a severidade"
    antigo = cal.sombra({"veredito": "RESSALVA", "achados": [{"severidade": "alta", "problema": "x"}]})
    assert antigo["teria_segurado"] and "anterior à v0.13" in antigo["regra"]
    assert cal.sombra({"estado": "sem chave"}) is None


def test_matriz_do_veto_contra_a_revisao_do_owner():
    s, d = {"teria_segurado": True}, {"teria_segurado": False}
    q = cal.comparar([(s, {"desfecho": "consertado"}), (s, {"desfecho": "como_veio"}),
                      (d, {"desfecho": "descartado"}), (d, {"desfecho": "como_veio"}), (d, None), (None, None)])
    assert q == {"segurou_e_owner_barrou": 1, "segurou_e_entrou_como_veio": 1, "deixou_e_owner_barrou": 1,
                 "deixou_e_entrou_como_veio": 1, "sem_revisao": 1, "com_parecer": 5}


def test_calibrar_grava_sem_reescrever_as_chaves_antigas_da_op():
    d = Path(tempfile.mkdtemp())
    antigo = {"status": "OK", "operator": {"total_cost_usd": 1}, "revisao": {"desfecho": "consertado"},
              "revisor": {"veredito": "RESSALVA", "achados": [_achado("alta", problema="o except largo")]}}
    (d / "metrics.json").write_text(json.dumps(antigo), encoding="utf-8")
    (d / "AAR.md").write_text("# AAR\n\nanálise à mão\n", encoding="utf-8")
    r = t._revisar()
    assert r.main(["--op", str(d), "--calibrar", "1=real", "--perdidos", "2"]) == 0
    m = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
    assert "operator" in m and "revisor" in m and "agent" not in m, "o histórico não se reescreve"
    assert m["review_calibration"]["achados"][0]["julgamento"] == "real" and m["review_calibration"]["perdidos"] == 2
    aar = (d / "AAR.md").read_text(encoding="utf-8")
    assert "análise à mão" in aar and "## Calibração do reviewer" in aar
    assert r.main(["--op", str(d), "--calibrar", "2=real"]) == 2, "achado que não existe"
    assert r.main(["--op", str(d), "--calibrar", "1=talvez"]) == 2, "julgamento fora da lista"
    assert r.main(["--op", str(d), "--calibrar", ""]) == 2, "vazio, com achado para julgar"


def test_revisar_desfecho_numa_op_antiga_nao_renomeia_as_chaves():
    d = Path(tempfile.mkdtemp())
    (d / "metrics.json").write_text(json.dumps({"status": "OK", "operator_ok": True, "revisao": {"x": 1}}),
                                    encoding="utf-8")
    t._revisar().revisar(d, "como_veio")
    m = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
    assert "operator_ok" in m and "agent_ok" not in m and "revisao" not in m and m["review"]["desfecho"] == "como_veio"


def test_placar_de_precisao_e_cobertura():
    p = cal.placar([{"achados": [{"julgamento": "real"}, {"julgamento": "parcial"}], "perdidos": 1},
                    {"achados": [{"julgamento": "falso"}], "perdidos": 0}])
    assert p == {"ops": 2, "julgados": 3, "reais": 1.5, "perdidos": 1, "precisao": 0.5, "cobertura": 0.6}


def test_run_grava_o_veto_em_sombra_no_metrics_e_no_report():
    import reviewer as mod
    orig = (mod.ligado, mod.revisar)
    mod.ligado = lambda cfg: True
    mod.revisar = lambda *a, **k: {"veredito": "REPROVA", "achados": [], "estado": "ok", "review_md": "x",
                                   "profundidade": k.get("profundidade")}
    carregar = t.e9._carregar
    t.e9._carregar = lambda nome, apelido: mod if nome == "reviewer.py" else carregar(nome, apelido)
    try:
        rc, m, _ = t._live(t.AGENTE_CUMPRE)
    finally:
        mod.ligado, mod.revisar = orig
        t.e9._carregar = carregar
    assert rc == 0, "o veto em sombra não muda o rc"
    assert m["veto_sombra"]["teria_segurado"] is True and m["veto_sombra"]["modo"] == "sombra"
    assert "## Veto em sombra" in (t._ULTIMA_OP / "report.md").read_text(encoding="utf-8")


def test_dashboard_mostra_veto_e_calibracao():
    import test_monitor as tv
    base, d = tv._ops([tv.RESULT], metrics={"status": "OK", "rc": 0, "review": {"desfecho": "consertado"},
                                            "reviewer": {"veredito": "RESSALVA", "achados": [{"severidade": "alta"}]},
                                            "review_calibration": {"achados": [{"julgamento": "real"}], "perdidos": 0}})
    a = tv.v.aar(tv.v._metrics(d))
    assert a["veto"]["teria_segurado"] and a["veto"]["retroativo"] == "run"
    p = tv.v.placar()
    assert p["veto_sombra"]["segurou_e_owner_barrou"] == 1 and p["calibracao"]["precisao"] == 1.0
    assert "Veto em sombra" in tv.v.PAGINA and "precisão" in tv.v.PAGINA


if __name__ == "__main__":
    falhas = 0
    for nome, f in sorted(globals().items()):
        if nome.startswith("test_") and callable(f):
            try:
                f(); print(f"  ok   {nome}")
            except AssertionError as e:
                falhas += 1; print(f"  FAIL {nome} — {e}")
    sys.exit(1 if falhas else 0)
