"""e-Stat 境界データと jpac 市区町村の突合 (docs/STATION_JOIN_PLAN.md §4.3).

The property under test is not a match rate. It is that **every code on either
side is classified**, and that the classes which mean "someone accounted for
this" are kept apart from the one that means "nobody has yet". A version skew
reported as an unexplained mismatch, or an unexplained mismatch quietly folded
into a benign class, are the two failures that matter.

Both were real: the first implementation read the lineage file in one direction
only, so the six superseded 浜松 ward codes were explained while the three
successor codes were not, and a municipality split across two successors was
reported as needing investigation when the file already said why it could not
be mapped.
"""

from __future__ import annotations

import polars as pl

from jp_address_crosswalk.build import estat as eb

SMALL_AREA_SCHEMA = {
    "key_code": pl.Utf8, "jis_city_code": pl.Utf8, "city_name_raw": pl.Utf8,
    "hcode": pl.Utf8,
}
MUNI_SCHEMA = {
    "jis_city_code": pl.Utf8, "pref": pl.Utf8, "city": pl.Utf8, "ward": pl.Utf8,
    "is_current": pl.Int64,
}


def _small_area(rows: list[tuple[str, str, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"key_code": f"k{i}", "jis_city_code": c, "city_name_raw": n, "hcode": h}
            for i, (c, n, h) in enumerate(rows)
        ],
        schema=SMALL_AREA_SCHEMA,
    )


def _municipality(rows: list[tuple[str, str, str, str | None]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"jis_city_code": c, "pref": p, "city": city, "ward": ward, "is_current": 1}
            for c, p, city, ward in rows
        ],
        schema=MUNI_SCHEMA,
    )


def _status(rec: pl.DataFrame, code: str) -> str:
    return rec.filter(pl.col("jis_city_code") == code)["reconcile_status"][0]


