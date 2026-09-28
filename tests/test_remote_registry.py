"""Pure tests for the remote-pane registry (roster replacement + R3 counts)."""

from __future__ import annotations

from megacode.remote_registry import RemoteRegistry

SELF = "aaaaaaaaaaaa"
HUB = "bbbbbbbbbbbb"
W2 = "cccccccccccc"
W3 = "dddddddddddd"


def _win(wid, name, panes):
    return {"id": wid, "name": name, "panes": panes}


def test_apply_roster_replaces_wholesale_and_skips_self():
    reg = RemoteRegistry()
    reg.apply_roster([_win(HUB, "W1", []), _win(W2, "W2", [])], SELF)
    assert reg.other_windows() == 2

    reg.apply_roster([_win(W2, "W2", [])], SELF)
    assert reg.other_windows() == 1  # wholesale replace, not a merge
    assert reg.display_name(W2) == "W2"

    # our own row (a stale echo of ourselves) never enters
    reg.apply_roster([_win(SELF, "W9", [])], SELF)
    assert reg.other_windows() == 0
    assert reg.display_name(W2) == "another window"  # gone = honest fallback


def test_known_tags_first_seen_order_unions():
    reg = RemoteRegistry()
    reg.apply_roster([
        _win(HUB, "W1", [{"id": "p1", "tags": ["z", "q"], "alive": True, "kind": "term"}]),
        _win(W2, "W2", [{"id": "p2", "tags": ["q", "m"], "alive": True, "kind": "term"}]),
    ], SELF)
    assert reg.known_tags() == ["z", "q", "m"]


def test_audience_counts_skip_dead_and_chat_panes():
    reg = RemoteRegistry()
    reg.apply_roster([
        _win(HUB, "W1", [
            {"id": "p1", "tags": ["a"], "alive": True, "kind": "term"},
            {"id": "p2", "tags": [], "alive": True, "kind": "term"},
            {"id": "p3", "tags": ["a"], "alive": False, "kind": "term"},  # dead
            {"id": "p4", "tags": ["a"], "alive": True, "kind": "chat"},   # chat
        ]),
        _win(W2, "W2", [
            {"id": "p5", "tags": ["b"], "alive": True, "kind": "term"},
        ]),
    ], SELF)

    assert reg.alive_pane_count() == 3                  # p1, p2, p5
    assert reg.alive_panes_with_tag("a") == 1
    assert reg.windows_with_tag("a") == 1
    # chat panes feed the vocabulary but never count as an audience
    assert "a" in reg.known_tags() and reg.known_tags().count("a") == 1
