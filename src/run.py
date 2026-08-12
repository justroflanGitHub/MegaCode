"""Top-level launcher for the frozen (PyInstaller) build.

This file is intentionally *outside* the ``megacode`` package and uses an
absolute import, which is what PyInstaller needs: the entry script becomes
``__main__``, so a relative import (as in ``megacode/__main__.py``) would have
no parent package to resolve against and would fail at runtime.

For development, prefer ``python -m megacode`` instead.
"""

from __future__ import annotations

from megacode.app import run

if __name__ == "__main__":
    raise SystemExit(run())
