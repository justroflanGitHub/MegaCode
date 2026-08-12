"""Verify the underline filter, Shift+Tab encoding and grid-stretch fix (dev only)."""

from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from megacode.terminal_widget import TerminalWidget  # noqa: E402
from megacode.workspace import WorkspaceView  # noqa: E402


def check_underline_and_keys() -> bool:
    claude = shutil.which("claude") or shutil.which("claude.exe")
    term = TerminalWidget(claude, cwd=os.getcwd(), font_size=12)
    for _ in range(70):
        QApplication.processEvents()
        time.sleep(0.1)

    with term._lock:  # noqa: SLF001 (test)
        lines, cols = term._screen.lines, term._screen.columns
        under = sum(1 for r in range(lines) for c in range(cols)
                    if term._screen.buffer[r][c].underscore)
        non_blank = sum(1 for r in range(lines) for c in range(cols)
                        if term._screen.buffer[r][c].data not in ("", " "))
    print(f"underline cells: {under} (expect 0); non_blank: {non_blank} (expect >200)")

    ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
    seq = term._encode_key(ev)
    print(f"Shift+Tab -> {seq!r} (expect '\\x1b[Z')")
    ev_tab = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier)
    print(f"Tab      -> {term._encode_key(ev_tab)!r} (expect '\\t')")
    term.close()
    return under == 0 and non_blank > 200 and seq == "\x1b[Z"


def check_grid_stretch() -> bool:
    ws = WorkspaceView()
    ws.start(2, r"C:\Windows\System32\cmd.exe", os.getcwd())
    QApplication.processEvents()
    ws.add_tile()
    ws.add_tile()  # now 4 terminals
    g = ws._grid  # noqa: SLF001 (test)
    stretches = [g.columnStretch(c) for c in range(g.columnCount())]
    print(f"tiles: {len(ws.tiles)}; grid columns: {g.columnCount()}; col stretches: {stretches}")
    ws.cleanup()
    # 4 terminals -> 2x2: only columns 0 and 1 should keep a non-zero stretch
    return len(ws.tiles) == 0 and stretches[:2] == [1, 1] and all(s == 0 for s in stretches[2:])


def main() -> int:
    app = QApplication([])
    ok1 = check_underline_and_keys()
    ok2 = check_grid_stretch()
    print("RESULT:", "PASS" if ok1 and ok2 else "CHECK", f"(underline/keys={ok1}, grid={ok2})")
    return 0 if ok1 and ok2 else 2


if __name__ == "__main__":
    sys.exit(main())
