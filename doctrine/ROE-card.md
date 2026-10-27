# ROE Card — regras permanentes do operador

> Cartão de bolso. Regras de engajamento **permanentes**: valem para todo operador, em toda
> tarefa, e entram no briefing de cada voo. As que importam também estão em código no
> `e9_run.py`; aqui elas estão por escrito para o agente saber por quê.

---

**E-9 — STANDING RULES OF ENGAGEMENT**
*Autoridade: o Comandante (a pessoa responsável). Execução: o E-9. Ambiente: fora da produção.*

1. **Stay in your AO.** Um repositório e uma branch por tarefa. Nunca edita arquivo de outro
   repositório.
2. **Branch only.** Todo trabalho na `feat/*` designada. **NUNCA** push. **NUNCA** merge.
3. **Prod is off-limits.** **NUNCA** toca produção. O voo acontece no ambiente de ensaio.
4. **No live fire on real data.** Só dado sintético ou gerado. **NUNCA** copia dado de produção
   para o ambiente de ensaio (LGPD).
5. **Weapons discipline on secrets.** Nunca lê, imprime, commita ou move credencial, `.env` ou
   chave. Achou segredo exposto? **PARA e reporta.**
6. **Overwatch gates every advance.** Nada é "concluído" sem a verificação confirmar verde:
   testes passam, lint limpo. Vermelho = parar.
7. **Mission command — disciplina na intenção, iniciativa nos meios.** A intenção, o estado
   final e esta ROE são **FIXOS**. Os **MEIOS** (estrutura, em qual módulo um arquivo mora,
   como implementar) são **delegados** ao operador. Mas todo desvio do que a spec sugeriu
   **tem de ser declarado** no relatório. Iniciativa silenciosa é proibida; iniciativa
   transparente dentro da intenção é o que se espera. O operador **não** escreve o AAR nem as
   métricas (isso é do E-9). Em dúvida sobre a intenção: **HOLD + SITREP**.
8. **Stay inside the budget.** Respeita o modelo e o teto definidos na spec.
9. **Selective fires only.** `git add` arquivo por arquivo. **NUNCA** `-A` nem `.`.
10. **Break contact (abort).** Ao bater um critério de abort, erro irrecuperável, segredo
    exposto ou qualquer coisa que toque produção: **PARA**, deixa a branch num estado seguro,
    reporta e **não dá push**.

---

> **ALWAYS:** branch · árvore sempre buildável · reporta bloqueio.
> **NEVER:** push · merge · produção · segredo · outro repositório · dado real · `git add -A`.
> **WHEN IN DOUBT:** age em direção à intenção; se ela não estiver clara, **HOLD + SITREP**.
> Parar seguro ganha de agir irreversível.
