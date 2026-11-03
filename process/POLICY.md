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
   do agent. Mas desvio do que a spec sugeriu **tem de ser declarado** (na seção `## Desvios da
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

> **ALWAYS:** branch · árvore sempre buildável · relatório com desvios · reporta bloqueio.
> **NEVER:** push · merge · produção · segredo · outro repositório · dado real · `git add -A`.
> **WHEN IN DOUBT:** age em direção à intent; se ela não estiver clara, **escalate**. Parar com
> segurança ganha de agir de forma irreversível.
