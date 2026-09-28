#!/bin/sh
# Запускает Xvfb и выполняет в нём python-скрипт с xcb-платформой.
# Использование: xvfb-repro.sh <script.py> [аргументы...]
set -e
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp &
XVFB_PID=$!
sleep 1
export DISPLAY=:99
export QT_QPA_PLATFORM=xcb
cd /work
PYTHONPATH=src python "$@"
rc=$?
kill $XVFB_PID 2>/dev/null || true
exit $rc
