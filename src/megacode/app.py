"""MegaCode application: launcher + single-window terminal workspace.

Two screens live in one window:
  * the **launcher** (pick 2/3/4/6, working folder, launch), and
  * the **workspace** (one window hosting a draggable grid of embedded Claude
    Code terminals -- minimize/restore all at once, drag to swap).
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import traceback
from typing import Optional

from PySide6.QtCore import Qt, QSettings, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPalette, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
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

from . import plat_helpers
from . import shells
from . import sync_security
from . import themes
from .layouts import SUPPORTED, compute_layout
from .sync_bus import SyncBus
from .workspace import WorkspaceView

if sys.platform == "win32":
    from . import terminal as separate_terminal

    _SEPARATE_AVAILABLE = True
else:
    from . import terminal_posix as separate_terminal

    _SEPARATE_AVAILABLE = separate_terminal.available()

log = logging.getLogger("megacode")

# Палитра и весь QSS приложения теперь живут в themes.py (смена тем из
# тулбара рабочего пространства); «claude-dark» повторяет прежние константы.


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
            painter.setBrush(QColor(themes.active()["cell"]))
            painter.drawRoundedRect(cell.x, cell.y, cell.w, cell.h, 8, 8)


class LauncherPage(QWidget):
    """Choose instance count, folder and launch mode."""

    launch_workspace = Signal(int, str, int, str, str, str)  # (n, cwd, font, command, label, kind)
    launch_separate = Signal(int, str, int)                  # (n, cwd, gap)

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
        layout.addLayout(self._run_row())
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

    def _run_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(6)
        col.addWidget(QLabel("RUN", objectName="section"))
        line = QHBoxLayout()
        self._run_combo = QComboBox()
        for menu_label, kind in shells.RUN_KINDS:
            self._run_combo.addItem(menu_label, kind)
        self._run_combo.currentIndexChanged.connect(self._on_run_changed)
        line.addWidget(self._run_combo, 1)
        col.addLayout(line)
        self._custom_edit = QLineEdit()
        self._custom_edit.setPlaceholderText(
            "command, e.g. pwsh -NoLogo" if sys.platform == "win32"
            else "command, e.g. htop")
        self._custom_edit.setVisible(False)
        col.addWidget(self._custom_edit)
        row.addLayout(col, 1)
        # default to the platform's plain shell (set after _custom_edit
        # exists, so the change handler doesn't run against a half-built
        # widget)
        for i in range(self._run_combo.count()):
            if self._run_combo.itemData(i) == shells.DEFAULT_KIND:
                self._run_combo.setCurrentIndex(i)
                break
        return row

    def _on_run_changed(self, _index: int) -> None:
        kind = self._run_combo.currentData()
        self._custom_edit.setVisible(kind == "custom")

    def _options_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(QLabel("FONT", objectName="section"))
        self._font_spin = QSpinBox()
        self._font_spin.setRange(7, 22)
        self._font_spin.setValue(12)
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
        # the legacy mode needs a spawnable external terminal; hide it when
        # the platform has none (Linux without xterm -- see terminal_posix)
        btn.setVisible(_SEPARATE_AVAILABLE)
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
        if not cwd:
            return
        kind = self._run_combo.currentData()
        custom = self._custom_edit.text() if kind == "custom" else None
        command = shells.resolve(kind, custom)
        if not command:
            QMessageBox.warning(
                self, "MegaCode",
                f"Couldn't resolve a command to run for '{self._run_combo.currentText()}'.",
            )
            return
        label = shells.label_for(kind)
        if kind == "custom" and custom:
            label = os.path.basename(custom.split()[0]) or "custom"
        self.launch_workspace.emit(
            self._count, cwd, self._font_spin.value(), command, label, kind
        )

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
        self._workspace.linkedInfoChanged.connect(self._on_linked_info)
        self._workspace.linkPrefRequested.connect(self._on_link_pref)
        self._workspace.themeChanged.connect(self.apply_theme)
        self._stack.addWidget(self._workspace)  # index 1

        # Cross-window linking: one bus per process, owned here and injected
        # into the workspace. MainWindow is the ONLY place the persistent
        # preference gates the auto-start -- the toolbar chip must always be
        # able to re-link for the session, so SyncBus itself does not check
        # the settings file.
        self._bus = SyncBus(sync_security.state_dir())
        prefs = sync_security.settings_load(sync_security.state_dir())
        self._workspace.set_link_pref(bool(prefs.get("link_windows")))
        if self._bus_may_start(prefs):
            self._workspace.set_link(self._bus)

        self.setStyleSheet(themes.build_qss(themes.active()))
        QShortcut(QKeySequence("F11"), self, activated=self._toggle_fullscreen)
        self._enter_launcher()

    # --- color scheme ----------------------------------------------------
    def apply_theme(self, name: str) -> None:
        """Switch the whole window to another scheme and persist the choice.

        The startup theme is applied before MainWindow exists (run() calls
        themes.set_active with the stored name), so this only serves the
        toolbar menu's live switches.
        """
        themes.set_active(name)
        settings = QSettings()
        settings.setValue("theme", name)
        scheme = themes.active()
        self.setStyleSheet(themes.build_qss(scheme))
        app = QApplication.instance()
        if app is not None:
            # the QPalette roles matter for the few unstyled surfaces
            # (e.g. tooltips and menus around the QSS-painted chrome)
            palette = app.palette()
            palette.setColor(QPalette.ColorRole.Window, QColor(scheme["bg"]))
            palette.setColor(
                QPalette.ColorRole.WindowText, QColor(scheme["text"]))
            app.setPalette(palette)
        self._workspace.apply_theme()

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # --- navigation ---------------------------------------------------------
    def _enter_launcher(self) -> None:
        if self.isMaximized() or self.isFullScreen():
            self.showNormal()
        self._stack.setCurrentWidget(self._launcher)
        self.setFixedSize(520, 660)
        self._center()

    def _enter_workspace(self) -> None:
        # Undo the launcher's fixed size so the window can be resized /
        # maximized / fullscreened. The width floor follows the workspace's
        # real minimum (the full toolbar row -- broadcast box, Run all, Run
        # pasted, Sync input, Add, Minimize, Fullscreen -- measures ~1300px,
        # and narrower would crush the buttons into slivers): the launcher's
        # 360 would let the window squeeze them into slivers. Maximum is
        # raised first -- the launcher's setFixedSize left it at 520x660, and
        # a minimum above that would clamp to it.
        self.setMaximumSize(16777215, 16777215)
        self.setMinimumSize(
            max(360, self._workspace.minimumSizeHint().width()), 260
        )
        self._stack.setCurrentWidget(self._workspace)
        if not (self.isMaximized() or self.isFullScreen()):
            self.showMaximized()

    def _center(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        fg = self.frameGeometry()
        fg.moveCenter(geo.center())
        self.move(fg.topLeft())

    # --- actions ------------------------------------------------------------
    def _on_launch_workspace(
        self, n: int, cwd: str, font_size: int, command: str, label: str, kind: str
    ) -> None:
        if not command:
            QMessageBox.critical(self, "MegaCode", "No command to run.")
            return
        try:
            self._workspace.start(n, command, cwd, font_size=font_size, label=label, kind=kind)
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

    # --- cross-window linking --------------------------------------------------
    def _bus_may_start(self, prefs: dict) -> bool:
        """Auto-start gates, in order. Hard gates (env / elevation / state
        dir) live in SyncBus.start(); this is only the persisted preference
        and the elevation notice -- both user-facing launch decisions."""
        import os as _os
        if _os.environ.get("MEGACODE_NO_LINK"):
            return False
        if prefs.get("link_windows") is False:
            return False
        if plat_helpers.is_process_elevated() and not prefs.get(
                "link_windows_forced"):
            self._launcher.set_status("Window linking off (elevated).")
            return False
        return True

    def _on_link_pref(self, on: bool) -> None:
        """Persist the 'Link windows' preference and apply it now."""
        directory = sync_security.state_dir()
        prefs = sync_security.settings_load(directory)
        prefs["link_windows"] = bool(on)
        try:
            sync_security.settings_save(directory, prefs)
        except OSError:
            log.exception("could not persist link settings")
        if on:
            self._workspace.set_link(self._bus)  # idempotent connect+start
        else:
            self._bus.stop()

    def _on_linked_info(self, n: int) -> None:
        self.setWindowTitle("MegaCode" if n < 2 else f"MegaCode — linked: {n} windows")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        # order matters: the workspace's cleanup publishes the empty digest
        # while the link is still alive, THEN the bus says its goodbyes
        self._workspace.cleanup()
        self._bus.stop(reason="shutdown")
        super().closeEvent(event)


def _setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        if sys.platform == "win32":
            log_dir = tempfile.gettempdir()
        else:
            # NOT /tmp: it is world-readable, and the log carries window titles
            log_dir = os.path.join(
                os.environ.get("XDG_CACHE_HOME",
                               os.path.join(os.path.expanduser("~"), ".cache")),
                "megacode")
            os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "megacode.log")
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
        if sys.platform != "win32":
            try:
                os.chmod(log_path, 0o600)
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass
    if sys.stderr:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)


def _excepthook(exc_type, exc_value, tb) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        # ^C while a Qt slot runs: the SIGINT handler owns shutdown now.
        # A modal "error" box here was why the app refused to die on ^C.
        logging.getLogger("megacode").warning("KeyboardInterrupt in a slot")
        return
    logging.getLogger("megacode").critical(
        "Uncaught exception", exc_info=(exc_type, exc_value, tb)
    )
    if QApplication.instance():
        text = "".join(traceback.format_exception(exc_type, exc_value, tb))
        QMessageBox.critical(None, "MegaCode - error", text)


def _install_quit_signals(app: QApplication) -> None:
    """Make ^C (and SIGTERM) shut the app down instead of dying inside it.

    Without a handler, Ctrl+C raises KeyboardInterrupt in whatever Qt slot
    happens to run next (timers tick constantly); PySide6 logs it via
    sys.excepthook and the event loop just continues -- the process becomes
    unkillable from the terminal it was started in. First signal quits
    gracefully (closeEvent still kills the PTY children); a second one
    exits hard, the way a user pressed ^C twice expects.
    """
    import signal

    state = {"requested": False}

    def _handler(_signum, _frame) -> None:
        if state["requested"]:
            os._exit(130)
        state["requested"] = True
        # from the handler it is unsafe to touch Qt directly; a queued
        # 0-timeout shot lands in the running event loop instead
        QTimer.singleShot(0, app.quit)

    handled = [signal.SIGINT]
    if sys.platform != "win32":
        handled.append(signal.SIGTERM)  # `timeout`, systemd, kill default
    for sig in handled:
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass  # not the main thread / unsupported on this platform


def _load_theme_setting() -> str:
    """The persisted scheme name, or the default for a first run."""
    stored = QSettings().value("theme", "", type=str)
    return stored if stored in themes.SCHEMES else themes.DEFAULT_SCHEME


def run() -> int:
    _setup_logging()
    sys.excepthook = _excepthook

    app = QApplication(sys.argv)
    app.setApplicationName("MegaCode")
    app.setOrganizationName("MegaCode")
    app.setApplicationDisplayName("MegaCode")
    _install_quit_signals(app)
    themes.set_active(_load_theme_setting())
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(themes.active()["bg"]))
    palette.setColor(
        QPalette.ColorRole.WindowText, QColor(themes.active()["text"]))
    app.setPalette(palette)

    window = MainWindow()
    window.show()
    return app.exec()
