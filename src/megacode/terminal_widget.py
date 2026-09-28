"""An embeddable terminal widget: ConPTY child + pyte emulator + QPainter.

Renders a real process (Claude Code, a shell, ...) inside a QWidget by feeding
the pseudo-console's VT output stream into ``pyte`` and painting the resulting
screen grid. Keystrokes are encoded back into VT sequences and written to the
child. This is what makes true drag-to-swap possible: the running session lives
inside this widget, so moving the widget moves the session.
"""

from __future__ import annotations

import re
import sys
import threading
from typing import List, Optional

import pyte
import pyte.modes
from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QClipboard, QColor, QFont, QFontMetrics, QGuiApplication, QPainter,
    QPalette,
)
from PySide6.QtWidgets import QMenu, QWidget

from . import themes

if sys.platform == "win32":
    from .conpty import Pty
else:
    from .unixpty import Pty

# pyte misparses private/extended SGR sequences (e.g. Kitty's underline-style
# "ESC[>4;2m") as plain SGR 4 (underline), so the whole frame ends up
# "underlined". Windows Terminal ignores these, so we strip them. Standard SGR
# (no < > = ? prefix) is never touched.
_PRIVATE_SGR = re.compile(r"\x1b\[[<>=?][0-9;:]*m")
# A trailing, possibly-incomplete numeric CSI -- held back so a private sequence
# is never split across two reads (which would defeat the filter).
_TRAILING_CSI = re.compile(r"\x1b\[[<>=?]?[0-9;:]*$")
# DEC private mode set/reset: ESC[?Ps...h  / ESC[?Ps...l  (bracketed paste,
# mouse tracking, focus reporting, ...)
_PRIV_MODE = re.compile(r"\x1b\[\?([0-9;]+)([hl])")
_BRACKETED_PASTE = 2004
_MOUSE_TRACKING = {1000, 1002, 1003}  # X10 / button-event / any-event
_SGR_MOUSE = 1006
# DECSET 47 / 1047 / 1049: the alternate screen used by full-screen TUIs
# (nano, vim, less). Text pasted there lands in an app buffer, not a prompt.
_ALT_SCREEN_MODES = {47, 1047, 1049}
# Key sequences that end a pane's "input waiting at the prompt" state: Enter
# executes the line; Esc clears it (PSReadLine revert, claude's clear);
# Ctrl+C / Ctrl+Break abandon it. Shared by typed and mirrored input so a
# pane never stays armed after its line is gone.
_CANCEL_SEQS = ("\r", "\x1b", "\x03", "\x1c")

#: How many scrolled-off lines to retain so they can be scrolled back to with
#: the mouse wheel (this is what cmd/powershell "lose the top output" needs).
_HISTORY_LINES = 10000
#: Lines scrolled per wheel notch (matches Windows Terminal's default feel).
_WHEEL_LINES = 3
#: Auto-scroll cadence while a selection drag is held past the top/bottom edge.
_AUTOSCROLL_INTERVAL_MS = 50
#: Cap on lines per auto-scroll tick, however far the pointer overshoots.
_AUTOSCROLL_MAX_LINES = 8

# --- theme & palette --------------------------------------------------------
# Цвета дефолтного fg/bg, 16 ANSI-цветов и заливки выделения приходят из
# активной схемы (themes.py) и читаются на каждом paint'е — смена темы не
# требует пересоздания панелей, только refresh_theme() + repaint.

# pyte emits the 16 ANSI colours by name; everything 256/truecolour comes as a
# 6-digit hex string. (pyte maps SGR 33 -> "brown".)
_HEX_DIGITS = set("0123456789abcdef")


def _resolve_color(spec: str, default: QColor) -> QColor:
    if not spec or spec == "default":
        return default
    palette = themes.active()["term_colors"]
    if spec in palette:
        return QColor(palette[spec])
    if len(spec) == 6 and all(ch in _HEX_DIGITS for ch in spec):
        return QColor("#" + spec)
    return default


def _forward_mouse(event) -> bool:
    """Should this mouse event go to the TUI app instead of acting locally?

    The app asked for mouse tracking (DECSET 1000-1003), but Shift held
    down means the user wants the terminal's own behaviour -- the xterm
    convention: Shift freezes tracking so selection/paste/menus always
    stay reachable inside mouse-aware TUIs (claude, htop...).
    """
    return not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)


class _ConPtyScreen(pyte.HistoryScreen):
    """HistoryScreen plus the two ops pyte 0.8.2 is missing for TUIs.

    ``CSI Ps S`` / ``CSI Ps T`` (scroll the DECSTBM region up/down) are what
    full-screen editors (nano's smooth scroll, vim's ``scrolljump``) use to
    shift their edit area; stock pyte silently ignores both, leaving part of
    the region stale while the freshly drawn lines move. These follow the
    margin semantics of pyte's own ``index`` / ``reverse_index``.
    """

    def _region(self) -> tuple[int, int]:
        if self.margins:
            top, bottom = self.margins
        else:
            top, bottom = 0, self.lines - 1
        return top, bottom

    def su(self, count: int | None = None) -> None:
        """``CSI Ps S`` -- scroll the margins region up by ``Ps`` lines."""
        top, bottom = self._region()
        n = min(max(count or 1, 1), bottom - top + 1)
        for _ in range(n):
            if top == 0:
                # a full-screen scroll retires the top line into scrollback,
                # exactly like HistoryScreen.index does
                self.history.top.append(self.buffer[top])
            for y in range(top, bottom):
                self.buffer[y] = self.buffer[y + 1]
            self.buffer.pop(bottom, None)
        self.dirty.update(range(self.lines))

    def sd(self, count: int | None = None) -> None:
        """``CSI Ps T`` -- scroll the margins region down by ``Ps`` lines."""
        top, bottom = self._region()
        n = min(max(count or 1, 1), bottom - top + 1)
        for _ in range(n):
            for y in range(bottom, top, -1):
                self.buffer[y] = self.buffer[y - 1]
            self.buffer.pop(top, None)
        self.dirty.update(range(self.lines))


