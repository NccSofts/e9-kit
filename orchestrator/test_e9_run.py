#!/usr/bin/env python3
"""
test_e9_run.py — testes da policy codificada no harness.

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

os.environ["E9_SEM_AVISO"] = "1"    # os voos de teste nunca mandam aviso
os.environ["E9_SEM_REVISOR"] = "1"  # nem pedem parecer pago ao OpenRouter (o .env pode ter a chave)
os.environ["E9_SEM_ESPERA"] = "1"   # nem esperam a máquina (v0.7)
os.environ["E9_SLOTS_DIR"] = tempfile.mkdtemp(prefix="e9-slots-")   # nem disputam os slots dos voos reais
os.environ["E9_PRONTO_TETO_S"] = "2"

# v0.7 — o `docker` dos testes é falso: guarda o que recebeu num JSON e responde como o de verdade.
# Nenhum teste sobe container de verdade (o voo de prova é que faz isso).
_FAKE_DOCKER = Path(tempfile.mkdtemp(prefix="e9-fakedocker-")) / "docker.py"
_FAKE_DOCKER.write_text(r'''
import json, os, sys
st_path = os.environ["E9_FAKE_STATE"]
st = json.load(open(st_path, encoding="utf-8"))
a = sys.argv[1:]
st.setdefault("chamadas", []).append(a)
entrada = sys.stdin.read() if "-i" in a else ""
rc, out = 0, ""
if a[0] == "run":
    if st.get("run_falha"):
        rc, out = 1, "Error: pull access denied"
    else:
        nome = a[a.index("--name") + 1]
        rot = dict(x.split("=", 1) for x in (a[i + 1] for i, v in enumerate(a) if v == "--label"))
        st.setdefault("containers", {})[nome] = rot
        st["super_no_ambiente"] = bool(os.environ.get("POSTGRES_PASSWORD"))
        out = "abc123"
elif a[0] == "port":
    out = f"127.0.0.1:{st['porta']}"
elif a[0] == "exec":
    if st.get("exec_falha"):
        rc, out = 2, "psql: error: connection refused"
    else:
        if entrada:
            st.setdefault("stdin", []).append(entrada)
        out = "app_user|1" if "current_user" in " ".join(a) else "1"
elif a[0] == "rm":
    st.setdefault("removidos", []).append(a[-1])
    st.get("containers", {}).pop(a[-1], None)
elif a[0] == "ps":
    out = "\n".join(f"{n} {r.get('e9.pid', '')}" for n, r in st.get("containers", {}).items())
json.dump(st, open(st_path, "w", encoding="utf-8"))
print(out)
sys.exit(rc)
''', encoding="utf-8")
os.environ["E9_DOCKER"] = json.dumps([sys.executable, str(_FAKE_DOCKER)])
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
    raise AssertionError("esperava SystemExit (policy abort), mas não houve")


def _mk_op(over):
    base = {"op": "t", "target_repo": "/srv/repos/projeto", "branch": "feat/ok",
            "worktree": "/tmp/w", "operator": {"cmd_template": "echo"},
            "gate": {"test_cmd": "true"}, "expected_artifacts": ["a.py"]}
    d = Path(tempfile.mkdtemp())
    (d / "spec.md").write_text("x", encoding="utf-8")
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


def test_target_em_producao_barrado_pelo_prod_markers():
    with blocks():
        e9.load_op(_mk_op({"target_repo": "/srv/producao/app", "prod_markers": ["/srv/producao"]}))
    e9.load_op(_mk_op({"target_repo": "/srv/repos/app", "prod_markers": ["/srv/producao"]}))   # fora: passa


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


# --- v0.2: o agente não trava -----------------------------------------------

def test_run_nao_herda_stdin():
    """Regressão do travamento das Ops 002/003: filho que lê stdin tem de receber EOF na
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
    """O agente roda debaixo de `sh -c`: matar só o shell deixaria o `claude` órfão."""
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


# --- v0.2: saída do agente vai pro run report ---------------------------------------

def test_parse_stream_json_pega_result_final():
    linhas = [json.dumps({"type": "system", "subtype": "init"}),
              "lixo que não é json",
              json.dumps({"type": "assistant", "message": {}}),
              json.dumps({"type": "result", "subtype": "success", "is_error": False,
                          "result": "Feito. DESVIO: pus o teste em tests/x.py.",
                          "num_turns": 7, "duration_ms": 120000, "total_cost_usd": 0.42,
                          "usage": {"input_tokens": 10, "output_tokens": 20}})]
    m = e9.parse_agent_output("\n".join(linhas))
    assert m["stream_json"] and m["num_turns"] == 7 and m["total_cost_usd"] == 0.42
    assert "DESVIO" in m["result_text"]


def test_parse_registra_o_modelo_que_respondeu():
    linhas = [json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-4-6"}),
              json.dumps({"type": "assistant", "message": {"model": "claude-sonnet-4-6"}}),
              json.dumps({"type": "user", "message": {"model": "nao-conta"}}),
              json.dumps({"type": "result", "result": "ok", "num_turns": 1})]
    m = e9.parse_agent_output("\n".join(linhas))
    assert m["modelos"] == ["claude-sonnet-4-6"]
    d = Path(tempfile.mkdtemp())
    e9.write_aar(d, "t", {"branch": "feat/x", "gate_pass": True, "committed": True,
                          "operator_rc": 0, "operator": m})
    assert "modelo=claude-sonnet-4-6" in (d / "report.md").read_text(encoding="utf-8")


def test_parse_aguenta_recusa_com_message_em_texto_e_a_leva_ao_aar():
    """Visto num run real: o `permission_denied` traz `message` como TEXTO, e o parser do modelo quebrava."""
    linhas = [json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-5-5"}),
              json.dumps({"type": "system", "subtype": "permission_denied", "tool_name": "Bash",
                          "message": "This command requires approval: git rm -r x"}),
              json.dumps({"type": "result", "result": "parcial", "num_turns": 3})]
    m = e9.parse_agent_output("\n".join(linhas))
    assert m["modelos"] == ["claude-sonnet-5-5"] and m["result_text"] == "parcial"
    assert m["recusas"] == ["Bash: This command requires approval: git rm -r x"]
    d = Path(tempfile.mkdtemp())
    e9.write_aar(d, "t", {"branch": "feat/x", "operator_rc": 0, "operator": m})
    assert "Permissões recusadas ao agente (1)" in (d / "report.md").read_text(encoding="utf-8")


