"""Render an AI-chat tile to a PNG with a scripted conversation (offscreen).

Dev tool: shows the chat UI with the app stylesheet applied without talking
to the real claude CLI.   .venv/Scripts/python.exe scripts/chat_preview.py
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import megacode.workspace as wsm  # noqa: E402
from megacode.app import QSS  # noqa: E402
from megacode.chat_widget import ChatWidget  # noqa: E402


class _FakeTerminal(QWidget):
    """Stands in for ``TerminalWidget``: no child process."""

    finished = Signal()

    def __init__(self, _command, _cwd=None, font_size=10, parent=None):  # noqa: ARG002
        super().__init__(parent)

    def is_dead(self) -> bool:
        return False

    def tick(self) -> None:
        return None

    def close(self) -> None:
        return None


class ScriptedBackend(QObject):
    """A fake backend that never talks to a process."""

    ready = Signal(str, str)
    delta = Signal(str)
    turn_finished = Signal()
    failed = Signal(str)

    def __init__(self, cwd: str, parent=None):  # noqa: ARG002
        super().__init__(parent)

    def send(self, text: str) -> None:  # noqa: ARG002
        pass

    def interrupt(self) -> None:
        pass

    def close(self) -> None:
        pass


def main() -> int:
    app = QApplication([])
    app.setStyleSheet(QSS)

    wsm.TerminalWidget = _FakeTerminal
    wsm.ChatWidget = lambda cwd, font_size=10: ChatWidget(
        cwd, backend_factory=lambda c: ScriptedBackend(c)
    )

    view = wsm.WorkspaceView()
    view.resize(900, 720)
    view.start(1, "fake", os.getcwd(), font_size=12, label="chat", kind="chat")
    view.show()
    app.processEvents()

    chat = view.tiles[0].terminal
    fake = chat.backend

    chat.submit("What does this snippet do?")
    fake.ready.emit("sess-1", "claude-test-model")
    fake.delta.emit(
        "It greets the world:\n\n"
        "```python\n"
        "def hello():\n"
        "    print('hello, world')\n"
        "```\n\n"
        "The `print` call writes to **stdout**. See [the docs](https://example.com).\n"
    )
    chat._flush_now()
    fake.turn_finished.emit()
    chat.submit("And a second, longer question about streams and scrolling behaviour?")
    fake.delta.emit("Working on it: the quick brown fox jumps over ")
    chat._flush_now()
    app.processEvents()

    out = os.path.join(os.path.dirname(__file__), "..", "chat_preview.png")
    shot = view.grab()
    img = shot.toImage()
    warm = sum(
        1
        for y in range(img.height())
        for x in range(img.width())
        if (c := img.pixelColor(x, y)).red() > 110
        and c.red() > c.green() + 30
        and c.green() > c.blue() + 10
    )
    print("in-memory warm pixels:", warm)
    shot.save(os.path.abspath(out))
    print("saved", os.path.abspath(out))
    view.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
