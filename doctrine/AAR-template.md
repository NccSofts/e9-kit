# AAR — modelo de relatório pós-tarefa (After Action Review)

> O `e9_run.py` gera o `AAR.md` de cada voo com os fatos: verificação, custo, tempo, o relatório
> do operador e onde ele diverge do que foi medido. As perguntas 3 e 4 são humanas: preencha na
> revisão. Este modelo mostra o formato completo.

---

## AAR — [NOME DA TAREFA] · [DATA]

**Spec:** `ops/<nome-da-tarefa>/OPORD.md`

### As 4 perguntas
1. **O que devia acontecer?** A missão e o estado final da spec.
2. **O que de fato aconteceu?** Sem maquiar.
3. **Por quê?** A causa das diferenças, para os dois lados: o que deu certo também tem causa.
4. **O que manter e o que mudar?**
   - **Manter:** o que funcionou e vira padrão.
   - **Mudar:** o que falhou e como evitar na próxima (de preferência, em código).

### Medição
- **Primeira tentativa:** acertou? Errou como?
- **A cerca segurou?** A verificação barrou o que tinha de barrar?
- **Relatório × verificação:** o que o operador disse bate com o que foi medido?
- **Custo e tempo:** do operador e do voo inteiro.

### Situação final
- **Branch:** `feat/...` @ <sha>, verificação verde?
- **Pronto para merge?** Sim ou não, e o que falta.
- **Próximas tarefas:** o que vira spec da próxima.
