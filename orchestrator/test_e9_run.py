#!/usr/bin/env python3
"""
test_e9_run.py — testes da ROE codificada no harness.

Fixa a regressão dos guardrails: se alguém afrouxar uma trava do e9_run.py, isto
fica vermelho. Roda com pytest (`pytest orchestrator/test_e9_run.py`) ou direto
(`python orchestrator/test_e9_run.py`). Stdlib apenas — sem dependência de pytest.

NÃO toca em git real: os comandos proibidos são recusados ANTES de executar.
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

os.environ["E9_SEM_AVISO"] = "1"    # os voos de teste nunca mandam aviso para o celular
_spec = importlib.util.spec_from_file_location("e9_run", Path(__file__).with_name("e9_run.py"))
e9 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e9)


@contextmanager
def blocks():
    """Afirma que o bloco levanta SystemExit (a forma de abort do harness)."""
    try:
        yield
    except SystemExit:
        return
    raise AssertionError("esperava SystemExit (ROE abort), mas não houve")


def _mk_op(over):
    base = {"op": "t", "target_repo": "/home/dev/projeto", "branch": "feat/ok",
            "worktree": "/tmp/w", "operator": {"cmd_template": "echo"},
            "overwatch": {"test_cmd": "true"}, "expected_artifacts": ["a.py"]}
    d = Path(tempfile.mkdtemp())
    (d / "OPORD.md").write_text("x", encoding="utf-8")
    (d / "op.json").write_text(json.dumps({**base, **over}), encoding="utf-8")
    return d


# --- git() guard (runtime) ---------------------------------------------------

def test_git_push_recusado():
    with blocks():
        e9.git(["push", "origin", "main"])


def test_git_merge_recusado():
    with blocks():
        e9.git(["merge", "feat/x"])


def test_git_reset_recusado():
    with blocks():
        e9.git(["reset", "--hard"])


def test_git_add_dash_A_recusado():
    with blocks():
        e9.git(["add", "-A"])


def test_git_add_dot_recusado():
    with blocks():
        e9.git(["add", "."])


# --- load_op() validação de contrato -----------------------------------------

def test_branch_fora_de_feat_barrada():
    with blocks():
        e9.load_op(_mk_op({"branch": "main"}))


def test_target_em_marcador_de_producao_barrado():
    with blocks():
        e9.load_op(_mk_op({"target_repo": "/srv/producao/app", "prod_markers": ["/srv/producao"]}))


def test_qualquer_marcador_da_lista_barra():
    with blocks():
        e9.load_op(_mk_op({"target_repo": "deploy@vps-prod:/app", "prod_markers": ["/srv/", "vps-prod"]}))


def test_sem_marcador_que_bata_o_alvo_passa():
    cfg, _ = e9.load_op(_mk_op({"prod_markers": ["/srv/producao"]}))
    assert cfg["target_repo"] == "/home/dev/projeto"


def test_expected_artifacts_vazio_barrado():
    with blocks():
        e9.load_op(_mk_op({"expected_artifacts": []}))


def test_push_embutido_na_config_barrado():
    with blocks():
        e9.load_op(_mk_op({"operator": {"cmd_template": "git push"}}))


# --- controle: o contrato válido tem que PASSAR ------------------------------

def test_op_valido_carrega():
    cfg, opord = e9.load_op(_mk_op({}))
    assert cfg["branch"].startswith("feat/")
    assert opord.exists()


# --- v0.2: o operador não trava -----------------------------------------------

def test_run_nao_herda_stdin():
    """Regressão do travamento das primeiras tarefas: filho que lê stdin tem de receber EOF na
    hora. Com stdin herdado e sem TTY, este filho ficaria esperando para sempre."""
    t0 = time.monotonic()
    rc, out = e9.run([sys.executable, "-c", "import sys; print(len(sys.stdin.read()))"], timeout=20)
    assert rc == 0 and out.strip() == "0", (rc, out)
    assert time.monotonic() - t0 < 15


def test_run_timeout_mata_e_devolve_parcial():
    t0 = time.monotonic()
    code = "import time,sys; print('parcial', flush=True); time.sleep(60)"
    rc, out = e9.run([sys.executable, "-c", code], timeout=2)
    assert rc == e9.RC_TIMEOUT, rc
    assert "parcial" in out
    assert time.monotonic() - t0 < 20


def test_run_timeout_mata_os_netos():
    """O operador roda debaixo de `sh -c`: matar só o shell deixaria o `claude` órfão."""
    if os.name != "posix":
        return
    marker = Path(tempfile.mkdtemp()) / "vivo"
    cmd = f"sh -c 'sleep 4; touch {marker}' & wait"
    rc, _ = e9.run(cmd, timeout=1)
    assert rc == e9.RC_TIMEOUT
    time.sleep(5)
    assert not marker.exists(), "neto sobreviveu ao timeout"


def test_run_saida_grande_nao_trava():
    rc, out = e9.run([sys.executable, "-c", "print('x' * 2_000_000)"], timeout=30)
    assert rc == 0 and len(out) >= 2_000_000


def test_run_grava_log_pedido():
    f = Path(tempfile.mkdtemp()) / "op.log"
    rc, out = e9.run([sys.executable, "-c", "print('oi')"], timeout=10, log_file=f)
    assert rc == 0 and f.read_text(encoding="utf-8").strip() == "oi"


# --- v0.2: mensagem de commit vem do op.json ----------------------------------

def test_commit_subject_do_op_json():
    msg = e9.commit_message({"commit": {"subject": "fix(descoberta): x", "body": "porquê"}},
                            "A", ["a.py"])
    assert msg.splitlines()[0] == "fix(descoberta): x"
    assert "porquê" in msg and "a.py" in msg and "feat(seed)" not in msg


def test_commit_sem_subject_cai_em_chore_neutro():
    assert e9.commit_message({}, "X", ["a.py"]).startswith("chore(e9): op X")


def test_commit_subject_sem_prefixo_barrado():
    with blocks():
        e9.load_op(_mk_op({"commit": {"subject": "conserta a descoberta"}}))


def test_commit_subject_prefixo_invalido_barrado():
    with blocks():
        e9.load_op(_mk_op({"commit": {"subject": "wip: coisa"}}))


# --- v0.2: saída do operador vai pro AAR ---------------------------------------

def test_parse_stream_json_pega_result_final():
    linhas = [json.dumps({"type": "system", "subtype": "init"}),
              "lixo que não é json",
              json.dumps({"type": "assistant", "message": {}}),
              json.dumps({"type": "result", "subtype": "success", "is_error": False,
                          "result": "Feito. DESVIO: pus o teste em tests/x.py.",
                          "num_turns": 7, "duration_ms": 120000, "total_cost_usd": 0.42,
                          "usage": {"input_tokens": 10, "output_tokens": 20}})]
    m = e9.parse_operator_output("\n".join(linhas))
    assert m["stream_json"] and m["num_turns"] == 7 and m["total_cost_usd"] == 0.42
    assert "DESVIO" in m["result_text"]


def test_parse_texto_puro_vira_relatorio():
    m = e9.parse_operator_output("relatório em texto\n")
    assert not m["stream_json"] and m["result_text"] == "relatório em texto"


def test_aar_carrega_relatorio_do_operador():
    d = Path(tempfile.mkdtemp())
    e9.write_aar(d, "t", {"branch": "feat/x", "overwatch_pass": True, "committed": True,
                          "operator_rc": 0, "operator": {"result_text": "DESVIO declarado"},
                          "overwatch_tail": "61 passed"})
    aar = (d / "AAR.md").read_text(encoding="utf-8")
    assert "DESVIO declarado" in aar and "61 passed" in aar
    assert json.loads((d / "metrics.json").read_text(encoding="utf-8"))["operator_rc"] == 0


# --- v0.2: rc=0 não basta — is_error do `result` derruba o operador -----------

def _live(fake_operator_py: str, overwatch="true", extra=None, aceite=None):
    """Roda o main() em --live contra um repo git descartável, com um operador falso.
    `aceite` = {caminho relativo: conteúdo} vira a pasta `aceite/` da op (v0.4)."""
    base = Path(tempfile.mkdtemp())
    repo, wt = base / "repo", base / "wt"
    e9.run(["git", "init", "-q", "-b", "main", str(repo)])
    e9.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
            "commit", "-q", "--allow-empty", "-m", "init"])
    fake = base / "fake.py"
    fake.write_text(fake_operator_py, encoding="utf-8")
    op = _mk_op({"target_repo": str(repo), "worktree": str(wt), "expected_artifacts": ["a.py"],
                 "operator": {"cmd_template": f'"{sys.executable}" "{fake}"', "timeout_s": 30},
                 "overwatch": {"test_cmd": overwatch}, **(extra or {})})
    for rel, conteudo in (aceite or {}).items():
        (op / "aceite" / rel).parent.mkdir(parents=True, exist_ok=True)
        (op / "aceite" / rel).write_text(conteudo, encoding="utf-8")
    global _ULTIMA_OP
    _ULTIMA_OP = op
    argv = sys.argv
    sys.argv = ["e9_run.py", "--op", str(op), "--live"]
    try:
        rc = e9.main()
    finally:
        sys.argv = argv
    return rc, json.loads((op / "metrics.json").read_text(encoding="utf-8")), wt


def test_operador_is_error_com_rc0_aborta_antes_do_gate():
    """Caso real de 24/10: OAuth vencido, `claude -p` sai 0 com is_error=true."""
    payload = json.dumps({"type": "result", "is_error": True, "api_error_status": 401,
                          "result": "Failed to authenticate. API Error: 401"})
    rc, m, _ = _live(f"print({payload!r})")
    assert rc == 6, rc
    assert m["operator_ok"] is False and m["committed"] is False
    assert "overwatch_pass" not in m, "não devia ter chegado ao gate"
    assert "401" in m["status"]


def test_ciclo_verde_commita_com_subject_e_relatorio():
    code = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n'); import json; "
            "print(json.dumps({'type': 'result', 'is_error': False, 'result': 'fiz a.py', "
            "'num_turns': 2, 'total_cost_usd': 0.01}))")
    rc, m, wt = _live(code)
    assert rc == 0, m
    assert m["committed"] and m["commit_sha"]
    assert m["rc"] == 0 and m["arquivos"] == ["a.py"], (m.get("rc"), m.get("arquivos"))
    aar = (_ULTIMA_OP / "AAR.md").read_text(encoding="utf-8")
    assert e9.LEMBRETE_REVISAO in aar and "revisar.py --op ops/" in aar, "v0.5.1: o voo pede a revisão"
    rv = _revisar()
    rv.revisar(_ULTIMA_OP, "como_veio", commit_alvo="abc1234")
    aar = (_ULTIMA_OP / "AAR.md").read_text(encoding="utf-8")
    assert e9.LEMBRETE_REVISAO not in aar and "## Revisão no alvo" in aar, "a revisão gravada tira o lembrete"
    assert [f["fase"] for f in m["fases"]] == ["preparo", "base", "operador", "gate", "commit", "fim"]
    brief = (_ULTIMA_OP / "_brief.txt").read_text(encoding="utf-8")
    assert "TEMPO DO PORTÃO NESTE VOO" in brief and "timeout: 600000" in brief, "v0.5.1: o tempo medido vai ao briefing"
    assert m["aviso"] == "sem aviso configurado", m.get("aviso")
    assert m["fases"][2]["teto_s"] == 30, "o teto do operador vai junto"
    assert m["operator"]["result_text"] == "fiz a.py"
    _, subj = e9.run(["git", "-C", str(wt), "log", "-1", "--format=%s"])
    assert subj.strip() == "chore(e9): op t", subj


def test_arquivo_fora_dos_artefatos_barra_o_commit():
    """O gate testou a árvore toda; commitar só parte dela entregaria código não testado."""
    code = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n'); "
            "Path('b.py').write_text('y = 2\\n')")
    rc, m, _ = _live(code)
    assert rc == 4, m
    assert m["committed"] is False and "b.py" in m["status"]


# --- v0.3: linha de base antes do operador ------------------------------------

_ULTIMA_OP = None


def _rerun(op):
    argv = sys.argv
    sys.argv = ["e9_run.py", "--op", str(op), "--live"]
    try:
        return e9.main()
    finally:
        sys.argv = argv


def test_linha_de_base_vermelha_aborta_sem_despachar():
    """O `main` já nascia vermelho, e o operador voou 45 min à toa. O gate aqui só
    passa depois que a.py existe, então na base (antes do operador) ele é vermelho."""
    marca = Path(tempfile.mkdtemp()) / "operador_rodou"
    rc, m, _ = _live(f"from pathlib import Path; Path(r'{marca}').write_text('x')",
                     overwatch="test -f a.py" if os.name == "posix" else 'python -c "import os,sys; sys.exit(0 if os.path.exists(\'a.py\') else 1)"')
    assert rc == 7, m
    assert m["rc"] == 7, "o metrics.json tem de gravar o código de saída"
    assert m["baseline_pass"] is False and "linha de base" in m["status"].lower()
    assert not marca.exists(), "o operador foi despachado sobre uma base vermelha"


def test_worktree_suja_aborta_antes_da_base():
    """Sobra de um voo anterior na mesma worktree: a base não seria o main."""
    code = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n'); "
            "Path('b.py').write_text('y = 2\\n')")
    rc, _, _ = _live(code)          # 1º voo: commit barrado (b.py fora), sobras ficam
    assert rc == 4
    rc2 = _rerun(_ULTIMA_OP)        # 2º voo na mesma worktree
    assert rc2 == 8
    m = json.loads((_ULTIMA_OP / "metrics.json").read_text(encoding="utf-8"))
    assert "não está limpa" in m["status"]


def test_reaproveita_worktree_limpa_da_mesma_branch():
    """Re-voo depois de um voo que não deixou sobra: a worktree é reaproveitada, não recusada."""
    code = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n'); import json; "
            "print(json.dumps({'type': 'result', 'is_error': False, 'result': 'ok'}))")
    rc, _, wt = _live(code)
    assert rc == 0
    # o commit limpou a árvore; o 2º voo sobre a mesma op segue (o operador reescreve a.py igual,
    # e não há nada novo a commitar, então o commit falha com 4 — mas passou pela worktree)
    rc2 = _rerun(_ULTIMA_OP)
    assert rc2 in (0, 4), rc2


def test_voo_de_treino_nao_pede_revisao():
    code = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n'); import json; "
            "print(json.dumps({'type': 'result', 'is_error': False, 'result': 'ok'}))")
    rc, m, _ = _live(code, extra={"treino": True})
    assert rc == 0 and m["treino"] is True
    assert e9.LEMBRETE_REVISAO not in (_ULTIMA_OP / "AAR.md").read_text(encoding="utf-8")


def test_revoo_guarda_o_voo_anterior():
    """v0.5.1 — o metrics.json do re-voo leva o resumo do voo anterior: o verde de primeira se mede."""
    code = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n'); "
            "Path('b.py').write_text('y = 2\\n')")
    rc, m1, _ = _live(code)                # 1º voo: commit barrado (rc 4)
    assert rc == 4 and m1["voos_anteriores"] == []
    _rerun(_ULTIMA_OP)                     # 2º voo: worktree suja (rc 8)
    m2 = json.loads((_ULTIMA_OP / "metrics.json").read_text(encoding="utf-8"))
    assert m2["rc"] == 8 and [v["rc"] for v in m2["voos_anteriores"]] == [4]
    _rerun(_ULTIMA_OP)                     # 3º voo: o histórico acumula
    m3 = json.loads((_ULTIMA_OP / "metrics.json").read_text(encoding="utf-8"))
    assert [v["rc"] for v in m3["voos_anteriores"]] == [4, 8]


def test_baseline_desligavel_no_op_json():
    base = {"overwatch": {"test_cmd": "true", "baseline": False}}
    cfg, _ = e9.load_op(_mk_op(base))
    assert cfg["overwatch"]["baseline"] is False


# --- v0.3: o gate do E-9 ao lado do relatório do operador ----------------------

RUFF_VERMELHO = "tests/x.py:1:1: I001 Import block is un-sorted\nFound 1 error.\n[*] 1 fixable"
MYPY_VERMELHO = "src/a.py:3: error: x\nFound 5 errors in 3 files (checked 174 source files)"
GATE_VERDE = ("All checks passed!\nSuccess: no issues found in 175 source files\n"
              "....\n814 passed, 1 skipped in 346.38s (0:05:46)")


def test_gate_summary_le_as_tres_etapas():
    s = e9.gate_summary(GATE_VERDE)
    assert s == {"ruff": "limpo", "mypy": "limpo (175 arquivos)", "passed": 814}
    assert e9.gate_summary(RUFF_VERMELHO)["ruff"] == "1 erro(s)"
    s2 = e9.gate_summary("All checks passed!\n" + MYPY_VERMELHO)
    assert s2["ruff"] == "limpo" and s2["mypy"] == "5 erro(s)", s2   # erro do mypy não vira do ruff
    s3 = e9.gate_summary("FAILED t::x\n5 failed, 795 passed, 1 skipped in 325s")
    assert s3["failed"] == 5 and s3["passed"] == 795


def test_divergencia_verde_com_gate_vermelho():
    """Relatório com "Gate verde" e cinco falhas no gate."""
    d = e9.divergencias(False, {"failed": 5, "passed": 795}, "Status: Gate verde. Tudo ok.")
    assert any("VERMELHO" in x for x in d)


def test_divergencia_de_contagem():
    """Relatório com "418 passed" e o gate em 814."""
    d = e9.divergencias(True, {"passed": 814}, "Resultado: 418 passed, 0 failed.")
    assert d and "418" in d[0] and "814" in d[0]


SEM_DESVIO = "\n\n## Desvios da OPORD\nnenhum"


def test_relatorio_sem_secao_de_desvios_vira_achado():
    """v0.3.3 — em duas tarefas o gate deu verde e o defeito era um desvio não declarado."""
    d = e9.divergencias(True, {"passed": 10}, "Fiz tudo. 10 passed.")
    assert any("Desvios da OPORD" in x for x in d)
    assert e9.divergencias(True, {"passed": 10},
                           "Fiz. 10 passed.\n### Desvios da OPORD\n- a OPORD dizia X -> fiz Y") == []


def test_timeout_do_portao_e_o_dobro_da_base_dentro_dos_limites():
    """Numa tarefa real, `timeout: 300000` numa suíte de 6m20s foi para segundo plano sozinho."""
    cfg = {"operator": {"cmd_template": "BASH_MAX_TIMEOUT_MS=1200000 claude -p x"}}
    assert e9.timeout_do_portao_ms(cfg, 380) == 760_000      # uma suíte de 6m20s: o dobro
    assert e9.timeout_do_portao_ms(cfg, 10) == 600_000       # piso de 10 min
    assert e9.timeout_do_portao_ms(cfg, 900) == 1_200_000    # teto do cmd_template
    brief = e9.build_brief(_mk_op({}) / "OPORD.md", Path("/nao/existe"))
    assert "Não passe ao Bash um `timeout` menor que o tempo do portão" in brief


def test_briefing_proibe_segundo_plano():
    brief = e9.build_brief(_mk_op({}) / "OPORD.md", Path("/nao/existe"))
    assert "NUNCA rode nada em segundo plano" in brief


def test_restos_do_operador_na_worktree_sao_mortos():
    """O operador deixou suítes em segundo plano e encerrou. Um filho solto em sessão
    própria, com cwd na worktree, tem de morrer antes do gate."""
    if os.name != "posix":
        return
    import subprocess
    wt = Path(tempfile.mkdtemp())
    fora = Path(tempfile.mkdtemp())
    dentro = subprocess.Popen(["sleep", "60"], cwd=wt, start_new_session=True)
    alheio = subprocess.Popen(["sleep", "60"], cwd=fora, start_new_session=True)
    try:
        mortos = e9.matar_restos(wt)
        assert dentro.pid in mortos and alheio.pid not in mortos
        assert dentro.wait(timeout=10) != 0
        assert alheio.poll() is None, "matou processo fora da worktree"
    finally:
        alheio.kill(); alheio.wait()


def test_briefing_exige_a_secao_de_desvios():
    brief = e9.build_brief(_mk_op({}) / "OPORD.md", Path("/nao/existe"))
    assert "## Desvios da OPORD" in brief and "a OPORD dizia" in brief


def test_sem_divergencia_quando_bate():
    assert e9.divergencias(True, {"passed": 814}, "Rodei tudo: 814 passed. Gate verde." + SEM_DESVIO) == []
    assert e9.divergencias(False, {"failed": 1}, "Uma falha esperada, explico abaixo." + SEM_DESVIO) == []


def test_aar_mostra_gate_e_divergencia():
    d = Path(tempfile.mkdtemp())
    e9.write_aar(d, "t", {"branch": "feat/x", "overwatch_pass": False, "committed": False,
                          "operator_rc": 0, "operator": {"result_text": "Gate verde"},
                          "gate": {"ruff": "1 erro(s)"},
                          "divergencias": ["o relatório alega verde/limpo, e o gate do E-9 deu VERMELHO"]})
    aar = (d / "AAR.md").read_text(encoding="utf-8")
    assert "Gate do E-9 × relatório do operador" in aar and "Divergência" in aar
    assert "ruff 1 erro(s)" in aar


def test_ciclo_live_grava_gate_no_metrics():
    code = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n'); import json; "
            "print(json.dumps({'type': 'result', 'is_error': False, 'result': 'tudo verde\\n## Desvios da OPORD\\nnenhum'}))")
    rc, m, _ = _live(code, overwatch="echo 'All checks passed!'")
    assert rc == 0, m
    assert m["baseline_pass"] is True and m["gate"]["ruff"] == "limpo"
    assert m["divergencias"] == []


def test_briefing_proibe_teste_em_paralelo_e_verde_sem_prova():
    op = _mk_op({})
    brief = e9.build_brief(op / "OPORD.md", Path("/nao/existe"))
    assert "banco de teste é UM só" in brief
    assert "sem a saída que prove" in brief


def test_alegacao_negada_nao_e_divergencia():
    """Relatório com "não reporto 'verde' sem prova" é honestidade, não alegação."""
    rel = "NÃO tenho saída do gate. Conforme a disciplina, não reporto \"verde\" sem prova."
    assert e9.divergencias(False, {"ruff": "1 erro(s)"}, rel + SEM_DESVIO) == []
    assert e9.divergencias(False, {}, "Gate verde, tudo certo.")            # a alegação de fato
    assert e9.divergencias(False, {}, "Não rodei tudo. Ruff limpo, mypy ok.")  # a negação longe


def test_briefing_lista_os_comandos_liberados_ja_resolvidos():
    """A lista só existia no op.json, e o operador teve todo Bash recusado."""
    op = _mk_op({"venv": "/v", "operator": {"cmd_template":
        'claude -p x --allowedTools "Bash({venv}/bin/ruff:*)" "Bash(git status:*)" --model {model}'}})
    cfg, opord = e9.load_op(op)
    brief = e9.build_brief(opord, Path("/nao/existe"), cfg)
    assert "COMANDOS BASH LIBERADOS" in brief
    assert "`/v/bin/ruff…`" in brief and "`git status…`" in brief
    assert "{venv}" not in brief.split("COMANDOS BASH LIBERADOS")[1]


def test_baseline_cmd_proprio_roda_na_base_e_o_gate_novo_no_fim():
    """v0.3.1 — quando a tarefa é trocar a ferramenta do gate, o gate novo é vermelho no main por definição;
    a base roda o gate de hoje, e o gate novo só depois do operador. Aqui o gate novo só passa
    depois que a.py existe, e a base é `true`: sem baseline_cmd, a op abortaria com rc 7."""
    code = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n'); import json; "
            "print(json.dumps({'type': 'result', 'is_error': False, 'result': 'ok'}))")
    gate_novo = ("test -f a.py" if os.name == "posix" else
                 'python -c "import os,sys; sys.exit(0 if os.path.exists(\'a.py\') else 1)"')
    base = Path(tempfile.mkdtemp())
    repo, wt = base / "repo", base / "wt"
    e9.run(["git", "init", "-q", "-b", "main", str(repo)])
    e9.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
            "commit", "-q", "--allow-empty", "-m", "init"])
    fake = base / "fake.py"
    fake.write_text(code, encoding="utf-8")
    op = _mk_op({"target_repo": str(repo), "worktree": str(wt), "expected_artifacts": ["a.py"],
                 "operator": {"cmd_template": f'"{sys.executable}" "{fake}"', "timeout_s": 30},
                 "overwatch": {"test_cmd": gate_novo, "baseline_cmd": "true"}})
    assert _rerun(op) == 0
    m = json.loads((op / "metrics.json").read_text(encoding="utf-8"))
    assert m["baseline_pass"] is True and m["overwatch_pass"] is True


# --- v0.4: o aceite — falha antes, intocado, passa inteiro depois -------------------

# Um "pytest" de mentira: o aceite exige que a.py exista. `pular.flag` simula um skip posto por fora.
CHECK_ACEITE = ("import sys\nfrom pathlib import Path\n"
                "if Path('pular.flag').exists():\n    print('1 skipped in 0.1s'); sys.exit(0)\n"
                "if Path('a.py').exists():\n    print('1 passed in 0.1s'); sys.exit(0)\n"
                "print('FAILED check_aceite.py::test_a\\n1 failed in 0.1s'); sys.exit(1)\n")
ACEITE_CMD = {"aceite": {"cmd": f'"{sys.executable}" tests_aceite/check_aceite.py'}}
OPERADOR_CUMPRE = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n'); import json; "
                   "print(json.dumps({'type': 'result', 'is_error': False, "
                   "'result': 'fiz\\n## Desvios da OPORD\\nnenhum'}))")


def _aceite(conteudo=CHECK_ACEITE):
    return {"tests_aceite/check_aceite.py": conteudo}


def test_aceite_cumprido_entra_no_commit():
    rc, m, wt = _live(OPERADOR_CUMPRE, extra=ACEITE_CMD, aceite=_aceite())
    assert rc == 0, m
    a = m["aceite"]
    assert a["base_rc"] == 1 and a["coletados"] == 1 and a["entrega_rc"] == 0, a
    assert "cumprido" in a["veredito"]
    _, nomes = e9.run(["git", "-C", str(wt), "show", "--name-only", "--format=", "HEAD"])
    assert "tests_aceite/check_aceite.py" in nomes and "a.py" in nomes, nomes
    assert "Aceite × entrega" in (_ULTIMA_OP / "AAR.md").read_text(encoding="utf-8")


def test_aceite_que_ja_passa_aborta_sem_despachar():
    marca = Path(tempfile.mkdtemp()) / "operador_rodou"
    rc, m, _ = _live(f"from pathlib import Path; Path(r'{marca}').write_text('x')", extra=ACEITE_CMD,
                     aceite=_aceite("print('1 passed in 0.1s')\n"))
    assert rc == e9.RC_ACEITE_BASE, m
    assert "já PASSA" in m["status"] and not marca.exists()


def test_aceite_com_erro_de_coleta_aborta():
    """rc 2 do pytest (ImportError na coleta) não é um teste falhando: não prova nada."""
    rc, m, _ = _live(OPERADOR_CUMPRE, extra=ACEITE_CMD,
                     aceite=_aceite("import sys; print('1 error in 0.1s'); sys.exit(2)\n"))
    assert rc == e9.RC_ACEITE_BASE, m
    assert "não rodou como teste" in m["status"]


def test_aceite_mexido_pelo_operador_aborta_mesmo_verde():
    code = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n'); "
            "Path('tests_aceite/check_aceite.py').write_text('print(\"1 passed\")\\n')")
    rc, m, _ = _live(code, extra=ACEITE_CMD, aceite=_aceite())
    assert rc == e9.RC_ACEITE_ENTREGA, m
    assert m["aceite"]["mexidos"] == ["tests_aceite/check_aceite.py"]
    assert "overwatch_pass" not in m, "com a prova alterada não devia ter chegado ao gate"


def test_aceite_pulado_nao_e_cumprido():
    code = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n'); "
            "Path('pular.flag').write_text('')")
    rc, m, _ = _live(code, extra=ACEITE_CMD, aceite=_aceite())
    assert rc == e9.RC_ACEITE_ENTREGA, m
    assert "pulados" in m["status"] and m["committed"] is False


def test_aceite_sem_pasta_barrado():
    with blocks():
        e9.load_op(_mk_op(ACEITE_CMD))


def test_aceite_vai_ao_briefing_como_so_leitura():
    brief = e9.build_brief(_mk_op({}) / "OPORD.md", Path("/nao/existe"),
                           aceite=["tests/aceite/test_x.py"])
    assert "TESTES DE ACEITE (só leitura)" in brief and "`tests/aceite/test_x.py`" in brief
    com_wt = e9.build_brief(_mk_op({}) / "OPORD.md", Path("/nao/existe"), {"worktree": "/w/smoke/"},
                            aceite=["tests/aceite/test_x.py"])
    assert "`/w/smoke/tests/aceite/test_x.py`" in com_wt, "o caminho vai na worktree, não no alvo"


def test_contagem_do_pytest():
    assert e9.contagem("2 failed, 3 passed, 1 skipped, 1 error in 2s") == {
        "passed": 3, "failed": 2, "skipped": 1, "errors": 1}
    assert e9.julgar_aceite_entrega(0, {"passed": 2}, 3)              # menos que a base coletou
    assert e9.julgar_aceite_entrega(0, {"passed": 3}, 3) is None


def test_contagem_do_unittest():
    """No unittest o import dentro da função vira erro (E), não falha (F): na base conta."""
    base = "EEE\n" + "=" * 20 + "\nERROR: test_a\n" + "-" * 20 + "\nRan 3 tests in 0.001s\n\nFAILED (errors=3)\n"
    assert e9.contagem(base) == {"errors": 3}
    assert e9.julgar_aceite_base({}, 1, e9.contagem(base)) is None
    assert e9.contagem("...\nRan 3 tests in 0.001s\n\nOK\n") == {"passed": 3}
    assert e9.contagem("Ran 3 tests in 0.1s\n\nOK (skipped=1)\n") == {"passed": 2, "skipped": 1}
    coleta = ("E\nERROR: test_x (unittest.loader._FailedTest.test_x)\nImportError: Failed to import "
              "test module: test_x\nRan 1 test in 0.000s\n\nFAILED (errors=1)\n")
    assert e9.julgar_aceite_base({}, 1, e9.contagem(coleta)), "erro de coleta passou como teste"


# --- v0.5: a revisão no alvo — o número que o gate não dá ---------------------------

def _revisar():
    spec = importlib.util.spec_from_file_location("revisar", Path(__file__).with_name("revisar.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_revisar_grava_o_desfecho_e_preserva_o_aar_escrito_a_mao():
    rv = _revisar()
    d = Path(tempfile.mkdtemp())
    (d / "metrics.json").write_text(json.dumps({"status": "OK", "rc": 0}), encoding="utf-8")
    (d / "AAR.md").write_text("# AAR\n\n3. Por quê? → análise escrita à mão\n", encoding="utf-8")
    rv.revisar(d, "como_veio", commit_alvo="abc1234")
    rv.revisar(d, "consertado", "estreitei o except", "def5678")         # a 2ª troca a seção
    m = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
    assert m["revisao"]["desfecho"] == "consertado" and m["revisao"]["commit_alvo"] == "def5678"
    aar = (d / "AAR.md").read_text(encoding="utf-8")
    assert "análise escrita à mão" in aar and aar.count("## Revisão no alvo") == 1
    assert "estreitei o except" in aar and "abc1234" not in aar


def test_revisar_recusa_conserto_sem_nota():
    rv = _revisar()
    d = Path(tempfile.mkdtemp())
    (d / "metrics.json").write_text("{}", encoding="utf-8")
    for desfecho in ("consertado", "descartado", "inventado"):
        try:
            rv.revisar(d, desfecho, "")
            raise AssertionError(f"{desfecho} sem nota devia ser recusado")
        except ValueError:
            pass
    assert rv.main(["--op", str(d), "--consertado", "  "]) == 2


# --- v0.4: as travas do alvo — quem redige a OPORD cita cada uma --------------------

def _alvo(travas="- **[XX-01]** uma.\n- **[XX-02]** outra.\n"):
    d = Path(tempfile.mkdtemp())
    (d / "teste.md").write_text(travas, encoding="utf-8")
    return d


def _op_com_opord(texto):
    op = _mk_op({"alvo": "teste"})
    (op / "OPORD.md").write_text(texto, encoding="utf-8")
    return op


def test_opord_sem_uma_trava_citada_barrada():
    op = _op_com_opord("# OPORD\n## Travas do alvo\n- XX-01: aplica, como.\n## 1. SITUATION\n")
    with blocks():
        e9.load_op(op, alvos_dir=_alvo())


def test_trava_citada_fora_da_secao_nao_conta():
    op = _op_com_opord("# OPORD\n## Travas do alvo\n- [XX-01] aplica.\n## 5. COMMAND\nXX-02 aqui não vale\n")
    with blocks():
        e9.load_op(op, alvos_dir=_alvo())


def test_alvo_inexistente_barrado():
    op = _op_com_opord("## Travas do alvo\n- XX-01\n")
    with blocks():
        e9.load_op(op, alvos_dir=Path(tempfile.mkdtemp()))


def test_opord_com_todas_as_travas_carrega_e_vai_ao_briefing():
    alvos = _alvo()
    op = _op_com_opord("# OPORD\n## Travas do alvo\n- [XX-01] aplica: assim.\n"
                       "- [XX-02] não se aplica: a op não cria rota.\n")
    cfg, opord = e9.load_op(op, alvos_dir=alvos)
    brief = e9.build_brief(opord, Path("/nao/existe"), cfg, alvo=e9.alvo_path(cfg, alvos))
    assert "TRAVAS DO ALVO" in brief and "**[XX-02]** outra" in brief


def test_travas_do_exemplo_tem_ids_unicos():
    ids = e9._TRAVA_ID.findall((e9.ALVOS_DIR / "exemplo.md").read_text(encoding="utf-8"))
    assert ids and len(ids) == len(set(ids)) and all(i.startswith("EX-") for i in ids), ids


def test_tarefa_de_exemplo_passa_no_ensaio():
    """A tarefa do kit usa aceite e travas: o contrato dela tem de carregar como está."""
    op = Path(__file__).resolve().parent.parent / "ops" / "exemplo-slug"
    cfg, _ = e9.load_op(op)
    assert cfg["alvo"] == "exemplo" and e9.aceite_files(op)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = fail = 0
    for t in tests:
        try:
            t(); print(f"  ok   ✅ {t.__name__}"); ok += 1
        except AssertionError as e:
            print(f"  FAIL ❌ {t.__name__} — {e}"); fail += 1
    print(f"\nRESULTADO: {ok} ok, {fail} falha(s)")
    sys.exit(1 if fail else 0)
