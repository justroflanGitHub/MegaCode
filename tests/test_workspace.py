"""Offscreen tests for the workspace's resizable splitter panes.

``TerminalWidget`` is replaced by a no-op fake (no child process); the tests
then drive the splitter layout: structure, drag-to-resize effect, and that a
user's pane arrangement survives add / close / swap.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QSplitter, QWidget  # noqa: E402

import megacode.workspace as wsm  # noqa: E402


class _FakeTerminal(QWidget):
    """Stands in for ``TerminalWidget``: same API, no child process."""

    finished = Signal()

    def __init__(self, _command, _cwd=None, font_size=10, parent=None):  # noqa: ARG002
        super().__init__(parent)

    def is_dead(self) -> bool:
        return False

    def tick(self) -> None:
        return None

    def close(self) -> None:
        return None


@pytest.fixture()
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture()
def make_ws(qapp, monkeypatch):
    monkeypatch.setattr(wsm, "TerminalWidget", _FakeTerminal)

    def _make(n: int = 2) -> wsm.WorkspaceView:
        view = wsm.WorkspaceView()
        view.resize(1200, 800)
        view.start(n, "fake", os.getcwd(), font_size=10, label="term")
        view.show()
        qapp.processEvents()
        return view

    return _make


@pytest.fixture()
def ws(make_ws):
    return make_ws(2)


def _row_splitter(view) -> QSplitter:
    return view._splitter.widget(0)  # noqa: SLF001 (test)


def test_panes_are_splitter_resizable(ws, qapp):
    root = ws._splitter
    assert isinstance(root, QSplitter)
    assert root.orientation() == Qt.Orientation.Vertical
    assert not root.childrenCollapsible()
    assert root.handleWidth() == 8

    row = _row_splitter(ws)
    assert isinstance(row, QSplitter)
    assert row.orientation() == Qt.Orientation.Horizontal
    assert row.count() == 2
    assert not row.childrenCollapsible()
    # the gap between the two tiles is a real, draggable handle
    assert row.handle(1) is not None

    # a "drag" = the sizes changing: the small pane must end up visibly smaller
    # than an equal split, not just nudged
    row.setSizes([300, 900])
    qapp.processEvents()
    assert ws.tiles[0].width() < ws.tiles[1].width()
    assert ws.tiles[0].width() < 400  # equal halves would be ~590

    # a 4th/5th terminal grows the tree downwards: 2x2 -> vertical handle too
    for _ in range(2):
        ws._new_tile("fake", "term")
    qapp.processEvents()
    assert ws._splitter.count() == 2
    assert ws._splitter.handle(1) is not None


def test_pane_sizes_survive_add_and_close(ws, qapp):
    _row_splitter(ws).setSizes([300, 900])
    qapp.processEvents()

    # adding a third terminal keeps the two existing panes' proportions
    ws._new_tile("fake", "term")  # n=3 -> one row of three
    qapp.processEvents()
    row3 = _row_splitter(ws)
    assert row3.count() == 3
    widths = [t.width() for t in ws.tiles]
    assert widths[0] < widths[2] < widths[1]  # small, averaged newcomer, large
    # the newcomer takes the siblings' average (not a Qt 640px default), and
    # the user's small pane is not squeezed toward the 180px minimum
    assert abs(widths[2] - (widths[0] + widths[1]) / 2) < 12
    assert widths[0] > 185

    # closing the small pane keeps the others' arrangement
    ws._on_close_tile(0)
    qapp.processEvents()
    widths = [t.width() for t in ws.tiles]
    assert widths[0] > widths[1]  # the large pane is still the large pane


def test_swap_carries_sizes_with_sessions(ws, qapp):
    _row_splitter(ws).setSizes([300, 900])
    qapp.processEvents()
    assert ws.tiles[0].width() < ws.tiles[1].width()

    ws._on_swap(0, 1)
    qapp.processEvents()
    # the session that was small now sits on the right and is still small
    assert ws.tiles[0].width() > ws.tiles[1].width()


def test_cross_row_swap_preserves_row_heights(make_ws, qapp):
    """A drag-swap between rows must not collapse the user's vertical
arrangement to equal row heights."""
    ws = make_ws(4)  # 2x2
    qapp.processEvents()
    ws._splitter.setSizes([552, 184])  # user drags the horizontal divider
    qapp.processEvents()
    heights_before = ws._splitter.sizes()
    assert abs(heights_before[0] - heights_before[1]) > 100  # visibly uneven

    ws._on_swap(2, 0)  # top-left session <-> bottom-left session
    qapp.processEvents()
    assert ws._splitter.sizes() == heights_before


def test_from_scratch_launch_splits_equally(make_ws, qapp):
    """start() runs while the workspace page is still hidden (the launcher is
showing); the launch grid must come up as equal panes, not sizes skewed by
Qt's 640x480 default widget geometry."""
    ws = make_ws(3)
    widths = [t.width() for t in ws.tiles]
    assert max(widths) - min(widths) <= 5

    ws6 = make_ws(6)
    row_heights = ws6._splitter.sizes()
    assert max(row_heights) - min(row_heights) <= 5


def test_single_tile_is_direct_child(make_ws):
    ws = make_ws(1)
    assert ws._splitter.count() == 1
    assert ws._splitter.widget(0) is ws.tiles[0]
    assert not isinstance(ws._splitter.widget(0), QSplitter)


def test_five_tiles_rows_of_three_then_two(make_ws, qapp):
    ws = make_ws(5)  # auto_shape(5) = 3 columns x 2 rows
    qapp.processEvents()
    assert ws._splitter.count() == 2
    first, second = ws._splitter.widget(0), ws._splitter.widget(1)
    assert isinstance(first, QSplitter) and first.count() == 3
    assert isinstance(second, QSplitter) and second.count() == 2


def test_tiles_have_minimum_size(ws):
    for tile in ws.tiles:
        assert tile.minimumWidth() >= 180
        assert tile.minimumHeight() >= 120


def test_cleanup_empties_splitter(make_ws, qapp):
    ws = make_ws(4)
    qapp.processEvents()
    ws.cleanup()
    qapp.processEvents()
    assert ws._splitter.count() == 0
