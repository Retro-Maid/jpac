"""Shapefile record decoding (src/jp_address_crosswalk/payload.py).

Point is not a variation of the Polygon/PolyLine layout — it is a different
record: two doubles, with no bounding box and no part table. Reading a Point
through the polygon path does not fail loudly; it unpacks the X and Y as the
first half of a bbox and returns plausible-looking coordinates that are wrong.
So the tests below assert the decoded values are bit-exact rather than merely
present, which is what distinguishes "the point branch ran" from "something
parsed".

Bytes are hand-built here for the same reason the DBF tests build theirs
(tests/test_estat_n02_sources.py): the format is frozen, and testing the reader
against a mock would only assert that the mock behaves like the mock.
"""

from __future__ import annotations

import struct

import pytest

from jp_address_crosswalk.errors import SourceFetchFailed
from jp_address_crosswalk.payload import (
    SHP_POINT,
    SHP_POLYGON,
    SHP_POLYLINE,
    read_shp_shapes,
)

# 東京駅あたり。丸めると通ってしまう誤りを捕まえたいので、桁を残した値を使う。
TOKYO = (139.766103, 35.681247)
OSAKA = (135.498262, 34.733707)


def shp(*records: bytes) -> bytes:
    """A minimal but valid .shp: the 100-byte header, then length-prefixed records."""
    out = bytearray(b"\x00" * 100)
    out[0:4] = (9994).to_bytes(4, "big")       # file code
    out[28:32] = (1000).to_bytes(4, "little")  # version
    for number, content in enumerate(records, start=1):
        out += number.to_bytes(4, "big")
        out += (len(content) // 2).to_bytes(4, "big")   # content length in 16-bit words
        out += content
    out[24:28] = (len(out) // 2).to_bytes(4, "big")     # file length in words
    return bytes(out)


def point_record(x: float, y: float) -> bytes:
    return struct.pack("<idd", SHP_POINT, x, y)


def null_record() -> bytes:
    return struct.pack("<i", 0)


def polyline_record(points: list[tuple[float, float]]) -> bytes:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    body = struct.pack("<i", SHP_POLYLINE)
    body += struct.pack("<4d", min(xs), min(ys), max(xs), max(ys))
    body += struct.pack("<ii", 1, len(points))   # one part
    body += struct.pack("<i", 0)                 # part starts at index 0
    for x, y in points:
        body += struct.pack("<2d", x, y)
    return body


class TestPoint:
    def test_a_point_decodes_bit_exact(self) -> None:
        """Not "close to": a polygon-path misparse also returns floats."""
        shapes = read_shp_shapes(shp(point_record(*TOKYO)))
        assert len(shapes) == 1
        box, parts = shapes[0]
        assert parts == [[TOKYO]]
        assert box == (TOKYO[0], TOKYO[1], TOKYO[0], TOKYO[1])

    def test_the_box_is_degenerate_so_callers_stay_uniform(self) -> None:
        """A point's bbox is the point, so an index built on boxes still works."""
        (box, parts), = read_shp_shapes(shp(point_record(*OSAKA)))
        assert box[0] == box[2] and box[1] == box[3]
        assert parts[0][0] == OSAKA

    def test_several_points_keep_file_order(self) -> None:
        shapes = read_shp_shapes(
            shp(point_record(*TOKYO), point_record(*OSAKA))
        )
        assert [s[1][0][0] for s in shapes] == [TOKYO, OSAKA]

    def test_expect_accepts_points(self) -> None:
        shapes = read_shp_shapes(shp(point_record(*TOKYO)), expect=SHP_POINT)
        assert shapes[0][1] == [[TOKYO]]

    def test_expect_still_rejects_a_mismatched_type(self) -> None:
        """The guard must not have been weakened by adding a branch under it."""
        with pytest.raises(SourceFetchFailed):
            read_shp_shapes(shp(point_record(*TOKYO)), expect=SHP_POLYGON)


class TestIndexAlignment:
    def test_a_null_shape_between_points_holds_the_row_alignment(self) -> None:
        """Dropping the null would shift every later attribute onto the wrong stop."""
        shapes = read_shp_shapes(
            shp(point_record(*TOKYO), null_record(), point_record(*OSAKA))
        )
        assert len(shapes) == 3
        assert shapes[1] is None
        assert shapes[0][1] == [[TOKYO]]
        assert shapes[2][1] == [[OSAKA]]

    def test_a_null_shape_is_allowed_even_when_a_type_is_expected(self) -> None:
        shapes = read_shp_shapes(
            shp(null_record(), point_record(*TOKYO)), expect=SHP_POINT
        )
        assert shapes[0] is None
        assert shapes[1][1] == [[TOKYO]]


class TestPolylineStillWorks:
    """The point branch was added to shared code; this is the regression guard."""

    def test_a_polyline_decodes_as_before(self) -> None:
        pts = [(139.0, 35.0), (140.0, 36.0)]
        (box, parts), = read_shp_shapes(shp(polyline_record(pts)), expect=SHP_POLYLINE)
        assert parts == [pts]
        assert box == (139.0, 35.0, 140.0, 36.0)

    def test_a_polyline_and_a_point_can_share_a_file(self) -> None:
        shapes = read_shp_shapes(
            shp(polyline_record([(139.0, 35.0), (140.0, 36.0)]), point_record(*TOKYO))
        )
        assert shapes[0][1] == [[(139.0, 35.0), (140.0, 36.0)]]
        assert shapes[1][1] == [[TOKYO]]
