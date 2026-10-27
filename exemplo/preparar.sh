#!/usr/bin/env sh
# Copia o projeto de exemplo para /tmp/e9-exemplo/projeto e faz dele um repositório git
# com um commit inicial. É o alvo da tarefa ops/exemplo-slug.
set -eu

DESTINO=/tmp/e9-exemplo
ORIGEM="$(cd "$(dirname "$0")" && pwd)/projeto"

if [ -e "$DESTINO" ]; then
    echo "Já existe $DESTINO. Apague à mão se quiser recomeçar do zero:"
    echo "  git -C $DESTINO/projeto worktree prune; rm -r $DESTINO"
    exit 1
fi

mkdir -p "$DESTINO"
cp -R "$ORIGEM" "$DESTINO/projeto"
cd "$DESTINO/projeto"
git init -q -b main
git add .gitignore texto.py tests/__init__.py tests/test_texto.py
git -c user.name=E-9 -c user.email=e9@localhost commit -q -m "chore: projeto de exemplo"
python3 -m unittest discover -s tests -t . 2>&1 | tail -1
echo "Pronto: $DESTINO/projeto"
