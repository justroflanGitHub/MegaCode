"""Resolve "what to run in a terminal" into a concrete command line.

The launcher and the workspace's add-menu both turn a friendly kind
(``claude`` / shell / ``custom``) into the executable that the platform's
Pty backend (:mod:`megacode.conpty` on Windows, :mod:`megacode.unixpty`
on Linux) spawns. Kind TOKENS are stable across platforms (they persist
in settings and tile state); which shells the menu offers and how a kind
resolves is platform-specific -- on Linux the legacy "powershell"/"cmd"
kinds alias the user's login shell so stale saved preferences still run.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import List, Optional, Tuple

if sys.platform == "win32":
    #: Friendly run types shown in the UI: (label, kind).
    RUN_KINDS: List[Tuple[str, str]] = [
        ("Claude Code", "claude"),
        ("PowerShell", "powershell"),
        ("Command Prompt", "cmd"),
        ("AI Chat", "chat"),
        ("Custom…", "custom"),
    ]
    #: The launcher's preselected kind.
    DEFAULT_KIND = "cmd"
else:
    RUN_KINDS = [
        ("Claude Code", "claude"),
        ("Bash", "bash"),
        ("AI Chat", "chat"),
        ("Custom…", "custom"),
    ]
    DEFAULT_KIND = "bash"

_PS_FALLBACK = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
_CMD_FALLBACK = r"C:\Windows\System32\cmd.exe"
_SHELL_FALLBACK = "/bin/bash"

# A GUI session launched from the desktop menu exports a minimal PATH that
# lacks ~/.local/bin and friends (typical on an air-gapped Astra install),
# so probe the usual npm/global install spots before giving up.
_CLAUDE_PROBE_POSIX = (
    "~/.local/bin/claude",
    "~/.npm-global/bin/claude",
    "~/bin/claude",
    "/usr/local/bin/claude",
    "/usr/bin/claude",
    "/opt/claude/bin/claude",
)


def find_claude() -> Optional[str]:
    found = shutil.which("claude") or shutil.which("claude.exe")
    if found:
        return found
    if sys.platform != "win32":
        home = os.path.expanduser("~")
        for probe in _CLAUDE_PROBE_POSIX:
            candidate = os.path.expanduser(probe)
            if candidate.startswith(home) and os.access(candidate, os.X_OK):
                return candidate
        # nvm keeps one bin dir per node version; take the newest that has
        # it (numeric compare: lexicographic puts 'v10' above 'v9')
        nvm = os.path.join(home, ".nvm", "versions", "node")
        if os.path.isdir(nvm):
            def _verkey(v: str):
                return tuple(int(p) if p.isdigit() else 0
                             for p in v.lstrip("vV").split("."))

            try:
                versions = sorted(os.listdir(nvm), key=_verkey, reverse=True)
            except OSError:
                versions = []
            for version in versions:
                candidate = os.path.join(nvm, version, "bin", "claude")
                if os.access(candidate, os.X_OK):
                    return candidate
    return None


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


def find_shell() -> Optional[str]:
    """The user's interactive shell: ``$SHELL``, then the passwd entry,
    then /bin/bash (Astra 1.7.6 always ships bash)."""
    shell = os.environ.get("SHELL")
    if shell and (os.path.isfile(shell) or shutil.which(shell)):
        return shell
    if sys.platform != "win32":
        try:
            import pwd

            pw_shell = pwd.getpwuid(os.getuid()).pw_shell
            if pw_shell and os.path.isfile(pw_shell):
                return pw_shell
        except (ImportError, KeyError, OSError):
            pass
    return _SHELL_FALLBACK if os.path.exists(_SHELL_FALLBACK) else "/bin/sh"


def label_for(kind: str) -> str:
    return {
        "claude": "claude",
        "powershell": "powershell",
        "cmd": "cmd",
        "bash": "bash",
        "chat": "chat",
    }.get(kind, "term")


def resolve(kind: str, custom: Optional[str] = None) -> Optional[str]:
    """Return the command line for ``kind``, or ``None`` if it can't be found."""
    if kind == "claude":
        return find_claude()
    if kind == "chat":
        # the chat tile talks to the claude CLI over stdio (no PTY)
        return find_claude()
    if kind == "bash":
        return find_shell()
    if kind == "powershell":
        # Windows-native; on Linux a saved "powershell" kind falls back to
        # the login shell rather than breaking the pane
        return find_powershell() if sys.platform == "win32" else find_shell()
    if kind == "cmd":
        return find_cmd() if sys.platform == "win32" else find_shell()
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
