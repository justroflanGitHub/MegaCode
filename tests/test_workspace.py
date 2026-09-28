"""Offscreen tests for the workspace's resizable splitter panes.

``TerminalWidget`` is replaced by a no-op fake (no child process); the tests
then drive the splitter layout: structure, drag-to-resize effect, and that a
user's pane arrangement survives add / close / swap.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Qt, Signal  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QSplitter,
    QToolButton,
    QWidget,
)

import megacode.workspace as wsm  # noqa: E402


class _FakeTerminal(QWidget):
    """Stands in for ``TerminalWidget``: same API, no child process."""

    finished = Signal()
    inputSent = Signal(str, bool)
    pendingChanged = Signal()

    def __init__(self, _command, _cwd=None, font_size=10, parent=None):  # noqa: ARG002
        super().__init__(parent)
        self.commands = []   # broadcast bar: commands run_command received
        self.injected = []   # sync input: (data, pasted) inject_input received
        self._pending = False

    def is_dead(self) -> bool:
        return False

    # --- run-pasted button contract (mirrors the real widget) ----------------
    def has_pending_input(self) -> bool:
        return self._pending

    def set_pending(self, value: bool) -> None:
        if value != self._pending:
            self._pending = value
            self.pendingChanged.emit()

    def execute_pending(self) -> None:
        if self._pending:
            self._pending = False
            self.commands.append("\r")

    # --- sync-input contract (mirrors the real widget) ------------------------
    def inject_input(self, seq: str, pasted: bool = False) -> None:
        # a mirrored Enter/Esc runs/clears this pane's waiting line too
        if not pasted and seq in ("\r", "\x1b"):
            self.set_pending(False)
        self.injected.append((seq, pasted))

    def run_command(self, command: str) -> None:
        # mirror the real widget: it is run_command that appends the Enter (CR)
        self.commands.append(command + "\r")

    def tick(self) -> None:
        return None

    def close(self) -> None:
        return None


@pytest.fixture()
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture()
def make_ws(qapp, monkeypatch):
    monkeypatch.setattr(wsm, "TerminalWidget", _FakeTerminal)

    def _make(n: int = 2) -> wsm.WorkspaceView:
        view = wsm.WorkspaceView()
        view.resize(1200, 800)
        view.start(n, "fake", os.getcwd(), font_size=10, label="term")
        view.show()
        qapp.processEvents()
        return view

    return _make


@pytest.fixture()
def ws(make_ws):
    return make_ws(2)


def _row_splitter(view) -> QSplitter:
    return view._splitter.widget(0)  # noqa: SLF001 (test)


def test_panes_are_splitter_resizable(ws, qapp):
    root = ws._splitter
    assert isinstance(root, QSplitter)
    assert root.orientation() == Qt.Orientation.Vertical
    assert not root.childrenCollapsible()
    assert root.handleWidth() == 8

    row = _row_splitter(ws)
    assert isinstance(row, QSplitter)
    assert row.orientation() == Qt.Orientation.Horizontal
    assert row.count() == 2
    assert not row.childrenCollapsible()
    # the gap between the two tiles is a real, draggable handle
    assert row.handle(1) is not None

    # a "drag" = the sizes changing: the small pane must end up visibly smaller
    # than an equal split, not just nudged
    row.setSizes([300, 900])
    qapp.processEvents()
    assert ws.tiles[0].width() < ws.tiles[1].width()
    assert ws.tiles[0].width() < 400  # equal halves would be ~590

    # a 4th/5th terminal grows the tree downwards: 2x2 -> vertical handle too
    for _ in range(2):
        ws._new_tile("fake", "term")
    qapp.processEvents()
    assert ws._splitter.count() == 2
    assert ws._splitter.handle(1) is not None


def test_pane_sizes_survive_add_and_close(ws, qapp):
    _row_splitter(ws).setSizes([300, 900])
    qapp.processEvents()

    # adding a third terminal keeps the two existing panes' proportions
    ws._new_tile("fake", "term")  # n=3 -> one row of three
    qapp.processEvents()
    row3 = _row_splitter(ws)
    assert row3.count() == 3
    widths = [t.width() for t in ws.tiles]
    assert widths[0] < widths[2] < widths[1]  # small, averaged newcomer, large
    # the newcomer takes the siblings' average (not a Qt 640px default), and
    # the user's small pane is not squeezed toward the 180px minimum
    assert abs(widths[2] - (widths[0] + widths[1]) / 2) < 12
    assert widths[0] > 185

    # closing the small pane keeps the others' arrangement
    ws._on_close_tile(0)
    qapp.processEvents()
    widths = [t.width() for t in ws.tiles]
    assert widths[0] > widths[1]  # the large pane is still the large pane


def test_swap_carries_sizes_with_sessions(ws, qapp):
    _row_splitter(ws).setSizes([300, 900])
    qapp.processEvents()
    assert ws.tiles[0].width() < ws.tiles[1].width()

    ws._on_swap(0, 1)
    qapp.processEvents()
    # the session that was small now sits on the right and is still small
    assert ws.tiles[0].width() > ws.tiles[1].width()


def test_cross_row_swap_preserves_row_heights(make_ws, qapp):
    """A drag-swap between rows must not collapse the user's vertical
arrangement to equal row heights."""
    ws = make_ws(4)  # 2x2
    qapp.processEvents()
    ws._splitter.setSizes([552, 184])  # user drags the horizontal divider
    qapp.processEvents()
    heights_before = ws._splitter.sizes()
    assert abs(heights_before[0] - heights_before[1]) > 100  # visibly uneven

    ws._on_swap(2, 0)  # top-left session <-> bottom-left session
    qapp.processEvents()
    assert ws._splitter.sizes() == heights_before


def test_from_scratch_launch_splits_equally(make_ws, qapp):
    """start() runs while the workspace page is still hidden (the launcher is
showing); the launch grid must come up as equal panes, not sizes skewed by
Qt's 640x480 default widget geometry."""
    ws = make_ws(3)
    widths = [t.width() for t in ws.tiles]
    assert max(widths) - min(widths) <= 5

    ws6 = make_ws(6)
    row_heights = ws6._splitter.sizes()
    assert max(row_heights) - min(row_heights) <= 5


def test_single_tile_is_direct_child(make_ws):
    ws = make_ws(1)
    assert ws._splitter.count() == 1
    assert ws._splitter.widget(0) is ws.tiles[0]
    assert not isinstance(ws._splitter.widget(0), QSplitter)


def test_five_tiles_rows_of_three_then_two(make_ws, qapp):
    ws = make_ws(5)  # auto_shape(5) = 3 columns x 2 rows
    qapp.processEvents()
    assert ws._splitter.count() == 2
    first, second = ws._splitter.widget(0), ws._splitter.widget(1)
    assert isinstance(first, QSplitter) and first.count() == 3
    assert isinstance(second, QSplitter) and second.count() == 2


def test_tiles_have_minimum_size(ws):
    for tile in ws.tiles:
        assert tile.minimumWidth() >= 180
        assert tile.minimumHeight() >= 120


def test_cleanup_empties_splitter(make_ws, qapp):
    ws = make_ws(4)
    qapp.processEvents()
    ws.cleanup()
    qapp.processEvents()
    assert ws._splitter.count() == 0


# --- broadcast bar (run a command in every pane) -------------------------------


def test_broadcast_runs_command_in_every_pane(make_ws):
    ws = make_ws(3)
    ws._broadcast_input.setText("git status")
    assert ws._broadcast_btn.isEnabled()

    ws._broadcast()

    sent = [t.terminal.commands for t in ws.tiles]
    assert sent == [["git status\r"]] * 3
    # feedback flashes in the toolbar title, then restores the pane count
    assert "3 panes" in ws._count_label.text()
    ws._restore_status()
    assert ws._count_label.text() == "MegaCode · 3 panes"


def test_broadcast_skips_dead_panes(make_ws):
    ws = make_ws(2)
    ws.tiles[1].terminal.is_dead = lambda: True
    ws._broadcast_input.setText("dir")

    ws._broadcast()

    assert ws.tiles[0].terminal.commands == ["dir\r"]
    assert ws.tiles[1].terminal.commands == []
    # the flash reports what actually received it
    assert "1 pane" in ws._count_label.text()


def test_broadcast_skips_chat_tiles(make_ws):
    """A chat tile is not a console: it must never receive shell commands."""
    ws = make_ws(2)
    # a chat-tile stand-in: not a TerminalWidget, so not a broadcast target
    # (the tick() keeps the view's exit-detector timer happy meanwhile)
    ws.tiles.append(SimpleNamespace(terminal=SimpleNamespace(tick=lambda: None)))
    ws._broadcast_input.setText("dir")

    ws._broadcast()

    assert [t.terminal.commands for t in ws.tiles[:2]] == [["dir\r"], ["dir\r"]]


def test_broadcast_ignores_blank_input(make_ws):
    ws = make_ws(2)
    ws._broadcast_input.setText("   ")
    assert not ws._broadcast_btn.isEnabled()

    ws._broadcast()

    assert all(t.terminal.commands == [] for t in ws.tiles)


def test_broadcast_leaves_waiting_panes_to_run_pasted(make_ws):
    """run_command appends the CR itself: on a pane holding a pasted line that
    would execute "<pasted><command>" as ONE line. Those panes are the Run
    pasted button's job, so Run all skips them (and says so in the flash)."""
    ws = make_ws(3)
    ws.tiles[1].terminal.set_pending(True)
    ws._broadcast_input.setText("git status")

    ws._broadcast()

    assert ws.tiles[0].terminal.commands == ["git status\r"]
    assert ws.tiles[1].terminal.commands == []
    assert ws.tiles[2].terminal.commands == ["git status\r"]
    assert "2 panes" in ws._count_label.text()


