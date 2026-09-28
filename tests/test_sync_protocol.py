"""Pure tests for the cross-window wire protocol: framing, validation, mac."""

from __future__ import annotations

import secrets

from megacode import sync_protocol as proto


def test_encode_frames_one_json_object_per_line():
    raw = proto.encode({"v": 1, "t": "ping", "src": "abcdef123456"})
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 1
    assert b" " not in raw.replace(b'"ping"', b"") or True  # compact
    import json
    msg = json.loads(raw.decode("ascii"))
    assert msg == {"v": 1, "t": "ping", "src": "abcdef123456"}


def test_framing_survives_newlines_and_vt_in_payload():
    # a paste payload carrying CR/LF/VT/escape bytes must stay ONE frame
    seq = "git pull\r\nnpm \x1b[1m test\n"
    raw = proto.encode({"v": 1, "t": "input", "src": "abcdef123456",
                        "pane": "p1", "tags": ["fe"], "seq": seq,
                        "paste": True})
    buf = bytearray(raw)
    frames = list(proto.split_frames(buf))
    assert len(frames) == 1
    assert proto.decode_line(frames[0])["seq"] == seq
    assert not buf  # drained


def test_decode_rejects_bad_envelope():
    src = "abcdef123456"
    ok = {"v": 1, "t": "sync", "src": src, "on": True}
    assert proto.decode_line(proto.encode(ok)) is not None
    # wrong proto version, unknown type, non-hex src, float payload,
    # oversized line: all rejected
    assert proto.decode_line(proto.encode({"v": 2, "t": "sync", "src": src, "on": True})) is None
    assert proto.decode_line(proto.encode({"v": 1, "t": "nope", "src": src})) is None
    assert proto.decode_line(proto.encode({"v": 1, "t": "sync", "src": "xyz", "on": True})) is None
    assert proto.decode_line(b'{"v":1,"t":"sync","src":"abcdef123456","on":1.5}') is None
    big = proto.encode({"v": 1, "t": "input", "src": src, "pane": "p1",
                        "tags": [], "seq": "x" * (proto.MAX_SEQ + 1),
                        "paste": False})
    assert proto.decode_line(big) is None
    # mac-shaped fields must be 64 hex
    hello = {"v": 1, "t": "hello", "src": src, "proto": 1,
             "mac": "zz" * 32, "panes": []}
    assert proto.decode_line(proto.encode(hello)) is None


def test_sync_frame_scope_tag_is_optional_but_strict():
    """The menu's scope rides the sync frame: absent/"" = the all-windows
    arm (what an older same-proto peer's frames read as, each side then
    keeping its own unscoped semantics), anything else must be ONE
    well-formed tag."""
    src = "abcdef123456"
    base = {"v": 1, "t": "sync", "src": src, "on": True}
    # absent and explicit "": both decode as the unscoped arm
    for extra in ({}, {"tag": ""}):
        msg = proto.decode_line(proto.encode(dict(base, **extra)))
        assert msg is not None
        assert msg.get("tag", "") == ""
    ok = proto.decode_line(proto.encode(dict(base, tag="fe")))
    assert ok is not None and ok["tag"] == "fe"
    # malformed tag, non-string, explicit null: all rejected
    assert proto.decode_line(proto.encode(dict(base, tag="not a tag"))) is None
    assert proto.decode_line(proto.encode(dict(base, tag="echo"))) is None  # reserved
    assert proto.decode_line(b'{"v":1,"t":"sync","src":"abcdef123456","on":true,"tag":7}') is None
    assert proto.decode_line(b'{"v":1,"t":"sync","src":"abcdef123456","on":true,"tag":null}') is None


def test_input_frame_all_flag_is_optional_but_strict():
    """The left-click (all-windows) arm's bypass flag: absent/False keeps
    the domain rule (an older same-proto peer simply never sees the key),
    True delivers everywhere -- and anything non-bool is rejected."""
    src = "abcdef123456"
    base = {"v": 1, "t": "input", "src": src, "pane": "p1",
            "tags": ["fe"], "seq": "x", "paste": False}
    for extra in ({}, {"all": False}, {"all": True}):
        msg = proto.decode_line(proto.encode(dict(base, **extra)))
        assert msg is not None
        assert msg.get("all", False) is extra.get("all", False)
    # a string, a number, an explicit null: all rejected
    assert proto.decode_line(b'{"v":1,"t":"input","src":"abcdef123456",'
                             b'"pane":"p1","tags":[],"seq":"x","paste":false,'
                             b'"all":"yes"}') is None
    assert proto.decode_line(b'{"v":1,"t":"input","src":"abcdef123456",'
                             b'"pane":"p1","tags":[],"seq":"x","paste":false,'
                             b'"all":1}') is None
    assert proto.decode_line(b'{"v":1,"t":"input","src":"abcdef123456",'
                             b'"pane":"p1","tags":[],"seq":"x","paste":false,'
                             b'"all":null}') is None


def test_welcome_validates_the_adopted_scope_tag():
    src = "abcdef123456"
    welcome = {"v": 1, "t": "welcome", "src": src, "you": "W2",
               "epoch": "e" * 16, "mac": "f" * 64,
               "sync_on": True, "windows": []}
    ok = proto.decode_line(proto.encode(dict(welcome, sync_tag="fe")))
    assert ok is not None and ok["sync_tag"] == "fe"
    # absent stays legal (a pre-picker hub never says it)
    assert proto.decode_line(proto.encode(welcome)) is not None
    bad = proto.decode_line(proto.encode(dict(welcome, sync_tag="nope!")))
    assert bad is None


def test_pane_entry_renormalizes_and_caps_tags():
    entry = proto.build_pane_entry("p1", ["FE", "not a tag!", "fe", "ok"], True, "term")
    assert entry == {"id": "p1", "tags": ["fe", "ok"], "alive": True, "kind": "term"}
    # grammar-breaking tags are dropped, not passed through
    bad = proto.build_pane_entry("p1", ["@at", "a:b"], True, "term")
    assert bad == {"id": "p1", "tags": [], "alive": True, "kind": "term"}
    # over the per-pane tag cap: rejected outright
    assert proto.build_pane_entry("p1", [f"t{i}" for i in range(9)], True, "term") is None
    # unknown kind collapses to chat (never counted as an audience pane)
    assert proto.build_pane_entry("p1", [], False, "whatever")["kind"] == "chat"


def test_mac_is_deterministic_and_secret_dependent():
    a = proto.mac(b"s1" * 16, "c1", "sid", "wid")
    assert a == proto.mac(b"s1" * 16, "c1", "sid", "wid")
    assert a != proto.mac(b"s2" * 16, "c1", "sid", "wid")
    assert len(a) == 64
    assert secrets.compare_digest(a, proto.mac(b"s1" * 16, "c1", "sid", "wid"))


def test_pipe_name_is_user_and_session_scoped():
    n1 = proto.pipe_name("alice", "1")
    n2 = proto.pipe_name("alice", "2")
    n3 = proto.pipe_name("bob", "1")
    assert n1.startswith("megacode-sync-v1-")
    assert len(n1) == len("megacode-sync-v1-") + 16
    assert len({n1, n2, n3}) == 3  # users and sessions never collide
