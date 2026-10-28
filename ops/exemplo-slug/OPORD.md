# OPORD — slug para títulos · exemplo do kit

**Cabeçalho**
- **Área:** `/tmp/e9-exemplo/projeto`, branch `feat/slug`, na máquina de ensaio
- **Operador:** Claude Code (`claude -p`), modelo sonnet
- **Responsável:** quem rodou o exemplo

---

### 1. SITUAÇÃO
- **a. Terreno:** um projeto Python mínimo, só biblioteca padrão. `texto.py` tem a função
  `sem_acentos`; os testes ficam em `tests/test_texto.py` e rodam com `unittest`.
- **b. O problema:** o projeto precisa transformar títulos em slugs para usar em URL e nome de
  arquivo. Risco: reescrever o que já existe em vez de reaproveitar.
- **c. O que já existe:** `sem_acentos(texto)` já tira acento e cedilha. Reaproveite.
- **d. Recursos:** só o comando de teste do projeto está liberado.

### 2. MISSÃO
O operador implementa `slug(titulo)` em `texto.py`, com testes em `tests/test_texto.py`, para
que títulos virem identificadores seguros de URL.

### 3. EXECUÇÃO
- **a. Intenção:** `slug("Ação é Útil, de verdade!")` devolve `"acao-e-util-de-verdade"`.
- **b. Fases:** ler `texto.py` → escrever os testes → implementar → rodar os testes → relatar.
- **c. FIXO × LIVRE:**
  - FIXO: a assinatura `slug(titulo: str) -> str`; os testes de `sem_acentos` continuam como
    estão; só biblioteca padrão.
  - LIVRE: como implementar (regex ou laço), o nome dos testes.
- **d. Critérios de pronto:**
  - minúsculas, sem acento;
  - tudo que não for letra ou dígito vira um hífen só (sem hífen repetido);
  - sem hífen no começo nem no fim;
  - `slug("")` e `slug("!!!")` devolvem `""`;
  - pelo menos um teste para cada item acima, e todos verdes.
- **e. Aceite:** `tests/test_aceite_slug.py`, com os critérios acima, já está na worktree e
  falha hoje. Só leitura: faça passar sem editar.

### Travas do alvo
- [EX-01] aplica: `tests/test_texto.py` ganha testes novos para `slug`, e os de `sem_acentos`
  ficam como estão.
- [EX-02] aplica: `re` e `unicodedata`, da biblioteca padrão; `sem_acentos` já existe.

### 4. APOIO
Ambiente: Python 3 do sistema, sem venv. Teto: 20 minutos. Artefatos que podem mudar:
`texto.py` e `tests/test_texto.py`, e nenhum outro.

### 5. COMANDO E SINAIS
- **a. Cadeia:** o E-9 não faz push nem merge. Quem rodou o exemplo revisa a branch.
- **b. Relato:** relatório final com a última linha da saída dos testes e a seção
  `## Desvios da OPORD`.