def test_broadcast_enter_in_input_runs(make_ws):
    ws = make_ws(1)
    ws._broadcast_input.setText("echo hi")

    ws._broadcast_input.returnPressed.emit()

    assert ws.tiles[0].terminal.commands == ["echo hi\r"]


def test_broadcast_returns_focus_to_a_pane(make_ws, qapp):
    """Enter-to-run must not strand the keyboard in the box: a follow-up
    keystroke would silently mutate the retained command and re-run it in
    every pane. The Enter path hands focus to the first tile; the click path
    returns it to the pane the user came from."""
    from PySide6.QtWidgets import QApplication

    ws = make_ws(2)
    for tile in ws.tiles:
        tile.terminal.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    ws._broadcast_input.setFocus()
    qapp.processEvents()
    ws._broadcast_input.setText("dir")
    ws._broadcast_input.returnPressed.emit()
    qapp.processEvents()
    assert QApplication.focusWidget() is ws.tiles[0].terminal

    ws.tiles[1].terminal.setFocus()
    qapp.processEvents()
    ws._broadcast_btn.click()
    qapp.processEvents()
    assert QApplication.focusWidget() is ws.tiles[1].terminal


def test_relaunch_clears_the_broadcast_bar(make_ws):
    """A stale command must not stay armed across a relaunch: one accidental
    click would run the previous session's command in the fresh panes."""
    ws = make_ws(2)
    ws._broadcast_input.setText("del node_modules")

    ws.start(3, "fake", os.getcwd(), font_size=10, label="term")

    assert ws._broadcast_input.text() == ""
    assert not ws._broadcast_btn.isEnabled()


def test_broadcast_bar_refuses_tile_drops(make_ws):
    """Tile drags carry text/plain; a missed drop must not type a tile index
    into the command box."""
    ws = make_ws(1)
    assert not ws._broadcast_input.acceptDrops()


def test_broadcast_bar_takes_spare_width_but_degrades_first(make_ws):
    """The box gets the layout stretch (capped), and the title label -- not
    the buttons -- carries the narrow-window squeeze (small explicit floor)."""
    ws = make_ws(1)
    bar = ws._broadcast_input.parentWidget()
    assert ws._broadcast_input.maximumWidth() == 460
    assert ws._count_label.minimumWidth() <= 64
    lay = bar.layout()
    assert lay.stretch(lay.indexOf(ws._broadcast_input)) == 1


def test_add_tile_restores_flashed_status(make_ws):
    """A flash cut short by a rebuild shows the new count, never stale text."""
    ws = make_ws(1)
    ws._flash_status("ran in 1 pane")
    ws._new_tile("fake", "term")

    assert ws._count_label.text() == "MegaCode · 2 panes"


def test_add_button_plain_click_adds_default_shell_kind(make_ws, monkeypatch):
    """The toolbar's Add button adds the platform's default shell (cmd on
    Windows, bash on Astra) -- the same kind the launcher preselects, not
    claude: the user asked for a shell by default."""
    import megacode.shells as shells

    resolved = []
    monkeypatch.setattr(
        wsm.shells, "resolve", lambda kind, custom=None: resolved.append(kind)
        or "/bin/fake-default-shell")
    ws = make_ws(1)
    before = len(ws.tiles)

    ws._add_btn.click()

    assert len(ws.tiles) == before + 1
    assert resolved == [shells.DEFAULT_KIND]
    # the tile is labeled with the default kind, not "claude"
    assert shells.label_for(shells.DEFAULT_KIND) in ws.tiles[-1]._label


# --- run pasted (Enter in every pane that is holding input) ---------------------


def test_run_pasted_executes_every_waiting_pane(make_ws):
    ws = make_ws(3)
    ws.tiles[0].terminal.set_pending(True)
    ws.tiles[2].terminal.set_pending(True)
    assert ws._exec_btn.isEnabled()

    ws._execute_pasted()

    # each waiting pane got exactly its Enter; the idle pane is untouched
    assert ws.tiles[0].terminal.commands == ["\r"]
    assert ws.tiles[1].terminal.commands == []
    assert ws.tiles[2].terminal.commands == ["\r"]
    assert "2 panes" in ws._count_label.text()
    # nothing waits anymore: the button disarms itself
    assert not ws._exec_btn.isEnabled()


def test_run_pasted_skips_dead_panes(make_ws):
    ws = make_ws(2)
    ws.tiles[1].terminal.set_pending(True)
    ws.tiles[1].terminal.is_dead = lambda: True

    ws._execute_pasted()

    assert ws.tiles[1].terminal.commands == []


def test_exec_button_starts_disarmed_and_tracks_pending(make_ws):
    ws = make_ws(2)
    assert not ws._exec_btn.isEnabled()
    assert ws._exec_btn.text() == "↵  Run pasted"

    ws.tiles[1].terminal.set_pending(True)
    assert ws._exec_btn.isEnabled()
    # the armed state must be findable: waiting count in the label + accent
    assert "(1)" in ws._exec_btn.text()
    assert ws._exec_btn.property("armed") == "true"

    # executing the line by hand disarms the button again
    ws.tiles[1].terminal.set_pending(False)
    assert not ws._exec_btn.isEnabled()
    assert ws._exec_btn.text() == "↵  Run pasted"
    assert ws._exec_btn.property("armed") == "false"


def test_exec_button_count_tracks_how_many_panes_wait(make_ws):
    ws = make_ws(3)
    ws.tiles[0].terminal.set_pending(True)
    ws.tiles[2].terminal.set_pending(True)
    assert "(2)" in ws._exec_btn.text()


def test_closing_the_only_waiting_pane_disarms_run_pasted(make_ws):
    ws = make_ws(2)
    ws.tiles[1].terminal.set_pending(True)
    assert ws._exec_btn.isEnabled()

    ws._on_close_tile(1)

    assert not ws._exec_btn.isEnabled()


def test_relaunch_resets_sync_and_run_pasted(make_ws):
    """A stale sync toggle or waiting input must not carry into a fresh
    workspace: new panes must not receive the old session's keys or Enters."""
    ws = make_ws(2)
    ws._sync_btn.setChecked(True)
    ws.tiles[0].terminal.set_pending(True)
    assert ws._exec_btn.isEnabled()

    ws.start(2, "fake", os.getcwd(), font_size=10, label="term")

    assert not ws._sync_btn.isChecked()
    assert not ws._exec_btn.isEnabled()


# --- sync input (mirror the keyboard to every pane) ------------------------------


def test_sync_mirrors_keys_and_pastes_to_other_panes(make_ws):
    ws = make_ws(3)
    ws._sync_btn.setChecked(True)

    ws.tiles[0].terminal.inputSent.emit("d", False)
    assert [t.terminal.injected for t in ws.tiles] == [
        [], [("d", False)], [("d", False)],
    ]

    # paste payloads mirror as pastes (receivers apply their own paste mode)
    ws.tiles[1].terminal.inputSent.emit("npm test", True)
    assert ws.tiles[0].terminal.injected[-1] == ("npm test", True)
    assert ws.tiles[2].terminal.injected[-1] == ("npm test", True)
    assert ws.tiles[1].terminal.injected == [("d", False)]  # no self-echo


def test_sync_off_keeps_panes_individual(make_ws):
    ws = make_ws(2)
    ws.tiles[0].terminal.inputSent.emit("d", False)

    assert ws.tiles[1].terminal.injected == []


def test_sync_mirror_skips_dead_panes_and_chat_tiles(make_ws):
    ws = make_ws(3)
    ws.tiles[2].terminal.is_dead = lambda: True
    ws.tiles.append(SimpleNamespace(terminal=SimpleNamespace(tick=lambda: None)))
    ws._sync_btn.setChecked(True)

    ws.tiles[0].terminal.inputSent.emit("x", False)

    assert ws.tiles[1].terminal.injected == [("x", False)]
    assert ws.tiles[2].terminal.injected == []


def test_mirrored_enter_runs_the_receiving_panes_waiting_input(make_ws):
    ws = make_ws(2)
    ws.tiles[1].terminal.set_pending(True)
    ws._sync_btn.setChecked(True)

    ws.tiles[0].terminal.inputSent.emit("\r", False)

    assert not ws.tiles[1].terminal.has_pending_input()
    assert ws.tiles[1].terminal.injected == [("\r", False)]


def test_swap_keeps_sync_fanout_on_widgets(make_ws):
    """The per-pane connections must follow the WIDGET, not its position:
    after a drag-swap the reordered tiles still mirror from the pane the user
    actually types in, and the source never receives its own echo."""
    ws = make_ws(2)
    a, b = ws.tiles[0].terminal, ws.tiles[1].terminal
    ws._sync_btn.setChecked(True)

    ws._on_swap(0, 1)
    assert ws.tiles[0].terminal is b and ws.tiles[1].terminal is a

    a.inputSent.emit("x", False)  # typed into a, which now sits on the right

    assert a.injected == []                    # no self-echo
    assert b.injected == [("x", False)]        # the peer still gets it


def test_run_pasted_finds_its_pane_after_a_swap(make_ws):
    ws = make_ws(2)
    a, _b = ws.tiles[0].terminal, ws.tiles[1].terminal

    ws._on_swap(0, 1)
    a.set_pending(True)

    ws._execute_pasted()

    assert a.commands == ["\r"]  # a moved to index 1 but is still the target


# --- the two features cross-wired on the REAL widget -----------------------------
#
# Every assertion above runs against _FakeTerminal; the fake mirrors the API,
# but the paste->pending->mirror wiring must hold for the real TerminalWidget
# too (the `term` fixture in test_terminal_widget.py swaps in a fake Pty the
# same way).


