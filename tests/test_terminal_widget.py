"""Offscreen tests for the terminal widget's scrollback + selection math.

These exercise the new HistoryScreen-backed viewport (``_build_view``) and the
viewport-relative selection resolution (``_selection_text``) without spawning a
real process: ``Pty`` is replaced by a no-op fake, and output is fed directly.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from typing import List  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import (
    QColor, QGuiApplication, QKeyEvent, QMouseEvent,  # noqa: E402
)
from PySide6.QtWidgets import QApplication  # noqa: E402

from megacode import terminal_widget as tw  # noqa: E402


class _FakePty:
    """A stand-in for ``Pty`` that never spawns a child."""

    # ConPTY-era semantics (LNM on) keep the bare-LF expectations below
    alive = True  # flip to False to simulate the child exiting
    LNM_WORKAROUND = True

    def __init__(self, *_a, **_k) -> None:
        pass

    def start(self, _on_output) -> None:
        return None

    def write(self, _text: str) -> None:
        return None

    def resize(self, _cols: int, _rows: int) -> None:
        return None

    def is_alive(self) -> bool:
        return self.alive

    def stop(self) -> None:
        return None


@pytest.fixture()
def term(qapp):
    """A TerminalWidget on a fixed 80x24 grid, backed by a fake PTY."""
    saved = tw.Pty
    tw.Pty = _FakePty  # type: ignore[assignment]
    try:
        w = tw.TerminalWidget("fake", font_size=10)
    finally:
        tw.Pty = saved  # type: ignore[assignment]
    # Force deterministic cell metrics (offscreen font metrics vary by host).
    w._cell_w, w._cell_h = 10, 20
    w.resize(80 * 10, 24 * 20)  # -> 80 cols x 24 rows
    # Show the widget so subsequent resizes actually deliver resizeEvent under
    # the offscreen QPA (an unshown widget only fires it for the first resize).
    w.show()
    qapp.processEvents()
    return w


@pytest.fixture()
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _rows_text(rows: List[list]) -> List[str]:
    return ["".join(ch.data for ch in row) for row in rows]


def test_offset_zero_shows_live_buffer(term):
    term._on_output("hello\r\n")
    rows, cols = term._build_view()
    assert cols == 80
    text = _rows_text(rows)
    # the just-printed line is at the top of an otherwise-empty buffer
    assert text[0].rstrip() == "hello"


def test_scrolled_back_reveals_history(term):
    for i in range(60):
        term._on_output(f"line{i:02d}\r\n")
    hist_len = term._history_len()
    assert hist_len > 0

    # scrolled all the way up -> the oldest retained line is on top
    term._scroll_offset = hist_len
    rows, _cols = term._build_view()
    top_text = _rows_text(rows)[0].rstrip()
    # the very first emitted line should be visible at the top
    assert top_text.startswith("line00")


def test_scroll_offset_clamps_to_history(term):
    for i in range(40):
        term._on_output(f"line{i:02d}\r\n")
    hist_len = term._history_len()
    # try to scroll way past the top
    term._scroll_offset = hist_len + 9999
    rows, _cols = term._build_view()
    # the top row is still the oldest history line (no crash, no overshoot)
    assert _rows_text(rows)[0].rstrip().startswith("line00")


def test_no_history_no_crash(term):
    # empty terminal, offset 0: build_view must return full blank grid
    rows, cols = term._build_view()
    assert len(rows) == 24
    assert all(len(r) == cols for r in rows)


# --- ConPTY stream semantics (nano/TUI fixes) ---------------------------------


def test_conpty_lf_returns_to_column_zero(term):
    """ConPTY emits bare LFs with console semantics: LF must reset the column.

    nano's real pattern (captured from ConPTY): the title line ends with
    ``ESC[79G`` + ``\\n``, then ``ESC[23d`` positions the help row -- which
    only lands at column 0 if the LF behaved as CR+LF. pyte's VT-strict LF
    keeps the column, shearing the whole frame (half the screen stale).
    """
    term._on_output("\x1b[H\x1b[2J")
    term._on_output("TITLE\x1b[79G\n\x1b[23dHELP1\n\x1b[24dHELP2")
    rows, _ = term._build_view()
    text = _rows_text(rows)
    assert text[22].startswith("HELP1")
    assert text[23].startswith("HELP2")


def _fill_rows(term, label: str = "r") -> None:
    """Write 'r01'..'r22' at column 0 of 0-based rows 1..22 (CUP, no drift)."""
    term._on_output("\x1b[H\x1b[2J")
    for row in range(1, 23):
        term._on_output(f"\x1b[{row + 1};1H{label}{row:02d}")


def test_csi_s_scrolls_only_the_margins_region(term):
    """``CSI Ps S`` (scroll up) shifts the DECSTBM region, nothing outside."""
    _fill_rows(term)
    term._on_output("\x1b[6;21r")  # region = 0-based rows 5..20
    term._on_output("\x1b[3S")     # scroll the region up 3 lines
    rows, _ = term._build_view()
    text = _rows_text(rows)
    assert text[5].startswith("r08")    # r05..r07 scrolled out of the region
    assert text[17].startswith("r20")
    assert text[18].rstrip() == ""      # blanks enter at the region bottom
    assert text[20].rstrip() == ""
    # outside the region: untouched
    assert text[4].startswith("r04")
    assert text[21].startswith("r21")
    assert text[22].startswith("r22")


def test_csi_t_scrolls_only_the_margins_region(term):
    _fill_rows(term)
    term._on_output("\x1b[6;21r")
    term._on_output("\x1b[2T")     # scroll the region down 2 lines
    rows, _ = term._build_view()
    text = _rows_text(rows)
    assert text[5].rstrip() == ""       # blanks enter at the region top
    assert text[6].rstrip() == ""
    assert text[7].startswith("r05")
    assert text[20].startswith("r18")   # r19/r20 scrolled out at the bottom
    assert text[4].startswith("r04")    # outside the region: untouched
    assert text[21].startswith("r21")
    assert text[22].startswith("r22")


def test_csi_s_full_screen_feeds_history(term):
    """A full-screen ``CSI S`` retires lines into scrollback, like a ``LF``
    at the bottom would."""
    term._on_output("\x1b[H\x1b[2J")
    for i in range(24):
        term._on_output(f"\x1b[{i + 1};1Hh{i:02d}")
    before = term._history_len()
    term._on_output("\x1b[r")     # margins reset = whole screen
    term._on_output("\x1b[2S")    # scroll everything up 2
    assert term._history_len() == before + 2
    rows, _ = term._build_view()
    text = _rows_text(rows)
    assert text[0].startswith("h02")


def test_scroll_by_clamps_and_clears_selection(term):
    for i in range(40):
        term._on_output(f"line{i:02d}\r\n")
    hist_len = term._history_len()

    term._sel_active = True
    term._sel_start = (0, 0)
    term._sel_end = (1, 5)
    assert term.has_selection()

    term._scroll_by(hist_len + 1000)  # clamps to hist_len
    assert term._scroll_offset == hist_len
    # moving the view drops the (viewport-relative) selection
    assert not term.has_selection()

    term._scroll_by(-1000)  # clamps to 0
    assert term._scroll_offset == 0


def test_freeze_anchors_view_while_scrolled(term):
    for i in range(40):
        term._on_output(f"line{i:02d}\r\n")
    term._scroll_offset = 3
    _rows1, _c = term._build_view()           # records _last_hist_len
    view_top_before = term._view_top

    # more output arrives while we are scrolled up
    for i in range(40, 50):
        term._on_output(f"line{i:02d}\r\n")

    _rows2, _c = term._build_view()
    # the view stays anchored to the same combined line (frozen), not the tail
    assert term._view_top == view_top_before


def test_selection_resolves_through_viewport(term):
    for i in range(60):
        term._on_output(f"line{i:02d}\r\n")
    # scroll so the oldest content is at the top of the viewport
    term._scroll_offset = term._history_len()
    term._build_view()  # sync _view_top to this offset

    # select the whole first visible row
    term._sel_active = True
    term._sel_start = (0, 0)
    term._sel_end = (0, 79)
    assert term._selection_text().startswith("line00")


def test_typing_returns_to_bottom(term):
    for i in range(40):
        term._on_output(f"line{i:02d}\r\n")
    term._scroll_offset = 5
    assert term._scroll_offset != 0
    # _scroll_to_bottom is what keyPressEvent/paste use to follow input
    term._scroll_to_bottom()
    assert term._scroll_offset == 0


def test_run_command_writes_cr_and_follows_bottom(term):
    """Broadcast target: the line + Enter reaches the PTY, and a view that was
    scrolled back jumps to the live prompt so the output is visible."""
    for i in range(40):
        term._on_output(f"line{i:02d}\r\n")
    term._scroll_offset = 5
    term._build_view()  # sync the freeze baseline before the jump
    written: List[str] = []
    term._pty.write = written.append  # type: ignore[method-assign]

    term.run_command("echo hi")

    assert written == ["echo hi\r"]
    assert term._scroll_offset == 0


# --- pending input (the "run pasted" button) + sync-input mirroring ------------


def _spy(term) -> List[tuple]:
    sent: List[tuple] = []
    term.inputSent.connect(lambda seq, pasted: sent.append((seq, pasted)))
    return sent


def _paste(term, text: str) -> None:
    QGuiApplication.clipboard().setText(text)
    term.paste()


def test_paste_arms_pending_and_announces_paste(term, qapp):
    """A paste into a bracketed-paste shell (PSReadLine, claude) lands in the
    input line unexecuted: the pane arms, and the raw clipboard text -- not the
    wrapped sequence -- is announced, so receivers apply their own paste mode."""
    term._priv_modes.add(2004)
    sent = _spy(term)
    changes: List[int] = []
    term.pendingChanged.connect(lambda: changes.append(1))
    written: List[str] = []
    term._pty.write = written.append  # type: ignore[method-assign]

    _paste(term, "echo hi")

    assert written == ["\x1b[200~echo hi\x1b[201~"]
    assert term.has_pending_input()
    assert changes == [1]
    assert sent == [("echo hi", True)]


def test_paste_strips_one_trailing_newline_so_the_command_waits(term, qapp):
    """ConPTY's cmd/PowerShell never negotiate bracketed paste (verified
    against real streams: only ?1004h/?9001h), so a paste carrying its
    trailing newline would run immediately and the paste-first workflow would
    be dead in the default panes. One trailing CR is stripped: the command
    waits at the prompt and the pane arms, like in a bracketed-paste shell."""
    written: List[str] = []
    term._pty.write = written.append  # type: ignore[method-assign]

    _paste(term, "echo hi\n")            # the usual "copy button" clipboard
    assert written == ["echo hi"]
    assert term.has_pending_input()

    # a multi-line block: earlier lines run, the last one waits at the prompt
    _paste(term, "echo a\necho b\n")
    assert written[-1] == "echo a\recho b"
    assert term.has_pending_input()

    # a genuinely blank tail executes for real: nothing ends up waiting
    _paste(term, "echo c\n\n")
    assert written[-1] == "echo c\r"
    assert not term.has_pending_input()


def test_paste_into_alt_screen_tui_arms_nothing(term, qapp):
    """nano/vim live on the alternate screen: pasted text went into the app's
    buffer, not a command line, so the run-pasted button must not arm."""
    term._priv_modes.update({1049, 2004})
    _paste(term, "text into a buffer, not a prompt")
    assert not term.has_pending_input()


def test_enter_escape_and_ctrl_c_disarm_pending_but_typing_keeps_it(term, qapp):
    """Every way a waiting line retires -- Enter runs it, Esc clears it,
    Ctrl+C abandons it (PSReadLine/cmd line-cancel) -- must disarm the pane;
    plain typing only appends, so the paste is still waiting behind it."""
    changes: List[int] = []
    term.pendingChanged.connect(lambda: changes.append(1))

    term._pending_input = True
    _press(term, Qt.Key.Key_Return)
    assert not term.has_pending_input()

    term._pending_input = True
    _press(term, Qt.Key.Key_Escape)
    assert not term.has_pending_input()

    term._pending_input = True
    _press(term, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert not term.has_pending_input()

    term._pending_input = True
    _press(term, Qt.Key.Key_A, text="a")
    assert term.has_pending_input()

    # every transition above announced itself to the toolbar button
    assert changes == [1, 1, 1]


def test_execute_pending_presses_enter_exactly_once(term):
    written: List[str] = []
    term._pty.write = written.append  # type: ignore[method-assign]

    term.execute_pending()          # nothing waiting -> no stray Enter
    assert written == []

    term._pending_input = True
    term.execute_pending()
    assert written == ["\r"]
    assert not term.has_pending_input()

    term.execute_pending()          # already disarmed: still exactly one
    assert written == ["\r"]


def test_keys_announce_input_for_mirroring(term):
    sent = _spy(term)
    _press(term, Qt.Key.Key_A, text="a")
    _press(term, Qt.Key.Key_Up)
    assert sent == [("a", False), ("\x1b[A", False)]


def test_inject_input_writes_without_reannouncing(term):
    """Mirrored input must not be re-announced, or the workspace fan-out
    would loop forever."""
    sent = _spy(term)
    term._priv_modes.add(2004)
    written: List[str] = []
    term._pty.write = written.append  # type: ignore[method-assign]

    term.inject_input("\x1b[A")
    term.inject_input("npm test", pasted=True)

    assert written == ["\x1b[A", "\x1b[200~npm test\x1b[201~"]
    assert sent == []


def test_mirrored_enter_runs_this_panes_waiting_paste(term):
    term._priv_modes.add(2004)
    _paste(term, "echo hi")
    assert term.has_pending_input()

    term.inject_input("\r")

    assert not term.has_pending_input()


def test_run_command_disarms_pending_and_is_not_mirrored(term):
    """run_command is workspace-initiated: its CR executes whatever waited, and
    sync mode must not echo it (every pane already got the command)."""
    term._pending_input = True
    sent = _spy(term)

    term.run_command("echo hi")

    assert not term.has_pending_input()
    assert sent == []


def test_child_death_disarms_pending(term):
    """A dead pane can never run its waiting input; tick() must disarm it so
    the toolbar button does not keep pointing at a corpse."""
    changes: List[int] = []
    term.pendingChanged.connect(lambda: changes.append(1))
    term._pending_input = True
    term._pty.alive = False

    term.tick()

    assert term.is_dead()
    assert not term.has_pending_input()
    assert changes == [1]


def test_dead_pane_announces_nothing(term):
    """An exited pane keeps keyboard focus until the user clicks away; its
    keys (and right-click QuickEdit pastes!) must not leak into sync mode."""
    sent = _spy(term)
    term._dead = True

    _press(term, Qt.Key.Key_A, text="a")
    _paste(term, "rm -rf /tmp/x\necho done")
    term.inject_input("x")

    assert sent == []


def test_mirrored_input_follows_the_live_bottom(term):
    """A receiving pane left scrolled back must jump to the prompt, like the
    local input paths -- otherwise sync mode types into a view the user
    cannot see (offset frozen on old lines)."""
    for i in range(40):
        term._on_output(f"line{i:02d}\r\n")
    term._build_view()                       # sync the freeze baseline
    term._scroll_offset = 6

    term.inject_input("x")

    assert term._scroll_offset == 0


def test_mouse_input_is_never_announced_for_mirroring(term):
    """Sync mode fans keyboard only: mouse packets are built from THIS pane's
    grid coordinates and would corrupt a differently-sized pane's TUI."""
    sent = _spy(term)
    term._priv_modes.update({1000, 1006})    # the app tracks the mouse

    def _mouse(kind, pos, button, buttons):
        return QMouseEvent(kind, pos, QPointF(100, 100), button, buttons,
                           Qt.KeyboardModifier.NoModifier)

    term.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, QPointF(100, 200),
                                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    term.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, QPointF(120, 200),
                               Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    term.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, QPointF(120, 200),
                                  Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))

    assert sent == []


