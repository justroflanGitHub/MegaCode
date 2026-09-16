"""Pure tests for the tag grammar: normalization, colors, @tag selectors."""

from __future__ import annotations

from megacode import tags as tagmod


def test_normalize_tag_lowercase_hyphenates_and_validates():
    assert tagmod.normalize_tag("Build ") == "build"
    assert tagmod.normalize_tag("x y") == "x-y"
    assert tagmod.normalize_tag("СБОРКА") == "сборка"  # Cyrillic is fine
    assert tagmod.normalize_tag("front_end-2") == "front_end-2"
    assert tagmod.normalize_tag("  spaces   inside  ") == "spaces-inside"
    # the grammar must reject: empty, selector/paste punctuation, emoji,
    # overlong, and cmd's @-prefixed builtins (see _RESERVED in tags.py)
    for bad in ("", "   ", "@at", "a:b", "a,b", "x" * 17, "echo", "Rem", "🙂"):
        assert tagmod.normalize_tag(bad) is None, bad


def test_tag_class_is_stable_and_bounded():
    assert tagmod.tag_class("a") == tagmod.tag_class("a")
    assert 0 <= tagmod.tag_class("сборка") < len(tagmod.TAG_COLORS)
    # every class must have a real palette entry (app.py builds QSS from it)
    assert len(tagmod.TAG_COLORS) == 6


def test_parse_selector_requires_tag_then_body():
    assert tagmod.parse_selector("@fe git pull") == ("fe", "git pull")
    assert tagmod.parse_selector("@FE  x ") == ("fe", "x")  # lowercased, stripped
    # a bare "@fe" is never a selector (no body); "@x" mid-line isn't either
    assert tagmod.parse_selector("@fe") is None
    assert tagmod.parse_selector("git @fe pull") is None
    assert tagmod.parse_selector("plain command") is None
    assert tagmod.parse_selector("") is None


def test_parse_selector_accepts_multiline_pasted_bodies():
    """A multi-line paste into the box must still scope: without DOTALL the
    interior newline failed the match and the run silently went everywhere."""
    assert tagmod.parse_selector("@fe git pull\nnpm test") == (
        "fe", "git pull\nnpm test")
    assert tagmod.parse_selector("@fe git pull\r\nnpm test") == (
        "fe", "git pull\r\nnpm test")
    assert tagmod.parse_selector("@fe\ngit pull") == ("fe", "git pull")
    # ...but a body of nothing but whitespace still isn't a selector
    assert tagmod.parse_selector("@fe \n\n") is None
