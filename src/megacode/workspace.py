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

from PySide6.QtCore import QMimeData, QPoint, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QActionGroup, QColor, QCursor, QDrag, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import shells
from . import sync_protocol as proto
from . import tags
from . import themes
from .chat_widget import ChatWidget
from .layouts import auto_shape, grid_positions
from .sync_bus import (
    ST_ELEVATED,
    ST_FULL,
    ST_NETWORK_PROFILE,
    ST_NO_LISTEN,
    ST_NO_STATE_DIR,
    ST_PROTO_MISMATCH,
    NullLink,
)
from .terminal_widget import TerminalWidget

log = logging.getLogger("megacode")

_TILE_MIME = "application/x-megacode-tile"

#: Thickness of the draggable gap between tiles. It doubles as the visual
#: spacing between panes (the old QGridLayout's spacing).
_SPLIT_HANDLE = 8
#: Floor for a tile's size so splitter drags can't shrink a pane to nothing.
_TILE_MIN_W, _TILE_MIN_H = 180, 120
#: How long the "ran in N panes" feedback stays in the toolbar title.
_STATUS_FLASH_MS = 2000

#: The toolbar's hint strings, verbatim. _refresh_hint_strings() extends
#: them while any tag exists and restores these exact bytes when none do --
#: the zero-tag UI must stay identical to the pre-tags app. (Typed here, in
#: one place, so tests can pin them down instead of chasing literals.)
_SYNC_TIP_BASE = (
    "Mirror keyboard input from the focused pane to every other pane\n"
    "(typing, arrows, nano/vim editing — everything, at once).\n"
    "Keyboard only: mouse clicks/wheel stay per-pane, since every pane\n"
    "has its own geometry. Click again to stop mirroring."
)
_SYNC_TIP_TAGS = (
    "\nWith tags: a left-click mirrors to EVERY pane in every window,\n"
    "across tag groups; right-click the button to mirror into one tag\n"
    "group instead. Lit headers receive your keys."
)
_BROADCAST_PLACEHOLDER_BASE = "Command for every pane…"
_BROADCAST_PLACEHOLDER_TAGS = "Command for every pane · or @tag command…"
_BROADCAST_TIP_BASE = (
    "Run this command in every open terminal pane\n"
    "(Enter or the Run all button; chat tiles are skipped)"
)
_BROADCAST_TIP_TAGS = "\nA '@tag' prefix scopes the run to one group."
#: Link-era hint addenda -- appended ONLY while tags exist AND other windows
#: are linked (both no-peer/no-tag combinations emit today's exact bytes).
_SYNC_TIP_LINK = (
    "\nLinked windows (⛓ N): while Sync input is on, keys also reach"
    "\npanes in every linked MegaCode window -- all of them after a"
    "\nleft-click, or that tag's panes when a group is picked."
)
_BROADCAST_TIP_LINK = (
    "\nA '@tag' run also runs in that tag's panes in linked windows;"
    "\nan unscoped run stays in this window."
)
_LINK_CHIP_TIP = (
    "This MegaCode is linked with other MegaCode window(s) over a local\n"
    "socket (same user, same session). Sync input, tag groups and '@tag'\n"
    "Run all now span every linked window; their headers pulse as keys\n"
    "arrive. Uncheck to isolate this window."
)
_FIRST_LINK_FLASH = (
    "linked with another MegaCode window — tags and sync now cross windows"
)
#: How long a remote-key delivery pulses a receiving pane's header.
_REMOTE_PULSE_MS = 600


