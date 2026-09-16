"""Pure helpers for tag-based sync groups.

Tags are short lowercase tokens owned by tiles; a sync group is "every pane
that shares at least one tag with me". The charset deliberately excludes
'@', ':', ',' and whitespace: the broadcast bar's "@tag command" selector
must stay unambiguous against real shell lines (cmd's "@echo off"), and
lowercase normalization keeps "Build"/"build" from splitting a group. No
widgets live here, so every rule is testable without Qt.
"""

import re
from typing import Optional, Tuple

_TAG_RE = re.compile(r"[\w-]{1,16}")  # used with fullmatch; \w is Unicode
# DOTALL: a multi-line paste into the broadcast box ("@fe git pull\nnpm i")
# must still scope -- without it the body's newline fails the match and the
# whole line would silently fall back to a run in EVERY pane.
_SELECTOR_RE = re.compile(r"@([\w-]{1,16})\s+(.+)", re.DOTALL)

#: cmd's only @-prefixed builtins: a tag with one of these names would let
#: the broadcast selector swallow a real shell line ("@echo off"), so they
#: are reserved outright and can never exist as tags.
_RESERVED = {"echo", "rem"}

#: Six muted chip color classes: (background, border). app.py generates the
#: QSS rules from this same tuple, so chips, menu icons and QSS can never
#: disagree about a tag's color.
TAG_COLORS = (
    ("#3a2a22", "#613d2f"), ("#22302a", "#2f4a3c"), ("#2e2440", "#4a3a66"),
    ("#33262b", "#5a3a44"), ("#26303c", "#3a4a5e"), ("#31302a", "#4f4a33"),
)


def normalize_tag(text: str) -> Optional[str]:
    """Strip, lowercase, inner whitespace -> '-', then validate the charset.

    Returns None for anything the grammar must reject (empty, '@', ':', ',',
    punctuation, over 16 chars, or a reserved cmd word). Rejecting rather
    than truncating keeps the user's mental model exact.
    """
    cleaned = re.sub(r"\s+", "-", text.strip().lower())
    if cleaned in _RESERVED:
        return None
    return cleaned if _TAG_RE.fullmatch(cleaned) else None


def tag_class(tag: str) -> int:
    """Stable chip color class: the same tag looks the same in every header
    and every session (no allocation registry to maintain or free)."""
    return sum(ord(c) for c in tag) % len(TAG_COLORS)


def parse_selector(line: str) -> Optional[Tuple[str, str]]:
    """Match '@tag body' (tag lowercased, body stripped); None otherwise.

    Requires whitespace + a non-empty body, so a bare '@fe' is never a
    selector -- and '@echo off' can never scope a run, because 'echo' is
    reserved and can never be a tag in the first place. The body may span
    lines (DOTALL): a pasted multi-line command scopes like a one-liner.
    """
    m = _SELECTOR_RE.fullmatch(line)
    if m is None:
        return None
    body = m.group(2).strip()
    # "non-empty body" must survive DOTALL: '@fe \n\n' backtracks \s+ until
    # (.+) swallows a bare newline, and a whitespace-only body would press
    # a stray Enter in the whole group.
    return (m.group(1).lower(), body) if body else None