def test_padrao_do_agente_e_id_completo_nao_alias():
    assert e9.MODELO_AGENTE.startswith("claude-") and e9.MODELO_AGENTE != "sonnet"
    cfg = {"worktree": "/w", "target_repo": "/r"}
    assert e9._fmt("{model}", cfg) == e9.MODELO_AGENTE


def test_parse_texto_puro_vira_relatorio():
    m = e9.parse_agent_output("relatório em texto\n")
    assert not m["stream_json"] and m["result_text"] == "relatório em texto"


def test_aar_carrega_relatorio_do_agente():
    d = Path(tempfile.mkdtemp())
    e9.write_aar(d, "t", {"branch": "feat/x", "gate_pass": True, "committed": True,
                          "operator_rc": 0, "operator": {"result_text": "DESVIO declarado"},
                          "gate_tail": "61 passed"})
    aar = (d / "report.md").read_text(encoding="utf-8")
    assert "DESVIO declarado" in aar and "61 passed" in aar
    assert json.loads((d / "metrics.json").read_text(encoding="utf-8"))["agent_rc"] == 0


# --- v0.2: rc=0 não basta — is_error do `result` derruba o agente -----------

def _live(fake_operator_py: str, gate="true", extra=None, aceite=None):
    """Roda o main() em --live contra um repo git descartável, com um agente falso.
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
                 "gate": {"test_cmd": gate}, **(extra or {})})
    for rel, conteudo in (aceite or {}).items():
        (op / "aceite" / rel).parent.mkdir(parents=True, exist_ok=True)
        (op / "aceite" / rel).write_text(conteudo, encoding="utf-8")
    if aceite:
        (op / "spec.md").write_text(REQUISITOS_PADRAO, encoding="utf-8")
    global _ULTIMA_OP
    _ULTIMA_OP = op
    argv = sys.argv
    sys.argv = ["e9_run.py", "--op", str(op), "--live"]
    try:
        rc = e9.main()
    finally:
        sys.argv = argv
    return rc, json.loads((op / "metrics.json").read_text(encoding="utf-8")), wt


REQUISITOS_PADRAO = "# spec\n## Requisitos\n- **FR-001** — o a.py existe.\n"


def test_agente_is_error_com_rc0_aborta_antes_do_gate():
    """Caso real de 24/10: OAuth vencido, `claude -p` sai 0 com is_error=true."""
    payload = json.dumps({"type": "result", "is_error": True, "api_error_status": 401,
                          "result": "Failed to authenticate. API Error: 401"})
    rc, m, _ = _live(f"print({payload!r})")
    assert rc == 6, rc
    assert m["agent_ok"] is False and m["committed"] is False
    assert "gate_pass" not in m, "não devia ter chegado ao gate"
    assert "401" in m["status"]


def test_ciclo_verde_commita_com_subject_e_relatorio():
    code = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n'); import json; "
            "print(json.dumps({'type': 'result', 'is_error': False, 'result': 'fiz a.py', "
            "'num_turns': 2, 'total_cost_usd': 0.01}))")
    rc, m, wt = _live(code)
    assert rc == 0, m
    assert m["committed"] and m["commit_sha"]
    assert m["rc"] == 0 and m["arquivos"] == ["a.py"], (m.get("rc"), m.get("arquivos"))
    aar = (_ULTIMA_OP / "report.md").read_text(encoding="utf-8")
    assert e9.LEMBRETE_REVISAO in aar and "revisar.py --op ops/" in aar, "v0.5.1: o voo pede a revisão"
    rv = _revisar()
    rv.revisar(_ULTIMA_OP, "como_veio", commit_alvo="abc1234")
    aar = (_ULTIMA_OP / "report.md").read_text(encoding="utf-8")
    assert e9.LEMBRETE_REVISAO not in aar and "## Revisão no alvo" in aar, "a revisão gravada tira o lembrete"
    assert [f["fase"] for f in m["fases"]] == ["preparo", "base", "agente", "gate", "reviewer", "commit", "fim"]
    assert m["reviewer"] == {"estado": "desligado"}, m.get("reviewer")
    brief = (_ULTIMA_OP / "_brief.txt").read_text(encoding="utf-8")
    assert "TEMPO DO PORTÃO NESTE VOO" in brief and "timeout: 600000" in brief, "v0.5.1: o tempo medido vai ao briefing"
    assert m["aviso"] == "sem aviso configurado", m.get("aviso")   # o kit não traz aviso.py
    assert m["fases"][2]["teto_s"] == 30, "o teto do agente vai junto"
    assert m["agent"]["result_text"] == "fiz a.py"
    _, subj = e9.run(["git", "-C", str(wt), "log", "-1", "--format=%s"])
    assert subj.strip() == "chore(e9): op t", subj


def test_arquivo_fora_dos_artefatos_barra_o_commit():
    """O gate testou a árvore toda; commitar só parte dela entregaria código não testado."""
    code = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n'); "
            "Path('b.py').write_text('y = 2\\n')")
    rc, m, _ = _live(code)
    assert rc == 4, m
    assert m["committed"] is False and "b.py" in m["status"]


# --- v0.3: linha de base antes do agente ------------------------------------

_ULTIMA_OP = None


def _rerun(op):
    argv = sys.argv
    sys.argv = ["e9_run.py", "--op", str(op), "--live"]
    try:
        return e9.main()
    finally:
        sys.argv = argv


def test_linha_de_base_vermelha_aborta_sem_despachar():
    """Visto num run real: o `main` já nascia vermelho, e o agente voou 45 min à toa. O gate aqui só
    passa depois que a.py existe, então na base (antes do agente) ele é vermelho."""
    marca = Path(tempfile.mkdtemp()) / "agente_rodou"
    rc, m, _ = _live(f"from pathlib import Path; Path(r'{marca}').write_text('x')",
                     gate="test -f a.py" if os.name == "posix" else 'python -c "import os,sys; sys.exit(0 if os.path.exists(\'a.py\') else 1)"')
    assert rc == 7, m
    assert m["rc"] == 7, "o metrics.json tem de gravar o código de saída"
    assert m["baseline_pass"] is False and "linha de base" in m["status"].lower()
    assert not marca.exists(), "o agente foi despachado sobre uma base vermelha"


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
    # o commit limpou a árvore; o 2º voo sobre a mesma op segue (o agente reescreve a.py igual,
    # e não há nada novo a commitar, então o commit falha com 4 — mas passou pela worktree)
    rc2 = _rerun(_ULTIMA_OP)
    assert rc2 in (0, 4), rc2


def test_voo_de_treino_nao_pede_revisao():
    code = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n'); import json; "
            "print(json.dumps({'type': 'result', 'is_error': False, 'result': 'ok'}))")
    rc, m, _ = _live(code, extra={"treino": True})
    assert rc == 0 and m["treino"] is True
    assert e9.LEMBRETE_REVISAO not in (_ULTIMA_OP / "report.md").read_text(encoding="utf-8")


def test_revoo_guarda_o_voo_anterior():
    """v0.5.1 — o metrics.json do re-voo leva o resumo do voo anterior: o verde de primeira se mede."""
    code = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n'); "
            "Path('b.py').write_text('y = 2\\n')")
    rc, m1, _ = _live(code)                # 1º voo: commit barrado (rc 4)
    assert rc == 4 and m1["previous_runs"] == []
    _rerun(_ULTIMA_OP)                     # 2º voo: worktree suja (rc 8)
    m2 = json.loads((_ULTIMA_OP / "metrics.json").read_text(encoding="utf-8"))
    assert m2["rc"] == 8 and [v["rc"] for v in m2["previous_runs"]] == [4]
    _rerun(_ULTIMA_OP)                     # 3º voo: o histórico acumula
    m3 = json.loads((_ULTIMA_OP / "metrics.json").read_text(encoding="utf-8"))
    assert [v["rc"] for v in m3["previous_runs"]] == [4, 8]


def test_baseline_desligavel_no_op_json():
    base = {"gate": {"test_cmd": "true", "baseline": False}}
    cfg, _ = e9.load_op(_mk_op(base))
    assert cfg["gate"]["baseline"] is False


# --- v0.3: o gate do E-9 ao lado do relatório do agente ----------------------

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
    """Visto num run real: "Gate verde" com cinco falhas."""
    d = e9.divergencias(False, {"failed": 5, "passed": 795}, "Status: Gate verde. Tudo ok.")
    assert any("VERMELHO" in x for x in d)


def test_divergencia_de_contagem():
    """Visto num run real: "418 passed" com o gate em 814."""
    d = e9.divergencias(True, {"passed": 814}, "Resultado: 418 passed, 0 failed.")
    assert d and "418" in d[0] and "814" in d[0]


SEM_DESVIO = "\n\n## Desvios da spec\nnenhum"


def test_relatorio_sem_secao_de_desvios_vira_achado():
    """v0.3.3 — nas Ops 010 e 013 o gate deu verde e o defeito era um desvio não declarado."""
    d = e9.divergencias(True, {"passed": 10}, "Fiz tudo. 10 passed.")
    assert any("Desvios da spec" in x for x in d)
    assert e9.divergencias(True, {"passed": 10},
                           "Fiz. 10 passed.\n### Desvios da spec\n- a spec dizia X -> fiz Y") == []


def test_timeout_do_portao_e_o_dobro_da_base_dentro_dos_limites():
    """Visto num run real: `timeout: 300000` numa suíte de 6m20s foi para segundo plano sozinho."""
    cfg = {"operator": {"cmd_template": "BASH_MAX_TIMEOUT_MS=1200000 claude -p x"}}
    assert e9.timeout_do_portao_ms(cfg, 380) == 760_000      # uma suíte de 6m20s: o dobro
    assert e9.timeout_do_portao_ms(cfg, 10) == 600_000       # piso de 10 min
    assert e9.timeout_do_portao_ms(cfg, 900) == 1_200_000    # teto do cmd_template
    brief = e9.build_brief(_mk_op({}) / "spec.md", Path("/nao/existe"))
    assert "Não passe ao Bash um `timeout` menor que o tempo do portão" in brief


def test_briefing_proibe_segundo_plano():
    brief = e9.build_brief(_mk_op({}) / "spec.md", Path("/nao/existe"))
    assert "NUNCA rode nada em segundo plano" in brief


def test_restos_do_agente_na_worktree_sao_mortos():
    """Visto num run real: o agente deixou suítes em segundo plano e encerrou. Um filho solto em sessão
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
    brief = e9.build_brief(_mk_op({}) / "spec.md", Path("/nao/existe"))
    assert "## Desvios da spec" in brief and "a spec dizia" in brief


