"""The static map's table reader (site/lookup.js) against the Python writer.

The binaries are produced by ``mesh.pack_lookup`` and read by JavaScript in a
browser, so a format drift on either side would pass every Python test and
still answer the wrong municipality on the page. These tests build a table with
the real classifier, pack it with the real writer, and read it with the real
reader under node — one chain, end to end.

Skipped where node is not installed; CI's ubuntu runner has it.
"""

from __future__ import annotations

import base64
import json
import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from jp_address_crosswalk.build.mesh import (
    UNKNOWN_INDEX,
    build_mesh_municipality,
    cell_of,
    mesh_code,
    pack_lookup,
)

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

LOOKUP_JS = Path(__file__).resolve().parents[1] / "site" / "lookup.js"

DRIVER = r"""
const fs = require("fs"), vm = require("vm");
vm.runInThisContext(fs.readFileSync(process.env.LOOKUP_JS, "utf8"));
const inp = JSON.parse(fs.readFileSync(0, "utf8"));
const buf = (b) => { const x = Buffer.from(b, "base64"); return x.buffer.slice(x.byteOffset, x.byteOffset + x.byteLength); };
const t = MeshLookup.create(buf(inp.uniform), buf(inp.mixed));
const out = inp.points.map(([lat, lng]) => {
  const [r, c] = MeshLookup.rowColOf(lat, lng);
  const code = MeshLookup.codeOf(r, c);
  const back = MeshLookup.rowColOfCode(code);
  return { code: String(code).padStart(8, "0"), roundtrip: back[0] === r && back[1] === c, ...t.lookup(code) };
});
process.stdout.write(JSON.stringify(out));
"""


def _read(uniform: bytes, mixed: bytes, points: list[tuple[float, float]]) -> list[dict]:
    payload = json.dumps({
        "uniform": base64.b64encode(uniform).decode(),
        "mixed": base64.b64encode(mixed).decode(),
        "points": points,
    })
    done = subprocess.run(
        [NODE, "-e", DRIVER], input=payload, capture_output=True, text=True,
        encoding="utf-8", env={**os.environ, "LOOKUP_JS": str(LOOKUP_JS)}, check=True,
    )
    return json.loads(done.stdout)


def _square(code: str, x0: float, y0: float, x1: float, y1: float):
    ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return (code, (x0, y0, x1, y1), [ring])


# The shared edge sits mid-cell (139.105 * 80 = 11128.4) on purpose. On a mesh
# line it would be a coin toss in floating point which side each language puts
# a point that lies exactly on it.
WEST = _square("11111", 139.00, 35.00, 139.105, 35.10)
EAST = _square("22222", 139.105, 35.00, 139.20, 35.10)
NAMELESS = _square("13199", 140.00, 35.00, 140.05, 35.05)   # no lg_code, no successor
LG = {"11111": "111116", "22222": "222226"}


@pytest.fixture(scope="module")
def packed():
    table = build_mesh_municipality([WEST, EAST, NAMELESS], LG)
    return pack_lookup(table)


class TestMeshCode:
    def test_js_and_python_agree_on_the_code(self) -> None:
        rng = random.Random(7)
        pts = [(35.681236, 139.767125)] + [
            (rng.uniform(24, 45.5), rng.uniform(123, 153)) for _ in range(3000)
        ]
        got = _read(b"\x00", b"\x00", pts)       # empty tables: zero runs each
        assert got[0]["code"] == "53394611"
        assert [g["code"] for g in got] == [mesh_code(la, lo) for la, lo in pts]
        assert all(g["roundtrip"] for g in got)


SIXTH = r"""
const fs = require("fs"), vm = require("vm");
vm.runInThisContext(fs.readFileSync(process.env.LOOKUP_JS, "utf8"));
const inp = JSON.parse(fs.readFileSync(0, "utf8"));
const out = { codes: inp.points.map(([lat, lng]) => { const [r, c] = MeshLookup.rowCol6Of(lat, lng); return MeshLookup.code6Of(r, c); }),
              parsed: inp.parse.map((s) => MeshLookup.parseMeshCode(s)) };
process.stdout.write(JSON.stringify(out));
"""


def _ref_code6(lat: float, lng: float) -> str:
    """JIS X 0410 extended meshes, derived directly: halve three times, number
    each quarter 1 (SW) 2 (SE) 3 (NW) 4 (NE)."""
    from fractions import Fraction
    y, x = Fraction(lat), Fraction(lng)
    row, col = int(y * 120), int(x * 80)
    code = mesh_code(float((row + Fraction(1, 2)) / 120), float((col + Fraction(1, 2)) / 80))
    fy, fx = y * 120 - row, x * 80 - col           # position inside the 3次 cell, [0, 1)
    for _ in range(3):
        fy, fx = fy * 2, fx * 2
        north, east = int(fy >= 1), int(fx >= 1)
        code += str(1 + east + 2 * north)
        fy, fx = fy - north, fx - east
    return code


