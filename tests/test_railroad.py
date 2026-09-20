"""鉄道路線 → 市区町村 と 駅 → 路線 (src/jp_address_crosswalk/build/railroad.py).

Held to the discipline the station bridge is held to (tests/test_station_bridge.py).
Four things here are the point, and each is a case where the obvious shortcut is
wrong:

* including 鉄道区分 in a line's identity splits real lines that change class
  along their length,
* the one publisher record with 路線名/運営会社 reversed must be corrected from
  the station file rather than from a hand-written list — and a *second* such
  record must fail rather than be absorbed by the same rule,
* testing only the vertices of a polyline misses a municipality the track
  crosses between two distant vertices,
* a line in no polygon stays unresolved instead of being snapped to the nearest.
"""

from __future__ import annotations

import polars as pl

from jp_address_crosswalk.build.railroad import (
    build_line_municipality_bridge,
    build_railroad_lines,
    build_station_line_bridge,
    correct_pairs,
    detect_swapped_pairs,
    sample_points,
)

# Two adjacent unit squares, a far-away one, and a narrow strip that a straight
# line crosses between two distant vertices.
WEST = ("11111", (0.0, 0.0, 1.0, 1.0),
        [[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]])
EAST = ("22222", (1.0, 0.0, 2.0, 1.0),
        [[(1.0, 0.0), (2.0, 0.0), (2.0, 1.0), (1.0, 1.0), (1.0, 0.0)]])
# A code the 2020 boundaries carry and jpac does not — the 旧浜松区 case.
GONE = ("99999", (3.0, 0.0, 4.0, 1.0),
        [[(3.0, 0.0), (4.0, 0.0), (4.0, 1.0), (3.0, 1.0), (3.0, 0.0)]])
NARROW = ("33333", (1.40, 0.0, 1.405, 1.0),
          [[(1.400, 0.0), (1.405, 0.0), (1.405, 1.0), (1.400, 1.0), (1.400, 0.0)]])

LG_BY_JIS = {"11111": "111116", "22222": "222226", "33333": "333336"}


def sections(*rows: tuple[str, str, str, str]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"railway_class": a, "operator_class": b,
             "line_name_raw": c, "operator_name_raw": d}
            for a, b, c, d in rows
        ],
        schema={"railway_class": pl.Utf8, "operator_class": pl.Utf8,
                "line_name_raw": pl.Utf8, "operator_name_raw": pl.Utf8},
    )


def features(*rows: tuple[str, str, str, str]) -> pl.DataFrame:
    """Station features: (group code, station code, line, operator)."""
    return pl.DataFrame(
        [
            {"n02_group_code": g, "n02_station_code": s, "station_name_raw": f"駅{g}",
             "line_name_raw": ln, "operator_name_raw": op}
            for g, s, ln, op in rows
        ],
        schema={"n02_group_code": pl.Utf8, "n02_station_code": pl.Utf8,
                "station_name_raw": pl.Utf8, "line_name_raw": pl.Utf8,
                "operator_name_raw": pl.Utf8},
    )


def lines_frame(*pairs: tuple[str, str]) -> pl.DataFrame:
    return pl.DataFrame(
        [{"line_name_raw": a, "operator_name_raw": b} for a, b in pairs],
        schema={"line_name_raw": pl.Utf8, "operator_name_raw": pl.Utf8},
    )


class TestLineIdentity:
    def test_a_line_that_changes_class_stays_one_line(self) -> None:
        """富山地方鉄道の本線 is both 普通鉄道 (12) and 軌道 (21) along its length.

        Keying on all four attributes would make it two lines, which is the same
        inflation N02_005g prevents for stations.
        """
        lines, swapped = build_railroad_lines(
            sections(("12", "4", "本線", "富山地方鉄道"),
                     ("21", "4", "本線", "富山地方鉄道")),
            features(("g1", "s1", "本線", "富山地方鉄道")),
        )
        assert lines.height == 1
        row = lines.to_dicts()[0]
        assert row["section_count"] == 2
        assert row["railway_class_variants"] == 2
        assert row["railway_class"] in {"12", "21"}
        assert swapped == []

    def test_distinct_lines_stay_distinct(self) -> None:
        lines, _ = build_railroad_lines(
            sections(("11", "2", "東北線", "東日本旅客鉄道"),
                     ("11", "2", "山陰線", "西日本旅客鉄道")),
            features(("g1", "s1", "東北線", "東日本旅客鉄道")),
        )
        assert lines.height == 2