def _press(term, key, mods=Qt.KeyboardModifier.NoModifier, text: str = "") -> None:
    term.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, mods, text))


def test_keyboard_copy_while_scrolled_copies_selection(term, qapp):
    """Regression: copying a scrollback selection must grab the highlighted
    line, not the live-bottom line, and must NOT jump the view to the bottom."""
    QGuiApplication.clipboard().clear()
    for i in range(60):
        term._on_output(f"line{i:02d}\r\n")
    hist_len = term._history_len()

    # scroll to the very top, then select the first visible (oldest) row
    term._scroll_offset = hist_len
    term._build_view()  # sync _view_top / freeze state to this offset
    term._sel_active = True
    term._sel_start = (0, 0)
    term._sel_end = (0, 79)

    _press(term, Qt.Key.Key_C,
           Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)

    assert QGuiApplication.clipboard().text().startswith("line00")
    # copy must not have moved the viewport
    assert term._scroll_offset == hist_len


def test_scroll_to_bottom_clears_selection(term):
    for i in range(40):
        term._on_output(f"line{i:02d}\r\n")
    term._scroll_offset = 5
    term._sel_active = True
    term._sel_start = (0, 0)
    term._sel_end = (1, 5)
    assert term.has_selection()
    term._scroll_to_bottom()
    # the stale viewport-relative selection is dropped on the jump
    assert not term.has_selection()
    assert term._scroll_offset == 0


