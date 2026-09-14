"""Spatial primitives (src/jp_address_crosswalk/build/spatial.py).

These exist because the project chose not to take a spatial dependency. The
cases below are the ones a naive ray cast gets wrong — a ray through a vertex, a
horizontal edge, a hole, a point exactly on the boundary — and they are not
hypothetical here: e-Stat boundaries share vertices between neighbouring
polygons everywhere, so a double-count would misplace real stations.
"""

from __future__ import annotations

import math

from jp_address_crosswalk.build.spatial import (
    GridIndex,
    line_centroid,
    point_in_rings,
    point_on_line,
)

SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
# Wound the other way, which even-odd treats as a hole regardless of orientation.
HOLE = [(4.0, 4.0), (4.0, 6.0), (6.0, 6.0), (6.0, 4.0), (4.0, 4.0)]


class TestPointInRings:
    def test_inside_and_outside(self) -> None:
        assert point_in_rings(5, 5, [SQUARE])
        assert not point_in_rings(15, 5, [SQUARE])
        assert not point_in_rings(5, 15, [SQUARE])

    def test_a_hole_is_outside(self) -> None:
        assert not point_in_rings(5, 5, [SQUARE, HOLE])
        assert point_in_rings(2, 2, [SQUARE, HOLE])

    def test_a_ray_through_a_vertex_counts_once(self) -> None:
        """The half-open rule. Counting twice would report the point outside.

        A diamond with a vertex at y=5: a ray from (-1, 5) crosses the left
        vertex exactly.
        """
        diamond = [(0.0, 5.0), (5.0, 0.0), (10.0, 5.0), (5.0, 10.0), (0.0, 5.0)]
        assert point_in_rings(5, 5, [diamond])
        assert not point_in_rings(-1, 5, [diamond])
        assert not point_in_rings(11, 5, [diamond])

    def test_a_horizontal_edge_does_not_flip_the_count(self) -> None:
        """A ray along an edge must not toggle containment.

        The square's bottom edge lies at y=0; a point on that line to the right
        of the shape is outside, and would read as inside if the edge counted.
        """
        assert not point_in_rings(20, 0, [SQUARE])

    def test_degenerate_rings_are_ignored_not_crashed_on(self) -> None:
        assert not point_in_rings(1, 1, [[(0.0, 0.0), (1.0, 1.0)]])
        assert not point_in_rings(1, 1, [[]])

    def test_multipart_shapes_are_a_union(self) -> None:
        far = [(100.0, 100.0), (110.0, 100.0), (110.0, 110.0), (100.0, 110.0), (100.0, 100.0)]
        assert point_in_rings(5, 5, [SQUARE, far])
        assert point_in_rings(105, 105, [SQUARE, far])


class TestGridIndex:
    def test_a_candidate_set_never_misses_a_containing_box(self) -> None:
        boxes = [(0.0, 0.0, 1.0, 1.0), (0.5, 0.5, 2.0, 2.0), (10.0, 10.0, 11.0, 11.0)]
        idx = GridIndex(boxes)
        assert set(idx.candidates(0.75, 0.75)) == {0, 1}
        assert set(idx.candidates(1.5, 1.5)) == {1}
        assert idx.candidates(5.0, 5.0) == []

    def test_a_box_larger_than_one_cell_is_found_everywhere_inside_it(self) -> None:
        """A prefecture-sized bbox must register in every cell it spans."""
        idx = GridIndex([(0.0, 0.0, 1.0, 1.0)])
        for x in (0.01, 0.3, 0.66, 0.99):
            assert idx.candidates(x, x) == [0], x

    def test_a_point_on_the_box_edge_is_a_candidate(self) -> None:
        idx = GridIndex([(0.0, 0.0, 1.0, 1.0)])
        assert idx.candidates(1.0, 1.0) == [0]


class TestLinePoints:
    def test_centroid_is_length_weighted_not_vertex_averaged(self) -> None:
        """A densely digitised short end must not drag the centroid onto itself.

        One long segment 0→10 plus three vertices crammed into 10→10.3: the
        vertex mean sits near 7.6, the length-weighted centroid near the middle
        of the long run.
        """
        line = [(0.0, 0.0), (10.0, 0.0), (10.1, 0.0), (10.2, 0.0), (10.3, 0.0)]
        cx, _ = line_centroid([line])
        assert 5.0 < cx < 5.3
        assert cx < sum(p[0] for p in line) / len(line)

    def test_point_on_line_lies_on_the_line(self) -> None:
        line = [(0.0, 0.0), (10.0, 0.0)]
        assert point_on_line([line]) == (5.0, 0.0)

    def test_point_on_line_walks_across_parts(self) -> None:
        """Two parts of length 10 each: the halfway mark is the boundary between.

        (10,0) and (0,5) are both exactly 10 units along and both lie on the
        geometry. The implementation stops at the first, which is what makes the
        result deterministic rather than dependent on a tie-break.
        """
        p = point_on_line([[(0.0, 0.0), (10.0, 0.0)], [(0.0, 5.0), (10.0, 5.0)]])
        assert p == (10.0, 0.0)

    def test_the_halfway_mark_lands_inside_the_longer_part(self) -> None:
        p = point_on_line([[(0.0, 0.0), (4.0, 0.0)], [(0.0, 5.0), (16.0, 5.0)]])
        assert p == (6.0, 5.0)      # 10 units along: 4 in part 1, then 6 into part 2

    def test_a_centroid_outside_its_own_line_is_why_the_fallback_exists(self) -> None:
        """A U-shaped platform. The centroid sits in the gap; the fallback cannot.

        This is the real case behind the four stations that land in no polygon
        (docs/STATION_JOIN_PREFLIGHT.md §4.1): when the centroid misses, a point
        guaranteed to be on the geometry is the honest second attempt.
        """
        u = [(0.0, 0.0), (0.0, 10.0), (10.0, 10.0), (10.0, 0.0)]
        cx, cy = line_centroid([u])
        # The gap of the U: inside its bounding box but not on the line.
        gap = [[(1.0, 0.0), (9.0, 0.0), (9.0, 9.0), (1.0, 9.0), (1.0, 0.0)]]
        assert math.isclose(cx, 5.0)
        assert point_in_rings(cx, cy, gap), "the centroid is in the gap, as expected"
        on_x, on_y = point_on_line([u])
        assert not point_in_rings(on_x, on_y, gap), "the fallback must be on the line"

    def test_degenerate_geometry_returns_a_point_rather_than_inventing_one(self) -> None:
        assert line_centroid([[(3.0, 4.0)]]) == (3.0, 4.0)
        assert point_on_line([[(3.0, 4.0)]]) == (3.0, 4.0)
        assert line_centroid([]) is None
        assert point_on_line([]) is None