class TestSwappedPair:
    """N02 carries one 路線 record with 路線名 and 運営会社 reversed."""

    SEC = sections(
        ("12", "4", "えちぜん鉄道", "三国芦原線"),   # reversed, as published
        ("12", "4", "勝山永平寺線", "えちぜん鉄道"),  # correct
    )
    FEAT = features(
        ("g1", "s1", "三国芦原線", "えちぜん鉄道"),
        ("g2", "s2", "勝山永平寺線", "えちぜん鉄道"),
    )

    def test_detected_from_the_station_file_not_a_hand_written_list(self) -> None:
        assert detect_swapped_pairs(self.SEC, self.FEAT) == [
            ("えちぜん鉄道", "三国芦原線")
        ]

    def test_a_station_less_line_is_not_mistaken_for_a_swap(self) -> None:
        """Only a pair whose *reverse* is on a station qualifies."""
        assert detect_swapped_pairs(
            sections(("11", "2", "貨物線", "日本貨物鉄道")),
            features(("g1", "s1", "本線", "東日本旅客鉄道")),
        ) == []

    def test_correction_takes_the_station_orientation(self) -> None:
        fixed = correct_pairs(self.SEC, detect_swapped_pairs(self.SEC, self.FEAT))
        pairs = set(zip(fixed["line_name_raw"], fixed["operator_name_raw"], strict=True))
        assert pairs == {
            ("三国芦原線", "えちぜん鉄道"),
            ("勝山永平寺線", "えちぜん鉄道"),
        }

    def test_correcting_reconciles_the_two_files_exactly(self) -> None:
        """The assertion that makes a second occurrence fail loudly.

        After correction the line set and the station set must agree in both
        directions; a new reversed record the rule did not cover would leave a
        difference here rather than being absorbed.
        """
        lines, swapped = build_railroad_lines(self.SEC, self.FEAT)
        assert len(swapped) == 1
        line_pairs = set(
            zip(lines["line_name_raw"], lines["operator_name_raw"], strict=True)
        )
        station_pairs = set(
            zip(self.FEAT["line_name_raw"], self.FEAT["operator_name_raw"], strict=True)
        )
        assert line_pairs - station_pairs == set()
        assert station_pairs - line_pairs == set()


class TestSampling:
    def test_a_short_segment_yields_only_its_vertices(self) -> None:
        pts = list(sample_points([(0.0, 0.0), (0.0005, 0.0)], step_m=100.0))
        assert pts == [(0.0, 0.0), (0.0005, 0.0)]

    def test_a_long_segment_is_filled_in(self) -> None:
        """~1.1 km at this latitude, so 100 m steps add about ten points."""
        pts = list(sample_points([(0.0, 0.0), (0.01, 0.0)], step_m=100.0))
        assert len(pts) > 10
        assert pts[0] == (0.0, 0.0)
        assert pts[-1] == (0.01, 0.0)


