"""Загрузочные переменные окружения — ДО импорта Qt.

Вызывается точками входа (``run_megacode.py`` / ``python -m megacode``)
до ``from megacode.app import run``: выбор QPA-платформы должен быть
задан до того, как Qt создаст приложение.

``QT_QPA_PLATFORM=xcb`` (setdefault — явное значение пользователя
главнее), только Linux. PyInstaller-бандл тащит все QPA-плагины скопом,
включая wayland; на wayland-сессиях Debian Qt по умолчанию пробует
wayland первым, а вендоренные wayland-библиотеки на чужих хостах
поднимаются не всегда — итог: «приложение запускается только с
QT_QPA_PLATFORM=xcb». Целевая платформа — Astra/Fly (X11), поэтому
надёжный дефолт — xcb; начисто Wayland-сессии отдельно не поддерживаются.

(Про fontconfig: его НЕ вендорим в бандл — библиотека конфиг-зависима,
хостовая версия всегда соответствует хостовым конфигам; см.
docker/Dockerfile.astra-build.)
"""

from __future__ import annotations

import os
import sys


def prepare() -> None:
    if sys.platform == "win32":
        return
    # не затираем явный выбор пользователя
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
