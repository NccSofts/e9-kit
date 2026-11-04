# E-9 kit

Um harness em Python, só biblioteca padrão, que roda o **Claude Code sem ninguém olhando**
(`claude -p`) numa cópia isolada do seu repositório e **decide de fora** se o resultado presta. O
agent não consegue fazer push, merge nem mexer em arquivo que a spec não liberou. Quem diz que está
pronto é a sua suíte de testes, não o agent.

Nasceu de duas dezenas de tarefas reais num produto em produção, e cada regra daqui veio de uma
delas que deu errado. Versão **v0.9.1**; o que mudou de uma versão para outra está no
[`CHANGELOG.md`](CHANGELOG.md).

O porquê de cada peça, com os números das tarefas que as originaram, está no artigo
[Especificar, cercar, provar](https://www.nccsofts.com.br/artigos/especificar-cercar-provar/).

> **O que este repositório é, e o que não é.** É a versão limpa de um harness que uso no meu
> próprio trabalho, publicada para mostrar o método funcionando. É projeto de uma pessoa, não
> produto: não há suporte, prazo nem garantia de que a próxima versão chegue aqui, e o kit pode
> ficar atrás do harness que eu rodo. Use, copie e adapte à vontade (licença MIT); só não conte
> com ele como fornecedor.

## Nos termos da indústria

| Pilar | No E-9 |
|---|---|
| **Spec-driven development** | `spec.md` (para o agent ler), com requirements `FR-NNN`, + `op.json` (o contrato, para o script conferir) |
| **Context engineering** | agent novo por op, worktree isolada, spec escrita depois de ler o código |
| **Harness engineering** | policy em código: git sem push/merge, spec validada, só entram os arquivos listados |
| **Loop engineering** | baseline, quality gate, acceptance tests, traceability matrix e run report (`report.md`) de todo run |

Os nomes estão em `process/GLOSSARY.md`, com a coluna dos nomes antigos para quem tem ops da v0.5.

## O ciclo

```
dry run → worktree (+ banco do run) → baseline → acceptance tests (têm de falhar) → agent
        → quality gate → acceptance tests (têm de passar, intactos) → AI code review (opcional)
        → só os arquivos listados → commit na feat/*, sem push → run report
```

Cada jeito de falhar tem um código de saída:

| rc | Onde parou | O que quer dizer |
|---|---|---|
| 0 | fim | commit na branch, pronto para revisão |
| 2 | worktree | não montou a cópia isolada |
| 3 | quality gate | o agent entregou e o gate reprovou |
| 4 | commit | o agent mexeu em arquivo fora da lista |
| 5 | agent | estourou o teto de tempo |
| 6 | agent | o Claude Code terminou em erro, mesmo com rc 0 (login vencido, por exemplo) |
| 7 | baseline | o gate já falhava no main: o agent nem foi chamado |
| 8 | baseline | a worktree estava suja de um run anterior |
| 9 | acceptance tests | já passavam antes do agent, ou nem rodaram como teste (erro de import na coleta) |
| 10 | acceptance tests | o agent mexeu neles, ou não passaram inteiros (falha, skip, contagem menor) |
| 11 | trava da op | a mesma op já está rodando |
| 12 | banco do run | o Postgres do run não subiu ou não responde: nada foi despachado |

## Requisitos

- **Linux ou WSL.** No Windows puro o script roda, mas sem duas proteções: matar o grupo inteiro de
  processos no estouro de tempo e matar o que o agent deixou rodando na worktree.
- Python 3.10 ou mais novo, git.
- [Claude Code](https://docs.claude.com/claude-code) instalado e com login feito (`claude` no PATH).
- **Uma máquina que não seja a de produção.** O kit recusa target que bata em `prod_markers`, mas
  quem garante de verdade é não ter credencial nem rota de produção no ambiente do agent.
- Opcionais: **Docker**, para o banco por run; uma **chave do OpenRouter** com teto de crédito, para
  o AI code review.

## Começo rápido

```bash
# 1. os testes do próprio harness (não chamam o Claude nem sobem container)
python3 -m pytest -q orchestrator          # ou: python3 orchestrator/test_e9_run.py

# 2. cria o projeto de exemplo em /tmp/e9-exemplo/projeto
sh exemplo/preparar.sh

# 3. dry run: valida a spec e mostra o plano, sem mexer em nada
python3 orchestrator/e9_run.py --op ops/exemplo-slug --dry-run

# 4. (opcional) noutro terminal, o Monitor E-9 para ver o agent trabalhando:
#    python3 orchestrator/monitor.py      e abra http://127.0.0.1:8417 no navegador

# 5. o run de verdade (chama o Claude; custa alguns centavos de dólar)
python3 orchestrator/e9_run.py --op ops/exemplo-slug --live

# 6. o resultado
cat ops/exemplo-slug/report.md
git -C /tmp/e9-exemplo/wt-slug log --oneline -3
```

Verde: a branch `feat/slug` tem um commit, e o `report.md` mostra o relatório do agent ao lado do
que o gate mediu e da traceability matrix. Merge é com você. Depois dele, grave o desfecho:

```bash
python3 orchestrator/revisar.py --op ops/<op> --como-veio --commit-alvo <sha>
python3 orchestrator/revisar.py --op ops/<op> --consertado "o que mudou" --commit-alvo <sha>
python3 orchestrator/revisar.py --op ops/<op> --descartado "por quê"
```

O gate sabe se passou; só quem revisa sabe se entrou como veio. Op com `"treino": true` não pede
revisão.

## Acompanhar pelo Monitor E-9

O run acontece sem ninguém olhando, mas dá para olhar. O Monitor E-9 é uma página que mostra, ao
vivo, o que o agent está fazendo, e o run report quando ele termina. Ele só lê os arquivos que o
harness grava em `ops/`; não manda nada ao agent, não para o run e não grava coisa nenhuma.

**1. Suba o Monitor**, num terminal à parte, na raiz do kit:

```bash
python3 orchestrator/monitor.py
# [Monitor E-9] ... em http://127.0.0.1:8417 · ops em .../ops · Ctrl+C encerra
```

**2. Abra `http://127.0.0.1:8417` no navegador.** Pode deixar aberto antes de começar o run: a página
pergunta a cada 3 segundos e troca sozinha para o run que estiver rodando.

**3. Rode a op** noutro terminal, como sempre (`--live`). A página mostra:

- **Runs**, à esquerda: cada op que já rodou, com o desfecho (VERDE, VERMELHO, BARRADA, ACCEPTANCE,
  BASE, FALHA) e, se você gravou com o `revisar.py`, como ela entrou. Clique para ver outra.
- **Status**: em que etapa o run está (baseline, acceptance antes, agent, gate, acceptance depois,
  commit), com o cronômetro contra o teto de cada uma, os tokens gastos e as recusas de permissão.
  Fica vermelho perto de estourar.
- **Log do agent**, linha a linha, com a hora: **AÇÃO** (arquivo lido ou editado, comando rodado),
  **FALA** (o que o agent escreveu), **ERRO** (ferramenta que falhou) e **RECUSA** (comando que o
  `claude -p` recusou por falta de permissão: em modo não interativo ninguém aprova, e o agent segue
  sem saber por quê. Se aparecer, falta o comando no `--allowedTools` da op).
- **Run report**, quando termina, **na ordem da prova**: o veredito e o rc, o quality gate, a
  baseline, o relatório comparado com o gate, os acceptance tests antes e depois, o AI code review,
  os desvios que o agent declarou, custo, tempo de cada etapa e o commit. O relatório do agent fica
  por último, fechado: ele é a declaração, o gate é a prova.
- **Placar dos runs**: quantos deram verde, quantos de primeira, quantos entraram como vieram, em
  quantos o relatório do agent contradisse o gate, e o custo. Ops com `"treino": true` ou com
  `smoke` no nome ficam fora.

**4. `Ctrl+C`** no terminal do Monitor encerra a página. O run não depende dele.

Opções:

```bash
python3 orchestrator/monitor.py --porta 9000          # outra porta (se a 8417 estiver ocupada)
E9_OPS_DIR=/outro/lugar/ops python3 orchestrator/monitor.py   # ler as ops de outra pasta
```

**Segurança.** Por padrão o Monitor escuta só em `127.0.0.1`, isto é, só na sua máquina: a página
mostra o código, os comandos e os caminhos do seu projeto. `--host 0.0.0.0` abre para a rede local,
e ele avisa ao subir. Se o run roda noutra máquina (um servidor, uma VM), prefira deixar o Monitor
lá em `127.0.0.1` e abrir um túnel SSH, que só você usa:

```bash
ssh -N -L 8417:127.0.0.1:8417 usuario@servidor     # e abra http://127.0.0.1:8417 aqui
```

## Sua própria op

1. Crie `ops/<nome>/` com um `spec.md` (modelo em `process/SPEC-template.md`) e um `op.json`
   (copie o do exemplo).
2. No `op.json`, os campos que importam:
   - `target_repo`, `worktree`, `branch` (sempre `feat/...`);
   - `prod_markers`: trechos que, se aparecerem no `target_repo`, querem dizer produção;
   - `expected_artifacts` (obrigatórios, aceitam glob) e `optional_artifacts`;
   - `gate.test_cmd`: o mesmo comando que o seu CI roda;
   - `agent.model`: o **id completo** do modelo, nunca o alias, que pode resolver para um modelo
     mais velho sem ninguém saber;
   - `agent.cmd_template`: o `claude -p` com `--allowedTools` liberando exatamente os comandos de
     teste. Em modo não interativo, comando fora da lista é recusado em silêncio;
   - `worktree_link`: arquivos fora do git que o projeto precisa para rodar (um `.env` **de
     teste**, por exemplo), ligados na worktree por link simbólico;
   - `venv`: se o projeto usa um, para os placeholders `{venv}` do comando.
3. Confira o `.gitignore` do projeto-alvo: cache e arquivo gerado pelos testes (`__pycache__`,
   `.pytest_cache`, cobertura) têm de estar nele. Senão o harness vê arquivo novo fora da lista e
   recusa o commit (rc 4), mesmo com tudo verde.
4. **Acceptance tests e requirements (recomendado para feature):** escreva os requirements na seção
   `## Requirements` da spec (`- **FR-001** — ...`) e ponha os testes em `ops/<nome>/acceptance/`
   com o caminho que terão no projeto (`acceptance/tests/test_acceptance_x.py` vira
   `tests/test_acceptance_x.py`), cada um com o requirement no nome (`test_fr_001_...`). Declare
   `"acceptance": {"cmd": "python3 -m unittest tests.test_acceptance_x"}` (ou o pytest
   equivalente). **Importe o código novo dentro de cada teste**: import no topo quebra a coleta, e
   erro de coleta não prova nada (rc 9). O requirement que nenhum teste prova diz isso na própria
   linha: `(sem aceite: o gate prova)`.
5. **Guardrails (opcional):** crie `targets/<nome>.md` com as regras numeradas (`[EX-01]`), declare
   `"target": "<nome>"` e escreva a seção `## Guardrails` na spec, uma linha por guardrail
   (`aplica: como` ou `não se aplica: porquê`). Guardrail novo nasce quando uma op, ou a revisão
   dela, achar uma regra que a spec não previu.
6. **Banco por run (opcional):** copie `targets/exemplo.workspace.json` e `exemplo.db.sql` para a
   sua receita, ajuste imagem, papel e variáveis, e declare `"workspace": {"banco": "<receita>"}`.
   A sua aplicação precisa ler a URL do banco do ambiente antes de qualquer `.env`.
7. **AI code review (opcional):** ponha `E9_REVISOR_KEY=<chave do OpenRouter>` num `.env` na raiz
   do kit (fora do git). Sem chave, o run grava "sem chave" e segue; `"reviewer": false` no
   `op.json` o desliga, e `"reviewer": {"modelo": "..."}` troca o modelo.
8. Rode sempre `--dry-run` antes do `--live`.

Para suítes longas, ponha `BASH_DEFAULT_TIMEOUT_MS=1200000 BASH_MAX_TIMEOUT_MS=1200000` na frente
do `claude -p` no `cmd_template`. Sem isso, o Claude Code manda para segundo plano todo comando de
mais de 2 minutos, e o agent nunca vê o fim dos testes. Desde a v0.5 o briefing já leva quanto o
gate levou na baseline e o `timeout` a pedir.

### Variáveis de ambiente

| Variável | Para quê |
|---|---|
| `E9_MAX_VOOS` | quantos runs ao mesmo tempo (padrão 2) |
| `E9_SLOT_TETO_S` | quanto um run espera por um slot livre (padrão 6 h) |
| `E9_PRIORITARIOS` | processos que têm a vez na máquina, separados por vírgula; com um rodando, o run espera |
| `E9_SEM_ESPERA` | `1` ignora a espera acima |
| `E9_SEM_REVISOR` | `1` desliga o AI code review |
| `E9_DOCKER` | o comando do docker (padrão `docker`) |

## O que aprendemos (e virou código)

- **O relatório do agent é declaração; o gate é a prova.** O agent já disse "tudo verde" com cinco
  testes falhando e "418 passed" com a suíte em 814. O run report põe os dois lado a lado.
- **Testar o main antes do agent.** Sem isso, um main já vermelho gasta uma hora de agent tentando
  consertar o que ele não quebrou.
- **`claude -p` com login vencido sai com código 0.** O erro só aparece no último evento da saída;
  o harness lê o evento, não o código.
- **Nada em segundo plano.** Em `claude -p`, quando o agent para de falar a sessão acaba; o que ele
  deixou rodando é morto antes da verificação.
- **Gate verde não prova o desenho.** Autorização errada e regra de negócio errada passaram verdes.
  Por isso há acceptance tests escritos antes, a traceability matrix e o segundo olhar de outro
  modelo. E revisão antes do merge, fora do agent, sempre.
- **A spec também erra.** Uma ordem proibia justo o que dois guardrails do repositório exigiam; o
  agent obedeceu e o gate reprovou. Os guardrails fazem o erro aparecer antes do run.
- **Teste que passa na base não prova o requirement.** A matriz marca isso como guarda de regressão,
  em vez de deixar passar como prova.

## Limitações

- Sem nova tentativa automática: o re-run é decisão humana.
- O resumo do gate no run report entende a saída de `pytest`, `unittest`, `ruff` e `mypy`. Outros
  comandos funcionam como gate (o que vale é o código de saída), só que sem contagem no relatório.
- Os acceptance tests contam testes pelo resumo do `pytest` ou do `unittest`; outro runner precisa
  imprimir um dos dois formatos.
- O banco por run só sabe subir Postgres.
- Os textos do briefing, do run report e do Monitor estão em português.
- O Monitor descobre o run em andamento pelo `/proc`, então só em Linux e WSL. No Windows puro a
  página abre e mostra os runs terminados, mas não marca o que está rodando.

## Licença

MIT. Veja `LICENSE`.
