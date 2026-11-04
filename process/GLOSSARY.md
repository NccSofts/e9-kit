# Glossário — os nomes do E-9

Desde a v0.9 o E-9 usa o vocabulário de mercado, em inglês, no corte de SDLC: spec, agent, quality
gate, run report. Até a v0.5 do kit ele falava militar — OPORD, operador, overwatch, AAR. Esta
tabela existe para quem tem ops escritas antes: a coluna **Antes** é o nome antigo; o resto é o que
vale hoje. O harness lê os dois (`orchestrator/compat.py`).

Os quatro pilares em que o vocabulário da indústria se organiza:

- **Spec-driven development (SDD):** escrever o que se quer antes do código, num artefato versionado.
- **Context engineering:** o que o agent lê, e só isso (um agent novo por tarefa, a pasta isolada).
- **Harness engineering:** as regras impostas em código, fora do agent, e não pedidas a ele.
- **Loop engineering:** o ciclo fechado de medir, barrar e aprender (baseline, gate, run report).

## Papéis

| Hoje | Antes | Nota |
|---|---|---|
| owner | Comandante | quem define a intent e aprova merge e deploy |
| E-9 (orchestrator · agent harness) | E-9 | o programa é o `e9_run.py` |
| agent | operador | o `claude -p` que escreve o código numa worktree |
| quality gate | overwatch | **determinístico** (lint + suíte), não um agente |
| AI code review | revisor | outra família de modelo lendo o diff; **só informa** (`reviewer.py`) |

## Artefatos

| Hoje | Antes | Nota |
|---|---|---|
| spec (`spec.md`) | OPORD (`OPORD.md`) | o pedido escrito antes do código |
| intent | Commander's Intent | o porquê; fixo |
| objective | Mission | |
| context | Situation | inclui os riscos |
| requirements `FR-NNN` (v0.8) | requisitos | o formato do Spec Kit; cada um aponta para o teste que o prova |
| acceptance tests (`acceptance/`) | aceite (`aceite/`) | escritos antes do run, por quem não implementa, e imutáveis |
| traceability matrix (v0.8) | matriz de rastreio | requirement × test × base × entrega, conferida pelo harness |
| guardrails (`targets/<target>.md`) | travas do alvo (`alvos/<alvo>.md`) | o que toda mudança no target respeita; IDs `XX-NN` iguais |
| run report (`report.md`) | AAR (`AAR.md`) | retrospectiva de cada run, com as engineering metrics |
| status | SITREP | |
| `op.json` | `op.json` | chaves novas: `target`, `acceptance`, `agent`, `gate`, `reviewer` |
| engineering metrics (`metrics.json`) | medição da fábrica | custo, tempo, verde de primeira, retrabalho |

## Regras

| Hoje | Antes | Nota |
|---|---|---|
| policy (`process/POLICY.md`) | ROE Card (`ROE-card.md`) | mesma numeração: "ROE §2" antigo = policy §2 |
| policy-as-code | ROE em código | "skill aconselha, hook obriga": a parte que obriga mora no harness |
| outcome-based spec | mission command (FIXO × DELEGÁVEL) | resultado fixo, meio livre, desvio declarado |
| escalate | HOLD + SITREP | parar e perguntar em vez de adivinhar |
| deviation log (`## Desvios da spec`) | `## Desvios da OPORD` | o que o agent fez diferente da spec, e por quê |
| hermetic commit | gate de entrega | árvore testada = árvore commitada; fora do escopo aborta (rc 4) |

## Ciclo e lugares

| Hoje | Antes | Nota |
|---|---|---|
| run | voo | uma execução do harness; a pasta continua `ops/` |
| dry run (`--dry-run`) | ensaio | valida o contrato e não muta nada |
| baseline | linha de base | o gate no `main` antes do agent (rc 7 se já nasce vermelho) |
| discovery | recon | ler o target antes de escrever a spec |
| sandbox | casa de tiro | onde o agent pode errar sem dano; produção é off-limits |
| self-report vs. verification | relatório do operador × gate | o relatório é declaração; o gate é a prova |
| Monitor E-9 (`orchestrator/monitor.py`) | TOC | a página ao vivo do run; só lê, e só em `127.0.0.1` por padrão |
| `process/` | `doctrine/` | |
| `targets/` | `alvos/` | |

## Spec Kit × E-9

O Spec Kit (GitHub) escreve a spec; o E-9 executa e prova. A spec que sai dele vira a spec do run
à mão; os artefatos ficam em `ops/<op>/spec/`.

| Spec Kit | E-9 | Nota |
|---|---|---|
| `constitution.md` | guardrails (`targets/<target>.md`) | uma fonte só: gere a constitution dos guardrails |
| `spec.md` (FR-NNN, histórias, critérios) | spec, seção `## Requirements` | os IDs passam iguais |
| `/speckit-clarify` | perguntas ao owner antes do run | uma por vez, no máximo cinco |
| `plan.md` + `research.md` | spec, Context e Execution | o plano fixa a interface que os acceptance tests usam |
| `tasks.md` | spec, Tasks | |
| `/speckit-analyze` | (sem par) | liga requisito a tarefa **lendo** |
| traceability matrix (v0.8) | (sem par no Spec Kit) | liga requisito a teste **rodando** |
| `/speckit-implement` | o run (agent + gate + acceptance + reviewer) | o implement roda dentro da sessão do agent, sem prova de fora |

## Como usar

Os nomes da coluna **Hoje** são os únicos que entram em documento, código e conversa nova.
