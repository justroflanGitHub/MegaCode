"""Resolve "what to run in a terminal" into a concrete command line.

The launcher and the workspace's add-menu both turn a friendly kind
(``claude`` / ``powershell`` / ``cmd`` / ``custom``) into the executable that
:class:`megacode.conpty.Pty` spawns.
"""

from __future__ import annotations

import os
import shutil
from typing import List, Optional, Tuple

#: Friendly run types shown in the UI: (label, kind).
RUN_KINDS: List[Tuple[str, str]] = [
    ("Claude Code", "claude"),
    ("PowerShell", "powershell"),
    ("Command Prompt", "cmd"),
    ("Custom…", "custom"),
]

_PS_FALLBACK = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
_CMD_FALLBACK = r"C:\Windows\System32\cmd.exe"


def find_claude() -> Optional[str]:
    return shutil.which("claude") or shutil.which("claude.exe")


def find_powershell() -> Optional[str]:
    # Windows Terminal's default profile is Windows PowerShell (powershell.exe);
    # prefer that, then pwsh (PowerShell 7), then the well-known path.
    return (
        shutil.which("powershell")
        or shutil.which("pwsh")
        or (_PS_FALLBACK if os.path.exists(_PS_FALLBACK) else None)
    )


def find_cmd() -> str:
    return os.environ.get("COMSPEC") or _CMD_FALLBACK


def label_for(kind: str) -> str:
    return {"claude": "claude", "powershell": "powershell", "cmd": "cmd"}.get(kind, "term")


def resolve(kind: str, custom: Optional[str] = None) -> Optional[str]:
    """Return the command line for ``kind``, or ``None`` if it can't be found."""
    if kind == "claude":
        return find_claude()
    if kind == "powershell":
        return find_powershell()
    if kind == "cmd":
        return find_cmd()
    if kind == "custom":
        text = (custom or "").strip()
        if not text:
            return None
        # A bare executable name -> resolve via PATH; otherwise run as-is.
        if " " not in text:
            located = shutil.which(text)
            if located:
                return located
        return text
    return None
