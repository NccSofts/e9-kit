# Glossário — E-9 × mercado

A doutrina do E-9 fala militar; a roda de conversa fala engenharia de software. Esta tabela
traduz uma língua na outra. O termo de mercado é o nome que o engenheiro do outro lado da mesa
vai reconhecer, e nem sempre a correspondência é exata: onde não é, a coluna de nota diz
a diferença.

Os quatro pilares em que o vocabulário da indústria se organiza (o mesmo corte do deck
"Especificar, cercar, provar", que acompanha o kit):

- **Spec-driven development (SDD):** escrever o que se quer antes do código, num artefato versionado.
- **Context engineering:** o que o agente lê, e só isso (um agente novo por tarefa, a pasta isolada).
- **Harness engineering:** as regras impostas em código, fora do agente, e não pedidas a ele.
- **Loop engineering:** o ciclo fechado de medir, barrar e aprender (linha de base, gate, debrief).

## Papéis

| E-9 | Mercado | Nota |
|---|---|---|
| Comandante | human-in-the-loop · approver · tech lead | a pessoa responsável: define a intenção e aprova; no *AI-native SDLC playbook* da Anthropic, o *product owner* e o *code owner* |
| E-9 | orchestrator · agent harness | o programa que despacha o agente e impõe as regras (`e9_run.py`) |
| Operador | coding agent · worker agent | o `claude -p` que escreve o código numa worktree |
| Overwatch | quality gate · CI gate | no E-9 é **determinístico** (ruff + mypy + suíte), não um agente revisor |

## Artefatos

| E-9 | Mercado | Nota |
|---|---|---|
| OPORD | spec · `spec.md` | junta o que o playbook separa em `intent.md`, `spec.md` e `plan.md` |
| Commander's Intent | intent · goal | o porquê; fixo |
| End-state | definition of done · acceptance criteria | em prosa; a v0.4 o torna executável (ver *aceite*) |
| Enemy Forces | known risks · pitfalls | as minas do terreno levantadas no recon |
| `op.json` | job config · pipeline config | o contrato executável da op |
| `expected_artifacts` | change scope · allowlist de arquivos | o que pode mudar; fora disso o commit é recusado |
| Aceite (v0.4) | acceptance tests · ATDD · test-first | escrito antes do voo, por quem não implementa, e imutável |
| Travas do alvo (v0.4) | invariants · repo policies · guardrails do repo | o que toda mudança no alvo precisa respeitar; parente do CLAUDE.md e das *skills* |
| AAR (After Action Review) | post-mortem · retrospective | AAR também é termo de mercado (veio do Exército dos EUA); *post-mortem* é mais usado para incidente, *retro* para rotina |
| SITREP | status report | |
| `metrics.json` / medição da fábrica | engineering metrics · DORA metrics | custo, tempo, verde de primeira, retrabalho |

## Regras

| E-9 | Mercado | Nota |
|---|---|---|
| ROE (Rules of Engagement) | guardrails · policy | o cartão em `ROE-card.md` |
| ROE em código | policy-as-code · hooks · harness guardrails | "skill aconselha, hook obriga": a ROE do E-9 é a parte que obriga |
| Mission command (FIXO × DELEGÁVEL) | outcome-based spec · "specify the what, not the how" | intenção e estado final fixos; o meio é do agente, se declarado |
| HOLD + SITREP | escalation · handoff to human | parar e perguntar em vez de adivinhar |
| Desvios declarados | deviation log · change notes | o que o agente fez diferente da spec, e por quê |
| Gate de entrega (árvore testada = commitada) | "what you test is what you ship" · hermetic commit | arquivo fora do escopo aborta o commit (rc 4) |

## Ciclo e lugares

| E-9 | Mercado | Nota |
|---|---|---|
| Voo / op | agent run · job | uma execução do harness |
| Ensaio (`--dry-run`) | dry run | valida o contrato e não muta nada |
| Linha de base | baseline · pre-flight check | o gate roda no `main` antes do agente (rc 7 se já nasce vermelho) |
| Recon | discovery · codebase research | ler o alvo antes de escrever a spec |
| Kill house / casa de tiro (a máquina de ensaio) | sandbox · staging · dev environment | onde o agente pode errar sem dano; prod é off-limits |
| Worktree | git worktree · isolated workspace | o mesmo nome na indústria |
| Relatório do operador × gate | agent self-report vs. verification · "trust but verify" | o relatório é declaração; o gate é a prova |

## Como usar

Ao falar do E-9 para quem não conhece a doutrina, use o termo de mercado com o do E-9 ao lado:
"o quality gate (o overwatch) barrou", "o post-mortem (o AAR) da op 15". Termo novo que nascer
no E-9 entra aqui na mesma sessão.
