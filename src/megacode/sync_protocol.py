"""Wire protocol for cross-window linking: pure framing + validation.

One NDJSON line per message: compact JSON, ensure_ascii=True so the wire is
ASCII-only and VT/newline payloads stay frame-safe with no extra escaping
layer. Everything here is Qt-free and side-effect-free, so the grammar is
unit-testable exactly like tags.py.

The hub validates every inbound line with decode_line() BEFORE acting on or
relaying it, and relays ``input`` frames as the ORIGINAL RAW BYTES;
receivers re-validate, so a corrupt or hostile peer can neither crash a
receiver nor smuggle a grammar-breaking tag past tags.normalize_tag.
"""

import hashlib
import hmac
import json
import re
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from . import tags

PROTO = 1

# --- hard limits (fail-closed: a violation closes that connection only) -------
MAX_LINE = 1024 * 1024        # framed line cap (bounds every socket buffer)
MAX_SEQ = 512 * 1024          # mirrored keystroke / paste payload
MAX_CMD = 64 * 1024           # scoped run command
MAX_PANES = 256
MAX_TAGS_PER_PANE = 8
MAX_WINDOWS = 16              # max CLIENT connections; the hub is the 17th
RATE_BURST = 64               # token bucket capacity (lines)
RATE_PER_SEC = 2000.0
HELLO_TIMEOUT_MS = 3000
RECONNECT_BACKOFF_MS = (50, 100, 200, 400, 800, 1600)  # capped at the last
ELECTION_JITTER_MS = 150
PING_MS = 2000
DEGRADE_MS = 6500
KICK_PENDING = 1024 * 1024    # wedged-peer bytesToWrite threshold

_SRC_RE = re.compile(r"[0-9a-f]{12}")
_HEX16_RE = re.compile(r"[0-9a-f]{16}")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
_NAME_RE = re.compile(r"W[0-9]+")
_TYPES = frozenset({
    "challenge", "hello", "welcome", "roster", "digest", "sync",
    "input", "run", "bye", "gone", "ping",
})
_BYE_REASONS = frozenset({"shutdown", "kill", "auth", "proto", "rate", "full"})
_GONE_REASONS = frozenset({"exit", "drop"})


