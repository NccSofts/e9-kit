#!/usr/bin/env python3
"""
e9_run.py — harness de execução de UMA operação do E-9 (caminho leve, v0.4).

v0.2 (2026-10-24): o operador não trava mais sem TTY (stdin=DEVNULL + teto de tempo que
mata o grupo de processos); a mensagem de commit vem do op.json (`commit.subject`); a saída
do operador fica em `operator.jsonl` e o relatório final dele entra no AAR, com custo e
turnos quando o cmd_template usa `--output-format stream-json`.

v0.3 (2026-10-25): linha de base — o gate roda na worktree recém-cortada ANTES do operador, e
vermelho herdado do `main` (ou worktree suja) aborta sem despachar; o AAR põe o gate do E-9 ao
lado do relatório do operador e marca onde os dois divergem; o briefing proíbe teste em
paralelo no banco único e "verde" sem a saída que prove.

v0.4 (2026-10-28): o pedido vira contrato executável, em duas peças. (1) ACEITE — testes escritos
pelo E-9 no recon (`ops/<op>/aceite/`), copiados para a worktree depois da linha de base: têm de
FALHAR antes do operador (rc 9 se já passam ou nem rodam), ficam intocados por ele (sha256) e têm
de PASSAR inteiros, sem skip, depois (rc 10); entram no commit como regressão. Responde ao ponto
cego medido em voos reais: gate verde com o desenho errado. (2) TRAVAS DO ALVO — `alvos/<alvo>.md`
com os invariantes do repo-alvo, numerados; o `load_op` recusa OPORD que não cite cada um numa
seção `## Travas do alvo`, e o arquivo vai inteiro ao briefing. Responde à ordem que
contradizia invariantes do repo que quem a escreveu esqueceu.

Executa o ciclo da doutrina para um OPORD:
  rehearsal (--dry-run) → worktree → operador → overwatch (gate) → AAR → commit (SEM push)

Princípios codificados (ROE):
  - Só branch feat/* no repo-alvo; NUNCA push, NUNCA merge, NUNCA prod.
  - git add SELETIVO (expected_artifacts do op.json); NUNCA `git add -A`.
  - Overwatch (teste) é gate: vermelho = ABORT, sem marcar a op como concluída.
  - --dry-run = ensaio na casa de tiro: valida e planeja, NÃO muta nada.

Uso:
  python3 orchestrator/e9_run.py --op ops/exemplo-slug --dry-run
  python3 orchestrator/e9_run.py --op ops/exemplo-slug --live     # fora da produção, sempre

Stdlib apenas. O ponto que toca o modelo (dispatch do operador) é um seam claro:
o comando real vem do op.json (operator.cmd_template).
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
from datetime import datetime
from pathlib import Path

# Console em UTF-8 (Windows usa cp1252 por default e engasga em ─ · ←).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# --- ROE codificada no harness (guardrails que NÃO dependem de disciplina humana) ---
FORBIDDEN_GIT_SUBCMDS = {"push", "merge", "rebase", "reset"}      # ROE §2, §10
FORBIDDEN_ADD_FLAGS = {"-A", "--all", "."}                       # ROE §9 (add seletivo)
# ROE §3 — o que conta como produção é de cada um: o op.json declara em `prod_markers`
# (trechos que, se aparecerem no target_repo, barram a op). Estes valem sempre, por cima.
PROD_MARKERS: tuple[str, ...] = ()
ALVOS_DIR = Path(__file__).resolve().parent.parent / "alvos"     # v0.4 — travas por repo-alvo
_TRAVA_ID = re.compile(r"\[([A-Z][A-Z0-9]*-\d+)\]")


def log(tag, msg):
    print(f"[E-9 {tag}] {msg}")


def sitrep(lines):
    bar = "─" * 58
    print(bar)
    for ln in lines:
        print(ln)
    print(bar)


def load_op(op_dir: Path, alvos_dir: Path | None = None):
    """Lê op.json + confirma que OPORD.md existe. Retorna (cfg, opord_path)."""
    cfg_path = op_dir / "op.json"
    opord = op_dir / "OPORD.md"
    if not cfg_path.exists():
        sys.exit(f"ABORT: op.json ausente em {op_dir}")
    if not opord.exists():
        sys.exit(f"ABORT: OPORD.md ausente em {op_dir}")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    # --- Validações da ROE no contrato (falha cedo, antes de qualquer mutação) ---
    branch = cfg.get("branch", "")
    if not branch.startswith("feat/"):                                   # ROE §2
        sys.exit(f"ABORT (ROE §2): branch '{branch}' inválida — todo trabalho em `feat/*`.")
    target = cfg.get("target_repo", "")
    for m in (*PROD_MARKERS, *cfg.get("prod_markers", [])):             # ROE §3
        if m in target:
            sys.exit(f"ABORT (ROE §3): target_repo '{target}' bate marcador de prod ('{m}'). Prod é off-limits.")
    if not cfg.get("expected_artifacts"):                               # ROE §9
        sys.exit("ABORT (ROE §9): expected_artifacts vazio — sem ele não há `git add` seletivo possível.")
    subject = (cfg.get("commit") or {}).get("subject")
    if subject is not None:                                             # conventional commits
        prefix = subject.split(":", 1)[0].split("(", 1)[0].strip()
        if ":" not in subject or prefix not in COMMIT_PREFIXES:
            sys.exit(f"ABORT: commit.subject '{subject}' sem prefixo semântico {COMMIT_PREFIXES}.")
    # Defense-in-depth: nenhum comando configurado (operador/overwatch) pode embutir push/merge.
    blob = json.dumps(cfg)
    for danger in ("git push", "git merge", "git reset", "git rebase"):  # ROE §2/§10
        if danger in blob:
            sys.exit(f"ABORT (ROE §2/§10): op.json contém '{danger}' — proibido no harness.")
    if cfg.get("aceite") is not None:                                   # v0.4
        if not (cfg["aceite"] or {}).get("cmd"):
            sys.exit("ABORT (aceite): `aceite.cmd` ausente — sem comando não há o que provar.")
        if not aceite_files(op_dir):
            sys.exit(f"ABORT (aceite): nenhum arquivo em {op_dir / 'aceite'} — o aceite é a pasta.")
    if cfg.get("alvo"):                                                 # v0.4
        faltam = travas_nao_citadas(opord, alvo_path(cfg, alvos_dir))
        if faltam:
            sys.exit(f"ABORT (travas do alvo): a OPORD não cita {faltam} na seção "
                     "`## Travas do alvo` — cada trava leva \"aplica: como\" ou \"não se aplica: porquê\".")
    return cfg, opord


def aceite_files(op_dir: Path) -> list[tuple[str, Path]]:
    """Os testes de aceite da op (v0.4): tudo sob `ops/<op>/aceite/`, com o caminho relativo
    que terão na worktree. Cache de Python não é aceite."""
    raiz = Path(op_dir) / "aceite"
    if not raiz.is_dir():
        return []
    return sorted((p.relative_to(raiz).as_posix(), p) for p in raiz.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")


def alvo_path(cfg, alvos_dir: Path | None = None) -> Path:
    path = Path(alvos_dir or ALVOS_DIR) / f"{cfg['alvo']}.md"
    if not path.exists():
        sys.exit(f"ABORT (travas do alvo): {path} não existe — alvo declarado sem as travas dele.")
    return path


def travas_nao_citadas(opord: Path, alvo: Path) -> list[str]:
    """IDs de trava do alvo que a seção `## Travas do alvo` da OPORD não cita (v0.4). Numa tarefa
    real a ordem proibia justo o que duas travas do repo exigiam, e ninguém notou ao escrever: a
    trava não é do operador, é de quem redige a OPORD."""
    ids = list(dict.fromkeys(_TRAVA_ID.findall(alvo.read_text(encoding="utf-8"))))
    texto = opord.read_text(encoding="utf-8")
    m = re.search(r"^#{2,4}\s*Travas do alvo\s*$(.*?)(?=^#{1,4}\s|\Z)", texto,
                  re.IGNORECASE | re.MULTILINE | re.DOTALL)
    secao = m.group(1) if m else ""
    return [i for i in ids if not re.search(rf"\b{re.escape(i)}\b", secao)]


def build_brief(opord: Path, roe: Path, cfg: dict | None = None, *,
                alvo: Path | None = None, aceite: list[str] | None = None) -> str:
    """Briefing do operador = preâmbulo de comando + OPORD + ROE Card."""
    preamble = (
        "# BRIEFING DO OPERADOR (E-9)\n\n"
        "Você é um OPERADOR. Cumpra a Commander's Intent do OPORD, dentro da ROE.\n"
        "Trabalhe SÓ na worktree designada (`--add-dir`). Em dúvida sobre a Intent: HOLD + SITREP.\n\n"
        "## Regra de comando (mission command)\n"
        "- **FIXO, inviolável:** a Commander's Intent, o end-state e a ROE. Não negocie.\n"
        "- **DELEGÁVEL a você:** os MEIOS — estrutura, em qual app o arquivo mora, como você\n"
        "  implementa. Use seu julgamento de engenheiro sênior. Se desviar do que o OPORD\n"
        "  sugeriu (ex: outro app), está OK — mas **DECLARE o desvio e o porquê** na sua saída.\n"
        "- **PROIBIDO:** criar AAR ou métricas (isso é do E-9, não seu); pular um artefato\n"
        "  declarado; trabalhar fora da worktree; push/merge; tocar prod; usar dado real.\n"
        "- O caminho exato do artefato é seu; o ARTEFATO em si não é opcional.\n\n"
        "## Disciplina de teste (v0.3 — o que três voos seguidos erraram)\n"
        "- **O banco de teste é UM só.** Nunca rode duas rodadas de teste ao mesmo tempo, nem\n"
        "  uma em segundo plano enquanto outra roda: elas disputam o banco e inventam falhas.\n"
        "- **Não escreva \"verde\", \"limpo\" nem um número de testes sem a saída que prove.**\n"
        "  Antes do relatório, rode o portão do OPORD INTEIRO uma vez, espere terminar, e copie\n"
        "  a última linha de cada etapa. Rodou só parte? Diga que foi só parte.\n"
        "- O E-9 roda o portão de novo depois de você e põe o resultado dele ao lado do seu\n"
        "  relatório no AAR. Divergência entre os dois vira achado.\n"
        "- **NUNCA rode nada em segundo plano** (`run_in_background`, `&`, `nohup`). Você roda em\n"
        "  `claude -p`: quando você para de falar, a sessão acaba, e a notificação de \"terminou\"\n"
        "  nunca chega. Rode o portão em primeiro plano e ESPERE: o teto do Bash aqui é de 20 min.\n"
        "  O que sobrar rodando na worktree quando você terminar é morto pelo E-9 antes do gate.\n\n"
        "## Desvios da OPORD (v0.3.3 — obrigatório no relatório final)\n"
        "- Feche o relatório com uma seção `## Desvios da OPORD`. Para CADA ponto em que o que\n"
        "  você fez não é literalmente o que a OPORD descreve, uma linha: **a OPORD dizia** → **eu\n"
        "  fiz** → **por quê**. Uma lista mudada, um caso pulado, um teste antigo tocado, uma\n"
        "  regra do desenho de referência interpretada: tudo entra. Sem desvio, escreva `nenhum`.\n"
        "- O portão verde não prova o desenho: em voos reais ele deu verde e o defeito estava\n"
        "  num desvio do desenho que ninguém declarou. É esta seção que o Comandante revisa primeiro.\n\n"
        + "=" * 60 + "\n# OPORD\n\n" + opord.read_text(encoding="utf-8"))
    if roe.exists():
        preamble += "\n\n" + "=" * 60 + "\n# ROE (regras permanentes)\n\n" + roe.read_text(encoding="utf-8")
    if alvo is not None:
        preamble += ("\n\n" + "=" * 60 + "\n# TRAVAS DO ALVO (invariantes deste repositório)\n\n"
                     "Toda mudança neste repo respeita estas travas, mesmo as que a OPORD não "
                     "repete. Se uma ordem da OPORD contradiz uma trava: HOLD + SITREP, e declare "
                     "nos desvios.\n\n" + alvo.read_text(encoding="utf-8"))
    if aceite:
        preamble += ("\n\n" + "=" * 60 + "\n# TESTES DE ACEITE (só leitura)\n\n"
                     "O E-9 já pôs estes arquivos na worktree, e eles falham hoje: são a prova de "
                     "que o pedido foi cumprido. Faça-os passar sem editá-los, sem apagá-los e sem "
                     "pulá-los (nada de skip, xfail ou conftest que os desligue): o E-9 confere o "
                     "sha256 de cada um, e mexer é abort mesmo com tudo verde. Achou um teste "
                     "errado? HOLD + SITREP, e declare nos desvios.\n\n"
                     + "\n".join(f"- `{rel}`" for rel in aceite) + "\n")
    liberados = comandos_liberados(cfg) if cfg else []
    if liberados:
        preamble += ("\n\n" + "=" * 60 + "\n# COMANDOS BASH LIBERADOS (exatamente nestas formas)\n\n"
                     "Fora destas formas o comando é RECUSADO em silêncio: sem TTY ninguém aprova. "
                     "Comece pelo início exato de uma linha abaixo; o resto (argumentos, `2>&1 | tail`) "
                     "é livre. Para ler arquivo, use Read/Grep/Glob, não `cat`/`grep`/`find`.\n\n"
                     + "\n".join(f"- `{c}…`" for c in liberados) + "\n")
    return preamble


def comandos_liberados(cfg) -> list[str]:
    """Os prefixos de Bash que o `--allowedTools` do cmd_template libera, com os placeholders
    resolvidos (v0.3.2). Numa tarefa real o operador tentou `PYTHONPATH=src <venv>/bin/ruff`, que a
    lista não tinha, e teve TODO Bash recusado sem saber por quê: a lista existia só no op.json."""
    tpl = (cfg.get("operator") or {}).get("cmd_template", "")
    try:
        tpl = _fmt(tpl, cfg, brief_file="")
    except (KeyError, IndexError):
        pass
    return [m.rstrip(":*") for m in re.findall(r"Bash\(([^)]*)\)", tpl)]


RC_TIMEOUT = 124   # mesmo código do `timeout(1)` do coreutils


def run(cmd, cwd=None, timeout=None, log_file=None):
    """Subprocess fino. Retorna (rc, stdout+stderr).

    v0.2 — o travamento das primeiras tarefas: o `claude -p` herdava o stdin do harness,
    que sem TTY nunca fecha, e o `subprocess.run(capture_output=True)` esperava para sempre.
    Agora: stdin=DEVNULL (EOF imediato), saída num ARQUIVO (pipe cheio não trava o filho),
    grupo de processos próprio e `timeout` que mata o grupo inteiro — o shell e o que ele
    lançou. Estourou: rc=124, e a saída parcial volta mesmo assim (vai pro AAR)."""
    own_log = log_file is None
    if own_log:
        fd, tmp = tempfile.mkstemp(prefix="e9-run-", suffix=".log")
        os.close(fd)
        log_file = Path(tmp)
    log_file = Path(log_file)
    with open(log_file, "wb") as out:
        p = subprocess.Popen(cmd, cwd=cwd, shell=isinstance(cmd, str),
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
    """Mata o que ainda roda DENTRO da worktree depois que o operador termina (v0.3.4).

    Numa tarefa real o operador jogou duas suítes para segundo plano e encerrou "aguardando
    notificações" — em `claude -p` não há depois, e as suítes órfãs seguiriam disputando o
    banco de teste com o gate. O Claude Code pode soltar esses filhos em sessão própria, fora
    do grupo do operador; por isso a varredura é pelo diretório de trabalho (/proc/<pid>/cwd),
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
    """Portão único pras operações git DO HARNESS. Recusa o que a ROE proíbe —
    cinto-e-suspensório por cima da garantia de design (nenhum push existe no código).
    Pula opções globais (`-c key=val`, `-C path`) pra achar o subcomando real."""
    i = 0
    while i + 1 < len(args) and args[i] in ("-c", "-C"):
        i += 2
    sub = args[i] if i < len(args) else ""
    if sub in FORBIDDEN_GIT_SUBCMDS:
        sys.exit(f"ROE VIOLATION (§2/§10): `git {sub}` é proibido no harness.")
    if sub == "add":
        bad = FORBIDDEN_ADD_FLAGS.intersection(args[i + 1:])
        if bad:
            sys.exit(f"ROE VIOLATION (§9): `git add {' '.join(bad)}` — add é seletivo, arquivo por arquivo.")
    return run(["git", *args], cwd=cwd)