def test_mouse_seq_sgr_vs_legacy(term):
    # SGR (mode 1006 negotiated): text form with < prefix
    term._priv_modes.add(1006)
    assert term._mouse_seq(0, 4, 9, pressed=True) == "\x1b[<0;5;10M"
    assert term._mouse_seq(2, 4, 9, pressed=False) == "\x1b[<2;5;10m"
    # wheel forward in SGR
    assert term._mouse_seq(64, 0, 0, pressed=True) == "\x1b[<64;1;1M"

    # legacy (no 1006): ESC[M + three bytes (value + 32)
    term._priv_modes.discard(1006)
    seq = term._mouse_seq(0, 4, 9, pressed=True)
    assert seq == "\x1b[M" + chr(32) + chr(32 + 5) + chr(32 + 10)
    # legacy release -> button code 3
    rel = term._mouse_seq(0, 0, 0, pressed=False)
    assert rel == "\x1b[M" + chr(32 + 3) + chr(33) + chr(33)
    # coords clamp to the 1..223 byte range
    big = term._mouse_seq(0, 10_000, 10_000, pressed=True)
    assert big == "\x1b[M" + chr(32) + chr(32 + 223) + chr(32 + 223)


def test_autoscroll_params(term):
    """Direction/speed for a drag position relative to the viewport edges."""
    h = term.height()  # 24 rows * 20px
    # inside the widget -> no auto-scroll
    assert term._autoscroll_params(QPointF(5, 5)) == (0, 0)
    assert term._autoscroll_params(QPointF(5, h - 1)) == (0, 0)
    # just past the top/bottom -> direction, 1 line per tick
    assert term._autoscroll_params(QPointF(5, -1)) == (-1, 1)
    assert term._autoscroll_params(QPointF(5, h + 1)) == (1, 1)
    # deeper overshoot scrolls faster (one extra line per row of overshoot)
    assert term._autoscroll_params(QPointF(5, -45)) == (-1, 3)   # 45//20 + 1
    assert term._autoscroll_params(QPointF(5, h + 40)) == (1, 3)
    # ... but capped
    assert term._autoscroll_params(QPointF(5, -100_000))[1] == 8
    assert term._autoscroll_params(QPointF(5, h + 100_000))[1] == 8


