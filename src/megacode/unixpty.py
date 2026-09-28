"""Terminal process backed by a Unix PTY (``os.openpty`` + ``subprocess``).

A drop-in twin of :mod:`megacode.conpty` for Linux: the same ``Pty`` surface
(ctor ``(command, cols, rows, cwd=None)``, ``pid``, ``start(on_output)``,
``write(str)``, ``resize(cols, rows)``, ``is_alive()``, ``stop()``), so
:class:`megacode.terminal_widget.TerminalWidget` stays platform-neutral.
stdlib-only on purpose: the Astra Linux target (Debian-10-era, air-gapped
installs) must not need a compiler or extra wheels for the terminal core.

Differences from the ConPTY backend that consumers must know:

* ``LNM_WORKAROUND`` is False: a Unix line discipline applies ONLCR to cooked
  output and raw-mode TUIs expect xterm semantics for a bare LF (column
  preserved), so the pyte LNM compensation ConPTY needed must stay off.
* Reads come off the master fd as bytes; an incremental UTF-8 decoder is held
  across reads so a multibyte glyph split across two chunks never garbles.
* ``stop()`` kills the child's whole process group (claude/node spawns
  children that share the slave); the group exists because spawn uses
  ``start_new_session=True``, making pgid == child pid.
"""

from __future__ import annotations

import codecs
import fcntl
import logging
import os
import shlex
import shutil
import signal
import struct
import subprocess
import termios
import threading
import time
from typing import Callable, List, Optional

from . import childenv

log = logging.getLogger("megacode")

#: How long stop() waits between SIGTERM and SIGKILL (seconds; interactive
#: bash ignores SIGTERM, so the escalation is not optional).
_KILL_GRACE_S = 0.05
_KILL_TICKS = 10


def _to_argv(command) -> List[str]:
    """Parse a command string into argv (POSIX quoting rules)."""
    if isinstance(command, str):
        return shlex.split(command, posix=True)
    return list(command)


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    """TIOCSWINSZ: struct order is rows, cols -- easy to transpose."""
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _child_session() -> None:
    """preexec_fn (runs in the forked child): new session + controlling tty.

    Without TIOCSCTTY the child merely HAS a tty (the dup'd fds) but no
    CONTROLLING one: no foreground process group is ever set, so the line
    discipline cannot deliver SIGINT when Ctrl+C (\\x03) is written to the
    master, bash job control stays off, and resize would not signal the
    child. ``start_new_session=True`` cannot fix this -- the slave must be
    acquired by the session leader itself, but the fd was opened by the
    parent. Pure syscalls, the pattern ptyprocess uses; safe under fork.
    """
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