def _fmt(template, cfg, **extra):
    """Substitui os placeholders padrão de comando (op.json é cabeamento por dado).
    {worktree} {target_repo} {venv} {model} + extras (ex: {brief_file})."""
    return template.format(
        worktree=cfg["worktree"], target_repo=cfg["target_repo"],
        venv=cfg.get("venv", cfg["target_repo"].rstrip("/") + "/.venv"),
        model=cfg.get("operator", {}).get("model", "sonnet"), **extra)


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
        if rel.startswith("/") or ".." in rel:                 # ROE: só dentro do worktree
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


OPERATOR_TIMEOUT_S = 90 * 60     # teto default do operador (op.json: operator.timeout_s)
OVERWATCH_TIMEOUT_S = 30 * 60


def dispatch_operator(cfg, brief_file: Path, log_file: Path):
    """SEAM do modelo: roda o operador. cmd_template vem do op.json.
    Placeholders: {brief_file} {worktree} {model}.
    v0.2: a saída inteira fica em `log_file` (é onde o operador declara desvio — ROE §7) e o
    teto de tempo vem do op.json. Retorna (ok, saída, rc)."""
    cmd = _fmt(cfg["operator"]["cmd_template"], cfg, brief_file=str(brief_file))
    timeout = cfg["operator"].get("timeout_s", OPERATOR_TIMEOUT_S)
    log("OPR", f"dispatch (teto {timeout}s): {cmd}")
    rc, out = run(cmd, cwd=cfg["worktree"], timeout=timeout, log_file=log_file)
    if rc == RC_TIMEOUT:
        log("OPR", f"TIMEOUT — operador morto após {timeout}s (saída parcial em {log_file.name})")
    return rc == 0, out, rc


