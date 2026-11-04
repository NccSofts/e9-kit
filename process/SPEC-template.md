# Spec — template

> A spec de um run do E-9: o pedido escrito antes do código, no vocabulário de SDLC. Copiar para
> `ops/<op>/spec.md`. Até a v0.5 do kit este documento se chamava OPORD (`OPORD.md`), no formato
> militar de cinco parágrafos; as ops antigas continuam com ele, e o harness lê os dois.

---

## Spec — [NOME DA OP] · [DATA]

**Cabeçalho**
- **Target:** repo + branch (`feat/...`) + máquina (o ambiente de ensaio, nunca a produção)
- **Agent:** modelo e o que ele faz nesta op
- **Owner:** quem define a intent e as regras de merge e deploy · executado pelo E-9

---

### 1. Context
- **a. Escopo:** repo exato, branch de trabalho, banco do run, e o que **não** faz parte (limites).
- **b. Problema e riscos:** o que resolver, os riscos conhecidos e o que já falhou antes. O pior
  jeito de isto dar errado.
- **c. O que já existe:** código e stack a reaproveitar, sistemas vizinhos, e o quality gate que
  cobre o run.
- **d. Recursos:** modelos autorizados, ferramentas liberadas, chaves disponíveis.

### 2. Objective
Uma frase, com quem faz o quê, onde, até quando e para quê. Verbos mensuráveis — proibido
"melhorar".

### Requirements
*(obrigatória quando a op tem acceptance tests — v0.8; IDs no formato do Spec Kit)*
Uma linha por requisito: `- **FR-001** — o que tem de ser verdade depois do run.` Cada teste de
aceite leva no nome o requisito que prova (`def test_fr_001_...`; um teste pode provar dois:
`test_fr_001_fr_002_...`). O `e9_run.py` recusa a op, inclusive no `--dry-run`, se um requisito
ficar sem teste, se um teste não disser qual requisito prova ou se citar um que não está aqui. O
requisito que nenhum teste prova diz isso na própria linha: `(sem aceite: o gate prova)`. O run
report traz a matriz requirement × test × base × entrega; o que já passa na base é guarda de
regressão, e fica marcado como tal. Se a spec veio do Spec Kit, os IDs passam iguais e os
artefatos dele ficam em `ops/<op>/spec/`. Para correção pequena, esta spec direta basta.

### 3. Execution
- **a. Intent:** o estado final nas palavras do owner — como o "pronto" se parece e por quê. Se o
  plano desmoronar no meio do run, o agent age **em direção à intent**, não improvisa fora dela.
- **b. Plano:** as fases em ordem (discovery → implementar → testar → relatar).
- **c. Tasks:** o que o agent faz; o que o quality gate e os acceptance tests conferem.
- **d. Regras do run:** a policy (`process/POLICY.md`), os critérios de pronto, os critérios de
  **escalate** (quando parar e perguntar) e o quality gate (não avança sem verde).
- **e. Acceptance tests (v0.4):** o estado final escrito como teste, antes do run, por quem não
  implementa — em `ops/<op>/acceptance/`, com o caminho que terá no target, e
  `"acceptance": {"cmd": ...}` no `op.json`. Tem de **falhar** hoje e **passar** depois, e o agent
  não o edita. Importe o código novo **dentro** da função de teste: `ImportError` na coleta não é
  falha, é rc 9. Com acceptance tests, a seção `Requirements` acima é obrigatória.

### Guardrails
*(obrigatória quando o `op.json` declara `"target"` — ver `targets/<target>.md`)*
Uma linha para **cada** guardrail do target: `[ID] aplica: como` ou `[ID] não se aplica: porquê`.
O `e9_run.py` recusa a op se faltar um. Quando uma ordem desta spec contradiz um guardrail, é aqui
que isso aparece — antes do run, e não no gate.

### 4. Support
Custo e recursos: teto de tempo e modelo, dependências para subir (banco do run, `.env`) e os
artefatos a entregar (branch com commit, relatório do agent, arquivos gerados).

### 5. Communication
- **a. Responsabilidade:** owner → E-9 → agent. O E-9 conduz, mas **não** dá push nem merge: isso
  é do owner.
- **b. Relato:** **status** e escalate quando travar; **run report** ao fim (`report.md`, ref.
  `process/REPORT-template.md`).

---

*Toda spec aponta para a `process/POLICY.md` — não repetir a policy aqui, só os desvios e ênfases.*
