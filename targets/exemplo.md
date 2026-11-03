# Guardrails do target — projeto de exemplo

As regras que toda mudança no projeto de exemplo respeita. Cada op com `"target": "exemplo"` cita
todas numa seção `## Guardrails` da spec, com `aplica: como` ou `não se aplica: porquê`. Troque por
regras do seu repositório: os melhores guardrails são os que já custaram um run.

- **[EX-01] Os testes de `sem_acentos` não mudam.** São o contrato da função que já existe.
- **[EX-02] Só biblioteca padrão.** Nada de dependência nova no projeto.
