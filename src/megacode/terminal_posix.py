"""Separate-windows mode on Linux: spawn N external terminal windows tiled.

The Windows twin (:mod:`megacode.terminal`) drives Windows Terminal via
``wt.exe`` and HWND geometry. On X11 (Astra 1.7.6 runs the Fly desktop)
the portable strategy is *geometry at spawn*: xterm's ``-geometry
WxH+X+Y`` places each window exactly in its cell, with no window-manager
round-trips, no window-id discovery and no race -- the reason xterm is
the preferred (and on a minimal install, the only) target. Other
emulators (qterminal/konsole/...) are spawned untiled and then moved via
wmctrl/xdotool when those tools exist; without them the windows still
open, just unarranged.

The public surface mirrors terminal.launch_and_arrange so
:class:`megacode.app.SeparateWorker` is platform-neutral. ``monitor_hwnd``
is accepted and ignored (an X11 window id cannot select a monitor through
this path; the primary display is tiled).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Callable, List, Optional

from . import childenv, plat_helpers
from .layouts import SUPPORTED, compute_layout
from .shells import find_claude

log = logging.getLogger("megacode")

StatusCb = Callable[[str], None]

#: emulators that accept a geometry at spawn, most standard first
_GEOMETRY_TERMS = ("xterm", "uxterm")
#: emulators we can spawn (and tile via wmctrl when available)
_OTHER_TERMS = (
    ("qterminal", ["--workdir", "{cwd}"]),
    ("konsole", ["--workdir", "{cwd}"]),
    ("fly-terminal", []),
    ("gnome-terminal", []),
    ("xfce4-terminal", ["--working-directory", "{cwd}"]),
)


def available() -> bool:
    """True when some external terminal can be spawned."""
    return _pick_terminal() is not None


def _pick_terminal() -> Optional[str]:
    for name in _GEOMETRY_TERMS + tuple(name for name, _argv in _OTHER_TERMS):
        path = shutil.which(name)
        if path:
            return name
    return None


def _pick_claude_or_shell() -> Optional[str]:
    claude = find_claude()
    if claude:
        return claude
    return shutil.which(os.environ.get("SHELL", "/bin/bash")) or "/bin/bash"


def _geometry(rect) -> str:
    """-geometry for the tiling cell. WxH is COLUMNS x ROWS, not pixels
    (only +X+Y is pixels) -- estimate cells from a typical 10x20px cell and
    let the WM clamp; pixel-exact tiling is the wmctrl path below."""
    cols = max(4, rect.w // 10)
    rows = max(2, rect.h // 20)
    return "%dx%d+%d+%d" % (cols, rows, rect.x, rect.y)


def _spawn(term: str, command: str, title: str, cwd: str, rect=None) -> None:
    if term in _GEOMETRY_TERMS:
        argv = [term, "-T", title]
        if rect is not None:
            argv += ["-geometry", _geometry(rect)]
        argv += ["-e", command]
    else:
        spec = dict(_OTHER_TERMS).get(term, [])
        argv = [term] + [a.format(cwd=cwd) for a in spec] + ["-e", command]
    log.debug("launching: %s", argv)
    # scrubbed env: the emulator's shell must not inherit the frozen
    # bundle's LD_LIBRARY_PATH (see childenv) -- same leak as the PTY
    subprocess.Popen(
        argv, cwd=cwd, close_fds=True, start_new_session=True,
        env=childenv.scrub_child_env(os.environ),
    )


def launch_and_arrange(
    n: int,
    working_dir: str,
    gap: int = 6,
    monitor_hwnd: Optional[int] = None,  # noqa: ARG001 (X11 id, unused)
    status: Optional[StatusCb] = None,
) -> List[int]:
    """Launch ``n`` terminal windows running ``claude`` and tile them.

    Returns the list of X11 window ids that were found (possibly empty --
    geometry-at-spawn needs no ids; they are only reported for parity).
    """
    if n not in SUPPORTED:
        raise ValueError(f"Unsupported instance count: {n!r}. Supported: {SUPPORTED}")

    def say(message: str) -> None:
        log.info(message)
        if status:
            status(message)

    term = _pick_terminal()
    if not term:
        raise RuntimeError(
            "No external terminal found. Install one:  sudo apt install xterm")
    command = _pick_claude_or_shell()
    if not command:
        raise RuntimeError("Neither 'claude' nor a shell was found.")
    if not os.path.isdir(working_dir):
        raise RuntimeError(f"Working folder does not exist: {working_dir}")

    area = plat_helpers.primary_work_area()
    rects = compute_layout(n, area, gap)
    say(f"Launching {n} {term} window{'s' if n != 1 else ''}...")

    windows: List[int] = []
    can_geometry = term in _GEOMETRY_TERMS
    before = set(plat_helpers.find_terminal_windows()) if not can_geometry else set()
    for i in range(n):
        _spawn(
            term, command, f"Claude {i + 1}/{n}", working_dir,
            rects[i] if can_geometry else None,
        )
        time.sleep(0.25)  # predictable stacking order

    if can_geometry:
        say(f"Done - {n} window{'s' if n != 1 else ''} tiled "
            f"({area.w}x{area.h}).")
        return []

    # non-geometry emulator: discover the new windows and move them
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        current = set(plat_helpers.find_terminal_windows())
        windows = sorted(current - before)
        if len(windows) >= n:
            break
        time.sleep(0.35)
    for wid, rect in zip(windows, rects):
        plat_helpers.move_window(wid, rect)
    if len(windows) < n:
        say(f"Only {len(windows)} of {n} windows could be arranged.")
    else:
        say(f"Done - {n} window{'s' if n != 1 else ''} arranged.")
    return windows