def test_sem_divergencia_quando_bate():
    assert e9.divergencias(True, {"passed": 814}, "Rodei tudo: 814 passed. Gate verde." + SEM_DESVIO) == []
    assert e9.divergencias(False, {"failed": 1}, "Uma falha esperada, explico abaixo." + SEM_DESVIO) == []


def test_aar_mostra_gate_e_divergencia():
    d = Path(tempfile.mkdtemp())
    e9.write_aar(d, "t", {"branch": "feat/x", "gate_pass": False, "committed": False,
                          "operator_rc": 0, "operator": {"result_text": "Gate verde"},
                          "gate": {"ruff": "1 erro(s)"},
                          "divergencias": ["o relatório alega verde/limpo, e o gate do E-9 deu VERMELHO"]})
    aar = (d / "report.md").read_text(encoding="utf-8")
    assert "Gate do E-9 × relatório do agente" in aar and "Divergência" in aar
    assert "ruff 1 erro(s)" in aar


def test_ciclo_live_grava_gate_no_metrics():
    code = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n'); import json; "
            "print(json.dumps({'type': 'result', 'is_error': False, 'result': 'tudo verde\\n## Desvios da spec\\nnenhum'}))")
    rc, m, _ = _live(code, gate="echo 'All checks passed!'")
    assert rc == 0, m
    assert m["baseline_pass"] is True and m["gate"]["ruff"] == "limpo"
    assert m["divergencias"] == []


