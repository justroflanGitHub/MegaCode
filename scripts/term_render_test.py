"""Headless check: does pyte render Claude Code's TUI sensibly? (dev only)

Runs claude via ConPTY, feeds the VT stream into a pyte screen, waits, then
prints the rendered rows. If claude's UI elements show up here, the QPainter
renderer (which paints the same pyte screen) will show them too.
"""

from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pyte  # noqa: E402

from megacode.conpty import Pty  # noqa: E402


def main(command: str, cwd: str) -> int:
    cols, rows = 100, 30
    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)

    pty = Pty(command, cols, rows, cwd=cwd)
    pty.start(stream.feed)
    time.sleep(6.0)
    pty.stop()
    time.sleep(0.3)

    print("=" * cols)
    for line in screen.display:
        print(line.rstrip())
    print("=" * cols)

    # crude richness check: distinct non-blank characters across the screen
    text = "\n".join(screen.display)
    distinct = len({(r, c): screen.buffer[r][c].data for r in range(rows) for c in range(cols)
                    if screen.buffer[r][c].data not in ("", " ")})
    print(f"non-blank cells: {distinct}")
    print(f"looks like a TUI: {any(ch in text for ch in ('Claude', 'claude', '?', '>')) and distinct > 200}")
    return 0


if __name__ == "__main__":
    claude = shutil.which("claude") or shutil.which("claude.exe")
    if not claude:
        print("claude not found")
        sys.exit(1)
    cmd = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "claude" else claude
    cwd = sys.argv[2] if len(sys.argv) > 2 else os.getcwd()
    sys.exit(main(cmd, cwd))