class _FakeRealPty:
    """No child process; output is fed by the test, writes are recorded."""

    alive = True
    LNM_WORKAROUND = True

    def __init__(self, *_a, **_k) -> None:
        self.written: List[str] = []

    def start(self, _on_output) -> None:
        return None

    def write(self, text: str) -> None:
        self.written.append(text)

    def resize(self, _cols: int, _rows: int) -> None:
        return None

    def is_alive(self) -> bool:
        return self.alive

    def stop(self) -> None:
        return None


def test_real_widget_mirrored_paste_arms_and_run_all_skips(qapp, monkeypatch):
    from megacode import terminal_widget as tw
    from PySide6.QtGui import QGuiApplication

    monkeypatch.setattr(tw, "Pty", _FakeRealPty)
    ws = wsm.WorkspaceView()
    ws.resize(1200, 800)
    ws.start(2, "fake", os.getcwd(), font_size=10, label="term")
    src, dst = ws.tiles[0].terminal, ws.tiles[1].terminal
    ws._sync_btn.setChecked(True)   # mirrored paste is a sync-mode delivery

    # paste into the focused pane: the raw text travels, the receiver applies
    # its own (plain, no-bracketed-paste) delivery and arms -- on BOTH panes
    QGuiApplication.clipboard().setText("echo hi\n")
    src.paste()

    assert src._pty.written == ["echo hi"]
    assert dst._pty.written == ["echo hi"]
    assert src.has_pending_input() and dst.has_pending_input()
    assert ws._exec_btn.isEnabled()
    assert "(2)" in ws._exec_btn.text()

    # ▶ Run all must NOT append onto the waiting lines; ↵ Run pasted runs both
    ws._broadcast_input.setText("git status")
    ws._broadcast()
    assert src._pty.written == ["echo hi"] and dst._pty.written == ["echo hi"]

    ws._execute_pasted()
    assert src._pty.written[-1] == "\r" and dst._pty.written[-1] == "\r"
    assert not ws._exec_btn.isEnabled()


# --- tag groups (sync domains + @tag scoped broadcast) ---------------------------


class _FakeChat(QWidget):
    """Stands in for ChatWidget: a tile widget that is NOT a console pane.

    Must be a DIFFERENT class from _FakeTerminal: the workspace decides
    "console pane or not" via isinstance against TerminalWidget, which the
    fixture patches to _FakeTerminal.
    """

    finished = Signal()

    def __init__(self, _cwd, font_size=10):  # noqa: ARG002
        super().__init__()
        self.seen = []

    def is_dead(self) -> bool:
        return False

    def tick(self) -> None:
        return None

    def close(self) -> None:
        return None


def _focus_pane(ws, i, qapp):
    """Focus a fake pane for tint tests. QWidget defaults to NoFocus, so the
    policy must be opted into first (the real widget sets StrongFocus); the
    existing focus test does exactly the same dance."""
    ws.tiles[i].terminal.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    ws.tiles[i].terminal.setFocus()
    qapp.processEvents()


def _sync_states(ws) -> list:
    """Each tile's header 'sync' property (None before the first tint)."""
    return [t.header.property("sync") for t in ws.tiles]


def test_no_tags_mirror_is_byte_identical_to_today(make_ws):
    """The hard no-tag contract: with zero tags the fan-out is every pane."""
    ws = make_ws(3)
    ws._sync_btn.setChecked(True)

    ws.tiles[0].terminal.inputSent.emit("d", False)

    assert [t.terminal.injected for t in ws.tiles] == [
        [], [("d", False)], [("d", False)],
    ]


def test_left_click_arm_mirrors_across_tag_groups(make_ws):
    """The new left-click contract: with tags around, a plain click mirrors
    to EVERY pane -- tag groups are the right-click menu's business, not the
    click's. (Zero tags stay covered by the byte-identical test above.)"""
    ws = make_ws(3)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])
    ws.tiles[2].set_tags(["b"])
    ws._sync_btn.setChecked(True)

    ws.tiles[0].terminal.inputSent.emit("d", False)

    assert ws.tiles[1].terminal.injected == [("d", False)]  # same group
    assert ws.tiles[2].terminal.injected == [("d", False)]  # other group too


def test_untagged_source_reaches_tagged_panes(make_ws):
    ws = make_ws(4)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])

    ws._sync_btn.setChecked(True)
    ws.tiles[2].terminal.inputSent.emit("d", False)

    assert ws.tiles[3].terminal.injected == [("d", False)]
    assert ws.tiles[0].terminal.injected == [("d", False)]
    assert ws.tiles[1].terminal.injected == [("d", False)]


def test_scoped_arm_follows_widget_across_swap(make_ws):
    """Tags ride the tile, the mirror wiring rides the widget: after a
    drag-swap the scoped arm still delivers only inside its tag group."""
    ws = make_ws(3)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])
    tagged, peer = ws.tiles[0].terminal, ws.tiles[1].terminal
    ws._apply_sync(True, "a", None)

    ws._on_swap(0, 2)  # tagged session moves to index 2
    assert ws.tiles[2].terminal is tagged

    tagged.inputSent.emit("x", False)

    assert peer.injected == [("x", False)]
    assert ws.tiles[0].terminal.injected == []  # untagged never in @a
    assert tagged.injected == []                # no self-echo


def test_dead_tagged_pane_not_a_sync_target(make_ws):
    ws = make_ws(2)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])
    ws.tiles[1].terminal.is_dead = lambda: True
    ws._sync_btn.setChecked(True)

    ws.tiles[0].terminal.inputSent.emit("x", False)

    assert ws.tiles[1].terminal.injected == []


def test_new_untagged_pane_joins_the_all_arm_only(make_ws):
    """A fresh pane receives mirrored keys under the left-click (all) arm --
    it is "every pane" -- but stays outside a scoped arm until tagged."""
    ws = make_ws(2)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])
    ws._sync_btn.setChecked(True)
    new = ws._new_tile("fake", "term")

    ws.tiles[0].terminal.inputSent.emit("d", False)
    assert ws.tiles[1].terminal.injected == [("d", False)]
    assert new.terminal.injected == [("d", False)]  # all means all

    ws._pick_sync_scope("a")
    ws.tiles[0].terminal.inputSent.emit("k", False)
    assert ws.tiles[1].terminal.injected == [("d", False), ("k", False)]
    assert new.terminal.injected == [("d", False)]  # scoped keeps it out


def test_run_all_stays_global_when_tags_exist(make_ws):
    """The button's name is the contract: tags alone never scope Run all."""
    ws = make_ws(3)
    ws.tiles[0].set_tags(["x"])
    ws._broadcast_input.setText("git status")

    ws._broadcast()

    assert [t.terminal.commands for t in ws.tiles] == [["git status\r"]] * 3
    assert ws._count_label.text() == "ran in 3 panes"  # no suffix


def test_run_all_selector_scopes_to_tag(make_ws):
    ws = make_ws(4)
    ws.tiles[0].set_tags(["fe"])
    ws.tiles[1].set_tags(["fe"])
    ws.tiles[2].set_tags(["be"])
    ws.tiles[3].set_tags(["be"])
    ws._broadcast_input.setText("@fe git pull")

    ws._broadcast()

    assert ws.tiles[0].terminal.commands == ["git pull\r"]
    assert ws.tiles[1].terminal.commands == ["git pull\r"]
    assert ws.tiles[2].terminal.commands == []
    assert ws.tiles[3].terminal.commands == []
    assert ws._count_label.text() == "ran in 2 panes · @fe"


def test_run_all_scoped_skips_pending_and_dead_in_group(make_ws):
    ws = make_ws(3)
    for t in ws.tiles:
        t.set_tags(["fe"])
    ws.tiles[1].terminal.set_pending(True)
    ws.tiles[2].terminal.is_dead = lambda: True
    ws._broadcast_input.setText("@fe cmd")

    ws._broadcast()

    assert ws.tiles[0].terminal.commands == ["cmd\r"]
    assert ws.tiles[1].terminal.commands == []
    assert ws.tiles[2].terminal.commands == []
    assert ws._count_label.text() == "ran in 1 pane · @fe"


def test_run_all_selector_passes_through_when_tag_unknown_and_no_tags_exist(make_ws):
    """Zero tags: '@echo off' must run verbatim, exactly like today."""
    ws = make_ws(2)
    ws._broadcast_input.setText("@echo off")

    ws._broadcast()

    assert [t.terminal.commands for t in ws.tiles] == [["@echo off\r"]] * 2
    assert ws._count_label.text() == "ran in 2 panes"


def test_run_all_selector_unknown_tag_warns_when_tags_exist(make_ws):
    """With tags around, a misspelled '@tag' still runs everywhere -- but
    the flash says the group never existed."""
    ws = make_ws(2)
    ws.tiles[0].set_tags(["fe"])
    ws._broadcast_input.setText("@nope cmd")

    ws._broadcast()

    assert [t.terminal.commands for t in ws.tiles] == [["@nope cmd\r"]] * 2
    assert "(no '@nope' group)" in ws._count_label.text()


def test_run_all_selector_requires_body(make_ws):
    """A bare '@fe' is not a selector: no body, no scoping."""
    ws = make_ws(2)
    ws.tiles[0].set_tags(["fe"])
    ws._broadcast_input.setText("@fe")

    ws._broadcast()

    assert [t.terminal.commands for t in ws.tiles] == [["@fe\r"]] * 2


def test_run_all_selector_with_only_chat_tiles_matching_flashes_no_target(
        make_ws, monkeypatch):
    """An explicit selector that matches no live console runs NOTHING."""
    monkeypatch.setattr(wsm, "ChatWidget", _FakeChat)
    ws = make_ws(2)
    chat = ws._new_tile(None, "chat", "chat")
    chat.set_tags(["fe"])  # organizational tag on a non-console tile
    ws._broadcast_input.setText("@fe cmd")

    ws._broadcast()

    assert all(t.terminal.commands == [] for t in ws.tiles[:2])
    assert chat.terminal.seen == []
    assert ws._count_label.text() == "no live pane tagged '@fe'"


