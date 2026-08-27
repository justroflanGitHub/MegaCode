"""The AI-chat backend: one persistent ``claude`` CLI session over stdio.

:class:`ClaudeChatBackend` spawns the Claude Code CLI in its machine-readable
streaming mode (``--input-format stream-json --output-format stream-json``)
and keeps the process alive for the whole conversation: every user turn is one
JSON line written to stdin, every assistant token arrives as a parsed event on
stdout. There is no thread of our own -- :class:`QProcess` drives everything
and its signals fire on the UI thread.

If the process dies (crash, stop button, closed session) the backend restarts
it transparently on the next send with ``--resume <session_id>``, so the
conversation continues where it left off.
"""

from __future__ import annotations

import json
import logging
import os
import re
import warnings
from typing import List, Optional

from PySide6.QtCore import QObject, QProcess, Signal

from . import shells

log = logging.getLogger("megacode")

#: stderr is only used for error bubbles; keep the tail, strip ANSI noise.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def delta_text(event: dict) -> str:
    """The streaming text carried by one ``stream_event``, or ``""``."""
    if event.get("type") != "content_block_delta":
        return ""
    delta = event.get("delta") or {}
    if delta.get("type") == "text_delta":
        return delta.get("text") or ""
    return ""


class ClaudeChatBackend(QObject):
    """Talks to one long-lived ``claude`` process (multi-turn chat)."""

    ready = Signal(str, str)   # (session_id, model) -- fires once per session
    delta = Signal(str)        # a piece of assistant text
    turn_finished = Signal()   # the assistant is done with this turn
    failed = Signal(str)       # human-readable error; the turn was aborted

    def __init__(
        self,
        cwd: str,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._cwd = cwd
        # optional per-tile overrides, passed to the CLI as --model/--effort;
        # read on every start(), so changing them mid-session just needs a
        # (quiet) restart -- --resume keeps the conversation
        self.model: Optional[str] = model
        self.effort: Optional[str] = effort
        #: Extra directories claude may read from (``--add-dir``), granted
        #: when a file from outside the cwd is dropped into the chat: in
        #: ``-p`` mode reads outside the working directories are auto-denied.
        self.extra_dirs: List[str] = []
        self._restart_pending = False  # settings changed mid-turn: restart after
        self._proc: Optional[QProcess] = None
        self._session_id: Optional[str] = None
        self._model: Optional[str] = None
        self._buffer = b""       # stdout chunks can split a JSON line -- or a
        #                          multi-byte UTF-8 char -- in half, so buffer
        #                          raw bytes and decode only complete lines
        self._stderr_tail = ""
        self._busy = False       # a turn is in flight
        self._deltas_seen = False  # any stream delta arrived for this turn
        self._aborted = False    # the user pressed Stop; don't cry about the exit
        self._closing = False    # close() was called; never restart

    # --- process lifecycle --------------------------------------------------
    def _argv(self) -> Optional[List[str]]:
        exe = shells.find_claude()
        if not exe:
            return None
        # Wire format verified against claude CLI 2.1.241: one long-lived
        # process reads {"type":"user","message":{...}} lines from stdin and
        # answers each with stream_event deltas + a final "result" event.
        # Without --include-partial-messages only complete messages arrive.
        args = [
            "-p", "--verbose",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--include-partial-messages",
        ]
        if self._session_id:
            # resuming a killed/aborted session keeps the whole conversation
            args += ["--resume", self._session_id]
        if self.model:
            args += ["--model", self.model]
        if self.effort:
            # verified against claude CLI 2.1.241: low/medium/high/xhigh/max
            args += ["--effort", self.effort]
        if self.extra_dirs:
            # variadic flag: keep it last so it can't swallow other options
            args += ["--add-dir", *self.extra_dirs]
        if exe.lower().endswith((".cmd", ".bat")):
            # npm shim: a batch file needs a shell host to interpret it
            host = os.environ.get("COMSPEC") or "cmd.exe"
            return [host, "/c", exe, *args]
        return [exe, *args]

    def start(self) -> bool:
        """Spawn the claude process (fresh, or resumed after a kill)."""
        argv = self._argv()
        if argv is None:
            self.failed.emit("The 'claude' command was not found on PATH.")
            return False
        proc = QProcess(self)
        proc.setWorkingDirectory(self._cwd)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._buffer = b""
        self._stderr_tail = ""
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_proc_finished)
        proc.errorOccurred.connect(self._on_proc_error)
        self._proc = proc
        log.info("chat: starting %s", " ".join(argv[:1] + argv[1:3]))
        proc.start(argv[0], argv[1:])
        return True

    def _ensure_running(self) -> bool:
        if self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning:
            return True
        return self.start()

    # --- turns ---------------------------------------------------------------
    def send(self, text: str) -> None:
        """Send one user message; events stream back via the signals."""
        if self._closing:
            return
        if not self._ensure_running():
            return
        if self._proc is None:
            # FailedToStart fires synchronously inside start() on Windows and
            # has already emitted ``failed``; nothing to write to
            return
        self._busy = True
        self._aborted = False
        self._deltas_seen = False
        message = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": text}],
            },
        }
        # a plain-string content field works too; blocks are what the SDK sends
        data = json.dumps(message, ensure_ascii=False) + "\n"
        self._proc.write(data.encode("utf-8"))

    def add_dir(self, path: str) -> bool:
        """Grant claude access to an extra directory (``--add-dir``).

        Used when a file from outside the cwd is dropped into the chat -- in
        ``-p`` mode such reads are otherwise auto-denied. Returns True when
        the set changed (the caller should ``apply_settings()``).
        """
        cleaned = os.path.normpath(os.path.abspath(path))
        case = os.path.normcase
        cwd = case(os.path.normpath(os.path.abspath(self._cwd)))
        if case(cleaned) == cwd or case(cleaned).startswith(cwd + os.sep):
            return False  # inside the working folder: already readable
        if any(case(d) == case(cleaned) for d in self.extra_dirs):
            return False
        self.extra_dirs.append(cleaned)
        return True

    def apply_settings(self) -> None:
        """Model/effort changed: make the next turn use them.

        Idle -> quietly restart now (the conversation resumes on next send);
        busy -> restart right after the running turn finishes.
        """
        if self._proc is None:
            return  # nothing running; start() reads the new values anyway
        if self._busy:
            self._restart_pending = True
        else:
            self._restart()

    def _restart(self) -> None:
        """Kill the process quietly; the next send resumes the session."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        for signal, slot in (
            (proc.readyReadStandardOutput, self._on_stdout),
            (proc.readyReadStandardError, self._on_stderr),
            (proc.finished, self._on_proc_finished),
            (proc.errorOccurred, self._on_proc_error),
        ):
            # a never-started QProcess has no connections: PySide6 only emits
            # a RuntimeWarning there, so silence it rather than catch
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                try:
                    signal.disconnect(slot)
                except RuntimeError:
                    pass
        proc.kill()
        proc.deleteLater()

    def interrupt(self) -> None:
        """Stop the current turn: kill the process; ``--resume`` continues later."""
        self._aborted = True
        self._busy = False
        if self._proc is not None:
            proc = self._proc
            # drop the reference immediately: the kill is asynchronous, and a
            # send() landing in that window must restart (--resume) instead of
            # writing into the dying process
            self._proc = None
            proc.kill()

    def close(self) -> None:
        """Kill the session for good (tile closed / app shutting down)."""
        self._closing = True
        self._restart_pending = False
        self._restart()

    # --- stdout parsing --------------------------------------------------------
    def _on_stdout(self) -> None:
        if self._proc is None:
            return
        self._feed_bytes(bytes(self._proc.readAllStandardOutput()))

    def _feed(self, chunk: str) -> None:
        """Test hook: feed stdout text; ``_feed_bytes`` does the real work."""
        self._feed_bytes(chunk.encode("utf-8"))

    def _feed_bytes(self, chunk: bytes) -> None:
        """Buffer raw stdout bytes and handle every complete JSON line in it."""
        self._buffer += chunk
        *lines, self._buffer = self._buffer.split(b"\n")
        for line in lines:
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                self._handle_line(text)

    def _handle_line(self, line: str) -> None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            log.debug("chat: unparseable line: %.120s", line)
            return
        etype = event.get("type")
        if etype == "system" and event.get("subtype") == "init":
            self._session_id = event.get("session_id") or self._session_id
            self._model = event.get("model") or self._model
            self.ready.emit(self._session_id or "", self._model or "")
        elif etype == "stream_event":
            text = delta_text(event.get("event") or {})
            if text:
                self._deltas_seen = True
                self.delta.emit(text)
        elif etype == "assistant":
            pass  # the complete message; the deltas already carried its text
        elif etype == "result":
            self._on_result(event)

    def _on_result(self, event: dict) -> None:
        self._busy = False
        if self._restart_pending:
            # model/effort changed while this turn was running
            self._restart_pending = False
            self._restart()
        if event.get("is_error"):
            message = str(event.get("result") or "claude returned an error.")
            self.failed.emit(self._with_stderr(message))
            return
        if not self._deltas_seen:
            # No stream events arrived (e.g. an immediate answer): the result
            # field holds the full text, so deliver it as one delta.
            text = str(event.get("result") or "")
            if text:
                self.delta.emit(text)
        self.turn_finished.emit()

    def _with_stderr(self, message: str) -> str:
        tail = _ANSI_RE.sub("", self._stderr_tail).strip()
        if tail:
            return f"{message}\n{tail[-400:]}"
        return message

    # --- process teardown --------------------------------------------------------
    def _on_stderr(self) -> None:
        if self._proc is None:
            return
        chunk = bytes(self._proc.readAllStandardError()).decode("utf-8", errors="replace")
        self._stderr_tail = (self._stderr_tail + chunk)[-2000:]

    def _on_proc_error(self, error) -> None:  # noqa: ANN001 (Qt enum)
        if error == QProcess.ProcessError.FailedToStart:
            proc, self._proc = self._proc, None
            self._busy = False
            if proc is not None:
                proc.deleteLater()
            self.failed.emit("Could not start the 'claude' CLI.")

    def _on_proc_finished(self, code: int, _status) -> None:  # noqa: ANN001
        # the sender (not self._proc) identifies the dying process: interrupt()
        # may already have dropped the reference
        proc = self.sender()
        if isinstance(proc, QProcess):
            proc.deleteLater()  # don't accumulate dead wrappers per restart
            if self._proc is proc:
                self._proc = None
        else:
            self._proc = None
        if self._closing:
            return
        # the process is dead: a pending settings-restart has nothing left to
        # restart -- the next send starts a fresh process with the new args
        self._restart_pending = False
        if self._aborted:
            # the user asked for this: end the turn quietly
            self._aborted = False
            self.turn_finished.emit()
        elif self._busy:
            self._busy = False
            self.failed.emit(self._with_stderr(f"claude exited unexpectedly (code {code})."))
        # an idle process dying is fine: the next send restarts it (--resume)
