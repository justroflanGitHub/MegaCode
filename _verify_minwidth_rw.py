"""Independent verification of the 'window min width 755 / clipped status label' claim.

Measures, with the shipped QSS on real Windows fonts:
  * WorkspaceView.minimumSizeHint().width() (the new window width floor)
  * the toolbar row's layout minimum, and each item's contribution
  * how wide the title label actually gets at window widths 755/800/900/1000
Tiles are created with a stubbed Pty so no real process is spawned.
"""
import sys

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QWidget

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
w.ensurePolished()
w.start(4, "cmd", "D:/claude/MegaCode", font_size=10, label="term", kind="terminal")
w.ensurePolished()

bar = w.layout().itemAt(0).widget()
bar.ensurePolished()

print("== floors ==")
print("workspace.minimumSizeHint :", w.minimumSizeHint().width(), "x", w.minimumSizeHint().height())
print("toolbar.minimumSizeHint   :", bar.minimumSizeHint().width())
print("toolbar layout minimumSize:", bar.layout().minimumSize().width())
print("toolbar layout sizeHint   :", bar.layout().sizeHint().width())
print("splitter minimumSizeHint  :", w._splitter.minimumSizeHint().width())
print("root layout margins       :", w.layout().contentsMargins().left(), w.layout().contentsMargins().right())

print("\n== toolbar items (min / sizeHint / current) ==")
lay = bar.layout()
for i in range(lay.count()):
    it = lay.itemAt(i)
    wdg = it.widget()
    name = type(it).__name__
    label = ""
    if wdg is not None:
        label = (wdg.objectName() or "") + " " + (wdg.text() if hasattr(wdg, "text") else "")
    print(f"  [{i}] {name:14s} min={it.minimumSize().width():5d} hint={it.sizeHint().width():5d} "
          f"stretch={lay.stretch(i)} max={it.maximumSize().width():8d}  {label.strip()}")

print("\n== label ==")
lbl = w._count_label
print("label.minimumSize()       :", lbl.minimumSize().width())
print("label.minimumSizeHint()   :", lbl.minimumSizeHint().width())
print("label.sizeHint()          :", lbl.sizeHint().width())

print("\n== label allocation vs window width ==")
print(f"{'winW':>6} {'barW':>6} {'lblW':>6} {'bcastW':>7} {'execW':>6} {'syncW':>6} {'addW':>5} {'minW':>5} {'fsW':>5} {'stretch':>8}")
for win_w in (360, 640, 755, 760, 800, 900, 1000, 1280):
    w.setFixedSize(win_w, 620)
    w.layout().activate()
    app.processEvents()
    g = lbl.geometry()
    bc = w._broadcast_input.geometry()
    cells = [w._broadcast_btn, w._exec_btn, w._sync_btn, w._add_btn, w._min_btn, w._fs_btn]
    widths = [c.geometry().width() for c in cells]
    print(f"{win_w:>6} {bar.width():>6} {g.width():>6} {bc.width():>7} "
          f"{widths[1]:>6} {widths[2]:>6} {widths[3]:>5} {widths[4]:>5} {widths[5]:>5} "
          f"{bar.width() - (g.width() + bc.width() + sum(widths) + lay.spacing() * 8 + lay.contentsMargins().left() + lay.contentsMargins().right()):>8}")

print("\n== label text/clip at 755 ==")
w.setFixedSize(755, 620)
w.layout().activate()
app.processEvents()
fm = lbl.fontMetrics()
print("label width at 755:", lbl.geometry().width(), "| text:", lbl.text(),
      "| fm.horizontalAdvance(text):", fm.horizontalAdvance(lbl.text()))
w._flash_status("ran in 4 panes")
app.processEvents()
print("flash text:", lbl.text(), "| advance:", fm.horizontalAdvance(lbl.text()),
      "| label width:", lbl.geometry().width())
w._restore_status()

print("\n== what if the label had no 48px floor (simulate 123px min) ==")
lbl.setMinimumWidth(123)
print("workspace.minimumSizeHint :", w.minimumSizeHint().width())
print("toolbar layout minimumSize:", bar.layout().minimumSize().width())
lbl.setMinimumWidth(48)

print("\n== floors with 1 / 2 / 6 tiles ==")
for n in (1, 2, 6):
    w2 = WorkspaceView()
    w2.ensurePolished()
    w2.start(n, "cmd", "D:/claude/MegaCode", font_size=10, label="term", kind="terminal")
    w2.ensurePolished()
    print(f"  n={n}: workspace min={w2.minimumSizeHint().width()} toolbar layout min="
          f"{w2.layout().itemAt(0).widget().layout().minimumSize().width()} "
          f"splitter min={w2._splitter.minimumSizeHint().width()}")
    w2.cleanup()
    w2.deleteLater()

w.cleanup()
print("\ndone")