def test_scoped_miss_runs_nothing_and_hands_focus_back(make_ws, qapp, monkeypatch):
    """The scoped-empty exit must not strand the keyboard in the box."""
    monkeypatch.setattr(wsm, "ChatWidget", _FakeChat)
    ws = make_ws(2)
    for t in ws.tiles:
        t.terminal.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    ws._new_tile(None, "chat", "chat").set_tags(["fe"])
    ws.tiles[0].terminal.setFocus()
    qapp.processEvents()
    ws._broadcast_input.setText("@fe cmd")

    ws._broadcast()

    assert QApplication.focusWidget() is ws.tiles[0].terminal


def test_run_pasted_ignores_groups(make_ws):
    """Pastes are hand-placed per pane: Run pasted fires every waiting pane,
    whatever their tags."""
    ws = make_ws(2)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["b"])
    ws.tiles[0].terminal.set_pending(True)
    ws.tiles[1].terminal.set_pending(True)

    ws._execute_pasted()

    assert [t.terminal.commands for t in ws.tiles] == [["\r"], ["\r"]]


def test_multiline_paste_still_scopes(make_ws):
    """A pasted multi-line "@fe ..." must not silently degrade into a run
    in EVERY pane (the interior newline must not defeat the selector)."""
    ws = make_ws(3)
    ws.tiles[0].set_tags(["fe"])
    ws.tiles[1].set_tags(["fe"])
    ws._broadcast_input.setText("@fe git pull\nnpm test")

    ws._broadcast()

    assert ws.tiles[0].terminal.commands == ["git pull\nnpm test\r"]
    assert ws.tiles[1].terminal.commands == ["git pull\nnpm test\r"]
    assert ws.tiles[2].terminal.commands == []
    assert ws._count_label.text() == "ran in 2 panes · @fe"


def test_chat_tile_sharing_tag_never_receives_mirror(make_ws, monkeypatch):
    """A chat tile can carry the same tag as the source pane; it must never
    receive mirrored keys (organizational tags only)."""
    monkeypatch.setattr(wsm, "ChatWidget", _FakeChat)
    ws = make_ws(2)
    chat = ws._new_tile(None, "chat", "chat")
    ws.tiles[0].set_tags(["fe"])
    ws.tiles[1].set_tags(["fe"])
    chat.set_tags(["fe"])
    ws._sync_btn.setChecked(True)

    ws.tiles[0].terminal.inputSent.emit("x", False)

    assert ws.tiles[1].terminal.injected == [("x", False)]
    assert chat.terminal.seen == []


# --- tag model, chips, menu, hints ------------------------------------------------


def test_tile_tag_api_dedups_and_signals(make_ws):
    ws = make_ws(1)
    tile = ws.tiles[0]
    seen = []
    tile.tagsChanged.connect(lambda a, r: seen.append((a, r)))

    tile.set_tags(["a", "A ", "b"])  # 'A ' normalizes onto the first 'a'
    assert tile.tags() == ["a", "b"]
    assert seen == [("a", "")]

    tile.set_tags(["a", "b"])  # no-op set: no signal
    assert seen == [("a", "")]

    tile.toggle_tag("b")  # removal
    assert tile.tags() == ["a"]
    assert seen[-1] == ("", "b")

    tile.set_tags([])  # clearing the last
    assert tile.tags() == []
    assert seen[-1] == ("", "a")


def test_known_tags_first_seen_order(make_ws):
    ws = make_ws(2)
    ws.tiles[0].set_tags(["z", "q"])
    ws.tiles[1].set_tags(["q", "m"])
    assert ws._known_tags() == ["z", "q", "m"]


def test_closing_last_tagged_pane_drops_tag_and_restores_hints(make_ws, qapp):
    """Tags die with the pane -- and the tag-era toolbar hints must revert
    through the close path too (a closing tile emits nothing)."""
    ws = make_ws(2)
    ws.tiles[1].set_tags(["solo"])
    assert ws._known_tags() == ["solo"]
    assert ws._broadcast_input.placeholderText() == wsm._BROADCAST_PLACEHOLDER_TAGS

    ws._on_close_tile(1)
    qapp.processEvents()

    assert ws._known_tags() == []
    assert ws._broadcast_input.placeholderText() == wsm._BROADCAST_PLACEHOLDER_BASE
    assert ws._sync_btn.toolTip() == wsm._SYNC_TIP_BASE
    assert ws._broadcast_input.toolTip() == wsm._BROADCAST_TIP_BASE


def test_hint_constants_have_no_invisible_characters():
    """Typed here, never copy-pasted: zero-width chars must not ship in a
    tooltip (a workflow spec once smuggled one in)."""
    for s in (wsm._SYNC_TIP_BASE, wsm._SYNC_TIP_TAGS,
              wsm._BROADCAST_PLACEHOLDER_BASE, wsm._BROADCAST_PLACEHOLDER_TAGS,
              wsm._BROADCAST_TIP_BASE, wsm._BROADCAST_TIP_TAGS):
        assert "​" not in s and "﻿" not in s


def test_hints_extend_only_while_tags_exist(make_ws):
    ws = make_ws(2)
    # zero tags: byte-identical pre-tags toolbar
    assert ws._broadcast_input.placeholderText() == "Command for every pane…"
    assert ws._sync_btn.toolTip() == wsm._SYNC_TIP_BASE

    ws.tiles[0].set_tags(["fe"])
    assert ws._broadcast_input.placeholderText() == wsm._BROADCAST_PLACEHOLDER_TAGS
    assert ws._sync_btn.toolTip() == wsm._SYNC_TIP_BASE + wsm._SYNC_TIP_TAGS
    assert ws._broadcast_input.toolTip() == wsm._BROADCAST_TIP_BASE + wsm._BROADCAST_TIP_TAGS

    ws.tiles[0].set_tags([])  # the last tag went away: hints revert
    assert ws._broadcast_input.placeholderText() == wsm._BROADCAST_PLACEHOLDER_BASE
    assert ws._sync_btn.toolTip() == wsm._SYNC_TIP_BASE


def test_tag_menu_actions_toggle_membership(make_ws, monkeypatch):
    ws = make_ws(2)
    ws.tiles[1].set_tags(["fe"])

    menu = ws._build_tile_menu(ws.tiles[0])
    fe = next(a for a in menu.actions() if a.text() == "fe")
    assert not fe.isChecked()
    fe.toggle()
    assert ws.tiles[0].tags() == ["fe"]

    # "New tag…": the dialog path adds the typed tag
    monkeypatch.setattr(
        wsm.QInputDialog, "getText",
        staticmethod(lambda *a, **k: ("zz", True)),
    )
    ws._on_new_tag(ws.tiles[0])
    assert "zz" in ws.tiles[0].tags()

    # naming an already-checked tag never unchecks it, and still answers
    monkeypatch.setattr(
        wsm.QInputDialog, "getText",
        staticmethod(lambda *a, **k: ("fe", True)),
    )
    ws._on_new_tag(ws.tiles[0])
    assert "fe" in ws.tiles[0].tags()
    assert 'group "fe"' in ws._count_label.text()


def test_clear_tags_menu_entry(make_ws):
    ws = make_ws(1)
    ws.tiles[0].set_tags(["a", "b"])
    menu = ws._build_tile_menu(ws.tiles[0])

    clear = next(a for a in menu.actions() if a.text() == "Clear tags")
    assert clear.isEnabled()
    clear.trigger()
    assert ws.tiles[0].tags() == []


def test_tag_flash_reports_group_size(make_ws):
    ws = make_ws(2)
    ws.tiles[0].set_tags(["build"])
    assert ws._count_label.text() == 'group "build": 1 pane'
    ws.tiles[1].set_tags(["build"])
    assert ws._count_label.text() == 'group "build": 2 panes'
    # removal recounts too -- dropping to zero says the group dissolved
    ws.tiles[1].toggle_tag("build")
    assert ws._count_label.text() == 'group "build": 1 pane'
    ws.tiles[0].toggle_tag("build")
    assert ws._count_label.text() == 'group "build": 0 panes'


def test_chips_render_tags_and_hide_when_untagged(make_ws, qapp):
    ws = make_ws(1)
    tile = ws.tiles[0]

    assert not tile.header.chips.isVisible()  # pixel-identical untagged header
    assert tile.header.toolTip() == (
        "Double-click to rename · drag onto another tile to swap"
    )

    # short tags: whether a long tag elides is font-metric dependent; the
    # elide path itself is covered by the minimum-size test's 16-char tags
    tile.set_tags(["fe", "be", "ci", "dx"])
    qapp.processEvents()
    assert tile.header.chips.isVisible()
    assert tile.header.chip_texts() == ["fe", "be", "+2"]
    chips_tip = tile.header._chips_layout.itemAt(2).widget().toolTip()
    assert chips_tip == "ci, dx"
    assert tile.header.toolTip().startswith("Tags: fe, be, ci, dx ·")

    tile.set_tags([])
    qapp.processEvents()
    assert not tile.header.chips.isVisible()
    assert tile.header.toolTip() == (
        "Double-click to rename · drag onto another tile to swap"
    )


def test_visible_chips_in_one_header_never_share_a_color_class(make_ws):
    from megacode import tags as tagmod

    # find two tags that hash to the same color class...
    seen: dict = {}
    colliding = None
    for i in range(500):
        t = f"tag{i}"
        c = tagmod.tag_class(t)
        if c in seen:
            colliding = (seen[c], t)
            break
        seen[c] = t
    assert colliding is not None  # 6 classes, pigeonhole long before 500

    ws = make_ws(1)
    ws.tiles[0].set_tags([*colliding, "zzz"])
    classes = [
        ws.tiles[0].header._chips_layout.itemAt(i).widget().property("tagClass")
        for i in range(3)
    ]
    assert len(set(classes)) == 3  # de-collided: all three distinguishable


