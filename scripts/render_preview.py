"""Render an embedded terminal to a PNG to visually verify the painter (dev only)."""

from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from megacode.terminal_widget import TerminalWidget  # noqa: E402


def main() -> int:
    app = QApplication([])
    claude = shutil.which("claude") or shutil.which("claude.exe")
    if not claude:
        print("claude not found")
        return 1

    term = TerminalWidget(claude, cwd=os.getcwd(), font_size=12)
    term.resize(QSize(960, 540))
    # let resize event + claude output settle
    for _ in range(80):
        app.processEvents()
        time.sleep(0.1)

    # how much content is in pyte's screen right now?
    with term._lock:  # noqa: SLF001 (test)
        cols, rows = term._screen.columns, term._screen.lines
        nb = sum(
            1 for r in range(rows) for c in range(cols)
            if term._screen.buffer[r][c].data not in ("", " ")
        )
    print(f"pyte {cols}x{rows} non_blank={nb}", flush=True)

    pix = term.grab()
    img = pix.toImage()
    bg = img.pixelColor(2, 2)
    total = img.width() * img.height()
    non_bg = sum(
        1 for y in range(0, img.height(), 3) for x in range(0, img.width(), 3)
        if img.pixelColor(x, y).rgb() != bg.rgb()
    )
    print(f"image {img.width()}x{img.height()} sampled_non_bg={non_bg} (bg={bg.name()})", flush=True)
    out = os.path.join(os.path.dirname(__file__), "..", "docs", "terminal_preview.png")
    out = os.path.abspath(out)
    pix.save(out)
    print("saved:", out, pix.width(), "x", pix.height(), flush=True)
    term.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
