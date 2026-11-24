# REVIEW.md — template (v0.13)

> O que o AI code review lê, por target, antes de olhar o diff: o que é **crítico** (seguraria o merge)
> e o que é **detalhe** (só se menciona, e com limite). Copiar para `targets/<target>.review.md`. Sem
> ele, o reviewer usa este arquivo como está. O formato é o do *AI-native SDLC playbook* da Anthropic:
> dizer o que importa no repositório, em vez de pedir ao modelo que adivinhe.
>
> O harness lê três coisas daqui, por nome de seção: `## Crítico`, `## Caminhos sensíveis` (globs entre
> crases) e a linha `Limite de detalhes: N`. O resto é texto para o reviewer.

## Crítico

Achado nestas categorias é crítico (`critico: true` no parecer) e, no veto em sombra, seguraria a
entrega:

- O pedido da spec não cumprido, cumprido pela metade ou trocado por outra coisa.
- Autorização e escopo de dados: quem pode chamar o quê, e o que vaza entre usuários ou clientes.
- Perda ou corrupção de dado: migration irreversível, apagar o que não devia, gravar o que não devia.
- Segredo, credencial ou dado pessoal em código, log, teste ou mensagem de erro.
- Tratamento de erro largo demais, que esconde ou rotula errado uma falha.
- Guardrail do target violado.

## Detalhe

Vale mencionar, mas não segura nada: nome confuso, comentário que mente, caso de borda menor que a spec
não nomeia, duplicação pequena. Estilo, formatação e tipos são do quality gate: não comente.

Limite de detalhes: 5

## Os três passes

1. **Bugs e regra de negócio:** o código faz o que os testes dele afirmam, mas faz o que a spec quer?
2. **Segurança:** autorização, escopo de dados, segredo, entrada não confiável.
3. **Aderência:** cada requirement da spec, cada guardrail do target, e os desvios que o diff mostra.

No passe rápido (mudança pequena) só o 1 e o 3, com no máximo 3 achados. No fundo, cada passe é uma
chamada separada.

## Caminhos sensíveis

Mudança que toca um destes nunca é passe rápido, mesmo pequena:

- `**/auth*`
- `**/migrations/**`
- `**/*permiss*`
