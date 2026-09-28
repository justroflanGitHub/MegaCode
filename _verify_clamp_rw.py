"""Does Qt itself clamp a top-level resize to the layout minimum
(independent of the explicit setMinimumSize)?"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMainWindow

app = QApplication(sys.argv + ["-platform", "windows"])

import megacode.terminal_widget as tw
from megacode.app import QSS
from megacode.workspace import WorkspaceView


class FakePty:
    def __init__(self, *a, **k):
        pass

    def start(self, *a, **k):
        pass

    def write(self, *a, **k):
        pass

    def resize(self, *a, **k):
        pass

    def is_alive(self):
        return True

    def stop(self):
        pass


tw.Pty = FakePty
app.setStyleSheet(QSS)


def probe(label, minw, minh):
    w = WorkspaceView()
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win = QMainWindow()
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.setCentralWidget(w)
    win.setStyleSheet(QSS)
    w.start(4, "cmd", "D:/claude/MegaCode", font_size=10, label="term", kind="terminal")
    win.show()
    app.processEvents()
    print(f"\n[{label}] explicit min={minw}x{minh} workspace minSizeHint={w.minimumSizeHint().width()}")
    print(f"  win.minimumSizeHint={win.minimumSizeHint().width()}x{win.minimumSizeHint().height()}"
          f"  win.minimumSizeHintHint(QMainWindow layout min)={win.layout().minimumSize().width()}")
    win.setMinimumSize(minw, minh)
    win.setMaximumSize(16777215, 16777215)
    print(f"  before resize: {win.width()}x{win.height()}")
    win.resize(400, 620)
    app.processEvents()
    print(f"  after resize(400,620): {win.width()}x{win.height()}")
    win.resize(360, 620)
    app.processEvents()
    print(f"  after resize(360,620): {win.width()}x{win.height()}")
    win.cleanup() if hasattr(win, 'cleanup') else None
    w.cleanup()
    win.close()


probe("OLD behaviour (360 floor)", 360, 260)
probe("NEW behaviour (755 floor)", 755, 260)
print("\ndone")
