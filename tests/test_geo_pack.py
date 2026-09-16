"""Boundary chunks: the Python writer (geo_pack) against the page's reader (geo.js).

The page decides a click inside a boundary cell by point-in-polygon on these
chunks, so the chain that matters is: shapes -> pack -> base64 script -> decode
in JavaScript -> point-in-polygon. These tests run exactly that chain under node
and compare the answer with the project's own ray cast (build/spatial.py), which
is itself pinned by tests/test_spatial.py.

Skipped where node is not installed; CI's ubuntu runner has it.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from jp_address_crosswalk.build.geo_pack import (
    SCALE,
    chunk_origin,
    js_chunk,
    pack_exact,
    pack_lines,
)
from jp_address_crosswalk.build.spatial import point_in_rings

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")
GEO_JS = Path(__file__).resolve().parents[1] / "site" / "geo.js"

DRIVER = r"""
const fs = require("fs"), vm = require("vm");
vm.runInThisContext(fs.readFileSync(process.env.GEO_JS, "utf8"));
const inp = JSON.parse(fs.readFileSync(0, "utf8"));
vm.runInThisContext(inp.script);           // the chunk exactly as the page loads it
(async () => {
  MeshGeo.units({ chunks: {} });
  const out = {};
  if (inp.kind === "exact") {
    const cells = await MeshGeo.load("exact", inp.key);
    out.cells = [...cells.keys()];
    out.hits = inp.points.map(([code, x, y]) => {
      const pieces = cells.get(code) || [];
      return pieces.filter((p) => MeshGeo.inside(p.rings, x, y)).map((p) => p.unit);
    });
    out.first = [...cells.values()][0].map((p) => p.rings.map((r) => Array.from(r)));
  } else {
    const lines = await MeshGeo.load("line", inp.key);
    out.lines = lines.map((l) => ({ unit: l.unit, xy: Array.from(l.xy) }));
  }
  process.stdout.write(JSON.stringify(out));
})();
"""


def _run(payload: dict) -> dict:
    done = subprocess.run(
        [NODE, "-e", DRIVER], input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", env={**os.environ, "GEO_JS": str(GEO_JS)}, check=True,
    )
    return json.loads(done.stdout)


KEY = "533946"                     # 2次メッシュ of Tokyo station (exact chunks)
LINE_KEY = "5339"                  # 1次メッシュ (line chunks)
CELL = 53394611


def _sq(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


class TestOrigin:
    def test_first_order_mesh_corner(self) -> None:
        assert chunk_origin("5339") == (139.0, 53 / 1.5)
        assert chunk_origin("all") == (122.0, 20.0)

    def test_second_order_mesh_corner(self) -> None:
        """533946: 1次 5339, then row 4 (5 min of latitude each) and column 6 (7.5 min)."""
        lng, lat = chunk_origin("533946")
        assert abs(lng - (139 + 6 / 8)) < 1e-12
        assert abs(lat - (53 / 1.5 + 4 / 12)) < 1e-12

    def test_the_chunk_contains_its_cell(self) -> None:
        lng, lat = chunk_origin("533946")
        assert lng <= 139.767125 < lng + 1 / 8 and lat <= 35.681236 < lat + 1 / 12


class TestExact:
    # Two municipalities sharing an edge inside one cell, one with a hole that
    # the other fills, and a strip of sea (in neither).
    WEST = [_sq(139.76, 35.675, 139.765, 35.683), _sq(139.761, 35.677, 139.762, 35.678)]
    EAST = [_sq(139.765, 35.675, 139.77, 35.681)]
    ISLAND = [_sq(139.761, 35.677, 139.762, 35.678)]   # fills WEST's hole

    @pytest.fixture(scope="class")
    def result(self):
        pieces = [(0, self.WEST), (1, self.EAST), (2, self.ISLAND)]
        script = js_chunk("exact", KEY, pack_exact(KEY, [(CELL, pieces)]))
        rng = random.Random(3)
        pts = [(CELL, rng.uniform(139.759, 139.771), rng.uniform(35.674, 35.684)) for _ in range(2000)]
        pts += [(CELL, 139.7615, 35.6775), (CELL, 139.7675, 35.682)]   # hole, sea
        return pts, pieces, _run({"kind": "exact", "key": KEY, "script": script, "points": pts})

    def test_the_cell_round_trips(self, result) -> None:
        _, _, got = result
        assert got["cells"] == [CELL]

    def test_every_point_agrees_with_the_reference_ray_cast(self, result) -> None:
        pts, pieces, got = result
        for (_, x, y), hits in zip(pts, got["hits"], strict=True):
            want = [u for u, rings in pieces if point_in_rings(x, y, rings)]
            assert hits == want, (x, y)

    def test_a_hole_is_not_the_outer_municipality(self, result) -> None:
        _, _, got = result
        assert got["hits"][-2] == [2]

    def test_sea_inside_a_boundary_cell_is_nobody(self, result) -> None:
        """The point-level answer the cell-level table could not give."""
        _, _, got = result
        assert got["hits"][-1] == []

    def test_vertices_survive_to_a_centimetre(self, result) -> None:
        _, _, got = result
        ring = got["first"][0][0]
        for (x, y), (gx, gy) in zip(self.WEST[0][:-1], zip(ring[::2], ring[1::2], strict=True), strict=True):
            assert abs(gx - x) <= 1 / SCALE and abs(gy - y) <= 1 / SCALE


class TestLines:
    def test_lines_round_trip_with_their_unit(self) -> None:
        line = [(139.1, 35.4), (139.2, 35.45), (139.3, 35.5)]
        script = js_chunk("line", LINE_KEY, pack_lines(LINE_KEY, [(7, line)]))
        got = _run({"kind": "line", "key": LINE_KEY, "script": script})
        assert [ln["unit"] for ln in got["lines"]] == [7]
        xy = got["lines"][0]["xy"]
        for (x, y), gx, gy in zip(line, xy[::2], xy[1::2], strict=True):
            assert abs(gx - x) <= 1 / SCALE and abs(gy - y) <= 1 / SCALE

    def test_degenerate_input_is_dropped_not_written_empty(self) -> None:
        assert pack_lines(LINE_KEY, [(0, [(139.1, 35.4), (139.1, 35.4)])]) == b"\x00"
        assert pack_exact(KEY, [(CELL, [(0, [[(139.76, 35.68)] * 4])])]) == b"\x00"

    def test_large_codes_and_negative_deltas_survive(self) -> None:
        """Mesh codes need 4 varint bytes; a ring going west/south has negative deltas."""
        ring = [(139.7699, 35.6819), (139.7601, 35.6751), (139.7650, 35.6801)]
        script = js_chunk("exact", KEY, pack_exact(KEY, [(68_45_77_99, [(65_000, [ring])])]))
        got = _run({"kind": "exact", "key": KEY, "script": script, "points": [(68457799, 139.765, 35.679)]})
        assert got["cells"] == [68457799]
        assert got["hits"] == [[65_000]]
