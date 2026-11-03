# Spec — slug para títulos · exemplo do kit

**Cabeçalho**
- **Target:** `/tmp/e9-exemplo/projeto`, branch `feat/slug`, na máquina de ensaio
- **Agent:** Claude Code (`claude -p`), modelo `claude-sonnet-5-5`
- **Owner:** quem rodou o exemplo (revisa e faz o merge)

---

### 1. Context
- **a. Escopo:** um projeto Python mínimo, só biblioteca padrão. `texto.py` tem a função
  `sem_acentos`; os testes ficam em `tests/test_texto.py` e rodam com `unittest`.
- **b. Problema e riscos:** o projeto precisa transformar títulos em slugs para usar em URL e nome
  de arquivo. Risco: reescrever o que já existe em vez de reaproveitar.
- **c. O que já existe:** `sem_acentos(texto)` já tira acento e cedilha. Reaproveite.
- **d. Recursos:** só o comando de teste do projeto está liberado.

### 2. Objective
O agent implementa `slug(titulo)` em `texto.py`, com testes em `tests/test_texto.py`, para que
títulos virem identificadores seguros de URL.

### Requirements
- **FR-001** — `slug` devolve minúsculas, sem acento: `slug("Ação é Útil, de verdade!")` é
  `"acao-e-util-de-verdade"`.
- **FR-002** — tudo que não for letra ou dígito vira um hífen só, sem hífen repetido.
- **FR-003** — o slug não começa nem termina com hífen.
- **FR-004** — `slug("")` e `slug("!!!")` devolvem `""`.
- **FR-005** — os testes de `sem_acentos` continuam como estão, e há pelo menos um teste novo para cada requirement acima (sem aceite: o gate prova).

### 3. Execution
- **a. Intent:** `slug` existe, reaproveita `sem_acentos` e passa nos acceptance tests sem que
  ninguém precise mexer neles.
- **b. Plano:** ler `texto.py` → escrever os testes → implementar → rodar os testes → relatar.
- **c. FIXO × LIVRE:**
  - FIXO: a assinatura `slug(titulo: str) -> str`; os testes de `sem_acentos`; só biblioteca
    padrão.
  - LIVRE: como implementar (regex ou laço), o nome dos testes novos.
- **d. Regras do run:** a policy (`process/POLICY.md`). Escalate se um requirement contradisser
  outro.
- **e. Acceptance tests:** `tests/test_acceptance_slug.py`, com um teste por requirement, já está
  na worktree e falha hoje. Só leitura: faça passar sem editar.

### Guardrails
- [EX-01] aplica: `tests/test_texto.py` ganha testes novos para `slug`, e os de `sem_acentos`
  ficam como estão.
- [EX-02] aplica: `re` e `unicodedata`, da biblioteca padrão; `sem_acentos` já existe.

### 4. Support
Ambiente: Python 3 do sistema, sem venv. Teto: 20 minutos. Artefatos que podem mudar:
`texto.py` e `tests/test_texto.py`, e nenhum outro.

### 5. Communication
- **a. Responsabilidade:** o E-9 não faz push nem merge. O owner revisa a branch.
- **b. Relato:** relatório final com a última linha da saída dos testes e a seção
  `## Desvios da spec`.
