# Changelog

As versões do kit, da mais nova para a mais antiga. As mudanças de comportamento vêm com o motivo, porque cada uma nasceu de uma falha medida.

## v0.13 e v0.14 (2026-11-24) — o reviewer com `REVIEW.md`, medido, e o veto em sombra

- **`REVIEW.md` por target** (`targets/<target>.review.md`; sem ele, `process/REVIEW-template.md`): o
  que é crítico no repositório (o achado ganha `critico: true`), os três passes (bugs, segurança,
  aderência à spec e aos guardrails), o limite de detalhes e os caminhos sensíveis. O formato é o do
  *AI-native SDLC playbook* da Anthropic.
- **Profundidade por risco**: `"risk": "baixo|medio|alto"` no `op.json` → rápido, padrão, fundo. Sem a
  chave, até 80 linhas e nenhum caminho sensível é rápido; o resto é padrão. Fundo faz um pedido por
  passe, e o veredito é o pior dos três. Os detalhes acima do limite saem; crítico e severidade alta
  ficam sempre.
- **Calibração** (`orchestrator/calibracao.py`, `revisar.py --calibrar "1=real,2=falso,3=parcial"
  --perdidos N`): o Monitor soma precisão e cobertura do reviewer.
- **Veto em sombra (v0.14)**: o que o reviewer teria segurado (REPROVA ou achado crítico), gravado em
  cada run e comparado no Monitor com o que o owner barrou na revisão. **Só registra**: o rc não muda.
- `revisar.py` deixou de reescrever com os nomes novos as chaves de um `metrics.json` antigo.
- 12 casos novos (`test_calibracao.py`).

## v0.12 (2026-11-24) — o gate de plano

- **`orchestrator/plano.py`**: entre o acceptance na base e a implementação, uma fase de plano. O agent
  recebe o briefing com a instrução de só ler e terminar com `{"arquivos": [...]}`. O run para com
  **rc 13** se a worktree mudou durante o plano, se o plano não veio legível, se um arquivo sai de
  `expected_artifacts`/`optional_artifacts`, ou se o plano inclui acceptance test. O padrão obrigatório
  que o plano esquece só avisa, porque o commit ainda o cobra.
- **A implementação retoma a sessão do plano** (`claude --resume <id> -p`): o recon não se paga duas
  vezes. Sem `claude -p` no `cmd_template`, começa nova, com o plano anexado ao briefing.
- Ligado por padrão; `"plan_gate": false` desliga, e `agent.plan_timeout_s` (20 min) é o teto. O Monitor
  ganha o rótulo PLANO, o bloco do plano e o log ao vivo da fase de plano.
- **`process/POLICY.md` ganha a tabela de todos os códigos de saída**, na ordem em que acontecem.
- 11 casos novos (`test_plano.py`).

## v0.11 (2026-11-24) — o caminho do agent, não só o fim

- **`orchestrator/trajetoria.py`**: varre o `agent.jsonl`, sem modelo, atrás de cinco padrões que
  custaram runs de verdade e não deixam marca na árvore entregue: `segundo_plano` (comando mandado ou
  jogado para segundo plano, relatório que encerra "aguardando"), `timeout_curto` (teste com `timeout`
  abaixo do tempo medido na baseline), `acceptance_aberto` (o sha256 só vê o estado final: editar e
  desfazer passa), `teste_em_paralelo` e `rede`. **Só informa**: vai ao `metrics.json`, ao run report
  e ao Monitor, e o rc não muda.
- Lê cada comando pela cabeça de cada trecho (`&&`, `;`, `|`), sem ambiente nem prefixos, e ignora o
  corpo de heredoc: `ps aux | grep pytest` não é teste, `pytest x.py 2>&1` não edita o `x.py`. As três
  confusões da primeira versão viraram casos de teste.
- O Monitor varre também os runs feitos antes da v0.11, pelo transcript que ficou na máquina, sem
  reescrever o `metrics.json`.
- `python3 orchestrator/trajetoria.py --op ops/<op>` varre runs já feitos. 9 casos novos.

## v0.10 (2026-11-24) — o tempo do owner

- **`orchestrator/owner.py`**: os minutos de spec (`"owner": {"spec_min": N}` no `op.json`, ou
  `revisar.py --spec-min N`) e de revisão (`revisar.py --revisao-min N`) vão ao `metrics.json`
  (`owner_time`), à seção "## Tempo do owner" do run report e ao Monitor, ao lado dos minutos de
  agent. O argumento de que o gargalo saiu do código para as pontas não tinha número; agora tem,
  quando o owner anota. Número inválido é recusado antes de gravar qualquer coisa.
- `revisar.py` passa a aceitar só os minutos, sem desfecho, e `--consertado ""` deixou de virar
  descartado.
- `orchestrator/test_owner.py`: 6 casos.

## v0.9.1 (2026-11-04) — Monitor E-9

- **`orchestrator/monitor.py`**: a página que mostra o run ao vivo (o log do agent, a etapa com o
  cronômetro contra o teto, as recusas de permissão) e, no fim, o run report na ordem da prova. Só
  lê os arquivos de `ops/`; stdlib, sem dependência. Veio do painel que o harness original usa,
  sem nada daquela instalação.
- Escuta só em `127.0.0.1` por padrão: a página mostra o código e os comandos do agent.
  `--host 0.0.0.0` abre para a rede, com aviso. Porta ocupada vira uma mensagem, não um traceback.
- Glossário, modelo de spec e mensagem de fim de run: o owner escreve as regras do merge e do
  deploy, e o merge passa por revisão fora do agent; nenhum deles descreve mais alguém aprovando
  caso a caso.
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
