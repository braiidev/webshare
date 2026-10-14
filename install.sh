#!/usr/bin/env bash
# install.sh — Instala/actualiza WebShare
# Uso: curl -fsSL https://raw.githubusercontent.com/braiidev/webshare/main/install.sh | bash
# Sin sudo: codigo en ~/.local/share/webshare, comando en ~/.local/bin/webshare.
# Override de rutas para tests: WEBSHARE_DIR, WEBSHARE_BIN

set -euo pipefail

REPO_URL="https://github.com/braiidev/webshare.git"
TARGET="${WEBSHARE_DIR:-$HOME/.local/share/webshare}"
BIN="${WEBSHARE_BIN:-$HOME/.local/bin/webshare}"
UNINSTALL="${WEBSHARE_UNINSTALL:-0}"

step() { printf '[install] %s\n' "$*" >&2; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

if [ "$UNINSTALL" = "1" ]; then
    step "desinstalacion iniciada"
    if [ -L "$BIN" ] || [ -e "$BIN" ]; then
        rm -f "$BIN"
        step "symlink eliminado: $BIN"
    else
        step "no habia symlink en $BIN"
    fi
    if [ -d "$TARGET" ]; then
        rm -rf "$TARGET"
        step "repo instalado eliminado: $TARGET"
    fi
    step "desinstalacion completada"
    exit 0
fi

if [ "${1:-}" = "--uninstall" ]; then
    WEBSHARE_UNINSTALL=1 exec "$0"
fi

step "verificando herramientas"
command -v git >/dev/null 2>&1 || die "git no esta instalado"
command -v python3 >/dev/null 2>&1 || die "python3 no esta instalado"

mkdir -p "$(dirname "$BIN")"
if [ -d "$TARGET/.git" ]; then
    step "actualizando repo existente"
    if [ -n "$(git -C "$TARGET" status --porcelain)" ]; then
        step "guardando cambios locales temporales"
        git -C "$TARGET" stash push --include-untracked -m "webshare-install"
    fi
    git -C "$TARGET" fetch --all --prune
    git -C "$TARGET" pull --ff-only
else
    step "clonando WebShare"
    rm -rf "$TARGET"
    git clone "$REPO_URL" "$TARGET"
fi

step "asegurando ejecutable"
chmod 0755 "$TARGET/webshare.py"

if [ -e "$BIN" ] && [ ! -L "$BIN" ]; then
    step "respectando binario externo previo en $BIN"
else
    step "creando symlink $BIN -> $TARGET/webshare.py"
    ln -sfn "$TARGET/webshare.py" "$BIN"
fi

step "verificando comando"
if "$BIN" --version >/dev/null 2>&1; then
    "$BIN" --version
else
    die "el comando no responde"
fi

step "instalacion completada"
step "Documentacion: $TARGET/README.md"
step "TODO: $TARGET/TODO.md"