def parse_operator_output(text: str) -> dict:
    """Lê a saída do `claude -p --output-format stream-json`: uma linha JSON por evento,
    a última `type=result` traz o texto final e o custo. Saída em texto puro (op antiga,
    sem stream-json) vira o próprio texto, sem métrica. Nunca levanta."""
    final = None
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "result":
            final = ev
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
    }


def run_overwatch(cfg, log_file: Path | None = None, *, chave: str = "test_cmd"):
    """Gate: roda o teste no worktree. Vermelho = não avança. Timeout conta como vermelho.
    ``chave="baseline_cmd"`` roda o gate da linha de base, quando a op declara um próprio
    (v0.3.1): op que conserta o que o gate novo acusa — o mypy de uma versão nova de
    dependência, por exemplo — tem o `main` vermelho nele por definição."""
    ow = cfg["overwatch"]
    cmd = _fmt(ow.get(chave) or ow["test_cmd"], cfg)
    timeout = cfg["overwatch"].get("timeout_s", OVERWATCH_TIMEOUT_S)
    log("OWX", f"overwatch: {cmd}")
    rc, out = run(cmd, cwd=cfg["worktree"], timeout=timeout, log_file=log_file)
    return rc == 0, out


# --- v0.4: o aceite — a pós-condição escrita antes do voo, por quem não implementa ---------
RC_ACEITE_BASE = 9       # o aceite não falha na base (já passa, ou nem roda como teste)
RC_ACEITE_ENTREGA = 10   # o aceite foi mexido, ou não passou inteiro depois do operador
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


