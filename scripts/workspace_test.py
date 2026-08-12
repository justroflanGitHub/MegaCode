"""Headless end-to-end check of the workspace (dev only).

Creates a WorkspaceView with two embedded Claude terminals (offscreen Qt),
lets them render, prints each tile's screen, then verifies that a swap
reorders the tiles without disturbing their sessions.
"""

from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from megacode.workspace import WorkspaceView  # noqa: E402


def screen_summary(term) -> tuple[str, int]:
    with term._lock:  # noqa: SLF001 (test)
        lines = list(term._screen.display)
        cols, rows = term._screen.columns, term._screen.lines
        non_blank = sum(
            1 for r in range(rows) for c in range(cols)
            if term._screen.buffer[r][c].data not in ("", " ")
        )
    return "\n".join(line.rstrip() for line in lines), non_blank


def main() -> int:
    app = QApplication([])
    claude = shutil.which("claude") or shutil.which("claude.exe")
    if not claude:
        print("claude not found")
        return 1

    ws = WorkspaceView()
    ws.start(2, claude, os.getcwd(), font_size=11)
    time.sleep(7.0)

    ok = True
    for i, tile in enumerate(ws.tiles):
        text, non_blank = screen_summary(tile.terminal)
        has_ui = "Claude" in text or "claude" in text or ">" in text or "❯" in text
        ok = ok and has_ui and non_blank > 200
        print(f"--- tile {i} index={tile.index} non_blank={non_blank} ui={has_ui} ---", flush=True)
        print(text[:240], flush=True)

    order_before = [id(t) for t in ws.tiles]
    ws._on_swap(1, 0)  # noqa: SLF001 (test)
    order_after = [id(t) for t in ws.tiles]
    swapped = order_after == list(reversed(order_before))
    print("swap reordered tiles:", swapped, flush=True)
    ok = ok and swapped

    ws.cleanup()
    print("RESULT:", "PASS" if ok else "CHECK", flush=True)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
