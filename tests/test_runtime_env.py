"""runtime_env.prepare(): the QPA platform default, set before Qt loads."""

from __future__ import annotations

import os
import sys


def test_prepare_sets_default_qpa_on_posix(monkeypatch):
    from megacode import runtime_env
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    runtime_env.prepare()
    assert os.environ["QT_QPA_PLATFORM"] == "xcb"


def test_prepare_respects_user_qpa(monkeypatch):
    from megacode import runtime_env
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("QT_QPA_PLATFORM", "wayland")
    runtime_env.prepare()
    assert os.environ["QT_QPA_PLATFORM"] == "wayland"


def test_prepare_noop_on_windows(monkeypatch):
    from megacode import runtime_env
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    runtime_env.prepare()
    assert "QT_QPA_PLATFORM" not in os.environ