class _ChipCloseButton(QToolButton):
    """The tag chip's (x).

    Swallows the second press of a double-click: the chips rebuild on the
    first removal, and with similar-width tags the NEIGHBOR's (x) slides
    under a stationary cursor -- without this, one double-click would
    remove two tags, the second never aimed at. Accepted, not ignored, so
    the press neither arms the button nor reaches the header's rename
    handler. A deliberate later click arrives as a fresh press and works."""

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        event.accept()


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
        # Sync-group tag chips. Hidden while empty so an untagged header is
        # pixel-identical to a pre-tags one (a visible empty container would
        # still claim layout spacing), and width-capped so a long tag list
        # can never raise the tile's -- and the window's -- minimum size.
        self.chips = QWidget(objectName="tileChips")
        self._chips_layout = QHBoxLayout(self.chips)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(4)
        self.chips.setMaximumWidth(150)
        self.chips.setVisible(False)
        layout.addWidget(self.chips)
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

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        # Defensive, same rule as the double-click above: a pending LEFT press
        # followed by this right-click must not leave a stale _press_pos that a
        # later move could misread as a drag start (mousePressEvent seeds only
        # on the left button, but the press may still be pending).
        self._press_pos = None
        self._tile.menuRequested.emit(self._tile.index)
        event.accept()

    # --- sync-group tag chips -------------------------------------------------
    #: How many tags get a real chip before the rest collapse into "+N".
    MAX_CHIPS = 2

    def set_chips(self, tag_list: List[str]) -> None:
        """Rebuild the chips: the first tags verbatim, the rest as "+N".

        Two chips + a counter is the whole display budget -- the header is a
        ~24px strip that already carries a title, and more would crowd it.
        Every real chip carries an explicit (x): a tag leaves THIS pane only
        by a deliberate click on it (or the equivalent menu uncheck) --
        removal is never a side effect of another interaction.
        """
        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        used: set = set()
        for tag in tag_list[: self.MAX_CHIPS]:
            self._add_chip(tag, tag, tags.tag_class(tag), used,
                           removable=True)
        rest = tag_list[self.MAX_CHIPS:]
        if rest:
            self._add_chip(f"+{len(rest)}", ", ".join(rest), None, used,
                           removable=False)
        self.chips.setVisible(bool(tag_list))

    def _add_chip(self, display: str, tip: str, cls: Optional[int],
                  used: set, removable: bool) -> None:
        """One chip: the tag name, plus an (x) that removes it from this
        pane. Colors de-collide per header: if two visible chips hash to the
        same class, the later one takes the next free class, so neighbors
        are always distinguishable (tag_class itself stays pure -- cross-pane
        color identity must not shift).

        A QFrame, not a QWidget: only style-aware widgets paint QSS
        backgrounds/borders, and the container now carries the chip's color
        (the pre-(x) chip was a QLabel, which paints; a plain QWidget would
        silently render colorless)."""
        chip = QFrame(objectName="tileTag")
        chip.setToolTip(tip)
        if cls is None:  # the "+N" counter: first free class
            cls = 0
        while cls in used:
            cls = (cls + 1) % len(themes.tag_colors())
        used.add(cls)
        chip.setProperty("tagClass", str(cls))
        row = QHBoxLayout(chip)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        label = QLabel(objectName="tileTagName")
        # A QLabel's size hint is its text width, so elide to a fixed budget:
        # the chip then contributes a bounded, tag-count-independent minimum.
        # The 10px font is set in code AND in QSS -- kept in lockstep so the
        # elide measurement can never diverge from what actually renders
        # (eliding with the default font would cut ~20% too soon here).
        font = label.font()
        font.setPixelSize(10)
        label.setFont(font)
        label.setText(label.fontMetrics().elidedText(
            display, Qt.TextElideMode.ElideRight, 36))
        row.addWidget(label)
        if removable:
            x = _ChipCloseButton(objectName="tileTagX")
            x.setText("×")
            x.setToolTip(f"Remove '{display}' from this pane")
            # the elide budget must cover the whole chip, so the (x) stays
            # tiny and fixed: it is an affordance, not text
            x.setFont(font)
            x.setFixedWidth(14)
            tag = display  # removable chips render the tag verbatim
            x.clicked.connect(
                lambda _=False, t=tag: self._tile.toggle_tag(t))
            row.addWidget(x)
        self._chips_layout.addWidget(chip)

    def chip_texts(self) -> List[str]:
        """The chip labels' texts (test helper)."""
        out: List[str] = []
        for i in range(self._chips_layout.count()):
            chip = self._chips_layout.itemAt(i).widget()
            label = chip.findChild(QLabel, "tileTagName")
            assert label is not None
            out.append(label.text())
        return out

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
    menuRequested = Signal(int)       # (index) -- right-click on the header
    #: (added, removed) -- the first tag that appeared / disappeared in a
    #: real change; "" for the absent side. Both directions carry the same
    #: information need: "how big is the group now?"
    tagsChanged = Signal(str, str)

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
        self._tags: List[str] = []

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

    # --- sync-group tags -------------------------------------------------------
    def tags(self) -> List[str]:
        return list(self._tags)

    def set_tags(self, tag_list: List[str]) -> None:
        """Normalize, dedupe preserving order, store; emit only on real change.

        Tags live on the tile (not the widget, not a registry), so they
        travel with the live session through drag-swaps and die with the
        pane -- no second source of identity to keep in sync. Capped at the
        wire grammar's per-pane budget so the in-window fan-out and the
        cross-window digest can never disagree about a pane's groups.
        """
        cleaned: List[str] = []
        for raw in tag_list:
            t = tags.normalize_tag(raw)
            if t and t not in cleaned:
                cleaned.append(t)
        cleaned = cleaned[:proto.MAX_TAGS_PER_PANE]
        if cleaned == self._tags:
            return
        added = next((t for t in cleaned if t not in self._tags), "")
        removed = next((t for t in self._tags if t not in cleaned), "")
        self._tags = cleaned
        self.header.set_chips(self._tags)
        tip = "Double-click to rename · drag onto another tile to swap"
        if self._tags:  # zero tags -> byte-identical tooltip to today
            tip = f"Tags: {', '.join(self._tags)} · right-click to edit · {tip}"
        self.header.setToolTip(tip)
        self.tagsChanged.emit(added, removed)

    def toggle_tag(self, tag: str) -> None:
        t = tags.normalize_tag(tag)
        if t is None:
            return
        if t in self._tags:
            self.set_tags([x for x in self._tags if x != t])
        else:
            self.set_tags(self._tags + [t])

    def set_sync_state(self, state: str) -> None:
        """Show 'source' | 'peer' | '' for the sync-domain tint.

        On the HEADER, not the tile frame: the border channel already
        belongs to the drop="true" swap highlight, and repolishing one
        small widget is cheaper. Change-guarded: focus moves are frequent.
        """
        if self.header.property("sync") == state:
            return
        self.header.setProperty("sync", state)
        for w in (self.header, self.header.title):
            w.style().unpolish(w)
            w.style().polish(w)

    def set_remote_pulse(self, on: bool) -> None:
        """A cool-blue header pulse marking remote key delivery.

        The warm source/peer tint physically cannot reach other windows,
        so the remote direction answers "keys are arriving" instead. Yields
        to the authoritative tint: while this pane is lit as a sync source/
        peer, a pulse would overwrite the answer to "where do MY keys go" --
        the more important question.
        """
        if on and self.header.property("sync"):
            return
        self.header.setProperty("remotePulse", "true" if on else "")
        for w in (self.header, self.header.title):
            w.style().unpolish(w)
            w.style().polish(w)
        if on:
            def _pulse_off() -> None:
                try:
                    self.set_remote_pulse(False)
                except RuntimeError:
                    # the tile closed inside the pulse window; nothing to unlit
                    pass
            QTimer.singleShot(_REMOTE_PULSE_MS, _pulse_off)

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
    #: total linked windows including self (1 = solo); emitted on change
    linkedInfoChanged = Signal(int)
    #: the user toggled the persistent "Link windows" preference (Add menu);
    #: MainWindow persists it and starts/stops the bus -- the workspace
    #: itself never learns about settings files
    linkPrefRequested = Signal(bool)
    #: the user picked a color scheme (Theme menu); MainWindow persists it,
    #: re-styles the window and calls back into apply_theme()
    themeChanged = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("workspace")
        self._command: Optional[str] = None
        self._cwd: Optional[str] = None
        self._font_size = 10
        self._label = "term"
        self.tiles: List[TerminalTile] = []
        # sync-input mode: every keystroke from the focused pane is mirrored
        # into all the others (tmux synchronize-panes). Toggled per workspace.
        # With tags in play the left-click arm still fans out to EVERY pane
        # (tags are the right-click menu's business, not the click's); with
        # zero tags that is byte-identical to the pre-tags behavior. With
        # linked windows the ONE global state replicates across every
        # MegaCode instance.
        self._sync_keys = False
        # The right-click menu's choice for an armed sync: ONE tag whose
        # group receives the mirrored keys, or None for every pane in every
        # window (the left-click arm). Travels the wire with the toggle
        # ("tag" on the sync frame) so every window arms the same audience.
        self._sync_scope: Optional[str] = None
        # programmatic checked flips (the _apply_sync choke point): the
        # toggled hook must not re-enter and double-apply
        self._sync_btn_guard = False
        # the cross-window link. NullLink until MainWindow injects the real
        # SyncBus -- the default IS the unlinked-app contract (no-ops, empty
        # registry, no signals), so a lone window behaves byte-identically.
        self._link: NullLink = NullLink()
        self._pane_seq = 0            # per-process pane ids ("p1", "p2", ...)
        self._link_peers = 0          # other linked windows (last count)
        self._link_state = ""         # "", "linking", "off" (session/full)
        self._link_seen_peer = False  # first-link flash fires once per era
        self._link_btn_guard = False  # programmatic chip (un)checks
        self._link_pref_guard = False

        # Repaint the sync-domain tint on every focus move: "which panes will
        # receive my keystrokes?" must be answered without typing first.
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)

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
        self._ticker = QTimer(self)
        self._ticker.setInterval(400)
        self._ticker.timeout.connect(self._tick)
        self._ticker.start()

        # transient "ran in N panes" feedback in the toolbar title
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.setInterval(_STATUS_FLASH_MS)
        self._status_timer.timeout.connect(self._restore_status)

    # --- toolbar ------------------------------------------------------------
    def _build_toolbar(self) -> QWidget:
        bar = QFrame(objectName="toolbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(8)
        self._count_label = QLabel("MegaCode")
        self._count_label.setObjectName("toolbarTitle")
        # An explicit small floor lets the TITLE absorb a narrow-window squeeze
        # (clipping text) instead of the buttons: without it the label's text
        # width (~220px) sets the row's layout minimum and every button gets
        # crushed to an unreadable sliver below ~900px of window width.
        self._count_label.setMinimumWidth(48)
        layout.addWidget(self._count_label)
        layout.addStretch(1)

        # Broadcast bar: one command, executed in every open terminal pane.
        self._broadcast_input = QLineEdit(objectName="broadcastInput")
        self._broadcast_input.setPlaceholderText(_BROADCAST_PLACEHOLDER_BASE)
        self._broadcast_input.setClearButtonEnabled(True)
        self._broadcast_input.setToolTip(_BROADCAST_TIP_BASE)
        # Tile drags carry text/plain (the swap mime includes setText), so a
        # missed drop would otherwise type a tile index into the command box.
        self._broadcast_input.setAcceptDrops(False)
        self._broadcast_input.returnPressed.connect(self._broadcast)
        self._broadcast_input.textChanged.connect(self._update_broadcast_button)
        # Stretch (capped): the box takes the spare width up to a comfortable
        # size instead of sitting pinned at its sizeHint -- and then collapsing
        # to ~2 characters on a snapped/half-screen window.
        self._broadcast_input.setMaximumWidth(460)
        layout.addWidget(self._broadcast_input, 1)

        self._broadcast_btn = QToolButton(objectName="toolbarBtn")
        self._broadcast_btn.setText("▶  Run all")
        self._broadcast_btn.setToolTip(
            "Run the command in every open terminal pane\n"
            "(panes holding pasted input are left to the Run pasted button)"
        )
        self._broadcast_btn.clicked.connect(self._broadcast)
        layout.addWidget(self._broadcast_btn)
        self._update_broadcast_button()

        # Run pasted: press Enter in every pane where pasted input is still
        # waiting, so per-pane commands pasted beforehand all run at once.
        self._exec_btn = QToolButton(objectName="toolbarBtn")
        self._exec_btn.setText("↵  Run pasted")
        self._exec_btn.setToolTip(
            "Press Enter in every pane where pasted input is waiting\n"
            "(paste different commands into the panes, then run them all at once;\n"
            "panes without waiting input are not touched; a Claude Code pane\n"
            "submits its input box; Esc / Ctrl+C / closing disarms a pane)"
        )
        self._exec_btn.setEnabled(False)
        self._exec_btn.clicked.connect(self._execute_pasted)
        layout.addWidget(self._exec_btn)

        # Sync input: mirror the keyboard from the focused pane into every
        # other pane (typing, arrows, nano/vim editing...), tmux-style.
        # Left-click arms EVERY pane in every window; right-click opens the
        # tag menu to narrow the mirror to one group.
        self._sync_btn = QToolButton(objectName="toolbarBtn")
        self._sync_btn.setText("⇉  Sync input")
        self._sync_btn.setCheckable(True)
        self._sync_btn.setToolTip(_SYNC_TIP_BASE)
        self._sync_btn.toggled.connect(self._on_sync_toggled)
        self._sync_btn.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self._sync_btn.customContextMenuRequested.connect(
            self._on_sync_menu)
        layout.addWidget(self._sync_btn)

        # Cross-window linking chip. Hidden while unlinked: a lone window
        # must not grow new UI. Visible states: "N windows" (linked),
        # "linking…" (election/reconnect gap), "off" (session kill / full).
        self._link_btn = QToolButton(objectName="toolbarBtn")
        self._link_btn.setText("⛓  2 windows")
        self._link_btn.setCheckable(True)
        self._link_btn.setToolTip(_LINK_CHIP_TIP)
        self._link_btn.setVisible(False)
        self._link_btn.toggled.connect(self._on_link_toggled)
        layout.addWidget(self._link_btn)

        self._add_btn = QToolButton(objectName="toolbarBtn")
        self._add_btn.setText("＋  Add")
        self._add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        add_menu = QMenu(self._add_btn)
        for menu_label, kind in shells.RUN_KINDS:
            if kind == "custom":
                continue  # custom command is only available from the launcher
            action = add_menu.addAction(menu_label)
            action.triggered.connect(lambda _=False, k=kind: self.add_kind(k))
        add_menu.addSeparator()
        # The persistent preference (this survives restarts); the chip above
        # is the session-level switch. Ownership boundary: the workspace
        # only emits, MainWindow persists and drives the bus.
        self._link_pref_action = add_menu.addAction("Link windows")
        self._link_pref_action.setCheckable(True)
        self._link_pref_action.setChecked(True)
        self._link_pref_action.toggled.connect(self._on_link_pref)
        self._add_btn.setMenu(add_menu)
        # The button's plain click adds the platform's default shell kind
        # (cmd on Windows, bash on Astra) -- the launcher's preselected kind,
        # so both entry points open the same thing. Claude stays one menu
        # entry away; the user asked for a shell by default.
        self._add_btn.clicked.connect(
            lambda: self.add_kind(shells.DEFAULT_KIND))
        layout.addWidget(self._add_btn)

        # Color scheme: one click cycles nothing -- it opens the menu, so a
        # misclick never repaints the whole window under the user.
        self._theme_btn = QToolButton(objectName="toolbarBtn")
        self._theme_btn.setText("◐  Theme")
        self._theme_btn.setToolTip("Color scheme for the window and terminals")
        self._theme_btn.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        theme_menu = QMenu(self._theme_btn)
        group = QActionGroup(self._theme_btn)
        group.setExclusive(True)
        self._theme_actions = {}
        for name, scheme in themes.SCHEMES.items():
            action = group.addAction(scheme["label"])
            action.setCheckable(True)
            action.setChecked(name == themes.active_name())
            action.triggered.connect(
                lambda _=False, n=name: self.themeChanged.emit(n))
            theme_menu.addAction(action)
            self._theme_actions[name] = action
        self._theme_btn.setMenu(theme_menu)
        layout.addWidget(self._theme_btn)

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

    # --- broadcast (run a command in every pane) -----------------------------
    @Slot()
    def _broadcast(self) -> None:
        """Run the toolbar command in the targeted live terminal panes.

        The default target is every live console pane: chat tiles are not
        consoles and exited panes have no prompt left, so both are skipped.
        A "@tag command" prefix scopes the run to one tag group instead --
        but only when that tag actually exists (an unknown "@tag" stays a
        literal command, so cmd's "@echo off" keeps working). The title
        flashes how many panes actually got it.
        """
        command = self._broadcast_input.text().strip()
        if not command:
            return
        prev = QApplication.focusWidget()
        sel = tags.parse_selector(command)
        # Appending onto a pane's waiting pasted line would run
        # "<pasted><command>" as ONE line (verified: cmd executes
        # "echo AAAecho BBB"). Those panes are the Run pasted button's job;
        # the flash reports the narrower delivery.
        live = [
            t for t in self._terminal_panes()
            if not t.is_dead() and not t.has_pending_input()
        ]
        if sel is not None and sel[0] in self._known_tags():
            tag, command = sel
            panes = [t for t in live if tag in self._tags_of(t)]
            # registry-predicted remote audience (feedback only; the run
            # itself is receiver-side filtered, so a stale roster can never
            # mis-deliver -- the counts can at worst overshoot momentarily)
            reg = self._link.registry() if self._link.is_alive() else None
            remote = reg.alive_panes_with_tag(tag) if reg else 0
            rwin = reg.windows_with_tag(tag) if reg else 0
            if not panes and not remote:
                # an explicit selector that matches nothing live (e.g. only
                # a chat tile holds the tag) must run nothing anywhere
                self._flash_status(f"no live pane tagged '@{tag}'")
                self._hand_back_focus(prev)
                return
            for term in panes:
                term.run_command(command)
            if remote and not self._link.publish_run(tag, command):
                self._flash_status("command too long to share across windows")
                self._hand_back_focus(prev)
                return
            if remote:
                self._flash_status(
                    f"ran in {len(panes)} pane{'s' if len(panes) != 1 else ''}"
                    f" here · {remote} in {rwin}"
                    f" window{'s' if rwin != 1 else ''} · @{tag}"
                )
            else:
                self._flash_status(
                    f"ran in {len(panes)} pane{'s' if len(panes) != 1 else ''}"
                    f" · @{tag}"
                )
            self._hand_back_focus(prev)
            return
        if sel is not None and self._any_tags():
            # "@nope cmd" with tags around: ran everywhere, but the user
            # probably aimed at a group -- make the miss visible.
            panes = live
            note = f" (no '@{sel[0]}' group)"
        else:
            panes = live  # today's path, byte-identical
            note = ""
        for term in panes:
            term.run_command(command)
        self._flash_status(
            f"ran in {len(panes)} pane{'s' if len(panes) != 1 else ''}{note}"
        )
        self._hand_back_focus(prev)

    def _hand_back_focus(self, prev) -> None:
        """Return the keyboard to the panes after a toolbar action.

        Enter-to-run must not strand the keyboard in the box: a follow-up
        keystroke meant for a pane would silently mutate the retained command
        and re-run it in EVERY pane. Hand focus back to the pane the user
        came from (the click path never left it); nowhere to go back -> the
        first tile, matching every other action in this class.
        """
        if (prev is not None and prev is not self._broadcast_input
                and isinstance(prev, TerminalWidget) and not prev.is_dead()):
            prev.setFocus()
        else:
            self._focus_first()

    def _update_broadcast_button(self) -> None:
        # nothing to run: keep the button visibly disabled rather than dead
        self._broadcast_btn.setEnabled(bool(self._broadcast_input.text().strip()))

    # --- run pasted (Enter in every pane that is holding input) ---------------
    def _terminal_panes(self) -> List[TerminalWidget]:
        """Every currently-open console pane (chat tiles are not consoles)."""
        return [
            tile.terminal for tile in self.tiles
            if isinstance(tile.terminal, TerminalWidget)
        ]

    @Slot()
    def _execute_pasted(self) -> None:
        """Press Enter in every pane where pasted input is still waiting.

        Each pane runs its own pasted command; panes without waiting input are
        untouched -- no stray blank lines at empty prompts, no stray Enter
        inside a TUI. The title flashes how many panes actually ran.
        Deliberately blind to tag groups: pasted input was placed per pane BY
        HAND, so a group filter here could only subtract Enters the user's
        own hands arranged.
        """
        prev = QApplication.focusWidget()
        waiting = [
            t for t in self._terminal_panes()
            if not t.is_dead() and t.has_pending_input()
        ]
        for term in waiting:
            term.execute_pending()
        self._flash_status(
            f"ran in {len(waiting)} pane{'s' if len(waiting) != 1 else ''}"
        )
        self._update_exec_button()
        # Same rule as the broadcast bar: the keyboard stays with the panes.
        if prev is not None and isinstance(prev, TerminalWidget) and not prev.is_dead():
            prev.setFocus()
        else:
            self._focus_first()

    def _update_exec_button(self) -> None:
        """Arm "run pasted" only while some live pane is holding input."""
        waiting = [
            t for t in self._terminal_panes()
            if not t.is_dead() and t.has_pending_input()
        ]
        self._exec_btn.setEnabled(bool(waiting))
        # A dimmed button alone reads as "disabled" -- the armed state must be
        # findable: the waiting count goes into the label and the accent border
        # lights up (QSS [armed="true"], repolished like the drop highlight).
        self._exec_btn.setText(
            "↵  Run pasted" + (f" ({len(waiting)})" if waiting else "")
        )
        self._exec_btn.setProperty("armed", "true" if waiting else "false")
        self._exec_btn.style().unpolish(self._exec_btn)
        self._exec_btn.style().polish(self._exec_btn)

    # --- sync input (mirror the keyboard across panes / windows) --------------
    @Slot(bool)
    def _on_sync_toggled(self, checked: bool) -> None:
        if self._sync_btn_guard:
            return  # a programmatic flip: _apply_sync already applied state
        # Left-click: the broad brush. Every pane in every window receives,
        # tag groups be damned; narrowing to one group is the right-click
        # menu's job. Clicking again disarms.
        self._arm_sync(checked, None)

    def _set_sync_checked_quietly(self, checked: bool) -> None:
        """Flip the button without running the toggled hook."""
        self._sync_btn_guard = True
        try:
            self._sync_btn.setChecked(checked)
        finally:
            self._sync_btn_guard = False

    def _arm_sync(self, on: bool, scope: Optional[str],
                  tile: Optional[TerminalTile] = None) -> None:
        """The user-side arm / disarm / re-scope: apply, flash, publish.

        ``scope=None`` means every pane in every window (the left-click
        arm); a tag narrows the mirror to that one group. The menu passes
        its captured ``tile`` so the flash names the pane the user chose to
        drive from, whatever holds keyboard focus at pick time (the menu's
        popup can steal it).
        """
        if on:
            if tile is None:
                tile = self._focused_term_tile()
            if scope is not None:
                flash = self._sync_toggle_text(tile, scope)
            elif tile is not None and self._any_tags():
                flash = self._sync_toggle_text(tile)
            else:
                flash = "sync input on"
        else:
            flash = "sync input off"
        self._apply_sync(on, scope, flash)
        self._link.publish_sync(on, scope or "")

    def _apply_sync(self, on: bool, scope: Optional[str],
                    flash: Optional[str]) -> None:
        """The ONE sync-state choke point: keys, scope, button, label, tint.

        Remote adoption passes flash=None (state only; the relay path flashes
        "set in Wn" itself). The checked flip is guarded so the toggled hook
        cannot re-enter -- that hook exists for direct setChecked callers
        (tests, the relaunch reset), which run the plain arm path themselves.
        """
        self._sync_keys = on
        self._sync_scope = scope if on else None
        self._set_sync_checked_quietly(on)
        self._refresh_sync_btn_label()
        self._update_sync_tint()
        if flash is not None:
            self._flash_status(flash)

    def _refresh_sync_btn_label(self) -> None:
        """Show the armed audience on the button: with a scope picked, the
        button itself answers "which group gets my keys" -- and an unscoped
        arm on a tagged workspace says "all", because that is a choice too
        (the zero-tag workspace keeps the plain label: "every pane" is the
        only meaning there, exactly as before tags existed).

        The tag elides to a fixed pixel budget (the chips' trick): the
        button's minimum -- and so the toolbar's -- must not grow with the
        tag length, or a long tag squeezes the row at the window's pinned
        minimum width. The budget fits ~13 chars: most tags read verbatim,
        only the 14-16 char tail elides."""
        if self._sync_keys and self._sync_scope:
            tag = self._sync_btn.fontMetrics().elidedText(
                self._sync_scope, Qt.TextElideMode.ElideRight, 96)
            self._sync_btn.setText(f"⇉  Sync · @{tag}")
        elif self._sync_keys and self._any_tags():
            self._sync_btn.setText("⇉  Sync · all")
        else:
            self._sync_btn.setText("⇉  Sync input")

    @Slot(QPoint)
    def _on_sync_menu(self, pos: QPoint) -> None:
        """Right-click on the Sync input button: pick the mirror's audience.

        The focused tile is captured BEFORE the exec: the menu's popup can
        steal keyboard focus, and the pick's flash should still name the
        pane the user was (probably) driving from.
        """
        tile = self._focused_term_tile()
        menu = self._build_sync_menu(tile)
        menu.exec(self._sync_btn.mapToGlobal(pos))
        menu.deleteLater()

    def _build_sync_menu(self, tile: Optional[TerminalTile]) -> QMenu:
        """"Which panes receive the mirrored keys?"

        The left-click audience (every pane in every window) plus one entry
        per known tag -- local AND remote, first-seen order. Built fresh per
        open (groups come and go; construct-only builders are also
        offscreen-testable).
        """
        menu = QMenu(self)
        if self._sync_keys:
            off = menu.addAction("Turn mirroring off")
            off.triggered.connect(lambda: self._arm_sync(False, None))
            menu.addSeparator()
        menu.addSection("Mirror keys to")
        allact = menu.addAction("All windows")
        allact.setCheckable(True)
        allact.setChecked(self._sync_keys and self._sync_scope is None)
        # checkable so the active audience reads as active; an uncheck (the
        # user re-clicking it) is a no-look dismissal, not an arm. The tile
        # rides along for the same reason the tag actions pass theirs: the
        # popup holds keyboard focus while the handler runs.
        allact.toggled.connect(
            lambda on, tl=tile: self._arm_sync(True, None, tl) if on else None)
        for tag in self._known_tags():
            act = menu.addAction(self._tag_icon(tag), tag)
            act.setCheckable(True)
            act.setChecked(self._sync_keys and self._sync_scope == tag)
            # same checkable contract as "All windows" above
            act.toggled.connect(
                lambda on, t=tag, tl=tile:
                    self._pick_sync_scope(t, tl) if on else None)
        if not self._known_tags():
            hint = menu.addAction(
                "No tags yet — right-click a pane header to group panes")
            hint.setEnabled(False)
        return menu

    def _pick_sync_scope(self, tag: str,
                         tile: Optional[TerminalTile] = None) -> None:
        """Switch the armed mirror to ONE group (arming if needed) -- a
        single click, no off/on blip, and the wire re-scopes with it."""
        if self._sync_keys and self._sync_scope == tag:
            return  # already driving that group
        self._arm_sync(True, tag, tile)

    def _sync_toggle_text(self, tile: Optional[TerminalTile],
                          scope: Optional[str] = None) -> str:
        """The arming flash, split-audience once other windows are linked.

        With tags in play the audience is a choice, so say it up front
        instead of letting the user find out by typing: a scoped arm names
        the one chosen group; the unscoped (left-click) arm names "all" --
        every pane in every window, tags ignored. The zero-tag workspace
        never gets here at all (the caller keeps its plain "sync input on").
        """
        if scope is not None:
            group = f"@{scope}"
            here = len(self._scope_receivers(
                tile.terminal if tile is not None else None, scope))
            reg = self._link.registry() if self._link.is_alive() else None
            if reg is not None:
                remote = reg.alive_panes_with_tag(scope)
                rwin = reg.windows_with_tag(scope)
                if remote or rwin:
                    return (f"sync input on · {group} ({here} here · {remote} in"
                            f" {rwin} window{'s' if rwin != 1 else ''})")
            return (f"sync input on · {group} "
                    f"({here} pane{'s' if here != 1 else ''})")
        here = len(self._scope_receivers(
            tile.terminal if tile is not None else None, None))
        reg = self._link.registry() if self._link.is_alive() else None
        if reg is not None:
            remote = reg.alive_pane_count()
            if remote:
                rwin = reg.other_windows()
                return (f"sync input on · all windows ({here} here · {remote}"
                        f" in {rwin} window{'s' if rwin != 1 else ''})")
        return (f"sync input on · all "
                f"({here} pane{'s' if here != 1 else ''})")

    def _mirror_input(self, source: TerminalWidget, seq: str, pasted: bool) -> None:
        """Fan one pane's user input out to the armed audience.

        Connected per pane in ``_new_tile``. The left-click arm mirrors to
        every other live pane, tags ignored; a scoped arm (the right-click
        menu) narrows the group to the chosen tag AND requires the source
        to be a member of it: mirroring is a property OF the group, so
        typing on a pane outside it stays local. The source never receives
        its own echo, and ``inject_input`` does not re-emit ``inputSent``,
        so there is no feedback loop. Only keystrokes/pastes travel:
        PTY-sized panes render TUIs at their own geometry, so mouse clicks
        (coordinates!) must not.

        With linked windows the same input also crosses the pipe: the
        all-windows arm carries the "all" flag so receivers bypass the
        domain rule entirely, and a scoped arm carries the scope tag as a
        one-element snapshot. The registry gate is a traffic optimization
        only: staleness briefly skips publishing and the next digest heals
        it.
        """
        if not self._sync_keys:
            return
        scope = self._sync_scope
        src_tags = self._tags_of(source)
        if scope is not None and scope not in src_tags:
            return
        for term in self._scope_receivers(source, scope):
            term.inject_input(seq, pasted=pasted)
        if not self._link.is_alive():
            return
        if scope is not None:
            wire_tags, wire_all = [scope], False
            remote = self._link.registry().alive_panes_with_tag(scope)
        else:
            wire_tags, wire_all = src_tags, True
            remote = self._link.registry().alive_pane_count()
        if remote == 0:
            return
        if not self._link.publish_input(
                self._pane_id_of(source), wire_tags, seq, pasted,
                all_mode=wire_all):
            # the wire cap is on the ENCODED frame (non-ASCII and VT control
            # bytes grow several-fold under JSON escaping), so a payload
            # that passed the char-level MAX_SEQ check can still be refused
            # here -- say it instead of silently skipping the wire copy.
            # Local panes already received it; only mirroring is skipped.
            self._flash_status("paste too large to mirror across windows")

    # --- sync groups (tag-based) -----------------------------------------------
    def _tags_of(self, term) -> List[str]:
        """Tags of the tile hosting ``term``, resolved by widget identity.

        An identity scan (the grid holds a handful of panes) is always
        correct across swap/close/retag; a cache would need invalidation
        hooks for zero gain -- and a stale identity is exactly the bug the
        widget-not-index rule elsewhere in this file exists to avoid.
        """
        for tile in self.tiles:
            if isinstance(tile, TerminalTile) and tile.terminal is term:
                return tile.tags()
        return []  # the tile is already gone: crash-proofing only

    def _known_tags(self) -> List[str]:
        """Every tag in use -- here AND in linked windows, first seen over
        the CURRENT tile order (chat and dead tiles included -- their tags
        stay organizational). The menu order may reshuffle after a
        drag-swap; cosmetic, accepted. NullLink contributes nothing, so an
        unlinked workspace keeps today's exact vocabulary."""
        out: List[str] = []
        for tile in self.tiles:
            if isinstance(tile, TerminalTile):
                for t in tile.tags():
                    if t not in out:
                        out.append(t)
        if self._link.is_alive():
            for t in self._link.registry().known_tags():
                if t not in out:
                    out.append(t)
        return out

    def _any_tags(self) -> bool:
        return bool(self._known_tags())

    def _scope_receivers(self, source, scope: Optional[str]) -> List[TerminalWidget]:
        """Every pane that would receive a mirrored keystroke from ``source``
        under ``scope``: the chosen tag's group when scoped, every live pane
        when not (the left-click all-windows arm -- with zero tags that is
        the same set the domain rule ever produced, so the pre-tags app is
        unchanged). Shared by the mirror fan-out, the header tint and the
        arming flash, so all three can never disagree about the real
        delivery set.
        """
        if scope is None:
            return [
                t for t in self._terminal_panes()
                if t is not source and not t.is_dead()
            ]
        return [
            t for t in self._terminal_panes()
            if t is not source and not t.is_dead()
            and scope in self._tags_of(t)
        ]

    def _focused_term_tile(self) -> Optional[TerminalTile]:
        focus = QApplication.focusWidget()
        if focus is None:
            return None
        for tile in self.tiles:
            if (isinstance(tile, TerminalTile) and tile.terminal is focus
                    and isinstance(tile.terminal, TerminalWidget)
                    and not tile.terminal.is_dead()):
                return tile
        return None

    def _update_sync_tint(self) -> None:
        """Mark WHERE a keystroke would go while sync is armed.

        The _any_tags() gate keeps the zero-tag workspace pixel-identical to
        the pre-tags app: with no tags the fan-out is simply "every pane",
        which the plain armed button already communicates. An unscoped arm
        (with tags around) lights every pane -- the audience IS everyone; a
        scoped arm lights only the chosen group, and only while the focused
        pane is a member of it (otherwise its keys stay local and the tint
        must not promise otherwise). Recomputed on focus moves, the sync
        toggle, tag changes, pane death, and every _rebuild (add/close/swap).
        """
        src = (self._focused_term_tile()
               if (self._sync_keys and self._any_tags()) else None)
        if (src is not None and self._sync_scope is not None
                and self._sync_scope not in src.tags()):
            src = None
        peers = (set(self._scope_receivers(src.terminal, self._sync_scope))
                 if src is not None else set())
        for tile in self.tiles:
            if not isinstance(tile, TerminalTile):
                continue
            if tile is src:
                tile.set_sync_state("source")
            elif tile.terminal in peers:
                tile.set_sync_state("peer")
            else:
                tile.set_sync_state("")

    def _on_focus_changed(self, _old, _new) -> None:
        if self.tiles:  # focus moves also fire during teardown; stay quiet
            self._update_sync_tint()

    def _tag_icon(self, tag: str) -> QIcon:
        pm = QPixmap(10, 10)
        # The active scheme's palette, and the class's FOREGROUND (the tag's
        # own ink): the plate/border tones wash out on a light theme's
        # near-white menus (the light borders fell under 3:1 there), while
        # the tinted ink keeps both the class hue and full contrast on dark
        # and light menus alike.
        pm.fill(QColor(themes.tag_colors()[tags.tag_class(tag)][2]))
        return QIcon(pm)  # the same color identity as the header chip

    def _build_tile_menu(self, tile: TerminalTile) -> QMenu:
        """The header's right-click menu, built fresh per open (tags come
        and go; a construct-only builder is also offscreen-testable)."""
        menu = QMenu(self)
        menu.addSection("Sync group tags")
        for tag in self._known_tags():
            act = menu.addAction(self._tag_icon(tag), tag)
            act.setCheckable(True)
            act.setChecked(tag in tile.tags())
            act.toggled.connect(lambda _on, t=tag, tl=tile: tl.toggle_tag(t))
        menu.addAction("New tag…").triggered.connect(
            lambda: self._on_new_tag(tile))
        clear = menu.addAction("Clear tags")
        clear.setEnabled(bool(tile.tags()))
        clear.triggered.connect(lambda: tile.set_tags([]))
        menu.addSeparator()
        menu.addAction("Rename pane…", tile.begin_rename)
        menu.addAction("Close pane", lambda: self._on_close_tile(tile.index))
        return menu

    def _on_tile_menu(self, index: int) -> None:
        if not (0 <= index < len(self.tiles)):
            return
        tile = self.tiles[index]
        if isinstance(tile, TerminalTile):
            menu = self._build_tile_menu(tile)
            menu.exec(QCursor.pos())
            # The menu is parented to this long-lived view, so without an
            # explicit delete every right-click would accumulate one QMenu
            # (and its tile-pinning lambdas) on it for the window's life.
            menu.deleteLater()

    def _on_new_tag(self, tile: TerminalTile) -> None:
        prompt = "Tag (letters, digits, '-' or '_'; spaces become '-'):"
        while True:
            text, ok = QInputDialog.getText(self, "New sync group tag", prompt)
            if not ok:
                return
            tag = tags.normalize_tag(text)
            if tag is not None:
                if tag not in tile.tags():
                    # naming an existing tag checks it, never unchecks it
                    tile.toggle_tag(tag)
                else:
                    # already checked: the dialog still owes the user an
                    # answer (the group size), not a silent no-op
                    self._flash_group_size(tag)
                return
            prompt = (f"'{text.strip()}' is not a usable tag (1-16 letters,"
                      " digits, '-' or '_'; 'echo'/'rem' are reserved):")

    def _flash_group_size(self, tag: str) -> None:
        n = sum(
            1 for t in self._terminal_panes()
            if not t.is_dead() and tag in self._tags_of(t)
        )
        reg = self._link.registry() if self._link.is_alive() else None
        remote = reg.alive_panes_with_tag(tag) if reg else 0
        if reg and remote:
            rwin = reg.windows_with_tag(tag)
            self._flash_status(
                f'group "{tag}": {n} pane{"s" if n != 1 else ""} here'
                f" · {remote} in {rwin} window{'s' if rwin != 1 else ''}"
            )
            return
        self._flash_status(f'group "{tag}": {n} pane{"s" if n != 1 else ""}')

    def _on_tags_changed(self, added: str, removed: str) -> None:
        """Tags came or went: refresh hints and tint, then flash a recount.

        The recount serves BOTH directions: after untagging, "did the group
        drop to one pane?" is exactly the information to surface (a count of
        zero says you dissolved it).
        """
        self._refresh_hint_strings()
        self._update_sync_tint()
        # the armed-ALL label ("Sync · all") exists only while tags do; a
        # tag appearing/vanishing flips it either way
        self._refresh_sync_btn_label()
        self._publish_digest()
        if added:
            self._flash_group_size(added)
        elif removed:
            self._flash_group_size(removed)

    def _refresh_hint_strings(self) -> None:
        """Extend the toolbar hints while tags exist; restore them otherwise.

        The discovery surface (@tag selectors, group-scoped sync, the linked
        -windows addenda) should exist only once the user actually has tags
        -- the zero-tag toolbar keeps the exact pre-tags strings, and a
        tag-less OR peer-less combination never shows link hints.
        """
        linked = self._link.is_alive() and self._link.registry().other_windows() > 0
        if self._any_tags():
            self._broadcast_input.setPlaceholderText(_BROADCAST_PLACEHOLDER_TAGS)
            self._broadcast_input.setToolTip(_BROADCAST_TIP_BASE + _BROADCAST_TIP_TAGS)
            self._sync_btn.setToolTip(
                _SYNC_TIP_BASE + _SYNC_TIP_TAGS
                + (_SYNC_TIP_LINK if linked else ""))
            if linked:
                self._broadcast_input.setToolTip(
                    _BROADCAST_TIP_BASE + _BROADCAST_TIP_TAGS + _BROADCAST_TIP_LINK)
        else:
            self._broadcast_input.setPlaceholderText(_BROADCAST_PLACEHOLDER_BASE)
            self._broadcast_input.setToolTip(_BROADCAST_TIP_BASE)
            self._sync_btn.setToolTip(_SYNC_TIP_BASE)

    # --- cross-window linking (see sync_bus.py) --------------------------------
    def set_link(self, link) -> None:
        """Inject the real SyncBus (MainWindow owns it) and bring it up.

        Until this is called the workspace holds a NullLink: no-ops, an
        empty registry, no signals -- the unlinked app is byte-identical.
        Idempotent: re-injecting the same bus just (re)starts it -- the
        persistent-preference path calls this on every toggle-on.
        """
        if self._link is link:
            link.start()
            return
        self._link = link
        link.incoming.connect(self._on_link_msg)
        link.syncAdopted.connect(self._adopt_remote_sync)
        link.rosterChanged.connect(self._on_remote_roster_changed)
        link.peersChanged.connect(self._on_peers_changed)
        link.statusChanged.connect(self._on_link_status)
        link.start()

    def set_link_pref(self, on: bool) -> None:
        """Reflect the persisted preference into the Add-menu action."""
        self._link_pref_action.blockSignals(True)
        self._link_pref_action.setChecked(on)
        self._link_pref_action.blockSignals(False)

    def _on_link_pref(self, on: bool) -> None:
        """The persistent 'Link windows' preference changed (Add menu)."""
        if self._link_pref_guard:
            return
        self.linkPrefRequested.emit(on)

    def _on_link_toggled(self, checked: bool) -> None:
        """The toolbar chip: the session-level link switch."""
        if self._link_btn_guard:
            return
        if checked:
            # say "linking…" while the election resolves (A3): start() is
            # async and a silent chip would look like a dead button
            self._link_state = "linking"
            self._link_btn.setText("⛓  linking…")
            self._link.start()
        else:
            self._link_state = "off"
            self._link.stop()
            self._flash_status("window linking off")
        self._update_link_chip()

    def _on_link_status(self, text: str) -> None:
        """Bus status transitions: user-visible flash + chip state."""
        self._flash_status(text)
        if text in ("relinking…",):
            self._link_state = "linking"
        elif text in (ST_FULL, ST_ELEVATED, ST_NETWORK_PROFILE,
                      ST_NO_STATE_DIR, ST_NO_LISTEN, ST_PROTO_MISMATCH):
            self._link_state = "off"
            # keep the chip usable: it IS the way back
            self._link_btn_guard = True
            self._link_btn.setChecked(False)
            self._link_btn_guard = False
        self._update_link_chip()

    def _on_peers_changed(self, n: int) -> None:
        was = self._link_peers
        self._link_peers = n
        if n >= 1 and self._link_state == "linking":
            self._link_state = ""  # healthy again
        elif (n == 0 and self._link_state == "linking"
                and (not self._link.enabled() or self._link.is_alive())):
            # "linking…" must not outlive the link's resolution: a lone
            # re-elected HUB is healthy (alive, zero peers -> solo), and a
            # bus turned off (the persistent preference) is simply off. A
            # still-connecting CLIENT is neither, so genuine linking keeps
            # its label.
            self._link_state = ""
        if n >= 1 and not self._link_seen_peer:
            # fire once per link era: a window joining an existing pair also
            # learns the feature exists (0->2 counts, not just 0->1)
            self._link_seen_peer = True
            self._flash_status(_FIRST_LINK_FLASH)
        elif n == 0:
            self._link_seen_peer = False
        self._update_link_chip()

    def _update_link_chip(self) -> None:
        """Chip text/visibility: hidden for a healthy lone window.

        The checked state IS the link state ("uncheck to isolate"): it is
        driven here, guarded, so programmatic flips never re-enter the
        toggle handler.
        """
        linked = (self._link_peers >= 1 and self._link_state == "") \
            or self._link_state == "linking"
        self._link_btn_guard = True
        try:
            self._link_btn.setChecked(linked)
        finally:
            self._link_btn_guard = False
        if self._link_state == "linking":
            self._link_btn.setText("⛓  linking…")
            self._link_btn.setVisible(True)
        elif self._link_state == "off":
            self._link_btn.setText("⛓  off")
            self._link_btn.setVisible(True)
        elif self._link_peers >= 1:
            self._link_btn.setText(f"⛓  {self._link_peers + 1} windows")
            self._link_btn.setVisible(True)
        else:
            self._link_btn.setVisible(False)
        total = max(1, self._link_peers + 1) if self._link_peers >= 1 else 1
        if total != getattr(self, "_linked_total", 1):
            self._linked_total = total
            self.linkedInfoChanged.emit(total)

    def _on_remote_roster_changed(self) -> None:
        self._refresh_hint_strings()
        self._update_link_chip()
        # remote-only tags count toward _any_tags(): they too flip the
        # armed-ALL button label and the hint strings' extended forms
        self._refresh_sync_btn_label()

    def _on_link_msg(self, msg) -> None:
        """A validated remote message (input / sync / run)."""
        t = msg.get("t")
        if t == "input":
            self._deliver_remote(msg["tags"], msg["seq"], msg["paste"],
                                 msg.get("all", False))
        elif t == "sync":
            self._apply_remote_sync(
                msg["on"], msg.get("tag", ""),
                self._link.registry().display_name(msg["src"]))
        elif t == "run":
            self._on_remote_run(
                msg["tag"], msg["cmd"],
                self._link.registry().display_name(msg["src"]))

    def _deliver_remote(self, source_tags: List[str], seq: str, pasted: bool,
                        all_mode: bool = False) -> None:
        """Deliver a mirrored keystroke that came over the pipe.

        The RECEIVER applies the R3 domain rule to its own panes with the
        send-time tag snapshot carried in the message, so cross-window
        semantics can never drift from in-window semantics -- and registry
        staleness can never mis-deliver. ``all_mode`` (the left-click arm)
        bypasses the domain rule: the audience is every live pane. No
        sync-state check: the toggle is ONE global state, and this message
        exists only because it is on. ``inject_input`` never re-emits
        ``inputSent``, so injected input never re-publishes: no echo loop,
        end-to-end.
        """
        for term in self._terminal_panes():
            if not term.is_dead() and (all_mode or tags.share_domain(
                    source_tags, self._tags_of(term))):
                term.inject_input(seq, pasted=pasted)
                tile = self._tile_of(term)
                if tile is not None:
                    tile.set_remote_pulse(True)

    def _on_remote_run(self, tag: str, cmd: str, label: str) -> None:
        """A scoped '@tag' run-all that came over the pipe."""
        live = [
            t for t in self._terminal_panes()
            if not t.is_dead() and not t.has_pending_input()
            and tag in self._tags_of(t)
        ]
        for term in live:
            term.run_command(cmd)
        self._flash_status(
            f"ran in {len(live)} pane{'s' if len(live) != 1 else ''}"
            f" · @{tag} (from {label})"
        )

    def _apply_remote_sync(self, on: bool, tag: str, label: str) -> None:
        """A live sync relay from another window: apply + announce."""
        scope = f" · @{tag}" if (on and tag) else ""
        self._apply_sync(on, tag or None,
                         f"sync input {'on' if on else 'off'}{scope}"
                         f" · set in {label}")

    def _adopt_remote_sync(self, on: bool, tag: str) -> None:
        """Welcome-time adoption of the group's sync state: state only.

        No flash -- the join's first-link announcement (processed before
        the roster signal) must be the last write and win.
        """
        self._apply_sync(on, tag or None, None)

    # --- cross-window digest ----------------------------------------------------
    def _pane_digest(self) -> List[dict]:
        """Our pane table for linked windows: id / tags / alive / kind.

        Tag names, aliveness and kind only -- no titles, commands, output
        or cwd ever leave the process. Chat tiles are carried (their tags
        stay organizational), so the union vocabulary is complete.
        """
        out = []
        for tile in self.tiles:
            if not isinstance(tile, TerminalTile):
                continue
            entry = proto.build_pane_entry(
                getattr(tile, "pane_id", ""),
                tile.tags(),
                not tile.terminal.is_dead(),
                "term" if isinstance(tile.terminal, TerminalWidget) else "chat",
            )
            if entry is not None:
                out.append(entry)
        # receiver-side validation rejects rosters above MAX_PANES outright
        # (which would bounce the connection); a pane past the budget simply
        # stays unannounced cross-window
        return out[:proto.MAX_PANES]

    def _publish_digest(self) -> None:
        self._link.publish_digest(self._pane_digest())

    def _pane_id_of(self, term) -> str:
        """The digest id of the tile hosting ``term`` (identity scan, the
        same WHY as _tags_of: always correct across swap/close/retag)."""
        for tile in self.tiles:
            if isinstance(tile, TerminalTile) and tile.terminal is term:
                return getattr(tile, "pane_id", "")
        return ""

    def _tile_of(self, term) -> Optional[TerminalTile]:
        for tile in self.tiles:
            if isinstance(tile, TerminalTile) and tile.terminal is term:
                return tile
        return None

    def _on_term_finished(self, term) -> None:
        """A pane's process died: unlight it as a mirror target and drop it
        from the cross-window digest (its tile stays, tags organizational)."""
        self._update_sync_tint()
        self._publish_digest()

    def _flash_status(self, text: str) -> None:
        self._count_label.setText(text)
        self._status_timer.start()

    def _restore_status(self) -> None:
        n = len(self.tiles)
        self._count_label.setText(f"MegaCode · {n} pane{'s' if n != 1 else ''}")

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
        # A new workspace is a new context: the previous session's command must
        # not stay armed one accidental click away in the fresh panes, and
        # stale sync must not type the old session's keys into them.
        self._broadcast_input.clear()
        self._sync_btn.setChecked(False)
        self._command = command
        self._cwd = cwd
        self._font_size = font_size
        self._label = label
        # a run kind from the launcher (claude/powershell/...) maps onto the
        # two tile kinds: a real terminal, or the AI chat panel
        tile_kind = "chat" if kind == "chat" else "terminal"
        for _ in range(n):
            self._new_tile(command, label, tile_kind)
        self._update_exec_button()
        # cleanup() destroyed every tagged tile, so the tag-era toolbar hints
        # and any stale tint must go back to the zero-tag state (defensive:
        # the per-tile _rebuild in the loop above already covers it).
        self._refresh_hint_strings()
        self._update_sync_tint()
        self._focus_first()

    def _new_tile(self, command: str, label: str, kind: str = "terminal") -> TerminalTile:
        assert self._cwd is not None
        tile = TerminalTile(
            len(self.tiles), command, self._cwd, self._font_size, label, kind=kind
        )
        # per-process pane id for the cross-window digest; never reused
        self._pane_seq += 1
        tile.pane_id = f"p{self._pane_seq}"
        tile.swapRequested.connect(self._on_swap)
        tile.closeRequested.connect(self._on_close_tile)
        tile.menuRequested.connect(self._on_tile_menu)
        tile.tagsChanged.connect(self._on_tags_changed)
        if isinstance(tile.terminal, TerminalWidget):
            term = tile.terminal
            # Per-pane hooks for the two broadcast features. The lambda closes
            # over the WIDGET (not the tile), so the fan-out source stays the
            # same object across drag-swaps. Chat tiles have neither signal.
            term.inputSent.connect(
                lambda seq, pasted, src=term: self._mirror_input(src, seq, pasted)
            )
            term.pendingChanged.connect(self._update_exec_button)
            # a peer dying mid-sync must stop being lit as a mirror target,
            # and must drop out of the cross-window digest
            term.finished.connect(lambda src=term: self._on_term_finished(src))
        self.tiles.append(tile)
        self._rebuild()
        self._publish_digest()
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
        # One choke point covering add/close/swap: tile identity and order
        # just changed, and closing the last tagged pane must also restore
        # the zero-tag toolbar hints AND the plain sync-button label (a
        # closing tile emits nothing, so no tagsChanged fires).
        self._update_sync_tint()
        self._refresh_hint_strings()
        self._refresh_sync_btn_label()
        self._restore_status()

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
        # the closed pane may have been the only one holding pasted input
        self._update_exec_button()
        # and its tags just left the cross-window vocabulary
        self._publish_digest()
        if not self.tiles:
            # the empty branch skips _rebuild, so the tag-era toolbar hints
            # and sync-button label are retired here too (latent only --
            # the launcher hides this toolbar synchronously -- but the
            # state should not lie)
            self._refresh_hint_strings()
            self._update_sync_tint()
            self._refresh_sync_btn_label()
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

    def apply_theme(self) -> None:
        """After themes.set_active: repaint panes with the new palette. The
        chrome is QSS-driven and restyles with the window's stylesheet; the
        terminal grids are painted by hand and hold resolved QColors."""
        for action_name, action in self._theme_actions.items():
            action.setChecked(action_name == themes.active_name())
        for tile in self.tiles:
            # chat tiles are pure QSS and restyle with the window's sheet
            if isinstance(tile.terminal, TerminalWidget):
                tile.terminal.refresh_theme()

    def cleanup(self) -> None:
        for tile in self.tiles:
            tile.terminal.close()
            tile.setParent(None)
            tile.deleteLater()
        self.tiles.clear()
        # announce the empty table while the link is still up (closeEvent
        # calls this BEFORE the bus shutdown, so peers see the departure in
        # one step); the next start() republishes fresh rows
        self._publish_digest()
        # drop any leftover row splitters so the next start() begins clean
        while self._splitter.count():
            child = self._splitter.widget(0)
            child.setParent(None)
            child.deleteLater()
