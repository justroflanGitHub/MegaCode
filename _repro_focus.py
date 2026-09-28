"""Does a dead pane keep (and keep receiving) keyboard focus in a shown window?"""
from __future__ import annotations

import os
import subprocess
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from megacode.workspace import WorkspaceView  # noqa: E402

app = QApplication([])
ws = WorkspaceView()
ws.show()
ws.start(3, "cmd /k echo alive", os.getcwd(), font_size=10, label="term")
app.processEvents()

a, b, c = (t.terminal for t in ws.tiles)
a.setFocus()
app.processEvents()
print("focus on A while alive:", QApplication.focusWidget() is a)

subprocess.run(["taskkill", "/F", "/PID", str(a._pty.pid)], capture_output=True, check=True)
for _ in range(200):
    app.processEvents()
    time.sleep(0.05)
    ws._tick()
    if a.is_dead():
        break
app.processEvents()
print("A dead:", a.is_dead())
print("focus STILL on dead A (nothing moved it):", QApplication.focusWidget() is a)
print("A focusPolicy:", a.focusPolicy(), "| A enabled:", a.isEnabled(),
      "| A visible:", a.isVisible())

# Clicking a dead pane: StrongFocus includes click-to-focus, so a user who
# clicks the dead tile and then types is in exactly this state.
b.setFocus()
app.processEvents()
a.setFocus()
app.processEvents()
print("re-focus dead A by click/tab after visiting B:", QApplication.focusWidget() is a)
ws.cleanup()
