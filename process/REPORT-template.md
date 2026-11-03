# Run report — template

> O relatório de fim de run do E-9: as quatro perguntas de uma retrospectiva + a camada de
> **engineering metrics** (provar que o agent escreveu código certo, e medir quando não escreveu).
> O harness gera `ops/<op>/report.md` sozinho; a análise humana entra depois. Até a v0.5 do kit se
> chamava AAR (`AAR.md`); as ops antigas seguem com ele, e o harness lê os dois.

---

## Run report — [NOME DA OP] · [DATA]

**Ref:** spec em `ops/<op>/spec.md`

### As 4 perguntas
1. **O que devia acontecer?** (o objective e o estado final da spec)
2. **O que de fato aconteceu?** (sem maquiar — o delta nu)
3. **Por quê?** (causa-raiz das diferenças, dos dois lados: o que deu certo também tem causa)
4. **O que manter / melhorar?**

### Engineering metrics
- **Tentativas do agent** e se acertou de primeira.
- **O gate segurou?** barrou o que tinha de barrar?
- **Acceptance tests e traceability:** a matriz requirement × test × base × entrega.
- **AI code review** (se ligado): o veredito e os achados — informa, não decide.
- **Custo:** tokens e US$ por run. **Tempo:** wall-clock.

### Situação final
- **Branch:** `feat/...` @ <sha> — buildável? gate verde?
- **Pronta para merge?** sim / não — se não, o que falta.
- **Bloqueios:** o que travou e exigiu o owner.
- **Próximos passos:** o que vira a próxima spec.

### Revisão no target
Depois do merge, `python3 orchestrator/revisar.py --op ops/<op> --como-veio | --consertado "…" |
--descartado "…"` grava o desfecho: o número que o gate não dá.
