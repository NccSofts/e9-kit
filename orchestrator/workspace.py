#!/usr/bin/env python3
"""
workspace.py — o ambiente próprio e descartável de cada run (v0.7), opcional.

Sem isto, dois runs no mesmo repositório disputam o mesmo banco de teste e inventam falhas um para o
outro. A op que declara `"workspace": {"banco": "<receita>"}` roda com um Postgres só dela, num
container (receita em `targets/<receita>.workspace.json`):

  - `--rm`, dados em tmpfs, sem fsync: morre inteiro no fim, e não precisa de durabilidade;
  - porta efêmera em 127.0.0.1, senhas geradas por run, só em memória;
  - o papel da aplicação SEM superuser, como na produção (senão um defeito de permissão fica escondido);
  - rótulos `e9.*` no container: a faxina do começo de todo run remove o que um harness morto deixou.

O resto continua no host: a worktree já é uma por op, e o venv é só leitura. Junto vêm a trava por op
(a mesma op duas vezes ao mesmo tempo é rc 11), os slots de run (no máximo E9_MAX_VOOS, padrão 2) e,
se você quiser, a espera pela máquina (E9_PRIORITARIOS: um desses processos rodando, o run cede a vez).

Stdlib apenas. O `docker` é trocável por E9_DOCKER (os testes põem um falso no lugar).
"""
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RC_OP_EM_VOO = 11        # a mesma op já está voando
RC_BANCO = 12            # o banco do voo não subiu ou não responde: nada é despachado
ROTULO = "e9.voo"
# Processos que têm a vez na máquina: com um deles rodando, o run espera (até o teto). Lista vazia
# por padrão; declare em E9_PRIORITARIOS, separados por vírgula (ex.: "ffmpeg,blender").
PRIORITARIOS = tuple(n.strip() for n in os.environ.get("E9_PRIORITARIOS", "").split(",") if n.strip())
PRONTO_TETO_S = 90


def _docker() -> list[str]:
    v = os.environ.get("E9_DOCKER", "docker")
    return json.loads(v) if v.startswith("[") else [v]


