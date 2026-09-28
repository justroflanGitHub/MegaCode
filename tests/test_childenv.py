"""childenv: the frozen-bundle LD_LIBRARY_PATH leak scrub.

The scenario under test is the onedir deb install: the PyInstaller
bootloader set LD_LIBRARY_PATH=/opt/MegaCode, and every child spawned by
the app must NOT see it, while entries the user set themselves survive.
"""

from __future__ import annotations

import os
import sys

import pytest

from megacode import childenv


@pytest.fixture
def frozen_bundle(monkeypatch, tmp_path):
    """Pretend to be the onedir bundle at <tmp>/opt/MegaCode."""
    app = tmp_path / "opt" / "MegaCode"
    app.mkdir(parents=True)
    exe = app / "MegaCode"
    exe.write_bytes(b"")  # realpath does not need it executable
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    # onedir: the bootloader points _MEIPASS at the executable's dir
    monkeypatch.setattr(sys, "_MEIPASS", str(app), raising=False)
    return str(app)


def test_scrub_drops_bundle_keeps_user_paths(frozen_bundle):
    sep = os.pathsep
    env = {
        "LD_LIBRARY_PATH": (
            frozen_bundle + sep + "/opt/user-libs" + sep
            + os.path.join(frozen_bundle, "sub")
        ),
        "PATH": "/usr/bin:/bin",
    }
    out = childenv.scrub_child_env(env)
    assert out["LD_LIBRARY_PATH"] == "/opt/user-libs"
    # the input mapping is never modified (callers pass os.environ)
    assert env["LD_LIBRARY_PATH"].startswith(frozen_bundle)
    assert out["PATH"] == "/usr/bin:/bin"


def test_scrub_removes_var_when_only_bundle_entries(frozen_bundle):
    env = {"LD_LIBRARY_PATH": frozen_bundle}
    out = childenv.scrub_child_env(env)
    assert "LD_LIBRARY_PATH" not in out


def test_scrub_empty_var_is_removed(frozen_bundle):
    # an empty LD_LIBRARY_PATH is indistinguishable from unset; dropping it
    # keeps the child's env exactly what a non-MegaCode parent would give
    out = childenv.scrub_child_env({"LD_LIBRARY_PATH": "", "TERM": "xterm"})
    assert "LD_LIBRARY_PATH" not in out
    assert out["TERM"] == "xterm"


def test_noop_when_running_from_source(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    env = {"LD_LIBRARY_PATH": "/opt/user-libs", "TERM": "xterm-256color"}
    assert childenv.scrub_child_env(env) == env
    assert "LD_LIBRARY_PATH" not in childenv.scrub_child_env({"TERM": "xterm"})


def test_empty_entries_are_preserved(frozen_bundle):
    # a bare separator means "also the cwd" to ld.so; it is the user's
    # entry, not ours to drop
    sep = os.pathsep
    value = "/opt/a" + sep + sep + frozen_bundle
    assert childenv.drop_bundle_entries(value) == "/opt/a" + sep
