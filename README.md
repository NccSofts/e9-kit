# E-9 kit

Um harness em Python, só biblioteca padrão, que roda o **Claude Code sem ninguém olhando**
(`claude -p`) numa cópia isolada do seu repositório e **decide de fora** se o resultado presta:
o agente não consegue fazer push, merge nem mexer em arquivo que a spec não liberou, e o
"está pronto" quem diz é a sua suíte de testes, não o agente.

Nasceu de onze tarefas reais num produto em produção. Cada regra daqui veio de uma tarefa
que deu errado.

## Nos termos da indústria

| Termo | No E-9 |
|---|---|
| **Spec-driven development** | `OPORD.md` (a spec, para o agente ler) + `op.json` (o contrato, para o script conferir) |
| **Context engineering** | agente novo por tarefa, worktree isolada, spec escrita depois de ler o código |
| **Harness engineering** | regras em código: git sem push/merge, spec validada, só entram os arquivos listados |
| **Loop engineering** | testes antes e depois do agente, teto de tempo, relatório (`AAR.md`) de todo voo |

O vocabulário militar (OPORD, ROE, AAR, overwatch) está traduzido no fim deste arquivo, e
termo por termo, com o nome de mercado, em `doctrine/GLOSSARIO.md`.

## O que mudou na v0.4

A pergunta que faltava ao harness era *"o pedido foi cumprido?"* — o gate só responde *"nada
quebrou?"*. Em duas tarefas reais o gate deu verde com o desenho errado, e numa terceira a spec
contradizia regras do próprio repositório que quem a escreveu esqueceu. Duas peças novas,
opcionais:

- **Aceite (acceptance tests).** Testes escritos por quem pede, antes do voo, em
  `ops/<tarefa>/aceite/`. O harness exige que **falhem** antes do agente, que **ninguém os
  edite** (confere o sha256) e que **passem inteiros, sem skip**, depois. Entram no commit como
  regressão. É o critério de pronto virando código: Hoare e Meyer, sem a cerimônia.
- **Travas do alvo (invariants).** `alvos/<repo>.md` lista, numeradas, as regras que toda
  mudança naquele repositório respeita. A spec tem de citar **cada uma** numa seção
  `## Travas do alvo` ("aplica: como" ou "não se aplica: porquê"), senão a tarefa é recusada já
  no `--dry-run`. A trava não é para o agente lembrar; é para quem escreve a spec não esquecer.

A tarefa de exemplo usa as duas.

## Requisitos

- **Linux ou WSL.** No Windows puro o script roda, mas sem duas proteções: matar o grupo inteiro
  de processos no estouro de tempo e matar o que o agente deixou rodando na worktree.
