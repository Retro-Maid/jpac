"""Point-in-polygon with a grid index, and the line→point rule for stations.

No spatial dependency. The project pins every runtime dependency with an upper
bound and justifies each one (docs/ARCHITECTURE.md §5); the two operations
needed here are a ray cast and a uniform grid, and neither is worth a pinned
library that would also have to be justified for a build that is otherwise pure
tabular work. The same trade was taken for the DBF and shapefile readers in
``payload``.

That decision is only safe because it is checked. ``tests/test_spatial.py``
verifies the primitives against hand-worked cases including the ones that
usually break a naive ray cast — a vertex hit, a horizontal edge, a hole — and
the nationwide result this produces was reproduced independently before the code
was written (docs/STATION_JOIN_PREFLIGHT.md §3, §4).

Coordinates are lon/lat degrees throughout. Nothing here measures distance, so
no projection is involved: containment is topological and a degree grid only has
to be *consistent*, not equal-area.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

Point = tuple[float, float]
Ring = list[Point]
Box = tuple[float, float, float, float]


def point_in_rings(x: float, y: float, rings: Sequence[Ring]) -> bool:
    """Even-odd containment across every ring of one shape.

    Holes need no special handling: a point inside a hole is inside two rings,
    which is even, so it falls outside. That is why ring orientation is left as
    the publisher wrote it.

    The ``(y1 > y) != (y2 > y)`` test is the standard half-open rule — an edge
    counts when it spans the ray, with the upper endpoint excluded — which makes
    a vertex hit and a horizontal edge count once rather than twice or zero
    times. e-Stat boundaries share vertices between neighbours constantly, so
    this is not an edge case here, it is the normal case.
    """
    inside = False
    for ring in rings:
        n = len(ring)
        if n < 3:
            continue
        x1, y1 = ring[-1]
        for x2, y2 in ring:
            # The second half computes x of the edge at height y. It cannot
            # divide by zero: `and` short-circuits, and reaching it means the
            # first test already established y1 != y2.
            if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
            x1, y1 = x2, y2
    return inside


class GridIndex:
    """Uniform lon/lat grid over shape bounding boxes.

    A quadtree or R-tree would be tighter, but the payload is 232k mostly-small
    polygons over a country-sized extent, where a uniform grid is close to
    optimal and is ~30 lines. Cell size is a degree constant rather than derived:
    at 0.05° a cell is roughly 5km, so a typical 小地域 touches one or two cells
    and a query scans a handful of candidates.
    """

    CELL = 0.05

    def __init__(self, boxes: Iterable[Box]) -> None:
        self._boxes: list[Box] = list(boxes)
        self._cells: dict[tuple[int, int], list[int]] = {}
        for i, (x0, y0, x1, y1) in enumerate(self._boxes):
            for cx in range(self._cell(x0), self._cell(x1) + 1):
                for cy in range(self._cell(y0), self._cell(y1) + 1):
                    self._cells.setdefault((cx, cy), []).append(i)

    @classmethod
    def _cell(cls, v: float) -> int:
        return math.floor(v / cls.CELL)

    def candidates(self, x: float, y: float) -> list[int]:
        """Indices whose bounding box contains the point. Never a false negative."""
        return [
            i for i in self._cells.get((self._cell(x), self._cell(y)), ())
            if self._boxes[i][0] <= x <= self._boxes[i][2]
            and self._boxes[i][1] <= y <= self._boxes[i][3]
        ]

    def __len__(self) -> int:
        return len(self._boxes)


def line_centroid(parts: Sequence[Ring]) -> Point | None:
    """Length-weighted centroid of a set of polylines.

    Length-weighted rather than the mean of the vertices: N02 digitises a long
    straight run with few vertices and a curve with many, so a vertex mean is
    pulled toward whichever end happens to be curvier. The weighted value is what
    a spatial library returns for a MultiLineString, which is what the reference
    measurement used.

    Returns ``None`` for a degenerate geometry (all points coincident), where
    there is no meaningful answer to invent.
    """
    sx = sy = total = 0.0
    for part in parts:
        for (x1, y1), (x2, y2) in zip(part, part[1:], strict=False):
            seg = math.hypot(x2 - x1, y2 - y1)
            if seg == 0.0:
                continue
            sx += (x1 + x2) / 2 * seg
            sy += (y1 + y2) / 2 * seg
            total += seg
    if total == 0.0:
        pts = [p for part in parts for p in part]
        return pts[0] if pts else None
    return sx / total, sy / total


def point_on_line(parts: Sequence[Ring]) -> Point | None:
    """A point guaranteed to lie on the geometry: the halfway mark by length.

    The centroid of a curved or multi-part line can fall outside it — a station
    that wraps around a building, or platforms on both sides of a river. When
    that happens the centroid lands in no polygon at all, and this is the
    fallback that still describes the same feature.
    """
    lengths = [
        (part, i, math.hypot(part[i + 1][0] - part[i][0], part[i + 1][1] - part[i][1]))
        for part in parts
        for i in range(len(part) - 1)
    ]
    total = sum(seg for _, _, seg in lengths)
    if total == 0.0:
        pts = [p for part in parts for p in part]
        return pts[0] if pts else None
    walked = 0.0
    for part, i, seg in lengths:
        if walked + seg >= total / 2:
            t = (total / 2 - walked) / seg if seg else 0.0
            (x1, y1), (x2, y2) = part[i], part[i + 1]
            return x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
        walked += seg
    part, i, _ = lengths[-1]
    return part[i + 1]
