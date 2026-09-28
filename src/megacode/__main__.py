"""Entry point so the app can be run with ``python -m megacode``."""

from __future__ import annotations

from .runtime_env import prepare as _prepare_env

_prepare_env()  # QPA + fontconfig defaults must win over any Qt import

from .app import run  # noqa: E402 (import order is the point here)

if __name__ == "__main__":
    raise SystemExit(run())