def instalar_aceite(cfg, op_dir: Path) -> tuple[bool, str, dict]:
    """Copia o aceite para a worktree e guarda o sha256 de cada arquivo. O aceite só CRIA
    arquivo: sobrescrever um teste que o alvo já tem mudaria o que a linha de base provou."""
    wt, hashes = Path(cfg["worktree"]), {}
    for rel, src in aceite_files(op_dir):
        if rel.startswith("/") or ".." in Path(rel).parts:
            return False, f"caminho de aceite inseguro: {rel}", {}
        dst, h = wt / rel, _sha256(src)
        if dst.exists() and _sha256(dst) != h:       # re-voo: o mesmo arquivo, já commitado, serve
            return False, f"{rel} já existe no alvo com outro conteúdo — o aceite só cria arquivo novo", {}
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        hashes[rel] = h
    return True, f"{len(hashes)} arquivo(s) de aceite na worktree", {"arquivos": hashes}


def rodar_aceite(cfg, log_file: Path | None = None) -> tuple[int, dict, str]:
    cmd = _fmt(cfg["aceite"]["cmd"], cfg)
    log("ACT", f"aceite: {cmd}")
    rc, out = run(cmd, cwd=cfg["worktree"],
                  timeout=cfg["overwatch"].get("timeout_s", OVERWATCH_TIMEOUT_S), log_file=log_file)
    return rc, contagem(out), out


