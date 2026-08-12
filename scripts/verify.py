"""End-to-end functional check on a real Windows desktop.

Launches N Claude Code windows, reads their final on-screen rectangles back,
compares them against the expected tiling, holds for a moment so you can see
the result, then closes them. Run from the project root:

    .venv/Scripts/python.exe scripts/verify.py [count]

Default count is 2 to keep it unobtrusive. Pass 2, 3, 4 or 6.
"""

from __future__ import annotations

import os
import sys
import time

# The Windows console defaults to cp1251; force UTF-8 so Unicode status
# messages print cleanly regardless of the active code page.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from megacode import terminal as T  # noqa: E402
from megacode import win32_helpers as w32  # noqa: E402
from megacode.layouts import compute_layout  # noqa: E402


def main(n: int = 2, gap: int = 6, hold: float = 4.0) -> int:
    cwd = os.getcwd()
    hwnds = T.launch_and_arrange(
        n, cwd, gap=gap, status=lambda msg: print(f"[status] {msg}")
    )
    if not hwnds:
        print("FAIL: no windows were created/detected")
        return 1

    time.sleep(1.5)  # let the move settle
    area = w32.get_work_area(hwnds[0])
    expected = compute_layout(n, area, gap)
    print(f"\nwork area: {area}")
    ok = True
    for hwnd, exp in zip(hwnds, expected):
        got = w32.get_window_rect(hwnd)
        match = got and _close(got, exp, tol=12)
        ok = ok and bool(match)
        print(f"  hwnd {hwnd}: expected {exp} | got {got} | {'OK' if match else 'MISMATCH'}")

    print(f"\nHolding {hold}s so you can inspect, then closing the windows…")
    time.sleep(hold)
    for hwnd in hwnds:
        w32.close_window(hwnd)

    print("PASS" if ok else "CHECK positions above")
    return 0 if ok else 2


def _close(a, b, tol: int) -> bool:
    return (
        abs(a.x - b.x) <= tol
        and abs(a.y - b.y) <= tol
        and abs(a.w - b.w) <= tol
        and abs(a.h - b.h) <= tol
    )


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    if count not in (2, 3, 4, 6):
        print("count must be one of 2, 3, 4, 6")
        sys.exit(3)
    raise SystemExit(main(count))
