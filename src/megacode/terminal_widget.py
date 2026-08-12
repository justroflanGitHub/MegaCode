"""An embeddable terminal widget: ConPTY child + pyte emulator + QPainter.

Renders a real process (Claude Code, a shell, ...) inside a QWidget by feeding
the pseudo-console's VT output stream into ``pyte`` and painting the resulting
screen grid. Keystrokes are encoded back into VT sequences and written to the
child. This is what makes true drag-to-swap possible: the running session lives
inside this widget, so moving the widget moves the session.
"""

from __future__ import annotations

import re
import threading
from typing import List, Optional

import pyte
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPalette
from PySide6.QtWidgets import QMenu, QWidget

from .conpty import Pty

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

# --- theme & palette --------------------------------------------------------
_DEFAULT_FG = "#d4d4d4"
_DEFAULT_BG = "#1e1e1e"
_SELECTION = QColor(255, 255, 255, 40)

# pyte emits the 16 ANSI colours by name; everything 256/truecolour comes as a
# 6-digit hex string. (pyte maps SGR 33 -> "brown".)
_PALETTE = {
    "black": "#0c0c0c", "red": "#c50f1f", "green": "#13a10e",
    "brown": "#c19c00", "yellow": "#c19c00", "blue": "#0037da",
    "magenta": "#881798", "cyan": "#3a96dd", "white": "#cccccc",
    "brightblack": "#767676", "brightred": "#e74856", "brightgreen": "#16c60c",
    "brightyellow": "#f9f1a5", "brightblue": "#3b78ff", "brightmagenta": "#b4009e",
    "brightcyan": "#61d6d6", "brightwhite": "#f2f2f2",
}
_HEX_DIGITS = set("0123456789abcdef")


def _resolve_color(spec: str, default: QColor) -> QColor:
    if not spec or spec == "default":
        return default
    if spec in _PALETTE:
        return QColor(_PALETTE[spec])
    if len(spec) == 6 and all(ch in _HEX_DIGITS for ch in spec):
        return QColor("#" + spec)
    return default