def julgar_aceite_base(cfg, rc: int, c: dict) -> str | None:
    """Antes do operador o aceite tem de FALHAR, e falhar como teste. Devolve o motivo do abort,
    ou None. rc 0 = já passa, não prova nada novo; rc 2/4/5 do pytest = erro de coleta, de uso
    ou nenhum teste — um ImportError não é a prova de nada (por isso o import vai DENTRO da função)."""
    if rc == 0:
        return "o aceite já PASSA antes do operador — não prova nada que a op vá fazer"
    esperado = (cfg.get("aceite") or {}).get("base_rc", 1)
    if rc != esperado or c.get("erro_de_coleta") or not (c.get("failed", 0) + c.get("errors", 0)):
        return (f"o aceite não rodou como teste na base (rc {rc}, esperado {esperado} com falhas; "
                f"contagem {c}): erro de coleta ou de comando não é aceite")
    return None


def aceite_mexido(cfg, hashes: dict) -> list[str]:
    """Arquivos de aceite que mudaram ou sumiram desde a base."""
    wt = Path(cfg["worktree"])
    return [rel for rel, h in hashes.items()
            if not (wt / rel).exists() or _sha256(wt / rel) != h]


def julgar_aceite_entrega(rc: int, c: dict, coletados: int) -> str | None:
    """Depois do operador: tudo passa, a mesma quantidade da base, nada pulado. Um skip posto
    por fora (conftest, marcador global) passaria no rc e não na contagem."""
    pulados = {k: c[k] for k in _PULADOS if c.get(k)}
    if rc != 0 or c.get("failed") or c.get("errors"):
        return f"o aceite não passou depois do operador (rc {rc}, contagem {c})"
    if pulados:
        return f"o aceite passou com testes pulados ({pulados}) — pulado não é cumprido"
    if c.get("passed") != coletados:
        return f"o aceite rodou {c.get('passed')} teste(s), e a base coletou {coletados}"
    return None


COMMIT_PREFIXES = ("feat", "fix", "refactor", "chore", "docs", "test")   # conventional commits


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
# v0.3.2 — numa tarefa real o operador escreveu "não reporto 'verde' sem prova", honesto, e o detector
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
    """Onde o relatório do operador contradiz o gate do E-9 (v0.3). Em três tarefas seguidas o
    relatório disse "Gate verde" com cinco falhas, "ruff LIMPO" com o ruff vermelho e
    "418 passed" com o gate em 814. O gate é a prova; isto marca o que não bate."""
    achados = []
    if not gate_ok and _alega_verde(relatorio or ""):
        achados.append("o relatório alega verde/limpo, e o gate do E-9 deu VERMELHO")
    declarados = {int(n) for n in re.findall(r"(\d+) passed", relatorio or "")}
    if declarados and gate.get("passed") is not None and gate["passed"] not in declarados:
        achados.append(f"o relatório declara {sorted(declarados)} passed, e o gate deu "
                       f"{gate['passed']}")
    if relatorio and not re.search(r"^#+\s*Desvios da OPORD", relatorio, re.IGNORECASE | re.MULTILINE):
        achados.append("o relatório não tem a seção `## Desvios da OPORD` que o briefing exige")
    return achados