def test_briefing_proibe_teste_em_paralelo_e_verde_sem_prova():
    op = _mk_op({})
    brief = e9.build_brief(op / "spec.md", Path("/nao/existe"))
    assert "banco de teste é UM só" in brief
    assert "sem a saída que prove" in brief


def test_alegacao_negada_nao_e_divergencia():
    """Visto num run real: "não reporto 'verde' sem prova" é honestidade, não alegação."""
    rel = "NÃO tenho saída do gate. Conforme a disciplina, não reporto \"verde\" sem prova."
    assert e9.divergencias(False, {"ruff": "1 erro(s)"}, rel + SEM_DESVIO) == []
    assert e9.divergencias(False, {}, "Gate verde, tudo certo.")            # a alegação de fato
    assert e9.divergencias(False, {}, "Não rodei tudo. Ruff limpo, mypy ok.")  # a negação longe


def test_briefing_lista_os_comandos_liberados_ja_resolvidos():
    """Visto num run real: a lista só existia no op.json, e o agente teve todo Bash recusado."""
    op = _mk_op({"venv": "/v", "operator": {"cmd_template":
        'claude -p x --allowedTools "Bash({venv}/bin/ruff:*)" "Bash(git status:*)" --model {model}'}})
    cfg, opord = e9.load_op(op)
    brief = e9.build_brief(opord, Path("/nao/existe"), cfg)
    assert "COMANDOS BASH LIBERADOS" in brief
    assert "`/v/bin/ruff…`" in brief and "`git status…`" in brief
    assert "{venv}" not in brief.split("COMANDOS BASH LIBERADOS")[1]