def encode(msg: Dict) -> bytes:
    """Frame one message: compact ASCII-safe JSON + newline."""
    return json.dumps(
        msg, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii") + b"\n"


def split_frames(buffer: bytearray) -> Iterator[bytes]:
    """Pop every complete newline-terminated frame off ``buffer``."""
    while True:
        nl = buffer.find(b"\n")
        if nl < 0:
            return
        yield bytes(buffer[:nl])
        del buffer[: nl + 1]


def _no_floats(obj) -> bool:
    """The wire carries integers/strings/bools only; a float means someone
    smuggled a hand-built JSON -- reject rather than guess their intent."""
    if isinstance(obj, float):
        return False
    if isinstance(obj, dict):
        return all(_no_floats(v) for v in obj.values())
    if isinstance(obj, list):
        return all(_no_floats(v) for v in obj)
    return True


def _valid_tag_list(value) -> bool:
    if not isinstance(value, list) or len(value) > MAX_TAGS_PER_PANE:
        return False
    # re-normalize on receipt: a tag that would not survive normalize_tag
    # unchanged must never enter anyone's vocabulary
    return all(isinstance(t, str) and tags.normalize_tag(t) == t for t in value)


def _valid_scope_tag(value) -> bool:
    """The sync-scope field: "" (absent -- the all-windows arm, every pane
    in every linked window) or ONE well-formed tag (the right-click menu's
    group). Strictly a str, so an explicit JSON null can never pass here
    yet arrive as None downstream. Optional and strictly validated, so a
    PROTO-1 peer that predates the field simply never sees it (unknown
    JSON keys are ignored) and an old sender's frames read as "" here --
    an old peer then keeps its own (domain) semantics for "", a newer
    peer arms all-windows; same-build groups never mix the two."""
    return isinstance(value, str) and (
        value == "" or tags.normalize_tag(value) == value)


def build_pane_entry(
    pane_id: str, tag_list: Sequence[str], alive: bool, kind: str
) -> Optional[Dict]:
    """One digest row. Normalizes, caps and de-dupes tags -- the sender's
    liberal hooks can pass anything; the wire grammar stays strict."""
    cleaned: List[str] = []
    for raw in tag_list:
        t = tags.normalize_tag(raw)
        if t and t not in cleaned:
            cleaned.append(t)
    if len(cleaned) > MAX_TAGS_PER_PANE or not isinstance(pane_id, str):
        return None
    return {"id": pane_id, "tags": cleaned, "alive": bool(alive),
            "kind": "term" if kind == "term" else "chat"}


def _valid_panes(panes) -> bool:
    if not isinstance(panes, list) or len(panes) > MAX_PANES:
        return False
    for p in panes:
        if not isinstance(p, dict):
            return False
        if not isinstance(p.get("id"), str) or not p["id"]:
            return False
        if not _valid_tag_list(p.get("tags")):
            return False
        if not isinstance(p.get("alive"), bool):
            return False
        if p.get("kind") not in ("term", "chat"):
            return False
    return True


def _valid_windows(windows) -> bool:
    # the roster includes the HUB's own row: MAX_WINDOWS counts clients,
    # so a full group's roster carries MAX_WINDOWS + 1 rows
    if not isinstance(windows, list) or len(windows) > MAX_WINDOWS + 1:
        return False
    for w in windows:
        if not isinstance(w, dict):
            return False
        if not isinstance(w.get("id"), str) or not _SRC_RE.fullmatch(w["id"]):
            return False
        if not isinstance(w.get("name"), str) or not _NAME_RE.fullmatch(w["name"]):
            return False
        if not _valid_panes(w.get("panes")):
            return False
    return True


def decode_line(raw: bytes) -> Optional[Dict]:
    """Parse + validate one framed line; None when the line must be rejected.

    Per-type field checks keep every consumer's reads total: if a message
    passes here, ``msg["x"]`` exists with the promised type downstream.
    """
    try:
        msg = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(msg, dict) or not _no_floats(msg):
        return None
    if msg.get("v") != PROTO or msg.get("t") not in _TYPES:
        return None
    src = msg.get("src")
    if not isinstance(src, str) or not _SRC_RE.fullmatch(src):
        return None
    t = msg["t"]
    if t == "challenge":
        if not _HEX16_RE.fullmatch(msg.get("sid", "")):
            return None
        if not _HEX16_RE.fullmatch(msg.get("epoch", "")):
            return None
    elif t == "hello":
        if msg.get("proto") != PROTO:
            return None
        if not _HEX64_RE.fullmatch(msg.get("mac", "")):
            return None
        if not _valid_panes(msg.get("panes")):
            return None
    elif t == "welcome":
        if not isinstance(msg.get("you"), str) or not _NAME_RE.fullmatch(msg["you"]):
            return None
        if not _HEX16_RE.fullmatch(msg.get("epoch", "")):
            return None
        if not _HEX64_RE.fullmatch(msg.get("mac", "")):
            return None
        if not isinstance(msg.get("sync_on"), bool):
            return None
        if not _valid_scope_tag(msg.get("sync_tag", "")):
            return None
        if not _valid_windows(msg.get("windows")):
            return None
    elif t == "roster":
        if not _valid_windows(msg.get("windows")):
            return None
    elif t == "digest":
        if not _valid_panes(msg.get("panes")):
            return None
    elif t == "sync":
        if not isinstance(msg.get("on"), bool):
            return None
        if not _valid_scope_tag(msg.get("tag", "")):
            return None
    elif t == "input":
        if not isinstance(msg.get("pane"), str):
            return None
        if not _valid_tag_list(msg.get("tags")):
            return None
        seq = msg.get("seq")
        if not isinstance(seq, str) or len(seq) > MAX_SEQ:
            return None
        if not isinstance(msg.get("paste"), bool):
            return None
        # the all-windows arm's bypass flag: optional, strictly bool (the
        # same graceful-degradation contract as "sync_tag" -- a peer that
        # predates the field never sees it and keeps its domain rule)
        if not isinstance(msg.get("all", False), bool):
            return None
    elif t == "run":
        tag, cmd = msg.get("tag"), msg.get("cmd")
        if not isinstance(tag, str) or tags.normalize_tag(tag) != tag:
            return None
        if not isinstance(cmd, str) or not cmd or len(cmd) > MAX_CMD:
            return None
    elif t == "bye":
        if msg.get("reason") not in _BYE_REASONS:
            return None
    elif t == "gone":
        if not isinstance(msg.get("id"), str) or not _SRC_RE.fullmatch(msg["id"]):
            return None
        if msg.get("reason") not in _GONE_REASONS:
            return None
    return msg


def mac(secret: bytes, *parts: str) -> str:
    """HMAC-SHA256 over the parts, joined by '|'. The secret never crosses
    the wire; both sides derive the same tag from the shared file.

    TRUST BOUNDARY (deliberate): the MAC proves the peer could READ the
    per-user secret file -- nothing more. It is not channel binding: a
    same-user process with profile access can read the secret and
    impersonate a window directly (or relay a handshake byte-for-byte), so
    no nonce gymnastics here would add anything. The handshake's value is
    against processes that can reach the pipe but NOT the user's profile
    (other sessions, restricted tokens) -- the account is the boundary.
    """
    return hmac.new(secret, "|".join(parts).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def pipe_name(username: str, session_id: str) -> str:
    """Per-user, per-Terminal-Services-session pipe name.

    Two Windows users (or two sessions of one user via fast switching/RDP)
    hash to different names, so their links can never cross. Inputs are
    injected so tests stay pure.
    """
    digest = hashlib.sha256(f"{username}|{session_id}".encode("utf-8"))
    return "megacode-sync-v1-" + digest.hexdigest()[:16]
