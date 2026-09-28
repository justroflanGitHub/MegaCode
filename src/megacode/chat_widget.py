"""An embeddable AI-chat tile: web-chatbot looks, dark, streaming.

:class:`ChatWidget` speaks the same tile interface as
:class:`~megacode.terminal_widget.TerminalWidget` (a ``finished`` signal plus
``is_dead`` / ``tick`` / ``close`` / ``setFocus``), so a
:class:`~megacode.workspace.TerminalTile` can host either a real terminal or
this chat. All claude interaction lives in
:class:`~megacode.chat_backend.ClaudeChatBackend`; tests inject a fake through
``backend_factory``.

The visual design (bubbles, input bar, scrollbar) is styled from the app-wide
QSS in :mod:`megacode.app` via the ``chat*`` objectNames used here.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QEvent, QObject, QDir, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QFontMetrics, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .chat_backend import ClaudeChatBackend

log = logging.getLogger("megacode")

#: Assistant markdown is rendered by QTextDocument; this stylesheet themes
#: code blocks / inline code / links to match the app palette (QSS cannot
#: reach inside a QTextDocument -- app.py owns the widget chrome).
_DOC_CSS = """
pre { background-color: #0d0f14; color: #c9d1dc; }
code { font-family: "Consolas", "Cascadia Mono", "DejaVu Sans Mono", "Liberation Mono", monospace; font-size: 12px; }
a { color: #d97757; }
p, li { margin: 0; }
"""

#: The block cursor shown after streaming text while a turn is in flight.
_CURSOR_HTML = '<span style="color:#d97757;">▍</span>'

#: Frames of the "Thinking…" indicator shown until the first text arrives
#: (covers the model's reasoning/thinking phase).
_THINKING_FRAMES = ("Thinking", "Thinking.", "Thinking..", "Thinking...")

_ACCENT = "#d97757"


def _unwrap_code_blocks(doc) -> None:
    """Let fenced code blocks wrap.

    Qt's markdown importer marks code blocks ``nonBreakableLines``, so a long
    code line lays out as one unwrappable line and vanishes past the bubble
    edge (the horizontal scrollbar is off by design). Clearing the flag makes
    code wrap like every web chat renders it.
    """
    cursor = QTextCursor(doc)
    block = doc.firstBlock()
    while block.isValid():
        fmt = block.blockFormat()
        if fmt.nonBreakableLines():
            fmt.setNonBreakableLines(False)
            cursor.setPosition(block.position())
            cursor.setBlockFormat(fmt)
        block = block.next()


def _select(combo: QComboBox, value: Optional[str]) -> None:
    """Select the item whose data is ``value`` (None = the "Default" entry)."""
    for i in range(combo.count()):
        if combo.itemData(i) == value:
            combo.setCurrentIndex(i)
            return


def _tint_links(doc) -> None:
    """Recolor markdown anchors to the accent color.

    QTextDocument's markdown importer ignores the document's default
    stylesheet for links (they stay Qt-blue), so walk the fragments and set
    the foreground on anchor formats explicitly.
    """
    block = doc.firstBlock()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            fmt = frag.charFormat()
            if fmt.isAnchor() and fmt.anchorHref():
                cursor = QTextCursor(doc)
                cursor.setPosition(frag.position())
                cursor.setPosition(
                    frag.position() + frag.length(), QTextCursor.MoveMode.KeepAnchor
                )
                fmt.setForeground(QColor(_ACCENT))
                cursor.setCharFormat(fmt)
            it += 1
        block = block.next()

#: ms between markdown re-renders of the streaming message.
_FLUSH_INTERVAL = 80

#: vertical padding added to the composer's computed text height (matches the
#: QSS padding + border in app.py; tests use the same constant).
_INPUT_CHROME = 18

#: Pickable models in the chat's selector: (label, ``--model`` value).
#: "Default" sends no flag (whatever the CLI is configured with). Edit this
#: list to expose other models.
CHAT_MODELS: List[Tuple[str, Optional[str]]] = [
    ("Default", None),
    ("glm-5.3", "glm-5.3"),
    ("glm-5-turbo", "glm-5-turbo"),
    ("glm-4.7", "glm-4.7"),
    ("glm-4.6v", "glm-4.6v"),
]

#: Pickable reasoning efforts: (label, ``--effort`` value). Verified against
#: claude CLI 2.1.241 (valid: low, medium, high, xhigh, max).
CHAT_EFFORTS: List[Tuple[str, Optional[str]]] = [
    ("Default", None),
    ("low", "low"),
    ("high", "high"),
    ("max", "max"),
]


class _MarkdownBubble(QTextBrowser):
    """A QTextBrowser that grows to fit its rendered document.

    QTextBrowser's own sizeHint is a fixed viewport default; for a chat
    transcript we want the bubble to be exactly as tall as its text and let
    the outer scroll area do the scrolling.
    """

    def __init__(
        self, parent: Optional[QWidget] = None, object_name: str = "chatAssistantBubble"
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Minimum)
        self.setOpenExternalLinks(True)
        self.setOpenLinks(False)
        self.document().setDocumentMargin(0)
        self.document().setDefaultStyleSheet(_DOC_CSS)

    def set_markdown(self, text: str) -> None:
        self.setMarkdown(text)
        _unwrap_code_blocks(self.document())
        _tint_links(self.document())
        self._refit()

    def set_plain(self, text: str) -> None:
        """Plain text (user messages): no markdown, still document-rendered so
        over-long words wrap instead of clipping."""
        self.setPlainText(text)
        self._refit()

    def show_stream_cursor(self) -> None:
        """Append the blinking block cursor after the streaming text."""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(_CURSOR_HTML)
        self._refit()

    def _refit(self) -> None:
        """Grow the bubble to exactly fit its (re-flowed) document.

        A QTextBrowser inside a layout won't size itself to its content on its
        own -- its default sizeHint is a fixed viewport -- so wrap the text at
        the current widget width and pin the height to the document's. The
        QSS border + padding are not part of the viewport, so they are added
        on top (measured, so a stylesheet change just works).
        """
        self.document().setTextWidth(self.viewport().width())
        height = int(self.document().documentLayout().documentSize().height())
        chrome = max(2, self.height() - self.viewport().height())
        wanted = max(height + chrome + 2, 24)
        if abs(wanted - self.height()) > 1:
            self.setFixedHeight(wanted)
        self._notify_layout()

    def _doc_height(self) -> int:
        return int(self.document().documentLayout().documentSize().height())

    def _notify_layout(self) -> None:
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None and parent.layout() is not None:
            parent.layout().invalidate()

    def sizeHint(self):  # noqa: N802 (Qt signature)
        hint = super().sizeHint()
        hint.setHeight(max(self._doc_height(), 20))
        return hint

    def minimumSizeHint(self):  # noqa: N802 (Qt signature)
        return self.sizeHint()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        super().resizeEvent(event)
        # the wrap width changed: re-flow and re-fit the height
        self._refit()


class _MessageRow(QWidget):
    """One chat message: a bubble + a hover copy button, aligned per role.

    ``align`` is ``"left"`` for assistant/error messages and ``"right"`` for
    the user's own. Assistant bubbles stretch up to their maximumWidth (the
    transcript's 82% cap) like claude.ai's response column; user bubbles hug
    their text.
    """

    def __init__(self, bubble: QWidget, align: str = "left") -> None:
        super().__init__()
        self.bubble = bubble
        self.copy_btn = QToolButton(objectName="chatCopyBtn")
        self.copy_btn.setText("⧉")
        self.copy_btn.setToolTip("Copy message")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.hide()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        if align == "right":
            layout.addStretch(1)
            layout.addWidget(self.copy_btn, 0, Qt.AlignmentFlag.AlignTop)
            layout.addWidget(bubble)
        else:
            # stretch factor 1 and no trailing stretch: the bubble may take
            # the whole row up to its maximumWidth
            layout.addWidget(bubble, 1)
            layout.addWidget(self.copy_btn, 0, Qt.AlignmentFlag.AlignTop)

    def set_bubble_max_width(self, width: int) -> None:
        self.bubble.setMaximumWidth(width)

    def enterEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        if self.copy_btn is not None:
            self.copy_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        if self.copy_btn is not None:
            self.copy_btn.hide()
        super().leaveEvent(event)


class _ChatInput(QPlainTextEdit):
    """The composer: Enter sends, Shift+Enter is a newline, Escape stops."""

    send_requested = Signal()
    escape_requested = Signal()
    paths_dropped = Signal(list)  # local file paths dragged onto the composer

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("chatInput")
        self.setPlaceholderText("Message Claude…  (Enter to send, Shift+Enter for a new line)")
        # the QSS padding already insets the text; the default 4px document
        # margin would double it and force a permanent vertical scrollbar
        self.document().setDocumentMargin(0)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        urls = event.mimeData().urls()
        if urls:
            event.acceptProposedAction()
            self.paths_dropped.emit(urls)
        else:
            super().dropEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        key = event.key()
        mods = event.modifiers()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not mods & Qt.KeyboardModifier.ShiftModifier:
            # plain Enter or Ctrl+Enter sends; Shift+Enter is a line break
            event.accept()
            self.send_requested.emit()
            return
        if key == Qt.Key.Key_Escape:
            event.accept()
            self.escape_requested.emit()
            return
        super().keyPressEvent(event)


class ChatWidget(QWidget):
    """A dark, streaming chat panel backed by one claude CLI session."""

    finished = Signal()  # tile API compatibility (a chat never "exits")

    # the last model/effort picked in any chat tile: new tiles start there
    _last_model: Optional[str] = None
    _last_effort: Optional[str] = None

    def __init__(
        self,
        cwd: str,
        font_size: int = 10,
        parent: Optional[QWidget] = None,
        backend_factory: Optional[Callable[[str], QObject]] = None,
    ) -> None:
        # ``font_size`` matches the TerminalWidget constructor signature (tiles
        # pass it through); the chat keeps its own QSS typographic scale.
        super().__init__(parent)
        self.setObjectName("chatRoot")
        self.setAcceptDrops(True)  # files can be dropped anywhere in the chat
        self._cwd = cwd
        self._busy = False
        self._rows: List[_MessageRow] = []
        self._stream_row: Optional[_MessageRow] = None
        self._stream_text = ""
        self._rendered_text: Optional[str] = None
        self._stick = True  # follow new output until the user scrolls away

        self._backend_factory = backend_factory or (lambda c: ClaudeChatBackend(c))
        self.backend = self._backend_factory(cwd)
        self.backend.model = ChatWidget._last_model
        self.backend.effort = ChatWidget._last_effort
        self._wire_backend()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_messages(), 1)
        root.addWidget(self._build_selector_bar())
        root.addWidget(self._build_input_bar())

        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(_FLUSH_INTERVAL)
        self._flush_timer.timeout.connect(self._flush_now)

        self._dots_timer = QTimer(self)
        self._dots_timer.setInterval(350)
        self._dots_frame = 0
        self._dots_timer.timeout.connect(self._tick_dots)

    # --- construction -------------------------------------------------------
    def _build_messages(self) -> QWidget:
        self._scroll = QScrollArea(objectName="chatScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget(objectName="chatMessages")
        self._messages_layout = QVBoxLayout(container)
        self._messages_layout.setContentsMargins(10, 12, 10, 12)
        self._messages_layout.setSpacing(10)
        self._messages_layout.addWidget(self._build_empty_state())
        self._messages_layout.addStretch(1)
        self._scroll.setWidget(container)
        self._container = container
        container.installEventFilter(self)

        # Sticky autoscroll: while the view sits at the bottom it follows new
        # content (rangeChanged fires as the transcript grows); the moment the
        # user scrolls up, following stops until they jump back down.
        sb = self._scroll.verticalScrollBar()
        sb.valueChanged.connect(self._on_scroll_value)
        sb.rangeChanged.connect(self._on_range_changed)

        self._jump_btn = QToolButton(self, objectName="chatJumpBtn")
        self._jump_btn.setText("↓  Latest")
        self._jump_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._jump_btn.hide()
        self._jump_btn.clicked.connect(self._scroll_to_bottom)
        return self._scroll

    def _build_empty_state(self) -> QWidget:
        box = QWidget(objectName="chatEmpty")
        # fill the transcript area so the greeting centers vertically
        box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 24, 0, 24)
        layout.setSpacing(6)
        layout.addStretch(1)
        glyph = QLabel("✦", objectName="chatEmptyGlyph", alignment=Qt.AlignmentFlag.AlignCenter)
        title = QLabel("How can I help?", objectName="chatEmptyTitle", alignment=Qt.AlignmentFlag.AlignCenter)
        hint = QLabel(
            "A streaming chat session powered by the Claude Code CLI.",
            objectName="chatEmptyHint", alignment=Qt.AlignmentFlag.AlignCenter,
        )
        keys = QLabel(
            "Enter to send · Shift+Enter for a new line",
            objectName="chatEmptyHint", alignment=Qt.AlignmentFlag.AlignCenter,
        )
        self._empty_model = QLabel("", objectName="chatEmptyHint", alignment=Qt.AlignmentFlag.AlignCenter)
        for widget in (glyph, title, hint, keys, self._empty_model):
            layout.addWidget(widget)
        layout.addStretch(1)
        return box

    def _build_selector_bar(self) -> QWidget:
        """The model / reasoning-effort picker row above the composer."""
        bar = QFrame(objectName="chatModelBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(8)

        self._model_combo = QComboBox(objectName="chatModelCombo")
        for label, value in CHAT_MODELS:
            self._model_combo.addItem(label, value)
        self._model_combo.setToolTip(
            "Model for this chat (--model).\nApplies immediately when idle; "
            "after the current reply when streaming."
        )
        _select(self._model_combo, self.backend.model)
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)

        self._effort_combo = QComboBox(objectName="chatEffortCombo")
        for label, value in CHAT_EFFORTS:
            self._effort_combo.addItem(label, value)
        self._effort_combo.setToolTip(
            "Reasoning effort (--effort).\nApplies immediately when idle; "
            "after the current reply when streaming."
        )
        _select(self._effort_combo, self.backend.effort)
        self._effort_combo.currentIndexChanged.connect(self._on_effort_changed)

        layout.addWidget(self._model_combo)
        layout.addStretch(1)
        layout.addWidget(self._effort_combo)
        return bar

    def _on_model_changed(self, _index: int) -> None:
        ChatWidget._last_model = self._model_combo.currentData()
        self.backend.model = ChatWidget._last_model
        self.backend.apply_settings()

    def _on_effort_changed(self, _index: int) -> None:
        ChatWidget._last_effort = self._effort_combo.currentData()
        self.backend.effort = ChatWidget._last_effort
        self.backend.apply_settings()

    def _build_input_bar(self) -> QWidget:
        bar = QFrame(objectName="chatInputBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        self._input = _ChatInput()
        self._input.textChanged.connect(self._grow_input)
        self._input.send_requested.connect(self._on_send)
        self._input.escape_requested.connect(self._on_escape)
        self._input.paths_dropped.connect(self._insert_dropped_urls)

        self._send_btn = QToolButton(objectName="chatSendBtn")
        self._send_btn.setText("↑")
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.clicked.connect(self._on_send_clicked)

        layout.addWidget(self._input, 1)
        layout.addWidget(self._send_btn, 0, Qt.AlignmentFlag.AlignBottom)
        self._update_send_button()
        self._grow_input()
        return bar

    def _wire_backend(self) -> None:
        self.backend.ready.connect(self._on_ready)
        self.backend.delta.connect(self._on_delta)
        self.backend.turn_finished.connect(self._on_turn_finished)
        self.backend.failed.connect(self._on_failed)

    # --- tile API (duck-types TerminalWidget) ---------------------------------
    def is_dead(self) -> bool:
        return False

    # --- drag & drop of files ---------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        urls = event.mimeData().urls()
        if urls:
            event.acceptProposedAction()
            self._insert_dropped_urls(urls)
        else:
            super().dropEvent(event)

    def _insert_dropped_urls(self, urls) -> None:
        """Turn dropped files into message text + grant claude their folders.

        A file outside the working folder would be unreadable for the chat
        (in ``-p`` mode reads outside the working directories are denied), so
        its directory is added with ``--add-dir`` before it is ever sent.
        """
        pieces: List[str] = []
        for url in urls:
            if url.isLocalFile():
                # QUrl hands out forward slashes; Windows users (and claude)
                # expect the native backslash form
                path = QDir.toNativeSeparators(url.toLocalFile())
            else:
                pieces.append(url.toString())
                continue
            pieces.append(f'"{path}"' if " " in path else path)
            grant = path if os.path.isdir(path) else os.path.dirname(path)
            if self.backend.add_dir(grant):
                self.backend.apply_settings()  # restart; the chat resumes
        if not pieces:
            return
        text = self._input.toPlainText()
        sep = "" if not text or text.endswith(("\n", " ")) else "\n"
        self._input.insertPlainText(sep + "\n".join(pieces))
        self._input.setFocus()

    def tick(self) -> None:  # QProcess drives the chat; nothing to poll
        return None

    def close(self) -> None:  # noqa: N802 (QWidget override)
        self._flush_timer.stop()
        self._dots_timer.stop()
        try:
            self.backend.close()
        except Exception:  # noqa: BLE001 (shutdown must never raise)
            log.exception("chat backend close failed")

    def setFocus(self, reason=Qt.FocusReason.OtherFocusReason) -> None:  # noqa: N802
        self._input.setFocus(reason)

    # --- sending -----------------------------------------------------------------
    def submit(self, text: str) -> None:
        """Send ``text`` as one user turn (also the test entry point)."""
        cleaned = text.strip()
        if not cleaned or self._busy:
            return
        self._hide_empty_state()
        self._add_user_message(cleaned)
        self._begin_turn()
        self._follow()
        self.backend.send(cleaned)

    def _on_send(self) -> None:
        text = self._input.toPlainText()
        if not text.strip() or self._busy:
            return
        self._input.clear()
        self.submit(text)

    def _on_send_clicked(self) -> None:
        if self._busy:
            self.backend.interrupt()
        else:
            self._on_send()

    def _on_escape(self) -> None:
        if self._busy:
            self.backend.interrupt()

    def _begin_turn(self) -> None:
        self._busy = True
        self._update_send_button()
        self._stream_text = ""
        self._rendered_text = None
        bubble = _MarkdownBubble()
        self._stream_row = self._add_row(bubble)
        self._dots = QLabel(_THINKING_FRAMES[0], bubble, objectName="chatThinking")
        self._dots.move(10, 6)
        self._dots.show()
        self._dots_timer.start()
        self._scroll_to_bottom_later()

    def _end_turn(self) -> None:
        self._busy = False
        self._update_send_button()
        self._input.setFocus()

    def _update_send_button(self) -> None:
        self._send_btn.setText("■" if self._busy else "↑")
        # a string, matching the QSS [streaming="true"] selector (bool props
        # don't reliably match across Qt builds -- see _set_drop_highlight)
        self._send_btn.setProperty("streaming", "true" if self._busy else "false")
        self._send_btn.setToolTip("Stop" if self._busy else "Send")
        # idle + empty input: keep it visibly disabled instead of a dead button
        self._send_btn.setEnabled(self._busy or bool(self._input.toPlainText().strip()))
        self._send_btn.style().unpolish(self._send_btn)
        self._send_btn.style().polish(self._send_btn)

    # --- streaming ------------------------------------------------------------
    def _on_ready(self, session_id: str, model: str) -> None:
        if model:
            self._empty_model.setText(model)

    def _on_delta(self, text: str) -> None:
        self._stop_dots()
        self._stream_text += text
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _stop_dots(self) -> None:
        """Hide the thinking dots (first delta arrived, or the turn ended)."""
        dots = getattr(self, "_dots", None)
        if dots is not None:
            self._dots_timer.stop()
            dots.hide()
            dots.deleteLater()
            self._dots = None

    def _tick_dots(self) -> None:
        dots = getattr(self, "_dots", None)
        if dots is not None:
            self._dots_frame = (self._dots_frame + 1) % len(_THINKING_FRAMES)
            dots.setText(_THINKING_FRAMES[self._dots_frame])
            # the label lives outside any layout: it won't grow on setText,
            # and without this the dots clip to the initial "Thinking" width
            dots.adjustSize()

    def _flush_now(self) -> None:
        self._flush_timer.stop()
        self._render(final=False)

    def _render(self, final: bool) -> None:
        """Re-render the streaming bubble; ``final`` drops the stream cursor."""
        row = self._stream_row
        if row is None:
            return
        bubble = row.bubble
        assert isinstance(bubble, _MarkdownBubble)
        if final or self._stream_text != self._rendered_text:
            self._rendered_text = self._stream_text
            bubble.set_markdown(self._stream_text)
            if not final:
                bubble.show_stream_cursor()

    # --- backend end-of-turn -------------------------------------------------------
    def _on_turn_finished(self) -> None:
        self._flush_timer.stop()
        self._stop_dots()
        self._finalize_stream_row()
        self._end_turn()

    def _finalize_stream_row(self) -> None:
        """Render the streaming bubble, or drop it if the turn produced nothing."""
        row = self._stream_row
        if row is None:
            return
        if not self._stream_text.strip():
            # e.g. Stop pressed before the first delta: no empty bubble left
            self._rows.remove(row)
            row.setParent(None)
            row.deleteLater()
        else:
            self._render(final=True)
        self._stream_row = None

    def _on_failed(self, message: str) -> None:
        self._flush_timer.stop()
        self._stop_dots()
        self._finalize_stream_row()
        error = QLabel(message, objectName="chatErrorBubble", wordWrap=True)
        error.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._add_row(error)
        self._end_turn()

    # --- messages layout --------------------------------------------------------
    def _add_user_message(self, text: str) -> None:
        # document-rendered (not QLabel) so pasted long words wrap, not clip
        bubble = _MarkdownBubble(object_name="chatUserBubble")
        bubble.set_plain(text)
        self._add_row(bubble, align="right")
        self._scroll_to_bottom_later()

    def _add_row(self, bubble: QWidget, align: str = "left") -> _MessageRow:
        row = _MessageRow(bubble, align)
        row.set_bubble_max_width(self._bubble_limit())
        row.copy_btn.clicked.connect(lambda _=False, b=bubble: self._copy_bubble(b))
        # insert before the trailing stretch so the transcript stays on top
        self._messages_layout.insertWidget(self._messages_layout.count() - 1, row)
        self._rows.append(row)
        return row

    def _copy_bubble(self, bubble: QWidget) -> None:
        if isinstance(bubble, _MarkdownBubble):
            text = bubble.toPlainText()
        else:
            text = bubble.text()  # type: ignore[attr-defined]
        QGuiApplication.clipboard().setText(text)
        btn = self.sender()
        if isinstance(btn, QToolButton):
            btn.setText("✓")
            # a timer owned by the button: if the tile closes within the
            # flash, the timer dies with it instead of touching a corpse
            flash = QTimer(btn)
            flash.setSingleShot(True)
            flash.timeout.connect(lambda b=btn: b.setText("⧉"))
            flash.start(1200)

    def _hide_empty_state(self) -> None:
        empty = self._messages_layout.itemAt(0).widget()
        if empty is not None and empty.objectName() == "chatEmpty":
            empty.hide()

    def _bubble_limit(self) -> int:
        width = self._container.width() if self._container.width() > 1 else 560
        return max(160, int((width - 24) * 0.82))

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt signature)
        if obj is self._container and event.type() == QEvent.Type.Resize:
            limit = self._bubble_limit()
            for row in self._rows:
                row.set_bubble_max_width(limit)
                row.bubble.updateGeometry()
        return super().eventFilter(obj, event)

    # --- scrolling ------------------------------------------------------------------
    def _at_bottom(self) -> bool:
        sb = self._scroll.verticalScrollBar()
        return sb.maximum() - sb.value() <= 24

    def _on_scroll_value(self, _value: int) -> None:
        self._stick = self._at_bottom()
        self._update_jump_button()

    def _on_range_changed(self, _min: int, _max: int) -> None:
        # the transcript grew: keep following only if we were at the bottom
        if self._stick:
            self._scroll_to_bottom()
        self._update_jump_button()
        self._position_jump_button()

    def _follow(self) -> None:
        """Jump to the latest message and resume following (used on sends)."""
        self._stick = True
        self._scroll_to_bottom_later()

    def _scroll_to_bottom_later(self) -> None:
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _update_jump_button(self) -> None:
        self._jump_btn.setVisible(self.isVisible() and not self._at_bottom())

    def _position_jump_button(self) -> None:
        area = self._scroll.rect()
        size = self._jump_btn.sizeHint()
        # centered like the web-chat "jump to latest" pill, clear of the
        # scrollbar gutter and the message text
        self._jump_btn.move(
            area.center().x() - size.width() // 2,
            area.bottom() - size.height() - 12,
        )
        self._jump_btn.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        super().resizeEvent(event)
        self._position_jump_button()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        super().showEvent(event)
        # the QSS font (13px) only lands on polish: re-measure the composer
        self._grow_input()
        self._update_send_button()
        self._update_jump_button()
        self._position_jump_button()

    # --- input ---------------------------------------------------------------------
    def _grow_input(self) -> None:
        """Grow the composer with its text, capped at ~6 lines."""
        metrics = QFontMetrics(self._input.font())
        spacing = metrics.lineSpacing()
        lines = max(1, self._input.document().blockCount())
        wanted = spacing * lines + 2 + _INPUT_CHROME
        capped = max(spacing + 2 + _INPUT_CHROME, min(wanted, spacing * 6 + _INPUT_CHROME))
        if capped != self._input.height():
            self._input.setFixedHeight(capped)
        self._update_send_button()
