"""Color schemes: completeness of every scheme + the dark QSS regression pins.

The default ``claude-dark`` scheme must reproduce the pre-theme constants
byte-for-byte (tests pin the exact hexes), and the QSS must carry the
explicit ``QSpinBox::up/down-button`` rules -- without them QStyleSheetStyle
leaks painter states on xcb and prints
"QPainter::end: Painter ended with 2 saved states" at startup
(reproduced in the xvfb testbed, docker/xvfb-testbed).
"""

from __future__ import annotations

import pytest  # noqa: E402  (import order pinned by conftest's env)
from PySide6.QtGui import QColor  # noqa: E402

from megacode import themes  # noqa: E402

_ANSI_NAMES = (
    "black", "red", "green", "brown", "yellow", "blue", "magenta", "cyan",
    "white", "brightblack", "brightred", "brightgreen", "brightyellow",
    "brightblue", "brightmagenta", "brightcyan", "brightwhite",
)

# The pre-theme constants from app.py/terminal_widget.py that claude-dark
# must keep rendering with -- changing any of these is a visual regression.
_DARK_QSS_PINS = (
    "#0e1014",   # BG (window)
    "#161a21",   # CARD
    "#232830",   # BORDER
    "#3a4150",   # BORDER_HI
    "#d97757",   # ACCENT
    "#e08866",   # ACCENT_HI
    "#e6e6e6",   # TEXT
    "#8a93a3",   # MUTED
    "#1e1e1e",   # tile background == terminal background
    "#252526",   # tile header
    "#12151a",   # chat background
)


def test_dark_qss_pins_legacy_colors():
    qss = themes.build_qss(themes.SCHEMES[themes.DEFAULT_SCHEME])
    for hex_color in _DARK_QSS_PINS:
        assert hex_color in qss, hex_color


def test_dark_qss_styles_spinbox_subcontrols():
    """The xcb painter-states fix: explicit ::up/::down-button rules."""
    qss = themes.build_qss(themes.SCHEMES[themes.DEFAULT_SCHEME])
    assert "QSpinBox::up-button" in qss
    assert "QSpinBox::down-button" in qss


def test_every_scheme_is_complete():
    for name, scheme in themes.SCHEMES.items():
        missing = [k for k in _ANSI_NAMES if k not in scheme["term_colors"]]
        assert not missing, (name, missing)
        assert scheme["label"]
        assert QColor(scheme["term_fg"]).isValid()
        assert QColor(scheme["term_bg"]).isValid()
        qss = themes.build_qss(scheme)
        assert "None" not in qss  # a missing slot renders as None


def test_tag_chip_colors_follow_the_scheme_and_stay_readable():
    """Every scheme carries its own chip palette, matched to its value
    direction: dark chip + light tag name on dark themes, pastel chip +
    dark tag name on light ones (paper-light carried the dark chips and
    read as foreign plaques). The class foreground is what keeps the tag
    name visible in either direction -- the scheme's own text color
    vanished on the dark chip (Debian report 1.2)."""
    from megacode.tags import TAG_COLORS

    for name, scheme in themes.SCHEMES.items():
        # the chip sits on the tile header: light header -> light palette
        light = QColor(scheme["tile_header"]).lightness() > 128
        palette = scheme["tag_colors"]
        # class indices are theme-independent, so palettes must align
        assert len(palette) == len(TAG_COLORS), name
        for bg, border, fg in palette:
            for c in (bg, border, fg):
                assert QColor(c).isValid(), (name, c)
            if light:
                assert QColor(bg).lightness() > 180, (name, bg)
                assert QColor(fg).lightness() < 120, (name, fg)
            else:
                assert QColor(bg).lightness() < 80, (name, bg)
                assert QColor(fg).lightness() > 180, (name, fg)
            # the QSS rules must actually apply fg inside that scheme's chips
            assert fg in themes.build_qss(scheme), (name, fg)


def test_dark_terminal_palette_is_the_legacy_one():
    colors = themes.SCHEMES["claude-dark"]["term_colors"]
    assert colors["red"] == "#c50f1f"
    assert colors["brightblack"] == "#767676"
    assert themes.SCHEMES["claude-dark"]["term_fg"] == "#d4d4d4"


def test_set_active_unknown_name_raises():
    with pytest.raises(KeyError):
        themes.set_active("no-such-theme")


def test_active_tracks_set_active():
    themes.set_active("paper-light")
    assert themes.active_name() == "paper-light"
    assert themes.active() is themes.SCHEMES["paper-light"]
