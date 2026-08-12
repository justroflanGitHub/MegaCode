"""Unit tests for the pure layout math in :mod:`megacode.layouts`.

These run anywhere (no Qt, no Windows API) and lock down the tiling contract:
the right number of rectangles, inside the area, non-overlapping, and tiling it
exactly when the gap is zero.
"""

from __future__ import annotations

import pytest

from megacode.layouts import (
    SUPPORTED,
    Rect,
    SHAPES,
    _distribute,
    compute_layout,
    grid_shape,
)


# --- grid_shape -------------------------------------------------------------
@pytest.mark.parametrize("n,shape", list(SHAPES.items()))
def test_grid_shape_supported(n: int, shape: tuple[int, int]) -> None:
    assert grid_shape(n) == shape


@pytest.mark.parametrize("n", [0, 1, 5, 7, -1, 8])
def test_grid_shape_rejects_unsupported(n: int) -> None:
    with pytest.raises(ValueError):
        grid_shape(n)


# --- compute_layout ---------------------------------------------------------
@pytest.mark.parametrize("n", SUPPORTED)
def test_returns_n_rects(n: int) -> None:
    area = Rect(0, 0, 1920, 1040)
    rects = compute_layout(n, area, gap=6)
    assert len(rects) == n


@pytest.mark.parametrize("n", SUPPORTED)
def test_rects_within_area(n: int) -> None:
    area = Rect(100, 50, 1920, 1040)
    for r in compute_layout(n, area, gap=6):
        assert r.x >= area.x
        assert r.y >= area.y
        assert r.x + r.w <= area.x + area.w
        assert r.y + r.h <= area.y + area.h


@pytest.mark.parametrize("n", SUPPORTED)
def test_no_overlaps(n: int) -> None:
    area = Rect(0, 0, 1920, 1040)
    rects = compute_layout(n, area, gap=0)
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert not _intersects(a, b), f"overlap between {a} and {b}"


@pytest.mark.parametrize("n", SUPPORTED)
def test_zero_gap_tiles_exactly(n: int) -> None:
    """With gap=0 the cells must cover the whole area with nothing left over."""
    area = Rect(0, 0, 1920, 1040)
    rects = compute_layout(n, area, gap=0)
    cols, rows = SHAPES[n]

    # total width and height covered, accounting for the grid layout
    covered_w = sum(r.w for r in rects[:cols])  # first row widths
    assert covered_w == area.w
    covered_h = sum(rects[r * cols].h for r in range(rows))  # first column heights
    assert covered_h == area.h


@pytest.mark.parametrize("n", SUPPORTED)
def test_gap_leaves_only_gap_between_cells(n: int) -> None:
    area = Rect(0, 0, 1920, 1040)
    gap = 7
    rects = compute_layout(n, area, gap=gap)
    cols, rows = SHAPES[n]
    covered_w = sum(r.w for r in rects[:cols]) + (cols - 1) * gap
    covered_h = sum(rects[r * cols].h for r in range(rows)) + (rows - 1) * gap
    assert covered_w == area.w
    assert covered_h == area.h


def test_four_is_two_by_two() -> None:
    rects = compute_layout(4, Rect(0, 0, 1000, 1000), gap=0)
    top = [r for r in rects if r.y == 0]
    assert len(top) == 2
    # left column same width, top row same height
    assert top[0].h == top[1].h
    assert rects[0].w == rects[2].w  # left column widths equal
    assert rects[0].x < rects[1].x  # reading order left-to-right
    assert rects[0].y < rects[2].y  # then top-to-bottom


def test_six_is_three_by_two() -> None:
    rects = compute_layout(6, Rect(0, 0, 3000, 2000), gap=0)
    xs = {r.x for r in rects[:3]}  # top row x positions
    assert len(xs) == 3  # three distinct columns
    ys = {r.y for r in rects}
    assert len(ys) == 2  # two distinct rows


# --- _distribute ------------------------------------------------------------
def test_distribute_sums_correctly() -> None:
    assert sum(_distribute(1920, 3, gap=6)) == 1920 - 2 * 6
    assert sum(_distribute(1000, 4, gap=0)) == 1000
    assert sum(_distribute(7, 3, gap=0)) == 7  # 7 = 2 + 2 + 3


def test_distribute_single_cell() -> None:
    assert _distribute(1234, 1, gap=5) == [1234]


def test_distribute_zero_count() -> None:
    assert _distribute(100, 0, gap=0) == []


# --- helpers ----------------------------------------------------------------
def _intersects(a: Rect, b: Rect) -> bool:
    return not (
        a.x + a.w <= b.x
        or b.x + b.w <= a.x
        or a.y + a.h <= b.y
        or b.y + b.h <= a.y
    )
