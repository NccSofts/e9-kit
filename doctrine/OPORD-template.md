# OPORD — modelo de spec (cinco parágrafos)

> A spec que o agente lê. Segue a ordem de operação militar de cinco parágrafos (Situação,
> Missão, Execução, Apoio, Comando e sinais), que obriga a responder perguntas que um prompt
> solto pula. Copie para `ops/<nome-da-tarefa>/OPORD.md`. O par dela é o `op.json`, que o
> script confere.

---

## OPORD — [NOME DA TAREFA] · [DATA]

**Cabeçalho**
- **Área:** repositório + branch (`feat/...`) + máquina de ensaio
- **Operador:** modelo e especialidade
- **Responsável:** quem escreveu a spec e vai revisar

---

### 1. SITUAÇÃO
- **a. Terreno:** o repositório, a branch e o banco de teste, e o que **não** faz parte da tarefa.
- **b. O problema:** o que precisa mudar, os riscos conhecidos e o que já foi tentado.
  *O pior jeito de isto dar errado.*
- **c. O que já existe:** código e funções a aproveitar (leia o código antes de escrever isto),
  e a verificação que cobre a tarefa.
- **d. Recursos:** modelo autorizado, ferramentas e comandos liberados.

### 2. MISSÃO
Uma frase: *quem* faz *o quê*, *onde* e *para quê*. Verbos mensuráveis; proibido "melhorar".

### 3. EXECUÇÃO
- **a. Intenção:** como o "pronto" se parece e por quê. Se o plano desmoronar, o operador age
  **em direção à intenção**, não fora dela.
- **b. Fases:** ler → implementar → testar → relatar.
- **c. FIXO × LIVRE:** o que não se negocia (intenção, fronteiras, testes antigos) e o que o
  operador escolhe (onde e como).
- **d. Critérios:** de pronto (objetivos), de abort (quando parar e reportar) e a verificação
  que tem de dar verde.

### 4. APOIO
Ambiente para rodar (venv, `.env` de teste, banco de teste), teto de tempo e custo, e os
**artefatos a entregar**: os arquivos que podem mudar, que são os mesmos do `op.json`.

### 5. COMANDO E SINAIS
- **a. Cadeia:** Comandante → E-9 → operador. O E-9 **não** faz push, merge nem deploy: isso é
  do Comandante, depois da revisão.
- **b. Relato:** relatório final com a seção `## Desvios da OPORD`; o E-9 escreve o AAR.

---

*A ROE está em `ROE-card.md` e entra no briefing sozinha: aqui vão só as ênfases da tarefa.*
