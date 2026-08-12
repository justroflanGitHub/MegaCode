"""MegaCode application: launcher + single-window terminal workspace.

Two screens live in one window:
  * the **launcher** (pick 2/3/4/6, working folder, launch), and
  * the **workspace** (one window hosting a draggable grid of embedded Claude
    Code terminals -- minimize/restore all at once, drag to swap).
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import traceback
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import terminal as separate_terminal
from .layouts import SUPPORTED, compute_layout
from .workspace import WorkspaceView

log = logging.getLogger("megacode")

# --- palette ----------------------------------------------------------------
BG = "#0e1014"
CARD = "#161a21"
BORDER = "#232830"
BORDER_HI = "#3a4150"
ACCENT = "#d97757"
ACCENT_HI = "#e08866"
TEXT = "#e6e6e6"
MUTED = "#8a93a3"
CELL = "#3a3f4b"

QSS = f"""
QWidget#root, QWidget#workspace {{ background: {BG}; }}
QLabel {{ color: {TEXT}; }}
QLabel#title {{ font-size: 22px; font-weight: 600; }}
QLabel#subtitle {{ color: {MUTED}; font-size: 12px; }}
QLabel#section {{ color: {MUTED}; font-size: 11px; }}
QLabel#status {{ color: {MUTED}; font-size: 11px; }}

QLineEdit {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 8px 10px; color: {TEXT};
}}
QLineEdit:focus {{ border: 1px solid {BORDER_HI}; }}
QSpinBox {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 5px 6px; color: {TEXT};
}}
QSpinBox:focus {{ border: 1px solid {BORDER_HI}; }}

QPushButton#count {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px;
    padding: 16px; color: {TEXT}; font-size: 18px; font-weight: 600;
}}
QPushButton#count:hover {{ border: 1px solid {BORDER_HI}; background: #1b2029; }}
QPushButton#count:checked {{
    background: #3a2a22; border: 1px solid {ACCENT}; color: #f3e3dc;
}}
QPushButton#secondary, QPushButton#toolbarBtn {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 8px 14px; color: {TEXT};
}}
QPushButton#secondary:hover, QPushButton#toolbarBtn:hover {{ border: 1px solid {BORDER_HI}; }}
QPushButton#launch {{
    background: {ACCENT}; border: none; border-radius: 10px;
    padding: 14px; color: #1a120e; font-size: 14px; font-weight: 700;
}}
QPushButton#launch:hover {{ background: {ACCENT_HI}; }}
QPushButton#launch:disabled {{ background: #3a3a3a; color: #777777; }}

/* workspace */
QFrame#toolbar {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}
QLabel#toolbarTitle {{ color: {TEXT}; font-size: 13px; font-weight: 600; }}
QFrame#tile {{ background: #1e1e1e; border: 1px solid #2a2a2a; border-radius: 6px; }}
QFrame#tile[drop="true"] {{ border: 2px solid {ACCENT}; }}
QFrame#tileHeader {{ background: #252526; border-top-left-radius: 6px; border-top-right-radius: 6px; }}
QLabel#tileGrip {{ color: #6a6a6a; font-size: 14px; }}
QLabel#tileTitle {{ color: #cccccc; font-size: 12px; }}
QPushButton#tileClose {{
    background: transparent; border: none; color: #9a9a9a; font-size: 16px; padding: 0 6px;
}}
QPushButton#tileClose:hover {{ color: #e74856; }}
"""


class LayoutPreview(QWidget):
    """Paints a scaled preview of the currently selected tiling."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._n = SUPPORTED[0]
        self.setMinimumHeight(150)

    def set_count(self, n: int) -> None:
        self._n = n
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt signature)
        from .layouts import Rect

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        inset = 10
        rect = self.rect().adjusted(inset, inset, -inset, -inset)
        area = Rect(rect.x(), rect.y(), rect.width(), rect.height())
        for cell in compute_layout(self._n, area, gap=10):
            painter.setBrush(QColor(CELL))
            painter.drawRoundedRect(cell.x, cell.y, cell.w, cell.h, 8, 8)


