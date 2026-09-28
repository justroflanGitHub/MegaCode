#!/bin/sh
# Установка MegaCode на Astra Linux SE 1.7.6 (и системы эпохи Debian 10).
#
#   ./install-astra.sh                  онлайн: pip тянет колёса с PyPI
#   ./install-astra.sh --offline DIR    air-gap: DIR — каталог с колёсами
#                                       (как его собрать — README-ASTRA.md)
#
# Всё ставится в профиль пользователя (~/.local) — root не нужен.
set -eu

SRC=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX=${HOME}/.local
APPDIR=${PREFIX}/share/megacode
MODE=online
WHEELS=

while [ $# -gt 0 ]; do
  case "$1" in
    --offline) MODE=offline; WHEELS=$(CDPATH= cd -- "$2" && pwd); shift 2 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "неизвестный аргумент: $1 (см. --help)" >&2; exit 2 ;;
  esac
done

# --- 1/4 интерпретатор ------------------------------------------------------
command -v python3 >/dev/null 2>&1 || {
  echo "ОШИБКА: python3 не найден (в Astra 1.7.6 он есть из коробки)." >&2
  exit 1
}
python3 - <<'PY' || exit 1
import sys
if sys.version_info < (3, 7):
    sys.exit("ОШИБКА: нужен Python >= 3.7 (системный в Astra 1.7.6 — 3.7.3)")
PY

# --- 2/4 зависимости --------------------------------------------------------
if [ "$MODE" = offline ]; then
  echo "[2/4] зависимости offline из $WHEELS"
  python3 -m pip install --user --no-index --find-links="$WHEELS" \
    -r "$SRC/requirements-astra.txt"
else
  echo "[2/4] зависимости с PyPI"
  python3 -m pip install --user -r "$SRC/requirements-astra.txt"
fi

# --- 3/4 файлы приложения + лаунчер ----------------------------------------
echo "[3/4] установка в $APPDIR и $PREFIX/bin"
rm -rf "$APPDIR"
mkdir -p "$APPDIR" "$PREFIX/bin"
cp -r "$SRC/src" "$APPDIR/src"
# PYTHONUTF8: в сессиях без LANG строки UI (… — ⇉ ⛓) падают в ASCII stderr
cat > "$PREFIX/bin/megacode" <<EOF
#!/bin/sh
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
export PYTHONPATH="$APPDIR/src"\${PYTHONPATH:+:\$PYTHONPATH}
exec python3 -m megacode "\$@"
EOF
chmod 755 "$PREFIX/bin/megacode"

# --- 4/4 пункт меню Fly -----------------------------------------------------
echo "[4/4] пункт меню (~/.local/share/applications)"
mkdir -p "${HOME}/.local/share/applications"
sed "s#@BINDIR@#$PREFIX/bin#" "$SRC/packaging/megacode.desktop" \
  > "${HOME}/.local/share/applications/megacode.desktop"

echo
echo "Готово.  Запуск:  megacode   (или из меню приложений Fly)"
echo
echo "Если при старте Qt жалуется на системные библиотеки, поставьте их:"
echo "  sudo apt install libgl1 libegl1 libxkbcommon0 libgssapi-krb5-2 libxcb-cursor0"
echo "Глифы-иконки тулбара (⛓ ⛶ ＋) дорисует шрифтовой пакет (необязательно):"
echo "  sudo apt install fonts-noto-color-emoji"