def test_drag_scroll_anchors_selection(term):
    """Scrolling mid-drag must keep the selection on its content lines
(positive offset delta = the view scrolled up = content moved DOWN the
viewport, so both endpoints' rows shift by the delta)."""
    for i in range(60):
        term._on_output(f"line{i:02d}\r\n")

    term._sel_active = True
    term._sel_dragging = True
    term._sel_start = (10, 0)
    term._sel_end = (2, 40)

    term._scroll_by(5)  # an auto-scroll tick of 5 lines up
    assert term._scroll_offset == 5
    assert term._sel_start == (15, 0)
    assert term._sel_end == (7, 40)
    assert term.has_selection()  # NOT dropped mid-drag

    term._scroll_by(-2)  # dragged back down 2 lines
    assert term._sel_start == (13, 0)
    assert term._sel_end == (5, 40)


def test_autoscroll_up_extends_selection_into_history(term):
    """Holding the drag above the top edge scrolls the view up while the
selection end stays pinned to the top row -> the sweep covers every line
scrolled past, and the copy grabs exactly those lines."""
    for i in range(60):
        term._on_output(f"line{i:02d}\r\n")
    hist = term._history_len()
    term._build_view()  # sync _view_top / freeze state

    term._sel_active = True
    term._sel_dragging = True
    term._sel_start = (20, 79)  # press at viewport row 20, right edge
    term._sel_end = (0, 0)      # dragged up to the top-left corner

    term._autoscroll_pos = QPointF(0, -5)
    term._autoscroll_dir, term._autoscroll_lines = term._autoscroll_params(
        term._autoscroll_pos
    )
    for _ in range(5):
        term._autoscroll_tick()

    assert term._scroll_offset == 5
    assert term._sel_end == (0, 0)          # still pinned to the top edge
    assert term._sel_start == (25, 79)      # anchor kept on its content line

    text = term._selection_text().split("\n")
    # viewport top after the scroll-up shows history line (hist - 5); the
    # anchored press was on content line (hist + 20)
    assert text[0].startswith(f"line{hist - 5:02d}")
    assert text[-1].startswith(f"line{hist + 20:02d}")
    assert len(text) == 26


def test_drag_below_bottom_scrolls_down_to_live(term):
    """Holding the drag below the bottom edge scrolls back toward the live
prompt and clamps at offset 0; the anchor row goes negative (its content is
now above the viewport) without breaking anything."""
    for i in range(60):
        term._on_output(f"line{i:02d}\r\n")
    # sync _last_hist_len while still at offset 0, THEN scroll up -- setting
    # the offset first would make _build_view's freeze count the whole feed
    # as growth-while-scrolled and jump the view to the very top
    term._build_view()
    term._scroll_offset = 10   # user had scrolled back up

    term._sel_active = True
    term._sel_dragging = True
    term._sel_start = (5, 0)
    term._sel_end = (23, 30)

    term._autoscroll_pos = QPointF(50, term.height() + 10)
    term._autoscroll_dir, term._autoscroll_lines = term._autoscroll_params(
        term._autoscroll_pos
    )
    for _ in range(20):        # 1 line/tick, clamps at 0 after 10
        term._autoscroll_tick()

    assert term._scroll_offset == 0
    assert term._sel_start == (-5, 0)       # content scrolled off the top
    assert term._sel_end[0] == 23           # pinned to the bottom edge
    # resolving the sweep must not crash and must span the swept lines
    text = term._selection_text().split("\n")
    assert len(text) == 29                  # rows -5..23 inclusive