class TestReconcile:
    def test_a_designated_city_parent_is_not_a_mismatch(self) -> None:
        """The 省令 puts 政令市 boundaries at ward level, so the parent has none."""
        muni = _municipality(
            [
                ("14100", "神奈川県", "横浜市", None),
                ("14101", "神奈川県", "横浜市", "鶴見区"),
            ]
        )
        _, rec = eb.reconcile(_small_area([("14101", "横浜市鶴見区", "8101")]), muni)
        assert _status(rec, "14100") == "designated_city_parent"
        assert rec.filter(pl.col("reconcile_status").is_in(list(eb.UNEXPLAINED))).height == 0

    def test_parents_are_found_by_having_wards_not_by_a_00_suffix(self) -> None:
        """川崎 14130 is a parent and does not end in 00; 倶知安 01400 does and is not.

        Measured on the real data: 17 codes end in 00 and two of them are ordinary
        municipalities, while four parents do not end in 00.
        """
        muni = _municipality(
            [
                ("14130", "神奈川県", "川崎市", None),
                ("14131", "神奈川県", "川崎市", "川崎区"),
                ("01400", "北海道", "倶知安町", None),
            ]
        )
        _, rec = eb.reconcile(
            _small_area([("14131", "川崎市川崎区", "8101"), ("01400", "倶知安町", "8101")]),
            muni,
        )
        assert _status(rec, "14130") == "designated_city_parent"
        assert rec.filter(pl.col("jis_city_code") == "01400").height == 0

    def test_northern_territories_have_no_boundary_and_that_is_correct(self) -> None:
        muni = _municipality([("01695", "北海道", "色丹村", None)])
        _, rec = eb.reconcile(_small_area([]), muni)
        assert _status(rec, "01695") == "not_surveyed"

    def test_lineage_is_read_in_both_directions(self) -> None:
        """The regression: a successor must be explained, not just the predecessor.

        e-Stat speaks the pre-reorganisation code and jpac the post- one, so each
        side is missing what the other has. Reading old→new only explains the
        e-Stat side and leaves the jpac side reported as unexplained.
        """
        muni = _municipality([("22138", "静岡県", "浜松市", "中央区")])
        lineage = {"221317": "221384"}          # 中区 → 中央区, 6-digit as on disk
        _, rec = eb.reconcile(
            _small_area([("22131", "浜松市中区", "8101")]), muni, lineage
        )
        assert _status(rec, "22131") == "superseded"     # e-Stat side
        assert _status(rec, "22138") == "successor"      # jpac side
        assert rec.filter(pl.col("reconcile_status").is_in(list(eb.UNEXPLAINED))).height == 0

    def test_a_split_municipality_is_explained_not_investigated(self) -> None:
        """北区 has two successors, so it is in `unlisted`, not `transitions`.

        Forcing a single successor would be the invented 1:1 mapping POLICY.md §4
        calls a defect, but reporting it as "requires investigation" is also wrong:
        it has been investigated and recorded.
        """
        _, rec = eb.reconcile(
            _small_area([("22135", "浜松市北区", "8101")]),
            _municipality([]),
            lineage={},
            unlisted={"221350": "中央区と浜名区に分割された"},
        )
        assert _status(rec, "22135") == "split_no_single_successor"
        assert rec.filter(pl.col("reconcile_status").is_in(list(eb.UNEXPLAINED))).height == 0

    def test_a_nameless_polygon_is_unassigned_not_a_mismatch(self) -> None:
        labelled, rec = eb.reconcile(
            _small_area([("13199", "", "8101")]), _municipality([])
        )
        assert labelled["reconcile_status"].to_list() == ["unassigned_area"]
        assert _status(rec, "13199") == "unassigned_area"

    def test_an_unaccounted_code_stays_unexplained(self) -> None:
        """The class that must not silently absorb anything."""
        _, rec = eb.reconcile(
            _small_area([("99999", "架空市", "8101")]), _municipality([("11111", "某県", "某市", None)])
        )
        assert _status(rec, "99999") == "estat_only"
        assert _status(rec, "11111") == "jpac_only"
        assert set(rec["reconcile_status"]) == set(eb.UNEXPLAINED)

    def test_water_surface_areas_do_not_define_the_municipality_set(self) -> None:
        """HCODE 8154 is 港湾区域/漁港の水域, not land (省令 第一条4項).

        A municipality present only as a water-surface polygon must not count as
        having a boundary, or the station join would place points in it.
        """
        muni = _municipality([("12345", "某県", "某市", None)])
        _, rec = eb.reconcile(_small_area([("12345", "某市", "8154")]), muni)
        assert _status(rec, "12345") == "jpac_only"


class TestStatusVocabulary:
    def test_unexplained_is_a_subset_of_the_declared_statuses(self) -> None:
        assert set(eb.UNEXPLAINED) <= set(eb.STATUSES)

    def test_every_produced_status_is_declared(self) -> None:
        muni = _municipality(
            [("14100", "神奈川県", "横浜市", None), ("14101", "神奈川県", "横浜市", "鶴見区")]
        )
        labelled, rec = eb.reconcile(
            _small_area([("14101", "横浜市鶴見区", "8101"), ("13199", "", "8101")]), muni
        )
        produced = set(labelled["reconcile_status"]) | set(rec["reconcile_status"])
        assert produced <= set(eb.STATUSES), f"undeclared: {produced - set(eb.STATUSES)}"


class TestLandPolygon:
    """One definition of "land" for every spatial consumer (station, mesh)."""

    def test_an_ordinary_town_polygon_is_land(self) -> None:
        assert eb.is_land_polygon("8101", "")

    def test_a_detached_part_is_land(self) -> None:
        """飛び地 (D) is a genuine part of its own municipality."""
        assert eb.is_land_polygon("8101", "D")

    def test_a_hole_is_not_land(self) -> None:
        """抜け地 (D1) carries the enclosing code over another municipality's 飛び地."""
        assert not eb.is_land_polygon("8101", "D1")

    def test_a_water_district_is_not_land(self) -> None:
        assert not eb.is_land_polygon("8154", "")
