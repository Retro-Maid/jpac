"""バス停留所 → 市区町村 の結合 (src/jp_address_crosswalk/build/busstop.py).

Held to the same discipline as tests/test_station_bridge.py. The cases that
matter are the ones where the tempting fix is wrong: no polygon, two polygons, a
polygon whose code jpac no longer has — and one this table has that the station
bridge does not, a stop whose geometry is absent altogether.
"""

from __future__ import annotations

import polars as pl

from jp_address_crosswalk.build.busstop import build_bus_stop_bridge

WEST = ("11111", (0.0, 0.0, 1.0, 1.0),
        [[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]])
EAST = ("22222", (1.0, 0.0, 2.0, 1.0),
        [[(1.0, 0.0), (2.0, 0.0), (2.0, 1.0), (1.0, 1.0), (1.0, 0.0)]])
# Overlapping, so a point can fall in two — the prefecture seams e-Stat does not join.
OVERLAP = ("33333", (0.4, 0.0, 1.4, 1.0),
           [[(0.4, 0.0), (1.4, 0.0), (1.4, 1.0), (0.4, 1.0), (0.4, 0.0)]])
# A code the 2020 boundaries carry and jpac does not: the 旧浜松区 case.
GONE = ("99999", (5.0, 5.0, 6.0, 6.0),
        [[(5.0, 5.0), (6.0, 5.0), (6.0, 6.0), (5.0, 6.0), (5.0, 5.0)]])

LG_BY_JIS = {"11111": "111116", "22222": "222226", "33333": "333336"}


def stops(*ids: str) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"p11_stop_id": i, "stop_name_raw": f"停留所{i}",
             "operator_name_raw": "バス会社"}
            for i in ids
        ],
        schema={"p11_stop_id": pl.Utf8, "stop_name_raw": pl.Utf8,
                "operator_name_raw": pl.Utf8},
    )


class TestBusStopBridge:
    def test_a_stop_inside_one_municipality(self) -> None:
        br = build_bus_stop_bridge(
            stops("s1"), {"s1": (0.5, 0.5)}, [WEST, EAST], LG_BY_JIS
        )
        assert br.height == 1
        row = br.to_dicts()[0]
        assert row["lg_code"] == "111116"
        assert row["boundary_jis_city_code"] == "11111"
        assert row["relation_type"] == "contains"
        assert row["candidate_count"] == 1
        assert row["is_unique_match"] == 1
        assert row["verification_status"] == "auto"
        assert row["confidence"] == 1.0
        assert row["mismatch_note"] is None

    def test_a_stop_in_two_polygons_keeps_both(self) -> None:
        """Overlapping boundaries are real; picking one would discard a true candidate."""
        br = build_bus_stop_bridge(
            stops("s1"), {"s1": (0.6, 0.5)}, [WEST, OVERLAP], LG_BY_JIS
        )
        assert br.height == 2
        assert set(br["lg_code"]) == {"111116", "333336"}
        assert set(br["relation_type"]) == {"ambiguous"}
        assert set(br["candidate_count"]) == {2}
        assert set(br["is_unique_match"]) == {0}
        assert set(br["verification_status"]) == {"review_required"}

    def test_a_stop_in_no_polygon_is_not_snapped(self) -> None:
        br = build_bus_stop_bridge(
            stops("s1"), {"s1": (9.0, 9.0)}, [WEST, EAST], LG_BY_JIS
        )
        assert br.height == 1
        row = br.to_dicts()[0]
        assert row["relation_type"] == "unresolved"
        assert row["lg_code"] is None
        assert row["boundary_jis_city_code"] is None
        assert row["confidence"] == 0.0
        assert row["candidate_count"] == 0
        assert row["verification_status"] == "review_required"
        assert "最寄り" in row["mismatch_note"]

    def test_a_code_jpac_lacks_keeps_its_row(self) -> None:
        br = build_bus_stop_bridge(
            stops("s1"), {"s1": (5.5, 5.5)}, [WEST, GONE], LG_BY_JIS
        )
        assert br.height == 1
        row = br.to_dicts()[0]
        assert row["lg_code"] is None
        assert row["boundary_jis_city_code"] == "99999"
        assert row["relation_type"] == "contains"
        assert row["is_unique_match"] == 1
        assert row["verification_status"] == "review_required"
        assert "99999" in row["mismatch_note"]

    def test_a_stop_with_no_geometry_is_unresolved_not_dropped(self) -> None:
        """"We have no point" must not be silently identical to "it matched nothing"."""
        br = build_bus_stop_bridge(stops("s1"), {}, [WEST, EAST], LG_BY_JIS)
        assert br.height == 1
        assert br.to_dicts()[0]["relation_type"] == "unresolved"

    def test_rows_are_sorted_for_reproducibility(self) -> None:
        br = build_bus_stop_bridge(
            stops("s2", "s1"),
            {"s1": (0.5, 0.5), "s2": (1.5, 0.5)},
            [WEST, EAST], LG_BY_JIS,
        )
        assert br["p11_stop_id"].to_list() == ["s1", "s2"]
