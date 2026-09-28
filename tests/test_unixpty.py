"""Real-PTY tests for the Unix backend (skipped on Windows).

These spawn REAL children through :class:`megacode.unixpty.Pty` and exercise
the exact surface terminal_widget consumes: decoded str chunks, multibyte
UTF-8 surviving chunk boundaries, TIOCSWINSZ resize, liveness/reaping and
the SIGTERM->SIGKILL group kill in stop().
"""

from __future__ import annotations

import os
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="Unix PTY backend")

# module-level import would pull fcntl/termios into the COLLECTION phase on
# Windows (before the skip marker applies) -- guard it
if sys.platform != "win32":
    from megacode.unixpty import Pty  # noqa: E402


def _wait(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def _gone(pid, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.05)
    return False


def test_spawn_output_arrives_decoded():
    received = []
    p = Pty(
        'python3 -c "import sys; sys.stdout.write(\'привет world\');'
        ' sys.stdout.flush(); import time; time.sleep(30)"',
        80, 24)
    p.start(received.append)
    try:
        assert _wait(lambda: "привет world" in "".join(received)), \
            "".join(received)
    finally:
        p.stop()


def test_write_reaches_child_and_echoes():
    chunks = []
    p = Pty("cat", 40, 10)  # cat echoes stdin back through the pty
    p.start(chunks.append)
    try:
        p.write("hello-pty\r")
        assert _wait(lambda: "hello-pty" in "".join(chunks)), "".join(chunks)
    finally:
        p.stop()


def test_resize_updates_window_size():
    import fcntl
    import struct
    import termios

    p = Pty("cat", 80, 24)
    p.start(lambda _text: None)
    try:
        p.resize(100, 30)
        buf = fcntl.ioctl(p._master, termios.TIOCGWINSZ, b"\0" * 8)
        rows, cols = struct.unpack("HHHH", buf)[:2]
        assert (cols, rows) == (100, 30)  # resize() takes cols FIRST
    finally:
        p.stop()


def test_is_alive_reaps_and_stop_kills_term_ignorer():
    # interactive shells ignore SIGTERM; stop() must escalate to SIGKILL
    p = Pty(
        'python3 -c "import signal, time;'
        ' signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"',
        80, 24)
    p.start(lambda _text: None)
    assert p.is_alive()
    p.stop()
    assert not p.is_alive()
    assert _gone(p.pid), "stop() must kill even a SIGTERM-ignoring child"


def test_failed_spawn_raises_runtime_error():
    with pytest.raises(RuntimeError):
        Pty("definitely-not-a-command-megacode-test", 80, 24)


def test_write_after_stop_is_silent_noop():
    p = Pty("cat", 80, 24)
    p.start(lambda _text: None)
    p.stop()
    p.write("x")  # must not raise (EIO on a closed master is swallowed)


def test_lnm_workaround_is_off():
    """The Unix backend must NOT request pyte's LNM compensation: the line
    discipline emits CRLF for cooked output and raw TUIs expect xterm LF
    semantics (terminal_widget keys off this flag)."""
    assert Pty.LNM_WORKAROUND is False


_SIGINT_CODE = (
    "import signal, sys, time\n"
    "def _hit(*_a):\n"
    "    sys.stdout.write('GOT_INT\\n')\n"
    "    sys.stdout.flush()\n"
    "    sys.exit(0)\n"
    "signal.signal(signal.SIGINT, _hit)\n"
    "time.sleep(30)\n"
)


def test_ctrl_c_delivers_sigint_through_controlling_tty():
    """The slave must be the child's CONTROLLING terminal (TIOCSCTTY in the
    preexec): without it the tty has no foreground process group, the line
    discipline cannot deliver ISIG's SIGINT for the \\x03 Ctrl+C byte, and
    no cooked-mode pane would ever be interruptible."""
    received = []
    p = Pty(["python3", "-u", "-c", _SIGINT_CODE], 80, 24)
    p.start(received.append)
    try:
        # give the child a moment to install the handler, then Ctrl+C
        time.sleep(0.4)
        p.write("\x03")
        assert _wait(lambda: any("GOT_INT" in c for c in received)), \
            "".join(received)
    finally:
        p.stop()


def _child_prints_ld_library_path(monkeypatch, tmp_path, value):
    """Spawn a REAL child through Pty and return its LD_LIBRARY_PATH view.

    The frozen markers are faked (sys.frozen + executable inside a fake
    bundle dir), so childenv.scrub_child_env() engages exactly as it does
    in the deb install, and the env that survives is observed in the
    forked process itself -- not in a unit-level dict.
    """
    bundle = tmp_path / "opt" / "MegaCode"
    bundle.mkdir(parents=True)
    (bundle / "MegaCode").write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(bundle / "MegaCode"))
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", value)
    import shlex

    script = (
        "import os\n"
        "v = os.environ.get('LD_LIBRARY_PATH')\n"
        "print('CHILDENV:' + (v if v is not None else '<unset>'))\n"
    )
    chunks = []
    p = Pty("python3 -c " + shlex.quote(script), 80, 24)
    p.start(chunks.append)
    try:
        assert _wait(lambda: "CHILDENV:" in "".join(chunks)), "".join(chunks)
    finally:
        p.stop()
    line = next(l for l in "".join(chunks).splitlines()
                if l.startswith("CHILDENV:"))
    return line[len("CHILDENV:"):]


def test_pty_child_does_not_inherit_bundle_lib_path(monkeypatch, tmp_path):
    """The frozen bundle's LD_LIBRARY_PATH must not reach PTY children.

    Regression: the deb install's bootloader sets LD_LIBRARY_PATH to
    /opt/MegaCode; children inherited it and every C++ tool run in the
    terminal resolved the (older) bundled libstdc++, dying on missing
    GLIBCXX symbols -- qpdf et al.
    """
    bundle = tmp_path / "opt" / "MegaCode"
    seen = _child_prints_ld_library_path(
        monkeypatch, tmp_path,
        str(bundle) + os.pathsep + str(bundle / "sub") + os.pathsep
        + "/opt/user-libs")
    assert seen == "/opt/user-libs"


def test_pty_child_lib_path_unset_when_only_bundle(monkeypatch, tmp_path):
    bundle = tmp_path / "opt" / "MegaCode"
    seen = _child_prints_ld_library_path(
        monkeypatch, tmp_path, str(bundle))
    assert seen == "<unset>"
