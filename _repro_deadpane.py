"""Adversarial verification: does a DEAD pane still emit inputSent, so sync
mode injects its keys/pastes into the live panes?

Two levels:
  A. Real PTYs (real pywinpty children that exit on their own) -- proves the
     dead pane's write() does not raise before inputSent.emit, and that the
     fan-out really happens.
  B. Real WorkspaceView wiring (the _mirror_input lambda from _new_tile).
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from megacode.workspace import WorkspaceView  # noqa: E402

app = QApplication([])

ws = WorkspaceView()
ws.start(3, "cmd /k echo alive", os.getcwd(), font_size=10, label="term")
app.processEvents()

a, b, c = (t.terminal for t in ws.tiles)
print("panes:", [id(x) % 100000 for x in (a, b, c)])

# Record exactly what lands in the two live panes' PTYs.
got = {"b": [], "c": []}
real_b_write, real_c_write = b._pty.write, c._pty.write
b._pty.write = lambda text: got["b"].append(text)  # type: ignore[method-assign]
c._pty.write = lambda text: got["c"].append(text)  # type: ignore[method-assign]

# Sync input ON, exactly as the toolbar toggle does.
ws._sync_btn.setChecked(True)

# Kill only pane A's child, WITHOUT Pty.stop() (that would set _closed and
# change the write() path). A bare TerminateProcess mimics the child exiting on
# its own: _closed stays False, is_alive() goes False, tick() flags it dead.
import time
import subprocess
subprocess.run(["taskkill", "/F", "/PID", str(a._pty.pid)],
               capture_output=True, check=True)
for _ in range(200):
    app.processEvents()
    time.sleep(0.05)
    ws._tick()
    if a.is_dead():
        break
print("pane A dead:", a.is_dead(), "| B dead:", b.is_dead(), "| C dead:", c.is_dead())
assert a.is_dead(), "pane A never died -- setup failed"
assert not b.is_dead() and not c.is_dead()

# Is A still able to take keyboard focus, as the claim states?
a.setFocus()
app.processEvents()
print("focus after A.setFocus():", QApplication.focusWidget() is a)

# 1) A keystroke on the DEAD pane.
a.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_D,
                          Qt.KeyboardModifier.NoModifier, "d"))
app.processEvents()
print("after key 'd' on dead pane -> B:", got["b"], "C:", got["c"])

# 2) A QuickEdit-style right-click paste on the DEAD pane.
QGuiApplication.clipboard().setText("rm -rf /tmp/x\necho done")
a.paste()
app.processEvents()
print("after paste on dead pane -> B:", got["b"])
print("                            C:", got["c"])

# 3) Does the paste ARM a live pane's "Run pasted" button?
print("B pending:", b.has_pending_input(), "C pending:", c.has_pending_input())
ws._update_exec_button()
print("'Run pasted' enabled:", ws._exec_btn.isEnabled())

danger_b = any("rm -rf" in s for s in got["b"])
danger_c = any("rm -rf" in s for s in got["c"])
print()
print("VERDICT: dead pane's input mirrored into live panes =",
      bool(got["b"] or got["c"]), "| clipboard payload reached live panes =",
      bool(danger_b or danger_c))
ws.cleanup()
