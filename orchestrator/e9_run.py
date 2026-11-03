#!/usr/bin/env python3
"""
e9_run.py — o harness do E-9 kit: executa UMA op (um run de um agente de código), v0.9.

Roda o Claude Code sem ninguém olhando (`claude -p`) numa worktree isolada do seu repositório e decide
de fora se o resultado presta. O ciclo:

  dry run → worktree (+ banco do run) → baseline → acceptance tests (têm de falhar) → agent
          → quality gate → acceptance tests (têm de passar, intactos) → AI code review (opcional)
          → commit na feat/* (SEM push) → run report

O que cada versão acrescentou, com o defeito real que a motivou, está no README.

Policy imposta em código:
  - Só branch feat/* no target; NUNCA push, NUNCA merge, NUNCA produção (`prod_markers` no op.json).
  - git add SELETIVO (expected_artifacts do op.json); NUNCA `git add -A`.
  - O quality gate decide: vermelho = abort, sem commit.
  - --dry-run valida e planeja, NÃO muta nada.

Uso:
  python3 orchestrator/e9_run.py --op ops/exemplo-slug --dry-run
  python3 orchestrator/e9_run.py --op ops/exemplo-slug --live     # fora da produção, sempre

Stdlib apenas. O ponto que toca o modelo é um seam claro: o comando vem do op.json
(`agent.cmd_template`).
"""
import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compat  # noqa: E402 — os nomes antigos das ops de antes da v0.9

# Console em UTF-8 (Windows usa cp1252 por default e engasga em ─ · ←).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# --- policy codificada no harness (guardrails que NÃO dependem de disciplina humana) ---
FORBIDDEN_GIT_SUBCMDS = {"push", "merge", "rebase", "reset"}      # policy §2, §10
FORBIDDEN_ADD_FLAGS = {"-A", "--all", "."}                       # policy §9 (add seletivo)
# policy §3 — o que conta como produção é de cada um: o op.json declara em `prod_markers`
# (trechos que, se aparecerem no target_repo, barram a op). Estes valem sempre, por cima.
PROD_MARKERS: tuple[str, ...] = ()
TARGETS_DIR = Path(__file__).resolve().parent.parent / "targets"     # v0.4 — travas por repo-alvo
_TRAVA_ID = re.compile(r"\[([A-Z][A-Z0-9]*-\d+)\]")


def log(tag, msg):
    print(f"[E-9 {tag}] {msg}")


def sitrep(lines):
    bar = "─" * 58
    print(bar)
    for ln in lines:
        print(ln)
    print(bar)


def load_op(op_dir: Path, targets_dir: Path | None = None):
    """Lê op.json + confirma que a spec existe (`spec.md`, ou o `OPORD.md` de op antiga).
    Retorna (cfg, spec_path), com as chaves do op.json já nos nomes novos (`compat.cfg`)."""
    cfg_path = op_dir / "op.json"
    opord = compat.spec_path(op_dir)
    if not cfg_path.exists():
        sys.exit(f"ABORT: op.json ausente em {op_dir}")
    if not opord.exists():
        sys.exit(f"ABORT: spec.md ausente em {op_dir}")
    cfg = compat.cfg(json.loads(cfg_path.read_text(encoding="utf-8")))

    # --- Validações da policy no contrato (falha cedo, antes de qualquer mutação) ---
    branch = cfg.get("branch", "")
    if not branch.startswith("feat/"):                                   # policy §2
        sys.exit(f"ABORT (policy §2): branch '{branch}' inválida — todo trabalho em `feat/*`.")
    target = cfg.get("target_repo", "")
    for m in (*PROD_MARKERS, *cfg.get("prod_markers", [])):             # policy §3
        if m in target:
            sys.exit(f"ABORT (policy §3): target_repo '{target}' bate marcador de prod ('{m}'). Prod é off-limits.")
    if not cfg.get("expected_artifacts"):                               # policy §9
        sys.exit("ABORT (policy §9): expected_artifacts vazio — sem ele não há `git add` seletivo possível.")
    subject = (cfg.get("commit") or {}).get("subject")
    if subject is not None:                                             # prefixo semântico
        prefix = subject.split(":", 1)[0].split("(", 1)[0].strip()
        if ":" not in subject or prefix not in COMMIT_PREFIXES:
            sys.exit(f"ABORT (commit): commit.subject '{subject}' sem prefixo semântico {COMMIT_PREFIXES}.")
    # Defense-in-depth: nenhum comando configurado (agente/gate) pode embutir push/merge.
    blob = json.dumps(cfg)
    for danger in ("git push", "git merge", "git reset", "git rebase"):  # policy §2/§10
        if danger in blob:
            sys.exit(f"ABORT (policy §2/§10): op.json contém '{danger}' — proibido no harness.")
    if cfg.get("acceptance") is not None:                                   # v0.4
        if not (cfg["acceptance"] or {}).get("cmd"):
            sys.exit("ABORT (aceite): `aceite.cmd` ausente — sem comando não há o que provar.")
        if not acceptance_files(op_dir):
            sys.exit(f"ABORT (acceptance): nenhum arquivo em {compat.acceptance_dir(op_dir)} — o aceite é a pasta.")
    furos = traceability_furos(opord, op_dir, cfg.get("acceptance") is not None)   # v0.8
    if furos:
        sys.exit("ABORT (traceability): " + "; ".join(furos) + ".")
    if cfg.get("workspace") is not None:                                # v0.7
        try:
            if _workspace().receita(cfg, targets_dir or TARGETS_DIR) is None:
                sys.exit("ABORT (workspace): `workspace.banco` vazio — diga a receita ou tire a chave.")
        except ValueError as e:
            sys.exit(f"ABORT (workspace): {e}")
    if cfg.get("target"):                                                 # v0.4
        faltam = guardrails_nao_citados(opord, target_path(cfg, targets_dir))
        if faltam:
            sys.exit(f"ABORT (guardrails): a spec não cita {faltam} na seção "
                     "`## Guardrails` — cada trava leva \"aplica: como\" ou \"não se aplica: porquê\".")
    return cfg, opord


def acceptance_files(op_dir: Path) -> list[tuple[str, Path]]:
    """Os testes de aceite da op (v0.4): tudo sob `ops/<op>/acceptance/` (`aceite/` nas ops antigas), com o caminho relativo
    que terão na worktree. Cache de Python não é aceite."""
    raiz = compat.acceptance_dir(op_dir)
    if not raiz.is_dir():
        return []
    return sorted((p.relative_to(raiz).as_posix(), p) for p in raiz.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")


def target_path(cfg, targets_dir: Path | None = None) -> Path:
    path = Path(targets_dir or TARGETS_DIR) / f"{cfg['target']}.md"
    if not path.exists():
        sys.exit(f"ABORT (guardrails): {path} não existe — alvo declarado sem as travas dele.")
    return path


def guardrails_nao_citados(opord: Path, alvo: Path) -> list[str]:
    """IDs de guardrail do target que a seção `## Guardrails` da spec (`## Travas do alvo` nas antigas) não cita (v0.4). Numa tarefa
    real a ordem proibia justo o que dois guardrails do repo exigiam, e ninguém notou ao escrever: a
    trava não é do agente, é de quem redige a spec."""
    ids = list(dict.fromkeys(_TRAVA_ID.findall(alvo.read_text(encoding="utf-8"))))
    texto = opord.read_text(encoding="utf-8")
    m = re.search(r"^#{2,4}\s*" + compat.SECAO_GUARDRAILS + r"\s*$(.*?)(?=^#{1,4}\s|\Z)", texto,
                  re.IGNORECASE | re.MULTILINE | re.DOTALL)
    secao = m.group(1) if m else ""
    return [i for i in ids if not re.search(rf"\b{re.escape(i)}\b", secao)]


# --- v0.8: a rastreabilidade — cada requisito com o teste de aceite que o prova --------------
# O formato do ID é o do Spec Kit (FR-001), para a spec dele poder virar spec sem renomear nada.
_FR_ID = re.compile(r"\bFR-(\d{3})\b")
_FR_NO_TESTE = re.compile(r"fr_(\d{3})", re.IGNORECASE)
_DEF_TESTE = re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)", re.MULTILINE)


def requisitos(opord: Path) -> dict[str, dict]:
    """Os requisitos da seção `## Requirements` da spec (`## Requisitos` nas antigas), na ordem: {"FR-001": {"texto", "sem_aceite"}}.
    A linha que diz "sem aceite: porquê" declara o requisito que nenhum teste prova (o gate prova,
    ou só a leitura do diff) — dito, e não esquecido."""
    m = re.search(r"^#{2,4}\s*" + compat.SECAO_REQUIREMENTS + r"\s*$(.*?)(?=^#{1,4}\s|\Z)", opord.read_text(encoding="utf-8"),
                  re.IGNORECASE | re.MULTILINE | re.DOTALL)
    # Um requisito é um item da lista com as linhas de continuação dele: o "sem aceite" que quebra
    # de linha ainda é do item (achado no 1º --dry-run real da v0.8). O ID é o PRIMEIRO do item —
    # citar outro requisito no texto ("como o FR-001") não cria requisito novo.
    itens: list[str] = []
    for linha in (m.group(1) if m else "").splitlines():
        if re.match(r"[-*] ", linha):
            itens.append(linha)
        elif itens and linha.strip():
            itens[-1] += " " + linha.strip()
    reqs: dict[str, dict] = {}
    for item in itens:
        ids = _FR_ID.findall(item)
        if ids:
            reqs.setdefault(f"FR-{ids[0]}", {"texto": item.replace("**", "").strip(" -*\t"),
                                             "sem_aceite": "sem aceite" in item.lower()})
    return reqs