class LauncherPage(QWidget):
    """Choose instance count, folder and launch mode."""

    launch_workspace = Signal(int, str, int)   # (n, cwd, font_size)
    launch_separate = Signal(int, str, int)    # (n, cwd, gap)

    def __init__(self) -> None:
        super().__init__(objectName="root")
        self._count = SUPPORTED[0]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)

        layout.addWidget(self._header())
        layout.addWidget(self._count_row())
        layout.addWidget(self._preview(), 1)
        layout.addLayout(self._folder_row())
        layout.addLayout(self._options_row())
        layout.addStretch(1)
        layout.addWidget(self._primary_button())
        layout.addWidget(self._secondary_button())
        layout.addWidget(self._status_label())

    def _header(self) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        v.addWidget(QLabel("MegaCode", objectName="title"))
        v.addWidget(QLabel(
            "Launch multiple Claude Code sessions in one window, tiled and draggable.",
            objectName="subtitle", wordWrap=True,
        ))
        return box

    def _count_row(self) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        v.addWidget(QLabel("INSTANCES", objectName="section"))
        row = QHBoxLayout()
        row.setSpacing(10)
        self._count_group = QButtonGroup(self)
        self._count_group.setExclusive(True)
        for n in SUPPORTED:
            btn = QPushButton(str(n), objectName="count", checkable=True)
            btn.clicked.connect(lambda _=False, value=n: self._on_count(value))
            self._count_group.addButton(btn, n)
            row.addWidget(btn)
        row.itemAt(0).widget().setChecked(True)
        v.addLayout(row)
        return box

    def _preview(self) -> QWidget:
        self._preview_widget = LayoutPreview()
        return self._preview_widget

    def _folder_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(6)
        col.addWidget(QLabel("WORKING FOLDER", objectName="section"))
        line = QHBoxLayout()
        self._path_edit = QLineEdit(os.getcwd())
        browse = QPushButton("Browse…", objectName="secondary")
        browse.clicked.connect(self._browse)
        line.addWidget(self._path_edit, 1)
        line.addWidget(browse)
        col.addLayout(line)
        row.addLayout(col, 1)
        return row

    def _options_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(QLabel("FONT", objectName="section"))
        self._font_spin = QSpinBox()
        self._font_spin.setRange(7, 22)
        self._font_spin.setValue(11)
        self._font_spin.setFixedWidth(64)
        row.addWidget(self._font_spin)
        row.addSpacing(16)
        row.addWidget(QLabel("GAP", objectName="section"))
        self._gap_spin = QSpinBox()
        self._gap_spin.setRange(0, 64)
        self._gap_spin.setValue(6)
        self._gap_spin.setFixedWidth(64)
        row.addWidget(self._gap_spin)
        row.addStretch(1)
        return row

    def _primary_button(self) -> QWidget:
        self._launch_btn = QPushButton("Launch in workspace", objectName="launch")
        self._launch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._launch_btn.clicked.connect(self._emit_workspace)
        return self._launch_btn

    def _secondary_button(self) -> QWidget:
        btn = QPushButton("Open as separate windows", objectName="secondary")
        btn.clicked.connect(self._emit_separate)
        return btn

    def _status_label(self) -> QWidget:
        self._status = QLabel("Ready.", objectName="status", wordWrap=True)
        return self._status

    # --- handlers -----------------------------------------------------------
    def _on_count(self, n: int) -> None:
        self._count = n
        self._preview_widget.set_count(n)

    def _browse(self) -> None:
        start = self._path_edit.text() or os.getcwd()
        chosen = QFileDialog.getExistingDirectory(self, "Select working folder", start)
        if chosen:
            self._path_edit.setText(chosen)

    def _validated_cwd(self) -> Optional[str]:
        cwd = self._path_edit.text().strip()
        if not cwd or not os.path.isdir(cwd):
            QMessageBox.warning(self, "MegaCode", "Please choose an existing working folder.")
            return None
        return cwd

    def _emit_workspace(self) -> None:
        cwd = self._validated_cwd()
        if cwd:
            self.launch_workspace.emit(self._count, cwd, self._font_spin.value())

    def _emit_separate(self) -> None:
        cwd = self._validated_cwd()
        if cwd:
            self.launch_separate.emit(self._count, cwd, self._gap_spin.value())

    def set_status(self, text: str) -> None:
        self._status.setText(text)


