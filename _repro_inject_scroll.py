"""Adversarial reproduction: does inject_input leave a scrolled-back pane frozen?"""
from __future__ import annotations
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from typing import List
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QKeyEvent
from PySide6.QtCore import Qt, QEvent

from megacode import terminal_widget as tw


class _FakePty:
    def __init__(self, *_a, **_k) -> None:
        self.written: List[str] = []

    def start(self, _on_output) -> None:
        return None

    def write(self, text: str) -> None:
        self.written.append(text)

    def resize(self, _cols, _rows) -> None:
        return None

    def is_alive(self) -> bool:
        return True

    def stop(self) -> None:
        return None


app = QApplication.instance() or QApplication([])
saved = tw.Pty
tw.Pty = _FakePty
try:
    A = tw.TerminalWidget("fake", font_size=10)
    B = tw.TerminalWidget("fake", font_size=10)
finally:
    tw.Pty = saved
for w in (A, B):
    w._cell_w, w._cell_h = 10, 20
    w.resize(80 * 10, 24 * 20)
    w.show()
app.processEvents()

# wire sync input the way WorkspaceView._new_tile does (without the workspace)
A.inputSent.connect(lambda seq, pasted, src=A: B.inject_input(seq, pasted=pasted))

# Build real history in B so the pane CAN be scrolled back, then scroll it.
for i in range(40):
    B._on_output(f"line{i:02d}\r\n")
B._scroll_offset = 6
B._build_view()  # sync the freeze baseline, as the scroll path would
assert B._scroll_offset == 6, B._scroll_offset
hist_len_before = B._history_len()

# User presses 'a' in pane A (the real local input path).
ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, text="a")
A.keyPressEvent(ev)

print("A scroll_offset after typing:", A._scroll_offset)
print("B scroll_offset after mirrored 'a':", B._scroll_offset)
print("B PTY received:", B._pty.written)
print("B history len:", hist_len_before, "->", B._history_len())

# Freeze check: what does B's viewport actually show now? Push the echo through
# the emulator (a real shell echoes the keystroke) and repaint.
B._on_output("a")  # the echo of the mirrored keystroke
rows, _ = B._build_view()
print("B top visible row now shows:", repr("".join(c.data for c in rows[0]).rstrip()))

# Same test for a mirrored paste (paste path in the source pane scrolls to bottom)
B._scroll_offset = 6
B._build_view()
from PySide6.QtGui import QGuiApplication
QGuiApplication.clipboard().setText("echo hi")
A.paste()
print("after mirrored paste: B scroll_offset =", B._scroll_offset,
      "pending =", B.has_pending_input(), "written =", B._pty.written[-1:])

A.close(); B.close()
