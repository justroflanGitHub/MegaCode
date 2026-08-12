"""Launch Claude Code in Windows Terminal windows and tile them on screen.

Strategy
--------
Windows Terminal runs as a *single* ``WindowsTerminal.exe`` process that can
own many independent top-level windows (one per ``wt -w 0`` call). That breaks
the usual "one process -> one window" assumption, so we cannot simply map a
spawned PID to a window. Instead we:

1. Snapshot existing Windows Terminal windows.
2. Launch ``n`` fresh windows (``-w 0`` forces a new window each time).
3. Poll until ``n`` *new* windows appear (diff against the snapshot).
4. Tile them with :func:`megacode.layouts.compute_layout`.

The window-to-slot mapping does not matter because every window runs an
identical Claude Code session.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Callable, List, Optional

from . import win32_helpers as w32
from .layouts import SUPPORTED, compute_layout

log = logging.getLogger("megacode")

#: Status callback type: ``(message) -> None``.
StatusCb = Callable[[str], None]

_CREATE_NEW_PROCESS_GROUP = 0x00000200


def find_wt() -> Optional[str]:
    """Locate ``wt.exe`` (the Windows Terminal launcher), or ``None``."""
    path = shutil.which("wt") or shutil.which("wt.exe")
    if path:
        return path
    localappdata = os.environ.get("LOCALAPPDATA", "")
    candidate = os.path.join(localappdata, "Microsoft", "WindowsApps", "wt.exe")
    return candidate if os.path.exists(candidate) else None


def find_claude() -> Optional[str]:
    """Locate the ``claude`` executable on PATH, or ``None``."""
    return shutil.which("claude") or shutil.which("claude.exe")


def _launch_one(wt: str, claude: str, working_dir: str, index: int, total: int) -> None:
    """Open a single new Windows Terminal window running ``claude``."""
    title = f"Claude {index + 1}/{total}"
    # ``-w new`` forces a brand-new top-level window every time, overriding a
    # user's ``windowingBehavior`` setting that would otherwise merge the call
    # into an existing window as a tab. (``-w 0`` does NOT do this reliably.)
    cmd = [wt, "-w", "new", "new-tab", "--title", title, "-d", working_dir, claude]
    log.debug("launching: %s", cmd)
    subprocess.Popen(cmd, close_fds=True, creationflags=_CREATE_NEW_PROCESS_GROUP)


def _wait_for_new_windows(
    before: set[int], expected: int, timeout: float = 20.0, interval: float = 0.35
) -> List[int]:
    """Poll until ``expected`` new Windows Terminal hwnds appear."""
    deadline = time.monotonic() + timeout
    found: List[int] = []
    while time.monotonic() < deadline:
        current = set(w32.find_terminal_windows())
        found = sorted(current - before)
        if len(found) >= expected:
            return found[:expected]
        time.sleep(interval)
    log.warning("timed out waiting for windows: expected %d, found %d", expected, len(found))
    return found[:expected]


def launch_and_arrange(
    n: int,
    working_dir: str,
    gap: int = 6,
    monitor_hwnd: Optional[int] = None,
    status: Optional[StatusCb] = None,
) -> List[int]:
    """Launch ``n`` Claude Code windows and tile them on the monitor.

    Args:
        n: Number of instances; must be one of :data:`megacode.layouts.SUPPORTED`.
        working_dir: Folder each Claude Code session starts in.
        gap: Pixels of spacing between tiled windows.
        monitor_hwnd: Window whose monitor should be tiled. ``None`` -> the
            monitor of the first launched terminal, falling back to primary.
        status: Optional callback for human-readable progress updates.

    Returns:
        The list of arranged window handles (may be shorter than ``n`` if some
        windows failed to appear).

    Raises:
        ValueError: if ``n`` is unsupported.
        RuntimeError: if Windows Terminal or Claude Code cannot be found, or the
            working directory does not exist.
    """
    if n not in SUPPORTED:
        raise ValueError(f"Unsupported instance count: {n!r}. Supported: {SUPPORTED}")

    def say(message: str) -> None:
        log.info(message)
        if status:
            status(message)

    wt = find_wt()
    if not wt:
        raise RuntimeError("Windows Terminal (wt.exe) was not found on this system.")
    claude = find_claude()
    if not claude:
        raise RuntimeError("claude was not found on PATH. Install Claude Code first.")
    if not os.path.isdir(working_dir):
        raise RuntimeError(f"Working folder does not exist: {working_dir}")

    say(f"Preparing {n} instance{'s' if n != 1 else ''} in {working_dir}")
    before = set(w32.find_terminal_windows())

    say(f"Launching {n} Windows Terminal window{'s' if n != 1 else ''}...")
    for i in range(n):
        _launch_one(wt, claude, working_dir, i, n)
        time.sleep(0.25)  # stagger so windows are created in a predictable order

    say("Waiting for the windows to open...")
    hwnds = _wait_for_new_windows(before, n)
    if len(hwnds) < n:
        say(f"Only {len(hwnds)} of {n} windows appeared; arranging what we have.")

    if not hwnds:
        say("No terminal windows were detected. Nothing to arrange.")
        return []

    reference = monitor_hwnd if monitor_hwnd else hwnds[0]
    area = w32.get_work_area(reference)
    rects = compute_layout(n, area, gap)
    say(f"Arranging on monitor ({area.w}x{area.h})...")

    for hwnd, rect in zip(hwnds, rects):
        w32.move_window(hwnd, rect)

    say(f"Done - {len(hwnds)} instance{'s' if len(hwnds) != 1 else ''} arranged.")
    return hwnds
