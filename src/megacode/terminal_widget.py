"""An embeddable terminal widget: ConPTY child + pyte emulator + QPainter.

Renders a real process (Claude Code, a shell, ...) inside a QWidget by feeding
the pseudo-console's VT output stream into ``pyte`` and painting the resulting
screen grid. Keystrokes are encoded back into VT sequences and written to the
child. This is what makes true drag-to-swap possible: the running session lives
inside this widget, so moving the widget moves the session.
"""

from __future__ import annotations

import threading
from typing import List, Optional

import pyte
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import QWidget

from .conpty import Pty

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

        self._font = QFont("Consolas", font_size)
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        metrics = QFontMetrics(self._font)
        self._cell_w = max(1, metrics.horizontalAdvance("M"))
        self._cell_h = max(1, metrics.lineSpacing())
        self._ascent = metrics.ascent()

        self._cols, self._rows = 80, 24
        self._lock = threading.Lock()
        self._screen = pyte.Screen(self._cols, self._rows)
        self._stream = pyte.Stream(self._screen)

        self._pty = Pty(command, self._cols, self._rows, cwd=cwd)
        self._pty.start(self._on_output)

        self._default_fg = QColor(_DEFAULT_FG)
        self._default_bg = QColor(_DEFAULT_BG)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, self._default_bg)
        palette.setColor(QPalette.ColorRole.Base, self._default_bg)
        palette.setColor(QPalette.ColorRole.Text, self._default_fg)
        palette.setColor(QPalette.ColorRole.WindowText, self._default_fg)
        self.setPalette(palette)

        self._dead = False

    # --- PTY output -> pyte -> repaint --------------------------------------
    def _on_output(self, text: str) -> None:
        with self._lock:
            self._stream.feed(text)
        # signal crosses from the reader thread to the GUI thread (queued)
        self.update()

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

        if self._dead:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 170))
            painter.setPen(QColor("#cccccc"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "[ process exited ]")

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
        f = QFont(self._font)
        if bold:
            f.setBold(True)
        if underline:
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
    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        seq = self._encode_key(event)
        if seq:
            self._pty.write(seq)
        else:
            super().keyPressEvent(event)

    def _encode_key(self, event) -> Optional[str]:
        key = event.key()
        mods = event.modifiers()
        text = event.text()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)

        if ctrl and Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            return chr(key - Qt.Key.Key_A + 1)

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
