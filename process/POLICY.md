# Policy — E-9

> As regras **permanentes** de todo run, de todo agent. Toda spec aponta para cá, e o texto entra
> no briefing de cada run. A parte que obriga está no código (`e9_run.py`); aqui ela está por
> escrito para o agent saber por quê. Até a v0.5 do kit este documento se chamava ROE Card; a
> numeração não mudou.

---

**E-9 — STANDING POLICY**
*Owner: a pessoa responsável pelo repositório. Execução: o E-9. Ambiente: fora da produção.*

1. **Stay in scope.** Um repositório, uma branch por op. Nunca edita arquivo de outro repositório.
2. **Branch only.** Todo trabalho na `feat/*` designada. **NUNCA** push. **NUNCA** merge.
3. **Prod is off-limits.** **NUNCA** toca produção. O run acontece no ambiente de ensaio.
4. **No real data.** Só dado sintético ou gerado. **NUNCA** copia dado de produção para o ambiente
   de ensaio (LGPD).
5. **Secrets stay put.** Nunca lê, imprime, commita ou move credencial, `.env` ou chave. Achou
   segredo exposto? **Para e escala.**
6. **The quality gate gates every step.** Nada é "concluído" sem o gate verde — testes passam, lint
   limpo. Vermelho = parar.
7. **Outcome-based spec — fixo no resultado, livre nos meios.** A intent, o estado final e a policy
   são **FIXOS**. Os **MEIOS** — estrutura, em qual módulo um arquivo mora, como implementar — são
   do agente, e o agente diz quais arquivos vai tocar **antes** de tocar (o gate de plano, v0.12: fora
   do escopo é rc 13). Mas desvio do que a spec sugeriu **tem de ser declarado** (na seção `## Desvios da
   spec` do relatório). Iniciativa silenciosa é proibida; iniciativa declarada dentro da intent é o
   esperado. O agent **não** produz run report nem métricas (isso é do E-9). Em dúvida sobre a
   intent: escalate.
8. **Stay inside the budget.** Respeita o modelo e o teto da spec. Não sobe de modelo sem
   autorização.
9. **Selective staging.** `git add` arquivo por arquivo — **NUNCA** `-A` nem `.`.
10. **Abort safely.** Ao bater um critério de abort, erro irrecuperável, segredo exposto ou qualquer
    coisa que toque produção: **para**, deixa a branch num estado seguro, relata o status, **não dá
    push**, espera o owner.

---

## Onde um run para — os códigos de saída

Cada jeito de parar tem o seu código, na ordem em que acontece; de manhã o número já diz onde olhar.
Só o rc 0 entrega alguma coisa, e o que ele entrega é uma branch para revisão, nunca um deploy.

| rc | Onde | O que quer dizer |
|---|---|---|
| 1 | contrato | a spec ou o `op.json` não passam na validação (policy, guardrails, traceability, owner) — nada foi tocado |
| 11 | trava | a mesma op já está em run, ou nenhum slot desocupou dentro do teto |
| 2 | worktree | não deu para cortar a worktree do target |
| 12 | banco do run | o Postgres do run não ficou pronto |
| 8 | baseline | a worktree não estava limpa antes do run |
| 7 | baseline | o gate já falha no `main`, antes do agent |
| 9 | acceptance | os acceptance tests não falham na base (já passam, ou nem rodam como teste) |
| 13 | **plano (v0.12)** | o plano do agent saiu do escopo do commit, incluiu acceptance test, não veio legível, ou o agent mexeu na worktree durante ele — **antes** da implementação |
| 6 | agent | o agent terminou em erro (`is_error`, login vencido) |
| 5 | agent | o agent estourou o teto de tempo |
| 10 | acceptance | o agent mexeu num acceptance test, ou eles não passaram inteiros depois |
| 3 | gate | o quality gate reprovou a entrega (ou pulou mais testes que a baseline) |
| 4 | commit | gate verde, mas a árvore testada não é a que seria commitada (arquivo fora do escopo) |
| 0 | entrega | gate verde, acceptance cumprido, commit na `feat/*`, sem push — vai para revisão |

O AI code review, a varredura da trajetória e o veto em sombra **não** têm código: só informam.

> **ALWAYS:** branch · árvore sempre buildável · relatório com desvios · reporta bloqueio.
> **NEVER:** push · merge · produção · segredo · outro repositório · dado real · `git add -A`.
> **WHEN IN DOUBT:** age em direção à intent; se ela não estiver clara, **escalate**. Parar com
> segurança ganha de agir de forma irreversível.
