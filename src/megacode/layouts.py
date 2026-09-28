"""Pure layout math for tiling N terminal windows over a work area.

This module has no dependencies on Qt or the Windows API, which keeps it fully
unit-testable. The grid shapes were chosen to be the most "logical" tiling for
each supported count:

    2 -> two columns side by side          (2 x 1)
    3 -> three columns side by side        (3 x 1)
    4 -> a 2x2 grid                        (2 x 2)
    6 -> a 3x2 grid                        (3 x 2)

All rectangles are integer pixel coordinates. The :func:`compute_layout`
function guarantees that the returned cells exactly tile the supplied area
(only the inter-cell ``gap`` is left empty), with no leftover strips.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Tuple

#: Counts supported by the UI.
SUPPORTED: Tuple[int, ...] = (2, 3, 4, 6)

#: Maps an instance count to a (columns, rows) grid shape.
SHAPES: dict[int, Tuple[int, int]] = {
    2: (2, 1),
    3: (3, 1),
    4: (2, 2),
    6: (3, 2),
}


@dataclass(frozen=True)
class Rect:
    """An integer pixel rectangle: top-left origin plus size.

    (No ``slots=True``: that dataclass keyword needs Python 3.10+, and the
    Astra Linux target runs 3.7.)
    """

    x: int
    y: int
    w: int
    h: int


def grid_shape(n: int) -> Tuple[int, int]:
    """Return the ``(columns, rows)`` grid shape for ``n`` instances.

    Raises:
        ValueError: if ``n`` is not one of the :data:`SUPPORTED` counts.
    """
    if n not in SHAPES:
        raise ValueError(
            f"Unsupported instance count: {n!r}. Supported: {SUPPORTED}"
        )
    return SHAPES[n]


def _distribute(total: int, count: int, gap: int) -> List[int]:
    """Split ``total`` pixels across ``count`` cells separated by ``gap``.

    Returns ``count`` integer sizes whose sum plus ``(count - 1) * gap`` equals
    ``total`` (when ``total`` is large enough). Any remainder pixels from
    integer division are given to the first cells so nothing is lost.
    """
    if count <= 0:
        return []
    if count == 1:
        return [max(total, 0)]
    inner = total - (count - 1) * gap
    if inner < 0:
        inner = 0
    base = inner // count
    remainder = inner - base * count
    sizes = [base] * count
    for i in range(remainder):
        sizes[i] += 1
    return sizes


def auto_shape(n: int) -> Tuple[int, int]:
    """Return a sensible ``(columns, rows)`` grid for any ``n >= 1``.

    The predefined shapes are used for the UI preset counts (2/3/4/6); for any
    other count (e.g. after adding/removing a terminal) a near-square grid is
    derived so new terminals always tile cleanly.
    """
    if n in SHAPES:
        return SHAPES[n]
    if n <= 1:
        return (1, 1)
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    return (cols, rows)


def grid_positions(n: int) -> List[Tuple[int, int]]:
    """Return ``n`` ``(row, col)`` positions in a grid, reading order."""
    cols, _rows = auto_shape(n)
    return [(i // cols, i % cols) for i in range(n)]


def compute_layout(n: int, area: Rect, gap: int = 6) -> List[Rect]:
    """Compute ``n`` tiling rectangles covering ``area``.

    Args:
        n: Number of instances (must be in :data:`SUPPORTED`).
        area: The work area to fill, in physical pixels.
        gap: Pixels of empty space left between adjacent cells.

    Returns:
        A list of ``n`` :class:`Rect` objects in left-to-right, top-to-bottom
        reading order.
    """
    cols, rows = grid_shape(n)
    widths = _distribute(area.w, cols, gap)
    heights = _distribute(area.h, rows, gap)

    rects: List[Rect] = []
    y = area.y
    for row in range(rows):
        x = area.x
        for col in range(cols):
            index = row * cols + col
            if index < n:
                rects.append(Rect(x, y, widths[col], heights[row]))
            x += widths[col] + gap
        y += heights[row] + gap
    return rects
