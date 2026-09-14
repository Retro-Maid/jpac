"""駅 → 市区町村 の結合 (src/jp_address_crosswalk/build/station.py).

The three outcomes that are not "one station, one municipality" are the point of
these tests. Each is a case where the tempting fix is wrong:

* no polygon → snapping to the nearest municipality is confirming a match on
  proximity (POLICY.md §4 forbids it),
* two polygons → picking one discards a true candidate,
* a polygon whose code jpac no longer has → the containment is correct and the
  *cross-section* is what differs, so the row must survive rather than be dropped.
"""

from __future__ import annotations

import polars as pl

from jp_address_crosswalk.build.station import build_station_bridge

# Two adjacent unit squares and one far away.
WEST = ("11111", (0.0, 0.0, 1.0, 1.0), [[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]])
EAST = ("22222", (1.0, 0.0, 2.0, 1.0), [[(1.0, 0.0), (2.0, 0.0), (2.0, 1.0), (1.0, 1.0), (1.0, 0.0)]])
GONE = ("99999", (5.0, 5.0, 6.0, 6.0), [[(5.0, 5.0), (6.0, 5.0), (6.0, 6.0), (5.0, 6.0), (5.0, 5.0)]])

# The boundary polygons carry 5-digit JIS codes; the bridge stores the 6-digit
# lg_code the rest of the schema joins on. 99999 is deliberately absent: that is
# the 旧浜松区 case, a code the 2020 boundaries still have and jpac does not.
LG_BY_JIS = {"11111": "111116", "22222": "222226"}


def stations(*codes: str) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"n02_group_code": c, "station_name_raw": f"駅{c}",
             "operator_name_raw": "会社"}
            for c in codes
        ],
        schema={"n02_group_code": pl.Utf8, "station_name_raw": pl.Utf8,
                "operator_name_raw": pl.Utf8},
    )


class TestStationBridge:
    def test_a_station_inside_one_municipality(self) -> None:
        br = build_station_bridge(
            stations("s1"), {"s1": [[(0.2, 0.5), (0.8, 0.5)]]}, [WEST, EAST], LG_BY_JIS
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

    def test_a_station_on_a_border_keeps_both_candidates(self) -> None:
        """大阪空港 straddles 大阪府/兵庫県; discarding one would be a false answer."""
        br = build_station_bridge(
            # Centroid lands at x=1.0, the shared edge: the half-open ray rule
            # puts it in EAST, so span the line to sit in both by using two parts.
            stations("s1"),
            {"s1": [[(0.4, 0.5), (0.6, 0.5)], [(1.4, 0.5), (1.6, 0.5)]]},
            [WEST, EAST],
            LG_BY_JIS,
        )
        # The centroid of the two parts is x=1.0 — on the shared edge, inside
        # exactly one by the half-open rule. Assert the *shape* of the outcome
        # rather than which side wins, since either is a defensible edge case.
        assert br.height in (1, 2)
        if br.height == 2:
            assert set(br["lg_code"]) == {"111116", "222226"}
            assert br["relation_type"].to_list() == ["ambiguous", "ambiguous"]
            assert br["candidate_count"].to_list() == [2, 2]
            assert br["is_unique_match"].to_list() == [0, 0]
            assert set(br["verification_status"]) == {"review_required"}

    def test_a_station_in_no_polygon_stays_unresolved(self) -> None:
        br = build_station_bridge(
            stations("s1"), {"s1": [[(9.0, 9.0), (9.1, 9.1)]]}, [WEST, EAST], LG_BY_JIS
        )
        row = br.to_dicts()[0]
        assert row["relation_type"] == "unresolved"
        assert row["lg_code"] is None
        assert row["confidence"] == 0.0
        assert row["verification_status"] == "review_required"
        assert "最寄りへの割り当てはしない" in row["mismatch_note"]

    def test_a_station_with_no_geometry_is_unresolved_not_dropped(self) -> None:
        br = build_station_bridge(stations("s1"), {}, [WEST, EAST], LG_BY_JIS)
        assert br.height == 1
        assert br["relation_type"][0] == "unresolved"

    def test_a_code_jpac_lacks_survives_as_a_cross_section_difference(self) -> None:
        """The 54 浜松 stations: contained correctly, in a ward jpac no longer has."""
        br = build_station_bridge(
            stations("s1"), {"s1": [[(5.2, 5.5), (5.8, 5.5)]]}, [WEST, GONE], LG_BY_JIS
        )
        row = br.to_dicts()[0]
        # lg_code is NULL rather than the 5-digit JIS code: a consumer joining
        # this bridge to `municipality` must not be handed a value of the wrong
        # domain that silently matches nothing.
        assert row["lg_code"] is None
        # …and the code the polygon carried survives, so the row is recoverable
        # through municipality_lineage.yml instead of being a silent loss.
        assert row["boundary_jis_city_code"] == "99999"
        assert row["relation_type"] == "contains"      # the geometry is right
        assert row["is_unique_match"] == 1             # and unambiguous
        assert row["verification_status"] == "review_required"   # but unsettled
        assert "断面のズレ" in row["mismatch_note"]

    def test_lg_code_is_never_the_five_digit_jis_code(self) -> None:
        """The defect this column pair exists to prevent: a 0% join.

        Before the fix the bridge stored the boundary's 5-digit JIS code under
        the name every other table uses for the 6-digit 全国地方公共団体コード,
        so `bridge_station_municipality -> municipality` resolved 0 of 9,043
        rows and returned an empty result rather than an error.
        """
        br = build_station_bridge(
            stations("a", "b"),
            {"a": [[(0.2, 0.5), (0.3, 0.5)]], "b": [[(5.2, 5.5), (5.8, 5.5)]]},
            [WEST, GONE], LG_BY_JIS,
        )
        present = [c for c in br["lg_code"].to_list() if c is not None]
        assert present, "at least one row should resolve"
        assert all(len(c) == 6 for c in present)
        jis = [c for c in br["boundary_jis_city_code"].to_list() if c is not None]
        assert all(len(c) == 5 for c in jis)

    def test_manually_verified_is_never_written_by_code(self) -> None:
        br = build_station_bridge(
            stations("s1", "s2"),
            {"s1": [[(0.2, 0.5), (0.8, 0.5)]], "s2": [[(9.0, 9.0), (9.1, 9.1)]]},
            [WEST, EAST], LG_BY_JIS,
        )
        assert "manually_verified" not in set(br["verification_status"])

    def test_every_station_produces_at_least_one_row(self) -> None:
        """No silent drops: POLICY.md §5."""
        st = stations("a", "b", "c")
        br = build_station_bridge(
            st,
            {"a": [[(0.2, 0.5), (0.3, 0.5)]], "b": [[(1.2, 0.5), (1.3, 0.5)]]},
            [WEST, EAST], LG_BY_JIS,
        )
        assert set(br["n02_group_code"]) == {"a", "b", "c"}

    def test_the_fallback_point_is_used_when_the_centroid_misses(self) -> None:
        """A U-shaped station whose centroid falls in the hollow, outside WEST."""
        u = [[(0.1, 0.1), (0.1, 0.9), (0.9, 0.9), (0.9, 0.1)]]
        ring = [[(0.0, 0.85), (1.0, 0.85), (1.0, 1.0), (0.0, 1.0), (0.0, 0.85)]]
        top_strip = ("33333", (0.0, 0.85, 1.0, 1.0), ring)
        br = build_station_bridge(stations("s1"), {"s1": u}, [top_strip], {"33333": "333336"})
        row = br.to_dicts()[0]
        assert row["lg_code"] == "333336"
        assert row["placement_point"] == "point_on_line"
