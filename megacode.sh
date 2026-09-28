#!/bin/sh
# MegaCode — запуск из дерева исходников (Astra Linux / Debian-10-эпоха).
#
#   ./megacode.sh          (или симлинк из ~/.local/bin)
#
# PYTHONUTF8 обязателен: в air-gap-сессиях часто нет LANG, и строки
# интерфейса (… — ⇉ ⛓) падают в default-ASCII stderr внутри excepthook.
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
export PYTHONPATH="$DIR/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m megacode "$@"
