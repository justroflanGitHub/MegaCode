"""File-backed state for the cross-window link (stdlib only).

Under ``%LOCALAPPDATA%/MegaCode/sync`` (Windows) or
``$XDG_STATE_HOME/megacode/sync`` (Linux, default ``~/.local/state`` --
persistent by design, NOT ``$XDG_RUNTIME_DIR``, which is tmpfs wiped on
logout; the HMAC secret must survive reboots):
  ``hub.lock``    -- the QLockFile (owned by sync_bus, not here)
  ``link.json``   -- where the listening hub lives (name/epoch/proto/pid)
  ``link-secret`` -- the HMAC secret; never crosses the wire
  ``settings.json``-- the user's link preferences

Writes are atomic (temp + os.replace) so a crash never leaves a torn file a
peer would then trust. The secret file gets best-effort owner-only hardening
via plat_helpers.harden_user_only (a DACL on Windows; mode 0600 on Linux,
where the 0700 state directory is the primary boundary -- a Debian home is
0755 by default, unlike the Windows profile).
"""

from __future__ import annotations

import json
import logging
import os
import secrets as pysecrets
import sys
from pathlib import Path
from typing import Dict, Optional

from . import plat_helpers

log = logging.getLogger("megacode")

_SETTINGS_DEFAULTS = {"link_windows": True, "link_windows_forced": False}


def state_dir() -> Path:
    """The per-user link state directory."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local")
        return Path(base) / "MegaCode" / "sync"
    base = os.environ.get("XDG_STATE_HOME") or str(
        Path.home() / ".local" / "state")
    return Path(base) / "megacode" / "sync"


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def read_secret(directory: Path) -> Optional[bytes]:
    """The shared secret, or None when missing/unreadable/corrupt."""
    try:
        text = (directory / "link-secret").read_text(encoding="ascii").strip()
    except OSError:
        return None
    # 64 hex chars exactly; anything else is treated as absent (the hub
    # rewrites it on takeover, so nobody is ever stranded)
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        return None
    return text.encode("ascii")


def rotate_secret(directory: Path) -> bytes:
    """Fresh secret, atomically persisted. Called on every hub takeover:
    a secret that might have leaked during a crashed era dies with it."""
    secret = pysecrets.token_hex(32).encode("ascii")
    _atomic_write(directory / "link-secret", secret + b"\n", 0o600)
    # best-effort hardening; failure is non-fatal (logged) -- the profile
    # ACL (Windows) / the 0700 state directory (Linux) is the primary boundary
    if not plat_helpers.harden_user_only(str(directory / "link-secret")):
        log.info("link-secret owner-only hardening failed (non-fatal)")
    return secret


def read_link_info(directory: Path) -> Optional[Dict]:
    """The hub rendezvous record (pipe name / epoch / proto / pid)."""
    try:
        info = json.loads((directory / "link.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(info, dict):
        return None
    return info


def write_link_info(directory: Path, info: Dict) -> None:
    _atomic_write(
        directory / "link.json",
        json.dumps(info, separators=(",", ":")).encode("utf-8"), 0o600,
    )


def settings_load(directory: Path) -> Dict:
    """Preferences; a missing or corrupt file means the defaults (never a
    crash, never a surprise lock-out)."""
    try:
        data = json.loads((directory / "settings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(_SETTINGS_DEFAULTS)
    if not isinstance(data, dict):
        return dict(_SETTINGS_DEFAULTS)
    out = dict(_SETTINGS_DEFAULTS)
    if isinstance(data.get("link_windows"), bool):
        out["link_windows"] = data["link_windows"]
    if isinstance(data.get("link_windows_forced"), bool):
        out["link_windows_forced"] = data["link_windows_forced"]
    return out


def settings_save(directory: Path, prefs: Dict) -> None:
    """Persist preferences. Called ONLY from an explicit user toggle."""
    _atomic_write(
        directory / "settings.json",
        json.dumps(prefs, separators=(",", ":")).encode("utf-8"), 0o600,
    )
