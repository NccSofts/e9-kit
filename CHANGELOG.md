# Changelog

As versões do kit, da mais nova para a mais antiga. As mudanças de comportamento vêm com o motivo, porque cada uma nasceu de uma falha medida.

## v0.9.1 (2026-11-04) — Monitor E-9

- **`orchestrator/monitor.py`**: a página que mostra o run ao vivo (o log do agent, a etapa com o
  cronômetro contra o teto, as recusas de permissão) e, no fim, o run report na ordem da prova. Só
  lê os arquivos de `ops/`; stdlib, sem dependência. Veio do painel que o harness original usa,
  sem nada daquela instalação.
- Escuta só em `127.0.0.1` por padrão: a página mostra o código e os comandos do agent.
  `--host 0.0.0.0` abre para a rede, com aviso. Porta ocupada vira uma mensagem, não um traceback.
- `orchestrator/test_monitor.py`: 19 casos, incluindo o de não sair da pasta de ops pelo nome que o
  navegador manda e o de escutar só na máquina por padrão.

## v0.5 → v0.9 (2026-11-03)

- **v0.6 — AI code review de outra família de modelo** (`orchestrator/reviewer.py`, opcional). O
  ponto cego medido do harness é o gate verde com o desenho errado. Um modelo da OpenAI, pelo
  OpenRouter, lê a spec, os guardrails e o diff e diz se aquilo faz o que foi pedido. Ele **só
  informa**: o parecer vai ao run report e ao `metrics.json`, e nunca muda o rc. Na calibração,
  acertou um defeito numa tarefa e deixou passar o mesmo defeito na seguinte, então é pista, não
  prova. O diff sai mascarado (CNJ, CNPJ, CPF, e-mail), `.env*` nunca entra, e o pedido exige
  provedor sem retenção (ZDR). Se nenhum atender, fica "indisponível", e o pedido **nunca** se
  repete sem a restrição. Quando o provedor está sobrecarregado ("high demand", "rate-limited",
  HTTP 429), repete o mesmo pedido, até duas vezes.
- **v0.7 — banco próprio por run** (`orchestrator/workspace.py`, opcional). Com dois runs no mesmo
  repositório, o banco de teste compartilhado inventa falhas de um para o outro. A op que declara
  `"workspace": {"banco": "exemplo"}` ganha um Postgres num container descartável (receita em
  `targets/exemplo.workspace.json`): tmpfs, porta efêmera, senha gerada no run e nunca escrita em
  arquivo, papel da aplicação **sem** superuser. Junto vieram a trava por op (rc 11), os slots (no
  máximo `E9_MAX_VOOS` runs juntos, padrão 2) e a guarda contra o skip silencioso: a suíte que pula
  os testes de banco que não conectam sairia verde com metade dela sem rodar.
- **v0.8 — traceability.** A spec ganha a seção `## Requirements`, com IDs `FR-NNN` no formato do
  Spec Kit, e cada acceptance test leva no nome o requirement que prova (`test_fr_001_...`). O
  harness recusa a op, já no `--dry-run`, se um requirement ficar sem teste, se um teste não disser
  qual prova ou se citar um que não existe. O run report traz a matriz requirement × test × base ×
  entrega. Na estreia, a matriz pegou um teste que passava sem provar o requirement, e é para isso
  que ela existe.
- **v0.9 — vocabulário de mercado.** Até a v0.5 o kit falava militar (OPORD, ROE, operador,
  overwatch, AAR). Agora: spec, policy, agent, quality gate, acceptance tests, guardrails, run
  report, owner. `doctrine/` virou `process/`, `alvos/` virou `targets/`, `OPORD.md` virou
  `spec.md`, `aceite/` virou `acceptance/`. **As ops antigas continuam rodando como estão**:
  `orchestrator/compat.py` é o único lugar que conhece os nomes velhos e os traduz na leitura
  (chaves do `op.json`, nomes de arquivo, seções da spec).
- O `prod_markers` deixou de ter valores fixos: o que conta como produção é declarado no `op.json`
  de cada op.

## v0.5 (2026-10-28)

- O briefing passa a dizer quanto o quality gate levou na baseline e o timeout a pedir: comando que
  passa do timeout vai sozinho para segundo plano, e o agent encerrava "aguardando".
- `metrics.json` com o rc, os arquivos do commit, as etapas com teto e os runs anteriores da mesma op.
- `orchestrator/revisar.py` grava se a entrega entrou como veio, consertada ou descartada.

## v0.4 (2026-10-28)

O gate responde "nada quebrou?"; faltava "o pedido foi cumprido?".

- Acceptance tests de quem pede: falham antes do agent (rc 9 se não), ficam intocados (sha256) e
  passam inteiros, sem skip, depois (rc 10). Entram no commit.
- Guardrails do target: regras numeradas, e a op é recusada no `--dry-run` se a spec não citar cada
  uma.

## Primeira versão (2026-10-27)

Spec + `op.json`, worktree isolada, baseline, quality gate, commit só dos arquivos listados, sem
push, e run report.