class TestLineMunicipalityBridge:
    def test_a_line_through_two_municipalities_keeps_both(self) -> None:
        bridge = build_line_municipality_bridge(
            lines_frame(("本線", "会社")),
            {("本線", "会社"): [[(0.5, 0.5), (1.5, 0.5)]]},
            [WEST, EAST],
            LG_BY_JIS,
        )
        assert bridge.height == 2
        assert set(bridge["lg_code"]) == {"111116", "222226"}
        assert set(bridge["relation_type"]) == {"overlap"}
        assert set(bridge["municipality_count"]) == {2}
        assert set(bridge["verification_status"]) == {"auto"}

    def test_a_municipality_crossed_between_two_vertices_is_found(self) -> None:
        """The whole reason sampling exists.

        The polyline has exactly two vertices, at x=0.5 and x=1.9. NARROW spans
        x=1.400..1.405 and contains neither of them, so a vertex-only test would
        report that the line never enters it.
        """
        parts = [[(0.5, 0.5), (1.9, 0.5)]]
        bridge = build_line_municipality_bridge(
            lines_frame(("本線", "会社")), {("本線", "会社"): parts},
            [WEST, EAST, NARROW], LG_BY_JIS,
        )
        assert "333336" in set(bridge["lg_code"])

        vertices_only = build_line_municipality_bridge(
            lines_frame(("本線", "会社")), {("本線", "会社"): parts},
            [WEST, EAST, NARROW], LG_BY_JIS, step_m=1e9,
        )
        assert "333336" not in set(vertices_only["lg_code"])

    def test_a_line_in_no_polygon_is_not_snapped(self) -> None:
        bridge = build_line_municipality_bridge(
            lines_frame(("海底線", "会社")),
            {("海底線", "会社"): [[(9.0, 9.0), (9.1, 9.0)]]},
            [WEST, EAST],
            LG_BY_JIS,
        )
        assert bridge.height == 1
        row = bridge.to_dicts()[0]
        assert row["relation_type"] == "unresolved"
        assert row["lg_code"] is None
        assert row["boundary_jis_city_code"] is None
        assert row["confidence"] == 0.0
        assert row["verification_status"] == "review_required"

    def test_a_code_jpac_lacks_keeps_its_row(self) -> None:
        bridge = build_line_municipality_bridge(
            lines_frame(("本線", "会社")),
            {("本線", "会社"): [[(3.2, 0.5), (3.8, 0.5)]]},
            [WEST, GONE],
            LG_BY_JIS,
        )
        assert bridge.height == 1
        row = bridge.to_dicts()[0]
        assert row["lg_code"] is None
        assert row["boundary_jis_city_code"] == "99999"
        assert row["relation_type"] == "overlap"
        assert row["verification_status"] == "review_required"
        assert "99999" in row["mismatch_note"]

    def test_a_lineage_transition_resolves_the_cross_section(self) -> None:
        """路線の断面ズレ16行にあたる場合。"""
        bridge = build_line_municipality_bridge(
            lines_frame(("本線", "会社")),
            {("本線", "会社"): [[(3.2, 0.5), (3.8, 0.5)]]},
            [WEST, GONE], LG_BY_JIS,
            successors={"99999": ["999996"]},
        )
        assert bridge.height == 1
        row = bridge.to_dicts()[0]
        assert row["lg_code"] == "999996"
        assert row["boundary_jis_city_code"] == "99999"
        assert row["verification_status"] == "auto"
        assert row["match_method"] == "spatial_sampling_via_lineage"
        assert row["mismatch_note"] is None

    def test_a_split_ward_becomes_two_municipalities(self) -> None:
        """路線は一意性を要求しないので、分割は素直に2市区町村になる。"""
        bridge = build_line_municipality_bridge(
            lines_frame(("本線", "会社")),
            {("本線", "会社"): [[(3.2, 0.5), (3.8, 0.5)]]},
            [WEST, GONE], LG_BY_JIS,
            successors={"99999": ["999996", "888886"]},
        )
        assert bridge.height == 2
        assert set(bridge["lg_code"]) == {"999996", "888886"}
        assert set(bridge["municipality_count"]) == {2}
        # 同じポリゴンから出た2行なので、根拠の量は等しい。
        assert len(set(bridge["sample_hits"])) == 1

    def test_sample_hits_count_the_evidence(self) -> None:
        """A long run through a municipality is distinguishable from a clip."""
        bridge = build_line_municipality_bridge(
            lines_frame(("本線", "会社")),
            {("本線", "会社"): [[(0.01, 0.5), (0.99, 0.5)]]},
            [WEST, EAST],
            LG_BY_JIS,
        )
        assert bridge.to_dicts()[0]["sample_hits"] > 10


class TestStationLineBridge:
    def test_an_interchange_station_gets_a_row_per_line(self) -> None:
        """盛岡 is JR and IGR いわて銀河鉄道; grouping must not lose either."""
        bridge = build_station_line_bridge(
            features(
                ("g1", "s1", "東北線", "東日本旅客鉄道"),
                ("g1", "s2", "いわて銀河鉄道線", "IGRいわて銀河鉄道"),
                ("g2", "s3", "東北線", "東日本旅客鉄道"),
            )
        )
        assert bridge.height == 3
        g1 = bridge.filter(pl.col("n02_group_code") == "g1")
        assert g1.height == 2
        assert set(g1["operator_name_raw"]) == {"東日本旅客鉄道", "IGRいわて銀河鉄道"}
        assert set(bridge["match_method"]) == {"n02_attribute"}

    def test_platforms_of_one_line_collapse_to_one_row(self) -> None:
        bridge = build_station_line_bridge(
            features(
                ("g1", "s1", "東北線", "東日本旅客鉄道"),
                ("g1", "s2", "東北線", "東日本旅客鉄道"),
            )
        )
        assert bridge.height == 1
        assert bridge.to_dicts()[0]["feature_count"] == 2
