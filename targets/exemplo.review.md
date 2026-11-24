# REVIEW.md — exemplo

O que o AI code review lê antes do diff de uma op do projeto de exemplo (v0.13). Copie este arquivo como
ponto de partida para o seu target: diga o que, no seu repositório, seguraria o merge.

## Crítico

- O pedido da spec não cumprido, ou trocado por outra coisa.
- Comportamento das funções que já existiam mudado (EX-01): `sem_acentos` e os testes dela.
- Dependência fora da biblioteca padrão (EX-02).

## Detalhe

Nome, comentário, docstring, caso de borda que a spec não nomeia.

Limite de detalhes: 3

## Caminhos sensíveis

- `tests/**`
