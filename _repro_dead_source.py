"""Adversarial verification: does a DEAD pane emit inputSent into live panes?"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtWidgets import QApplication

from megacode import terminal_widget as tw


class _FakePty:
    def __init__(self, *_a, **_k) -> None:
        self.written: List[str] = []
        self._alive = True

    def start(self, _on_output):
        return None

    def write(self, text):
        self.written.append(text)

    def resize(self, _c, _r):
        return None

    def is_alive(self):
        return self._alive

    def stop(self):
        self._alive = False


def make_term(qapp, label):
    saved = tw.Pty
    tw.Pty = _FakePty
    try:
        w = tw.TerminalWidget("fake", font_size=10)
    finally:
        tw.Pty = saved
    w._cell_w, w._cell_h = 10, 20
    w.resize(80 * 10, 24 * 20)
    w.show()
    qapp.processEvents()
    w.setWindowTitle(label)
    return w


def press(term, key, text=""):
    ev = QKeyEvent(QEvent := None or __import__("PySide6.QtCore", fromlist=["QEvent"]).QEvent.KeyPress,
                   key, Qt.KeyboardModifier.NoModifier, text=text)
    term.keyPressEvent(ev)


def main():
    qapp = QApplication.instance() or QApplication([])
    saved = tw.Pty
    tw.Pty = _FakePty
    saved_wp = None
    import megacode.workspace as wsm
    saved_wp = wsm.TerminalWidget
    wsm.TerminalWidget = tw.TerminalWidget  # ensure the real widget class
    try:
        from megacode.workspace import WorkspaceView
        ws = WorkspaceView()
        ws.start(3, "fake", os.getcwd(), font_size=10)
        ws.show()
        qapp.processEvents()
    finally:
        tw.Pty = saved
    qapp.processEvents()

    a, b, c = (t.terminal for t in ws.tiles)
    for t in (a, b, c):
        t._pty.write = t._pty.written.append  # keep recording

    # enable sync exactly like the toolbar does
    ws._sync_btn.setChecked(True)
    ws._on_sync_toggled(True)

    # Kill pane A the way the app does: the ticker calls tick() after exit.
    a._pty._alive = False
    a.tick()
    assert a.is_dead(), "A should be dead after tick()"
    qapp.processEvents()

    print("tile count after A exits:", len(ws.tiles))
    print("A visible/enabled:", a.isVisible(), a.isEnabled(),
          "focusPolicy:", a.focusPolicy())

    b._pty.written.clear()
    c._pty.written.clear()

    # 1) a keystroke delivered to the DEAD pane A
    press(a, Qt.Key.Key_D, text="d")
    qapp.processEvents()
    print("after key 'd' on dead A -> B:", b._pty.written, "C:", c._pty.written)

    # 2) a right-click QuickEdit paste on the DEAD pane A
    b._pty.written.clear()
    c._pty.written.clear()
    b._priv_modes.add(2004)  # bracketed paste, like a live PSReadLine shell
    QGuiApplication.clipboard().setText("rm -rf /tmp/x\necho done")
    # the real entry point: right-click QuickEdit paste on the dead pane
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent, QPointF
    me = QMouseEvent(QEvent.MouseButtonPress, QPointF(40, 40),
                     Qt.MouseButton.RightButton, Qt.MouseButton.RightButton,
                     Qt.KeyboardModifier.NoModifier)
    a.mousePressEvent(me)
    qapp.processEvents()
    print("A visible now:", a.isVisible(), "A has focus:", a.hasFocus())
    qapp.processEvents()
    print("after paste on dead A -> B:", b._pty.written)
    print("after paste on dead A -> C:", c._pty.written)
    print("B pending (Run pasted armed):", b.has_pending_input(),
          "exec btn enabled:", ws._exec_btn.isEnabled())
    print("A pending:", a.has_pending_input())
    ws.cleanup()


if __name__ == "__main__":
    main()
