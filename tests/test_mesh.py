"""メッシュ照合 (src/jp_address_crosswalk/build/mesh.py).

The mesh code itself is arithmetic and is tested against a coordinate whose
code is independently known. Everything after that is the same three-outcome
discipline the station bridge is held to, so the tests that matter are the ones
about cells the answer is *not* clean for.
"""

from __future__ import annotations

import pytest

from jp_address_crosswalk.build.mesh import (
    DLAT_3,
    DLNG_3,
    build_mesh_municipality,
    cell_code,
    cell_of,
    classify_cells,
    mesh_code,
)


class TestMeshCode:
    def test_tokyo_station(self) -> None:
        """53394611 is the published 3次 code for 東京駅."""
        assert mesh_code(35.681236, 139.767125) == "53394611"

    def test_the_code_is_eight_digits_everywhere_in_japan(self) -> None:
        for lat, lng in ((24.45, 122.93),    # 与那国島 — south-west corner
                         (45.52, 141.93),    # 宗谷岬 — north
                         (35.68, 139.77),
                         (26.20, 127.68)):   # 那覇
            assert len(mesh_code(lat, lng)) == 8
            assert mesh_code(lat, lng).isdigit()

    def test_a_cell_maps_back_to_its_own_code(self) -> None:
        """Any point in a cell yields the code the cell's centre yields."""
        for lat, lng in ((35.681236, 139.767125), (43.0682, 141.3508)):
            assert cell_code(cell_of(lat, lng)) == mesh_code(lat, lng)

    def test_neighbouring_cells_differ(self) -> None:
        base = (35.681236, 139.767125)
        east = mesh_code(base[0], base[1] + DLNG_3)
        north = mesh_code(base[0] + DLAT_3, base[1])
        assert mesh_code(*base) != east != north
        assert mesh_code(*base) != north


# One cell wide enough to hold whole cells inside it, and a neighbour sharing
# an edge, so that a straddling cell genuinely exists.
def _square(code: str, x0: float, y0: float, x1: float, y1: float):
    ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return (code, (x0, y0, x1, y1), [ring])


WEST = _square("11111", 139.00, 35.00, 139.10, 35.10)
EAST = _square("22222", 139.10, 35.00, 139.20, 35.10)
GONE = _square("99999", 140.00, 35.00, 140.05, 35.05)
LG_BY_JIS = {"11111": "111116", "22222": "222226"}


class TestClassify:
    def test_an_interior_cell_belongs_to_one_municipality(self) -> None:
        cells = classify_cells([WEST, EAST])
        interior = cell_of(35.05, 139.05)
        assert cells[interior] == {"11111"}

    def test_a_cell_on_the_shared_edge_keeps_both(self) -> None:
        cells = classify_cells([WEST, EAST])
        straddling = cell_of(35.05, 139.10)
        assert cells[straddling] == {"11111", "22222"}

    def test_a_cell_outside_every_polygon_is_absent(self) -> None:
        """Sea and prefecture seams: absent, never filled in from a neighbour."""
        cells = classify_cells([WEST, EAST])
        assert cell_of(35.05, 138.50) not in cells
        assert cell_of(36.50, 139.05) not in cells


class TestTable:
    def test_uniform_cells_resolve_automatically(self) -> None:
        t = build_mesh_municipality([WEST, EAST], LG_BY_JIS)
        code = cell_code(cell_of(35.05, 139.05))
        row = t.filter(t["mesh_code"] == code).to_dicts()
        assert len(row) == 1
        assert row[0]["lg_code"] == "111116"
        assert row[0]["relation_type"] == "contains"
        assert row[0]["is_unique_match"] == 1
        assert row[0]["verification_status"] == "auto"
        assert row[0]["mismatch_note"] is None

    def test_a_straddling_cell_yields_a_row_per_candidate(self) -> None:
        t = build_mesh_municipality([WEST, EAST], LG_BY_JIS)
        code = cell_code(cell_of(35.05, 139.10))
        rows = t.filter(t["mesh_code"] == code).to_dicts()
        assert len(rows) == 2
        assert {r["lg_code"] for r in rows} == {"111116", "222226"}
        assert {r["relation_type"] for r in rows} == {"ambiguous"}
        assert {r["candidate_count"] for r in rows} == {2}
        assert {r["is_unique_match"] for r in rows} == {0}
        assert {r["verification_status"] for r in rows} == {"review_required"}

    def test_lg_code_is_null_when_the_cross_section_moved(self) -> None:
        """旧浜松区 again: the cell is real, the municipality is not current."""
        t = build_mesh_municipality([GONE], LG_BY_JIS)
        rows = t.to_dicts()
        assert rows, "the cells exist even though the code is stale"
        assert all(r["lg_code"] is None for r in rows)
        assert all(r["boundary_jis_city_code"] == "99999" for r in rows)
        assert all(r["verification_status"] == "review_required" for r in rows)
        assert all("断面のズレ" in r["mismatch_note"] for r in rows)

    def test_lg_code_is_six_digits_and_jis_is_five(self) -> None:
        t = build_mesh_municipality([WEST, EAST], LG_BY_JIS)
        assert all(len(c) == 6 for c in t["lg_code"].drop_nulls().to_list())
        assert all(len(c) == 5 for c in t["boundary_jis_city_code"].to_list())

    def test_no_cell_is_invented_outside_the_polygons(self) -> None:
        t = build_mesh_municipality([WEST, EAST], LG_BY_JIS)
        assert cell_code(cell_of(35.05, 138.50)) not in set(t["mesh_code"])

    @pytest.mark.parametrize("col", ["mesh_code", "boundary_jis_city_code"])
    def test_codes_stay_strings(self, col: str) -> None:
        """Leading zeros must survive: CLAUDE.md §8."""
        t = build_mesh_municipality([WEST, EAST], LG_BY_JIS)
        assert t.schema[col] == __import__("polars").Utf8
