#!/usr/bin/env python3
"""
reviewer.py — o AI code review de outra família de modelo (v0.6), opcional.

O ponto cego medido do harness é o gate verde com o desenho errado: o teste prova que nada quebrou,
não que o pedido foi feito. Este módulo pede a um modelo da OpenAI, pelo OpenRouter, que leia a spec,
os guardrails e o diff da entrega e responda se aquilo faz o que foi pedido. Ele erra diferente do
agente, que é Claude.

- **Só informa.** O parecer vai ao run report e ao metrics.json; não muda o rc. Na calibração ele
  acertou um defeito numa tarefa e deixou passar o mesmo defeito na seguinte: é pista, não prova.
- **Mascarado e sem retenção.** CNJ, CNPJ, CPF e e-mail saem do diff trocados por marcadores antes do
  envio, e o pedido exige provedor ZDR (`provider.zdr` + `data_collection: "deny"`). Se nenhum
  provedor atender, o revisor fica "indisponível" — **nunca** se repete sem a restrição. A sobrecarga
  do provedor ("high demand", "rate-limited", HTTP 429) repete o MESMO pedido, até duas vezes.
- **Modelo:** `openai/gpt-6-astra` por padrão; o op.json troca com `reviewer.modelo`.

O relatório do agente NÃO vai ao revisor, de propósito: o segundo olhar não parte da conclusão do
primeiro. Arquivo ligado à worktree (`.env`) e qualquer `.env*` nunca entram no diff; arquivo apagado
vai só pelo nome.

A chave fica no `.env` da raiz do kit (fora do git): `E9_REVISOR_KEY`, uma chave do OpenRouter com
teto de crédito. Sem ela, o run grava "sem chave" e segue. `E9_SEM_REVISOR=1` desliga (os testes
usam), e `"reviewer": false` no op.json também.

Revisão de um commit já gravado (grava `ops/<op>/reviewer-calibracao.json`):
  python3 orchestrator/reviewer.py --op ops/<op> --repo <clone do target> --commit <sha>

Stdlib apenas.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compat  # noqa: E402 — os nomes antigos das ops de antes da v0.9

try:
    sys.stdout.reconfigure(encoding="utf-8")      # console do Windows é cp1252
except Exception:
    pass

RAIZ = Path(__file__).resolve().parent.parent
URL = "https://openrouter.ai/api/v1/chat/completions"
MODELO_PADRAO = "openai/gpt-6-astra"
TIMEOUT_S = 600
LIMITE_DIFF = 600_000          # caracteres; o modelo aceita ~1M tokens, mas o custo cresce junto
VEREDITOS = ("APROVA", "RESSALVA", "REPROVA")
SEVERIDADES = ("alta", "media", "baixa")

# Ordem importa: o CNJ (20 dígitos) antes do CNPJ (14), e este antes do CPF (11), para o padrão
# menor não comer um pedaço do maior. Mascarar a mais é barato; mascarar a menos é o vazamento.
MASCARAS = (
    ("CNJ", re.compile(r"(?<!\d)\d{7}-?\d{2}\.?\d{4}\.?\d\.?\d{2}\.?\d{4}(?!\d)")),
    ("CNPJ", re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)")),
    ("CPF", re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")),
    # O lookbehind não é enfeite: sem ele, uma sequência longa sem `@` (linha minificada) faz o
    # padrão recomeçar em cada posição — custo quadrático, e o teste de 600 mil caracteres travou.
    ("EMAIL", re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
)

ESQUEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["veredito", "resumo", "achados"],
    "properties": {
        "veredito": {"type": "string", "enum": list(VEREDITOS)},
        "resumo": {"type": "string"},
        "achados": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["severidade", "arquivo", "linha", "problema", "por_que"],
                "properties": {
                    "severidade": {"type": "string", "enum": list(SEVERIDADES)},
                    "arquivo": {"type": "string"},
                    "linha": {"type": "integer"},
                    "problema": {"type": "string"},
                    "por_que": {"type": "string"},
                },
            },
        },
    },
}

INSTRUCAO = """Você é o REVISOR de uma entrega de código feita por outro agente (um agente).
Responda à pergunta: esta entrega faz o que a spec pede, sem quebrar as travas do repositório?

