"""Offscreen tests for the AI-chat tile: widget + backend protocol parsing.

The widget is driven through a fake backend (same signals, no child process);
the backend's line parsing is tested by feeding it raw JSONL directly.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

import megacode.workspace as wsm  # noqa: E402
from megacode.chat_backend import ClaudeChatBackend  # noqa: E402
from megacode.chat_widget import ChatWidget, _MarkdownBubble  # noqa: E402


class FakeBackend(QObject):
    """Stands in for ``ClaudeChatBackend``: same signals, no process."""

    ready = Signal(str, str)
    delta = Signal(str)
    turn_finished = Signal()
    failed = Signal(str)

    def __init__(self, cwd: str, parent=None):  # noqa: ARG002
        super().__init__(parent)
        self.sent: list[str] = []
        self.interrupts = 0
        self.closed = False
        self.model = None
        self.effort = None
        self.applied = 0
        self.cwd = cwd
        self.extra_dirs: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)

    def interrupt(self) -> None:
        self.interrupts += 1

    def apply_settings(self) -> None:
        self.applied += 1

    def add_dir(self, path: str) -> bool:
        # mirrors ClaudeChatBackend.add_dir (incl. the inside-cwd skip)
        cleaned = os.path.normpath(os.path.abspath(path))
        case = os.path.normcase
        cwd = case(os.path.normpath(os.path.abspath(self.cwd)))
        if case(cleaned) == cwd or case(cleaned).startswith(cwd + os.sep):
            return False
        if any(case(d) == case(cleaned) for d in self.extra_dirs):
            return False
        self.extra_dirs.append(cleaned)
        return True

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture()
def chat(qapp):
    # isolate the "new tiles inherit the last selection" class state
    ChatWidget._last_model = None
    ChatWidget._last_effort = None
    widget = ChatWidget(os.getcwd(), backend_factory=lambda cwd: FakeBackend(cwd))
    widget.resize(600, 400)
    widget.show()
    qapp.processEvents()
    return widget


def _bubbles(chat, object_name):
    return [w for w in chat.findChildren(QObject) if w.objectName() == object_name]


# --- widget ------------------------------------------------------------------


def test_submit_starts_a_turn(chat):
    fake = chat.backend
    chat.submit("hello")
    assert fake.sent == ["hello"]
    user = _bubbles(chat, "chatUserBubble")
    assert len(user) == 1 and user[0].toPlainText() == "hello"
    # streaming state: stop glyph, property set for QSS
    assert chat._busy
    assert chat._send_btn.text() == "■"
    assert chat._send_btn.property("streaming") == "true"
    # the empty state is hidden from the first message on
    empty = _bubbles(chat, "chatEmpty")
    assert empty and not empty[0].isVisible()


def test_empty_submit_is_ignored(chat):
    chat.submit("   ")
    assert chat.backend.sent == []
    assert not chat._busy


def test_enter_sends_shift_enter_breaks_line(chat):
    QTest.keyClicks(chat._input, "hi there")
    QTest.keyClick(chat._input, Qt.Key.Key_Return)
    assert chat.backend.sent == ["hi there"]
    assert chat._input.toPlainText() == ""

    QTest.keyClicks(chat._input, "line one")
    QTest.keyClick(chat._input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClicks(chat._input, "line two")
    assert chat._input.toPlainText() == "line one\nline two"


def test_delta_streams_into_markdown_bubble(chat, qapp):
    chat.submit("question")
    chat.backend.delta.emit("Hello **bold** ")
    chat._flush_now()  # skip the 80ms coalescing timer
    qapp.processEvents()
    bubbles = [w for w in _bubbles(chat, "chatAssistantBubble") if isinstance(w, _MarkdownBubble)]
    assert len(bubbles) == 1
    assert "bold" in bubbles[0].toPlainText()
    # cursor shown while streaming
    chat.backend.delta.emit("world")
    chat._flush_now()
    qapp.processEvents()
    assert "▍" in bubbles[0].toPlainText()


def test_turn_finished_finalizes_and_reenables(chat, qapp):
    chat.submit("q")
    chat.backend.delta.emit("answer text")
    chat.backend.turn_finished.emit()
    qapp.processEvents()
    assert not chat._busy
    assert chat._send_btn.text() == "↑"
    bubble = [w for w in _bubbles(chat, "chatAssistantBubble") if isinstance(w, _MarkdownBubble)][0]
    assert "answer text" in bubble.toPlainText()
    # final render drops the streaming cursor
    assert "▍" not in bubble.toPlainText()


def test_failed_adds_error_bubble(chat, qapp):
    chat.submit("q")
    chat.backend.failed.emit("claude exploded")
    qapp.processEvents()
    assert not chat._busy
    errors = [w for w in _bubbles(chat, "chatErrorBubble") if isinstance(w, QLabel)]
    assert len(errors) == 1
    assert "claude exploded" in errors[0].text()


def test_send_button_is_stop_while_busy(chat):
    chat.submit("q")
    chat._send_btn.click()
    assert chat.backend.interrupts == 1
    # the process-death path ends the turn; the button goes back to send
    chat.backend.turn_finished.emit()
    assert chat._send_btn.text() == "↑"


def test_escape_interrupts(chat):
    chat.submit("q")
    QTest.keyClick(chat._input, Qt.Key.Key_Escape)
    assert chat.backend.interrupts == 1


def test_close_closes_backend(chat):
    chat.close()
    assert chat.backend.closed


def test_thinking_indicator_animates(chat):
    chat.submit("q")
    dots = chat._dots
    assert dots is not None and dots.text() == "Thinking"
    initial_width = dots.width()
    for _ in range(3):
        chat._tick_dots()
    assert dots.text() == "Thinking..."
    # no layout manages the label: it must resize itself or the dots clip
    assert dots.width() >= dots.fontMetrics().horizontalAdvance("Thinking...")
    assert dots.width() > initial_width
    # the first text delta replaces the indicator
    chat.backend.delta.emit("answer")
    assert chat._dots is None and not dots.isVisible()


# --- model / effort selection ------------------------------------------------


def test_model_effort_default_to_cli_settings(chat):
    assert chat._model_combo.currentText() == "Default"
    assert chat._effort_combo.currentText() == "Default"
    assert chat.backend.model is None and chat.backend.effort is None


def test_selecting_model_and_effort_updates_backend(chat):
    chat._model_combo.setCurrentIndex(chat._model_combo.findData("glm-4.7"))
    assert chat.backend.model == "glm-4.7"
    assert chat.backend.applied == 1

    chat._effort_combo.setCurrentIndex(chat._effort_combo.findData("max"))
    assert chat.backend.effort == "max"
    assert chat.backend.applied == 2

    # back to Default -> flags are dropped again
    chat._model_combo.setCurrentIndex(chat._model_combo.findData(None))
    assert chat.backend.model is None


def test_new_chat_tile_inherits_last_selection(chat):
    chat._model_combo.setCurrentIndex(chat._model_combo.findData("glm-4.6v"))
    chat._effort_combo.setCurrentIndex(chat._effort_combo.findData("low"))
    second = ChatWidget(os.getcwd(), backend_factory=lambda c: FakeBackend(c))
    assert second.backend.model == "glm-4.6v"
    assert second.backend.effort == "low"
    assert second._model_combo.currentText() == "glm-4.6v"


def test_backend_argv_carries_model_and_effort(backend):
    from megacode import shells as shells_mod

    if shells_mod.find_claude() is None:
        pytest.skip("claude CLI not on PATH")
    backend.model = "glm-4.6v"
    backend.effort = "high"
    argv = backend._argv()
    assert argv[argv.index("--model") + 1] == "glm-4.6v"
    assert argv[argv.index("--effort") + 1] == "high"
    # defaults send no flags at all
    backend.model = backend.effort = None
    argv = backend._argv()
    assert "--model" not in argv and "--effort" not in argv


def test_apply_settings_restarts_after_running_turn(backend, qapp):
    from PySide6.QtCore import QProcess

    backend._proc = QProcess()  # a live-looking process (never started)
    backend._busy = True
    backend.apply_settings()
    assert backend._restart_pending is True
    got = _collect(backend)
    backend._feed(json.dumps({
        "type": "result", "subtype": "success", "is_error": False, "result": "ok",
    }) + "\n")
    assert got["turns"] == 1
    assert backend._restart_pending is False
    assert backend._proc is None  # killed: the next send respawns with new args


def test_apply_settings_without_process_is_noop(backend):
    backend.apply_settings()  # nothing running; must not raise or flag
    assert backend._restart_pending is False


# --- drag & drop of files -------------------------------------------------------


def test_backend_add_dir_skips_cwd_and_dedups(backend, qapp, monkeypatch, tmp_path):
    inside = os.path.join(os.getcwd(), "some", "sub")
    assert backend.add_dir(inside) is False
    assert backend.extra_dirs == []

    outside = str(tmp_path)
    assert backend.add_dir(outside) is True
    assert backend.add_dir(outside) is False  # dedup
    assert backend.add_dir(outside.lower()) is False or len(backend.extra_dirs) == 1

    from megacode import shells as shells_mod

    if shells_mod.find_claude() is None:
        pytest.skip("claude CLI not on PATH")
    argv = backend._argv()
    assert "--add-dir" in argv
    assert argv[argv.index("--add-dir") + 1] == os.path.normpath(outside)
    assert argv.index("--add-dir") > argv.index("--effort") if "--effort" in argv else True


def test_dropped_outside_file_inserts_path_and_grants_dir(chat, qapp, monkeypatch, tmp_path):
    from PySide6.QtCore import QUrl

    dropped = tmp_path / "note with spaces.txt"
    dropped.write_text("hi", encoding="utf-8")
    chat._insert_dropped_urls([QUrl.fromLocalFile(str(dropped))])
    qapp.processEvents()
    # the path is quoted (it has spaces) and sits in the composer
    assert f'"{dropped}"' in chat._input.toPlainText()
    # claude got access to the file's folder before the message can be sent
    assert chat.backend.extra_dirs == [os.path.normpath(str(tmp_path))]
    assert chat.backend.applied == 1


def test_dropped_inside_file_needs_no_grant(chat, qapp, tmp_path):
    from PySide6.QtCore import QUrl

    inside = os.path.join(os.getcwd(), "local_file.txt")
    chat._insert_dropped_urls([QUrl.fromLocalFile(inside)])
    qapp.processEvents()
    assert inside in chat._input.toPlainText()
    assert chat.backend.extra_dirs == []
    assert chat.backend.applied == 0


def test_input_grows_and_caps(chat):
    from PySide6.QtGui import QFontMetrics

    from megacode.chat_widget import _INPUT_CHROME

    # setFixedHeight applies synchronously; the laid-out geometry may lag
    one = chat._input.minimumHeight()
    chat._input.setPlainText("line\n" * 5)
    grown = chat._input.minimumHeight()
    chat._input.setPlainText("line\n" * 50)
    capped = chat._input.minimumHeight()
    cap = QFontMetrics(chat._input.font()).lineSpacing() * 6 + _INPUT_CHROME
    assert one < grown
    assert grown <= cap          # 5 lines are still below the cap
    assert capped == cap         # 50 lines stop at the 6-row cap


# --- backend protocol parsing ---------------------------------------------------


@pytest.fixture()
def backend(qapp):
    return ClaudeChatBackend(os.getcwd())


def _collect(b: ClaudeChatBackend) -> dict:
    got = {"ready": [], "deltas": [], "turns": 0, "fails": []}
    b.ready.connect(lambda sid, model: got["ready"].append((sid, model)))
    b.delta.connect(lambda text: got["deltas"].append(text))
    b.turn_finished.connect(lambda: got.__setitem__("turns", got["turns"] + 1))
    b.failed.connect(lambda msg: got["fails"].append(msg))
    return got


def test_backend_init_delta_result(backend):
    got = _collect(backend)
    backend._feed(json.dumps({
        "type": "system", "subtype": "init",
        "session_id": "s1", "model": "glm-test", "tools": [],
    }) + "\n")
    backend._feed(json.dumps({
        "type": "stream_event",
        "event": {"type": "content_block_delta", "index": 0,
                  "delta": {"type": "text_delta", "text": "Hi "}},
    }) + "\n")
    backend._feed(json.dumps({
        "type": "stream_event",
        "event": {"type": "content_block_delta", "index": 0,
                  "delta": {"type": "text_delta", "text": "there"}},
    }) + "\n")
    backend._feed(json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi there"}]},
    }) + "\n")
    backend._feed(json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "result": "Hi there", "session_id": "s1",
    }) + "\n")
    assert got["ready"] == [("s1", "glm-test")]
    assert got["deltas"] == ["Hi ", "there"]
    assert got["turns"] == 1 and not got["fails"]
    # the resume argument chain now has a session to continue
    from megacode import shells as shells_mod

    if shells_mod.find_claude() is None:
        pytest.skip("claude CLI not on PATH")
    argv = backend._argv()
    assert argv is not None and "--resume" in argv and "s1" in argv


def test_backend_ignores_noise_events(backend):
    got = _collect(backend)
    for line in (
        {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 3},
        {"type": "system", "subtype": "status", "status": "working"},
        {"type": "stream_event", "event": {"type": "content_block_start", "index": 0}},
        {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "thinking_delta", "thinking": "hm"}}},
        {"type": "user", "message": {"role": "user", "content": []}},
    ):
        backend._feed(json.dumps(line) + "\n")
    assert got["deltas"] == [] and got["turns"] == 0 and not got["fails"]


def test_backend_partial_lines_and_split_json(backend):
    got = _collect(backend)
    half = json.dumps({
        "type": "stream_event",
        "event": {"type": "content_block_delta", "index": 0,
                  "delta": {"type": "text_delta", "text": "split"}},
    })
    backend._feed(half[:20])              # a chunk boundary mid-JSON
    assert got["deltas"] == []
    backend._feed(half[20:] + "\n")
    assert got["deltas"] == ["split"]


def test_backend_error_result(backend):
    got = _collect(backend)
    backend._feed(json.dumps({
        "type": "result", "subtype": "error_during_execution",
        "is_error": True, "result": "API key invalid",
    }) + "\n")
    assert got["turns"] == 0
    assert len(got["fails"]) == 1 and "API key invalid" in got["fails"][0]


def test_backend_result_without_deltas_falls_back(backend):
    got = _collect(backend)
    backend._feed(json.dumps({
        "type": "result", "subtype": "success", "is_error": False, "result": "full text",
    }) + "\n")
    assert got["deltas"] == ["full text"]
    assert got["turns"] == 1


# --- workspace integration --------------------------------------------------------


class _FakeTerminal(QWidget):
    """Stands in for ``TerminalWidget``: same API, no child process."""

    finished = Signal()
    inputSent = Signal(str, bool)
    pendingChanged = Signal()

    def __init__(self, _command, _cwd=None, font_size=10, parent=None):  # noqa: ARG002
        super().__init__(parent)

    def is_dead(self):
        return False

    def has_pending_input(self):
        return False

    def tick(self):
        return None

    def close(self):
        return None


def test_workspace_add_chat_tile(qapp, monkeypatch):
    monkeypatch.setattr(wsm, "TerminalWidget", _FakeTerminal)
    ws = wsm.WorkspaceView()
    ws.resize(1200, 800)
    ws.start(1, "fake", os.getcwd(), font_size=10, label="term")
    qapp.processEvents()

    monkeypatch.setattr(
        wsm, "ChatWidget",
        lambda cwd, font_size=10: ChatWidget(cwd, backend_factory=lambda c: FakeBackend(c)),
    )
    ws.add_kind("chat")
    qapp.processEvents()
    assert len(ws.tiles) == 2
    chat_tile = ws.tiles[1]
    assert isinstance(chat_tile.terminal, ChatWidget)
    assert not chat_tile.terminal.is_dead()

    # the chat tile participates in cleanup like any terminal
    ws.cleanup()
    qapp.processEvents()
    assert chat_tile.terminal.backend.closed


def test_shells_know_chat():
    from megacode import shells

    kinds = [kind for _label, kind in shells.RUN_KINDS]
    assert "chat" in kinds
    assert shells.label_for("chat") == "chat"
    assert shells.resolve("chat") == shells.find_claude()
