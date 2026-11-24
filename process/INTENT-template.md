# Intent — template (v0.16)

> O que o owner escreve, em meia página, antes de qualquer spec: o problema, o resultado que conta como
> pronto, as restrições e o que ainda não sabe. Copiar para `ops/AAAA-MM-DD-<op>/intent.md`. A partir
> dele, `orchestrator/intent.py` rascunha a `spec.md`, o `op.json` e os acceptance tests, já passando pela
> validação do harness (policy, guardrails, traceability). O rascunho nasce com `"draft": true` no
> `op.json`, e o `--live` o recusa até o owner revisar e aprovar (`intent.py --aprovar`). Nunca vai
> direto a run.
>
> As quatro seções abaixo são obrigatórias nos nomes (o script as lê); Problema e Resultado esperado
> não podem ficar vazias. Escreva como falaria a quem vai implementar: o porquê vale mais que o como.

**Target:** `<nome em targets/, ex.: exemplo>`

## Problema

O que está errado ou faltando hoje, para quem, e como se percebe. Um caso concreto ajuda mais que uma
regra geral.

## Resultado esperado

Como o "pronto" se parece, do lado de quem usa: o que passa a acontecer, o que deixa de acontecer. Cada
frase aqui tende a virar um requirement `FR-NNN` com o teste que o prova.

## Restrições

O que não pode mudar, o que não está no escopo, decisões já tomadas (com quem decidiu), dados que não
se tocam.

## Perguntas em aberto

O que você ainda não decidiu. O rascunho as devolve como perguntas ao owner no topo da spec, e não as
responde por você.
