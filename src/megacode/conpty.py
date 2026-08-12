"""Terminal process backed by ``pywinpty`` (Windows ConPTY).

A thin, dependency-light wrapper exposing only what the embedded terminal widget
needs: spawn a child attached to a pseudo-console, read its VT output stream as
``str``, feed it keystrokes, resize, and stop. pywinpty is battle-tested
(used by Jupyter/Spyder) and sidesteps the fiddly ``STARTUPINFOEX`` plumbing.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from typing import Callable, Optional

import winpty

log = logging.getLogger("megacode")

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.argtypes = [ctypes.c_uint64, ctypes.c_int, ctypes.c_uint32]
_kernel32.OpenProcess.restype = ctypes.c_void_p
_kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
_kernel32.TerminateProcess.restype = ctypes.c_int
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
_kernel32.CloseHandle.restype = ctypes.c_int

_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000


class Pty:
    """A child process attached to a Windows pseudo-console."""

    def __init__(
        self,
        command: str,
        cols: int,
        rows: int,
        cwd: Optional[str] = None,
    ) -> None:
        if cols < 1 or rows < 1:
            raise ValueError("cols and rows must be >= 1")
        self._pty = winpty.PTY(cols, rows)
        if not self._pty.spawn(command, cwd=cwd):
            raise RuntimeError(f"Failed to spawn: {command}")
        self._pid = int(self._pty.pid)
        self._on_output: Optional[Callable[[str], None]] = None
        self._thread: Optional[threading.Thread] = None
        self._closed = False

    @property
    def pid(self) -> int:
        return self._pid

    # --- I/O ----------------------------------------------------------------
    def start(self, on_output: Callable[[str], None]) -> None:
        """Pump VT output to ``on_output`` on a background thread."""
        self._on_output = on_output
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        try:
            while not self._closed and self._pty.isalive():
                data = self._pty.read()
                if data:
                    if self._on_output:
                        try:
                            self._on_output(data)
                        except Exception:  # noqa: BLE001 (reader must not die)
                            log.exception("output callback failed")
                elif self._pty.iseof():
                    break
                else:
                    time.sleep(0.01)
        except Exception:  # noqa: BLE001
            log.exception("reader thread crashed")

    def write(self, text: str) -> None:
        if not self._closed:
            self._pty.write(text)

    def resize(self, cols: int, rows: int) -> None:
        try:
            self._pty.set_size(cols, rows)
        except Exception:  # noqa: BLE001
            log.debug("resize failed", exc_info=True)

    def is_alive(self) -> bool:
        if self._closed:
            return False
        try:
            return self._pty.isalive()
        except Exception:  # noqa: BLE001
            return False

    def stop(self) -> None:
        """Terminate the child process."""
        if self._closed:
            return
        self._closed = True
        handle = _kernel32.OpenProcess(_PROCESS_TERMINATE | _SYNCHRONIZE, False, self._pid)
        if handle:
            _kernel32.TerminateProcess(handle, 0)
            _kernel32.CloseHandle(handle)
