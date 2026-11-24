#!/usr/bin/env python3
"""test_intent.py — v0.16: do intent.md ao rascunho. O rascunho só escreve na pasta da op, nunca no
target; passa pelo dry run do harness, e o agent recebe o erro para corrigir; nasce com "draft": true, e
o --live o recusa até o owner aprovar. O "claude" é falso; nenhum caso gasta crédito."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_e9_run as t  # noqa: E402
import intent  # noqa: E402

e9 = t.e9
INTENT = """# Intent

**Target:** `teste`

## Problema

O a.py não existe.

## Resultado esperado

O a.py existe e define x.

## Restrições

Nada além do a.py.

## Perguntas em aberto

x vale 1 ou 2?
"""

SPEC_BOA = ("# Spec\n\n## Perguntas ao owner\n- x vale 1 ou 2? SUPOSIÇÃO: 1\n\n## Requirements\n"
            "- **FR-001** — o a.py existe.\n\n## Guardrails\n- [XX-01] aplica: só o a.py.\n")
TESTE = "def test_fr_001_a_existe():\n    from pathlib import Path\n    assert Path('a.py').exists()\n"

# O "claude" falso: lê o comportamento pedido pelo teste e o número da chamada.
FALSO = r'''
import json, sys, os
from pathlib import Path
aqui = Path(__file__).parent
cfg = json.loads((aqui / "comportamento.json").read_text(encoding="utf-8"))
n = len(list(aqui.glob("chamada-*"))) + 1
(aqui / f"chamada-{n}").write_text(" ".join(sys.argv[1:]), encoding="utf-8")
passo = cfg["passos"][min(n, len(cfg["passos"])) - 1]
for rel, conteudo in passo.get("escreve", {}).items():
    p = Path(rel) if not rel.startswith("REPO/") else Path(cfg["repo"]) / rel[5:]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(conteudo, encoding="utf-8")
print(json.dumps({"type": "result", "is_error": False, "session_id": "abcdef12-0000", "total_cost_usd": 0.1,
                  "result": "resumo " + str(n)}))
'''


def _cenario(passos, gate="true"):
    base = Path(tempfile.mkdtemp())
    repo = base / "repo"
    e9.run(["git", "init", "-q", "-b", "main", str(repo)])
    e9.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"])
    ops, targets = base / "ops", base / "targets"
    ops.mkdir()
    targets.mkdir()
    (targets / "teste.md").write_text("- **[XX-01]** só o a.py.\n", encoding="utf-8")
    molde = ops / "2026-11-01-anterior"
    molde.mkdir()
    (molde / "op.json").write_text(json.dumps({"op": "anterior", "target": "teste", "target_repo": str(repo),
                                               "branch": "feat/x", "worktree": str(base / "wt"), "gate": {"test_cmd": gate},
                                               "agent": {"cmd_template": "echo"}, "expected_artifacts": ["a.py"]}),
                                   encoding="utf-8")
    op = ops / "2026-11-24-nova"
    op.mkdir()
    (op / "intent.md").write_text(INTENT, encoding="utf-8")
    fake = base / "claude_falso.py"
    fake.write_text(FALSO, encoding="utf-8")
    (base / "comportamento.json").write_text(json.dumps({"repo": str(repo), "passos": passos}), encoding="utf-8")
    intent.OPS, intent.TARGETS = ops, targets
    global _TARGETS
    _TARGETS = targets
    cmd = f'"{sys.executable}" "{fake}" claude -p {{brief}} --model {{modelo}}'
    return base, op, repo, cmd


def _op_json(acc=True, **extra):
    c = {"op": "nova", "target": "teste", "target_repo": "/tmp/repo", "branch": "feat/nova",
         "worktree": "/tmp/wt-nova", "gate": {"test_cmd": "true"}, "agent": {"cmd_template": "echo"},
         "expected_artifacts": ["a.py"], **extra}
    if acc:
        c["acceptance"] = {"cmd": "python3 -m pytest tests/test_aceite_a.py"}
    return json.dumps(c)


_TARGETS = None


def _dry(op):
    # o dry run do intent.py chama o e9_run.py em subprocesso, que lê os guardrails de targets/ do repo
    # do E-9; nos testes, quem valida é o load_op do módulo carregado, com o targets/ do cenário
    try:
        e9.load_op(op, _TARGETS)
        return True, "ok"
    except SystemExit as e:
        return False, str(e)


def _com_dry(fn):
    orig = intent.dry_run
    intent.dry_run = _dry
    try:
        return fn()
    finally:
        intent.dry_run = orig


def test_ler_intent_exige_target_problema_e_resultado():
    d = Path(tempfile.mkdtemp())
    (d / "intent.md").write_text(INTENT, encoding="utf-8")
    i = intent.ler_intent(d / "intent.md")
    assert i["target"] == "teste" and i["secoes"]["Perguntas em aberto"] == "x vale 1 ou 2?"
    (d / "intent.md").write_text(INTENT.replace("O a.py não existe.", ""), encoding="utf-8")
    try:
        intent.ler_intent(d / "intent.md")
        raise AssertionError("Problema vazio devia ser recusado")
    except ValueError as e:
        assert "Problema" in str(e)
    (d / "intent.md").write_text(INTENT.replace("**Target:** `teste`", "**Target:** `<nome>`"), encoding="utf-8")
    try:
        intent.ler_intent(d / "intent.md")
        raise AssertionError("Target do template devia ser recusado")
    except ValueError:
        pass


def test_rascunho_bom_passa_no_dry_run_e_nasce_draft():
    base, op, repo, cmd = _cenario([{"escreve": {"spec.md": SPEC_BOA, "op.json": _op_json(),
                                                 "acceptance/tests/test_aceite_a.py": TESTE}}])
    r = _com_dry(lambda: intent.rascunhar(op, cmd=cmd, tentativas=2))
    assert r["veredito"].startswith("RASCUNHO PRONTO"), r
    assert json.loads((op / "op.json").read_text(encoding="utf-8"))["draft"] is True, "o draft vem do harness"
    assert r["molde"] == "2026-11-01-anterior" and len(r["tentativas"]) == 1 and r["custo"] == 0.1
    brief = (op / "_rascunho_brief.txt").read_text(encoding="utf-8")
    assert "x vale 1 ou 2?" in brief and "[XX-01]" in brief and "MOLDE DO op.json" in brief


def test_dry_run_que_recusa_volta_ao_agent_na_mesma_sessao():
    sem_teste = SPEC_BOA.replace("- **FR-001** — o a.py existe.", "- **FR-001** — o a.py existe.\n- **FR-002** — b.")
    base, op, repo, cmd = _cenario([
        {"escreve": {"spec.md": sem_teste, "op.json": _op_json(), "acceptance/tests/test_aceite_a.py": TESTE}},
        {"escreve": {"spec.md": SPEC_BOA}}])
    r = _com_dry(lambda: intent.rascunhar(op, cmd=cmd, tentativas=2))
    assert r["veredito"].startswith("RASCUNHO PRONTO"), r
    assert "FR-002" in r["tentativas"][0]["dry_run"]
    chamada2 = (base / "chamada-2").read_text(encoding="utf-8")
    assert "--resume abcdef12-0000" in chamada2, "a correção retoma a sessão do rascunho"
    assert "CORREÇÃO DO RASCUNHO" in (op / "_rascunho_brief.txt").read_text(encoding="utf-8")


def test_rascunho_que_mexe_no_target_e_recusado():
    base, op, repo, cmd = _cenario([{"escreve": {"spec.md": SPEC_BOA, "op.json": _op_json(), "REPO/a.py": "x = 1\n"}}])
    r = _com_dry(lambda: intent.rascunhar(op, cmd=cmd))
    assert r["veredito"].startswith("RECUSADO") and "target" in r["veredito"], r


def test_rascunho_que_escreve_fora_da_pasta_permitida_e_recusado():
    base, op, repo, cmd = _cenario([{"escreve": {"spec.md": SPEC_BOA, "op.json": _op_json(), "notas/x.md": "y"}}])
    r = _com_dry(lambda: intent.rascunhar(op, cmd=cmd))
    assert r["veredito"].startswith("RECUSADO") and "notas/x.md" in r["veredito"], r


def test_pasta_que_ja_tem_spec_nao_e_sobrescrita():
    base, op, repo, cmd = _cenario([{"escreve": {}}])
    (op / "spec.md").write_text("à mão", encoding="utf-8")
    try:
        intent.rascunhar(op, cmd=cmd)
        raise AssertionError("devia recusar")
    except ValueError:
        assert (op / "spec.md").read_text(encoding="utf-8") == "à mão"


def test_live_recusa_rascunho_e_aprovar_libera_com_os_minutos():
    op = t._mk_op({"draft": True})
    argv = sys.argv
    sys.argv = ["e9_run.py", "--op", str(op), "--live"]
    try:
        with t.blocks():
            e9.main()
    finally:
        sys.argv = argv
    sys.argv = ["e9_run.py", "--op", str(op), "--dry-run"]
    try:
        assert e9.main() == 0, "o dry run de um rascunho valida"
    finally:
        sys.argv = argv
    orig = intent.dry_run
    intent.dry_run = lambda d: (True, "ok")
    try:
        ok, msg = intent.aprovar(op, "18")
    finally:
        intent.dry_run = orig
    cfg = json.loads((op / "op.json").read_text(encoding="utf-8"))
    assert ok and "draft" not in cfg and cfg["owner"]["spec_min"] == 18.0, (msg, cfg)
    assert intent.aprovar(op)[0] is False, "aprovar duas vezes não faz nada"


if __name__ == "__main__":
    falhas = 0
    for nome, f in sorted(globals().items()):
        if nome.startswith("test_") and callable(f):
            try:
                f(); print(f"  ok   {nome}")
            except AssertionError as e:
                falhas += 1; print(f"  FAIL {nome} — {e}")
    sys.exit(1 if falhas else 0)