def test_baseline_cmd_proprio_roda_na_base_e_o_gate_novo_no_fim():
    """v0.3.1 — Op H (TD-058): o gate novo (mypy com a 2.1) é vermelho no main por definição;
    a base roda o gate de hoje, e o gate novo só depois do agente. Aqui o gate novo só passa
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
                 "gate": {"test_cmd": gate_novo, "baseline_cmd": "true"}})
    assert _rerun(op) == 0
    m = json.loads((op / "metrics.json").read_text(encoding="utf-8"))
    assert m["baseline_pass"] is True and m["gate_pass"] is True


# --- v0.4: o aceite — falha antes, intocado, passa inteiro depois -------------------

# Um "pytest" de mentira: o aceite exige que a.py exista. `pular.flag` simula um skip posto por fora.
CHECK_ACEITE = ("import sys\nfrom pathlib import Path\n"
                "def test_fr_001_a_existe(): pass\n"
                "if Path('pular.flag').exists():\n    print('1 skipped in 0.1s'); sys.exit(0)\n"
                "if Path('a.py').exists():\n    print('1 passed in 0.1s'); sys.exit(0)\n"
                "print('FAILED check_aceite.py::test_fr_001_a_existe\\n1 failed in 0.1s'); sys.exit(1)\n")
ACEITE_CMD = {"aceite": {"cmd": f'"{sys.executable}" tests_aceite/check_aceite.py'}}
AGENTE_CUMPRE = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n'); import json; "
                   "print(json.dumps({'type': 'result', 'is_error': False, "
                   "'result': 'fiz\\n## Desvios da spec\\nnenhum'}))")


def _aceite(conteudo=CHECK_ACEITE):
    return {"tests_aceite/check_aceite.py": conteudo}


def test_aceite_cumprido_entra_no_commit():
    rc, m, wt = _live(AGENTE_CUMPRE, extra=ACEITE_CMD, aceite=_aceite())
    assert rc == 0, m
    a = m["acceptance"]
    assert a["base_rc"] == 1 and a["coletados"] == 1 and a["entrega_rc"] == 0, a
    assert "cumprido" in a["veredito"]
    _, nomes = e9.run(["git", "-C", str(wt), "show", "--name-only", "--format=", "HEAD"])
    assert "tests_aceite/check_aceite.py" in nomes and "a.py" in nomes, nomes
    assert "Acceptance tests × entrega" in (_ULTIMA_OP / "report.md").read_text(encoding="utf-8")


def test_aceite_que_ja_passa_aborta_sem_despachar():
    marca = Path(tempfile.mkdtemp()) / "agente_rodou"
    rc, m, _ = _live(f"from pathlib import Path; Path(r'{marca}').write_text('x')", extra=ACEITE_CMD,
                     aceite=_aceite("def test_fr_001_a(): pass\nprint('1 passed in 0.1s')\n"))
    assert rc == e9.RC_ACCEPTANCE_BASE, m
    assert "já PASSA" in m["status"] and not marca.exists()


def test_aceite_com_erro_de_coleta_aborta():
    """rc 2 do pytest (ImportError na coleta) não é um teste falhando: não prova nada."""
    rc, m, _ = _live(AGENTE_CUMPRE, extra=ACEITE_CMD,
                     aceite=_aceite("def test_fr_001_a(): pass\nimport sys; print('1 error in 0.1s'); sys.exit(2)\n"))
    assert rc == e9.RC_ACCEPTANCE_BASE, m
    assert "não rodou como teste" in m["status"]


def test_acceptance_mexido_pelo_agente_aborta_mesmo_verde():
    code = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n'); "
            "Path('tests_aceite/check_aceite.py').write_text('print(\"1 passed\")\\n')")
    rc, m, _ = _live(code, extra=ACEITE_CMD, aceite=_aceite())
    assert rc == e9.RC_ACCEPTANCE_ENTREGA, m
    assert m["acceptance"]["mexidos"] == ["tests_aceite/check_aceite.py"]
    assert "gate_pass" not in m, "com a prova alterada não devia ter chegado ao gate"


def test_aceite_pulado_nao_e_cumprido():
    code = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n'); "
            "Path('pular.flag').write_text('')")
    rc, m, _ = _live(code, extra=ACEITE_CMD, aceite=_aceite())
    assert rc == e9.RC_ACCEPTANCE_ENTREGA, m
    assert "pulados" in m["status"] and m["committed"] is False


def test_aceite_sem_pasta_barrado():
    with blocks():
        e9.load_op(_mk_op(ACEITE_CMD))


def test_aceite_vai_ao_briefing_como_so_leitura():
    brief = e9.build_brief(_mk_op({}) / "spec.md", Path("/nao/existe"),
                           aceite=["tests/aceite/test_x.py"])
    assert "ACCEPTANCE TESTS (só leitura)" in brief and "`tests/aceite/test_x.py`" in brief
    com_wt = e9.build_brief(_mk_op({}) / "spec.md", Path("/nao/existe"), {"worktree": "/w/smoke/"},
                            aceite=["tests/aceite/test_x.py"])
    assert "`/w/smoke/tests/aceite/test_x.py`" in com_wt, "o caminho vai na worktree, não no alvo"


def test_contagem_do_pytest():
    assert e9.contagem("2 failed, 3 passed, 1 skipped, 1 error in 2s") == {
        "passed": 3, "failed": 2, "skipped": 1, "errors": 1}
    assert e9.julgar_acceptance_entrega(0, {"passed": 2}, 3)              # menos que a base coletou
    assert e9.julgar_acceptance_entrega(0, {"passed": 3}, 3) is None


def test_contagem_do_unittest():
    """No unittest o import dentro da função vira erro (E), não falha (F): na base conta."""
    base = "EEE\n" + "=" * 20 + "\nERROR: test_a\n" + "-" * 20 + "\nRan 3 tests in 0.001s\n\nFAILED (errors=3)\n"
    assert e9.contagem(base) == {"errors": 3}
    assert e9.julgar_acceptance_base({}, 1, e9.contagem(base)) is None
    assert e9.contagem("...\nRan 3 tests in 0.001s\n\nOK\n") == {"passed": 3}
    assert e9.contagem("Ran 3 tests in 0.1s\n\nOK (skipped=1)\n") == {"passed": 2, "skipped": 1}
    coleta = ("E\nERROR: test_x (unittest.loader._FailedTest.test_x)\nImportError: Failed to import "
              "test module: test_x\nRan 1 test in 0.000s\n\nFAILED (errors=1)\n")
    assert e9.julgar_acceptance_base({}, 1, e9.contagem(coleta)), "erro de coleta passou como teste"


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
    (d / "report.md").write_text("# Run report\n\n3. Por quê? → análise escrita à mão\n", encoding="utf-8")
    rv.revisar(d, "como_veio", commit_alvo="abc1234")
    rv.revisar(d, "consertado", "estreitei o except", "def5678")         # a 2ª troca a seção
    m = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
    assert m["review"]["desfecho"] == "consertado" and m["review"]["commit_alvo"] == "def5678"
    aar = (d / "report.md").read_text(encoding="utf-8")
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


# --- v0.4: as guardrails — quem redige a spec cita cada uma --------------------

def _alvo(travas="- **[XX-01]** uma.\n- **[XX-02]** outra.\n"):
    d = Path(tempfile.mkdtemp())
    (d / "teste.md").write_text(travas, encoding="utf-8")
    return d


def _op_com_opord(texto):
    op = _mk_op({"alvo": "teste"})
    (op / "spec.md").write_text(texto, encoding="utf-8")
    return op


def test_opord_sem_uma_trava_citada_barrada():
    op = _op_com_opord("# spec\n## Guardrails\n- XX-01: aplica, como.\n## 1. SITUATION\n")
    with blocks():
        e9.load_op(op, targets_dir=_alvo())


def test_trava_citada_fora_da_secao_nao_conta():
    op = _op_com_opord("# spec\n## Guardrails\n- [XX-01] aplica.\n## 5. COMMAND\nXX-02 aqui não vale\n")
    with blocks():
        e9.load_op(op, targets_dir=_alvo())


def test_alvo_inexistente_barrado():
    op = _op_com_opord("## Guardrails\n- XX-01\n")
    with blocks():
        e9.load_op(op, targets_dir=Path(tempfile.mkdtemp()))


def test_opord_com_todas_as_travas_carrega_e_vai_ao_briefing():
    alvos = _alvo()
    op = _op_com_opord("# spec\n## Guardrails\n- [XX-01] aplica: assim.\n"
                       "- [XX-02] não se aplica: a op não cria rota.\n")
    cfg, opord = e9.load_op(op, targets_dir=alvos)
    brief = e9.build_brief(opord, Path("/nao/existe"), cfg, alvo=e9.target_path(cfg, alvos))
    assert "GUARDRAILS" in brief and "**[XX-02]** outra" in brief


def test_guardrails_do_exemplo_tem_ids_unicos():
    ids = e9._TRAVA_ID.findall((e9.TARGETS_DIR / "exemplo.md").read_text(encoding="utf-8"))
    assert ids and len(ids) == len(set(ids)) and all(i.startswith("EX-") for i in ids), ids


# --- v0.7: o workspace do voo -------------------------------------------------------------

import signal as _signal  # noqa: E402
import socket as _socket  # noqa: E402

_OUVINTES = []          # sockets abertos durante a sessão de testes: a "porta do Postgres" do voo


def _docker_falso(**over) -> Path:
    """Zera o estado do docker falso. A porta publicada é um socket que escuta de verdade, para a
    conferência do host (a porta abre?) ter o que conferir."""
    s = _socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(5)
    _OUVINTES.append(s)
    st = Path(tempfile.mkdtemp()) / "estado.json"
    st.write_text(json.dumps({"porta": s.getsockname()[1], **over}), encoding="utf-8")
    os.environ["E9_FAKE_STATE"] = str(st)
    return st


def _estado(st: Path) -> dict:
    return json.loads(st.read_text(encoding="utf-8"))


def _py(code: str) -> str:
    """Um comando de shell que roda `code` no Python dos testes (o gate e o agente falsos)."""
    f = Path(tempfile.mkdtemp()) / "c.py"
    f.write_text(code, encoding="utf-8")
    return f'"{sys.executable}" "{f}"'


ESCREVE_A_E_ENV = ("import os, json; from pathlib import Path; "
                   "Path('a.py').write_text('x = 1\\n'); "
                   "Path('env_visto.txt').write_text(os.environ.get('APP_TEST_DATABASE_URL', '')); "
                   "print(json.dumps({'type': 'result', 'is_error': False, 'result': 'ok\\n## Desvios da spec\\nnenhum'}))")


def test_workspace_sobe_injeta_o_banco_e_derruba_no_fim():
    st = _docker_falso()
    gate = _py("import os, sys; sys.exit(0 if 'APP_TEST_DATABASE_URL' in os.environ else 1)")
    rc, m, wt = _live(ESCREVE_A_E_ENV, gate=gate,
                      extra={"workspace": {"banco": "exemplo"},
                             "optional_artifacts": ["env_visto.txt"]})
    assert rc == 0, m.get("status")
    e = _estado(st)
    w = m["workspace"]
    assert w["container"] in e["removidos"] and not e["containers"], "o container tem de sair no fim"
    url = (Path(wt) / "env_visto.txt").read_text(encoding="utf-8")
    assert url.startswith("postgresql+psycopg://app_user:") and f":{w['porta']}/app_test" in url
    texto = (_ULTIMA_OP / "metrics.json").read_text(encoding="utf-8")
    senha = url.split(":")[2].split("@")[0]
    assert senha not in texto and "postgresql+psycopg" not in texto, "a URL leva a senha: fica fora do metrics"
    assert w["variaveis"] == ["APP_DATABASE_URL", "APP_TEST_DATABASE_URL"]
    run_args = next(c for c in e["chamadas"] if c[0] == "run")
    assert "POSTGRES_PASSWORD" in run_args and not any(senha in a or "POSTGRES_PASSWORD=" in a for a in run_args)
    assert e["super_no_ambiente"], "a senha do superuser vai pelo ambiente do docker, não pela linha"
    assert any(f"ALTER ROLE app_user PASSWORD '{senha}'" in s for s in e["stdin"]), "a senha do papel vai pelo stdin"
    assert not any(senha in " ".join(c) for c in e["chamadas"]), "senha nenhuma na linha de comando"
    assert "--rm" in run_args and "127.0.0.1::5432" in run_args and "fsync=off" in run_args, run_args
    assert "BANCO DESTE VOO" in (_ULTIMA_OP / "_brief.txt").read_text(encoding="utf-8")
    assert "Banco do voo (v0.7)" in (_ULTIMA_OP / "report.md").read_text(encoding="utf-8")


def test_senha_do_voo_nao_sai_nem_quando_o_pytest_imprime_a_url():
    """Voo de prova de 02/11: a asserção que falha mostra a URL do banco, com a senha, e o fim da
    saída do gate vai ao run report e ao metrics.json, que entram no git."""
    _docker_falso()
    gate = _py("import os, sys\nprint('E  AssertionError: ' + os.environ['APP_TEST_DATABASE_URL'])\n"
               "sys.exit(1 if os.path.exists('a.py') else 0)")
    rc, m, wt = _live(ESCREVE_A_E_ENV, gate=gate, extra={"workspace": {"banco": "exemplo"}})
    assert rc == 3, m.get("status")
    senha = (Path(wt) / "env_visto.txt").read_text(encoding="utf-8").split(":")[2].split("@")[0]
    for nome in ("metrics.json", "report.md", "gate.log", "baseline.log", "agent.jsonl"):
        p = _ULTIMA_OP / nome
        if p.exists():
            assert senha not in p.read_text(encoding="utf-8"), f"a senha do voo vazou em {nome}"
    assert "<senha-do-voo>" in (_ULTIMA_OP / "report.md").read_text(encoding="utf-8")


def test_workspace_derruba_quando_o_gate_e_vermelho():
    st = _docker_falso()
    gate = _py("import os, sys; sys.exit(1 if os.path.exists('a.py') else 0)")   # base verde, gate vermelho
    rc, m, _ = _live(ESCREVE_A_E_ENV, gate=gate, extra={"workspace": {"banco": "exemplo"}})
    assert rc == 3, m.get("status")
    assert m["workspace"]["container"] in _estado(st)["removidos"]


def test_banco_que_nao_responde_aborta_sem_despachar():
    st = _docker_falso(exec_falha=True)
    marca = Path(tempfile.mkdtemp()) / "agente_rodou"
    rc, m, _ = _live(f"from pathlib import Path; Path(r'{marca}').write_text('x')",
                     extra={"workspace": {"banco": "exemplo"}})
    assert rc == 12, m.get("status")
    assert not marca.exists(), "o agente não pode voar sem o banco"
    assert "não ficou pronto" in m["status"] and "base" not in [f["fase"] for f in m["fases"]]
    assert not _estado(st)["containers"], "o container que não ficou pronto também sai"


def test_docker_run_que_falha_aborta_com_rc12():
    _docker_falso(run_falha=True)
    rc, m, _ = _live("print('nunca')", extra={"workspace": {"banco": "exemplo"}})
    assert rc == 12 and "docker run falhou" in m["status"], m.get("status")


def test_sigterm_no_meio_do_voo_derruba_o_banco():
    if os.name != "posix":
        return
    st = _docker_falso()
    os.environ["E9_TESTE_PID"] = str(os.getpid())
    try:
        _live("import os, signal, time; os.kill(int(os.environ['E9_TESTE_PID']), signal.SIGTERM); time.sleep(5)",
              extra={"workspace": {"banco": "exemplo"}})
        raise AssertionError("o SIGTERM devia ter virado SystemExit")
    except SystemExit as e:
        assert e.code == 128 + _signal.SIGTERM, e.code
    finally:
        os.environ.pop("E9_TESTE_PID", None)
    e = _estado(st)
    assert e["removidos"] and not e["containers"], "o finally tem de derrubar o banco no sinal"
    assert _signal.getsignal(_signal.SIGTERM) is _signal.SIG_DFL or callable(_signal.getsignal(_signal.SIGTERM))


def test_op_sem_workspace_nao_toca_o_docker():
    st = _docker_falso()
    code = ("from pathlib import Path; Path('a.py').write_text('x = 1\\n')")
    rc, m, _ = _live(code)
    assert rc == 0 and "workspace" not in m
    assert not _estado(st).get("chamadas"), "op sem workspace voa como na v0.6"


def test_workspace_sem_receita_barrado():
    with blocks():
        e9.load_op(_mk_op({"workspace": {"banco": "nao-existe"}}))
    with blocks():
        e9.load_op(_mk_op({"workspace": {}}))


def test_receita_do_exemplo_carrega_e_nao_tem_senha():
    cfg, _ = e9.load_op(_mk_op({"workspace": {"banco": "exemplo"}}))
    rec = e9._workspace().receita(cfg, e9.TARGETS_DIR)
    assert rec["papel"] == "app_user" and rec["imagem"].startswith("pgvector/pgvector:")
    sql = "\n".join(ln for ln in Path(rec["_sql"]).read_text(encoding="utf-8").upper().splitlines()
                    if not ln.lstrip().startswith("--"))
    assert "PASSWORD" not in sql and "SUPERUSER" not in sql, "o papel nasce sem senha e sem superuser"
    assert all("{senha}" in v and "{porta}" in v for v in rec["env"].values())


def test_faxina_tira_o_de_harness_morto_e_poupa_o_vivo():
    if not Path("/proc").is_dir():
        return
    st = _docker_falso()
    e = _estado(st)
    e["containers"] = {"e9-vivo-1": {"e9.pid": str(os.getpid())}, "e9-morto-2": {"e9.pid": "4194000"}}
    st.write_text(json.dumps(e), encoding="utf-8")
    removidos = e9._workspace().faxina()
    assert removidos == ["e9-morto-2"], removidos
    assert list(_estado(st)["containers"]) == ["e9-vivo-1"]


def test_mesma_op_em_voo_da_rc11_sem_tocar_nada():
    code = "from pathlib import Path; Path('a.py').write_text('x = 1\\n')"
    _live(code)
    (_ULTIMA_OP / "metrics.json").unlink()
    trava = e9._workspace().travar_op(_ULTIMA_OP)
    assert trava is not None
    try:
        rc = _rerun(_ULTIMA_OP)
    finally:
        e9._workspace().soltar(trava)
    assert rc == 11, rc
    assert not (_ULTIMA_OP / "metrics.json").exists(), "o voo em curso não pode ter o metrics sobrescrito"


def test_slots_limitam_os_voos_simultaneos():
    w = e9._workspace()
    os.environ["E9_MAX_VOOS"] = "2"
    try:
        a, na = w.pegar_slot(teto_s=0)
        b, nb = w.pegar_slot(teto_s=0)
        c, nc = w.pegar_slot(teto_s=0.2, intervalo=0.05)
        assert a and b and {na, nb} == {0, 1} and c is None and nc == -1
        w.soltar(a)
        d, nd = w.pegar_slot(teto_s=0)
        assert d is not None and nd == na, "o slot solto volta a servir"
        w.soltar(b)
        w.soltar(d)
    finally:
        os.environ.pop("E9_MAX_VOOS", None)


def test_gate_que_pula_mais_que_a_base_e_vermelho():
    """Há suíte que PULA o teste de banco que não conecta: o gate sairia verde com metade da suíte sem rodar."""
    gate = _py("import os\nprint('2 passed, 1 skipped in 0.1s' if os.path.exists('a.py') else '3 passed in 0.1s')")
    rc, m, _ = _live("from pathlib import Path; Path('a.py').write_text('x = 1\\n')", gate=gate)
    assert rc == 3, m.get("status")
    assert m["pulados_a_mais"] == {"base": 0, "gate": 1} and "pulado" in m["status"]


def test_maquina_ocupada_pelo_prioritario_e_nao_pela_carga():
    w = e9._workspace()
    proc = Path(tempfile.mkdtemp())
    for pid, nome in (("10", "llama-server"), ("11", "python3")):
        (proc / pid).mkdir()
        (proc / pid / "comm").write_text(nome + "\n", encoding="utf-8")
    salvo, antes = os.environ.pop("E9_SEM_ESPERA"), w.PRIORITARIOS
    w.PRIORITARIOS = ("ffmpeg",)
    try:
        assert w.maquina_ocupada(proc) is None, "a CPU alta sozinha não segura voo"
        (proc / "12").mkdir()
        (proc / "12" / "comm").write_text("ffmpeg\n", encoding="utf-8")
        assert w.maquina_ocupada(proc) == "ffmpeg rodando"
    finally:
        os.environ["E9_SEM_ESPERA"], w.PRIORITARIOS = salvo, antes


if __name__ == "__main__":
    tests =[v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = fail = 0
    for t in tests:
        try:
            t(); print(f"  ok   ✅ {t.__name__}"); ok += 1
        except AssertionError as e:
            print(f"  FAIL ❌ {t.__name__} — {e}"); fail += 1
    print(f"\nRESULTADO: {ok} ok, {fail} falha(s)")
    sys.exit(1 if fail else 0)


# --- v0.8: a rastreabilidade — requisito × teste de aceite × voo ---------------------------

def _op_rastreio(opord, testes=None, aceite=True):
    op = _mk_op(ACEITE_CMD if aceite else {})
    (op / "spec.md").write_text(opord, encoding="utf-8")
    for rel, conteudo in (testes or {}).items():
        (op / "aceite" / rel).parent.mkdir(parents=True, exist_ok=True)
        (op / "aceite" / rel).write_text(conteudo, encoding="utf-8")
    return op


REQS = "## Requisitos\n- **FR-001** — grava com máscara.\n- **FR-002** — CPF não muda.\n## 4. X\n"


def test_aceite_sem_secao_de_requisitos_barrado():
    op = _op_rastreio("# spec\n", {"t/test_a.py": "def test_fr_001_x(): pass\n"})
    with blocks():
        e9.load_op(op)


def test_requisito_sem_teste_barrado():
    op = _op_rastreio(REQS, {"t/test_a.py": "def test_fr_001_x(): pass\n"})
    assert any("FR-002 sem teste" in f for f in e9.traceability_furos(op / "spec.md", op, True))
    with blocks():
        e9.load_op(op)


def test_teste_sem_requisito_ou_com_requisito_inventado_barrado():
    op = _op_rastreio(REQS, {"t/test_a.py": "def test_fr_001_x(): pass\ndef test_fr_002_y(): pass\n"
                                            "def test_solto(): pass\ndef test_fr_009_z(): pass\n"})
    furos = e9.traceability_furos(op / "spec.md", op, True)
    assert any("test_solto" in f for f in furos) and any("FR-009" in f for f in furos), furos


def test_requisito_declarado_sem_aceite_passa():
    op = _op_rastreio("## Requisitos\n- FR-001 — grava.\n- FR-002 — nenhuma migration (sem aceite: "
                      "o gate prova).\n", {"t/test_a.py": "def test_fr_001_x(): pass\n"})
    cfg, _ = e9.load_op(op)
    assert cfg["acceptance"]


def test_requisito_em_varias_linhas_e_citacao_de_outro_fr():
    """Visto num run real: o "sem aceite" quebrou de linha; e citar outro FR no texto não cria requisito."""
    op = _op_rastreio("## Requisitos\n- **FR-001** — grava, como o FR-002\n  manda.\n"
                      "- **FR-002** — a remoção dos arquivos (sem aceite: a remoção\n"
                      "  se confere no diff).\n### 3. EXECUTION\n- FR-009 aqui não conta\n",
                      {"t/test_a.py": "def test_fr_001_x(): pass\n"})
    reqs = e9.requisitos(op / "spec.md")
    assert list(reqs) == ["FR-001", "FR-002"] and reqs["FR-002"]["sem_aceite"], reqs
    assert reqs["FR-001"]["texto"] == "FR-001 — grava, como o FR-002 manda."
    assert e9.traceability_furos(op / "spec.md", op, True) == []


def test_op_sem_aceite_e_sem_requisitos_carrega_como_antes():
    e9.load_op(_op_rastreio("x", aceite=False))


def test_requisito_sem_aceite_numa_op_sem_aceite_barrado():
    op = _op_rastreio("## Requisitos\n- FR-001 — grava.\n", aceite=False)
    with blocks():
        e9.load_op(op)


def test_um_teste_prova_dois_requisitos():
    op = _op_rastreio(REQS, {"t/test_a.py": "async def test_fr_001_fr_002_juntos(): pass\n"})
    assert e9.traceability_furos(op / "spec.md", op, True) == []


def test_testes_que_falharam_le_pytest_e_unittest():
    saida = ("FAILED tests/a.py::test_fr_001_x[10184] - assert\nERROR tests/b.py::Classe::test_fr_002_y\n"
             "FAIL: test_fr_003_z (pkg.mod.Classe.test_fr_003_z)\n2 failed in 0.1s\n")
    assert e9.testes_que_falharam(saida) == {"test_fr_001_x", "test_fr_002_y", "test_fr_003_z"}


def test_matriz_marca_guarda_de_regressao_e_entrega():
    op = _op_rastreio(REQS, {"t/test_a.py": "def test_fr_001_x(): pass\ndef test_fr_002_y(): pass\n"})
    m = e9.matriz_de_traceability(op / "spec.md", op, "FAILED t/test_a.py::test_fr_001_x\n1 failed",
                              "2 passed", 0)
    assert [(r["fr"], r["base"], r["entrega"]) for r in m] == [
        ("FR-001", "falha", "passa"), ("FR-002", "já passa", "passa")]
    m = e9.matriz_de_traceability(op / "spec.md", op, "", "FAILED t/test_a.py::test_fr_002_y\n1 failed", 1)
    assert [r["entrega"] for r in m] == ["passa", "falha"]


def test_voo_com_aceite_grava_a_matriz_no_aar():
    rc, m, _ = _live(AGENTE_CUMPRE, extra=ACEITE_CMD, aceite=_aceite())
    assert rc == 0, m
    assert m["traceability"] == [{"fr": "FR-001", "texto": "FR-001 — o a.py existe.",
                              "testes": ["test_fr_001_a_existe"], "sem_aceite": False,
                              "base": "falha", "entrega": "passa"}], m["traceability"]
    aar = (_ULTIMA_OP / "report.md").read_text(encoding="utf-8")
    assert "## Traceability" in aar and "`test_fr_001_a_existe` | falha | passa" in aar


def test_commit_aceita_arquivo_que_o_agente_apagou_com_git_rm():
    """Visto num run real: `git rm` deixa a remoção no índice, e o `git add` do caminho apagado falhava."""
    wt = Path(tempfile.mkdtemp())
    e9.run(["git", "init", "-q", "-b", "main", str(wt)])
    for nome in ("velho.py", "vai_sumir.py"):
        (wt / nome).write_text("x = 1\n", encoding="utf-8")
    e9.run(["git", "-C", str(wt), "add", "velho.py", "vai_sumir.py"])
    e9.run(["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"])
    e9.run(["git", "-C", str(wt), "rm", "-q", "velho.py"])          # no índice
    (wt / "vai_sumir.py").unlink()                                   # só no disco
    (wt / "a.py").write_text("y = 2\n", encoding="utf-8")
    cfg = {"worktree": str(wt), "expected_artifacts": ["a.py", "velho.py"],
           "optional_artifacts": ["vai_sumir.py"]}
    ok, out = e9.stage_and_commit(cfg, "t")
    assert ok, out
    _, nomes = e9.run(["git", "-C", str(wt), "show", "--name-status", "--format=", "HEAD"])
    assert "D\tvelho.py" in nomes and "D\tvai_sumir.py" in nomes and "A\ta.py" in nomes, nomes