@pytest.mark.parametrize("scheme_name", sorted(wsm.themes.SCHEMES))
def test_chip_pixels_follow_the_scheme_palette(make_ws, qapp, scheme_name):
    """Offscreen pixel proof that a chip renders with ITS scheme's palette:
    the sampled interior is the class background, and the LABEL's glyphs
    read as the class foreground against it -- dark-on-pastel in
    paper-light, light-on-dark in the dark themes. Exercises the whole
    chain (palette slot -> QSS rule -> property selector), not just the
    constants."""
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QLabel

    from megacode import tags as tagmod
    from megacode import themes as th

    def cheb(a: QColor, b: QColor) -> int:
        return max(abs(a.red() - b.red()), abs(a.green() - b.green()),
                   abs(a.blue() - b.blue()))

    was = th.active_name()
    th.set_active(scheme_name)
    try:
        ws = make_ws(1)
        ws.setStyleSheet(th.build_qss(th.SCHEMES[scheme_name]))
        ws.tiles[0].set_tags(["build"])
        qapp.processEvents()
        chip = ws.tiles[0].header._chips_layout.itemAt(0).widget()
        bg, _border, fg = th.tag_colors()[tagmod.tag_class("build")]

        # interior sample at mid-height: past the 1px border, left of the
        # label's 6px text padding, away from the 7px corner radii -- the
        # pure class background
        img = chip.grab().toImage()
        sample = img.pixelColor(4, img.height() // 2)
        assert abs(sample.lightness() - QColor(bg).lightness()) <= 3, (
            scheme_name, sample.name(), bg)

        # Glyph pin, on the LABEL ALONE (grabbing the chip would mix in the
        # (x) button, which carries the class fg through its own selector
        # and would mask a broken label rule). The label's background is
        # transparent, so any sufficiently-opaque pixel IS glyph; Qt hands
        # out UN-premultiplied colors, so anti-aliasing only touches alpha,
        # never the RGB a glyph pixel carries. The nearest opaque pixel to
        # the class fg must sit closer than a fallback to the scheme's base
        # text color ever could -- that margin is what actually pins the
        # per-class rule (a lightness-only tolerance let the fallback pass
        # in every scheme; found by review mutation experiment).
        label = chip.findChild(QLabel, "tileTagName")
        limg = label.grab().toImage()
        glyph_px = [limg.pixelColor(x, y)
                    for x in range(limg.width())
                    for y in range(limg.height())
                    if limg.pixelColor(x, y).alpha() > 64]
        assert glyph_px, scheme_name  # the tag name must actually render
        fg_c, text_c = QColor(fg), QColor(th.active()["text"])
        nearest = min(cheb(px, fg_c) for px in glyph_px)
        # any glyph pixel matches the class fg exactly-ish; the fallback
        # color's own pixels would sit at >= cheb(fg, text) instead
        assert nearest <= max(8, cheb(fg_c, text_c) - 8), (
            scheme_name, nearest, fg, th.active()["text"])
        # and the pen must still differ from the plate it sits on
        assert abs(fg_c.lightness() - QColor(bg).lightness()) > 60, (
            scheme_name, fg, bg)
    finally:
        th.set_active(was)


def test_chips_cannot_raise_tile_minimum_without_bound(make_ws):
    """A long tag list must not inflate the header's -- and so the tile's
    and window's -- minimum width: the display is capped, not the model."""
    ws = make_ws(1)
    base = ws.tiles[0].header.minimumSizeHint().width()

    ws.tiles[0].set_tags(["a" * 16] * 9)   # 9 tags -> 2 chips + "+7"
    nine = ws.tiles[0].header.minimumSizeHint().width()
    ws.tiles[0].set_tags(["a" * 16] * 3)   # 3 tags -> same 2 chips + "+1"
    three = ws.tiles[0].header.minimumSizeHint().width()

    assert three == nine              # the cap does not grow with the count
    assert nine < base + 160          # and stays well inside a tile's floor


def test_chip_x_removes_only_that_tag(make_ws, qapp):
    """A tag name leaves the pane ONLY by its explicit (x): this pane drops
    the tag, the group's other holders keep it, and the recount says so."""
    ws = make_ws(2)
    ws.tiles[0].set_tags(["fe", "be"])
    ws.tiles[1].set_tags(["fe"])

    chip = ws.tiles[0].header._chips_layout.itemAt(0).widget()
    x = chip.findChild(QToolButton, "tileTagX")
    x.click()
    qapp.processEvents()

    assert ws.tiles[0].tags() == ["be"]
    assert ws.tiles[1].tags() == ["fe"]  # other holders untouched
    assert ws.tiles[0].header.chip_texts() == ["be"]
    assert ws._count_label.text() == 'group "fe": 1 pane'

    # the "+N" counter is not a tag: it carries no (x)
    ws.tiles[0].set_tags(["a", "b", "c"])
    counter = ws.tiles[0].header._chips_layout.itemAt(2).widget()
    assert counter.findChild(QToolButton, "tileTagX") is None


def test_chip_x_double_click_removes_only_one_tag(make_ws, qapp):
    """The double-click's second press lands on whatever slid under the
    stationary cursor after the rebuild -- the (x) must swallow it, or one
    double-click wipes two tags (the second never aimed at)."""
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    def _dblclick(w):
        center = QPointF(w.rect().center())
        ev = QMouseEvent(QEvent.Type.MouseButtonDblClick,
                         center, center, center,
                         Qt.MouseButton.LeftButton,
                         Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(w, ev)

    ws = make_ws(1)
    t = ws.tiles[0]
    t.set_tags(["aaaa", "bbbb"])

    x0 = t.header._chips_layout.itemAt(0).widget().findChild(
        QToolButton, "tileTagX")
    x0.click()  # first release of the double-click: "aaaa" leaves
    qapp.processEvents()
    assert t.tags() == ["bbbb"]

    x1 = t.header._chips_layout.itemAt(0).widget().findChild(
        QToolButton, "tileTagX")
    _dblclick(x1)  # the second press, on the slid-in (x)
    qapp.processEvents()
    assert t.tags() == ["bbbb"]  # swallowed: not removed

    x1.click()  # a deliberate fresh click still works
    qapp.processEvents()
    assert t.tags() == []


def test_relaunch_clears_tags_tint_and_hints(make_ws, qapp):
    ws = make_ws(2)
    ws.tiles[0].set_tags(["fe"])
    ws.tiles[1].set_tags(["fe"])
    ws._sync_btn.setChecked(True)
    assert ws._known_tags() == ["fe"]

    ws.start(2, "fake", os.getcwd(), font_size=10, label="term")
    qapp.processEvents()

    assert all(t.tags() == [] for t in ws.tiles)
    assert ws._known_tags() == []
    assert not any(_sync_states(ws))  # no stale tint
    assert ws._broadcast_input.placeholderText() == wsm._BROADCAST_PLACEHOLDER_BASE
    assert ws._sync_btn.toolTip() == wsm._SYNC_TIP_BASE


# --- sync tint (WHERE a keystroke will go) ----------------------------------------


def test_sync_tint_marks_every_pane_when_unscoped(make_ws, qapp):
    """The left-click arm's audience is everyone: every header lights, tag
    groups and all. Scoping (see below) is what narrows the tint."""
    ws = make_ws(3)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])
    ws.tiles[2].set_tags(["b"])
    ws._sync_btn.setChecked(True)
    _focus_pane(ws, 0, qapp)

    assert _sync_states(ws) == ["source", "peer", "peer"]

    _focus_pane(ws, 2, qapp)  # any pane drives the same everyone audience
    assert _sync_states(ws) == ["peer", "peer", "source"]

    ws._sync_btn.setChecked(False)  # sync off: everything clears
    assert _sync_states(ws) == ["", "", ""]


def test_zero_tags_never_tint(make_ws, qapp):
    """The no-tag contract: no header may light without tags, ever."""
    ws = make_ws(3)
    ws._sync_btn.setChecked(True)
    _focus_pane(ws, 0, qapp)

    assert not any(_sync_states(ws))


def test_scoped_tint_marks_only_the_group(make_ws, qapp):
    """A scoped arm narrows the tint to the chosen group; a pane outside it
    (here: the untagged one) keeps its keyboard local and its header dark."""
    ws = make_ws(3)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])
    ws._apply_sync(True, "a", None)
    _focus_pane(ws, 0, qapp)

    assert _sync_states(ws) == ["source", "peer", ""]

    _focus_pane(ws, 2, qapp)  # outside the armed group: nothing promised
    assert _sync_states(ws) == ["", "", ""]


def test_sync_tint_clears_on_focus_loss(make_ws, qapp):
    ws = make_ws(3)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])
    ws._sync_btn.setChecked(True)
    _focus_pane(ws, 0, qapp)
    assert _sync_states(ws) == ["source", "peer", "peer"]

    ws._broadcast_input.setFocus()  # keyboard left the panes
    qapp.processEvents()

    assert not any(_sync_states(ws))


def test_scoped_tint_updates_when_tags_change_while_armed(make_ws, qapp):
    """Untagging a pane mid-sync must unlight it in the same instant its
    keys stop flowing -- the tint may never disagree with delivery."""
    ws = make_ws(3)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])
    ws._apply_sync(True, "a", None)
    _focus_pane(ws, 0, qapp)
    assert _sync_states(ws) == ["source", "peer", ""]

    ws.tiles[1].toggle_tag("a")  # pane 1 leaves the group under armed sync

    assert _sync_states(ws) == ["source", "", ""]
    ws.tiles[0].terminal.inputSent.emit("x", False)
    assert ws.tiles[1].terminal.injected == []  # delivery agrees with the tint