class TestSixthOrderMesh:
    def test_codes_agree_with_an_independent_derivation(self) -> None:
        rng = random.Random(5)
        pts = [(35.681236, 139.767125)] + [(rng.uniform(24, 45.5), rng.uniform(123, 153)) for _ in range(3000)]
        done = subprocess.run([NODE, "-e", SIXTH], input=json.dumps({"points": pts, "parse": []}),
                              capture_output=True, text=True, encoding="utf-8",
                              env={**os.environ, "LOOKUP_JS": str(LOOKUP_JS)}, check=True)
        got = json.loads(done.stdout)["codes"]
        assert got == [_ref_code6(la, lo) for la, lo in pts]
        assert got[0].startswith("53394611") and len(got[0]) == 11

    def test_parse_gives_the_cell_back(self) -> None:
        """Every level parses to the 6次 corner and size the code names."""
        cases = ["53394611", "533946114", "5339461143", "53394611432", "53394611", "5339461105", "53399611"]
        done = subprocess.run([NODE, "-e", SIXTH], input=json.dumps({"points": [], "parse": cases}),
                              capture_output=True, text=True, encoding="utf-8",
                              env={**os.environ, "LOOKUP_JS": str(LOOKUP_JS)}, check=True)
        parsed = json.loads(done.stdout)["parsed"]
        base = parsed[0]
        assert base["span"] == 8 and base["level"] == 3
        assert parsed[1] == {"r6": base["r6"] + 4, "c6": base["c6"] + 4, "span": 4, "level": 4}       # 4 = NE
        assert parsed[2] == {"r6": base["r6"] + 6, "c6": base["c6"] + 4, "span": 2, "level": 5}       # then 3 = NW
        assert parsed[3] == {"r6": base["r6"] + 6, "c6": base["c6"] + 5, "span": 1, "level": 6}       # then 2 = SE
        assert parsed[5] is None      # 0 is not a quarter
        assert parsed[6] is None      # 2次 digit 9 is not a 2次 mesh


class TestLookup:
    def test_a_uniform_cell_names_its_municipality(self, packed) -> None:
        ub, mb, order = packed
        [g] = _read(ub, mb, [(35.046, 139.05)])
        assert g["kind"] == "contains"
        assert [order[i] for i in g["ids"]] == ["111116"]

    def test_a_straddling_cell_returns_both(self, packed) -> None:
        ub, mb, order = packed
        [g] = _read(ub, mb, [(35.046, 139.105)])
        assert g["kind"] == "ambiguous"
        assert sorted(order[i] for i in g["ids"]) == ["111116", "222226"]

    def test_sea_is_none_not_the_nearest_municipality(self, packed) -> None:
        ub, mb, _ = packed
        [g] = _read(ub, mb, [(35.046, 138.50)])
        assert g == {**g, "kind": "none", "ids": []}

    def test_land_without_a_municipality_is_unknown_not_absent(self, packed) -> None:
        """Dropping the NULL candidate would make this cell read as sea."""
        ub, mb, _ = packed
        [g] = _read(ub, mb, [(35.025, 140.025)])
        assert g["kind"] == "unknown"
        assert g["ids"] == [UNKNOWN_INDEX]

    def test_every_cell_of_the_table_reads_back(self, packed) -> None:
        """Each (cell, candidate set) the table holds is what the page returns."""
        ub, mb, order = packed
        table = build_mesh_municipality([WEST, EAST, NAMELESS], LG)
        want: dict[str, set] = {}
        for r in table.iter_rows(named=True):
            want.setdefault(r["mesh_code"], set()).add(r["lg_code"])
        centres = []
        for code in want:
            p, u, q, v, rr, w = int(code[:2]), int(code[2:4]), *map(int, code[4:])
            centres.append(((p * 80 + q * 10 + rr + 0.5) / 120, ((u + 100) * 80 + v * 10 + w + 0.5) / 80))
        got = _read(ub, mb, centres)
        for g in got:
            assert g["kind"] != "none", g["code"]
            ids = {None if i == UNKNOWN_INDEX else order[i] for i in g["ids"]}
            assert ids == want[g["code"]], g["code"]

    def test_runs_are_smaller_than_one_record_per_cell(self, packed) -> None:
        """WEST/EAST hold long stretches of consecutive codes: they must pack as runs."""
        ub, _, _ = packed
        table = build_mesh_municipality([WEST, EAST, NAMELESS], LG)
        uniform_cells = table.filter(table["candidate_count"] == 1).height
        assert len(ub) < uniform_cells * 2

    def test_the_python_cell_index_agrees_with_the_page(self, packed) -> None:
        """The build rasterises with cell_of; the page with rowColOf."""
        ub, mb, _ = packed
        pts = [(35.0123, 139.0456), (35.0987, 139.1543)]
        got = _read(ub, mb, pts)
        from jp_address_crosswalk.build.mesh import cell_code
        assert [g["code"] for g in got] == [cell_code(cell_of(la, lo)) for la, lo in pts]
