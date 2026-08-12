"""PySide6 user interface for MegaCode.

A small, modern, dark-themed window that lets the user pick how many Claude
Code instances to launch (2, 3, 4 or 6), choose a working folder, and fire
them off — tiled neatly across the monitor.
"""

from __future__ import annotations

import logging
import os
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
    QVBoxLayout,
    QWidget,
)

from .layouts import SUPPORTED, compute_layout
from .terminal import launch_and_arrange

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
QWidget#root {{ background: {BG}; }}
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

QPushButton#secondary {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 8px 14px; color: {TEXT};
}}
QPushButton#secondary:hover {{ border: 1px solid {BORDER_HI}; }}

QPushButton#launch {{
    background: {ACCENT}; border: none; border-radius: 10px;
    padding: 14px; color: #1a120e; font-size: 14px; font-weight: 700;
}}
QPushButton#launch:hover {{ background: {ACCENT_HI}; }}
QPushButton#launch:disabled {{ background: #3a3a3a; color: #777777; }}
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
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        inset = 10
        area_rect = self.rect().adjusted(inset, inset, -inset, -inset)
        # local Rect in the same coordinate space the layout math expects
        from .layouts import Rect  # local import keeps the widget self-contained

        area = Rect(area_rect.x(), area_rect.y(), area_rect.width(), area_rect.height())
        for cell in compute_layout(self._n, area, gap=10):
            painter.setBrush(QColor(CELL))
            painter.drawRoundedRect(cell.x, cell.y, cell.w, cell.h, 8, 8)


class LaunchWorker(QThread):
    """Runs :func:`launch_and_arrange` off the UI thread."""

    status = Signal(str)
    finished_ok = Signal(int)
    failed = Signal(str)

    def __init__(
        self, n: int, working_dir: str, gap: int, monitor_hwnd: int
    ) -> None:
        super().__init__()
        self._n = n
        self._working_dir = working_dir
        self._gap = gap
        self._monitor_hwnd = monitor_hwnd

    def run(self) -> None:  # noqa: N802 (Qt signature)
        try:
            launch_and_arrange(
                self._n,
                self._working_dir,
                gap=self._gap,
                monitor_hwnd=self._monitor_hwnd,
                status=self.status.emit,
            )
            self.finished_ok.emit(self._n)
        except Exception as exc:  # noqa: BLE001 (surface to UI)
            log.exception("launch failed")
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MegaCode")
        self.setFixedSize(520, 640)
        self._worker: Optional[LaunchWorker] = None

        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)

        layout.addWidget(self._header())
        layout.addWidget(self._count_row())
        layout.addWidget(self._preview(), 1)
        layout.addLayout(self._folder_row())
        layout.addLayout(self._options_row())
        layout.addStretch(1)
        layout.addWidget(self._launch_button())
        layout.addWidget(self._status_label())

        self.setStyleSheet(QSS)

    # -- sections ------------------------------------------------------------
    def _header(self) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        title = QLabel("MegaCode", objectName="title")
        subtitle = QLabel(
            "Launch multiple Claude Code sessions and tile them on your screen.",
            objectName="subtitle",
            wordWrap=True,
        )
        v.addWidget(title)
        v.addWidget(subtitle)
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
            btn.clicked.connect(lambda _checked=False, value=n: self._on_count_changed(value))
            self._count_group.addButton(btn, n)
            row.addWidget(btn)
        row.itemAt(0).widget().setChecked(True)
        self._count = SUPPORTED[0]
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
        row.setSpacing(8)
        gap_label = QLabel("GAP (px)", objectName="section")
        self._gap_spin = QSpinBox()
        self._gap_spin.setRange(0, 64)
        self._gap_spin.setValue(6)
        self._gap_spin.setFixedWidth(74)
        row.addWidget(gap_label)
        row.addWidget(self._gap_spin)
        row.addStretch(1)
        return row

    def _launch_button(self) -> QWidget:
        self._launch_btn = QPushButton("Launch & tile", objectName="launch")
        self._launch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._launch_btn.clicked.connect(self._on_launch)
        return self._launch_btn

    def _status_label(self) -> QWidget:
        self._status = QLabel("Ready.", objectName="status", wordWrap=True)
        return self._status

    # -- behaviour -----------------------------------------------------------
    def _on_count_changed(self, n: int) -> None:
        self._count = n
        self._preview_widget.set_count(n)

    def _browse(self) -> None:
        start = self._path_edit.text() or os.getcwd()
        chosen = QFileDialog.getExistingDirectory(self, "Select working folder", start)
        if chosen:
            self._path_edit.setText(chosen)

    def _set_busy(self, busy: bool) -> None:
        self._launch_btn.setEnabled(not busy)
        self._launch_btn.setText("Working…" if busy else "Launch & tile")

    def _on_launch(self) -> None:
        working_dir = self._path_edit.text().strip()
        if not working_dir or not os.path.isdir(working_dir):
            QMessageBox.warning(self, "MegaCode", "Please choose an existing working folder.")
            return
        self._set_busy(True)
        self._status.setText("Starting…")
        self._worker = LaunchWorker(
            n=self._count,
            working_dir=working_dir,
            gap=self._gap_spin.value(),
            monitor_hwnd=int(self.winId()),
        )
        self._worker.status.connect(self._status.setText)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_done(self, n: int) -> None:
        self._set_busy(False)
        self._status.setText(f"Launched {n} instance(s). They are tiled on your screen.")
        self._worker = None

    def _on_failed(self, message: str) -> None:
        self._set_busy(False)
        self._status.setText("Failed.")
        QMessageBox.critical(self, "MegaCode", message)
        self._worker = None


def _setup_logging() -> None:
    """Configure logging that survives a windowed (no-console) frozen build.

    A UTF-8 file handler in the temp folder always captures diagnostics; a
    console handler is only added when a real stderr exists.
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        log_path = os.path.join(tempfile.gettempdir(), "megacode.log")
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except Exception:  # noqa: BLE001 (logging must never break the app)
        pass
    if sys.stderr:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(fmt)
        root.addHandler(stream_handler)


def _excepthook(exc_type, exc_value, tb) -> None:
    """Show uncaught main-thread exceptions instead of dying silently."""
    logging.getLogger("megacode").critical(
        "Uncaught exception", exc_info=(exc_type, exc_value, tb)
    )
    if QApplication.instance():
        text = "".join(traceback.format_exception(exc_type, exc_value, tb))
        QMessageBox.critical(None, "MegaCode - error", text)


def run() -> int:
    """Application entry point."""
    _setup_logging()
    sys.excepthook = _excepthook

    app = QApplication(sys.argv)
    app.setApplicationName("MegaCode")
    app.setApplicationDisplayName("MegaCode")

    # Keep the palette consistent with the dark QSS.
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    app.setPalette(palette)

    window = MainWindow()
    window.show()
    return app.exec()
