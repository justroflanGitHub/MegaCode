"""Entry point so the app can be run with ``python -m megacode``."""

from __future__ import annotations

from .app import run

if __name__ == "__main__":
    raise SystemExit(run())