- Python 3.10 ou mais novo, git.
- [Claude Code](https://docs.claude.com/claude-code) instalado e com login feito (`claude` no PATH).
- **Uma máquina que não seja a de produção.** O kit recusa alvo que bata em `prod_markers`, mas
  quem garante de verdade é não ter credencial nem rota de produção no ambiente do agente.

## Começo rápido

```bash
# 1. os testes do próprio harness (não chamam o Claude)
python3 orchestrator/test_e9_run.py

# 2. cria o projeto de exemplo em /tmp/e9-exemplo/projeto
sh exemplo/preparar.sh

# 3. ensaio: valida a spec e mostra o plano, sem mexer em nada
python3 orchestrator/e9_run.py --op ops/exemplo-slug --dry-run

# 4. o voo de verdade (chama o Claude; custa alguns centavos de dólar)
python3 orchestrator/e9_run.py --op ops/exemplo-slug --live

# 5. o resultado
cat ops/exemplo-slug/AAR.md
git -C /tmp/e9-exemplo/wt-slug log --oneline -3
```

Verde: a branch `feat/slug` tem um commit, e o `AAR.md` mostra o relatório do agente ao lado do
que os testes mediram. Merge é com você.

## O ciclo

```
--dry-run (padrão) → worktree → testes no main limpo → agente → testes de novo
                   → só os arquivos listados → commit na branch, sem push → AAR
```

Cada jeito de falhar tem um código de saída:

| rc | Onde parou | O que quer dizer |
|---|---|---|
| 0 | fim | commit na branch, pronto para revisão |
| 2 | worktree | não montou a cópia isolada |
| 3 | verificação | o agente entregou e os testes reprovaram |
| 4 | commit | o agente mexeu em arquivo fora da lista |
| 5 | agente | estourou o teto de tempo |
| 6 | agente | o Claude Code terminou em erro, mesmo com rc 0 (login vencido, por exemplo) |
| 7 | linha de base | os testes já falhavam no main: o agente nem foi chamado |
| 8 | linha de base | a worktree estava suja de um voo anterior |
| 9 | aceite | o aceite já passava antes do agente, ou nem rodou como teste (erro de import na coleta) |
| 10 | aceite | o agente mexeu no aceite, ou ele não passou inteiro (falha, skip, contagem menor) |

## Sua própria tarefa

1. Crie `ops/<nome>/` com um `OPORD.md` (modelo em `doctrine/OPORD-template.md`) e um `op.json`
   (copie o do exemplo).
2. No `op.json`, os campos que importam:
   - `target_repo`, `worktree`, `branch` (sempre `feat/...`);
   - `prod_markers`: trechos que, se aparecerem no `target_repo`, querem dizer produção;
   - `expected_artifacts` (obrigatórios, aceitam glob) e `optional_artifacts`;
   - `overwatch.test_cmd`: o mesmo comando que o seu CI roda;
   - `operator.cmd_template`: o `claude -p` com `--allowedTools` liberando exatamente os comandos
     de teste. Em modo não interativo, comando fora da lista é recusado em silêncio;
   - `worktree_link`: arquivos fora do git que o projeto precisa para rodar (um `.env` **de
     teste**, por exemplo), ligados na worktree por link simbólico;
   - `venv`: se o projeto usa um, para os placeholders `{venv}` do comando.
3. Confira o `.gitignore` do projeto-alvo: cache e arquivo gerado pelos testes (`__pycache__`,
   `.pytest_cache`, cobertura) têm de estar nele. Senão o harness vê arquivo novo fora da lista e
   recusa o commit (rc 4), mesmo com tudo verde. O primeiro voo deste kit caiu exatamente nisso.
4. **Aceite (opcional, recomendado para feature):** ponha os testes em `ops/<nome>/aceite/` com o
   caminho que terão no projeto (`aceite/tests/test_aceite_x.py` vira `tests/test_aceite_x.py`) e
   declare `"aceite": {"cmd": "python3 -m unittest tests.test_aceite_x"}` (ou o pytest
   equivalente). **Importe o código novo dentro de cada teste**: import no topo do arquivo quebra a
   coleta, e erro de coleta não prova nada (rc 9).
5. **Travas (opcional):** crie `alvos/<nome>.md` com as regras numeradas (`[EX-01]`), declare
   `"alvo": "<nome>"` e escreva a seção `## Travas do alvo` na OPORD. Trava nova nasce quando
   uma tarefa, ou a revisão dela, achar uma regra que a spec não previu.
6. Rode sempre `--dry-run` antes do `--live`.

Para suítes longas, ponha `BASH_DEFAULT_TIMEOUT_MS=1200000 BASH_MAX_TIMEOUT_MS=1200000` na frente
do `claude -p` no `cmd_template`. Sem isso, o Claude Code manda para segundo plano todo comando de
mais de 2 minutos, e o agente nunca vê o fim dos testes.

## O que aprendemos (e virou código)

- **O relatório do agente é declaração; o teste é a prova.** O agente já disse "tudo verde" com
  cinco testes falhando e "418 passed" com a suíte em 814. O AAR põe os dois lado a lado.
- **Testar o main antes do agente.** Sem isso, um main já vermelho gasta uma hora de agente
  tentando consertar o que ele não quebrou.
- **`claude -p` com login vencido sai com código 0.** O erro só aparece no último evento da
  saída; o harness lê o evento, não o código.
- **Nada em segundo plano.** Em `claude -p`, quando o agente para de falar a sessão acaba; o que
  ele deixou rodando é morto antes da verificação.
- **Teste verde não prova o desenho.** Autorização errada e regra de negócio errada passaram
  verdes. Revisão humana antes do merge, sempre — e, desde a v0.4, o aceite escrito antes.
- **A spec também erra.** Uma ordem proibia justo o que duas regras do repositório exigiam;
  o agente obedeceu e o gate reprovou. As travas fazem o erro aparecer antes do voo.

## Limitações

- Uma tarefa por vez, e sem nova tentativa automática: o re-voo é decisão humana.
- O resumo da verificação no AAR entende a saída de `pytest`, `ruff` e `mypy`. Outros comandos
  funcionam como gate (o que vale é o código de saída), só que sem contagem no relatório.
- O aceite conta testes pelo resumo do `pytest` ou do `unittest`; outro runner precisa imprimir
  um dos dois formatos.
- Os textos do briefing e do AAR estão em português.

## Vocabulário

| No E-9 | Origem militar | Termo da indústria |
|---|---|---|
| OPORD | ordem de operação, cinco parágrafos | a spec |
| ROE | regras de engajamento | cercas e permissões do harness |
| Operador | quem executa a missão | o agente (`claude -p`) |
| Overwatch | cobertura de quem está no terreno | a verificação |
| AAR | After Action Review | o relatório de cada tarefa |
| Comandante | quem decide a missão | a pessoa responsável |
| Mission command | comando pela intenção | fixo × livre na spec, desvio declarado |
| Aceite | — | acceptance tests, escritos antes, imutáveis |
| Travas do alvo | — | invariantes do repositório |

A tabela completa, com os nomes de mercado, está em `doctrine/GLOSSARIO.md`.

## Licença

MIT. Veja `LICENSE`.