def _dk(args, *, entrada=None, env=None, timeout=120) -> tuple[int, str]:
    try:
        p = subprocess.run([*_docker(), *args], input=entrada, capture_output=True, text=True,
                           env=env, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, f"{type(e).__name__}: {e}"
    return p.returncode, (p.stdout + p.stderr).strip()


# --- receita do alvo -----------------------------------------------------------------------

def receita(cfg, targets_dir: Path) -> dict | None:
    """A receita do banco (`targets/<banco>.workspace.json`), ou None se a op não pede workspace.
    Levanta ValueError se a op pede e a receita não serve — o `load_op` transforma em abort."""
    nome = ((cfg.get("workspace") or {}).get("banco") or "").strip()
    if not nome:
        return None
    path = Path(targets_dir) / f"{nome}.workspace.json"
    if not path.exists():
        raise ValueError(f"{path} não existe — workspace pedido sem a receita do banco")
    r = json.loads(path.read_text(encoding="utf-8"))
    for chave in ("imagem", "init_sql", "papel", "banco_sonda", "env"):
        if not r.get(chave):
            raise ValueError(f"{path.name} sem `{chave}`")
    sql = Path(targets_dir) / r["init_sql"]
    if not sql.exists():
        raise ValueError(f"{sql} não existe")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", r["papel"]):
        raise ValueError(f"papel inválido: {r['papel']!r}")
    r["_sql"] = str(sql.resolve())
    return r


def _slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", texto.lower()).strip("-")[:40] or "op"


# --- subir, conferir, derrubar ---------------------------------------------------------------

def subir(cfg, rec: dict) -> tuple[dict | None, str]:
    """Sobe o Postgres do voo. Devolve (ws, msg); ws None = não subiu. O `ws` leva o nome do
    container, a porta e o ambiente que o agente, a base, o gate e o aceite recebem."""
    voo = secrets.token_hex(4)
    nome = f"e9-{_slug(str(cfg.get('op', 'op')))}-{voo}"
    super_pw, papel_pw = secrets.token_hex(16), secrets.token_hex(16)
    args = ["run", "-d", "--rm", "--name", nome,
            "--label", f"{ROTULO}={voo}", "--label", f"e9.op={cfg.get('op', '')}",
            "--label", f"e9.pid={os.getpid()}",
            "--label", f"e9.inicio={datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            "-e", "POSTGRES_PASSWORD",            # o valor vem do ambiente do subprocesso, não da linha
            "-e", "TZ=America/Sao_Paulo",
            "-p", "127.0.0.1::5432",
            "--tmpfs", f"/var/lib/postgresql/data:rw,size={int(rec.get('tmpfs_mb', 2048))}m",
            "-v", f"{rec['_sql']}:/docker-entrypoint-initdb.d/01-e9.sql:ro"]
    if rec.get("cpus"):
        args += ["--cpus", str(rec["cpus"])]
    args += [rec["imagem"], "-c", "fsync=off", "-c", "synchronous_commit=off",
             "-c", "full_page_writes=off"]
    rc, out = _dk(args, env={**os.environ, "POSTGRES_PASSWORD": super_pw})
    if rc != 0:
        return None, f"docker run falhou (rc {rc}): {out[-300:]}"
    ws = {"container": nome, "voo": voo, "papel": rec["papel"], "banco": rec["banco_sonda"],
          "_super": super_pw, "_senha": papel_pw}
    rc, out = _dk(["port", nome, "5432/tcp"])
    m = re.search(r"127\.0\.0\.1:(\d+)", out)
    if rc != 0 or not m:
        derrubar(ws)
        return None, f"porta do container não publicada: {out[-200:]}"
    ws["porta"] = int(m.group(1))
    ws["env"] = {k: v.format(senha=papel_pw, porta=ws["porta"]) for k, v in rec["env"].items()}
    return ws, f"{nome} em 127.0.0.1:{ws['porta']}"


def pronto(ws: dict, teto_s: float | None = None, intervalo: float = 2.0) -> tuple[bool, str]:
    """O banco responde de verdade: psql por TCP dentro do container (durante o init a imagem sobe
    um servidor provisório só no socket local — o TCP só responde no definitivo), a senha do papel
    trocada, o papel conecta no banco da sonda, e a porta abre no host.

    É a guarda contra o skip silencioso: há suíte cujo `conftest.py` PULA os testes de banco quando a
    URL não conecta. Banco mal ligado daria gate verde com metade da suíte pulada."""
    if teto_s is None:
        teto_s = float(os.environ.get("E9_PRONTO_TETO_S", PRONTO_TETO_S))
    fim = time.monotonic() + teto_s
    out = ""
    while time.monotonic() < fim:
        rc, out = _dk(["exec", "-e", "PGPASSWORD", ws["container"], "psql", "-h", "127.0.0.1",
                       "-U", "postgres", "-tAc", "select 1"],
                      env={**os.environ, "PGPASSWORD": ws["_super"]}, timeout=20)
        if rc == 0 and out.strip().endswith("1"):
            break
        time.sleep(intervalo)
    else:
        return False, f"o Postgres do voo não respondeu em {teto_s:.0f}s: {out[-200:]}"
    sql = f"ALTER ROLE {ws['papel']} PASSWORD '{ws['_senha']}';"     # pelo stdin, nunca na linha
    rc, out = _dk(["exec", "-i", "-e", "PGPASSWORD", ws["container"], "psql", "-h", "127.0.0.1",
                   "-U", "postgres", "-v", "ON_ERROR_STOP=1", "-q"],
                  entrada=sql, env={**os.environ, "PGPASSWORD": ws["_super"]}, timeout=30)
    if rc != 0:
        return False, f"não deu para pôr a senha do papel {ws['papel']}: {out[-200:]}"
    rc, out = _dk(["exec", "-e", "PGPASSWORD", ws["container"], "psql", "-h", "127.0.0.1",
                   "-U", ws["papel"], "-d", ws["banco"], "-tAc",
                   "select current_user, (select count(*) from pg_extension where extname='vector')"],
                  env={**os.environ, "PGPASSWORD": ws["_senha"]}, timeout=20)
    if rc != 0:
        return False, f"o papel {ws['papel']} não conecta em {ws['banco']}: {out[-200:]}"
    try:
        with socket.create_connection(("127.0.0.1", ws["porta"]), timeout=5):
            pass
    except OSError as e:
        return False, f"a porta {ws['porta']} não abre no host: {e}"
    return True, f"{ws['papel']}@{ws['banco']} responde ({out.strip()})"


def derrubar(ws: dict | None) -> str:
    if not ws:
        return "nada a derrubar"
    rc, out = _dk(["rm", "-f", ws["container"]], timeout=60)
    return f"{ws['container']} removido" if rc == 0 else f"falha ao remover {ws['container']}: {out[-200:]}"


TARJA = "<senha-do-voo>"


def segredos(ws: dict | None) -> list[str]:
    return [s for s in ((ws or {}).get("_senha"), (ws or {}).get("_super")) if s]


def tarjar(obj, segs: list[str] | None):
    """Troca as senhas do voo por TARJA em texto, lista ou dict. O pytest imprime o argumento de
    uma asserção que falha — a URL do banco, com a senha — e a saída do gate vai ao run report e ao
    metrics.json, que entram no git (visto num voo de prova)."""
    if not segs:
        return obj
    if isinstance(obj, str):
        for s in segs:
            obj = obj.replace(s, TARJA)
        return obj
    if isinstance(obj, list):
        return [tarjar(x, segs) for x in obj]
    if isinstance(obj, dict):
        return {k: tarjar(v, segs) for k, v in obj.items()}
    return obj


def tarjar_logs(pasta: Path, segs: list[str] | None) -> None:
    """O mesmo nos brutos que ficam na máquina (logs do gate e do aceite, transcript do agente)."""
    if not segs:
        return
    for p in [*Path(pasta).glob("*.log"), Path(pasta) / "agent.jsonl", Path(pasta) / "_brief.txt"]:
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        novo = tarjar(t, segs)
        if novo != t:
            p.write_text(novo, encoding="utf-8")


def publico(ws: dict | None) -> dict | None:
    """O que vai para o metrics.json: nunca as senhas nem o ambiente (a URL leva a senha)."""
    if not ws:
        return None
    return {k: ws[k] for k in ("container", "voo", "porta", "papel", "banco") if k in ws} | {
        "variaveis": sorted((ws.get("env") or {}).keys())}


# --- faxina: o que um harness morto deixou ---------------------------------------------------

def _vivo(pid: int) -> bool:
    """O pid ainda é um e9_run.py? Pid reaproveitado por outro programa conta como morto."""
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    return b"e9_run" in cmd or b"test_e9_run" in cmd


def faxina() -> list[str]:
    """Remove os containers do E-9 cujo harness morreu (kill -9, queda do ssh sem SIGHUP). Só no
    Linux: sem /proc não há como saber se o dono vive, e então nada é removido."""
    if not Path("/proc").is_dir():
        return []
    rc, out = _dk(["ps", "-a", "--filter", f"label={ROTULO}",
                   "--format", '{{.Names}} {{.Label "e9.pid"}}'])
    if rc != 0:
        return []
    removidos = []
    for ln in out.splitlines():
        partes = ln.split()
        if len(partes) != 2 or not partes[1].isdigit():
            continue
        nome, pid = partes[0], int(partes[1])
        if pid != os.getpid() and not _vivo(pid):
            if _dk(["rm", "-f", nome], timeout=60)[0] == 0:
                removidos.append(nome)
    return removidos


# --- trava por op e slots de voo -------------------------------------------------------------

def _trancar(path: Path):
    """flock (ou msvcrt no Windows) sem esperar: devolve o arquivo aberto, ou None se já trancado.
    O sistema solta a trava quando o processo morre, inclusive com kill -9."""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+")
    try:
        if os.name == "posix":
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        return None
    return f


def soltar(f) -> None:
    if f is not None:
        try:
            f.close()
        except OSError:
            pass


def travar_op(op_dir: Path):
    return _trancar(Path(op_dir) / ".voo.lock")


def _slots_dir() -> Path:
    return Path(os.environ.get("E9_SLOTS_DIR") or Path.home() / ".e9-slots")


def pegar_slot(teto_s: float | None = None, intervalo: float = 30.0, avisar=None):
    """Um dos E9_MAX_VOOS slots (padrão 2). Espera até `teto_s` (E9_SLOT_TETO_S, padrão 6 h);
    devolve (arquivo, n) ou (None, -1)."""
    n_max = max(1, int(os.environ.get("E9_MAX_VOOS", "2")))
    if teto_s is None:
        teto_s = float(os.environ.get("E9_SLOT_TETO_S", 6 * 3600))
    fim, avisado = time.monotonic() + teto_s, False
    while True:
        for i in range(n_max):
            f = _trancar(_slots_dir() / f"slot-{i}.lock")
            if f is not None:
                return f, i
        if time.monotonic() >= fim:
            return None, -1
        if avisar and not avisado:
            avisar(f"os {n_max} slots de voo estão ocupados — esperando a vez")
            avisado = True
        time.sleep(intervalo)


# --- a máquina é de mais gente ---------------------------------------------------------------

def maquina_ocupada(proc: Path = Path("/proc")) -> str | None:
    """Um processo de `E9_PRIORITARIOS` rodando: o trabalho pesado da máquina tem a vez. Pela carga
    não: numa máquina dividida, a CPU alta é a regra, e esperar por ela travaria todo run."""
    if os.environ.get("E9_SEM_ESPERA") or not proc.is_dir():
        return None
    for d in proc.iterdir():
        if d.name.isdigit():
            try:
                nome = (d / "comm").read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if nome in PRIORITARIOS:
                return f"{nome} rodando"
    return None


def esperar_maquina(teto_s: float = 3600, intervalo: float = 60.0) -> dict | None:
    """Espera a máquina desocupar, até `teto_s`. Devolve o registro da espera, ou None se não
    precisou. Estourou o teto: segue assim mesmo (terminar devagar é melhor que perder o voo)."""
    motivo = maquina_ocupada()
    if not motivo:
        return None
    t0 = time.monotonic()
    print(f"[E-9 ESP] {motivo} — o voo cede a vez (até {teto_s / 60:.0f} min)", flush=True)
    while motivo and time.monotonic() - t0 < teto_s:
        time.sleep(intervalo)
        motivo = maquina_ocupada()
    return {"motivo": maquina_ocupada() or "desocupou", "esperou_s": round(time.monotonic() - t0),
            "estourou": bool(motivo)}


if __name__ == "__main__":       # faxina à mão: python orchestrator/workspace.py --faxina
    if "--faxina" in sys.argv:
        print("\n".join(faxina()) or "nada a remover")