def test_sync_tint_clears_when_peer_dies_mid_sync(make_ws, qapp):
    """A peer's process exiting (finished) must unlight it: the tint may
    not advertise a mirror target the fan-out just excluded."""
    ws = make_ws(2)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])
    ws._sync_btn.setChecked(True)
    _focus_pane(ws, 0, qapp)
    assert _sync_states(ws) == ["source", "peer"]

    dead = ws.tiles[1].terminal
    dead.is_dead = lambda: True   # the real widget flips _dead BEFORE emitting
    dead.finished.emit()

    assert _sync_states(ws) == ["source", ""]
    ws.tiles[0].terminal.inputSent.emit("x", False)
    assert dead.injected == []


def test_sync_toggle_flash_names_the_audience(make_ws, qapp):
    ws = make_ws(3)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])
    _focus_pane(ws, 0, qapp)

    ws._sync_btn.setChecked(True)

    assert ws._count_label.text() == "sync input on · all (2 panes)"

    ws._pick_sync_scope("a")  # the menu's narrower choice names its group
    assert ws._count_label.text() == "sync input on · @a (1 pane)"

    ws.tiles[0].set_tags([])  # zero tags again: today's exact string
    ws.tiles[1].set_tags([])
    ws._sync_btn.setChecked(False)
    ws._sync_btn.setChecked(True)
    assert ws._count_label.text() == "sync input on"


# --- sync input scoping (the right-click tag menu) --------------------------------


def test_right_click_menu_lists_every_known_tag(make_ws, qapp):
    """The menu's vocabulary is the whole workspace's, not the focused
    pane's: any group is one right-click away from any pane."""
    ws = make_ws(4)
    for tile, t in zip(ws.tiles, "abcd"):
        tile.set_tags([t])
    ws.tiles[1].set_tags(["b", "extra"])
    _focus_pane(ws, 1, qapp)

    menu = ws._build_sync_menu(ws.tiles[1])
    texts = [ac.text() for ac in menu.actions()]
    for tag in ("a", "b", "c", "d", "extra"):
        assert tag in texts
    assert "All windows" in texts
    assert "Turn mirroring off" not in texts  # unarmed: no off entry


def test_right_click_menu_without_tags_offers_all_and_a_hint(make_ws, qapp):
    ws = make_ws(2)

    menu = ws._build_sync_menu(None)
    acts = {ac.text(): ac for ac in menu.actions()}

    assert acts["All windows"].isEnabled()
    hint = next(ac for ac in menu.actions() if ac.text().startswith("No tags"))
    assert not hint.isEnabled()


def test_left_click_arms_all_windows_even_for_multi_tag_panes(
        make_ws, qapp, monkeypatch):
    """No more picker detour: a pane in several groups arms the everyone
    audience on the first click, exactly like every other pane."""
    ws = make_ws(3)
    ws.tiles[1].set_tags(["a", "b"])
    _focus_pane(ws, 1, qapp)
    opened = []
    monkeypatch.setattr(ws, "_on_sync_menu", lambda pos: opened.append(pos))

    ws._sync_btn.setChecked(True)  # the click's toggled path
    assert ws._sync_btn.isChecked()
    assert not opened              # no menu jumped out
    assert ws._sync_keys and ws._sync_scope is None


def test_menu_picking_a_tag_drives_only_that_group(make_ws, qapp):
    """The request's example, verbatim: a {1,2} pane can drive group 1 OR
    group 2 -- the unpicked group and everyone else stay untouched."""
    ws = make_ws(4)
    ws.tiles[0].set_tags(["1"])
    ws.tiles[1].set_tags(["1", "2"])   # the multi-tag driver
    ws.tiles[2].set_tags(["2"])
    _focus_pane(ws, 1, qapp)

    menu = ws._build_sync_menu(ws.tiles[1])
    next(ac for ac in menu.actions() if ac.text() == "2").toggle()

    assert ws._sync_btn.isChecked()
    assert ws._sync_scope == "2"
    assert ws._sync_btn.text() == "⇉  Sync · @2"
    assert ws._count_label.text() == "sync input on · @2 (1 pane)"

    ws.tiles[1].terminal.inputSent.emit("x", False)
    assert ws.tiles[2].terminal.injected == [("x", False)]  # group 2 got it
    assert ws.tiles[0].terminal.injected == []              # group 1 did not
    assert ws.tiles[3].terminal.injected == []              # untagged neither


def test_menu_all_windows_entry_arms_the_everyone_audience(make_ws, qapp):
    """The menu's own way back to the left-click audience (and out of a
    scoped arm) without an off/on blip."""
    ws = make_ws(3)
    ws.tiles[0].set_tags(["1"])
    ws.tiles[1].set_tags(["1"])
    ws._apply_sync(True, "1", None)

    menu = ws._build_sync_menu(ws.tiles[0])
    acts = {ac.text(): ac for ac in menu.actions()}
    assert acts["1"].isChecked() and not acts["All windows"].isChecked()
    acts["All windows"].toggle()

    assert ws._sync_btn.isChecked()
    assert ws._sync_scope is None
    assert ws._sync_btn.text() == "⇉  Sync · all"

    ws.tiles[0].terminal.inputSent.emit("y", False)
    assert ws.tiles[1].terminal.injected == [("y", False)]
    assert ws.tiles[2].terminal.injected == [("y", False)]  # untagged too


def test_scoped_sync_switches_group_in_one_click(make_ws, qapp):
    ws = make_ws(4)
    ws.tiles[0].set_tags(["1"])
    ws.tiles[1].set_tags(["1", "2"])
    ws.tiles[2].set_tags(["2"])
    _focus_pane(ws, 1, qapp)
    ws._apply_sync(True, "2", None)

    menu = ws._build_sync_menu(ws.tiles[1])
    acts = {ac.text(): ac for ac in menu.actions()}
    assert "Turn mirroring off" in acts          # armed: the off way back
    assert acts["2"].isChecked() and not acts["1"].isChecked()
    acts["1"].toggle()

    assert ws._sync_btn.isChecked()              # no off/on blip
    assert ws._sync_scope == "1"
    assert ws._sync_btn.text() == "⇉  Sync · @1"

    ws.tiles[1].terminal.inputSent.emit("y", False)
    assert ws.tiles[0].terminal.injected == [("y", False)]
    assert ws.tiles[2].terminal.injected == []   # group 2 no longer driven


def test_scoped_sync_off_entry_disarms(make_ws, qapp):
    ws = make_ws(3)
    ws.tiles[0].set_tags(["1", "2"])
    _focus_pane(ws, 0, qapp)
    ws._apply_sync(True, "2", None)

    menu = ws._build_sync_menu(ws.tiles[0])
    next(ac for ac in menu.actions()
         if ac.text() == "Turn mirroring off").trigger()

    assert not ws._sync_btn.isChecked()
    assert ws._sync_scope is None
    assert ws._sync_btn.text() == "⇉  Sync input"
    assert ws._count_label.text() == "sync input off"


def test_armed_zero_tag_workspace_keeps_the_plain_button_label(make_ws, qapp):
    """The zero-tag pixel-parity invariant, button-label edition: armed with
    no tags anywhere the button must read exactly "⇉  Sync input" -- and the
    "Sync · all" era must end the moment the last tag leaves, by untag OR by
    closing the pane (a close fires no tagsChanged; _rebuild covers it)."""
    ws = make_ws(2)
    ws._sync_btn.setChecked(True)
    assert ws._sync_btn.text() == "⇉  Sync input"   # armed, zero tags: plain

    ws.tiles[0].set_tags(["a"])                     # a tag arrives while armed
    assert ws._sync_btn.text() == "⇉  Sync · all"

    ws.tiles[0].set_tags([])                        # ...and leaves by untag
    assert ws._sync_btn.text() == "⇉  Sync input"

    ws.tiles[0].set_tags(["a"])
    ws._on_close_tile(0)                            # ...or by closing the pane
    assert ws._sync_btn.text() == "⇉  Sync input"


def test_menu_reclick_of_a_checked_entry_is_a_dismissal(make_ws, qapp):
    """Unchecking the already-checked audience (the user re-clicking the
    checkmark) must be a no-look dismissal -- never an arm change."""
    ws = make_ws(2)
    ws.tiles[0].set_tags(["1"])
    ws._apply_sync(True, "1", None)

    menu = ws._build_sync_menu(ws.tiles[0])
    next(ac for ac in menu.actions() if ac.text() == "1").toggle()
    assert ws._sync_keys and ws._sync_scope == "1"  # scoped arm untouched

    ws._apply_sync(True, None, None)                # same contract for "all"
    menu = ws._build_sync_menu(ws.tiles[0])
    next(ac for ac in menu.actions()
         if ac.text() == "All windows").toggle()
    assert ws._sync_keys and ws._sync_scope is None


def test_scoped_sync_label_elides_long_tags(make_ws, qapp):
    """A 16-char tag must not grow the button's (and so the toolbar's)
    minimum: the tag elides to a fixed pixel budget, short tags verbatim."""
    ws = make_ws(2)
    ws.tiles[0].set_tags(["short", "averyverylongtag"])
    _focus_pane(ws, 0, qapp)

    ws._pick_sync_scope("short", ws.tiles[0])
    assert ws._sync_btn.text() == "⇉  Sync · @short"

    ws._pick_sync_scope("averyverylongtag", ws.tiles[0])
    text = ws._sync_btn.text()
    assert text.startswith("⇉  Sync · @aver")  # elide keeps a readable prefix
    assert text.endswith("…") and len(text) < len("⇉  Sync · @averyverylongtag")


