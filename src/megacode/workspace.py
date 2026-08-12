"""The workspace view: a single window hosting a draggable grid of terminals.

Every :class:`TerminalTile` wraps an embedded :class:`TerminalWidget` running a
live Claude Code session. Because the session lives *inside* the widget, dragging
a tile's header onto another tile swaps the two running sessions' on-screen
positions non-destructively -- nothing restarts.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal, Slot
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .layouts import auto_shape, grid_positions
from .terminal_widget import TerminalWidget

log = logging.getLogger("megacode")

_TILE_MIME = "application/x-megacode-tile"


class TileHeader(QFrame):
    """The draggable title bar of a terminal tile."""

    def __init__(self, tile: "TerminalTile") -> None:
        super().__init__()
        self._tile = tile
        self._press_pos: Optional[QPoint] = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setObjectName("tileHeader")

        self.grip = QLabel("⠿")
        self.grip.setObjectName("tileGrip")
        self.title = QLabel()
        self.title.setObjectName("tileTitle")
        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("tileClose")
        self.close_btn.setCursor(Qt.CursorShape.ArrowCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 6, 4)
        layout.setSpacing(8)
        layout.addWidget(self.grip)
        layout.addWidget(self.title, 1)
        layout.addWidget(self.close_btn)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        if self._press_pos is not None:
            delta = event.position().toPoint() - self._press_pos
            if delta.manhattanLength() > 6:
                self._press_pos = None
                self._start_drag()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def _start_drag(self) -> None:
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(_TILE_MIME, bytes(str(self._tile.index), "utf-8"))
        mime.setText(str(self._tile.index))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)
        self.setCursor(Qt.CursorShape.OpenHandCursor)


class TerminalTile(QFrame):
    """One tile = a draggable, closeable embedded terminal."""

    swapRequested = Signal(int, int)  # (target_index, source_index)
    closeRequested = Signal(int)      # (index)

    def __init__(self, index: int, command: str, cwd: str, font_size: int = 10) -> None:
        super().__init__()
        self.setObjectName("tile")
        self._index = index
        self._command = command
        self._cwd = cwd
        self._drop_target = False

        self.header = TileHeader(self)
        self.header.close_btn.clicked.connect(lambda: self.closeRequested.emit(self._index))
        self.terminal = TerminalWidget(command, cwd, font_size=font_size)
        self.terminal.finished.connect(self._on_terminal_finished)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(self.terminal, 1)

        self.setAcceptDrops(True)
        self.set_index(index)

    @property
    def index(self) -> int:
        return self._index

    def set_index(self, index: int) -> None:
        self._index = index
        self.header.title.setText(f"claude #{index + 1}")

    def _on_terminal_finished(self) -> None:
        self.header.title.setText(f"claude #{self._index + 1}  (exited)")

    # --- drag & drop (drop target) ------------------------------------------
    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        if event.mimeData().hasFormat(_TILE_MIME):
            self._set_drop_highlight(True)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        self._set_drop_highlight(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        self._set_drop_highlight(False)
        mime = event.mimeData()
        if mime.hasFormat(_TILE_MIME):
            try:
                source = int(bytes(mime.data(_TILE_MIME)).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                source = -1
            if source >= 0:
                self.swapRequested.emit(self._index, source)
            event.acceptProposedAction()

    def _set_drop_highlight(self, on: bool) -> None:
        if on == self._drop_target:
            return
        self._drop_target = on
        self.setProperty("drop", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class WorkspaceView(QWidget):
    """A grid of terminal tiles inside one window, with a control toolbar."""

    all_closed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("workspace")
        self._command: Optional[str] = None
        self._cwd: Optional[str] = None
        self._font_size = 10
        self.tiles: List[TerminalTile] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        root.addWidget(self._build_toolbar())

        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(8)
        root.addWidget(self._grid_host, 1)

        # exit detection (no per-tick repaint: cursor is static, output repaints)
        from PySide6.QtCore import QTimer

        self._ticker = QTimer(self)
        self._ticker.setInterval(400)
        self._ticker.timeout.connect(self._tick)
        self._ticker.start()

    # --- toolbar ------------------------------------------------------------
    def _build_toolbar(self) -> QWidget:
        bar = QFrame(objectName="toolbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(8)
        self._count_label = QLabel("MegaCode")
        self._count_label.setObjectName("toolbarTitle")
        layout.addWidget(self._count_label)
        layout.addStretch(1)

        self._add_btn = QPushButton("＋  Add terminal", objectName="toolbarBtn")
        self._add_btn.clicked.connect(self.add_tile)
        layout.addWidget(self._add_btn)

        self._min_btn = QPushButton("—  Minimize", objectName="toolbarBtn")
        self._min_btn.clicked.connect(self._minimize_window)
        layout.addWidget(self._min_btn)
        return bar

    def _minimize_window(self) -> None:
        window = self.window()
        if window is not None:
            window.showMinimized()

    # --- lifecycle ----------------------------------------------------------
    def start(self, n: int, command: str, cwd: str, font_size: int = 10) -> None:
        self.cleanup()
        self._command = command
        self._cwd = cwd
        self._font_size = font_size
        for _ in range(n):
            self._new_tile()
        self._focus_first()

    def _new_tile(self) -> TerminalTile:
        assert self._command and self._cwd is not None
        tile = TerminalTile(len(self.tiles), self._command, self._cwd, self._font_size)
        tile.swapRequested.connect(self._on_swap)
        tile.closeRequested.connect(self._on_close_tile)
        self.tiles.append(tile)
        self._rebuild()
        return tile

    @Slot()
    def add_tile(self) -> None:
        if not self._command or self._cwd is None:
            return
        tile = self._new_tile()
        tile.terminal.setFocus()

    def _rebuild(self) -> None:
        # Clear existing stretches first: otherwise a column/row used by a
        # previous (larger) layout keeps a non-zero stretch and reserves empty
        # space (e.g. 4 terminals showing a phantom 3rd column from the earlier
        # 3-terminal layout -> "grid of 6").
        for r in range(self._grid.rowCount()):
            self._grid.setRowStretch(r, 0)
        for c in range(self._grid.columnCount()):
            self._grid.setColumnStretch(c, 0)

        # remove widgets from the grid without destroying them
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                self._grid.removeWidget(w)
        n = len(self.tiles)
        positions = grid_positions(n)
        cols, rows = auto_shape(n)
        for i, tile in enumerate(self.tiles):
            tile.set_index(i)
            r, c = positions[i]
            self._grid.addWidget(tile, r, c)
        for r in range(rows):
            self._grid.setRowStretch(r, 1)
        for c in range(cols):
            self._grid.setColumnStretch(c, 1)
        self._count_label.setText(f"MegaCode · {n} terminal{'s' if n != 1 else ''}")

    def _on_swap(self, target: int, source: int) -> None:
        if source == target or not (0 <= source < len(self.tiles)) or not (0 <= target < len(self.tiles)):
            return
        log.info("swap tile %d <-> %d", source, target)
        self.tiles[source], self.tiles[target] = self.tiles[target], self.tiles[source]
        self._rebuild()
        self.tiles[target].terminal.setFocus()

    def _on_close_tile(self, index: int) -> None:
        if not (0 <= index < len(self.tiles)):
            return
        tile = self.tiles[index]
        tile.terminal.close()
        self._grid.removeWidget(tile)
        tile.setParent(None)
        tile.deleteLater()
        self.tiles.pop(index)
        if not self.tiles:
            self.all_closed.emit()
        else:
            self._rebuild()
            self._focus_first()

    def _focus_first(self) -> None:
        if self.tiles:
            self.tiles[0].terminal.setFocus()

    def _tick(self) -> None:
        for tile in self.tiles:
            tile.terminal.tick()

    def cleanup(self) -> None:
        for tile in self.tiles:
            tile.terminal.close()
            tile.setParent(None)
            tile.deleteLater()
        self.tiles.clear()