def commit_message(cfg, op_id, artifacts):
    """v0.2 — a mensagem vem do op.json (`commit.subject`, com prefixo semântico);
    até a v0.1.2 saía `feat(seed): ...` fixo para qualquer op. Sem `commit`, cai num
    `chore(e9): op <id>` neutro — nunca num prefixo que afirma o que a op não é."""
    c = cfg.get("commit") or {}
    subject = (c.get("subject") or f"chore(e9): op {op_id}").strip()
    body = (c.get("body") or "").strip()
    trailer = (f"Op {op_id} — operador E-9, overwatch verde.\n"
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
    # delega o MEIO (qual app hospeda o arquivo) ao operador; o gate exige que cada
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
    for pat in opt:                                    # opcionais (meio do operador): só se vierem
        resolved.extend(match(pat))
    resolved.extend(rel for rel in aceite if rel in changed)   # já commitado num re-voo: não muda
    resolved = sorted(set(resolved))
    # v0.2 — o overwatch testou a árvore INTEIRA da worktree; se o operador mexeu num
    # arquivo que nenhum padrão cobre, o commit levaria outra árvore, não a testada, e a
    # branch "verde" poderia nem compilar. Sobra = abort declarado, nunca commit parcial.
    leftover = sorted(set(changed) - set(resolved) - set(cfg.get("worktree_link", [])))
    if leftover:
        return False, ("arquivo(s) alterado(s) fora dos expected/optional_artifacts — a árvore "
                       f"testada não seria a commitada: {leftover}")
    rc, out = git(["add", "--", *resolved], cwd=wt)         # add SELETIVO dos resolvidos
    if rc != 0:
        return False, out
    msg = commit_message(cfg, op_id, resolved)
    # Identidade própria do E-9, inline (não depende de git config global da máquina).
    ident = cfg.get("commit_identity", {})
    name = ident.get("name", "E-9")
    email = ident.get("email", "e9@localhost")
    rc, out = git(["-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-m", msg], cwd=wt)
    # NOTA: nenhum `git push` aqui, por desenho (ROE) + guard no git().
    return (rc == 0), (out if rc != 0 else f"commitado por {name}: {', '.join(resolved)}")


def write_aar(op_dir: Path, op_id, results: dict):
    """Grava AAR.md + metrics.json na pasta da op."""
    (op_dir / "metrics.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    aar = op_dir / "AAR.md"
    stamp = results.get("finished_at", "")
    lines = [f"# AAR — Op {op_id} · {stamp}", "",
             "> Gerado pelo e9_run.py (preencher análise humana no briefing da manhã).", "",
             "## As 4 perguntas",
             f"1. O que devia acontecer? → ver OPORD (Mission/Intent).",
             f"2. O que aconteceu? → overwatch: **{'VERDE' if results.get('overwatch_pass') else 'VERMELHO'}**; "
             f"operador {'terminou ok' if results.get('operator_ok') else 'NÃO terminou ok'}.",
             "3. Por quê? → (humano)",
             "4. Sustain / Improve → (humano)", "",
             "## Medição da fábrica",
             f"- Disposition: branch `{results.get('branch')}` — "
             f"{'pronta p/ revisão' if results.get('committed') else 'NÃO commitada'}"
             + (f" @ `{results['commit_sha']}`" if results.get("commit_sha") else "") + ".",
             f"- Overwatch: {'pass' if results.get('overwatch_pass') else 'FAIL/abort'}.",
             f"- Status: {results.get('status')}."]
    m = results.get("operator") or {}
    if m:
        lines += [f"- Operador: rc={results.get('operator_rc')}"
                  + (" (TIMEOUT)" if results.get("operator_rc") == RC_TIMEOUT else "")
                  + f" · turnos={m.get('num_turns')} · custo≈US$ {m.get('total_cost_usd')}"
                  + f" · tokens in/out={m.get('input_tokens')}/{m.get('output_tokens')}"
                  + f" · duração={_min(m.get('duration_ms'))}"]
    if results.get("wall_clock_s") is not None:
        lines.append(f"- Wall-clock da op: {results['wall_clock_s'] / 60:.1f} min.")
    lines.append("")
    if m.get("result_text"):
        lines += ["## Relatório do operador (verbatim — desvios declarados, ROE §7)", "",
                  m["result_text"], ""]
    if results.get("gate") is not None:
        g = results["gate"]
        partes = [f"{k} {g[k]}" for k in ("ruff", "mypy") if k in g]
        partes += [f"{g[k]} {k}" for k in ("passed", "failed", "errors") if k in g]
        lines += ["## Gate do E-9 × relatório do operador", "",
                  f"- **Gate (a prova):** {'VERDE' if results.get('overwatch_pass') else 'VERMELHO'}"
                  + (f" — {', '.join(partes)}" if partes else "") + "."]
        diverg = results.get("divergencias") or []
        lines += [f"- ⚠️ **Divergência:** {d}." for d in diverg] or [
            "- O relatório do operador não contradiz o gate."]
        lines.append("")
    a = results.get("aceite")
    if a:
        lines += ["## Aceite × entrega (v0.4)", "",
                  f"- Arquivos: {', '.join(f'`{r}`' for r in a.get('arquivos', {}))}.",
                  f"- Na base (antes do operador): rc {a.get('base_rc')} · {a.get('base')} · "
                  f"{a.get('coletados')} coletado(s)."]
        if a.get("mexidos"):
            lines.append(f"- ⚠️ **Mexidos pelo operador:** {', '.join(a['mexidos'])}.")
        elif "entrega" in a:
            lines.append(f"- Depois do operador: rc {a.get('entrega_rc')} · {a.get('entrega')} · "
                         "hashes intactos.")
        if a.get("veredito"):
            lines.append(f"- **{a['veredito']}**")
        lines.append("")
    if results.get("overwatch_tail"):
        lines += ["## Overwatch (fim da saída)", "", "```", results["overwatch_tail"], "```", ""]
    lines += ["## Arquivos brutos (ficam na máquina, fora do git)",
              "- `operator.jsonl` — saída integral do operador (stream-json).",
              "- `overwatch.log` — saída integral do gate.", ""]
    aar.write_text("\n".join(lines), encoding="utf-8")


def _min(ms):
    return f"{ms / 60000:.1f} min" if isinstance(ms, (int, float)) else "—"


def main():
    ap = argparse.ArgumentParser(description="E-9 — executor de uma operação")
    ap.add_argument("--op", required=True, help="pasta da op (contém OPORD.md + op.json)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="ensaio: valida e planeja, não muta nada (default)")
    g.add_argument("--live", action="store_true", help="execução real (fora da produção)")
    args = ap.parse_args()
    dry = not args.live  # default seguro = ensaio

    op_dir = Path(args.op).resolve()
    e9_root = Path(__file__).resolve().parent.parent
    roe = e9_root / "doctrine" / "ROE-card.md"
    cfg, opord = load_op(op_dir)
    op_id = cfg.get("op", op_dir.name)
    alvo = alvo_path(cfg) if cfg.get("alvo") else None
    aceite = [rel for rel, _ in aceite_files(op_dir)] if cfg.get("aceite") else []
    brief = build_brief(opord, roe, cfg, alvo=alvo, aceite=aceite)

    sitrep([f"E-9 · OP {op_id} · modo {'ENSAIO (dry-run)' if dry else 'LIVE'}",
            f"AO     : {cfg['target_repo']} @ {cfg['branch']}",
            f"Worktree: {cfg['worktree']}",
            f"Operador: model={cfg['operator'].get('model')} · cmd no op.json",
            f"Overwatch: {cfg['overwatch']['test_cmd']}",
            f"Artefatos: {', '.join(cfg.get('expected_artifacts', [])) or '—'}",
            f"Commit  : {commit_message(cfg, op_id, ['…']).splitlines()[0]}",
            f"Tetos   : operador {cfg['operator'].get('timeout_s', OPERATOR_TIMEOUT_S)}s · "
            f"overwatch {cfg['overwatch'].get('timeout_s', OVERWATCH_TIMEOUT_S)}s",
            f"Base    : {'desligada' if cfg['overwatch'].get('baseline') is False else (cfg['overwatch'].get('baseline_cmd') or 'o mesmo gate')}",
            f"Aceite  : {', '.join(aceite) + ' · ' + cfg['aceite']['cmd'] if aceite else '—'}",
            f"Travas  : {alvo.name + ' (todas citadas na OPORD)' if alvo else '—'}",
            f"Briefing: {len(brief)} chars (OPORD + ROE" + (" + travas" if alvo else "")
            + (" + aceite" if aceite else "") + ")"])

    if dry:
        log("ENSAIO", "validação OK. Plano: worktree → linha de base → operador → overwatch → commit(branch) → AAR.")
        log("ENSAIO", "Nada foi mutado. Para valer: --live.")
        return 0

    # ── LIVE ──────────────────────────────────────────────────────────────
    results = {"op": op_id, "branch": cfg["branch"], "started_at": datetime.now().isoformat()}
    ok, out = setup_worktree(cfg)
    if not ok:
        log("ABORT", f"worktree falhou:\n{out}")
        return 2
    brief_file = op_dir / "_brief.txt"
    brief_file.write_text(brief, encoding="utf-8")

    t0 = time.monotonic()

    def finish(status):
        results["status"] = status
        results["finished_at"] = datetime.now().isoformat()
        results["wall_clock_s"] = round(time.monotonic() - t0, 1)
        write_aar(op_dir, op_id, results)

    # v0.3 — linha de base: o gate roda na worktree recém-cortada, ANTES do operador. Na Op
    # 009 o `main` já nascia vermelho no ruff (herança de outro commit), e o operador voou 45 min
    # para o gate acusar um erro que não era dele. Worktree suja (sobra de um voo anterior) também
    # não serve de base: o que se testaria não seria o `main`.
    if cfg["overwatch"].get("baseline", True):
        rc, sujo = git(["status", "--porcelain", "-uall"], cwd=cfg["worktree"])
        sobras = [ln for ln in sujo.splitlines()
                  if ln.strip() and ln[3:].strip() not in cfg.get("worktree_link", [])]
        if rc != 0 or sobras:
            results["committed"] = False
            finish(f"ABORT — worktree não está limpa antes do voo: {sobras[:10] or sujo[:300]}")
            log("SITREP", "WORKTREE SUJA — a linha de base não seria o main. Abort antes do operador.")
            return 8
        log("OWX", "linha de base: o gate na worktree recém-cortada, antes do operador")
        base_ok, base_out = run_overwatch(cfg, op_dir / "baseline.log", chave="baseline_cmd")
        results["baseline_pass"] = base_ok
        results["baseline_gate"] = gate_summary(base_out)
        if not base_ok:
            results["committed"] = False
            results["overwatch_tail"] = base_out.strip()[-1500:]
            finish("ABORT — linha de base VERMELHA: o gate já falha no main, antes do operador "
                   "(nada foi despachado)")
            log("SITREP", "LINHA DE BASE VERMELHA — operador não despachado. AAR gravado.")
            return 7

    # v0.4 — o aceite entra DEPOIS da base (o `main` é verde sem ele) e tem de falhar ANTES do
    # operador: teste que já passa, ou que nem roda, não prova o que a op vai fazer.
    if aceite:
        ok, msg, info = instalar_aceite(cfg, op_dir)
        results["aceite"] = info
        if not ok:
            results["committed"] = False
            info["veredito"] = msg
            finish(f"ABORT — aceite não instalado: {msg}")
            log("SITREP", f"ACEITE NÃO INSTALADO — {msg}")
            return RC_ACEITE_BASE
        log("ACT", msg)
        rc_a, c, _ = rodar_aceite(cfg, op_dir / "aceite-base.log")
        info.update({"base_rc": rc_a, "base": c,
                     "coletados": c.get("passed", 0) + c.get("failed", 0) + c.get("errors", 0)})
        motivo = julgar_aceite_base(cfg, rc_a, c)
        if motivo:
            results["committed"] = False
            info["veredito"] = motivo
            finish(f"ABORT — {motivo} (nada foi despachado)")
            log("SITREP", f"ACEITE NÃO FALHA NA BASE — {motivo}")
            return RC_ACEITE_BASE
        log("ACT", f"aceite falha na base como devia: {c}")

    results["operator_ok"], op_out, results["operator_rc"] = dispatch_operator(
        cfg, brief_file, op_dir / "operator.jsonl")
    results["operator"] = parse_operator_output(op_out)
    restos = matar_restos(cfg["worktree"])
    if restos:
        results["restos_mortos"] = restos
        log("OPR", f"{len(restos)} processo(s) do operador ainda rodando na worktree — mortos antes do gate")
    if results["operator"].get("is_error"):
        # Visto em 24/10: OAuth vencido → `claude -p` sai com rc=0 e is_error=true. O rc
        # sozinho mente; quem diz se o operador trabalhou é o evento `result`.
        results["operator_ok"] = False
        results["committed"] = False
        finish("ABORT — operador terminou em erro (is_error): "
               + results["operator"].get("result_text", "")[:300])
        log("SITREP", f"OPERADOR EM ERRO — {results['operator'].get('result_text', '')[:200]}")
        return 6
    if results["operator_rc"] == RC_TIMEOUT:
        # Operador que estoura o teto não chega ao gate: o que ele deixou pela metade não é
        # entrega. Branch fica como está, para o Comandante olhar.
        results["committed"] = False
        finish("ABORT — operador estourou o teto de tempo (TIMEOUT), sem overwatch nem commit")
        log("SITREP", "OPERADOR EM TIMEOUT — abort. AAR gravado. Sem push.")
        return 5

    if aceite:
        mexidos = aceite_mexido(cfg, results["aceite"]["arquivos"])
        if mexidos:
            # Abort antes do gate: com a prova alterada, o verde do gate não diria nada.
            results["committed"] = False
            results["aceite"]["mexidos"] = mexidos
            results["aceite"]["veredito"] = "o operador mexeu no aceite — abort mesmo que o resto esteja verde"
            finish(f"ABORT — aceite alterado pelo operador: {mexidos}")
            log("SITREP", f"ACEITE MEXIDO — {mexidos}. Branch fica p/ revisão, sem commit.")
            return RC_ACEITE_ENTREGA

    results["overwatch_pass"], ow_out = run_overwatch(cfg, op_dir / "overwatch.log")
    results["overwatch_tail"] = ow_out.strip()[-1500:]
    results["gate"] = gate_summary(ow_out)
    results["divergencias"] = divergencias(
        results["overwatch_pass"], results["gate"], results["operator"].get("result_text", ""))
    for d in results["divergencias"]:
        log("OWX", f"DIVERGÊNCIA: {d}")

    if not results["overwatch_pass"]:
        results["committed"] = False
        finish("ABORT — overwatch vermelho (branch deixada p/ revisão, sem commit-como-concluído)")
        log("SITREP", "OVERWATCH VERMELHO — abort. AAR gravado. Sem push.")
        print(ow_out[-1500:])
        return 3

    if aceite:
        a = results["aceite"]
        a["entrega_rc"], a["entrega"], _ = rodar_aceite(cfg, op_dir / "aceite-entrega.log")
        motivo = julgar_aceite_entrega(a["entrega_rc"], a["entrega"], a["coletados"])
        if motivo:
            results["committed"] = False
            a["veredito"] = motivo
            finish(f"ABORT — gate verde, mas {motivo}")
            log("SITREP", f"ACEITE NÃO CUMPRIDO — {motivo}")
            return RC_ACEITE_ENTREGA
        a["veredito"] = f"cumprido: {a['coletados']} teste(s) de aceite passaram, nenhum mexido nem pulado"

    results["committed"], commit_out = stage_and_commit(cfg, op_id, aceite)
    if not results["committed"]:
        # Overwatch passou mas o commit não fechou (artefato faltando, etc.) — não maquiar.
        finish(f"ABORT — overwatch verde, mas commit falhou: {commit_out}")
        log("SITREP", f"COMMIT NÃO FECHOU — {commit_out}")
        return 4
    rc, sha = git(["rev-parse", "--short", "HEAD"], cwd=cfg["worktree"])
    results["commit_sha"] = sha.strip() if rc == 0 else None
    finish("OK — overwatch verde, commitado na branch (aguarda revisão do Comandante)")
    sitrep([f"OP {op_id}: VERDE.",
            f"Branch {cfg['branch']} commitada (SEM push).",
            "AAR + metrics gravados. Aguarda luz verde do Comandante p/ merge/deploy."])
    return 0


if __name__ == "__main__":
    sys.exit(main())
