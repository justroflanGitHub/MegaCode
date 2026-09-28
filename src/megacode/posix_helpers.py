"""POSIX twins of :mod:`megacode.win32_helpers`' public surface.

Everything the sync link and the launcher need, on Linux: pid liveness
(``/proc`` + ``os.kill(pid, 0)``), the "elevated" concept (root), the
per-login session discriminator, owner-only file hardening (plain mode
bits -- root bypasses discretionary checks the way SYSTEM did), the
Unix-socket rendezvous path, and the network-filesystem refusal. The
X11 window helpers are best-effort via ``xdotool``/``wmctrl`` (only the
legacy separate-windows mode wants them); they fail soft when the tools
are absent.

Import this module only on POSIX -- :mod:`megacode.plat_helpers` picks
the right sibling per ``sys.platform``.
"""

from __future__ import annotations

import os
import pwd
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from .layouts import Rect

# fstypes on which QLockFile (O_EXCL + fcntl) and the HMAC secret must not
# live: no atomic advisory locking, and the secret would sit on a share
_NETFS_TYPES = frozenset({
    "nfs", "nfs4", "cifs", "smbfs", "fuse.sshfs", "fuse.cephfs", "ncpfs",
})


def _proc_text(pid: int, what: str) -> Optional[str]:
    try:
        with open("/proc/%d/%s" % (pid, what), "r", encoding="ascii",
                  errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


# --- process helpers --------------------------------------------------------
def get_process_path(pid: int) -> Optional[str]:
    """Full executable path for ``pid``, or None (Linux appends
    ' (deleted)' when the binary was replaced -- strip it)."""
    try:
        exe = os.readlink("/proc/%d/exe" % pid)
    except OSError:
        return None
    return exe[:-len(" (deleted)")] if exe.endswith(" (deleted)") else exe


def get_process_name(pid: int) -> Optional[str]:
    path = get_process_path(pid)
    return os.path.basename(path).lower() if path else None


# --- session / identity -----------------------------------------------------
def user_name() -> str:
    """The login name (hashes into the rendezvous name on the bus)."""
    name = os.environ.get("USER") or os.environ.get("LOGNAME")
    if name:
        return name
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return "user"


def process_session_id() -> str:
    """A stable-per-login, distinct-across-logins id (the RDP/fast-switch
    isolation the Windows build got from ProcessIdToSessionId).

    XDG_SESSION_ID (set by pam_systemd, one per seat login) first, else the
    process session id from getsid -- every process of one login shares it
    and two concurrent seats of one user differ."""
    sid = os.environ.get("XDG_SESSION_ID")
    if sid:
        return sid
    try:
        return str(os.getsid(0))
    except OSError:
        return "0"


def is_process_elevated() -> bool:
    """True when running as root -- the Unix analog of Administrator."""
    return os.geteuid() == 0


def harden_user_only(path: str) -> bool:
    """Owner-only access: mode 0600 (root needs no explicit grant, and
    plain mode bits never inherit, so there is no PROTECTED-DACL analog).
    The sync state directory is 0700, which is the primary boundary."""
    try:
        os.chmod(path, 0o600)
        return True
    except OSError:
        return False


def socket_path(name: str) -> str:
    """Turn a rendezvous name into an absolute AF_UNIX socket path.

    Qt's QLocalServer honors an absolute path verbatim; a bare name would
    land in /tmp where every local user can create squatters. XDG_RUNTIME_DIR
    (/run/user/<uid>, 0700 tmpfs, wiped on logout -- so no stale sockets
    survive a reboot) is the right home; a private cache dir is the
    non-systemd fallback. sockaddr_un.sun_path caps at ~107 bytes -- a
    deeply nested HOME could overflow it, so check and fall back to a
    short /tmp name (uid-scoped; 0700 socket + HMAC keep it safe)."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        candidate = os.path.join(runtime, name + ".sock")
        if len(candidate) <= 100:
            return candidate
    base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    base = base / "megacode-sockets"
    try:
        base.mkdir(parents=True, exist_ok=True)
        os.chmod(str(base), 0o700)
    except OSError:
        pass
    candidate = str(base / (name + ".sock"))
    if len(candidate) <= 100:
        return candidate
    return "/tmp/megacode-%d-%s.sock" % (os.getuid(), name)


def is_network_dir(path: str) -> bool:
    """True when ``path`` lives on a network filesystem.

    Parsed from /proc/self/mountinfo: the entry whose mount point is the
    longest prefix of the resolved path owns it. Fail-open (False) when
    mountinfo is unreadable -- refusing to link for everyone because one
    file is unparsable trades correctness for nothing."""
    try:
        resolved = os.path.realpath(path)
        best_point, best_fstype = "", ""
        with open("/proc/self/mountinfo", "r", encoding="utf-8") as fh:
            for line in fh:
                # <id> <parent> <maj:min> <root> <mountpoint> <options>...
                parts = line.split()
                if len(parts) < 6:
                    continue
                mountpoint = parts[4].replace("\\040", " ").replace("\\011", "\t")
                fstype = ""
                if "-" in parts[5:]:
                    dash = parts.index("-", 6)
                    if dash + 1 < len(parts):
                        fstype = parts[dash + 1]
                if (resolved == mountpoint
                        or resolved.startswith(mountpoint.rstrip("/") + "/")) \
                        and len(mountpoint) > len(best_point):
                    best_point, best_fstype = mountpoint, fstype
        return best_fstype in _NETFS_TYPES
    except OSError:
        return False


def is_pid_alive(pid: int) -> bool:
    """True when ``pid`` names a live, unexited process.

    os.kill(pid, 0) probes existence; EPERM means the process exists but is
    foreign (alive). pid <= 0 must be rejected FIRST: kill(0, 0) targets the
    caller's process group and kill(-1, 0) every process on the machine. A
    zombie ('Z' in /proc/<pid>/stat) counts as dead -- it has exited, only
    the exit status awaits reaping, matching the Windows STILL_ACTIVE check.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    stat = _proc_text(pid, "stat")
    if stat:
        # field 3 (state) follows the parenthesized comm, which may itself
        # contain spaces -- split after the last ')'
        tail = stat.rpartition(")")[2].split()
        if tail and tail[0] == "Z":
            return False
    return True


# --- X11 window helpers (best-effort; legacy separate-windows mode) ----------
def _tool(name: str) -> Optional[str]:
    from shutil import which

    return which(name)


def enum_top_level_windows() -> List[Tuple[int, int]]:
    """``[(x11 window id, pid), ...]`` for visible top-level windows, via
    xdotool; empty when xdotool is not installed."""
    xdotool = _tool("xdotool")
    if not xdotool:
        return []
    try:
        out = subprocess.run(
            [xdotool, "search", "--onlyvisible", "--maxdepth", "1", "."],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    results: List[Tuple[int, int]] = []
    for line in out.split():
        try:
            wid = int(line)
        except ValueError:
            continue
        try:
            pid = int(subprocess.run(
                [xdotool, "getwindowpid", str(wid)],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip() or 0)
        except (OSError, subprocess.SubprocessError, ValueError):
            pid = 0
        if pid:
            results.append((wid, pid))
    return results


def find_terminal_windows() -> List[int]:
    """Window ids of the supported external terminals, via xdotool. Covers
    every emulator terminal_posix can spawn (matched by WM_CLASS)."""
    xdotool = _tool("xdotool")
    if not xdotool:
        return []
    found: List[int] = []
    for cls in ("xterm", "fly-terminal", "qterminal", "konsole",
                "gnome-terminal", "xfce4-terminal"):
        try:
            out = subprocess.run(
                [xdotool, "search", "--class", cls],
                capture_output=True, text=True, timeout=5,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for line in out.split():
            if line.isdigit() and int(line) not in found:
                found.append(int(line))
    return found


def primary_work_area() -> Rect:
    return get_work_area(0)


def get_work_area(_hwnd: int) -> Rect:
    """The primary screen's usable area via xdotool; a sane 1280x1024
    fallback when absent (this path only serves the legacy mode)."""
    xdotool = _tool("xdotool")
    if xdotool:
        try:
            out = subprocess.run(
                [xdotool, "getdisplaygeometry"],
                capture_output=True, text=True, timeout=5,
            ).stdout.split()
            if len(out) >= 2:
                w, h = int(out[0]), int(out[1])
                return Rect(0, 0, w, h)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    return Rect(0, 0, 1280, 1024)


def get_window_rect(hwnd: int) -> Optional[Rect]:
    xdotool = _tool("xdotool")
    if not xdotool:
        return None
    try:
        out = subprocess.run(
            [xdotool, "getwindowgeometry", "--shell", str(hwnd)],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    vals = {}
    for line in out.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            vals[key.strip()] = value.strip()
    try:
        return Rect(int(vals["X"]), int(vals["Y"]),
                    int(vals["WIDTH"]), int(vals["HEIGHT"]))
    except (KeyError, ValueError):
        return None


def move_window(hwnd: int, rect: Rect) -> None:
    """Best-effort EWMH move+resize (restore first), via wmctrl."""
    wmctrl = _tool("wmctrl")
    if not wmctrl:
        return
    ident = str(hwnd)
    _run([wmctrl, "-i", "-r", ident, "-b", "remove,hidden"])
    _run([wmctrl, "-i", "-r", ident, "-e",
          "0,%d,%d,%d,%d" % (rect.x, rect.y, rect.w, rect.h)])


def close_window(hwnd: int) -> None:
    """Polite EWMH close (lets the terminal confirm/save), via wmctrl."""
    wmctrl = _tool("wmctrl")
    if wmctrl:
        _run([wmctrl, "-i", "-c", str(hwnd)])


def _run(cmd: List[str]) -> None:
    try:
        subprocess.run(cmd, capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass
