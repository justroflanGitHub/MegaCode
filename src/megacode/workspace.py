"""The workspace view: a single window hosting a grid of resizable terminals.

Tiles sit in a splitter tree, so the gaps between them can be dragged to widen
one pane against its neighbours. Every :class:`TerminalTile` wraps an embedded
:class:`TerminalWidget` running a live Claude Code session. Because the session
lives *inside* the widget, dragging a tile's header onto another tile swaps the
two running sessions' on-screen positions non-destructively -- nothing restarts.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal, Slot
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import shells
from .chat_widget import ChatWidget
from .layouts import auto_shape, grid_positions
from .terminal_widget import TerminalWidget

log = logging.getLogger("megacode")

_TILE_MIME = "application/x-megacode-tile"

#: Thickness of the draggable gap between tiles. It doubles as the visual
#: spacing between panes (the old QGridLayout's spacing).
_SPLIT_HANDLE = 8
#: Floor for a tile's size so splitter drags can't shrink a pane to nothing.
_TILE_MIN_W, _TILE_MIN_H = 180, 120


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
        if (self._press_pos is not None
                and (event.buttons() & Qt.MouseButton.LeftButton)):
            delta = event.position().toPoint() - self._press_pos
            if delta.manhattanLength() > 6:
                self._press_pos = None
                self._start_drag()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        # A double-click (two presses with no drag) renames the tile; a real
        # drag starts on press+move, so the two never conflict.
        if event.button() == Qt.MouseButton.LeftButton:
            # Drop the press state before opening the modal dialog: the dialog
            # swallows the double-click's release, which would otherwise leave a
            # stale _press_pos that a later non-left drag could misread.
            self._press_pos = None
            self._tile.begin_rename()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

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
    """One tile = a draggable, closeable embedded terminal or chat.

    ``kind="chat"`` hosts a :class:`ChatWidget` instead of a terminal; the
    widget duck-types the few members the tile touches (``finished``, ``is_dead``,
    ``close``), so everything else -- renaming, drag-swap, closing -- is shared.
    """

    swapRequested = Signal(int, int)  # (target_index, source_index)
    closeRequested = Signal(int)      # (index)

    def __init__(
        self,
        index: int,
        command: Optional[str],
        cwd: str,
        font_size: int = 10,
        label: str = "term",
        kind: str = "terminal",
    ) -> None:
        super().__init__()
        self.setObjectName("tile")
        self.setMinimumSize(_TILE_MIN_W, _TILE_MIN_H)
        self._index = index
        self._command = command
        self._cwd = cwd
        self._label = label
        self._custom_title: Optional[str] = None
        self._drop_target = False

        self.header = TileHeader(self)
        self.header.setToolTip("Double-click to rename · drag onto another tile to swap")
        self.header.close_btn.clicked.connect(lambda: self.closeRequested.emit(self._index))
        if kind == "chat":
            self.terminal = ChatWidget(cwd, font_size=font_size)  # type: ignore[assignment]
        else:
            self.terminal = TerminalWidget(command or "", cwd, font_size=font_size)
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
        self.refresh_title()

    def refresh_title(self) -> None:
        """Rebuild the header label from the custom title (if any) + state."""
        base = self._custom_title if self._custom_title else f"{self._label} #{self._index + 1}"
        if self.terminal.is_dead():
            self.header.title.setText(f"{base}  (exited)")
        else:
            self.header.title.setText(base)

    def begin_rename(self) -> None:
        """Open a small dialog to rename this tile's window title."""
        current = self._custom_title or f"{self._label} #{self._index + 1}"
        text, ok = QInputDialog.getText(
            self, "Rename terminal", "Window title:", text=current,
        )
        if not ok:
            return
        cleaned = text.strip()
        # An empty name resets to the default "{label} #{n}".
        self._custom_title = cleaned or None
        self.refresh_title()

    def _on_terminal_finished(self) -> None:
        self.refresh_title()

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
        self._label = "term"
        self.tiles: List[TerminalTile] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        root.addWidget(self._build_toolbar())

        # Tiles live in a splitter tree (vertical root of horizontal rows) so
        # the gaps between them are draggable: panes can be widened/shrunk
        # against their neighbours, tmux-style. Children are never collapsible
        # and each tile has a minimum size, so a drag can't erase a pane.
        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(_SPLIT_HANDLE)
        root.addWidget(self._splitter, 1)

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

        self._add_btn = QToolButton(objectName="toolbarBtn")
        self._add_btn.setText("＋  Add")
        self._add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        add_menu = QMenu(self._add_btn)
        for menu_label, kind in shells.RUN_KINDS:
            if kind == "custom":
                continue  # custom command is only available from the launcher
            action = add_menu.addAction(menu_label)
            action.triggered.connect(lambda _=False, k=kind: self.add_kind(k))
        self._add_btn.setMenu(add_menu)
        self._add_btn.clicked.connect(lambda: self.add_kind("claude"))
        layout.addWidget(self._add_btn)

        self._min_btn = QPushButton("—  Minimize", objectName="toolbarBtn")
        self._min_btn.clicked.connect(self._minimize_window)
        layout.addWidget(self._min_btn)

        self._fs_btn = QPushButton("⛶  Fullscreen", objectName="toolbarBtn")
        self._fs_btn.clicked.connect(self._toggle_fullscreen)
        layout.addWidget(self._fs_btn)
        return bar

    def _minimize_window(self) -> None:
        window = self.window()
        if window is not None:
            window.showMinimized()

    def _toggle_fullscreen(self) -> None:
        window = self.window()
        if window is None:
            return
        if window.isFullScreen():
            window.showNormal()
        else:
            window.showFullScreen()

    # --- lifecycle ----------------------------------------------------------
    def start(
        self,
        n: int,
        command: str,
        cwd: str,
        font_size: int = 10,
        label: str = "term",
        kind: str = "terminal",
    ) -> None:
        self.cleanup()
        self._command = command
        self._cwd = cwd
        self._font_size = font_size
        self._label = label
        # a run kind from the launcher (claude/powershell/...) maps onto the
        # two tile kinds: a real terminal, or the AI chat panel
        tile_kind = "chat" if kind == "chat" else "terminal"
        for _ in range(n):
            self._new_tile(command, label, tile_kind)
        self._focus_first()

    def _new_tile(self, command: str, label: str, kind: str = "terminal") -> TerminalTile:
        assert self._cwd is not None
        tile = TerminalTile(
            len(self.tiles), command, self._cwd, self._font_size, label, kind=kind
        )
        tile.swapRequested.connect(self._on_swap)
        tile.closeRequested.connect(self._on_close_tile)
        self.tiles.append(tile)
        self._rebuild()
        return tile

    @Slot()
    def add_kind(self, kind: str) -> None:
        """Add a tile of the given run kind (claude/powershell/cmd/chat)."""
        if self._cwd is None:
            return
        if kind == "chat":
            # the chat talks to the claude CLI itself; no command to spawn
            tile = self._new_tile(None, shells.label_for("chat"), "chat")
        else:
            command = shells.resolve(kind)
            if not command:
                return
            tile = self._new_tile(command, shells.label_for(kind))
        tile.terminal.setFocus()

    def _rebuild(self) -> None:
        # Pane sizes are the user's arrangement (they drag the splitter
        # handles), so remember each tile's footprint and restore it after the
        # structural change instead of snapping every pane back to an equal
        # split. Visible-and-laid-out is the discriminator: a fresh tile
        # reports Qt's 640x480 default, which would poison the restore (and
        # during start() the page is hidden, so nothing is captured -> the
        # launch itself stays an equal split).
        saved = {
            id(t): (t.width(), t.height())
            for t in self.tiles if t.isVisible() and t.width() > 1 and t.height() > 1
        }

        # Snapshot the current row structure and heights BEFORE detaching: a
        # rebuild that keeps the same shape (a drag-swap) can then restore the
        # splitter's own exact sizes instead of reconstructing heights from
        # per-tile values (which loses the arrangement the moment a tile
        # crosses rows).
        old_row_heights = self._splitter.sizes()
        old_row_counts: List[int] = []
        for i in range(self._splitter.count()):
            child = self._splitter.widget(i)
            old_row_counts.append(child.count() if isinstance(child, QSplitter) else 1)

        # Detach the tiles, then drop the previous layout's row splitters.
        for tile in self.tiles:
            tile.setParent(None)
        while self._splitter.count():
            child = self._splitter.widget(0)
            child.setParent(None)
            child.deleteLater()

        n = len(self.tiles)
        positions = grid_positions(n)
        _cols, rows = auto_shape(n)
        groups: List[List[int]] = [[] for _ in range(rows)]
        for i, tile in enumerate(self.tiles):
            tile.set_index(i)
            r, _c = positions[i]
            groups[r].append(i)

        row_widgets: List[QWidget] = []
        for group in groups:
            if len(group) == 1:
                row_widget: QWidget = self.tiles[group[0]]
            else:
                # a row of 2+ tiles gets its own horizontal splitter; a lone
                # tile is added directly (no useless nested splitter/handle)
                row = QSplitter(Qt.Orientation.Horizontal)
                row.setChildrenCollapsible(False)
                row.setHandleWidth(_SPLIT_HANDLE)
                for i in group:
                    row.addWidget(self.tiles[i])
                row_widget = row
            self._splitter.addWidget(row_widget)
            row_widgets.append(row_widget)

        if old_row_counts == [len(g) for g in groups] and any(old_row_heights):
            # Same shape = a pure reorder (drag-swap): keep the rows' exact
            # heights; only the widths travel with the sessions.
            self._apply_sizes(self._splitter, old_row_heights)
            for group, row_widget in zip(groups, row_widgets):
                if isinstance(row_widget, QSplitter):
                    widths = [saved.get(id(self.tiles[i]), (0, 0))[0] for i in group]
                    self._apply_sizes(row_widget, widths)
        else:
            self._restore_sizes(groups, row_widgets, saved)
        self._count_label.setText(f"MegaCode · {n} pane{'s' if n != 1 else ''}")

    def _apply_sizes(self, splitter: QSplitter, sizes: List[int]) -> None:
        """Set pane sizes; 0 entries (unknown) take the siblings' average."""
        known = [s for s in sizes if s > 0]
        if not known:
            splitter.setSizes([1] * len(sizes))
        else:
            avg = sum(known) // len(known)
            splitter.setSizes([s if s > 0 else avg for s in sizes])
        for i in range(splitter.count()):
            splitter.setStretchFactor(i, 1)

    def _restore_sizes(
        self, groups: List[List[int]], row_widgets: List[QWidget], saved: dict
    ) -> None:
        """Re-apply remembered tile sizes after a shape change (equal if unknown)."""
        # One height per grid row (every tile in a row shares its height).
        heights = [
            max(saved.get(id(self.tiles[i]), (0, 0))[1] for i in group)
            for group in groups
        ]
        self._apply_sizes(self._splitter, heights)
        # Widths inside each multi-tile row.
        for group, row_widget in zip(groups, row_widgets):
            if isinstance(row_widget, QSplitter):
                widths = [saved.get(id(self.tiles[i]), (0, 0))[0] for i in group]
                self._apply_sizes(row_widget, widths)

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
        # drop any leftover row splitters so the next start() begins clean
        while self._splitter.count():
            child = self._splitter.widget(0)
            child.setParent(None)
            child.deleteLater()
