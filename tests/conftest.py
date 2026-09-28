"""Shared test setup: offscreen QPA (before any Qt import) and a clean
active theme after every test (theme switches are process-global)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from megacode import themes  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_theme():
    yield
    themes.set_active(themes.DEFAULT_SCHEME)