class _ConPtyStream(pyte.Stream):
    """A Stream that also dispatches ``CSI S``/``CSI T`` to su/sd."""

    csi = {**pyte.Stream.csi, "S": "su", "T": "sd"}


class TerminalWidget(QWidget):
    """A single embedded terminal surface."""

    #: Emitted when the child process exits.
    finished = Signal()
    #: User-originated input was written to this pane: ``(data, is_paste)``.
    #: Keys carry the encoded VT sequence; a paste carries the RAW clipboard
    #: text so every receiving pane can apply its own bracketed-paste mode.
    #: The workspace mirrors this to the other panes in sync-input mode.
    inputSent = Signal(str, bool)
    #: The "pasted input waiting for Enter" state changed (arms/disarms the
    #: workspace's "run pasted" button).
    pendingChanged = Signal()

    def __init__(
        self,
        command: str,
        cwd: Optional[str] = None,
        font_size: int = 10,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(False)

        # Windows Terminal's default face is "Cascadia Mono"; use it (with
        # fallbacks) so glyphs and metrics match what the user expects. The
        # Linux fallbacks (DejaVu/Liberation/Noto mono) cover Cyrillic on
        # Astra/Fly, where none of the Windows faces exist.
        self._font = QFont("Cascadia Mono", font_size)
        self._font.setFamilies(
            ["Cascadia Mono", "Cascadia Code", "Cascadia Mono NF", "Consolas",
             "DejaVu Sans Mono", "Liberation Mono", "Noto Sans Mono",
             "Courier New"]
        )
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        self._font.setFixedPitch(True)
        metrics = QFontMetrics(self._font)
        self._cell_w = max(1, metrics.horizontalAdvance("M"))
        self._cell_h = max(1, metrics.lineSpacing())
        self._ascent = metrics.ascent()

        self._cols, self._rows = 80, 24
        self._lock = threading.Lock()
        # HistoryScreen keeps scrolled-off lines in ``history.top`` so the user
        # can scroll back to them with the mouse wheel -- without it, pyte drops
        # everything past the visible grid (cmd/powershell "lost top output").
        self._screen = _ConPtyScreen(
            self._cols, self._rows, history=_HISTORY_LINES
        )
        # ConPTY's re-emitted stream uses the Windows console's cooked newline
        # semantics: a bare LF moves to the next row AND column 0 (verified
        # against nano 8.5: "\n<ESC>[2dline-0000\n<ESC>[3d..." with zero CRLFs).
        # pyte's VT-strict LF keeps the column, which shears every TUI frame;
        # LNM makes pyte's linefeed do index + carriage return, like WT.
        # A Unix PTY needs the opposite: the line discipline already emits CRLF
        # for cooked output and raw-mode TUIs expect xterm LF semantics, so
        # the flag engages only for the ConPTY backend (Pty.LNM_WORKAROUND).
        if Pty.LNM_WORKAROUND:
            self._screen.mode.add(pyte.modes.LNM)
        self._stream = _ConPtyStream(self._screen)
        self._pty_buf = ""

        self._pty = Pty(command, self._cols, self._rows, cwd=cwd)
        self._pty.start(self._on_output)

        self._default_fg = QColor(themes.active()["term_fg"])
        self._default_bg = QColor(themes.active()["term_bg"])
        # text selection + copy/paste
        self._sel_active = False
        self._sel_start: Optional[tuple[int, int]] = None
        self._sel_end: Optional[tuple[int, int]] = None
        # True only while the left button is held on a selection drag. Only
        # then may the view scroll without dropping the selection (the
        # auto-scroll below needs exactly that).
        self._sel_dragging = False
        # auto-scroll state while the drag is held beyond the top/bottom edge
        self._autoscroll_dir = 0          # -1 = up into history, +1 = down
        self._autoscroll_lines = 0        # lines per tick (speed ~ overshoot)
        self._autoscroll_pos: Optional[QPointF] = None
        self._autoscroll_timer = QTimer(self)
        self._autoscroll_timer.setInterval(_AUTOSCROLL_INTERVAL_MS)
        self._autoscroll_timer.timeout.connect(self._autoscroll_tick)
        # private modes parsed from the output stream
        self._priv_modes: set[int] = set()
        # scrollback: _scroll_offset is the number of lines the viewport is
        # scrolled up from the live bottom (0 == following new output).
        # _view_top is the combined (history+buffer) index of the top visible
        # row, recomputed each paint and used to resolve selections.
        self._scroll_offset = 0
        self._view_top = 0
        # last seen history length, used to freeze the view on the same lines
        # when new output arrives while scrolled up
        self._last_hist_len = 0
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, self._default_bg)
        palette.setColor(QPalette.ColorRole.Base, self._default_bg)
        palette.setColor(QPalette.ColorRole.Text, self._default_fg)
        palette.setColor(QPalette.ColorRole.WindowText, self._default_fg)
        self.setPalette(palette)

        # "pasted input waiting for Enter" -- armed by paste(), disarmed when
        # the line actually runs (manual Enter, run_command, or the workspace's
        # "run pasted" button). Drives that button's enabled state.
        self._pending_input = False

        self._dead = False

    # --- PTY output -> pyte -> repaint --------------------------------------
    def _on_output(self, text: str) -> None:
        # Buffer so a private SGR sequence is never split across reads, then
        # strip the ones pyte misparses (see _PRIVATE_SGR).
        buf = self._pty_buf + text
        tail = _TRAILING_CSI.search(buf)
        if tail:
            ready, self._pty_buf = buf[: tail.start()], buf[tail.start():]
        else:
            ready, self._pty_buf = buf, ""
        ready = _PRIVATE_SGR.sub("", ready)
        self._update_priv_modes(ready)
        if ready:
            with self._lock:
                self._stream.feed(ready)
        # signal crosses from the reader thread to the GUI thread (queued)
        self.update()

    def _update_priv_modes(self, text: str) -> None:
        for params, final in _PRIV_MODE.findall(text):
            on = final == "h"
            for num in params.split(";"):
                try:
                    mode = int(num)
                except ValueError:
                    continue
                if on:
                    self._priv_modes.add(mode)
                else:
                    self._priv_modes.discard(mode)

    @property
    def _bracketed_paste(self) -> bool:
        return _BRACKETED_PASTE in self._priv_modes

    @property
    def _alt_screen(self) -> bool:
        return bool(self._priv_modes & _ALT_SCREEN_MODES)

    @property
    def _mouse_on(self) -> bool:
        return bool(self._priv_modes & _MOUSE_TRACKING)

    # --- painting -----------------------------------------------------------
    def _build_view(self) -> tuple[list[list], int]:
        """Snapshot the visible rows, honouring the current scroll offset.

        Returns ``(rows_of_chars, columns)``. Each row is a list of pyte ``Char``
        cells for the grid width. Must be called under ``self._lock``. Also
        updates ``self._view_top`` to the combined (history+buffer) index of the
        first returned row, so selection code can map viewport rows back to the
        right line.
        """
        screen = self._screen
        hist = screen.history.top
        hist_len = len(hist)
        buf_lines = screen.lines
        columns = screen.columns
        buffer = screen.buffer
        total = hist_len + buf_lines

        view_h = max(1, min(self._rows_for_height(), buf_lines))
        offset = self._effective_offset(hist_len)
        # persist the freeze so wheel/clamp see the updated offset
        self._scroll_offset = offset
        self._last_hist_len = hist_len
        bottom = total - offset                       # combined idx, exclusive
        top = max(0, bottom - view_h)
        self._view_top = top

        blank = screen.default_char
        rows: list[list] = []
        for idx in range(top, top + view_h):
            if idx >= total:
                rows.append([blank] * columns)
            elif idx < hist_len:
                line = hist[idx]
                rows.append([line[c] for c in range(columns)])
            else:
                line = buffer[idx - hist_len]
                rows.append([line[c] for c in range(columns)])
        return rows, columns

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt signature)
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._default_bg)

        with self._lock:
            snapshot, columns = self._build_view()
            cursor_x = self._screen.cursor.x
            cursor_y = self._screen.cursor.y
            cursor_hidden = self._screen.cursor.hidden
            # the live cursor lives in the buffer, so it is only on-screen when
            # we are following new output (offset 0); then viewport row == cursor_y
            cursor_in_view = self._scroll_offset == 0

        painter.setFont(self._font)
        # viewport-row -> (first_col, last_col) of the selection, resolved
        # once per paint: selected cells swap their resolved fg/bg (classic
        # terminal inversion -- see _draw_run), so the highlight stays
        # readable in every scheme.
        sel = ({r: (c1, c2) for r, c1, c2 in self._selection_ranges()}
               if self._sel_active and self._sel_start is not None
               and self._sel_end is not None else {})
        for row, chars in enumerate(snapshot):
            self._draw_row(painter, row, chars, columns, sel.get(row))

        if (not cursor_hidden and not self._dead and cursor_in_view
                and 0 <= cursor_y < len(snapshot) and 0 <= cursor_x < columns):
            cs = sel.get(cursor_y)
            cursor_selected = cs is not None and cs[0] <= cursor_x <= cs[1]
            self._draw_cursor(painter, cursor_x, cursor_y,
                              snapshot[cursor_y][cursor_x], cursor_selected)

        if self._dead:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 170))
            painter.setPen(QColor("#cccccc"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "[ process exited ]")

    def _draw_row(
        self, painter: QPainter, row: int, chars: list, columns: int,
        sel: Optional[tuple[int, int]] = None,
    ) -> None:
        y = row * self._cell_h
        max_cols = min(columns, self._cols_for_width())
        if not chars:
            return
        # group consecutive cells with identical attributes into runs
        col = 0
        while col < max_cols:
            ch = chars[col]
            fg_spec, bg_spec = ch.fg, ch.bg
            bold, underline, reverse = ch.bold, ch.underscore, ch.reverse
            selected = sel is not None and sel[0] <= col <= sel[1]
            run_chars = []
            run_end = col
            while run_end < max_cols:
                c = chars[run_end]
                sel_here = sel is not None and sel[0] <= run_end <= sel[1]
                if (c.fg != fg_spec or c.bg != bg_spec or bool(c.bold) != bool(bold)
                        or bool(c.underscore) != bool(underline) or bool(c.reverse) != bool(reverse)
                        or sel_here != selected):
                    break
                run_chars.append(c.data)
                run_end += 1
            self._draw_run(painter, col, y, run_chars, fg_spec, bg_spec, bold,
                           underline, reverse, selected)
            col = run_end if run_end > col else col + 1

    def _draw_run(
        self, painter: QPainter, col: int, y: int, run_chars: List[str],
        fg_spec: str, bg_spec: str, bold: bool, underline: bool, reverse: bool,
        selected: bool = False,
    ) -> None:
        text = "".join(run_chars)
        x = col * self._cell_w
        fg = _resolve_color(fg_spec, self._default_fg)
        bg = _resolve_color(bg_spec, self._default_bg)
        if reverse:
            fg, bg = bg, fg
        if selected:
            # Classic terminal selection: swap the resolved cell colors. The
            # old translucent overlay composites with whatever is under it,
            # so light-on-light (dark theme, yellowish overlay) or
            # light-on-light (paper theme, near-invisible overlay) both end
            # with unreadable selected text; inversion is readable in every
            # scheme by construction (Debian report items 1.1/1.2).
            fg, bg = bg, fg
        if bg != self._default_bg:
            painter.fillRect(x, y, self._cell_w * len(run_chars), self._cell_h, bg)
        # Only decorate runs that actually contain visible glyphs; underlining
        # whitespace draws spurious full-width lines across empty cells.
        has_text = any(ch != " " for ch in run_chars)
        f = QFont(self._font)
        if bold:
            f.setBold(True)
        if underline and has_text:
            f.setUnderline(True)
        painter.setFont(f)
        painter.setPen(fg)
        painter.drawText(x, y + self._ascent, text)

    def _draw_cursor(
        self, painter: QPainter, col: int, row: int, char, selected: bool = False,
    ) -> None:
        x = col * self._cell_w
        y = row * self._cell_h
        fg = _resolve_color(char.fg, self._default_fg)
        bg = _resolve_color(char.bg, self._default_bg)
        if char.reverse:
            fg, bg = bg, fg
        if selected:
            # Inside the selection band the block cursor would fill with the
            # cell's fg -- the exact band color -- and disappear. Swap, like
            # the glyphs, so the block reads as the band's text color.
            fg, bg = bg, fg
        painter.fillRect(x, y, self._cell_w, self._cell_h, fg)

    # --- input --------------------------------------------------------------
    def focusNextPrevChild(self, _next: bool) -> bool:  # noqa: N802 (Qt signature)
        # Keep Tab / Shift+Tab inside the terminal (they drive the app, e.g.
        # Claude Code's mode cycling) instead of moving focus to other widgets.
        return False

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        mods = event.modifiers()
        key = event.key()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)

        # Copy / paste (Windows-style: Ctrl+Shift+C/V, Ctrl+Ins / Shift+Ins).
        # Handled BEFORE scrolling to the bottom so a selection made while
        # scrolled back copies the lines the user actually highlighted, not the
        # live-bottom rows.
        copy_combo = (ctrl and shift and key == Qt.Key.Key_C) or (ctrl and key == Qt.Key.Key_Insert)
        paste_combo = (ctrl and key == Qt.Key.Key_V) or (shift and key == Qt.Key.Key_Insert)
        if copy_combo:
            text = self._selection_text()
            if text:
                self._copy_text(text)
            return
        if paste_combo:
            self.paste()
            return
        # Plain Ctrl+C: copy if there is a selection, otherwise send SIGINT.
        if ctrl and key == Qt.Key.Key_C and self.has_selection():
            self._copy_text(self._selection_text())
            self._clear_selection()
            return

        # Genuine input returns to the live prompt (input follows the bottom of
        # the buffer, not the scrolled-back view).
        self._scroll_to_bottom()
        seq = self._encode_key(event)
        if seq:
            if seq in _CANCEL_SEQS:
                # Enter/Esc/Ctrl+C all retire the waiting line one way or another
                self._set_pending(False)
            self._write_input(seq)
        else:
            super().keyPressEvent(event)

    # --- mouse: selection / forwarding / paste -----------------------------
    def _cell_at(self, pos) -> tuple[int, int]:
        col = max(0, min(self._cols - 1, int(pos.x()) // self._cell_w))
        row = max(0, min(self._rows - 1, int(pos.y()) // self._cell_h))
        return (row, col)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        if self._mouse_on and _forward_mouse(event):
            if event.button() == Qt.MouseButton.LeftButton:
                # A press while the app tracks the mouse can never be part of
                # a selection drag; clear any state left over from one.
                self._sel_dragging = False
                self._stop_autoscroll()
            self._send_mouse(event, pressed=True)
            return
        button = event.button()
        if button == Qt.MouseButton.LeftButton:
            self._sel_start = self._sel_end = self._cell_at(event.position())
            self._sel_active = True
            self._sel_dragging = True
            self.update()
        elif button == Qt.MouseButton.RightButton:
            # Windows console "QuickEdit": right-click copies the selection
            # if there is one, otherwise pastes. On Linux the right button
            # is the context menu's (see contextMenuEvent) -- a paste with
            # an empty clipboard would read as "the button does nothing".
            if sys.platform == "win32":
                if self.has_selection():
                    self._copy_text(self._selection_text())
                    self._clear_selection()
                else:
                    self.paste()
        elif button == Qt.MouseButton.MiddleButton:
            # X11 convention: the middle button pastes the PRIMARY
            # selection (what's currently highlighted), not the clipboard
            self.paste(primary=sys.platform != "win32")

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        if self._mouse_on and _forward_mouse(event):
            if event.buttons() & Qt.MouseButton.LeftButton:
                self._send_mouse(event, pressed=True)
            return
        if self._sel_active and (event.buttons() & Qt.MouseButton.LeftButton):
            self._sel_end = self._cell_at(event.position())
            self._update_autoscroll(event.position())
            self.update()
            self._publish_selection()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        if event.button() == Qt.MouseButton.LeftButton:
            # End the drag on EVERY left release -- including when the app
            # turned mouse tracking on mid-hold (its release is forwarded
            # below) and when the selection was already cleared mid-drag
            # (right-click copy). Otherwise the auto-scroll timer outlives
            # the button that started it.
            self._sel_dragging = False
            self._stop_autoscroll()
        if self._mouse_on and _forward_mouse(event):
            if event.button() == Qt.MouseButton.LeftButton:
                self._send_mouse(event, pressed=False)
            return
        if event.button() == Qt.MouseButton.LeftButton and self._sel_active:
            self._sel_end = self._cell_at(event.position())
            self.update()
            # No copy-on-select into the CLIPBOARD: the user copies explicitly
            # with right-click / Ctrl+C, like the classic cmd console. The X11
            # PRIMARY is a different channel and IS published live -- that is
            # what the middle button pastes (see _publish_selection). A click
            # without a drag selects nothing: xterm disowns PRIMARY then, and
            # so do we -- a focus click must not clobber it with one char.
            if self.has_selection():
                self._publish_selection()
            else:
                self._disown_selection()

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        # A tile can be hidden mid-drag (closed / swapped away); the implicit
        # mouse grab then ends without a release, so stop the auto-scroll here.
        self._sel_dragging = False
        self._stop_autoscroll()
        super().hideEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        # Windows keeps the QuickEdit right button (copy/paste on press);
        # while an app tracks the mouse the click belongs to it (Shift
        # bypasses -- see _forward_mouse). Everywhere else the right button
        # opens the terminal's own menu, as a Linux user expects.
        if sys.platform == "win32" or (self._mouse_on and _forward_mouse(event)):
            event.accept()
            return
        menu = QMenu(self)
        has_sel = self.has_selection()
        copy_action = menu.addAction("Copy")
        copy_action.setEnabled(has_sel)
        paste_action = menu.addAction("Paste")
        clear_action = menu.addAction("Clear selection")
        clear_action.setEnabled(has_sel)
        chosen = menu.exec(event.globalPos())
        if chosen is copy_action:
            self._copy_text(self._selection_text())
            self._clear_selection()
        elif chosen is paste_action:
            self.paste()
        elif chosen is clear_action:
            self._clear_selection()
        event.accept()

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        if self._mouse_on and _forward_mouse(event):
            # App is tracking the mouse: forward the wheel as SGR mouse events so
            # mouse-aware TUIs (less, pagers, claude's scrollback) scroll themselves.
            self._send_wheel(event)
            return
        delta = event.angleDelta().y()
        if delta == 0:
            pd = event.pixelDelta().y()
            delta = pd * 8 if pd else 0
        if delta == 0:
            event.ignore()
            return
        page = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        step = self._rows if page else _WHEEL_LINES
        notches = abs(delta) // 120 or 1
        self._scroll_by(notches * step if delta > 0 else -(notches * step))

    def _send_wheel(self, event) -> None:
        delta = event.angleDelta().y() or (event.pixelDelta().y() * 8)
        if delta == 0:
            return
        code = 64 if delta > 0 else 65  # SGR mouse: button wheel up / down
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ShiftModifier:
            code += 4
        if mods & Qt.KeyboardModifier.AltModifier:
            code += 8
        if mods & Qt.KeyboardModifier.ControlModifier:
            code += 16
        row, col = self._cell_at(event.position())
        self._pty.write(self._mouse_seq(code, col, row, pressed=True))

    # --- selection auto-scroll ----------------------------------------------
    def _autoscroll_params(self, pos) -> tuple[int, int]:
        """``(direction, lines_per_tick)`` for a drag position.

        Direction -1 = the pointer is above the widget (scroll up into
        history), +1 = below it (scroll down), 0 = inside: no auto-scroll.
        The further past the edge, the faster -- one extra line per row of
        overshoot, capped so a fling across the window stays sane.
        """
        if pos.y() < 0:
            over = -pos.y()
            return -1, max(1, min(_AUTOSCROLL_MAX_LINES, int(over) // self._cell_h + 1))
        if pos.y() > self.height():
            over = pos.y() - self.height()
            return 1, max(1, min(_AUTOSCROLL_MAX_LINES, int(over) // self._cell_h + 1))
        return 0, 0

    def _update_autoscroll(self, pos) -> None:
        if not self._sel_dragging:
            return
        self._autoscroll_pos = pos
        self._autoscroll_dir, self._autoscroll_lines = self._autoscroll_params(pos)
        if self._autoscroll_dir == 0:
            self._autoscroll_timer.stop()
        elif not self._autoscroll_timer.isActive():
            # Only start when idle: restarting on every move would starve the
            # timer while the user drags along an edge (moves every few ms).
            self._autoscroll_timer.start()

    def _autoscroll_tick(self) -> None:
        if (not self._sel_dragging or self._autoscroll_dir == 0
                or self._autoscroll_pos is None):
            self._stop_autoscroll()
            return
        # -dir: holding above the top edge (-1) scrolls up (offset grows);
        # holding below the bottom edge (+1) scrolls back down toward the
        # live prompt (offset shrinks, clamped at 0 by _scroll_by).
        self._scroll_by(-self._autoscroll_dir * self._autoscroll_lines)
        # Re-pin the dragged end to the edge the pointer is beyond, so the
        # selection extends through every line the view scrolls past even
        # while the mouse itself no longer moves.
        self._sel_end = self._cell_at(self._autoscroll_pos)
        self.update()
        self._publish_selection()

    def _stop_autoscroll(self) -> None:
        self._autoscroll_timer.stop()
        self._autoscroll_dir = 0
        self._autoscroll_lines = 0
        self._autoscroll_pos = None

    # --- scrollback ---------------------------------------------------------
    def _history_len(self) -> int:
        with self._lock:
            return len(self._screen.history.top)

    def _effective_offset(self, hist_len: int) -> int:
        """The scroll offset after the freeze-on-output adjustment.

        When the view is scrolled up and new output has arrived since the last
        paint, the offset grows to keep the same lines anchored (real-terminal
        behaviour). Computed WITHOUT mutating state so both _build_view (which
        persists it) and _selection_text (read-only copy) resolve to the exact
        same view the user sees.
        """
        offset = min(self._scroll_offset, hist_len)
        if self._scroll_offset > 0:
            growth = hist_len - self._last_hist_len
            if growth > 0:
                offset = min(self._scroll_offset + growth, hist_len)
        return offset

    def _scroll_by(self, lines: int) -> None:
        """Scroll the viewport; positive = up into history, negative = down."""
        hist_len = self._history_len()
        # Absorb any history growth since the last paint (the freeze) into
        # the base BEFORE computing the re-anchor delta. At the offset clamps
        # that growth is either not real view movement (pinned at the top of
        # history -- the next paint re-pins the view, so shifting the anchor
        # by it drifts the selection onto neighbouring lines) or is missing
        # from a plain offset delta (jumping to the bottom skips the freeze,
        # so the view moves by the growth too). Basing everything on the
        # effective offset makes the delta exactly the commanded movement.
        base = self._effective_offset(hist_len)
        self._scroll_offset = base
        self._last_hist_len = hist_len
        new_offset = max(0, min(base + lines, hist_len))
        if new_offset == base:
            return
        delta = new_offset - base
        self._scroll_offset = new_offset
        if self._sel_dragging and self._sel_start is not None and self._sel_end is not None:
            # Mid-drag (auto-scroll): selection coordinates are
            # viewport-relative, so re-anchor both ends to their content.
            # The view moved ``delta`` lines, which shifts the same content
            # ``delta`` rows down the viewport (positive delta = scrolled up).
            self._sel_start = (self._sel_start[0] + delta, self._sel_start[1])
            self._sel_end = (self._sel_end[0] + delta, self._sel_end[1])
        else:
            # Outside a drag: drop the selection -- its viewport-relative
            # coordinates would resolve against the wrong lines after the move.
            self._clear_selection()
        self.update()

    def _scroll_to_bottom(self) -> None:
        # Jump to the live bottom from wherever the view REALLY is: base the
        # jump on the effective offset (freeze included) so it always lands
        # at 0. _scroll_by applies the same invariant as everywhere else:
        # clear the selection outside a drag, re-anchor it during one.
        self._scroll_by(-self._effective_offset(self._history_len()))

    def _mouse_seq(self, code: int, col: int, row: int, pressed: bool) -> str:
        """Encode a mouse event using the app's negotiated encoding.

        Mode 1006 (SGR) -> ``ESC[<{code};{col};{row}M|m``. Otherwise the legacy
        X10 / button-event format ``ESC[M`` followed by three bytes (button,
        col, row -- each +32, coords 1-based and clamped to the 1..223 byte
        range). A legacy button release is reported with button code 3.
        """
        c = max(1, min(col + 1, 223))
        r = max(1, min(row + 1, 223))
        if _SGR_MOUSE in self._priv_modes:
            return f"\x1b[<{code};{c};{r}{'M' if pressed else 'm'}"
        btn = 3 if (not pressed and code <= 2) else code
        return "\x1b[M" + chr(32 + btn) + chr(32 + c) + chr(32 + r)

    def _send_mouse(self, event, pressed: bool) -> None:
        btn_map = {
            Qt.MouseButton.LeftButton: 0,
            Qt.MouseButton.MiddleButton: 1,
            Qt.MouseButton.RightButton: 2,
        }
        code = btn_map.get(event.button(), 3)
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ShiftModifier:
            code += 4
        if mods & Qt.KeyboardModifier.AltModifier:
            code += 8
        if mods & Qt.KeyboardModifier.ControlModifier:
            code += 16
        row, col = self._cell_at(event.position())
        self._pty.write(self._mouse_seq(code, col, row, pressed))

    # --- selection / clipboard ----------------------------------------------
    def has_selection(self) -> bool:
        return self._sel_active and self._sel_start != self._sel_end

    def _ordered_selection(self) -> Optional[tuple[tuple[int, int], tuple[int, int]]]:
        if self._sel_start is None or self._sel_end is None:
            return None
        a, b = self._sel_start, self._sel_end
        return (a, b) if a <= b else (b, a)

    def _selection_ranges(self):
        """Yield (row, col_start, col_end) inclusive for the current selection."""
        ends = self._ordered_selection()
        if ends is None:
            return
        (r1, c1), (r2, c2) = ends
        cols = max(1, self._cols)
        for r in range(r1, r2 + 1):
            start = c1 if r == r1 else 0
            end = c2 if r == r2 else cols - 1
            yield r, start, end

    def _selection_text(self) -> str:
        # Selection coordinates are viewport rows (0-based over the visible
        # grid). Resolve each to its combined history+buffer line using the same
        # offset->top math as _build_view, computed fresh here (the cached
        # _view_top may be stale if the offset just changed this tick).
        with self._lock:
            hist = self._screen.history.top
            hist_len = len(hist)
            buf_lines = self._screen.lines
            columns = self._screen.columns
            buffer = self._screen.buffer
            total = hist_len + buf_lines
            view_h = max(1, min(self._rows_for_height(), buf_lines))
            offset = self._effective_offset(hist_len)
            top = max(0, total - offset - view_h)
            parts = []
            for r, c1, c2 in self._selection_ranges():
                idx = top + r
                if idx < 0 or idx >= total:
                    continue
                line = hist[idx] if idx < hist_len else buffer[idx - hist_len]
                c2 = min(c2, columns - 1)
                row_text = "".join(line[c].data for c in range(c1, c2 + 1))
                parts.append(row_text.rstrip())
        return "\n".join(parts)

    def _clear_selection(self) -> None:
        self._sel_active = False
        self._sel_start = self._sel_end = None
        # nothing is highlighted here any more, so PRIMARY must not keep
        # serving this pane's old text as "the selection"
        self._disown_selection()
        # Any path that drops the selection also ends a drag in progress
        # (e.g. right-click QuickEdit copy mid-drag): otherwise the release
        # handler would find _sel_active already False, skip its cleanup and
        # leave the auto-scroll timer running with no button held.
        self._sel_dragging = False
        self._stop_autoscroll()
        self.update()

    def _copy_text(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)

    def _publish_selection(self) -> None:
        """Own the X11 PRIMARY selection with the highlighted text.

        xterm semantics: what is selected IS the middle-button paste -- here
        and in every other application. Without publishing, PRIMARY holds
        whatever was last selected elsewhere, so the middle button pasted
        foreign text ("что-то другое" from the Debian report). No-op on
        platforms without a selection clipboard (Windows: the middle button
        pastes the clipboard instead). A degenerate (zero-drag) or
        blank-cell selection owns nothing -- publishing one stray character
        on every focus click would clobber the user's real selection.
        """
        if not self.has_selection():
            return
        clipboard = QGuiApplication.clipboard()
        if not clipboard.supportsSelection():
            return
        text = self._selection_text()
        if text.strip():
            clipboard.setText(text, mode=QClipboard.Mode.Selection)
        else:
            # a real (banded) selection over blank cells resolves to bare
            # newline separators -- publishing those would serve garbage;
            # disown instead, like a collapsed selection
            self._disown_selection()

    def _disown_selection(self) -> None:
        """Release PRIMARY ownership -- but only if this widget holds it.

        Clearing unconditionally would steal the selection from whatever
        other application legitimately owns it; ``ownsSelection`` keeps the
        disown honest (no-op on Windows: there is no selection clipboard).
        """
        clipboard = QGuiApplication.clipboard()
        if clipboard.supportsSelection() and clipboard.ownsSelection():
            clipboard.setText("", mode=QClipboard.Mode.Selection)

    def paste(self, primary: bool = False) -> None:
        """Paste into the pane: the clipboard, or -- ``primary=True`` -- the
        X11 PRIMARY selection (the middle-button paste on Linux)."""
        if self._dead:
            return
        self._scroll_to_bottom()
        mode = (QClipboard.Mode.Selection if primary
                else QClipboard.Mode.Clipboard)
        text = QGuiApplication.clipboard().text(mode=mode)
        if not text:
            return
        self._deliver_paste(text)
        self.inputSent.emit(text, True)

    def _deliver_paste(self, text: str) -> None:
        """Write clipboard ``text`` into this pane and track the pending state.

        Shared by the local paste and by mirrored input (sync-input mode): the
        raw clipboard text travels so each pane applies its own bracketed-paste
        mode. A paste arms this pane for the workspace's "run pasted" button --
        unless it executed itself (blank tail) or landed inside a full-screen
        TUI, where it went into an app buffer (nano's file), not a prompt.
        """
        # terminals use CR, not LF
        text = text.replace("\r\n", "\r").replace("\n", "\r")
        # One trailing newline is stripped: ConPTY's cmd/PowerShell never
        # negotiate bracketed paste (verified: both emit only ?1004h/?9001h),
        # so a paste carrying its newline would run immediately and the
        # "paste, then ↵ Run pasted" workflow would be dead in the default
        # panes. Without it the command waits, like in any bracketed-paste
        # shell. (A blank tail -- "cmd\n\n" -- still executes for real.)
        if text.endswith("\r"):
            text = text[:-1]
        if not text:
            return
        if self._bracketed_paste:
            text = f"\x1b[200~{text}\x1b[201~"
            pending = True
        else:
            pending = not text.endswith("\r")
        if self._alt_screen:
            pending = False
        self._pty.write(text)
        self._set_pending(pending)

    # --- pending input + mirrored (sync) input -------------------------------
    def _set_pending(self, value: bool) -> None:
        if value == self._pending_input:
            return
        self._pending_input = value
        self.pendingChanged.emit()

    def has_pending_input(self) -> bool:
        """True while pasted-but-unexecuted input sits at this pane's prompt."""
        return self._pending_input

    def execute_pending(self) -> None:
        """Press Enter on the waiting input (the "run pasted" button)."""
        if not self._pending_input:
            return
        self._set_pending(False)
        self._scroll_to_bottom()
        self._pty.write("\r")

    def _write_input(self, seq: str) -> None:
        """Write user-typed input and announce it for sync-input mirroring."""
        if self._dead:
            # an exited pane keeps keyboard focus until clicked away; emitting
            # here would leak its keys (and pastes!) into every live pane
            return
        self._pty.write(seq)
        self.inputSent.emit(seq, False)

    def inject_input(self, seq: str, pasted: bool = False) -> None:
        """Receive a pane's user input mirrored by the workspace (sync mode).

        Writes straight to the PTY -- deliberately NOT through ``inputSent`` --
        so mirrored input can never trigger a second fan-out. Paste payloads
        re-deliver here (this pane's own bracketed-paste mode and pending
        flag); a mirrored Enter/Esc/Ctrl+C runs/clears this pane's waiting
        line too. The view follows the live bottom, like every local input
        path: a pane left scrolled back must still show what it receives.
        """
        if self._dead:
            return
        self._scroll_to_bottom()
        if pasted:
            self._deliver_paste(seq)
            return
        if seq in _CANCEL_SEQS:
            self._set_pending(False)
        self._pty.write(seq)

    def run_command(self, command: str) -> None:
        """Type ``command`` at the prompt and press Enter.

        The broadcast target for the workspace's "run in every pane" bar. A
        plain write + CR -- deliberately NOT bracketed-paste wrapped, so the
        shell sees the line as typed input and executes it. The view follows
        the live bottom first so the command's output is what the user sees.
        The workspace skips panes holding pasted input (appending would
        concatenate "<pasted><command>" onto one line); the disarm here is
        just defense in depth. Workspace-initiated, so this is NOT announced
        on ``inputSent`` (sync mode must not echo it a second time).
        """
        self._scroll_to_bottom()
        self._set_pending(False)
        self._pty.write(command + "\r")

    def _encode_key(self, event) -> Optional[str]:
        key = event.key()
        mods = event.modifiers()
        text = event.text()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)

        if ctrl and Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            return chr(key - Qt.Key.Key_A + 1)

        # Shift+Tab cycles Claude Code modes (auto-accept / plan); must send
        # the "back-tab" sequence, not a plain tab. Qt may deliver it as either
        # Key_Tab + Shift or Key_Backtab.
        if key == Qt.Key.Key_Backtab or (shift and key == Qt.Key.Key_Tab):
            return "\x1b[Z"

        special = {
            Qt.Key.Key_Return: "\r", Qt.Key.Key_Enter: "\r",
            Qt.Key.Key_Backspace: "\x7f", Qt.Key.Key_Tab: "\t",
            Qt.Key.Key_Escape: "\x1b",
            Qt.Key.Key_Up: "\x1b[A", Qt.Key.Key_Down: "\x1b[B",
            Qt.Key.Key_Right: "\x1b[C", Qt.Key.Key_Left: "\x1b[D",
            Qt.Key.Key_Home: "\x1b[H", Qt.Key.Key_End: "\x1b[F",
            Qt.Key.Key_PageUp: "\x1b[5~", Qt.Key.Key_PageDown: "\x1b[6~",
            Qt.Key.Key_Insert: "\x1b[2~", Qt.Key.Key_Delete: "\x1b[3~",
        }
        if key in special:
            seq = special[key]
            return ("\x1b" + seq) if alt else seq

        if text:
            return ("\x1b" + text) if alt else text
        return None

    # --- resizing -----------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        new_cols = max(1, self._cols_for_width())
        new_rows = max(1, self._rows_for_height())
        if (new_cols, new_rows) != (self._cols, self._rows):
            self._cols, self._rows = new_cols, new_rows
            with self._lock:
                screen = self._screen
                if new_rows < screen.lines:
                    # pyte's resize() drops excess top rows on a shrink WITHOUT
                    # promoting them to history, so push them first -- mirroring
                    # HistoryScreen.index, which saves a scrolled-off line. This
                    # keeps the currently-visible top lines recoverable via the
                    # scrollback.
                    #
                    # pyte grows by only bumping ``lines``: the buffer (a
                    # defaultdict) stays SPARSE, so rows past the old height do
                    # not exist until first read. Without densifying first, the
                    # promotion below would read -- and thus materialise -- each
                    # absent row, aliasing the very same dict into history; pyte's
                    # coming delete_lines then skips absent *source* rows (its
                    # ``if (y + count) in self.buffer`` guard) and fails to detach
                    # the promoted ones, so a later write mutates the scrollback
                    # and the same line shows up duplicated/ghosted on scroll-back
                    # (notably after a grow->shrink such as tiling reflow). Touch
                    # every row first so the shift detaches all promoted rows.
                    buf = screen.buffer
                    for y in range(screen.lines):
                        _ = buf[y]  # force defaultdict to materialise blank rows
                    for y in range(screen.lines - new_rows):
                        screen.history.top.append(screen.buffer[y])
                screen.resize(lines=new_rows, columns=new_cols)
                # pyte clamps the cursor against the PRE-resize line/column count
                # (self.lines is updated only after restore_cursor), so a shrink
                # can leave the cursor out of bounds and the child's next write
                # landing on a non-existent row. Clamp to the new grid and drop
                # any buffer rows left beyond the new height (ghosts that would
                # otherwise resurrect on a later grow).
                screen.cursor.x = min(screen.cursor.x, screen.columns - 1)
                screen.cursor.y = min(screen.cursor.y, screen.lines - 1)
                for y in [k for k in list(screen.buffer) if k >= screen.lines]:
                    screen.buffer.pop(y, None)
            self._pty.resize(new_cols, new_rows)
        super().resizeEvent(event)

    def _cols_for_width(self) -> int:
        return self.width() // self._cell_w

    def _rows_for_height(self) -> int:
        return self.height() // self._cell_h

    # --- lifecycle ----------------------------------------------------------
    def is_dead(self) -> bool:
        return self._dead

    def refresh_theme(self) -> None:
        """Re-read the active scheme's terminal colors and repaint.

        Called by the workspace after themes.set_active: the paint loop
        resolves ANSI names through themes.active() anyway, but the cached
        default fg/bg, the widget palette and the backing store need the
        explicit nudge.
        """
        scheme = themes.active()
        self._default_fg = QColor(scheme["term_fg"])
        self._default_bg = QColor(scheme["term_bg"])
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, self._default_bg)
        palette.setColor(QPalette.ColorRole.Base, self._default_bg)
        palette.setColor(QPalette.ColorRole.Text, self._default_fg)
        palette.setColor(QPalette.ColorRole.WindowText, self._default_fg)
        self.setPalette(palette)
        self.update()

    def tick(self) -> None:
        """Called on a timer to detect child exit and refresh the cursor."""
        if not self._dead and not self._pty.is_alive():
            self._dead = True
            # a dead pane can never run its waiting input; disarm the button
            self._set_pending(False)
            self.finished.emit()
        self.update()

    def close(self) -> None:
        self._pty.stop()

    def sizeHint(self):  # noqa: N802 (Qt signature)
        from PySide6.QtCore import QSize

        return QSize(self._cell_w * self._cols, self._cell_h * self._rows)