def test_drag_outside_starts_and_release_stops_autoscroll(term, qapp):
    """Mouse-driven lifecycle: a move beyond the edge starts the timer, the
release stops it (and ends the drag so a later wheel scroll clears)."""
    for i in range(60):
        term._on_output(f"line{i:02d}\r\n")

    def _mouse(kind, pos, button, buttons):
        return QMouseEvent(kind, pos, QPointF(100, 100), button, buttons,
                           Qt.KeyboardModifier.NoModifier)

    press = _mouse(QEvent.Type.MouseButtonPress, QPointF(100, 200),
                   Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    term.mousePressEvent(press)
    assert term._sel_dragging

    # wiggle inside first: nothing to start
    term.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, QPointF(100, 100),
                               Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    assert not term._autoscroll_timer.isActive()

    # drag past the top edge -> timer running with "up" parameters
    term.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, QPointF(100, -30),
                               Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    assert term._autoscroll_timer.isActive()
    assert term._autoscroll_dir == -1

    release = _mouse(QEvent.Type.MouseButtonRelease, QPointF(100, -30),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
    term.mouseReleaseEvent(release)
    assert not term._sel_dragging
    assert not term._autoscroll_timer.isActive()
    assert term._autoscroll_dir == 0

    # after the release, scrolling drops the (viewport-relative) selection
    assert term.has_selection() or term._sel_active
    term._scroll_by(2)
    assert not term.has_selection()


def test_scroll_to_bottom_mid_drag_keeps_selection(term):
    """_scroll_to_bottom now routes through _scroll_by, so a keypress while
holding a selection drag anchors instead of clearing."""
    for i in range(60):
        term._on_output(f"line{i:02d}\r\n")
    # sync the paint baseline first (see test_drag_below_bottom...): a manual
    # offset write with a stale _last_hist_len would be misread as growth
    term._build_view()
    term._scroll_offset = 7
    term._sel_active = True
    term._sel_dragging = True
    term._sel_start = (3, 0)
    term._sel_end = (1, 10)

    term._scroll_to_bottom()
    assert term._scroll_offset == 0
    assert term._sel_start == (-4, 0)   # 3 - 7
    assert term._sel_end == (-6, 10)
    assert term._sel_active


def test_right_click_mid_drag_stops_autoscroll(term, qapp):
    """Right-click mid-drag: on Windows QuickEdit copies + clears, so the
drag state and auto-scroll timer must not outlive it. On Linux the right
button is the menu's; the still-held LEFT drag legitimately continues."""
    for i in range(60):
        term._on_output(f"line{i:02d}\r\n")

    def _mouse(kind, pos, button, buttons):
        return QMouseEvent(kind, pos, QPointF(100, 100), button, buttons,
                           Qt.KeyboardModifier.NoModifier)

    term.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, QPointF(100, 200),
                                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    term.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, QPointF(100, -30),
                               Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    assert term._autoscroll_timer.isActive()

    # right button while the left drag is live
    term.mousePressEvent(_mouse(
        QEvent.Type.MouseButtonPress, QPointF(100, 100),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton,
    ))
    if sys.platform == "win32":
        # QuickEdit copy cleared the selection outright
        assert not term._autoscroll_timer.isActive()
        assert not term._sel_dragging
        assert not term._sel_active
    else:
        # the left button is still held: the drag (and its auto-scroll)
        # keep running and must die with the LEFT release, not linger
        assert term._autoscroll_timer.isActive()
        assert term._sel_dragging
        term.mouseReleaseEvent(_mouse(
            QEvent.Type.MouseButtonRelease, QPointF(100, 150),
            Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton))
        assert not term._autoscroll_timer.isActive()
        assert not term._sel_dragging


def test_tracking_enabled_mid_drag_release_is_clean(term, qapp):
    """The child can turn mouse tracking on between our press and release
(e.g. Claude Code finishing startup); the release must still end the drag."""
    for i in range(60):
        term._on_output(f"line{i:02d}\r\n")

    def _mouse(kind, pos, button, buttons):
        return QMouseEvent(kind, pos, QPointF(100, 100), button, buttons,
                           Qt.KeyboardModifier.NoModifier)

    term.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, QPointF(100, 200),
                                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    term.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, QPointF(100, -30),
                               Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    assert term._autoscroll_timer.isActive()

    term._on_output("\x1b[?1000h\x1b[?1006h")  # tracking turned on mid-hold
    assert term._mouse_on

    term.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, QPointF(100, -30),
                                  Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
    assert not term._autoscroll_timer.isActive()
    assert not term._sel_dragging


def test_autoscroll_at_top_of_history_no_anchor_drift(term):
    """Pinned at the very top of history with output streaming in: the ticks
must NOT shift the anchor (the freeze keeps the view re-pinned, so the
growth is not view movement -- counting it drifts the selection onto
neighbouring lines)."""
    for i in range(60):
        term._on_output(f"line{i:02d}\r\n")
    term._build_view()
    term._scroll_offset = term._history_len()   # pinned at the very top
    term._build_view()

    term._sel_active = True
    term._sel_dragging = True
    term._sel_start = (5, 79)
    term._sel_end = (0, 0)
    term._autoscroll_pos = QPointF(0, -5)
    term._autoscroll_dir, term._autoscroll_lines = term._autoscroll_params(
        term._autoscroll_pos
    )
    for i in range(3):
        term._on_output(f"extra{i}\r\n")        # output streams mid-drag
        term._autoscroll_tick()

    term._build_view()
    assert term._view_top == 0                  # the view stays pinned
    assert term._sel_start == (5, 79)           # the anchor does not drift
    text = term._selection_text().split("\n")
    assert len(text) == 6                       # rows 0..5, not more
    assert text[0].startswith("line00")
    assert text[-1].startswith("line05")


def test_resize_smaller_promotes_top_rows_to_history(term):
    for i in range(24):
        term._on_output(f"row{i:02d}\r\n")
    before = term._history_len()
    top_visible = "".join(term._screen.buffer[0][c].data for c in range(80)).rstrip()

    term.resize(80 * 10, 10 * 20)  # 80x24 -> 80x10 : drops 14 top rows

    after = term._history_len()
    # the 14 clipped rows were saved into history, not lost
    assert after - before == 14
    recoverable = ["".join(ln[c].data for c in range(80)).rstrip() for ln in term._screen.history.top]
    assert top_visible in recoverable


def test_resize_grow_then_shrink_no_aliasing(term):
    """Regression: a shrink that follows a grow must not alias a scrollback
    line to a live buffer row.

    pyte grows by only bumping ``lines`` -- the buffer (a defaultdict) stays
    sparse, so the manual row promotion used to read (and thus materialise)
    absent rows, appending the *same* dict to history. pyte's delete_lines then
    skipped the absent source rows and never detached the promoted ones, so a
    later write mutated the scrollback -> duplicated/ghosted lines on
    scroll-back. This is the classic "text duplicated after resizing/tiling".
    """
    term.resize(80 * 10, 30 * 20)        # 80x24 -> 80x30 (pyte leaves it sparse)
    term._on_output("\x1b[28;1Hdeep")    # write into a grown row
    term.resize(80 * 10, 10 * 20)        # 80x30 -> 80x10 (shrink)

    buf_ids = {id(term._screen.buffer[y]) for y in range(term._screen.lines)}
    aliased = [h for h in (id(ln) for ln in term._screen.history.top) if h in buf_ids]
    assert not aliased  # no scrollback line shares a dict with a live row

    # writing into the new bottom must not mutate any scrollback line
    before = ["".join(ln[c].data for c in range(80)) for ln in term._screen.history.top]
    term._on_output("\x1b[10;1Htail-mark")
    after = ["".join(ln[c].data for c in range(80)) for ln in term._screen.history.top]
    assert before == after


def test_resize_shrink_clamps_cursor(term):
    """Regression: pyte clamps the cursor against the PRE-resize size, so a
    height shrink left cursor.y beyond the new grid until we clamp it."""
    term._on_output("\x1b[22;1Hx")        # cursor to row 22 -> y = 21
    assert term._screen.cursor.y >= 10
    term.resize(80 * 10, 10 * 20)         # 80x24 -> 80x10
    assert term._screen.cursor.y < term._screen.lines
    assert term._screen.cursor.x < term._screen.columns


def test_resize_shrink_drops_ghost_rows(term):
    """Regression: buffer rows left beyond the new height would resurrect as
    stale 'ghost' lines on a later grow, so drop them on shrink."""
    term._on_output("\x1b[20;1Hhello")
    term.resize(80 * 10, 8 * 20)          # 80x24 -> 80x8
    ghosts = [k for k in term._screen.buffer if k >= term._screen.lines]
    assert not ghosts


def test_resize_reflow_cycle_stays_clean(term):
    """Repeated grow/shrink (as happens when tiles are added/removed) must not
    introduce aliasing or an out-of-bounds cursor at any point."""
    for i in range(24):
        term._on_output(f"r{i:02d}\r\n")
    for _ in range(6):
        term.resize(80 * 10, 10 * 20)
        term.resize(80 * 10, 30 * 20)
        term.resize(80 * 10, 16 * 20)
        buf_ids = {id(term._screen.buffer[y]) for y in range(term._screen.lines)}
        assert not [h for h in (id(ln) for ln in term._screen.history.top) if h in buf_ids]
        assert term._screen.cursor.y < term._screen.lines
        assert term._screen.cursor.x < term._screen.columns


def test_lnm_engages_only_for_conpty_backend(qapp):
    """The LNM workaround is a ConPTY-stream compensation, NOT terminal
    semantics: with a backend that does not request it (the Unix PTY), a
    bare LF keeps the column (xterm index), and pyte's default mode stays
    untouched."""
    class _UnixFakePty(_FakePty):
        LNM_WORKAROUND = False

    saved = tw.Pty
    tw.Pty = _UnixFakePty  # type: ignore[assignment]
    try:
        w = tw.TerminalWidget("fake", font_size=10)
    finally:
        tw.Pty = saved  # type: ignore[assignment]
    w._cell_w, w._cell_h = 10, 20
    w.resize(80 * 10, 24 * 20)
    w.show()
    qapp.processEvents()

    import pyte.modes
    assert pyte.modes.LNM not in w._screen.mode  # no ConPTY quirk to fix
    w._on_output("abc")
    w._on_output("\n")
    w._on_output("x")
    with w._lock:
        line1 = w._screen.buffer[1]
        cells = [line1[c].data for c in range(5)]
    assert cells[0] == " "   # xterm LF: NO carriage return
    assert cells[3] == "x"   # the column survives the linefeed


# --- right/middle mouse buttons + theme switch (Debian user report) ---------

class _RecordingPty(_FakePty):
    """A fake PTY that records what the widget writes to the child."""

    def __init__(self, *_a, **_k) -> None:
        self.writes: List[str] = []

    def write(self, text: str) -> None:
        self.writes.append(text)


@pytest.fixture()
def recterm(qapp):
    """TerminalWidget with a recording fake PTY (mouse-event tests)."""
    saved = tw.Pty
    tw.Pty = _RecordingPty  # type: ignore[assignment]
    try:
        w = tw.TerminalWidget("fake", font_size=10)
    finally:
        tw.Pty = saved  # type: ignore[assignment]
    w._cell_w, w._cell_h = 10, 20
    w.resize(80 * 10, 24 * 20)
    w.show()
    qapp.processEvents()
    return w


def test_middle_click_pastes_the_platform_selection(recterm, monkeypatch):
    """X11: the middle button pastes the PRIMARY selection; Windows keeps
    the clipboard paste (the Debian report: both buttons felt dead)."""
    from PySide6.QtTest import QTest

    pasted = []
    monkeypatch.setattr(
        recterm, "paste", lambda primary=False: pasted.append(primary))
    QTest.mouseClick(recterm, Qt.MouseButton.MiddleButton)
    assert pasted == [sys.platform != "win32"]


def test_right_click_is_quickedit_only_on_windows(recterm, monkeypatch):
    """On Windows right-click is QuickEdit copy/paste; on Linux the button
    belongs to the context menu (menu itself is covered below), so the
    press must not paste."""
    from PySide6.QtTest import QTest

    pasted = []
    monkeypatch.setattr(
        recterm, "paste", lambda primary=False: pasted.append(primary))
    QTest.mouseClick(recterm, Qt.MouseButton.RightButton)
    if sys.platform == "win32":
        assert pasted == [False]  # no selection -> QuickEdit pastes
    else:
        assert pasted == []       # the menu's job, not QuickEdit's


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only menu")
def test_posix_context_menu_actions(recterm, monkeypatch):
    """Right-click on Linux opens Copy/Paste/Clear -- exactly these three
    (the Debian follow-up asked for "Paste selection" to go away: the middle
    button already covers the PRIMARY paste)."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QContextMenuEvent

    shown = {}

    class _Action:
        def __init__(self, label):
            self.label = label
            self.enabled = True

        def setEnabled(self, value):
            self.enabled = value

    class _Menu:
        def __init__(self, _parent=None):
            self.actions = []

        def addAction(self, label):
            action = _Action(label)
            self.actions.append(action)
            return action

        def exec(self, _pos):
            shown["menu"] = [(a.label, a.enabled) for a in self.actions]
            return None

    monkeypatch.setattr(tw, "QMenu", _Menu)
    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, QPoint(4, 4), QPoint(10, 10))
    recterm.contextMenuEvent(event)

    labels = dict(shown["menu"])
    assert set(labels) == {"Copy", "Paste", "Clear selection"}
    assert labels["Copy"] is False            # nothing selected yet
    assert labels["Clear selection"] is False

    # with a live selection both selection actions enable
    recterm._sel_start, recterm._sel_end = (0, 0), (0, 3)
    recterm._sel_active = True
    recterm.contextMenuEvent(event)
    labels = dict(shown["menu"])
    assert labels["Copy"] is True
    assert labels["Clear selection"] is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows QuickEdit path")
def test_windows_context_menu_stays_swallowed(recterm, monkeypatch):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QContextMenuEvent

    def _fail(*_a, **_k):
        raise AssertionError("no menu may appear on Windows")

    monkeypatch.setattr(tw, "QMenu", _fail)
    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, QPoint(4, 4), QPoint(10, 10))
    recterm.contextMenuEvent(event)  # must simply accept


def test_shift_bypasses_app_mouse_tracking(recterm):
    """Shift+click inside a mouse-tracking TUI (claude, htop) must act
    locally: selection works, nothing is forwarded -- the xterm rule."""
    from PySide6.QtTest import QTest

    recterm._update_priv_modes("\x1b[?1000h\x1b[?1006h")
    assert recterm._mouse_on
    QTest.mouseClick(
        recterm, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier)
    assert recterm._sel_active          # local selection started
    assert recterm._pty.writes == []    # nothing forwarded to the app


def test_selection_publishes_to_primary(recterm, monkeypatch):
    """The highlighted text becomes the X11 PRIMARY selection live (drag,
    autoscroll, release), so the middle button pastes exactly what the user
    selected -- the Debian report had it paste stale foreign text. Without
    a selection clipboard (Windows) the publish is a no-op."""
    from PySide6.QtGui import QClipboard

    for i in range(5):
        recterm._on_output(f"line{i}\r\n")

    published = []
    supports = {"selection": True}

    class _Clipboard_:
        def supportsSelection(self):
            return supports["selection"]

        def setText(self, text, mode=None):
            published.append((text, mode))

        def text(self, mode=None):
            return ""

    class _GuiStub:
        @staticmethod
        def clipboard():
            return _Clipboard_()

    monkeypatch.setattr(tw, "QGuiApplication", _GuiStub)

    def _mouse(kind, pos, button, buttons):
        return QMouseEvent(kind, pos, QPointF(100, 100), button, buttons,
                           Qt.KeyboardModifier.NoModifier)

    supports["selection"] = False  # Windows-like: publishing must be a no-op
    recterm.mousePressEvent(_mouse(
        QEvent.Type.MouseButtonPress, QPointF(5, 5),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    recterm.mouseMoveEvent(_mouse(
        QEvent.Type.MouseMove, QPointF(45, 45),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    recterm.mouseReleaseEvent(_mouse(
        QEvent.Type.MouseButtonRelease, QPointF(45, 45),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
    assert published == []

    supports["selection"] = True     # X11: every selection change publishes
    recterm.mousePressEvent(_mouse(
        QEvent.Type.MouseButtonPress, QPointF(5, 5),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    recterm.mouseMoveEvent(_mouse(
        QEvent.Type.MouseMove, QPointF(45, 45),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    recterm.mouseReleaseEvent(_mouse(
        QEvent.Type.MouseButtonRelease, QPointF(45, 45),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))

    texts = [t for t, m in published if m == QClipboard.Mode.Selection]
    assert texts, "the selection was never published to PRIMARY"
    assert "line1" in texts[-1]      # rows 0..2, cols per the drag geometry


@pytest.mark.parametrize("name", sorted(tw.themes.SCHEMES))
def test_selection_paints_inverted_colors(term, qapp, name):
    """Selection = classic fg/bg inversion of the selected cells (the old
    translucent overlay made selected text unreadable in dark themes and
    near-invisible in light ones -- Debian report 1.1/1.2). Pins three
    things: the band color (theme fg), the run BOUNDARY (the very next
    unselected cell keeps the plain bg -- a run-split regression inverts
    the whole row), and the swapped glyph pen (the selected cell contains
    pixels of the theme bg: without the pen swap the text vanishes into
    the band, the exact reported symptom)."""
    from megacode import themes

    themes.set_active(name)
    term.refresh_theme()
    term._on_output("hello")
    term._sel_active = True
    term._sel_start = (0, 0)
    term._sel_end = (0, 3)           # "hell" selected; "o" + cursor stay plain

    img = term.grab().toImage()
    fg_lum = QColor(themes.active()["term_fg"]).lightness()
    bg_lum = QColor(themes.active()["term_bg"]).lightness()
    # band: inside the selected cell, above the glyphs
    assert abs(QColor(img.pixel(5, 1)).lightness() - fg_lum) <= 25, name
    # boundary: col 4 is one past the selection and must stay plain
    assert abs(QColor(img.pixel(45, 1)).lightness() - bg_lum) <= 25, name
    # glyph pen: the selected cell holds pixels of the bg color (the 'h'
    # stroke drawn with the swapped pen); antialiasing blends toward the
    # band, so the extreme over the whole cell is the honest probe
    lums = [QColor(img.pixel(x, y)).lightness()
            for x in range(0, 10) for y in range(0, 20)]
    if bg_lum < fg_lum:      # dark theme: light band, dark glyphs
        assert min(lums) <= bg_lum + 25, (name, min(lums), bg_lum)
    else:                    # light theme: dark band, light glyphs
        assert max(lums) >= bg_lum - 25, (name, max(lums), bg_lum)


def test_plain_click_never_clobbers_primary(recterm, monkeypatch):
    """A zero-drag click is a focus action, not a selection: it must publish
    nothing to X11 PRIMARY, steal nothing when another application owns it,
    and release our OWN stale PRIMARY (the xterm invariant). A real
    selection over blank cells (text resolves empty) and every selection
    CLEAR must also disown -- otherwise the middle button keeps pasting
    text that is no longer highlighted (review findings)."""
    from PySide6.QtGui import QClipboard

    recterm._on_output("hello\r\n")

    published = []
    owns = {"value": False}

    class _Clipboard_:
        def supportsSelection(self):
            return True

        def ownsSelection(self):
            return owns["value"]

        def setText(self, text, mode=None):
            published.append((text, mode))
            owns["value"] = bool(text)

        def text(self, mode=None):
            return ""

    class _GuiStub:
        @staticmethod
        def clipboard():
            return _Clipboard_()

    monkeypatch.setattr(tw, "QGuiApplication", _GuiStub)

    def _mouse(kind, pos, button, buttons):
        return QMouseEvent(kind, pos, QPointF(100, 100), button, buttons,
                           Qt.KeyboardModifier.NoModifier)

    def _drag(a, b):
        recterm.mousePressEvent(_mouse(
            QEvent.Type.MouseButtonPress, a,
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
        recterm.mouseMoveEvent(_mouse(
            QEvent.Type.MouseMove, b,
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
        recterm.mouseReleaseEvent(_mouse(
            QEvent.Type.MouseButtonRelease, b,
            Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))

    # 1) plain click on a glyph cell: another app owns PRIMARY -> hands off;
    #    when WE own it, the click releases it (nothing highlighted anymore)
    _drag(QPointF(5, 5), QPointF(5, 5))     # zero-drag == plain click
    assert published == []
    owns["value"] = True
    _drag(QPointF(5, 5), QPointF(5, 5))
    assert published == [("", QClipboard.Mode.Selection)]
    published.clear()
    owns["value"] = False

    # 2) a real selection over BLANK rows: text resolves empty -> disown,
    # so PRIMARY can never serve text that is not what is highlighted
    owns["value"] = True
    _drag(QPointF(5, 110), QPointF(5, 150))  # rows 5..7 are blank
    assert published and published[-1] == ("", QClipboard.Mode.Selection)
    assert all(t == "" for t, _m in published)

    # 3) a real text selection publishes, clearing it disowns
    owns["value"] = True
    _drag(QPointF(5, 5), QPointF(25, 5))     # "hel" on row 0
    assert published[-1] == ("hel", QClipboard.Mode.Selection)
    recterm._clear_selection()
    assert published[-1] == ("", QClipboard.Mode.Selection)


def test_left_click_still_forwards_when_tracking(recterm):
    from PySide6.QtTest import QTest

    recterm._update_priv_modes("\x1b[?1000h\x1b[?1006h")
    QTest.mouseClick(recterm, Qt.MouseButton.LeftButton)
    assert not recterm._sel_active
    assert recterm._pty.writes          # SGR mouse bytes went to the child


def test_refresh_theme_recolors_the_pane(term):
    """Theme switch repaints existing panes with the new palette."""
    from megacode import themes

    themes.set_active("paper-light")
    term.refresh_theme()
    light = themes.SCHEMES["paper-light"]
    assert term._default_bg == QColor(light["term_bg"])
    assert term._default_fg == QColor(light["term_fg"])

    themes.set_active("claude-dark")
    term.refresh_theme()
    assert term._default_bg == QColor("#1e1e1e")
    assert term._default_fg == QColor("#d4d4d4")


def test_resolve_color_follows_active_scheme():
    from megacode import themes

    themes.set_active("marine-night")
    expected = QColor(themes.SCHEMES["marine-night"]["term_colors"]["red"])
    assert tw._resolve_color("red", QColor("#ffffff")) == expected
