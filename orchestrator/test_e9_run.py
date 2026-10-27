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

def _live(fake_operator_py: str, overwatch="true"):
    """Roda o main() em --live contra um repo git descartável, com um operador falso."""
    base = Path(tempfile.mkdtemp())
    repo, wt = base / "repo", base / "wt"
    e9.run(["git", "init", "-q", "-b", "main", str(repo)])
    e9.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
            "commit", "-q", "--allow-empty", "-m", "init"])
    fake = base / "fake.py"
    fake.write_text(fake_operator_py, encoding="utf-8")
    op = _mk_op({"target_repo": str(repo), "worktree": str(wt), "expected_artifacts": ["a.py"],
                 "operator": {"cmd_template": f'"{sys.executable}" "{fake}"', "timeout_s": 30},
                 "overwatch": {"test_cmd": overwatch}})
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