def test_scoped_sync_source_outside_the_group_stays_local(make_ws, qapp):
    """Armed for one group, typing on a pane OUTSIDE it mirrors nowhere:
    the mirror is a property of the group, not of the keyboard. The tint
    must make exactly the same promise as the fan-out."""
    ws = make_ws(4)
    ws.tiles[0].set_tags(["1"])
    ws.tiles[1].set_tags(["1", "2"])
    ws.tiles[2].set_tags(["2"])
    _focus_pane(ws, 1, qapp)
    ws._apply_sync(True, "2", None)

    ws.tiles[0].terminal.inputSent.emit("x", False)  # tag {1}: not in group 2
    assert all(t.terminal.injected == [] for t in ws.tiles)

    _focus_pane(ws, 0, qapp)
    assert not any(_sync_states(ws))  # the tint promises nothing either
    _focus_pane(ws, 1, qapp)
    assert _sync_states(ws) == ["", "source", "peer", ""]


def test_scoped_arm_skips_dead_and_untagged_targets(make_ws):
    ws = make_ws(4)
    ws.tiles[0].set_tags(["1", "2"])
    ws.tiles[1].set_tags(["2"])
    ws.tiles[2].set_tags(["2"])
    ws.tiles[2].terminal.is_dead = lambda: True
    ws._apply_sync(True, "2", None)

    ws.tiles[0].terminal.inputSent.emit("k", False)
    assert ws.tiles[1].terminal.injected == [("k", False)]
    assert ws.tiles[2].terminal.injected == []  # dead target
    assert ws.tiles[3].terminal.injected == []  # untagged


def test_scope_tag_dropped_everywhere_mirrors_nowhere(make_ws, qapp):
    """Every holder of the armed tag drops it: the group is gone. The arm
    stays (the user set it) but nothing receives -- honestly and visibly,
    with no silent semantic fallback."""
    ws = make_ws(3)
    ws.tiles[0].set_tags(["1", "2"])
    ws.tiles[1].set_tags(["2"])
    ws._apply_sync(True, "2", None)

    ws.tiles[1].set_tags([])
    ws.tiles[0].set_tags(["1"])  # the last holder leaves group 2
    assert "2" not in ws._known_tags()

    ws.tiles[0].terminal.inputSent.emit("x", False)
    assert all(t.terminal.injected == [] for t in ws.tiles)
    _focus_pane(ws, 0, qapp)
    assert not any(_sync_states(ws))


# --- cross-window linking (two workspaces + two real buses, one process) --------

import uuid  # noqa: E402
from pathlib import Path  # noqa: E402

from megacode import sync_protocol as link_proto  # noqa: E402
from megacode.sync_bus import SyncBus  # noqa: E402


def _spin(cond, ms=3000):
    app = QApplication.instance()
    deadline = __import__("time").monotonic() + ms / 1000.0
    while __import__("time").monotonic() < deadline:
        if cond():
            return True
        app.processEvents()
        __import__("time").sleep(0.002)
    return cond()


@pytest.fixture()
def linked_pair(make_ws, qapp, tmp_path, monkeypatch):
    """Two workspaces, each on its own REAL SyncBus, linked over a pipe."""
    state = tmp_path / f"link{uuid.uuid4().hex[:8]}"
    state.mkdir()
    suffix = f"-wt{uuid.uuid4().hex[:8]}"
    buses = []

    def _make(n):
        bus = SyncBus(state, name_suffix=suffix, jitter_fn=lambda: 0,
                      hello_timeout_ms=500, ping_ms=40, backoff=[5, 10, 20, 40])
        buses.append(bus)
        ws = make_ws(n)
        ws.set_link(bus)
        return ws

    a = _make(2)
    b = _make(2)
    assert _spin(lambda: a._link.is_alive() and b._link.is_alive())
    yield a, b
    for bus in buses:
        try:
            bus.stop()
        except Exception:  # noqa: BLE001
            pass


def test_peerless_real_bus_keeps_workspace_byte_identical(make_ws, qapp, tmp_path):
    """A started-but-alone bus must not leak any UI: the chip stays hidden,
    the hints keep the base constants, typing stays local."""
    ws = make_ws(2)
    bus = SyncBus(tmp_path, name_suffix=f"-wt{uuid.uuid4().hex[:8]}",
                  jitter_fn=lambda: 0)
    ws.set_link(bus)
    assert _spin(lambda: bus._role == "hub")

    assert not ws._link_btn.isVisible()
    assert ws._broadcast_input.placeholderText() == wsm._BROADCAST_PLACEHOLDER_BASE
    assert ws._sync_btn.toolTip() == wsm._SYNC_TIP_BASE

    ws._sync_btn.setChecked(True)
    ws.tiles[0].terminal.inputSent.emit("d", False)
    assert ws.tiles[1].terminal.injected == [("d", False)]  # local mirror intact
    bus.stop()


def test_remote_input_reaches_all_remote_panes_when_unscoped(linked_pair):
    """The left-click arm crosses the wire as "all": every live pane in the
    other window receives, tag groups be damned."""
    a, b = linked_pair
    a.tiles[0].set_tags(["fe"])
    b.tiles[0].set_tags(["fe"])
    b.tiles[1].set_tags(["be"])
    assert _spin(lambda: "fe" in a._link.registry().known_tags()
                  and "fe" in b._link.registry().known_tags())

    a._sync_btn.setChecked(True)  # ONE global state replicates to b
    assert _spin(lambda: b._sync_btn.isChecked())

    a.tiles[0].terminal.inputSent.emit("x", False)
    assert _spin(lambda: b.tiles[0].terminal.injected == [("x", False)]
                 and b.tiles[1].terminal.injected == [("x", False)])
    assert a.tiles[1].terminal.injected == [("x", False)]  # local all-arm too


def test_remote_input_scopes_to_the_tag_over_the_wire(linked_pair):
    a, b = linked_pair
    a.tiles[0].set_tags(["fe"])
    b.tiles[0].set_tags(["fe"])
    b.tiles[1].set_tags(["be"])
    assert _spin(lambda: "fe" in a._link.registry().known_tags()
                  and "fe" in b._link.registry().known_tags())

    a._pick_sync_scope("fe")
    assert _spin(lambda: b._sync_btn.isChecked() and b._sync_scope == "fe")

    a.tiles[0].terminal.inputSent.emit("x", False)
    assert _spin(lambda: b.tiles[0].terminal.injected == [("x", False)])
    assert b.tiles[1].terminal.injected == []       # other tag: never crossed
    assert a.tiles[1].terminal.injected == []       # untagged local: not in @fe


def test_injected_remote_input_never_republishes(linked_pair):
    """The echo-loop pin, end to end: remote delivery must not re-emit
    inputSent, so nothing bounces back."""
    a, b = linked_pair
    a.tiles[0].set_tags(["fe"])
    b.tiles[0].set_tags(["fe"])
    assert _spin(lambda: "fe" in b._link.registry().known_tags())
    a._sync_btn.setChecked(True)
    assert _spin(lambda: b._sync_btn.isChecked())

    a.tiles[0].terminal.inputSent.emit("x", False)
    assert _spin(lambda: b.tiles[0].terminal.injected == [("x", False)])

    # inject_input (the fake mirrors the real contract) never re-emits;
    # b's panes got exactly ONE delivery each and nothing travelled back
    assert a.tiles[0].terminal.injected == []
    assert all(t.injected == [("x", False)] for t in
               (b.tiles[0].terminal, b.tiles[1].terminal))


def test_sync_toggle_replicates_with_set_in_flash(linked_pair):
    a, b = linked_pair
    a._sync_btn.setChecked(True)
    assert _spin(lambda: b._sync_btn.isChecked())
    assert "set in W1" in b._count_label.text()
    # one click == one state: turning it off propagates too
    a._sync_btn.setChecked(False)
    assert _spin(lambda: not b._sync_btn.isChecked())


def test_all_arm_flash_splits_the_remote_audience(linked_pair):
    """The left-click flash names the everyone audience on both sides of
    the pipe (alive_pane_count drives the remote half)."""
    a, b = linked_pair
    a.tiles[0].set_tags(["fe"])
    b.tiles[0].set_tags(["fe"])
    assert _spin(lambda: "fe" in a._link.registry().known_tags())
    # b was shown after a: offscreen keeps the LAST window active, and
    # QApplication.focusWidget() follows the active window -- so put a
    # back on top before focusing its pane
    a.raise_()
    a.activateWindow()
    _focus_pane(a, 0, QApplication.instance())

    a._sync_btn.setChecked(True)

    # a holds 2 panes (the focused one is the source), b holds 2 alive
    assert a._count_label.text() == \
        "sync input on · all windows (1 here · 2 in 1 window)"


def test_sync_menu_includes_remote_only_tags(linked_pair):
    """The right-click vocabulary spans windows: a tag held only by the
    other window's panes is still offered for scoping."""
    a, b = linked_pair
    b.tiles[0].set_tags(["far"])
    assert _spin(lambda: "far" in a._known_tags())

    texts = [ac.text() for ac in a._build_sync_menu(None).actions()]
    assert "far" in texts
    assert "All windows" in texts


def test_unscoped_rearm_over_the_wire_clears_a_remote_scope(linked_pair):
    """The menu's "All windows" re-arm (no off/on blip) must clear a scope
    another window adopted -- ONE global state, everywhere, at once."""
    a, b = linked_pair
    a.tiles[0].set_tags(["fe"])
    b.tiles[0].set_tags(["fe"])
    b.tiles[1].set_tags(["be"])
    assert _spin(lambda: {"fe", "be"} <= set(a._known_tags()))

    a._pick_sync_scope("fe")
    assert _spin(lambda: b._sync_scope == "fe")

    a._arm_sync(True, None)  # the "All windows" entry's path
    assert _spin(lambda: b._sync_btn.isChecked() and b._sync_scope is None)
    assert b._sync_btn.text() == "⇉  Sync · all"

    a.tiles[0].terminal.inputSent.emit("z", False)
    assert _spin(lambda: b.tiles[1].terminal.injected == [("z", False)])  # @be too