class TerminalWidget(QWidget):
    """A single embedded terminal surface."""

    #: Emitted when the child process exits.
    finished = Signal()

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
        # fallbacks) so glyphs and metrics match what the user expects.
        self._font = QFont("Cascadia Mono", font_size)
        self._font.setFamilies(
            ["Cascadia Mono", "Cascadia Code", "Cascadia Mono NF", "Consolas",
             "DejaVu Sans Mono", "Courier New"]
        )
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        self._font.setFixedPitch(True)
        metrics = QFontMetrics(self._font)
        self._cell_w = max(1, metrics.horizontalAdvance("M"))
        self._cell_h = max(1, metrics.lineSpacing())
        self._ascent = metrics.ascent()

        self._cols, self._rows = 80, 24
        self._lock = threading.Lock()
        self._screen = pyte.Screen(self._cols, self._rows)
        self._stream = pyte.Stream(self._screen)
        self._pty_buf = ""

        self._pty = Pty(command, self._cols, self._rows, cwd=cwd)
        self._pty.start(self._on_output)

        self._default_fg = QColor(_DEFAULT_FG)
        self._default_bg = QColor(_DEFAULT_BG)
        # text selection + copy/paste
        self._sel_active = False
        self._sel_start: Optional[tuple[int, int]] = None
        self._sel_end: Optional[tuple[int, int]] = None
        # private modes parsed from the output stream
        self._priv_modes: set[int] = set()
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, self._default_bg)
        palette.setColor(QPalette.ColorRole.Base, self._default_bg)
        palette.setColor(QPalette.ColorRole.Text, self._default_fg)
        palette.setColor(QPalette.ColorRole.WindowText, self._default_fg)
        self.setPalette(palette)

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
    def _mouse_on(self) -> bool:
        return bool(self._priv_modes & _MOUSE_TRACKING)

    # --- painting -----------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt signature)
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._default_bg)

        with self._lock:
            lines = self._screen.lines
            columns = self._screen.columns
            buffer = self._screen.buffer
            cursor_x = self._screen.cursor.x
            cursor_y = self._screen.cursor.y
            cursor_hidden = self._screen.cursor.hidden
            # snapshot immutable Char references; safe to use after unlock
            snapshot = [
                [buffer[r][c] for c in range(columns)] for r in range(lines)
            ]

        painter.setFont(self._font)
        for row in range(min(lines, self._rows_for_height())):
            chars = snapshot[row] if row < len(snapshot) else []
            self._draw_row(painter, row, chars, columns)

        if not cursor_hidden and not self._dead and 0 <= cursor_y < lines and 0 <= cursor_x < columns:
            self._draw_cursor(painter, cursor_x, cursor_y, snapshot[cursor_y][cursor_x])

        self._draw_selection(painter)

        if self._dead:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 170))
            painter.setPen(QColor("#cccccc"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "[ process exited ]")

    def _draw_selection(self, painter: QPainter) -> None:
        if not self._sel_active or self._sel_start is None or self._sel_end is None:
            return
        for r, c1, c2 in self._selection_ranges():
            x = c1 * self._cell_w
            y = r * self._cell_h
            painter.fillRect(x, y, (c2 - c1 + 1) * self._cell_w, self._cell_h, _SELECTION)

    def _draw_row(self, painter: QPainter, row: int, chars: list, columns: int) -> None:
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
            run_chars = []
            run_end = col
            while run_end < max_cols:
                c = chars[run_end]
                if (c.fg != fg_spec or c.bg != bg_spec or bool(c.bold) != bool(bold)
                        or bool(c.underscore) != bool(underline) or bool(c.reverse) != bool(reverse)):
                    break
                run_chars.append(c.data)
                run_end += 1
            self._draw_run(painter, col, y, run_chars, fg_spec, bg_spec, bold, underline, reverse)
            col = run_end if run_end > col else col + 1

    def _draw_run(
        self, painter: QPainter, col: int, y: int, run_chars: List[str],
        fg_spec: str, bg_spec: str, bold: bool, underline: bool, reverse: bool,
    ) -> None:
        text = "".join(run_chars)
        x = col * self._cell_w
        fg = _resolve_color(fg_spec, self._default_fg)
        bg = _resolve_color(bg_spec, self._default_bg)
        if reverse:
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

    def _draw_cursor(self, painter: QPainter, col: int, row: int, char) -> None:
        x = col * self._cell_w
        y = row * self._cell_h
        fg = _resolve_color(char.fg, self._default_fg)
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

        seq = self._encode_key(event)
        if seq:
            self._pty.write(seq)
        else:
            super().keyPressEvent(event)

    # --- mouse: selection / forwarding / paste -----------------------------
    def _cell_at(self, pos) -> tuple[int, int]:
        col = max(0, min(self._cols - 1, int(pos.x()) // self._cell_w))
        row = max(0, min(self._rows - 1, int(pos.y()) // self._cell_h))
        return (row, col)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        if self._mouse_on:
            self._send_mouse(event, pressed=True)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._sel_start = self._sel_end = self._cell_at(event.position())
            self._sel_active = True
            self.update()
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.paste()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        if self._mouse_on:
            if event.buttons() & Qt.MouseButton.LeftButton:
                self._send_mouse(event, pressed=True)
            return
        if self._sel_active and (event.buttons() & Qt.MouseButton.LeftButton):
            self._sel_end = self._cell_at(event.position())
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        if self._mouse_on:
            if event.button() == Qt.MouseButton.LeftButton:
                self._send_mouse(event, pressed=False)
            return
        if event.button() == Qt.MouseButton.LeftButton and self._sel_active:
            self._sel_end = self._cell_at(event.position())
            self.update()
            text = self._selection_text()
            if text:
                self._copy_text(text)  # copy-on-select

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        menu = QMenu(self)
        act_copy = menu.addAction("Copy")
        act_paste = menu.addAction("Paste")
        chosen = menu.exec(event.globalPos())
        if chosen is act_copy:
            text = self._selection_text()
            if text:
                self._copy_text(text)
        elif chosen is act_paste:
            self.paste()

    def _send_mouse(self, event, pressed: bool) -> None:
        # SGR mouse encoding: ESC[<{button};{col};{row}M|m
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
        final = "M" if pressed else "m"
        self._pty.write(f"\x1b[<{code};{col + 1};{row + 1}{final}")

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
        with self._lock:
            lines = self._screen.lines
            columns = self._screen.columns
            buffer = self._screen.buffer
            parts = []
            for r, c1, c2 in self._selection_ranges():
                if r >= lines:
                    break
                c2 = min(c2, columns - 1)
                row_text = "".join(buffer[r][c].data for c in range(c1, c2 + 1))
                parts.append(row_text.rstrip())
        return "\n".join(parts)

    def _clear_selection(self) -> None:
        self._sel_active = False
        self._sel_start = self._sel_end = None
        self.update()

    def _copy_text(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)

    def paste(self) -> None:
        text = QGuiApplication.clipboard().text()
        if not text:
            return
        # terminals use CR, not LF
        text = text.replace("\r\n", "\r").replace("\n", "\r")
        if self._bracketed_paste:
            text = f"\x1b[200~{text}\x1b[201~"
        self._pty.write(text)

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
                self._screen.resize(lines=new_rows, columns=new_cols)
            self._pty.resize(new_cols, new_rows)
        super().resizeEvent(event)

    def _cols_for_width(self) -> int:
        return self.width() // self._cell_w

    def _rows_for_height(self) -> int:
        return self.height() // self._cell_h

    # --- lifecycle ----------------------------------------------------------
    def is_dead(self) -> bool:
        return self._dead

    def tick(self) -> None:
        """Called on a timer to detect child exit and refresh the cursor."""
        if not self._dead and not self._pty.is_alive():
            self._dead = True
            self.finished.emit()
        self.update()

    def close(self) -> None:
        self._pty.stop()

    def sizeHint(self):  # noqa: N802 (Qt signature)
        from PySide6.QtCore import QSize

        return QSize(self._cell_w * self._cols, self._cell_h * self._rows)
