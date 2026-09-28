"""App-level behavior: theme setting load, KeyboardInterrupt excepthook
branch and the SIGINT-to-quit path (the Debian report: ^C left the app
unkillable because KeyboardInterrupt surfaced inside Qt timer slots)."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import megacode.app as appmod  # noqa: E402
from megacode import themes  # noqa: E402


@pytest.fixture()
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _SettingsStub:
    def __init__(self, stored: str) -> None:
        self._stored = stored

    def value(self, _key, default="", type=str):  # noqa: A002 (Qt signature)
        return self._stored


def test_load_theme_setting_prefers_stored_name(monkeypatch):
    monkeypatch.setattr(
        appmod, "QSettings", lambda: _SettingsStub("marine-night"))
    assert appmod._load_theme_setting() == "marine-night"


def test_load_theme_setting_falls_back_on_unknown_name(monkeypatch):
    monkeypatch.setattr(
        appmod, "QSettings", lambda: _SettingsStub("deleted-scheme"))
    assert appmod._load_theme_setting() == themes.DEFAULT_SCHEME


def test_load_theme_setting_default_on_first_run(monkeypatch):
    monkeypatch.setattr(appmod, "QSettings", lambda: _SettingsStub(""))
    assert appmod._load_theme_setting() == themes.DEFAULT_SCHEME


def test_excepthook_skips_modal_box_for_keyboard_interrupt(monkeypatch):
    """^C in a slot must not pop a modal error dialog -- that box was part
    of why the process seemed impossible to stop."""
    shown = []
    monkeypatch.setattr(
        appmod.QMessageBox, "critical",
        lambda *a, **k: shown.append(a))
    appmod._excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    assert shown == []


def test_excepthook_still_shows_real_exceptions(qapp, monkeypatch):
    shown = []
    monkeypatch.setattr(
        appmod.QMessageBox, "critical",
        lambda *a, **k: shown.append(a))
    try:
        raise ValueError("boom")
    except ValueError:
        appmod._excepthook(*sys.exc_info())
    assert shown  # a real crash still reaches the user


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")
def test_sigint_schedules_quit(qapp, monkeypatch):
    """The installed handler must route ^C to app.quit(), not to a
    KeyboardInterrupt inside whatever slot runs next."""
    import signal
    import time

    quit_called = []
    # instance attribute shadows the bound method for the handler's
    # ``app.quit`` lookup without touching Qt's C++ side
    monkeypatch.setattr(qapp, "quit", lambda: quit_called.append(True),
                        raising=False)
    previous = signal.getsignal(signal.SIGINT)
    appmod._install_quit_signals(qapp)
    try:
        os.kill(os.getpid(), signal.SIGINT)
        deadline = time.time() + 2
        while not quit_called and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.02)
    finally:
        signal.signal(signal.SIGINT, previous)
    assert quit_called, "SIGINT did not reach app.quit()"
