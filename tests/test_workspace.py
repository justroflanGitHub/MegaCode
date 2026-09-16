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
from PySide6.QtWidgets import QApplication, QSplitter, QWidget  # noqa: E402

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


def test_sync_mirrors_only_within_same_tag(make_ws):
    ws = make_ws(3)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])
    ws.tiles[2].set_tags(["b"])
    ws._sync_btn.setChecked(True)

    ws.tiles[0].terminal.inputSent.emit("d", False)

    assert ws.tiles[1].terminal.injected == [("d", False)]
    assert ws.tiles[2].terminal.injected == []


def test_untagged_source_mirrors_only_untagged_panes(make_ws):
    ws = make_ws(4)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])

    ws._sync_btn.setChecked(True)
    ws.tiles[2].terminal.inputSent.emit("d", False)

    assert ws.tiles[3].terminal.injected == [("d", False)]
    assert ws.tiles[0].terminal.injected == []
    assert ws.tiles[1].terminal.injected == []


def test_tagged_source_never_reaches_untagged(make_ws):
    ws = make_ws(3)
    ws.tiles[0].set_tags(["a"])

    ws._sync_btn.setChecked(True)
    ws.tiles[0].terminal.inputSent.emit("d", False)

    assert ws.tiles[1].terminal.injected == []
    assert ws.tiles[2].terminal.injected == []


def test_multi_tag_pane_joins_both_domains(make_ws):
    ws = make_ws(3)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a", "b"])
    ws.tiles[2].set_tags(["b"])
    ws._sync_btn.setChecked(True)

    ws.tiles[1].terminal.inputSent.emit("x", False)  # drives the union
    assert ws.tiles[0].terminal.injected == [("x", False)]
    assert ws.tiles[2].terminal.injected == [("x", False)]

    ws.tiles[0].terminal.inputSent.emit("y", False)  # group a reaches the bridge
    assert ws.tiles[1].terminal.injected == [("y", False)]
    assert ws.tiles[2].terminal.injected == [("x", False)]  # b is not in a


def test_sync_domain_follows_widget_across_swap(make_ws):
    """Tags ride the tile, the mirror wiring rides the widget: after a
    drag-swap the tagged session still mirrors only to its tagged peer."""
    ws = make_ws(3)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])
    tagged, peer = ws.tiles[0].terminal, ws.tiles[1].terminal
    ws._sync_btn.setChecked(True)

    ws._on_swap(0, 2)  # tagged session moves to index 2
    assert ws.tiles[2].terminal is tagged

    tagged.inputSent.emit("x", False)

    assert peer.injected == [("x", False)]
    assert ws.tiles[0].terminal.injected == []  # untagged never crosses
    assert tagged.injected == []                # no self-echo


def test_dead_tagged_pane_not_a_sync_target(make_ws):
    ws = make_ws(2)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])
    ws.tiles[1].terminal.is_dead = lambda: True
    ws._sync_btn.setChecked(True)

    ws.tiles[0].terminal.inputSent.emit("x", False)

    assert ws.tiles[1].terminal.injected == []


def test_new_pane_starts_untagged_and_safe(make_ws):
    """A fresh pane joins the untagged cohort: its keys can never leak into
    existing tagged groups until the user tags it."""
    ws = make_ws(2)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])
    ws._sync_btn.setChecked(True)
    new = ws._new_tile("fake", "term")

    new.terminal.inputSent.emit("k", False)
    assert all(t.terminal.injected == [] for t in ws.tiles)

    ws.tiles[0].terminal.inputSent.emit("d", False)
    assert ws.tiles[1].terminal.injected == [("d", False)]
    assert new.terminal.injected == []


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


def test_sync_tint_marks_domain_only_when_tags_exist(make_ws, qapp):
    ws = make_ws(3)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])
    ws.tiles[2].set_tags(["b"])
    ws._sync_btn.setChecked(True)
    _focus_pane(ws, 0, qapp)

    assert _sync_states(ws) == ["source", "peer", ""]

    _focus_pane(ws, 2, qapp)  # the b pane drives alone: it is its own group
    assert _sync_states(ws) == ["", "", "source"]

    ws._sync_btn.setChecked(False)  # sync off: everything clears
    assert _sync_states(ws) == ["", "", ""]


def test_zero_tags_never_tint(make_ws, qapp):
    """The no-tag contract: no header may light without tags, ever."""
    ws = make_ws(3)
    ws._sync_btn.setChecked(True)
    _focus_pane(ws, 0, qapp)

    assert not any(_sync_states(ws))


def test_lone_untagged_pane_shows_source_tint_only(make_ws, qapp):
    """A lone untagged pane drives nobody, but its own source tint still
    lights: 'you drive, and you are alone' beats looking disarmed."""
    ws = make_ws(3)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])
    ws._sync_btn.setChecked(True)
    _focus_pane(ws, 2, qapp)

    assert _sync_states(ws) == ["", "", "source"]


def test_sync_tint_clears_on_focus_loss(make_ws, qapp):
    ws = make_ws(3)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])
    ws._sync_btn.setChecked(True)
    _focus_pane(ws, 0, qapp)
    assert _sync_states(ws) == ["source", "peer", ""]

    ws._broadcast_input.setFocus()  # keyboard left the panes
    qapp.processEvents()

    assert not any(_sync_states(ws))


def test_sync_tint_updates_when_tags_change_while_armed(make_ws, qapp):
    """Untagging a pane mid-sync must unlight it in the same instant its
    keys stop flowing -- the tint may never disagree with delivery."""
    ws = make_ws(3)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])
    ws._sync_btn.setChecked(True)
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


def test_sync_toggle_flash_names_the_focused_group(make_ws, qapp):
    ws = make_ws(3)
    ws.tiles[0].set_tags(["a"])
    ws.tiles[1].set_tags(["a"])
    _focus_pane(ws, 0, qapp)

    ws._sync_btn.setChecked(True)

    assert ws._count_label.text() == "sync input on · @a (1 pane)"

    _focus_pane(ws, 2, qapp)  # untagged focused: the group has a name too
    ws._sync_btn.setChecked(False)
    ws._sync_btn.setChecked(True)
    assert ws._count_label.text() == "sync input on · untagged (0 panes)"

    ws.tiles[0].set_tags([])  # zero tags again: today's exact string
    ws.tiles[1].set_tags([])
    ws._sync_btn.setChecked(False)
    ws._sync_btn.setChecked(True)
    assert ws._count_label.text() == "sync input on"