def acceptance_tests(op_dir: Path) -> dict[str, list[str]]:
    """{nome do teste: [FR que ele prova]} — lido do nome da função (`test_fr_001_...`), que é o
    que o runner imprime quando o teste falha."""
    testes: dict[str, list[str]] = {}
    for _, src in acceptance_files(op_dir):
        if src.suffix == ".py":
            for nome in _DEF_TESTE.findall(src.read_text(encoding="utf-8")):
                testes[nome] = [f"FR-{n}" for n in dict.fromkeys(_FR_NO_TESTE.findall(nome))]
    return testes


def traceability_furos(opord: Path, op_dir: Path, com_aceite: bool) -> list[str]:
    """O que falta para cada requisito ter a sua prova. Op com aceite e sem requisitos é furo: o
    teste existe, e ninguém sabe o que ele prova."""
    reqs = requisitos(opord)
    testes = acceptance_tests(op_dir) if com_aceite else {}
    if com_aceite and not reqs:
        return ["a op tem aceite e a spec não tem a seção `## Requirements` com os FR-NNN"]
    furos = [f"o teste `{t}` não diz qual requisito prova (`test_fr_NNN_...`)"
             for t, frs in testes.items() if not frs]
    furos += [f"o teste `{t}` cita {fr}, que não está nos requisitos"
              for t, frs in testes.items() for fr in frs if fr not in reqs]
    provados = {fr for frs in testes.values() for fr in frs}
    furos += [f"{fr} sem teste de aceite — escreva `test_{fr.lower().replace('-', '_')}_...` ou "
              "diga \"sem aceite: porquê\" na linha dele"
              for fr, r in reqs.items() if fr not in provados and not r["sem_aceite"]]
    return furos


def testes_que_falharam(texto: str) -> set[str]:
    """Os testes que o runner diz que falharam: o resumo curto do pytest (`FAILED a.py::test_x[1]`,
    `ERROR a.py::Classe::test_x`) ou o do unittest (`FAIL: test_x (mod.Classe.test_x)`)."""
    nomes = re.findall(r"^(?:FAILED|ERROR) \S*::(\w+)", texto, re.MULTILINE)
    nomes += re.findall(r"^(?:FAIL|ERROR): (test\w*)", texto, re.MULTILINE)
    return set(nomes)


def matriz_de_traceability(opord: Path, op_dir: Path, base_out: str | None,
                       entrega_out: str | None, entrega_rc: int | None = None) -> list[dict]:
    """Uma linha por requisito: os testes dele e o que cada fase disse. Na base, "já passa" não
    aborta: é o requisito de não quebrar o que funciona (guarda de regressão), e fica dito."""
    testes = acceptance_tests(op_dir)
    caidos_base = testes_que_falharam(base_out or "")
    caidos_entrega = testes_que_falharam(entrega_out or "")
    linhas = []
    for fr, r in requisitos(opord).items():
        dele = [t for t, frs in testes.items() if fr in frs]
        linha = {"fr": fr, "texto": r["texto"], "testes": dele, "sem_aceite": r["sem_aceite"]}
        if dele and base_out is not None:
            linha["base"] = "falha" if any(t in caidos_base for t in dele) else "já passa"
        if dele and entrega_out is not None:
            linha["entrega"] = ("falha" if any(t in caidos_entrega for t in dele)
                                or (entrega_rc not in (0, None) and not caidos_entrega) else "passa")
        linhas.append(linha)
    return linhas


def build_brief(opord: Path, roe: Path, cfg: dict | None = None, *,
                alvo: Path | None = None, aceite: list[str] | None = None) -> str:
    """Briefing do agente = preâmbulo de comando + spec + policy."""
    preamble = (
        "# BRIEFING DO AGENTE (E-9)\n\n"
        "Você é um AGENTE. Cumpra a intent da spec, dentro da policy.\n"
        "Trabalhe SÓ na worktree designada (`--add-dir`). Em dúvida sobre a intent: escalate (pare e pergunte).\n\n"
        "## Regra de escopo (outcome-based spec)\n"
        "- **FIXO, inviolável:** a intent, o end-state e a policy. Não negocie.\n"
        "- **DELEGÁVEL a você:** os MEIOS — estrutura, em qual app o arquivo mora, como você\n"
        "  implementa. Use seu julgamento de engenheiro sênior. Se desviar do que a spec\n"
        "  sugeriu (ex: outro app), está OK — mas **DECLARE o desvio e o porquê** na sua saída.\n"
        "- **PROIBIDO:** criar run report ou métricas (isso é do E-9, não seu); pular um artefato\n"
        "  declarado; trabalhar fora da worktree; push/merge; tocar prod; usar dado real.\n"
        "- O caminho exato do artefato é seu; o ARTEFATO em si não é opcional.\n\n"
        "## Disciplina de teste (v0.3 — o que três voos seguidos erraram)\n"
        "- **O banco de teste é UM só.** Nunca rode duas rodadas de teste ao mesmo tempo, nem\n"
        "  uma em segundo plano enquanto outra roda: elas disputam o banco e inventam falhas.\n"
        "- **Não escreva \"verde\", \"limpo\" nem um número de testes sem a saída que prove.**\n"
        "  Antes do relatório, rode o portão da spec INTEIRO uma vez, espere terminar, e copie\n"
        "  a última linha de cada etapa. Rodou só parte? Diga que foi só parte.\n"
        "- O E-9 roda o portão de novo depois de você e põe o resultado dele ao lado do seu\n"
        "  relatório no run report. Divergência entre os dois vira achado.\n"
        "- **NUNCA rode nada em segundo plano** (`run_in_background`, `&`, `nohup`). Você roda em\n"
        "  `claude -p`: quando você para de falar, a sessão acaba, e a notificação de \"terminou\"\n"
        "  nunca chega. Rode o portão em primeiro plano e ESPERE: o teto do Bash aqui é de 20 min.\n"
        "  O que sobrar rodando na worktree quando você terminar é morto pelo E-9 antes do gate.\n"
        "- **Não passe ao Bash um `timeout` menor que o tempo do portão** (v0.5.1). Comando que passa\n"
        "  do `timeout` pedido NÃO é morto: o Claude Code o joga para segundo plano sozinho, e você\n"
        "  fica esperando um aviso que não chega. Foi o que encerrou uma tarefa real: `timeout: 300000` (5\n"
        "  min) numa suíte de 6m20s. Sem `timeout`, vale o teto acima; com ele, use o que o E-9\n"
        "  mediu na seção TEMPO DO PORTÃO NESTE VOO, no fim deste briefing.\n\n"
        "## Desvios da spec (v0.3.3 — obrigatório no relatório final)\n"
        "- Feche o relatório com uma seção `## Desvios da spec`. Para CADA ponto em que o que\n"
        "  você fez não é literalmente o que a spec descreve, uma linha: **a spec dizia** → **eu\n"
        "  fiz** → **por quê**. Uma lista mudada, um caso pulado, um teste antigo tocado, uma\n"
        "  regra do desenho de referência interpretada: tudo entra. Sem desvio, escreva `nenhum`.\n"
        "- O portão verde não prova o desenho: em tarefas reais ele deu verde e o defeito estava\n"
        "  num desvio do desenho que ninguém declarou. É esta seção que o owner revisa primeiro.\n\n"
        + "=" * 60 + "\n# spec\n\n" + opord.read_text(encoding="utf-8"))
    if roe.exists():
        preamble += "\n\n" + "=" * 60 + "\n# POLICY (regras permanentes)\n\n" + roe.read_text(encoding="utf-8")
    if alvo is not None:
        preamble += ("\n\n" + "=" * 60 + "\n# GUARDRAILS (invariantes deste repositório)\n\n"
                     "Toda mudança neste repo respeita estes guardrails, mesmo os que a spec não "
                     "repete. Se uma ordem da spec contradiz um guardrail: escalate (pare e pergunte), e declare "
                     "nos desvios.\n\n" + alvo.read_text(encoding="utf-8"))
    if aceite:
        preamble += ("\n\n" + "=" * 60 + "\n# ACCEPTANCE TESTS (só leitura)\n\n"
                     "O E-9 já pôs estes arquivos na worktree, e eles falham hoje: são a prova de "
                     "que o pedido foi cumprido. Faça-os passar sem editá-los, sem apagá-los e sem "
                     "pulá-los (nada de skip, xfail ou conftest que os desligue): o E-9 confere o "
                     "sha256 de cada um, e mexer é abort mesmo com tudo verde. Achou um teste "
                     "errado? escalate (pare e pergunte), e declare nos desvios.\n\n"
                     # v0.5 — caminho completo, NA WORKTREE: num voo de prova o agente leu o
                     # caminho relativo contra o repo-alvo da spec, fora da pasta liberada, e foi barrado
                     + "\n".join(f"- `{_na_worktree(cfg, rel)}`" for rel in aceite) + "\n")
    liberados = comandos_liberados(cfg) if cfg else []
    if liberados:
        preamble += ("\n\n" + "=" * 60 + "\n# COMANDOS BASH LIBERADOS (exatamente nestas formas)\n\n"
                     "Fora destas formas o comando é RECUSADO em silêncio: sem TTY ninguém aprova. "
                     "Comece pelo início exato de uma linha abaixo; o resto (argumentos, `2>&1 | tail`) "
                     "é livre. Para ler arquivo, use Read/Grep/Glob, não `cat`/`grep`/`find`.\n\n"
                     + "\n".join(f"- `{c}…`" for c in liberados) + "\n")
    return preamble


