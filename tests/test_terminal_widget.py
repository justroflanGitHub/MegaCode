"""Offscreen tests for the terminal widget's scrollback + selection math.

These exercise the new HistoryScreen-backed viewport (``_build_view``) and the
viewport-relative selection resolution (``_selection_text``) without spawning a
real process: ``Pty`` is replaced by a no-op fake, and output is fed directly.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from typing import List  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QKeyEvent, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from megacode import terminal_widget as tw  # noqa: E402


class _FakePty:
    """A stand-in for ``Pty`` that never spawns a child."""

    def __init__(self, *_a, **_k) -> None:
        pass

    def start(self, _on_output) -> None:
        return None

    def write(self, _text: str) -> None:
        return None

    def resize(self, _cols: int, _rows: int) -> None:
        return None

    def is_alive(self) -> bool:
        return True

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
    """QuickEdit copy (right-click) mid-drag clears the selection; the drag
state and auto-scroll timer must not outlive it."""
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

    # right button while the left drag is live -> copy + clear
    term.mousePressEvent(_mouse(
        QEvent.Type.MouseButtonPress, QPointF(100, 100),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton,
    ))
    assert not term._autoscroll_timer.isActive()
    assert not term._sel_dragging
    assert not term._sel_active


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