O portão automático (lint, tipos, suíte de testes) JÁ passou — não repita o trabalho dele, e não
comente estilo, nomes ou formatação. Procure o que o portão não vê:
- o pedido da spec não cumprido, cumprido pela metade ou trocado por outra coisa;
- defeito de desenho: o código faz o que os testes dele afirmam, mas não o que a spec quer;
- permissão, autorização e escopo de dados (quem pode chamar o quê, o que vaza entre clientes);
- tratamento de erro largo demais, que esconde ou rotula errado uma falha;
- trava do repositório violada;
- caso de borda que a spec nomeia e o diff não cobre.

Cada achado aponta arquivo e linha do diff (linha 0 se não couber) e diz por que é problema, citando a
spec ou a trava quando for o caso. Sem achado, a lista fica vazia. Veredito: APROVA (nada a corrigir),
RESSALVA (corrigir antes do merge, mas a entrega serve), REPROVA (não faz o que foi pedido).
Marcadores como <CNJ>, <CNPJ>, <CPF> e <EMAIL> são dados mascarados de propósito, não defeito.
Escreva em português."""


def mascarar(texto: str) -> tuple[str, dict]:
    """Troca os dados pessoais e de processo por marcadores. Devolve o texto e a contagem por tipo."""
    contagem = {}
    for nome, padrao in MASCARAS:
        texto, n = padrao.subn(f"<{nome}>", texto)
        if n:
            contagem[nome] = n
    return texto, contagem


def _proibido(rel: str, ligados=()) -> bool:
    nome = rel.rsplit("/", 1)[-1]
    return rel in ligados or nome == ".env" or nome.startswith(".env.")


def _git(args, cwd) -> tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=cwd, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout + p.stderr


def diff_da_worktree(worktree, base: str, ligados=()) -> str:
    """O que mudou na worktree em relação a `base`, commitado ou não, mais os arquivos novos ainda
    fora do índice. Arquivo ligado (`worktree_link`) e `.env*` ficam de fora sempre. Arquivo apagado
    vai só pelo nome (`--irreversible-delete`): num run real, 22 referências HTML apagadas fizeram o
    diff passar de 600 mil caracteres, e o provedor recusou o pedido."""
    fora = [f":(exclude){p}" for p in ligados] + [":(exclude,glob)**/.env", ":(exclude,glob)**/.env.*"]
    rc, out = _git(["diff", "--no-color", "--no-ext-diff", "--irreversible-delete", base, "--", ".",
                    *fora], worktree)
    if rc != 0:
        raise RuntimeError(f"git diff falhou: {out[:300]}")
    partes = [out]
    rc, novos = _git(["ls-files", "--others", "--exclude-standard"], worktree)
    for rel in (n.strip() for n in novos.splitlines() if rc == 0):
        if not rel or _proibido(rel, ligados):
            continue
        caminho = Path(worktree) / rel
        try:
            if caminho.is_symlink() or caminho.stat().st_size > 300_000:
                continue
            conteudo = caminho.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        linhas = conteudo.splitlines()
        partes.append(f"diff --git a/{rel} b/{rel}\nnew file\n--- /dev/null\n+++ b/{rel}\n"
                      f"@@ -0,0 +1,{len(linhas)} @@\n" + "".join(f"+{ln}\n" for ln in linhas))
    return "\n".join(p for p in partes if p.strip())


def diff_do_commit(repo, sha: str) -> str:
    """O diff de um commit já gravado (a calibração usa: o commit do agente no alvo)."""
    rc, out = _git(["show", "--no-color", "--no-ext-diff", "--irreversible-delete", "--format=", sha, "--",
                    ".", ":(exclude,glob)**/.env", ":(exclude,glob)**/.env.*"], repo)
    if rc != 0:
        raise RuntimeError(f"git show falhou: {out[:300]}")
    return out


def montar_pedido(modelo: str, opord: str, travas: str, diff: str, gate: dict | None) -> tuple[dict, dict]:
    """O corpo do pedido ao OpenRouter, já mascarado. Devolve (corpo, info) — info diz o que foi
    mascarado e se o diff foi cortado. O relatório do agente não entra."""
    cortado = len(diff) > LIMITE_DIFF
    if cortado:
        diff = diff[:LIMITE_DIFF]
    numeros = ", ".join(f"{k} {v}" for k, v in (gate or {}).items()) or "sem resumo"
    conteudo = ("# spec (o pedido)\n\n" + opord
                + "\n\n# TRAVAS DO REPOSITÓRIO\n\n" + (travas or "(nenhuma declarada)")
                + f"\n\n# PORTÃO AUTOMÁTICO\n\nPassou: {numeros}."
                + ("\n\n⚠️ O diff abaixo foi CORTADO no limite de tamanho; diga isso no resumo."
                   if cortado else "")
                + "\n\n# DIFF DA ENTREGA\n\n```diff\n" + diff + "\n```\n")
    conteudo, mascarados = mascarar(conteudo)
    corpo = {
        "model": modelo,
        "messages": [{"role": "system", "content": INSTRUCAO},
                     {"role": "user", "content": conteudo}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "parecer", "strict": True, "schema": ESQUEMA}},
        # ZDR e sem coleta; require_parameters para só cair em provedor que honra o esquema.
        # Sem fallback para provedor que não atenda: aí o pedido falha, e falhar é o certo.
        "provider": {"zdr": True, "data_collection": "deny", "require_parameters": True},
    }
    return corpo, {"mascarados": mascarados, "diff_cortado": cortado,
                   "tamanho": len(conteudo)}


def ler_parecer(resposta: dict) -> dict:
    """Valida a resposta contra o esquema, à mão (stdlib). Levanta ValueError se não servir."""
    try:
        texto = resposta["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        # O OpenRouter pode responder 200 com `error` em vez de `choices` (o provedor recusou
        # depois de aceitar o pedido). Sem isto, o motivo sumia — foi o que aconteceu num run real.
        erro = resposta.get("error") if isinstance(resposta, dict) else None
        detalhe = (erro.get("message") if isinstance(erro, dict) else erro) or ""
        raise ValueError("resposta sem choices[0].message.content"
                         + (f" — {str(detalhe)[:200]}" if detalhe else ""))
    p = json.loads(texto) if isinstance(texto, str) else texto
    if not isinstance(p, dict) or p.get("veredito") not in VEREDITOS:
        raise ValueError(f"veredito fora do esquema: {str(p)[:200]}")
    achados = []
    for a in p.get("achados") or []:
        if not isinstance(a, dict) or a.get("severidade") not in SEVERIDADES:
            raise ValueError(f"achado fora do esquema: {str(a)[:200]}")
        achados.append({"severidade": a["severidade"], "arquivo": str(a.get("arquivo", "")),
                        "linha": int(a.get("linha") or 0), "problema": str(a.get("problema", "")),
                        "por_que": str(a.get("por_que", ""))})
    return {"veredito": p["veredito"], "resumo": str(p.get("resumo", "")), "achados": achados}


def _env() -> dict:
    vals = {}
    arq = RAIZ / ".env"
    if arq.exists():
        for linha in arq.read_text(encoding="utf-8").splitlines():
            if "=" in linha and not linha.lstrip().startswith("#"):
                k, v = linha.split("=", 1)
                vals[k.strip()] = v.strip()
    return vals


def chamar(corpo: dict, chave: str, timeout: int = TIMEOUT_S) -> dict:
    pedido = urllib.request.Request(
        URL, method="POST", data=json.dumps(corpo).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {chave}",
                 "X-OpenRouter-Title": "E-9 revisor"})
    with urllib.request.urlopen(pedido, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


# Sobrecarga do provedor ("high demand"): vista num run real e em três chamadas seguidas da
# calibração dela — o mesmo pedido passava minutos depois. Repete-se SÓ esse caso, com o MESMO
# pedido (ZDR exigido): a regra da v0.6 proíbe tentar de novo sem ZDR, não tentar de novo com ele.
ESPERAS_SOBRECARGA_S = (30, 90)


def _sobrecarga(resposta: dict) -> bool:
    erro = resposta.get("error") if isinstance(resposta, dict) else None
    texto = str(erro.get("message") if isinstance(erro, dict) else erro or "")
    texto = texto.lower()
    # A segunda forma: "temporarily rate-limited upstream. Please retry". A terceira:
    # HTTP 429 "could not verify available credits for this request in time. Retry shortly".
    return not resposta.get("choices") and any(
        s in texto for s in ("high demand", "rate-limited", "retry shortly"))


def _chamar_sem_429(chamador, corpo: dict, chave: str) -> dict:
    """O 429 do OpenRouter vem como HTTPError, não como corpo com `error`; aqui ele vira o corpo,
    para a mesma regra de sobrecarga decidir se repete. Outro código de HTTP segue como exceção."""
    try:
        return chamador(corpo, chave)
    except urllib.error.HTTPError as e:
        if e.code != 429:
            raise
        try:
            detalhe = json.loads(e.read() or b"{}").get("error", {}).get("message", "")
        except Exception:                        # noqa: BLE001
            detalhe = ""
        return {"error": {"message": f"HTTP 429 — {detalhe}"}}


def revisar(modelo: str, opord: str, travas: str, diff: str, gate: dict | None = None,
            *, chave: str | None = None, chamador=chamar, esperar=time.sleep) -> dict:
    """Pede o parecer. Nunca levanta: o revisor que falha não derruba o voo, e o motivo fica gravado
    em `estado`. `chamador` é o seam dos testes."""
    t0 = time.monotonic()
    r = {"modelo": modelo}
    if not diff.strip():
        r["estado"] = "sem diff"
        return r
    chave = chave if chave is not None else _env().get("E9_REVISOR_KEY", "")
    if not chave:
        r["estado"] = "sem chave"
        return r
    corpo, info = montar_pedido(modelo, opord, travas, diff, gate)
    r.update(info)
    try:
        resposta = _chamar_sem_429(chamador, corpo, chave)
        for espera in ESPERAS_SOBRECARGA_S:
            if not _sobrecarga(resposta):
                break
            r["tentativas"] = r.get("tentativas", 1) + 1
            esperar(espera)
            resposta = _chamar_sem_429(chamador, corpo, chave)
        r.update(ler_parecer(resposta))
        uso = resposta.get("usage") or {}
        r["custo"] = uso.get("cost")
        r["tokens"] = {"entrada": uso.get("prompt_tokens"), "saida": uso.get("completion_tokens")}
        r["provedor"] = resposta.get("provider")
        r["estado"] = "ok"
    except urllib.error.HTTPError as e:
        try:
            detalhe = json.loads(e.read() or b"{}").get("error", {}).get("message", "")
        except Exception:                        # noqa: BLE001
            detalhe = ""
        r["estado"] = f"indisponível: HTTP {e.code}" + (f" — {detalhe[:160]}" if detalhe else "")
    except Exception as e:                       # noqa: BLE001 — qualquer falha vira registro
        r["estado"] = f"indisponível: {type(e).__name__}: {str(e)[:160]}"
    r["duracao_s"] = round(time.monotonic() - t0, 1)
    return r


def ligado(cfg: dict) -> bool:
    return os.environ.get("E9_SEM_REVISOR") != "1" and compat.cfg(cfg).get("reviewer") is not False


def modelo_de(cfg: dict) -> str:
    rv = compat.cfg(cfg).get("reviewer")
    return (rv.get("modelo") if isinstance(rv, dict) else None) or MODELO_PADRAO


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="E-9 — revisor de outro modelo (calibração por commit)")
    ap.add_argument("--op", required=True, help="pasta da op (spec.md + op.json)")
    ap.add_argument("--repo", required=True, help="clone do alvo onde o commit existe")
    ap.add_argument("--commit", required=True, help="o commit do agente")
    ap.add_argument("--modelo")
    a = ap.parse_args(argv)
    op = Path(a.op)
    cfg = compat.cfg(json.loads((op / "op.json").read_text(encoding="utf-8")))
    travas_arq = RAIZ / "targets" / f"{cfg['target']}.md" if cfg.get("target") else None
    travas = travas_arq.read_text(encoding="utf-8") if travas_arq and travas_arq.exists() else ""
    gate = (json.loads((op / "metrics.json").read_text(encoding="utf-8")).get("gate")
            if (op / "metrics.json").exists() else None)
    modelo = a.modelo or modelo_de(cfg)
    r = revisar(modelo, compat.spec_path(op).read_text(encoding="utf-8"), travas,
                diff_do_commit(a.repo, a.commit), gate)
    r["commit"] = a.commit
    saida = op / "reviewer-calibracao.json"
    todos = json.loads(saida.read_text(encoding="utf-8")) if saida.exists() else []
    todos = [x for x in todos if not (x.get("modelo") == modelo and x.get("commit") == a.commit)] + [r]
    saida.write_text(json.dumps(todos, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"[E-9 REVISOR] {op.name} @ {a.commit}: {r.get('veredito', r['estado'])} · "
          f"{len(r.get('achados') or [])} achado(s) · custo {r.get('custo')}")
    for x in r.get("achados") or []:
        print(f"  - [{x['severidade']}] {x['arquivo']}:{x['linha']} — {x['problema']}")
    return 0 if r["estado"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
