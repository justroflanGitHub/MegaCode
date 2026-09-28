"""Part 2: how wide is the toolbar title label at each window width?"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

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

w = WorkspaceView()
w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
w.ensurePolished()
w.start(4, "cmd", "D:/claude/MegaCode", font_size=10, label="term", kind="terminal")
w.ensurePolished()
w.show()
app.processEvents()

bar = w.layout().itemAt(0).widget()
lbl = w._count_label

print(f"{'winW':>6} {'barW':>6} {'lblW':>6} {'bcast':>6} {'runAll':>6} {'runPas':>6} {'sync':>6} {'add':>5} {'min':>5} {'fs':>5} {'spare':>6}")
for win_w in (400, 640, 755, 760, 800, 850, 900, 1000, 1280):
    w.setFixedSize(win_w, 620)
    w.layout().activate()
    app.processEvents()
    cells = [w._broadcast_input, w._broadcast_btn, w._exec_btn, w._sync_btn,
             w._add_btn, w._min_btn, w._fs_btn]
    cw = [c.geometry().width() for c in cells]
    used = lbl.geometry().width() + sum(cw) + bar.layout().spacing() * 8 \
        + bar.layout().contentsMargins().left() + bar.layout().contentsMargins().right()
    print(f"{win_w:>6} {bar.width():>6} {lbl.geometry().width():>6} {cw[0]:>6} {cw[1]:>6} "
          f"{cw[2]:>6} {cw[3]:>6} {cw[4]:>5} {cw[5]:>5} {cw[6]:>5} {bar.width() - used:>6}")

fm = lbl.fontMetrics()
for text in ("MegaCode · 4 panes", "ran in 4 panes", "sync input on", "sync input off"):
    print(f"  advance('{text}') = {fm.horizontalAdvance(text)}px")

print("\n-- clipping check at each width --")
for win_w in (755, 800, 900, 1000):
    w.setFixedSize(win_w, 620)
    w.layout().activate()
    app.processEvents()
    avail = lbl.geometry().width()
    line = f"  winW={win_w}: label {avail}px |"
    for text in ("MegaCode · 4 panes", "ran in 4 panes", "sync input on"):
        line += f"  '{text}'={fm.horizontalAdvance(text)}px({'CLIPPED' if fm.horizontalAdvance(text) > avail else 'fits'})"
    print(line)

w.cleanup()
print("done")