class SeparateWorker(QThread):
    """Runs the separate-Windows-Terminal launch off the UI thread."""

    status = Signal(str)
    finished_ok = Signal(int)
    failed = Signal(str)

    def __init__(self, n: int, cwd: str, gap: int, monitor_hwnd: int) -> None:
        super().__init__()
        self._n = n
        self._cwd = cwd
        self._gap = gap
        self._monitor_hwnd = monitor_hwnd

    def run(self) -> None:  # noqa: N802 (Qt signature)
        try:
            separate_terminal.launch_and_arrange(
                self._n, self._cwd, gap=self._gap,
                monitor_hwnd=self._monitor_hwnd, status=self.status.emit,
            )
            self.finished_ok.emit(self._n)
        except Exception as exc:  # noqa: BLE001
            log.exception("separate launch failed")
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MegaCode")
        self._worker: Optional[SeparateWorker] = None

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._launcher = LauncherPage()
        self._launcher.launch_workspace.connect(self._on_launch_workspace)
        self._launcher.launch_separate.connect(self._on_launch_separate)
        self._stack.addWidget(self._launcher)  # index 0

        self._workspace = WorkspaceView()
        self._workspace.all_closed.connect(self._on_all_closed)
        self._stack.addWidget(self._workspace)  # index 1

        self.setStyleSheet(QSS)
        self._enter_launcher()

    # --- navigation ---------------------------------------------------------
    def _enter_launcher(self) -> None:
        self._stack.setCurrentWidget(self._launcher)
        self.setFixedSize(520, 660)
        self._center()

    def _enter_workspace(self) -> None:
        self.setMaximumSize(16777215, 16777215)
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else None
        if geo is not None:
            self.resize(int(geo.width() * 0.92), int(geo.height() * 0.92))
        else:
            self.resize(1500, 900)
        self._stack.setCurrentWidget(self._workspace)
        self._center()

    def _center(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        fg = self.frameGeometry()
        fg.moveCenter(geo.center())
        self.move(fg.topLeft())

    # --- actions ------------------------------------------------------------
    def _on_launch_workspace(self, n: int, cwd: str, font_size: int) -> None:
        claude = shutil.which("claude") or shutil.which("claude.exe")
        if not claude:
            QMessageBox.critical(self, "MegaCode", "claude was not found on PATH.")
            return
        try:
            self._workspace.start(n, claude, cwd, font_size=font_size)
        except Exception as exc:  # noqa: BLE001
            log.exception("workspace start failed")
            QMessageBox.critical(self, "MegaCode", f"Failed to start terminals:\n{exc}")
            return
        self._enter_workspace()

    def _on_launch_separate(self, n: int, cwd: str, gap: int) -> None:
        self._launcher.set_status("Starting…")
        self._worker = SeparateWorker(n, cwd, gap, int(self.winId()))
        self._worker.status.connect(self._launcher.set_status)
        self._worker.finished_ok.connect(
            lambda k: self._launcher.set_status(f"Launched {k} separate window(s).")
        )
        self._worker.failed.connect(self._on_separate_failed)
        self._worker.start()

    def _on_separate_failed(self, message: str) -> None:
        self._launcher.set_status("Failed.")
        QMessageBox.critical(self, "MegaCode", message)
        self._worker = None

    def _on_all_closed(self) -> None:
        self._enter_launcher()
        self._launcher.set_status("All terminals closed.")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        self._workspace.cleanup()
        super().closeEvent(event)


def _setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        log_path = os.path.join(tempfile.gettempdir(), "megacode.log")
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:  # noqa: BLE001
        pass
    if sys.stderr:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)


def _excepthook(exc_type, exc_value, tb) -> None:
    logging.getLogger("megacode").critical(
        "Uncaught exception", exc_info=(exc_type, exc_value, tb)
    )
    if QApplication.instance():
        text = "".join(traceback.format_exception(exc_type, exc_value, tb))
        QMessageBox.critical(None, "MegaCode - error", text)


def run() -> int:
    _setup_logging()
    sys.excepthook = _excepthook

    app = QApplication(sys.argv)
    app.setApplicationName("MegaCode")
    app.setApplicationDisplayName("MegaCode")
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    app.setPalette(palette)

    window = MainWindow()
    window.show()
    return app.exec()
