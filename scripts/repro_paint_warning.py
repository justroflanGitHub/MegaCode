"""Repro/bisect: QPainter::end "saved states" warnings on the xcb platform.

Reported by a Debian user at startup (x4); reproduced on the Astra VM and
in the xvfb testbed (x2 on the launcher alone). Not reproducible on the
offscreen or windows QPA. Modes narrow the source widget down:

  (no mode)   the full launcher page
  preview     only the custom-painted LayoutPreview
  stock       only stock QLineEdit/QSpinBox/QPushButton/QLabel
"""
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from megacode.app import MainWindow
from megacode.layouts import Rect, compute_layout

mode = sys.argv[1] if len(sys.argv) > 1 else ""
app = QApplication([])

if mode == "mainwindow-noqss":
    # MainWindow but with the stylesheet neutered BEFORE __init__ applies it
    import megacode.app as appmod
    appmod.QSS = ""
    w = MainWindow()
    w.show()
elif mode == "launcher":
    # the full launcher page, but no WorkspaceView / SyncBus / QSS
    from megacode.app import LauncherPage
    page = LauncherPage()
    page.resize(520, 660)
    page.show()
elif mode.startswith("qss:"):
    # LauncherPage + one slice of the app stylesheet, to find the rule
    # behind the xcb-only "saved states" warnings
    from megacode.app import LauncherPage
    slice_name = mode.split(":", 1)[1]
    rules = {
        "root": "QWidget#root { background: #0e1014; }",
        "label": "QLabel { color: #e6e6e6; }",
        "lineedit": "QLineEdit { background: #161a21; border: 1px solid #232830;"
                    " border-radius: 8px; padding: 8px 10px; color: #e6e6e6; }",
        "spinbox": "QSpinBox { background: #161a21; border: 1px solid #232830;"
                   " border-radius: 8px; padding: 5px 6px; color: #e6e6e6; }",
        "countbtn": "QPushButton#count { background: #161a21;"
                    " border: 1px solid #232830; border-radius: 12px;"
                    " padding: 16px; color: #e6e6e6; font-size: 18px; }",
        "launchbtn": "QPushButton#launch { background: #d97757; border: none;"
                     " border-radius: 10px; padding: 14px; color: #1a120e; }",
        "combobox": "QComboBox { background: #161a21; border: 1px solid"
                    " #232830; border-radius: 8px; padding: 6px 10px;"
                    " color: #e6e6e6; }",
    }
    page = LauncherPage()
    page.setStyleSheet(rules[slice_name])
    page.resize(520, 660)
    page.show()
elif mode.startswith("spin:"):
    # QSpinBox narrowed property-by-property
    from megacode.app import LauncherPage
    slice_name = mode.split(":", 1)[1]
    rules = {
        "full": "QSpinBox { background: #161a21; border: 1px solid #232830;"
                " border-radius: 8px; padding: 5px 6px; color: #e6e6e6; }",
        "no-radius": "QSpinBox { background: #161a21; border: 1px solid"
                     " #232830; padding: 5px 6px; color: #e6e6e6; }",
        "no-border": "QSpinBox { background: #161a21; border-radius: 8px;"
                     " padding: 5px 6px; color: #e6e6e6; }",
        "no-padding": "QSpinBox { background: #161a21; border: 1px solid"
                      " #232830; border-radius: 8px; color: #e6e6e6; }",
        "radius0": "QSpinBox { background: #161a21; border: 1px solid"
                   " #232830; border-radius: 0px; padding: 5px 6px;"
                   " color: #e6e6e6; }",
        "subcontrols": "QSpinBox { background: #161a21; border: 1px solid"
                       " #232830; border-radius: 8px; padding: 5px 6px;"
                       " color: #e6e6e6; }"
                       "QSpinBox::up-button { background: #161a21; }"
                       "QSpinBox::down-button { background: #161a21; }",
        "sub-border-none": "QSpinBox { background: #161a21; border: 1px solid"
                           " #232830; border-radius: 8px; padding: 5px 6px;"
                           " color: #e6e6e6; }"
                           "QSpinBox::up-button { border: none; }"
                           "QSpinBox::down-button { border: none; }",
        "sub-transparent": "QSpinBox { background: #161a21; border: 1px solid"
                           " #232830; border-radius: 8px; padding: 5px 6px;"
                           " color: #e6e6e6; }"
                           "QSpinBox::up-button { background: transparent; }"
                           "QSpinBox::down-button { background: transparent; }",
        "sub-width": "QSpinBox { background: #161a21; border: 1px solid"
                     " #232830; border-radius: 8px; padding: 5px 6px;"
                     " color: #e6e6e6; }"
                     "QSpinBox::up-button { width: 18px; }"
                     "QSpinBox::down-button { width: 18px; }",
        "only-spinwidget-plain": "QAbstractSpinBox { background: #161a21;"
                                 " border: 1px solid #232830;"
                                 " border-radius: 8px; }",
    }
    page = LauncherPage()
    page.setStyleSheet(rules[slice_name])
    page.resize(520, 660)
    page.show()
elif mode == "mainwindow-bus":
    # MainWindow with the sync bus auto-start hard-gated off: isolates
    # SyncBus/QSS/workspace construction from a live bus
    import os
    os.environ["MEGACODE_NO_LINK"] = "1"
    w = MainWindow()
    w.show()
elif mode == "bus-alone":
    # only the SyncBus + its timers, no window beyond a dummy label
    from megacode import sync_security
    from megacode.sync_bus import SyncBus
    from PySide6.QtWidgets import QLabel as _L
    lbl = _L("bus test")
    lbl.show()
    bus = SyncBus(sync_security.state_dir())
    bus.start()
elif mode == "preview" or mode == "stock":
    page = QWidget()
    lay = QVBoxLayout(page)

    if mode == "preview":
        class Preview(QWidget):
            def paintEvent(self, _event) -> None:  # noqa: N802
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                rect = self.rect().adjusted(10, 10, -10, -10)
                area = Rect(rect.x(), rect.y(), rect.width(), rect.height())
                for cell in compute_layout(4, area, gap=10):
                    painter.setBrush(QColor("#3a3f4b"))
                    painter.drawRoundedRect(cell.x, cell.y, cell.w, cell.h,
                                            8, 8)

        from PySide6.QtCore import Qt  # noqa: F401 (used by Preview)
        lay.addWidget(Preview(), 1)
    else:
        row = QHBoxLayout()
        row.addWidget(QLabel("label"))
        row.addWidget(QLineEdit())
        row.addWidget(QSpinBox())
        row.addWidget(QPushButton("btn"))
        lay.addLayout(row)
    page.resize(500, 400)
    page.show()
else:
    w = MainWindow()
    w.show()

QTimer.singleShot(600, app.quit)
app.exec()
print("REPRO-OK")
