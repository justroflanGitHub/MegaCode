"""Pure helpers for tag-based sync groups.

Tags are short lowercase tokens owned by tiles; a sync group is "every pane
that shares at least one tag with me". The charset deliberately excludes
'@', ':', ',' and whitespace: the broadcast bar's "@tag command" selector
must stay unambiguous against real shell lines (cmd's "@echo off"), and
lowercase normalization keeps "Build"/"build" from splitting a group. No
widgets live here, so every rule is testable without Qt.
"""

import re
from typing import Optional, Sequence, Tuple

_TAG_RE = re.compile(r"[\w-]{1,16}")  # used with fullmatch; \w is Unicode
# DOTALL: a multi-line paste into the broadcast box ("@fe git pull\nnpm i")
# must still scope -- without it the body's newline fails the match and the
# whole line would silently fall back to a run in EVERY pane.
_SELECTOR_RE = re.compile(r"@([\w-]{1,16})\s+(.+)", re.DOTALL)

#: cmd's only @-prefixed builtins: a tag with one of these names would let
#: the broadcast selector swallow a real shell line ("@echo off"), so they
#: are reserved outright and can never exist as tags.
_RESERVED = {"echo", "rem"}

#: Six muted chip color classes: (background, border, foreground), one tuple
#: per class. themes.py generates each scheme's QSS rules from the palette the
#: scheme carries, so chips, menu icons and QSS can never disagree about a
#: tag's color. Both palettes walk the SAME hue order (brown, green, purple,
#: rose, blue, olive), so a tag keeps its hue family when the theme flips --
#: only the value direction changes. The class's own foreground is what keeps
#: the tag name readable: the scheme's text color vanished on the dark chip
#: in light themes (Debian report), and a light foreground would have vanished
#: on a light one.
TAG_COLORS = (  # dark themes: dark chip, light tag name
    ("#3a2a22", "#613d2f", "#f3e3dc"), ("#22302a", "#2f4a3c", "#d9e8de"),
    ("#2e2440", "#4a3a66", "#e4dcf2"), ("#33262b", "#5a3a44", "#f0dde2"),
    ("#26303c", "#3a4a5e", "#dde7f2"), ("#31302a", "#4f4a33", "#e9e6d6"),
)
TAG_COLORS_LIGHT = (  # light themes: pastel chip, dark tag name
    ("#efcdb2", "#bd835f", "#5c3a26"), ("#d9e8d6", "#7ba888", "#2e4a33"),
    ("#ded6ee", "#9484c2", "#463a66"), ("#eed6dc", "#bc8494", "#5e333f"),
    ("#d9e2f0", "#8298bd", "#33415c"), ("#e8dba2", "#a89a4a", "#4f4826"),
)
# The two warm classes (0 brown, 5 olive) sit deliberately DEEPER than the
# rest: light-theme sync tints are warm creams too (paper-light's
# header_sync_source #f0e2cb / peer #f5ecdc), and the first cut of this
# palette (#f2d9c4 / #ebe4c8) landed within dE2000 ~4-6 of them -- the chip
# plate dissolved into an armed-sync header, delineated only by its 1px
# border (review finding). Hue order still mirrors the dark palette.


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
    and every session (no allocation registry to maintain or free).

    The modulus anchors to THIS palette's length, so it is the single source
    of truth for the class count: every scheme's ``tag_colors`` must carry
    exactly as many classes (tags.py cannot ask themes -- that import would
    be circular; the equality is pinned in test_themes and re-exercised by
    the per-scheme pixel test indexing ``tag_colors()[tag_class(...)]``)."""
    return sum(ord(c) for c in tag) % len(TAG_COLORS)


def share_domain(ta: Sequence[str], tb: Sequence[str]) -> bool:
    """The R3 sync-domain rule, lifted to a pure function.

    Both untagged -> True (the implicit no-tag group: with zero tags
    anywhere every pair matches). Exactly one untagged -> False (tagged
    and untagged panes never cross). Otherwise -> any shared tag (a pane
    with two tags is a member of both groups at once).

    Shared by the in-window fan-out AND cross-window delivery so the two
    can never drift apart.
    """
    if not ta and not tb:
        return True
    if not ta or not tb:
        return False
    return bool(set(ta) & set(tb))


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
