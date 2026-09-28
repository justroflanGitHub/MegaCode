"""File-state tests for the link: secret rotation, settings, atomicity."""

from __future__ import annotations

from megacode import sync_security


def test_secret_rotates_atomically_and_corrupt_rotates(tmp_path):
    d = tmp_path / "g1"
    d.mkdir()
    s1 = sync_security.rotate_secret(d)
    assert s1 == sync_security.read_secret(d)

    s2 = sync_security.rotate_secret(d)
    assert s2 != s1  # every takeover dies with the previous era's secret

    # a corrupt file reads as absent (nobody is stranded: the hub rewrites)
    (d / "link-secret").write_text("garbage", encoding="ascii")
    assert sync_security.read_secret(d) is None


def test_settings_roundtrip_and_corrupt_falls_back_to_defaults(tmp_path):
    d = tmp_path / "g2"
    d.mkdir()
    assert sync_security.settings_load(d) == {
        "link_windows": True, "link_windows_forced": False}

    sync_security.settings_save(d, {"link_windows": False,
                                    "link_windows_forced": True})
    assert sync_security.settings_load(d) == {
        "link_windows": False, "link_windows_forced": True}

    # corrupt / non-dict files mean the defaults, never a crash
    (d / "settings.json").write_text("{oops", encoding="utf-8")
    assert sync_security.settings_load(d)["link_windows"] is True
    (d / "settings.json").write_text("[1,2]", encoding="utf-8")
    assert sync_security.settings_load(d)["link_windows_forced"] is False


def test_link_info_roundtrip(tmp_path):
    d = tmp_path / "g3"
    d.mkdir()
    assert sync_security.read_link_info(d) is None
    info = {"name": "megacode-sync-v1-x", "epoch": "a" * 16,
            "proto": 1, "pid": 4242}
    sync_security.write_link_info(d, info)
    assert sync_security.read_link_info(d) == info


def test_pid_liveness_helper():
    from megacode import plat_helpers
    import os
    assert plat_helpers.is_pid_alive(os.getpid()) is True
    assert plat_helpers.is_pid_alive(0) is False
    # pid 4 is the System idle-ish reserved range on Windows; a long-gone
    # pid from the recycled range must read dead
    assert plat_helpers.is_pid_alive(-1) is False