def test_scoped_run_all_spans_windows_and_flashes_split(linked_pair):
    a, b = linked_pair
    a.tiles[0].set_tags(["fe"])
    b.tiles[1].set_tags(["fe"])
    # wait for the CROSS-window vocabularies specifically: the local tag
    # satisfies _known_tags instantly and says nothing about the wire
    assert _spin(lambda: "fe" in a._link.registry().known_tags()
                 and "fe" in b._link.registry().known_tags())

    a._broadcast_input.setText("@fe git pull")
    a._broadcast()
    assert _spin(lambda: b.tiles[1].terminal.commands == ["git pull\r"])
    assert a.tiles[0].terminal.commands == ["git pull\r"]  # local side ran too
    assert "1 pane here · 1 in 1 window · @fe" in a._count_label.text()
    assert _spin(lambda: "(from W1)" in b._count_label.text())


def test_plain_run_all_and_run_pasted_never_hit_the_wire(linked_pair):
    a, b = linked_pair
    a.tiles[0].set_tags(["fe"])
    b.tiles[0].set_tags(["fe"])
    assert _spin(lambda: "fe" in a._known_tags())

    a._broadcast_input.setText("dir")
    a._broadcast()
    assert a.tiles[0].terminal.commands == ["dir\r"]
    assert _spin(lambda: True)
    assert b.tiles[0].terminal.commands == []          # never crossed

    b.tiles[0].terminal.set_pending(True)
    a._execute_pasted()
    assert b.tiles[0].terminal.has_pending_input()      # still waiting


def test_remote_only_tag_scopes_run_all(linked_pair):
    """A tag that exists ONLY in the other window still scopes the run --
    and must not trip the 'no live pane' warning."""
    a, b = linked_pair
    b.tiles[0].set_tags(["remote1"])
    assert _spin(lambda: "remote1" in a._known_tags())

    a._broadcast_input.setText("@remote1 cmd")
    a._broadcast()
    assert _spin(lambda: b.tiles[0].terminal.commands == ["cmd\r"])
    assert all(t.terminal.commands == [] for t in a.tiles)
    assert "no live pane" not in a._count_label.text()


def test_remote_tags_join_vocabulary_and_menu_then_leave(linked_pair):
    a, b = linked_pair
    b.tiles[0].set_tags(["shared"])
    assert _spin(lambda: "shared" in a._known_tags())
    menu = a._build_tile_menu(a.tiles[0])
    assert any(ac.text() == "shared" for ac in menu.actions())

    b.tiles[0].set_tags([])
    assert _spin(lambda: "shared" not in a._known_tags())


def test_chip_and_title_follow_the_link(linked_pair):
    a, b = linked_pair
    assert _spin(lambda: a._link_btn.isVisible() and b._link_btn.isVisible())
    assert a._link_btn.text() == "⛓  2 windows"

    titles = []
    a.linkedInfoChanged.connect(titles.append)
    b._link.stop()
    assert _spin(lambda: not a._link_btn.isVisible())  # back to solo: hidden
    a._link.start()
    assert _spin(lambda: a._link.is_alive())
    assert not a._link_btn.isVisible()  # healthy solo stays hidden
    a._link.stop()


def test_kill_switch_isolates_both_directions(linked_pair):
    a, b = linked_pair
    assert _spin(lambda: a._link_btn.isVisible())

    b._link_btn.setChecked(False)  # the session kill switch
    # the leaver dies; the survivor (hub) stays alive but alone
    assert _spin(lambda: not b._link.is_alive()
                 and a._link.registry().other_windows() == 0)
    assert _spin(lambda: not a._link_btn.isVisible())
    assert b._count_label.text() == "window linking off"

    # re-linking is one click
    b._link_btn.setChecked(True)
    assert _spin(lambda: a._link.is_alive() and b._link.is_alive())


def test_relaunch_publishes_empty_digest(linked_pair):
    a, b = linked_pair
    a.tiles[0].set_tags(["fe"])
    assert _spin(lambda: "fe" in b._known_tags())

    a.start(2, "fake", os.getcwd(), font_size=10, label="term")
    assert _spin(lambda: "fe" not in b._known_tags())  # empty digest traveled


def test_relink_revives_the_cross_window_vocabulary(linked_pair):
    """kill-switch -> re-link must re-announce the panes (the hello carries
    the cached digest; the workspace publishes on tile events only, so a
    stop() that threw the table away would leave this window pane-less in
    its peers' registries -- sync would silently stop crossing)."""
    a, b = linked_pair
    b.tiles[0].set_tags(["fe"])
    assert _spin(lambda: "fe" in a._link.registry().known_tags())

    b._link_btn.setChecked(False)  # session kill switch
    assert _spin(lambda: not b._link.is_alive()
                 and a._link.registry().other_windows() == 0)

    b._link_btn.setChecked(True)   # one click back
    assert _spin(lambda: b._link.is_alive())
    assert _spin(lambda: "fe" in a._link.registry().known_tags())


def test_tag_cap_matches_the_wire_budget(make_ws):
    """The wire digest carries at most MAX_TAGS_PER_PANE tags per pane; the
    tile model caps at the same budget so in-window mirroring and the
    cross-window digest can never disagree about a pane's groups."""
    from megacode import sync_protocol as lp

    ws = make_ws(1)
    tile = ws.tiles[0]
    tile.set_tags([f"t{i}" for i in range(lp.MAX_TAGS_PER_PANE + 4)])
    assert len(tile.tags()) == lp.MAX_TAGS_PER_PANE
    assert tile.tags() == [f"t{i}" for i in range(lp.MAX_TAGS_PER_PANE)]
    # the digest row survives (build_pane_entry no longer sees an over-budget pane)
    assert ws._pane_digest()[0]["tags"] == tile.tags()


def test_remote_pulse_marks_receiving_panes(linked_pair, qapp):
    a, b = linked_pair
    a.tiles[0].set_tags(["fe"])
    b.tiles[0].set_tags(["fe"])
    assert _spin(lambda: "fe" in b._known_tags())
    a._sync_btn.setChecked(True)
    assert _spin(lambda: b._sync_btn.isChecked())

    # Production shape: the user types in the SENDING window, so the
    # receiver's headers are tint-free and the pulse is visible. (Both
    # workspaces share one QApplication here, and offscreen keeps focus in
    # the last-shown window: without activateWindow() focus idles on b's
    # pane, which lights it as a sync SOURCE, and the pulse correctly
    # yields to that tint.)
    a.window().activateWindow()
    _focus_pane(a, 0, qapp)
    assert a.tiles[0].header.property("sync") == "source"
    assert not any(_sync_states(b))

    a.tiles[0].terminal.inputSent.emit("x", False)
    assert _spin(lambda: b.tiles[0].header.property("remotePulse") == "true")
    assert _spin(lambda: b.tiles[0].header.property("remotePulse") != "true",
                 ms=2500)  # the pulse decays


def test_oversize_paste_skips_the_wire_with_flash(linked_pair):
    a, b = linked_pair
    a.tiles[0].set_tags(["fe"])
    b.tiles[0].set_tags(["fe"])
    assert _spin(lambda: "fe" in b._known_tags())
    a._sync_btn.setChecked(True)
    assert _spin(lambda: b._sync_btn.isChecked())

    a.tiles[0].terminal.inputSent.emit(
        "x" * (link_proto.MAX_SEQ + 1), True)
    assert a._count_label.text() == "paste too large to mirror across windows"
    assert _spin(lambda: True)


def test_scoped_sync_replicates_and_re_scopes_across_windows(linked_pair):
    """The picked group is ONE global truth: it replicates with the toggle,
    drives only its group's panes in every window, and a live re-scope
    switches the group everywhere without an off/on blip."""
    a, b = linked_pair
    a.tiles[0].set_tags(["fe", "be"])
    b.tiles[0].set_tags(["fe"])
    b.tiles[1].set_tags(["be"])
    assert _spin(lambda: {"fe", "be"} <= set(a._known_tags()))

    a._pick_sync_scope("fe")
    assert _spin(lambda: b._sync_btn.isChecked() and b._sync_scope == "fe")
    assert "set in W1" in b._count_label.text()
    assert b._sync_btn.text() == "⇉  Sync · @fe"

    a.tiles[0].terminal.inputSent.emit("x", False)
    assert _spin(lambda: b.tiles[0].terminal.injected == [("x", False)])
    assert b.tiles[1].terminal.injected == []  # @be pane: the other group
    assert a.tiles[1].terminal.injected == []  # untagged local pane

    a._pick_sync_scope("be")
    assert _spin(lambda: b._sync_scope == "be")
    a.tiles[0].terminal.inputSent.emit("y", False)
    assert _spin(lambda: b.tiles[1].terminal.injected == [("y", False)])
    assert b.tiles[0].terminal.injected == [("x", False)]  # fe era, no more


# --- theme menu (color-scheme switching) --------------------------------------

def test_theme_menu_lists_every_scheme_and_emits_its_name(make_ws):
    ws = make_ws(1)
    from megacode import themes
    assert set(ws._theme_actions) == set(themes.SCHEMES)
    seen = []
    ws.themeChanged.connect(seen.append)
    ws._theme_actions["marine-night"].trigger()
    assert seen == ["marine-night"]


def test_apply_theme_refreshes_terminal_panes(make_ws, monkeypatch):
    """Existing panes re-resolve their colors when the scheme changes; chat
    tiles are QSS-only and must be left alone by the pane pass."""
    ws = make_ws(2)
    refreshed = []
    monkeypatch.setattr(
        wsm.TerminalWidget, "refresh_theme",
        lambda self: refreshed.append(self), raising=False)
    ws.apply_theme()
    assert refreshed == [t.terminal for t in ws.tiles]
