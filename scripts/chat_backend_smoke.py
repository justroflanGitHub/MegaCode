"""Live smoke test: drive the real ClaudeChatBackend against the real claude CLI.

Not part of the pytest suite (it costs an API turn and takes ~20-60s); run it
manually:  .venv/Scripts/python.exe scripts/chat_backend_smoke.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

from megacode.chat_backend import ClaudeChatBackend  # noqa: E402

app = QCoreApplication(sys.argv)

got = {"ready": [], "deltas": [], "turns": 0, "fails": []}
backend = ClaudeChatBackend(os.getcwd())
backend.ready.connect(lambda sid, model: got["ready"].append((sid, model)))
backend.delta.connect(lambda text: got["deltas"].append(text))


def _on_turn() -> None:
    got["turns"] += 1


backend.turn_finished.connect(_on_turn)
backend.failed.connect(lambda msg: got["fails"].append(msg))


def finish() -> None:
    backend.close()
    print("ready:", got["ready"])
    print("deltas:", repr("".join(got["deltas"]))[:200])
    print("turns:", got["turns"], "fails:", got["fails"])
    ok = (
        got["ready"]
        and got["deltas"]
        and got["turns"] >= 1
        and not got["fails"]
    )
    print("SMOKE:", "OK" if ok else "FAILED")
    app.exit(0 if ok else 1)


backend.send("Reply with exactly: ping")
QTimer.singleShot(90000, finish)
app.exec()