def previous_runs(op_dir: Path) -> list[dict]:
    """O histórico de voos desta op, a partir do metrics.json que o voo novo vai substituir."""
    try:
        prev = compat.metrics(json.loads((Path(op_dir) / "metrics.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []
    if not prev.get("status"):
        return list(prev.get("previous_runs") or [])
    resumo = {"rc": prev.get("rc"), "status": (prev.get("status") or "")[:160],
              "finished_at": prev.get("finished_at"), "gate": prev.get("gate"),
              "custo": (prev.get("agent") or {}).get("total_cost_usd")}
    return list(prev.get("previous_runs") or []) + [resumo]


def timeout_do_portao_ms(cfg, base_s: float) -> int:
    """O `timeout` mínimo para o agente passar ao Bash do portão: o dobro do que a linha de base
    levou, nunca abaixo de 10 min e nunca acima do teto do `cmd_template` (BASH_MAX_TIMEOUT_MS)."""
    m = re.search(r"BASH_MAX_TIMEOUT_MS=(\d+)", (cfg.get("agent") or {}).get("cmd_template", ""))
    teto = int(m.group(1)) if m else 1_200_000
    return min(max(int(2 * base_s * 1000), 600_000), teto)


def anexar_tempo_do_portao(brief_file: Path, cfg, inicio_base: str) -> None:
    """v0.5.1 — depois da linha de base, o briefing ganha o tempo medido do portão neste voo.
    Numa tarefa real o agente pediu `timeout` de 5 min para uma suíte de 6m20s: o Bash foi para segundo
    plano sozinho, e o agente encerrou "aguardando". O harness sabe o tempo; agora o agente também."""
    try:
        ini = datetime.fromisoformat(inicio_base.replace("Z", "+00:00"))
        base_s = (datetime.now(timezone.utc) - ini).total_seconds()
        ms = timeout_do_portao_ms(cfg, base_s)
        with open(brief_file, "a", encoding="utf-8") as h:
            h.write("\n\n" + "=" * 60 + "\n# TEMPO DO PORTÃO NESTE VOO\n\n"
                    f"A linha de base (o gate no `main`, antes de você) levou {base_s / 60:.1f} min. "
                    f"Ao rodar o portão no Bash, passe `timeout: {ms}` (ou não passe nenhum). "
                    "Menos que isso e o comando vai para segundo plano sem você pedir.\n")
    except (OSError, ValueError, TypeError):
        pass


def _na_worktree(cfg, rel: str) -> str:
    wt = (cfg or {}).get("worktree")
    return f"{wt.rstrip('/')}/{rel}" if wt else rel


def comandos_liberados(cfg) -> list[str]:
    """Os prefixos de Bash que o `--allowedTools` do cmd_template libera, com os placeholders
    resolvidos (v0.3.2). Numa tarefa real o agente tentou `PYTHONPATH=src <venv>/bin/ruff`, que a
    lista não tinha, e teve TODO Bash recusado sem saber por quê: a lista existia só no op.json."""
    tpl = (cfg.get("agent") or {}).get("cmd_template", "")
    try:
        tpl = _fmt(tpl, cfg, brief_file="")
    except (KeyError, IndexError):
        pass
    return [m.rstrip(":*") for m in re.findall(r"Bash\(([^)]*)\)", tpl)]


RC_TIMEOUT = 124   # mesmo código do `timeout(1)` do coreutils
# Id completo, nunca o alias: numa versão antiga do Claude Code o alias `sonnet` resolvia para um
# modelo mais velho, e duas tarefas rodaram nele sem ninguém saber. O run report registra o que respondeu.
MODELO_AGENTE = "claude-sonnet-5-5"


def run(cmd, cwd=None, timeout=None, log_file=None, env=None):
    """Subprocess fino. Retorna (rc, stdout+stderr).

    v0.2 — o travamento das primeiras tarefas: o `claude -p` herdava o stdin do harness,
    que sem TTY nunca fecha, e o `subprocess.run(capture_output=True)` esperava para sempre.
    Agora: stdin=DEVNULL (EOF imediato), saída num ARQUIVO (pipe cheio não trava o filho),
    grupo de processos próprio e `timeout` que mata o grupo inteiro — o shell e o que ele
    lançou. Estourou: rc=124, e a saída parcial volta mesmo assim (vai pro run report)."""
    own_log = log_file is None
    if own_log:
        fd, tmp = tempfile.mkstemp(prefix="e9-run-", suffix=".log")
        os.close(fd)
        log_file = Path(tmp)
    log_file = Path(log_file)
    with open(log_file, "wb") as out:
        p = subprocess.Popen(cmd, cwd=cwd, shell=isinstance(cmd, str), env=env,
                             stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                             start_new_session=(os.name == "posix"))
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(p)
            rc = RC_TIMEOUT
    text = log_file.read_text(encoding="utf-8", errors="replace")
    if own_log:
        log_file.unlink(missing_ok=True)
    return rc, text


def matar_restos(pasta) -> list[int]:
    """Mata o que ainda roda DENTRO da worktree depois que o agente termina (v0.3.4).

    Numa tarefa real o agente jogou duas suítes para segundo plano e encerrou "aguardando
    notificações" — em `claude -p` não há depois, e as suítes órfãs seguiriam disputando o
    banco de teste com o gate. O Claude Code pode soltar esses filhos em sessão própria, fora
    do grupo do agente; por isso a varredura é pelo diretório de trabalho (/proc/<pid>/cwd),
    não pelo grupo. Só posix; devolve os pids mortos."""
    if os.name != "posix" or not Path("/proc").is_dir():
        return []
    alvo = str(Path(pasta).resolve())
    vivos = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit() or int(d.name) == os.getpid():
            continue
        try:
            cwd = os.readlink(d / "cwd")
        except OSError:
            continue
        if cwd == alvo or cwd.startswith(alvo + os.sep):
            vivos.append(int(d.name))
    for pid in vivos:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if vivos:
        time.sleep(3)
        for pid in vivos:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    return vivos


def _kill_tree(p):
    """Mata o processo e os filhos (o `claude` roda debaixo de um `sh -c`)."""
    try:
        if os.name == "posix":
            os.killpg(p.pid, signal.SIGTERM)
            try:
                p.wait(timeout=10)
                return
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
        else:
            p.kill()
    except ProcessLookupError:
        pass
    p.wait()


def git(args, cwd=None):
    """Portão único pras operações git DO HARNESS. Recusa o que a policy proíbe —
    cinto-e-suspensório por cima da garantia de design (nenhum push existe no código).
    Pula opções globais (`-c key=val`, `-C path`) pra achar o subcomando real."""
    i = 0
    while i + 1 < len(args) and args[i] in ("-c", "-C"):
        i += 2
    sub = args[i] if i < len(args) else ""
    if sub in FORBIDDEN_GIT_SUBCMDS:
        sys.exit(f"policy VIOLATION (§2/§10): `git {sub}` é proibido no harness.")
    if sub == "add":
        bad = FORBIDDEN_ADD_FLAGS.intersection(args[i + 1:])
        if bad:
            sys.exit(f"policy VIOLATION (§9): `git add {' '.join(bad)}` — add é seletivo, arquivo por arquivo.")
    return run(["git", *args], cwd=cwd)


def ambiente(cfg):
    """O ambiente dos comandos do voo (agente, base, gate, aceite). v0.7: com workspace, leva as
    URLs do banco do voo, que o alvo lê antes do `.env` — a aplicação precisa ler o ambiente
    primeiro. Sem workspace, None: o subprocesso herda o ambiente do harness, como até a v0.6."""
    extra = cfg.get("_ambiente_do_voo")
    return {**os.environ, **extra} if extra else None


def _fmt(template, cfg, **extra):
    """Substitui os placeholders padrão de comando (op.json é cabeamento por dado).
    {worktree} {target_repo} {venv} {model} + extras (ex: {brief_file})."""
    return template.format(
        worktree=cfg["worktree"], target_repo=cfg["target_repo"],
        venv=cfg.get("venv", cfg["target_repo"].rstrip("/") + "/.venv"),
        model=cfg.get("agent", {}).get("model", MODELO_AGENTE), **extra)


def setup_worktree(cfg):
    """git worktree add do repo-alvo na branch feat/*. Idempotente o suficiente.
    v0.1: liga o ambiente — symlink dos arquivos gitignored que o alvo precisa pra
    rodar (.env etc.). A worktree é só a árvore de código; sem isso o Django não sobe."""
    target, branch, wt = cfg["target_repo"], cfg["branch"], cfg["worktree"]
    log("WT", f"worktree {wt} <- {target}@{branch}")
    if Path(wt).exists():
        # v0.3 — re-voo sobre a mesma op: o `worktree add -B` recusa com "already used by
        # worktree", não "already exists", e a op abortava com uma mensagem que não dizia nada.
        # Reaproveita só se for a mesma branch; a checagem de worktree suja vem depois.
        rc, cur = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=wt)
        if rc != 0 or cur.strip() != branch:
            return False, f"worktree {wt} já existe e não está em {branch}: {cur.strip()[:200]}"
        rc, out = 0, f"worktree reaproveitada em {branch}"
        log("WT", out)
    else:
        rc, out = git(["worktree", "add", "-B", branch, wt], cwd=target)
        if rc != 0:
            return False, out
    for rel in cfg.get("worktree_link", []):
        if rel.startswith("/") or ".." in rel:                 # POLICY: só dentro do worktree
            return False, f"worktree_link inseguro (fora do worktree): {rel}"
        src, dst = Path(target) / rel, Path(wt) / rel
        if not src.exists():
            return False, f"worktree_link ausente no alvo: {src}"
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                dst.symlink_to(src)
                log("WT", f"link {rel} -> {src}")
            except OSError as e:
                return False, f"falha ao linkar {rel}: {e}"
    return True, out


AGENT_TIMEOUT_S = 90 * 60     # teto default do agente (op.json: agent.timeout_s)
GATE_TIMEOUT_S = 30 * 60


def dispatch_agent(cfg, brief_file: Path, log_file: Path):
    """SEAM do modelo: roda o agente. cmd_template vem do op.json.
    Placeholders: {brief_file} {worktree} {model}.
    v0.2: a saída inteira fica em `log_file` (é onde o agente declara desvio — policy §7) e o
    teto de tempo vem do op.json. Retorna (ok, saída, rc)."""
    cmd = _fmt(cfg["agent"]["cmd_template"], cfg, brief_file=str(brief_file))
    timeout = cfg["agent"].get("timeout_s", AGENT_TIMEOUT_S)
    log("OPR", f"dispatch (teto {timeout}s): {cmd}")
    rc, out = run(cmd, cwd=cfg["worktree"], timeout=timeout, log_file=log_file, env=ambiente(cfg))
    if rc == RC_TIMEOUT:
        log("OPR", f"TIMEOUT — agente morto após {timeout}s (saída parcial em {log_file.name})")
    return rc == 0, out, rc


def parse_agent_output(text: str) -> dict:
    """Lê a saída do `claude -p --output-format stream-json`: uma linha JSON por evento,
    a última `type=result` traz o texto final e o custo. Saída em texto puro (op antiga,
    sem stream-json) vira o próprio texto, sem métrica. Nunca levanta."""
    final = None
    modelos: set[str] = set()
    recusas: list[str] = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "result":
            final = ev
        # v0.8.1: a recusa de permissão vem como evento `system` com o `message` em TEXTO — foi o
        # que derrubou o harness num run real, depois de o agente ter trabalhado. Ela vai ao run report.
        if ev.get("subtype") == "permission_denied":
            recusas.append(f"{ev.get('tool_name')}: {str(ev.get('message', ''))[:160]}")
        # o modelo que de fato respondeu: no init e em cada mensagem do assistente
        if ev.get("type") not in ("system", "assistant"):
            continue
        msg = ev.get("message")
        mod = ev.get("model") or (msg.get("model") if isinstance(msg, dict) else None)
        if isinstance(mod, str) and mod:
            modelos.add(mod)
    if final is None:
        return {"stream_json": False, "result_text": text.strip()[-4000:]}
    usage = final.get("usage") or {}
    return {
        "stream_json": True,
        "result_text": (final.get("result") or "").strip(),
        "is_error": final.get("is_error"),
        "subtype": final.get("subtype"),
        "num_turns": final.get("num_turns"),
        "duration_ms": final.get("duration_ms"),
        "total_cost_usd": final.get("total_cost_usd"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "modelos": sorted(modelos),
        "recusas": recusas,
    }


def run_gate(cfg, log_file: Path | None = None, *, chave: str = "test_cmd"):
    """Gate: roda o teste no worktree. Vermelho = não avança. Timeout conta como vermelho.
    ``chave="baseline_cmd"`` roda o gate da linha de base, quando a op declara um próprio
    (v0.3.1): op que conserta o que o gate novo acusa — o mypy de uma versão nova de
    dependência, por exemplo — tem o `main` vermelho nele por definição."""
    ow = cfg["gate"]
    cmd = _fmt(ow.get(chave) or ow["test_cmd"], cfg)
    timeout = cfg["gate"].get("timeout_s", GATE_TIMEOUT_S)
    log("OWX", f"gate: {cmd}")
    rc, out = run(cmd, cwd=cfg["worktree"], timeout=timeout, log_file=log_file, env=ambiente(cfg))
    return rc == 0, out


# --- v0.4: o aceite — a pós-condição escrita antes do voo, por quem não implementa ---------
RC_ACCEPTANCE_BASE = 9       # o aceite não falha na base (já passa, ou nem roda como teste)
RC_ACCEPTANCE_ENTREGA = 10   # o aceite foi mexido, ou não passou inteiro depois do agente
_PULADOS = ("skipped", "xfailed", "xpassed", "deselected")


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def contagem(text: str) -> dict:
    """A contagem final do runner de testes: o resumo do pytest (`2 failed, 3 passed, 1 skipped`)
    ou, sem ele, o do unittest (`Ran 3 tests` + `FAILED (failures=1, errors=2)`). Módulo de teste
    que nem importa marca `erro_de_coleta` — no unittest ele aparece como um teste com erro."""
    c = {}
    for chave in ("passed", "failed", "error", "errors", *_PULADOS):
        achados = re.findall(rf"(\d+) {chave}\b", text)
        if achados:
            c[chave] = int(achados[-1])
    if "error" in c:
        c["errors"] = c.pop("error") + c.get("errors", 0)
    ran = re.findall(r"^Ran (\d+) tests? in", text, re.MULTILINE)
    if not c and ran:
        partes = dict(re.findall(r"(\w[\w ]*?)=(\d+)", (re.findall(
            r"^(?:FAILED|OK) \(([^)]*)\)", text, re.MULTILINE) or [""])[-1]))
        n = {k: int(partes.get(k, 0)) for k in
             ("failures", "errors", "skipped", "expected failures", "unexpected successes")}
        c = {"passed": int(ran[-1]) - sum(n.values()), "failed": n["failures"],
             "errors": n["errors"], "skipped": n["skipped"], "xfailed": n["expected failures"],
             "xpassed": n["unexpected successes"]}
        c = {k: v for k, v in c.items() if v}
    if "Failed to import test module" in text or re.search(r"^ERROR collecting", text, re.MULTILINE):
        c["erro_de_coleta"] = True
    return c


def instalar_acceptance(cfg, op_dir: Path) -> tuple[bool, str, dict]:
    """Copia o aceite para a worktree e guarda o sha256 de cada arquivo. O aceite só CRIA
    arquivo: sobrescrever um teste que o alvo já tem mudaria o que a linha de base provou."""
    wt, hashes = Path(cfg["worktree"]), {}
    for rel, src in acceptance_files(op_dir):
        if rel.startswith("/") or ".." in Path(rel).parts:
            return False, f"caminho de aceite inseguro: {rel}", {}
        dst, h = wt / rel, _sha256(src)
        if dst.exists() and _sha256(dst) != h:       # re-voo: o mesmo arquivo, já commitado, serve
            return False, f"{rel} já existe no alvo com outro conteúdo — o aceite só cria arquivo novo", {}
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        hashes[rel] = h
    return True, f"{len(hashes)} arquivo(s) de aceite na worktree", {"arquivos": hashes}


def rodar_acceptance(cfg, log_file: Path | None = None) -> tuple[int, dict, str]:
    cmd = _fmt(cfg["acceptance"]["cmd"], cfg)
    log("ACT", f"aceite: {cmd}")
    rc, out = run(cmd, cwd=cfg["worktree"],
                  timeout=cfg["gate"].get("timeout_s", GATE_TIMEOUT_S), log_file=log_file,
                  env=ambiente(cfg))
    return rc, contagem(out), out


def julgar_acceptance_base(cfg, rc: int, c: dict) -> str | None:
    """Antes do agente o aceite tem de FALHAR, e falhar como teste. Devolve o motivo do abort,
    ou None. rc 0 = já passa, não prova nada novo; rc 2/4/5 do pytest = erro de coleta, de uso
    ou nenhum teste — um ImportError não é a prova de nada (por isso o import vai DENTRO da função)."""
    if rc == 0:
        return "o aceite já PASSA antes do agente — não prova nada que a op vá fazer"
    esperado = (cfg.get("acceptance") or {}).get("base_rc", 1)
    if rc != esperado or c.get("erro_de_coleta") or not (c.get("failed", 0) + c.get("errors", 0)):
        return (f"o aceite não rodou como teste na base (rc {rc}, esperado {esperado} com falhas; "
                f"contagem {c}): erro de coleta ou de comando não é aceite")
    return None


def acceptance_mexido(cfg, hashes: dict) -> list[str]:
    """Arquivos de aceite que mudaram ou sumiram desde a base."""
    wt = Path(cfg["worktree"])
    return [rel for rel, h in hashes.items()
            if not (wt / rel).exists() or _sha256(wt / rel) != h]


def julgar_acceptance_entrega(rc: int, c: dict, coletados: int) -> str | None:
    """Depois do agente: tudo passa, a mesma quantidade da base, nada pulado. Um skip posto
    por fora (conftest, marcador global) passaria no rc e não na contagem."""
    pulados = {k: c[k] for k in _PULADOS if c.get(k)}
    if rc != 0 or c.get("failed") or c.get("errors"):
        return f"o aceite não passou depois do agente (rc {rc}, contagem {c})"
    if pulados:
        return f"o aceite passou com testes pulados ({pulados}) — pulado não é cumprido"
    if c.get("passed") != coletados:
        return f"o aceite rodou {c.get('passed')} teste(s), e a base coletou {coletados}"
    return None


COMMIT_PREFIXES = ("feat", "fix", "refactor", "chore", "docs", "test")   # prefixo semântico


def gate_summary(text: str) -> dict:
    """O que o gate de fato disse, lido da saída: ruff, mypy e pytest, quando aparecem.
    Só números e vereditos; nunca levanta."""
    s: dict = {}
    if "All checks passed!" in text:
        s["ruff"] = "limpo"
    elif m := re.search(r"Found (\d+) errors?\.", text):       # ruff termina em ponto;
        s["ruff"] = f"{m.group(1)} erro(s)"                       # o mypy, em "in N files"
    if m := re.search(r"Success: no issues found in (\d+) source file", text):
        s["mypy"] = f"limpo ({m.group(1)} arquivos)"
    elif m := re.search(r"Found (\d+) errors? in \d+ files?", text):
        s["mypy"] = f"{m.group(1)} erro(s)"
    for chave in ("passed", "failed", "errors"):
        achados = re.findall(rf"(\d+) {chave}\b", text)
        if achados:
            s[chave] = int(achados[-1])          # o resumo final do pytest é o último
    return s


_ALEGA_VERDE = re.compile(r"\b(verde|limpo|limpa|clean|green)\b", re.IGNORECASE)
# v0.3.2 — numa tarefa real o agente escreveu "não reporto 'verde' sem prova", honesto, e o detector
# acusou divergência. A alegação negada (não, sem, nunca, nem, not, no) nos ~30 caracteres antes
# da palavra não conta.
_NEGACAO = re.compile(r"\b(n[ãa]o|sem|nunca|nem|not|no)\b", re.IGNORECASE)


def _alega_verde(texto: str) -> bool:
    for m in _ALEGA_VERDE.finditer(texto):
        antes = re.split(r"[.!?\n]", texto[max(0, m.start() - 30):m.start()])[-1]   # só a frase
        if not _NEGACAO.search(antes):
            return True
    return False


def divergencias(gate_ok: bool, gate: dict, relatorio: str) -> list[str]:
    """Onde o relatório do agente contradiz o gate do E-9 (v0.3). Em três tarefas seguidas o
    relatório disse "Gate verde" com cinco falhas, "ruff LIMPO" com o ruff vermelho e
    "418 passed" com o gate em 814. O gate é a prova; isto marca o que não bate."""
    achados = []
    if not gate_ok and _alega_verde(relatorio or ""):
        achados.append("o relatório alega verde/limpo, e o gate do E-9 deu VERMELHO")
    declarados = {int(n) for n in re.findall(r"(\d+) passed", relatorio or "")}
    if declarados and gate.get("passed") is not None and gate["passed"] not in declarados:
        achados.append(f"o relatório declara {sorted(declarados)} passed, e o gate deu "
                       f"{gate['passed']}")
    if relatorio and not re.search(r"^#+\s*" + compat.SECAO_DESVIOS, relatorio, re.IGNORECASE | re.MULTILINE):
        achados.append("o relatório não tem a seção `## Desvios da spec` que o briefing exige")
    return achados


def commit_message(cfg, op_id, artifacts):
    """v0.2 — a mensagem vem do op.json (`commit.subject`, com prefixo semântico);
    até a v0.1.2 saía `feat(seed): ...` fixo para qualquer op. Sem `commit`, cai num
    `chore(e9): op <id>` neutro — nunca num prefixo que afirma o que a op não é."""
    c = cfg.get("commit") or {}
    subject = (c.get("subject") or f"chore(e9): op {op_id}").strip()
    body = (c.get("body") or "").strip()
    trailer = (f"Op {op_id} — agente E-9, gate verde.\n"
               f"Artefatos: {', '.join(artifacts)}")
    return subject + "\n\n" + (body + "\n\n" if body else "") + trailer


def stage_and_commit(cfg, op_id, aceite=()):
    """git add SELETIVO (expected_artifacts) + commit. NUNCA -A, NUNCA push.
    v0.4: os arquivos de aceite entram também — fazem parte da árvore testada, e o contrato
    fica no alvo como teste de regressão."""
    wt = cfg["worktree"]
    arts = cfg.get("expected_artifacts", [])
    opt = cfg.get("optional_artifacts", [])
    if not arts:
        return False, "sem expected_artifacts — nada a commitar seletivamente"
    # Gate de entrega. v0.1: expected_artifacts são PADRÕES (globs) — mission command
    # delega o MEIO (qual app hospeda o arquivo) ao agente; o gate exige que cada
    # padrão OBRIGATÓRIO case ≥1 arquivo. -uall lista ARQUIVOS (não dirs untracked).
    rc, status = git(["status", "--porcelain", "-uall"], cwd=wt)
    if rc != 0:
        return False, f"git status falhou: {status}"
    changed = [ln[3:].strip() for ln in status.splitlines() if ln.strip()]

    def match(pat):
        return [c for c in changed if fnmatch.fnmatch(c, pat) or pat in c]

    resolved, missing = [], []
    for pat in arts:                                   # obrigatórios: gate exige match
        hits = match(pat)
        resolved.extend(hits) if hits else missing.append(pat)
    if missing:
        return False, f"padrão(ões) OBRIGATÓRIO(s) sem match na worktree: {missing}"
    for pat in opt:                                    # opcionais (meio do agente): só se vierem
        resolved.extend(match(pat))
    resolved.extend(rel for rel in aceite if rel in changed)   # já commitado num re-voo: não muda
    resolved = sorted(set(resolved))
    # v0.2 — o gate testou a árvore INTEIRA da worktree; se o agente mexeu num
    # arquivo que nenhum padrão cobre, o commit levaria outra árvore, não a testada, e a
    # branch "verde" poderia nem compilar. Sobra = abort declarado, nunca commit parcial.
    leftover = sorted(set(changed) - set(resolved) - set(cfg.get("worktree_link", [])))
    if leftover:
        return False, ("arquivo(s) alterado(s) fora dos expected/optional_artifacts — a árvore "
                       f"testada não seria a commitada: {leftover}")
    # v0.8.3: o que o agente já apagou com `git rm` está no índice e sumiu do disco, e o
    # `git add -- <caminho>` dele falha ("pathspec did not match") — achado num run real, com o gate
    # verde. Esses já estão como devem ir; o `add` é só para o resto.
    ja_no_indice = {ln[3:].strip() for ln in status.splitlines() if ln[:2] == "D "}
    a_adicionar = [r for r in resolved if r not in ja_no_indice]
    if a_adicionar:
        rc, out = git(["add", "--", *a_adicionar], cwd=wt)  # add SELETIVO dos resolvidos
        if rc != 0:
            return False, out
    msg = commit_message(cfg, op_id, resolved)
    # Identidade própria do E-9, inline (não depende de git config global da máquina).
    ident = cfg.get("commit_identity", {})
    name = ident.get("name", "E-9")
    email = ident.get("email", "e9@localhost")
    rc, out = git(["-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-m", msg], cwd=wt)
    # NOTA: nenhum `git push` aqui, por desenho (policy) + guard no git().
    return (rc == 0), (out if rc != 0 else f"commitado por {name}: {', '.join(resolved)}")


# v0.5.1 — o voo que deixa algo para mergear termina pedindo a revisão no alvo (revisar.py).
RC_PEDE_REVISAO = (0, 3, 4, 10)
LEMBRETE_REVISAO = "## Próximo passo — revisão no alvo"


def write_aar(op_dir: Path, op_id, results: dict):
    """Grava report.md + metrics.json na pasta da op."""
    results = compat.metrics(results)
    (op_dir / "metrics.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    aar = op_dir / "report.md"
    stamp = results.get("finished_at", "")
    lines = [f"# Run report — Op {op_id} · {stamp}", "",
             "> Gerado pelo e9_run.py (preencher análise humana no briefing da manhã).", "",
             "## As 4 perguntas",
             f"1. O que devia acontecer? → ver spec (Objective/Intent).",
             f"2. O que aconteceu? → gate: **{'VERDE' if results.get('gate_pass') else 'VERMELHO'}**; "
             f"agente {'terminou ok' if results.get('agent_ok') else 'NÃO terminou ok'}.",
             "3. Por quê? → (humano)",
             "4. Manter / melhorar → (humano)", "",
             "## Engineering metrics",
             f"- Branch: `{results.get('branch')}` — "
             f"{'pronta p/ revisão' if results.get('committed') else 'NÃO commitada'}"
             + (f" @ `{results['commit_sha']}`" if results.get("commit_sha") else "") + ".",
             f"- Gate: {'pass' if results.get('gate_pass') else 'FAIL/abort'}.",
             f"- Status: {results.get('status')}."]
    m = results.get("agent") or {}
    if m:
        lines += [f"- Agente: rc={results.get('agent_rc')}"
                  + (" (TIMEOUT)" if results.get("agent_rc") == RC_TIMEOUT else "")
                  + (f" · modelo={', '.join(m['modelos'])}" if m.get("modelos") else "")
                  + f" · turnos={m.get('num_turns')} · custo≈US$ {m.get('total_cost_usd')}"
                  + f" · tokens in/out={m.get('input_tokens')}/{m.get('output_tokens')}"
                  + f" · duração={_min(m.get('duration_ms'))}"]
        if m.get("recusas"):
            lines.append(f"- ⚠️ **Permissões recusadas ao agente ({len(m['recusas'])}):** "
                         + "; ".join(f"`{r[:100]}`" for r in m["recusas"][:5]) + ".")
    if results.get("wall_clock_s") is not None:
        lines.append(f"- Wall-clock da op: {results['wall_clock_s'] / 60:.1f} min.")
    w = results.get("workspace")
    if w:
        lines.append(f"- Banco do voo (v0.7): `{w.get('container', '—')}` — {w.get('estado')}.")
    for e in results.get("esperas") or []:
        lines.append(f"- Cedeu a vez à máquina: {e.get('esperou_s')} s ({e.get('motivo')})"
                     + (" — ⚠️ estourou o teto e seguiu" if e.get("estourou") else "") + ".")
    if results.get("pulados_a_mais"):
        pm = results["pulados_a_mais"]
        lines.append(f"- ⚠️ **Pulados a mais:** o gate pulou {pm['gate']}, a base {pm['base']}.")
    lines.append("")
    if m.get("result_text"):
        lines += ["## Relatório do agente (verbatim — desvios declarados, policy §7)", "",
                  m["result_text"], ""]
    if results.get("gate") is not None:
        g = results["gate"]
        partes = [f"{k} {g[k]}" for k in ("ruff", "mypy") if k in g]
        partes += [f"{g[k]} {k}" for k in ("passed", "failed", "errors") if k in g]
        lines += ["## Gate do E-9 × relatório do agente", "",
                  f"- **Gate (a prova):** {'VERDE' if results.get('gate_pass') else 'VERMELHO'}"
                  + (f" — {', '.join(partes)}" if partes else "") + "."]
        diverg = results.get("divergencias") or []
        lines += [f"- ⚠️ **Divergência:** {d}." for d in diverg] or [
            "- O relatório do agente não contradiz o gate."]
        lines.append("")
    a = results.get("acceptance")
    if a:
        lines += ["## Acceptance tests × entrega (v0.4)", "",
                  f"- Arquivos: {', '.join(f'`{r}`' for r in a.get('arquivos', {}))}.",
                  f"- Na base (antes do agente): rc {a.get('base_rc')} · {a.get('base')} · "
                  f"{a.get('coletados')} coletado(s)."]
        if a.get("mexidos"):
            lines.append(f"- ⚠️ **Mexidos pelo agente:** {', '.join(a['mexidos'])}.")
        elif "entrega" in a:
            lines.append(f"- Depois do agente: rc {a.get('entrega_rc')} · {a.get('entrega')} · "
                         "hashes intactos.")
        if a.get("veredito"):
            lines.append(f"- **{a['veredito']}**")
        lines.append("")
    if results.get("traceability"):
        lines += ["## Traceability — requirement × test × run (v0.8)", "",
                  "| Requirement | Acceptance tests | Na base | Na entrega |", "|---|---|---|---|"]
        for r in results["traceability"]:
            testes = ", ".join(f"`{t}`" for t in r["testes"]) or (
                "sem aceite (declarado)" if r.get("sem_aceite") else "—")
            lines.append(f"| {r['texto'].replace('|', '/')} | {testes} | {r.get('base', '—')} | "
                         f"{r.get('entrega', 'não rodou')} |")
        guardas = [r["fr"] for r in results["traceability"] if r.get("base") == "já passa"]
        if guardas:
            lines.append(f"\n- {', '.join(guardas)} já passava(m) na base: guarda de regressão, não "
                         "prova do que a op fez.")
        lines.append("")
    rv = results.get("reviewer")
    if rv:
        lines += ["## AI code review de outro modelo (v0.6 — só informa, não muda o rc)", ""]
        if rv.get("veredito"):
            mascara = ", ".join(f"{n} {k}" for k, n in (rv.get("mascarados") or {}).items())
            lines += [f"- **{rv['veredito']}** — `{rv.get('modelo')}`"
                      + (f" · US$ {rv['custo']:.2f}" if isinstance(rv.get("custo"), (int, float)) else "")
                      + (f" · mascarados: {mascara}" if mascara else "")
                      + (" · ⚠️ diff cortado no limite" if rv.get("diff_cortado") else "") + ".",
                      f"- {rv.get('resumo', '')}"]
            lines += [f"- [{a['severidade']}] `{a['arquivo']}:{a['linha']}` — {a['problema']} "
                      f"*Por quê:* {a['por_que']}" for a in rv.get("achados") or []] or [
                "- Nenhum achado."]
        else:
            lines.append(f"- {rv.get('estado')}.")
        lines.append("")
    if results.get("gate_tail"):
        lines += ["## Gate (fim da saída)", "", "```", results["gate_tail"], "```", ""]
    if (results.get("rc") in RC_PEDE_REVISAO and not results.get("treino")
            and not results.get("review")):
        lines += [LEMBRETE_REVISAO, "",
                  "Depois do merge (ou do descarte), a sessão do alvo grava o desfecho — é o número que o",
                  "gate não dá, e o placar do dashboard mostra \"sem revisão\" até lá:", "", "```",
                  f"python orchestrator/revisar.py --op ops/{op_dir.name} --como-veio --commit-alvo <sha>",
                  f"python orchestrator/revisar.py --op ops/{op_dir.name} --consertado \"o que mudou\" --commit-alvo <sha>",
                  f"python orchestrator/revisar.py --op ops/{op_dir.name} --descartado \"por quê\"",
                  "```", ""]
    lines += ["## Arquivos brutos (ficam na máquina, fora do git)",
              "- `agent.jsonl` — saída integral do agente (stream-json).",
              "- `gate.log` — saída integral do gate.", ""]
    aar.write_text("\n".join(lines), encoding="utf-8")


def avisar_fim(cfg, op_id, results) -> str:
    """O aviso de fim de voo (orchestrator/aviso.py). Carregado pelo caminho do arquivo, para valer
    tanto rodando o script quanto importado pelos testes; se faltar, o voo segue sem aviso."""
    try:
        mod = _carregar("aviso.py", "e9_aviso")
        if mod is None:                          # instalação sem canal de aviso (o kit, por exemplo)
            return "sem aviso configurado"
        return mod.avisar(cfg, op_id, results)
    except Exception as e:                       # noqa: BLE001 — aviso nunca derruba o voo
        return f"falhou: {type(e).__name__}"


def _carregar(nome: str, apelido: str):
    """Módulo irmão pelo caminho do arquivo (vale rodando o script e importado pelos testes); None se
    faltar — o kit, por exemplo, não tem aviso nem revisor."""
    arq = Path(__file__).with_name(nome)
    if not arq.exists():
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location(apelido, arq)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_WS = None


def _workspace():
    """O módulo do workspace (v0.7). Ao contrário do aviso e do revisor, não é opcional: a trava
    por op e os slots valem para todo voo."""
    global _WS
    if _WS is None:
        _WS = _carregar("workspace.py", "e9_workspace")
        if _WS is None:
            sys.exit("ABORT: orchestrator/workspace.py ausente")
    return _WS


def anexar_banco_do_voo(brief_file: Path, ws: dict) -> None:
    """v0.7 — o agente sabe que o banco é dele, e que a URL não se lê nem se escreve."""
    with open(brief_file, "a", encoding="utf-8") as h:
        h.write("\n\n" + "=" * 60 + "\n# BANCO DESTE VOO (v0.7)\n\n"
                f"Este voo tem um Postgres só dele (container `{ws['container']}`), que morre no fim. "
                f"As variáveis {', '.join(f'`{v}`' for v in sorted(ws['env']))} já estão no ambiente "
                "de todo Bash que você rodar e apontam para ele. Não as leia, não as imprima e não "
                "as escreva em arquivo (policy §5): basta rodar o portão como a spec manda. O `.env` da "
                "worktree continua apontando para o banco compartilhado e perde para o ambiente. "
                "Ninguém mais usa este banco, mas dentro do voo vale o de sempre: uma suíte por vez.\n")


def base_da_entrega(cfg) -> str | None:
    """O commit de onde a entrega parte: o merge-base da worktree com o HEAD do alvo. Num re-voo a
    branch pode já ter o commit do voo anterior, e o diff tem de mostrar a entrega inteira."""
    rc, topo = git(["rev-parse", "HEAD"], cwd=cfg["target_repo"])
    if rc != 0:
        return None
    rc, base = git(["merge-base", "HEAD", topo.strip()], cwd=cfg["worktree"])
    return base.strip() if rc == 0 else None


def review_entrega(cfg, opord: Path, alvo: Path | None, base: str | None, gate: dict) -> dict:
    """v0.6 — o parecer de outra família de modelo (orchestrator/revisor.py). Só informa: o rc não
    muda por ele. Nunca levanta."""
    mod = _carregar("reviewer.py", "e9_reviewer")
    if mod is None:
        return {"estado": "sem revisor instalado"}
    try:
        if not mod.ligado(cfg):
            return {"estado": "desligado"}
        if not base:
            return {"estado": "indisponível: base da entrega desconhecida"}
        diff = mod.diff_da_worktree(cfg["worktree"], base, cfg.get("worktree_link", []))
        travas = alvo.read_text(encoding="utf-8") if alvo else ""
        return mod.revisar(mod.modelo_de(cfg), opord.read_text(encoding="utf-8"), travas, diff, gate)
    except Exception as e:                       # noqa: BLE001 — revisor nunca derruba o voo
        return {"estado": f"indisponível: {type(e).__name__}: {str(e)[:160]}"}


def _min(ms):
    return f"{ms / 60000:.1f} min" if isinstance(ms, (int, float)) else "—"


def main():
    ap = argparse.ArgumentParser(description="E-9 — executor de uma operação")
    ap.add_argument("--op", required=True, help="pasta da op (contém spec.md + op.json)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="ensaio: valida e planeja, não muta nada (default)")
    g.add_argument("--live", action="store_true", help="execução real (fora da produção)")
    args = ap.parse_args()
    dry = not args.live  # default seguro = ensaio

    op_dir = Path(args.op).resolve()
    e9_root = Path(__file__).resolve().parent.parent
    roe = e9_root / "process" / "POLICY.md"
    cfg, opord = load_op(op_dir)
    op_id = cfg.get("op", op_dir.name)
    alvo = target_path(cfg) if cfg.get("target") else None
    aceite = [rel for rel, _ in acceptance_files(op_dir)] if cfg.get("acceptance") else []
    brief = build_brief(opord, roe, cfg, alvo=alvo, aceite=aceite)

    sitrep([f"E-9 · OP {op_id} · modo {'DRY RUN' if dry else 'LIVE'}",
            f"Target    : {cfg['target_repo']} @ {cfg['branch']}",
            f"Worktree: {cfg['worktree']}",
            f"Agent     : model={cfg['agent'].get('model')} · cmd no op.json",
            f"Gate      : {cfg['gate']['test_cmd']}",
            f"Artifacts : {', '.join(cfg.get('expected_artifacts', [])) or '—'}",
            f"Commit    : {commit_message(cfg, op_id, ['…']).splitlines()[0]}",
            f"Timeouts  : agent {cfg['agent'].get('timeout_s', AGENT_TIMEOUT_S)}s · "
            f"gate {cfg['gate'].get('timeout_s', GATE_TIMEOUT_S)}s",
            f"Baseline  : {'desligada' if cfg['gate'].get('baseline') is False else (cfg['gate'].get('baseline_cmd') or 'o mesmo gate')}",
            f"Acceptance: {', '.join(aceite) + ' · ' + cfg['acceptance']['cmd'] if aceite else '—'}",
            f"Guardrails: {alvo.name + ' (todas citadas na spec)' if alvo else '—'}",
            f"Database  : {'próprio do run, receita ' + cfg['workspace']['banco'] if cfg.get('workspace') else 'o do target (sem workspace)'}",
            f"Briefing  : {len(brief)} chars (spec + policy" + (" + guardrails" if alvo else "")
            + (" + acceptance" if aceite else "") + ")"])

    if dry:
        log("DRY RUN", "validação OK. Plano: worktree → baseline → agent → gate → commit(branch) → run report.")
        log("DRY RUN", "Nada foi mutado. Para valer: --live.")
        return 0

    # ── LIVE ─────────────────────────────────────────────────────────────
    # v0.7 — a trava da op e o slot vêm antes de tudo, e o `finally` derruba o banco do voo em
    # qualquer saída: fim normal, abort, exceção, SIGTERM/SIGHUP (viram SystemExit). Só o kill -9
    # escapa, e para ele há a faxina no começo do próximo voo.
    wsm = _workspace()
    trava = wsm.travar_op(op_dir)
    if trava is None:
        log("ABORT", f"a op {op_id} já está em voo ({op_dir / '.voo.lock'}): nada foi tocado")
        return wsm.RC_OP_EM_VOO
    slot, n_slot = wsm.pegar_slot(avisar=lambda m: log("SLOT", m))
    if slot is None:
        wsm.soltar(trava)
        log("ABORT", "nenhum slot de voo desocupou dentro do teto: nada foi tocado")
        return wsm.RC_OP_EM_VOO
    estado = {"slot": n_slot}
    sinais = [s for s in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGHUP", None)) if s]
    antes = {}
    for s in sinais:
        try:
            antes[s] = signal.signal(s, _sair_por_sinal)
        except (ValueError, OSError):            # fora da thread principal: segue sem o handler
            pass
    try:
        return _voar(cfg, op_dir, op_id, opord, alvo, aceite, brief, estado)
    finally:
        if estado.get("ws"):
            matar_restos(cfg["worktree"])        # nada do voo pode seguir usando o banco
            log("WS", wsm.derrubar(estado["ws"]))
            wsm.tarjar_logs(op_dir, wsm.segredos(estado["ws"]))
        for s, h in antes.items():
            signal.signal(s, h)
        wsm.soltar(slot)
        wsm.soltar(trava)


def _sair_por_sinal(num, _frame):
    raise SystemExit(128 + num)


def _voar(cfg, op_dir, op_id, opord, alvo, aceite, brief, estado):
    """O voo propriamente dito (até a v0.6, o corpo do `main`)."""
    wsm = _workspace()
    results = {"op": op_id, "branch": cfg["branch"], "started_at": datetime.now().isoformat(),
               "slot": estado.get("slot")}
    # v0.5.1 — o re-voo não apaga o voo anterior: o resumo dele fica em `previous_runs`, e o
    # placar mede o verde DE PRIMEIRA. Até aqui o metrics.json novo sobrescrevia o velho.
    results["previous_runs"] = previous_runs(op_dir)
    if cfg.get("treino"):
        results["treino"] = True
    ok, out = setup_worktree(cfg)
    if not ok:
        log("ABORT", f"worktree falhou:\n{out}")
        return 2
    brief_file = op_dir / "_brief.txt"
    brief_file.write_text(brief, encoding="utf-8")
    base = base_da_entrega(cfg)            # v0.6 — antes do agente: é de onde o diff do revisor parte

    t0 = time.monotonic()
    tetos = {"agente": cfg["agent"].get("timeout_s", AGENT_TIMEOUT_S), "reviewer": 600}
    for nome in ("base", "aceite_base", "gate", "aceite_entrega"):
        tetos[nome] = cfg["gate"].get("timeout_s", GATE_TIMEOUT_S)

    def fase(nome):
        """v0.5 — marca o começo de uma etapa, com o teto dela, em `fases.json` (o TOC lê para o
        cronômetro) e no metrics.json (o run report mostra quanto cada etapa levou)."""
        agora = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        results.setdefault("fases", []).append({"fase": nome, "inicio": agora, "teto_s": tetos.get(nome)})
        try:
            (op_dir / "fases.json").write_text(json.dumps(results["fases"]), encoding="utf-8")
        except OSError:
            pass

    fase("preparo")                      # zera o fases.json de um voo anterior desta mesma op

    def finish(rc, status):
        fase("fim")
        results["rc"] = rc                # v0.4.2 — o TOC lê o código, não adivinha pelo texto
        results["status"] = status
        results["finished_at"] = datetime.now().isoformat()
        results["wall_clock_s"] = round(time.monotonic() - t0, 1)
        # v0.7 — a senha do banco do voo não sai do voo: o pytest imprime a URL numa asserção que falha
        results.update(wsm.tarjar(dict(results), wsm.segredos(estado.get("ws"))))
        results["aviso"] = avisar_fim(cfg, op_id, results)    # v0.5 — opcional: orchestrator/aviso.py
        log("AVISO", results["aviso"])
        write_aar(op_dir, op_id, results)

    def ceder():
        """v0.7 — antes de cada suíte, os processos de E9_PRIORITARIOS têm a vez."""
        if wsm.maquina_ocupada():
            fase("espera")
            results.setdefault("esperas", []).append(wsm.esperar_maquina())

    # v0.7 — o banco do voo: faxina do que um harness morto deixou, sobe, e só segue se o papel da
    # aplicação conecta de verdade (há suíte que PULA os testes de banco que não conectam).
    rec = wsm.receita(cfg, TARGETS_DIR)
    if rec:
        removidos = wsm.faxina()
        if removidos:
            results["faxina"] = removidos
            log("WS", f"faxina: {', '.join(removidos)}")
        ws, msg = wsm.subir(cfg, rec)
        estado["ws"] = ws
        ok = ws is not None
        if ok:
            ok, msg = wsm.pronto(ws)
        log("WS", msg)
        results["workspace"] = {**(wsm.publico(ws) or {}), "estado": msg}
        if not ok:
            results["committed"] = False
            finish(wsm.RC_BANCO, f"ABORT — o banco do voo não ficou pronto: {msg} (nada foi despachado)")
            log("STATUS", "BANCO DO VOO FORA — agente não despachado.")
            return wsm.RC_BANCO
        cfg["_ambiente_do_voo"] = ws["env"]
        anexar_banco_do_voo(brief_file, ws)

    # v0.3 — linha de base: o gate roda na worktree recém-cortada, ANTES do agente. Na Op
    # 009 o `main` já nascia vermelho no ruff (herança de outro commit), e o agente voou 45 min
    # para o gate acusar um erro que não era dele. Worktree suja (sobra de um voo anterior) também
    # não serve de base: o que se testaria não seria o `main`.
    if cfg["gate"].get("baseline", True):
        rc, sujo = git(["status", "--porcelain", "-uall"], cwd=cfg["worktree"])
        sobras = [ln for ln in sujo.splitlines()
                  if ln.strip() and ln[3:].strip() not in cfg.get("worktree_link", [])]
        if rc != 0 or sobras:
            results["committed"] = False
            finish(8, f"ABORT — worktree não está limpa antes do voo: {sobras[:10] or sujo[:300]}")
            log("STATUS", "WORKTREE SUJA — a linha de base não seria o main. Abort antes do agente.")
            return 8
        log("OWX", "linha de base: o gate na worktree recém-cortada, antes do agente")
        ceder()
        fase("base")
        base_ok, base_out = run_gate(cfg, op_dir / "baseline.log", chave="baseline_cmd")
        results["baseline_pass"] = base_ok
        results["baseline_gate"] = gate_summary(base_out)
        results["baseline_skipped"] = contagem(base_out).get("skipped", 0)
        anexar_tempo_do_portao(brief_file, cfg, results["fases"][-1]["inicio"])
        if not base_ok:
            results["committed"] = False
            results["gate_tail"] = base_out.strip()[-1500:]
            finish(7, "ABORT — linha de base VERMELHA: o gate já falha no main, antes do agente "
                   "(nada foi despachado)")
            log("STATUS", "LINHA DE BASE VERMELHA — agente não despachado. run report gravado.")
            return 7

    # v0.4 — o aceite entra DEPOIS da base (o `main` é verde sem ele) e tem de falhar ANTES do
    # agente: teste que já passa, ou que nem roda, não prova o que a op vai fazer.
    if aceite:
        ok, msg, info = instalar_acceptance(cfg, op_dir)
        results["acceptance"] = info
        if not ok:
            results["committed"] = False
            info["veredito"] = msg
            finish(RC_ACCEPTANCE_BASE, f"ABORT — aceite não instalado: {msg}")
            log("STATUS", f"ACCEPTANCE NÃO INSTALADO — {msg}")
            return RC_ACCEPTANCE_BASE
        log("ACT", msg)
        ceder()
        fase("aceite_base")
        rc_a, c, base_out = rodar_acceptance(cfg, op_dir / "acceptance-base.log")
        info.update({"base_rc": rc_a, "base": c,
                     "coletados": c.get("passed", 0) + c.get("failed", 0) + c.get("errors", 0)})
        results["traceability"] = matriz_de_traceability(opord, op_dir, base_out, None)        # v0.8
        motivo = julgar_acceptance_base(cfg, rc_a, c)
        if motivo:
            results["committed"] = False
            info["veredito"] = motivo
            finish(RC_ACCEPTANCE_BASE, f"ABORT — {motivo} (nada foi despachado)")
            log("STATUS", f"ACCEPTANCE NÃO FALHA NA BASE — {motivo}")
            return RC_ACCEPTANCE_BASE
        log("ACT", f"aceite falha na base como devia: {c}")

    fase("agente")
    results["agent_ok"], op_out, results["agent_rc"] = dispatch_agent(
        cfg, brief_file, op_dir / "agent.jsonl")
    results["agent"] = parse_agent_output(op_out)
    restos = matar_restos(cfg["worktree"])
    if restos:
        results["restos_mortos"] = restos
        log("OPR", f"{len(restos)} processo(s) do agente ainda rodando na worktree — mortos antes do gate")
    if results["agent"].get("is_error"):
        # Visto em 24/10: OAuth vencido → `claude -p` sai com rc=0 e is_error=true. O rc
        # sozinho mente; quem diz se o agente trabalhou é o evento `result`.
        results["agent_ok"] = False
        results["committed"] = False
        finish(6, "ABORT — agente terminou em erro (is_error): "
               + results["agent"].get("result_text", "")[:300])
        log("STATUS", f"AGENTE EM ERRO — {results['agent'].get('result_text', '')[:200]}")
        return 6
    if results["agent_rc"] == RC_TIMEOUT:
        # Agente que estoura o teto não chega ao gate: o que ele deixou pela metade não é
        # entrega. Branch fica como está, para o owner olhar.
        results["committed"] = False
        finish(5, "ABORT — agente estourou o teto de tempo (TIMEOUT), sem gate nem commit")
        log("STATUS", "AGENTE EM TIMEOUT — abort. run report gravado. Sem push.")
        return 5

    if aceite:
        mexidos = acceptance_mexido(cfg, results["acceptance"]["arquivos"])
        if mexidos:
            # Abort antes do gate: com a prova alterada, o verde do gate não diria nada.
            results["committed"] = False
            results["acceptance"]["mexidos"] = mexidos
            results["acceptance"]["veredito"] = "o agente mexeu no aceite — abort mesmo que o resto esteja verde"
            finish(RC_ACCEPTANCE_ENTREGA, f"ABORT — aceite alterado pelo agente: {mexidos}")
            log("STATUS", f"ACCEPTANCE MEXIDO — {mexidos}. Branch fica p/ revisão, sem commit.")
            return RC_ACCEPTANCE_ENTREGA

    ceder()
    fase("gate")
    results["gate_pass"], ow_out = run_gate(cfg, op_dir / "gate.log")
    results["gate_tail"] = ow_out.strip()[-1500:]
    results["gate"] = gate_summary(ow_out)
    # v0.7 — pulou mais que a base, vermelho: banco que não conecta pode virar skip, e o gate
    # sairia verde com metade da suíte sem rodar. Vale também sem workspace.
    pulados = contagem(ow_out).get("skipped", 0)
    if results["gate_pass"] and "baseline_skipped" in results and pulados > results["baseline_skipped"]:
        results["gate_pass"] = False
        results["pulados_a_mais"] = {"base": results["baseline_skipped"], "gate": pulados}
    results["divergencias"] = divergencias(
        results["gate_pass"], results["gate"], results["agent"].get("result_text", ""))
    for d in results["divergencias"]:
        log("OWX", f"DIVERGÊNCIA: {d}")

    if results["gate_pass"]:
        # v0.6 — gate verde: o revisor de outro modelo lê a spec, as travas e o diff. Vem antes do
        # aceite e do commit para cobrir também a entrega barrada (F) e a de aceite falho.
        fase("reviewer")
        results["reviewer"] = review_entrega(cfg, opord, alvo, base, results["gate"])
        rv = results["reviewer"]
        log("REV", f"{rv.get('veredito') or rv.get('estado')}"
            + (f" · {len(rv['achados'])} achado(s)" if rv.get("achados") is not None else ""))

    if not results["gate_pass"]:
        results["committed"] = False
        pm = results.get("pulados_a_mais")
        finish(3, (f"ABORT — gate com {pm['gate']} teste(s) pulado(s), e a linha de base pulou "
                   f"{pm['base']}: teste que não roda não conta como verde" if pm else
                   "ABORT — gate vermelho") + " (branch deixada p/ revisão, sem commit-como-concluído)")
        log("STATUS", "OVERWATCH VERMELHO — abort. run report gravado. Sem push.")
        print(wsm.tarjar(ow_out[-1500:], wsm.segredos(estado.get("ws"))))
        return 3

    if aceite:
        a = results["acceptance"]
        ceder()
        fase("aceite_entrega")
        a["entrega_rc"], a["entrega"], entrega_out = rodar_acceptance(cfg, op_dir / "acceptance-entrega.log")
        results["traceability"] = matriz_de_traceability(opord, op_dir, base_out, entrega_out, a["entrega_rc"])
        motivo = julgar_acceptance_entrega(a["entrega_rc"], a["entrega"], a["coletados"])
        if motivo:
            results["committed"] = False
            a["veredito"] = motivo
            finish(RC_ACCEPTANCE_ENTREGA, f"ABORT — gate verde, mas {motivo}")
            log("STATUS", f"ACCEPTANCE NÃO CUMPRIDO — {motivo}")
            return RC_ACCEPTANCE_ENTREGA
        a["veredito"] = f"cumprido: {a['coletados']} teste(s) de aceite passaram, nenhum mexido nem pulado"

    fase("commit")
    results["committed"], commit_out = stage_and_commit(cfg, op_id, aceite)
    if not results["committed"]:
        # Gate passou mas o commit não fechou (artefato faltando, etc.) — não maquiar.
        finish(4, f"ABORT — gate verde, mas commit falhou: {commit_out}")
        log("STATUS", f"COMMIT NÃO FECHOU — {commit_out}")
        return 4
    rc, sha = git(["rev-parse", "--short", "HEAD"], cwd=cfg["worktree"])
    results["commit_sha"] = sha.strip() if rc == 0 else None
    rc, nomes = git(["show", "--name-only", "--format=", "HEAD"], cwd=cfg["worktree"])
    results["arquivos"] = [n.strip() for n in nomes.splitlines() if n.strip()] if rc == 0 else []
    finish(0,"OK — gate verde, commitado na branch (aguarda revisão do owner)")
    sitrep([f"OP {op_id}: VERDE.",
            f"Branch {cfg['branch']} commitada (SEM push).",
            "run report + metrics gravados. Aguarda luz verde do owner p/ merge/deploy.",
            f"No merge: python orchestrator/revisar.py --op ops/{op_dir.name} (--como-veio | --consertado | --descartado)"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