class Pty:
    """A child process attached to a Unix pseudo-terminal."""

    #: ConPTY re-emits output with the console's cooked newline semantics
    #: (bare LF already means CR+LF); a Unix PTY does not, so the pyte LNM
    #: workaround in terminal_widget must NOT engage for this backend.
    LNM_WORKAROUND = False

    def __init__(
        self,
        command: str,
        cols: int,
        rows: int,
        cwd: Optional[str] = None,
    ) -> None:
        if cols < 1 or rows < 1:
            raise ValueError("cols and rows must be >= 1")
        argv = _to_argv(command)
        if not argv:
            raise ValueError("empty command")
        # resolve bare names ('claude') to a full path; an absolute path
        # passes through unchanged (Popen execs it without a shell)
        argv[0] = shutil.which(argv[0]) or argv[0]

        master_fd, slave_fd = os.openpty()
        try:
            _set_winsize(master_fd, cols, rows)
            # dict(os.environ) would hand the child the frozen bundle's
            # LD_LIBRARY_PATH: system tools run in the terminal would
            # resolve libstdc++ from /opt/MegaCode and die on missing
            # GLIBCXX symbols (see childenv)
            env = childenv.scrub_child_env(os.environ)
            # the widget parses DECSET 2004/1006/1000-1003 from the stream;
            # without TERM the shell's line editor negotiates none of them
            env.setdefault("TERM", "xterm-256color")
            try:
                self._proc = subprocess.Popen(
                    argv,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    cwd=cwd,
                    env=env,
                    close_fds=True,
                    # new session -> pgid == child pid, so stop() can kill
                    # the whole tree; + TIOCSCTTY so the slave becomes the
                    # child's controlling terminal (see _child_session)
                    preexec_fn=_child_session,
                )
            except OSError as exc:
                os.close(master_fd)  # the slave is closed in finally
                master_fd = -1
                raise RuntimeError(f"Failed to spawn: {command}") from exc
        finally:
            # the parent must not hold the slave open: read() on the master
            # only reports EOF once every slave descriptor is closed
            os.close(slave_fd)

        self._master = master_fd
        # setsid made pgid == child pid; remember it NOW -- after the child
        # is reaped, os.getpgid(pid) fails and stop() could no longer kill
        # surviving group members (claude/node grandchildren)
        self._pgid = self._proc.pid
        self._on_output: Optional[Callable[[str], None]] = None
        self._thread: Optional[threading.Thread] = None
        self._thread: Optional[threading.Thread] = None
        self._closed = False
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    @property
    def pid(self) -> int:
        return self._proc.pid

    # --- I/O ----------------------------------------------------------------
    def start(self, on_output: Callable[[str], None]) -> None:
        """Pump VT output to ``on_output`` on a background thread."""
        self._on_output = on_output
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        try:
            while not self._closed:
                try:
                    data = os.read(self._master, 65536)
                except OSError:
                    # EIO: child side fully closed (EOF); EBADF: master
                    # closed under us by is_alive()/stop() to wake this loop
                    break
                if not data:
                    break
                text = self._decoder.decode(data)
                if text and self._on_output:
                    try:
                        self._on_output(text)
                    except Exception:  # noqa: BLE001 (reader must not die)
                        log.exception("output callback failed")
            # flush a partially-received multibyte sequence as U+FFFD
            tail = self._decoder.decode(b"", final=True)
            if tail and self._on_output and not self._closed:
                try:
                    self._on_output(tail)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            log.exception("reader thread crashed")

    def write(self, text: str) -> None:
        if self._closed:
            return
        try:
            os.write(self._master, text.encode("utf-8"))
        except OSError:
            pass  # child gone; a dropped key matches the ConPTY behaviour

    def resize(self, cols: int, rows: int) -> None:
        try:
            _set_winsize(self._master, cols, rows)
            # the kernel delivers SIGWINCH to the slave's foreground group
        except Exception:  # noqa: BLE001
            log.debug("resize failed", exc_info=True)

    def is_alive(self) -> bool:
        if self._closed:
            return False
        try:
            pid, _status = os.waitpid(self._proc.pid, os.WNOHANG)
        except ChildProcessError:
            # already reaped (a previous is_alive/stop collected it)
            return False
        if pid == self._proc.pid:
            # reaped == exited; closing the master also unblocks the reader
            self._close_master()
            return False
        return True

    def stop(self) -> None:
        """Terminate the child process group (SIGTERM, then SIGKILL)."""
        if self._closed:
            return
        self._closed = True
        pgid = self._pgid  # remembered at spawn; getpgid fails once reaped
        if pgid:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(pgid, sig)
                except (ProcessLookupError, PermissionError):
                    break
                for _ in range(_KILL_TICKS):
                    try:
                        done, _ = os.waitpid(self._proc.pid, os.WNOHANG)
                        if done == self._proc.pid:
                            break
                    except ChildProcessError:
                        break
                    if sig is signal.SIGKILL:
                        break
                    time.sleep(_KILL_GRACE_S / _KILL_TICKS)
                else:
                    continue
                break
        try:
            self._proc.wait(timeout=1)
        except Exception:  # noqa: BLE001
            pass
        # Closing the master also SIGHUPs the terminal's foreground group
        # (the ctty from _child_session), catching any group member the
        # signals above missed; it additionally unblocks a reader thread
        # still parked on the fd once every slave descriptor is gone.
        self._close_master()

    def _close_master(self) -> None:
        fd, self._master = self._master, -1
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
