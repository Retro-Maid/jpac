"""文部科学省 学校コード: 一覧の読み方と、所在地 → 町字の照合（build/school.py）.

照合の規則は「間違って結ぶより、結ばずに残す」側に倒してある。ここで固定するのは、
2026-10-15 の試算で実際に見つかった誤りと、その防ぎ方である。
"""

from __future__ import annotations

import polars as pl
import pytest

from jp_address_crosswalk.build import school
from jp_address_crosswalk.errors import SourceSchemaChanged
from jp_address_crosswalk.export import writers

HEADER = ",".join(school.COLUMNS)


def _csv(*rows: str) -> bytes:
    return "\n".join([HEADER, *rows]).encode(school.ENCODING)


def _code(prefix12: str) -> str:
    """検査数字を付けた13桁。"""
    return prefix12 + str(school.check_digit(prefix12 + "0"))


ROW_ACTIVE = (
    f"{_code('B11121000001')},B1(小学校),11(埼玉),2(公),1(本),テスト小学校,"
    "埼玉県テスト市小野田２２番地,3000001,2020/12/22,,116501,"
)


class TestCheckDigit:
    def test_the_publishers_worked_example(self) -> None:
        """別添1 別紙の算出イメージ: B1-48-2-1234567 の検査数字は 4."""
        assert school.check_digit("B148212345674") == 4

    def test_every_school_type_letter_is_converted(self) -> None:
        for letter in "ABCDEFGH":
            assert 0 <= school.check_digit(f"{letter}111210000010") <= 9


class TestReadingTheList:
    def test_the_header_must_match(self) -> None:
        bad = "学校コード,学校名\nB1,x\n".encode(school.ENCODING)
        with pytest.raises(SourceSchemaChanged):
            school.read_school_csv(bad)

    def test_columns_are_split_into_code_and_name(self) -> None:
        table = school.build_school_table(school.read_school_csv(_csv(ROW_ACTIVE)))
        row = table.row(0, named=True)
        assert (row["school_type_code"], row["school_type_name"]) == ("B1", "小学校")
        assert (row["pref_code"], row["founder_code"], row["branch_code"]) == ("11", "2", "1")
        assert row["attribute_set_date"] == "2020-12-22"
        assert row["is_active"] and row["check_digit_valid"]

    def test_a_kept_old_check_digit_is_flagged_not_fixed(self) -> None:
        """種別が変わった学校は、元の種別の検査数字を持ち続けている（発行元の値）."""
        good = _code("A11121000001")
        kept = "A2" + good[2:]  # 幼稚園 → 認定こども園。検査数字はそのまま
        raw = school.read_school_csv(_csv(
            f"{kept},A2(こども),11(埼玉),2(公),1(本),園,埼玉県テスト市本町１,3000001,2025/5/1,,,"
        ))
        row = school.build_school_table(raw).row(0, named=True)
        assert row["school_code"] == kept
        assert row["check_digit_valid"] is False

    def test_abolished_school_and_its_successors(self) -> None:
        a, b, c = _code("B11121000001"), _code("B11121000002"), _code("B11121000003")
        raw = school.read_school_csv(_csv(
            f'{a},B1(小学校),11(埼玉),2(公),9(廃),旧校,埼玉県テスト市本町１,3000001,'
            f'2020/12/22,2024/3/31,,"{b}、{c}"',
            f"{b},B1(小学校),11(埼玉),2(公),1(本),新校,埼玉県テスト市本町２,3000001,2024/4/1,,,",
        ))
        schools = school.build_school_table(raw)
        assert schools.filter(pl.col("school_code") == a)["is_active"][0] is False
        succession = school.build_succession(schools)
        assert succession.rows() == [(a, b, True), (a, c, False)]
        assert school.consistency_problems(schools, succession) == []

    def test_a_code_that_disagrees_with_its_columns_is_a_problem(self) -> None:
        raw = school.read_school_csv(_csv(ROW_ACTIVE.replace("11(埼玉)", "12(千葉)")))
        schools = school.build_school_table(raw)
        problems = school.consistency_problems(schools, school.build_succession(schools))
        assert len(problems) == 1 and "都道府県" in problems[0]


# ------------------------------------------------------------------ matching

MUNICIPALITY = pl.DataFrame(
    [
        {"lg_code": "112011", "pref": "埼玉県", "county": None, "city": "テスト市", "ward": None},
        {"lg_code": "113000", "pref": "埼玉県", "county": "郡郡", "city": "村村", "ward": None},
        # 政令市の親と、その区
        {"lg_code": "111007", "pref": "埼玉県", "county": None, "city": "政令市", "ward": None},
        {"lg_code": "111015", "pref": "埼玉県", "county": None, "city": "政令市", "ward": "北区"},
        {"lg_code": "111023", "pref": "埼玉県", "county": None, "city": "政令市", "ward": "南区"},
    ]
).with_columns(pl.lit(True).alias("is_current"))

TOWNS = [
    ("a_ono", "112011", "小野"),
    ("a_mano4", "112011", "真野4丁目"),
    ("a_mano", "112011", "真野"),
    ("a_dup1", "112011", "大字重複"),
    ("a_dup2", "112011", "重複"),
    ("a_kita", "111015", "北町"),
    ("a_mura", "113000", "大字山"),
]
ADDRESS = pl.DataFrame(
    [{"address_id": a, "lg_code": lg, "full_name_normalized": n, "valid_to": None}
     for a, lg, n in TOWNS],
    schema={"address_id": pl.Utf8, "lg_code": pl.Utf8, "full_name_normalized": pl.Utf8,
            "valid_to": pl.Utf8},
)
POSTAL = pl.DataFrame(
    {"address_id": ["a_mano4", "a_ono"], "postal_code": ["3000004", "3000001"]}
)


def _bridge(address: str, postal: str = "3000004") -> pl.DataFrame:
    schools = pl.DataFrame(
        [{"school_code": "B111210000013", "pref_code": "11",
          "address_raw": address, "postal_code_raw": postal}]
    )
    return school.build_school_bridge(schools, ADDRESS, MUNICIPALITY, POSTAL)


class TestMatching:
    def test_a_shorter_different_town_is_not_taken(self) -> None:
        """「小野田２２番地」は町字「小野」ではない（試算で実際に起きた誤り）."""
        b = _bridge("埼玉県テスト市小野田２２番地")
        assert b["relation_type"].to_list() == ["municipality_only"]
        assert b["lg_code"][0] == "112011" and b["address_id"][0] is None

    def test_the_town_name_followed_by_a_banchi_is_taken(self) -> None:
        b = _bridge("埼玉県テスト市小野２２番地", postal="3000001")
        row = b.row(0, named=True)
        assert (row["relation_type"], row["address_id"]) == ("located_in", "a_ono")
        assert row["verification_status"] == "auto" and row["postal_check"] == "agrees"

    def test_a_leading_number_is_read_as_chome_when_the_town_has_it(self) -> None:
        row = _bridge("埼玉県テスト市真野４－６－２７").row(0, named=True)
        assert row["address_id"] == "a_mano4"
        assert row["matched_town_name"] == "真野4丁目"

    def test_a_differing_postal_code_is_recorded_and_left_for_review(self) -> None:
        row = _bridge("埼玉県テスト市真野４－６－２７", postal="3009999").row(0, named=True)
        assert row["relation_type"] == "located_in"
        assert row["postal_check"] == "differs"
        assert row["verification_status"] == "review_required"
        assert row["mismatch_note"]

    def test_two_towns_of_the_same_name_keep_both(self) -> None:
        """「大字重複」と「重複」は、大字を落とすと同じ名前になる."""
        b = _bridge("埼玉県テスト市重複１２")
        assert b["relation_type"].to_list() == ["ambiguous", "ambiguous"]
        assert sorted(b["address_id"].to_list()) == ["a_dup1", "a_dup2"]
        assert set(b["candidate_count"]) == {2}

    def test_county_and_oaza_may_be_omitted(self) -> None:
        row = _bridge("埼玉県村村山１０").row(0, named=True)
        assert (row["lg_code"], row["address_id"]) == ("113000", "a_mura")

    def test_a_designated_city_without_its_ward_searches_the_wards(self) -> None:
        row = _bridge("埼玉県政令市北町３").row(0, named=True)
        assert (row["lg_code"], row["address_id"]) == ("111015", "a_kita")

    def test_an_unknown_municipality_is_unresolved_and_kept(self) -> None:
        row = _bridge("埼玉県どこか町１").row(0, named=True)
        assert row["relation_type"] == "unresolved"
        assert row["lg_code"] is None and row["confidence"] == 0.0


class TestShippedChecks:
    def test_every_relation_inserts_under_the_ddl(self, tmp_path) -> None:
        """CHECK は SQLite に書いたときに初めて効く（tests/test_lineage_sqlite.py と同じ理由）."""
        bridge = pl.concat([
            _bridge("埼玉県テスト市小野２２番地", postal="3000001"),
            _bridge("埼玉県テスト市真野４－６－２７", postal="3009999"),
            _bridge("埼玉県テスト市重複１２"),
            _bridge("埼玉県テスト市小野田２２番地"),
            _bridge("埼玉県どこか町１"),
        ])
        assert set(bridge["relation_type"]) == {
            "located_in", "ambiguous", "municipality_only", "unresolved",
        }
        empty = bridge.head(0)
        writers.write_sqlite({"bridge_school_address": bridge}, empty, empty,
                             tmp_path / "school.sqlite")


# ------------------------------------------------------------------ P29 points

def _p29(*rows: tuple[str, str]) -> pl.DataFrame:
    return pl.DataFrame(
        [{"school_code": c, "campus_code": k, "school_name_raw": "校", "campus_note_raw": "",
          "p29_admin_code": "11201", "closed_code_raw": "1"} for c, k in rows]
    ).with_row_index("p29_row")


class TestSchoolPoints:
    def test_each_campus_keeps_its_own_published_point(self) -> None:
        from jp_address_crosswalk.build import points

        p29 = _p29(("B111210000013", "01"), ("B111210000013", "02"))
        pts = points.build_school_points(p29, [(139.5, 35.9), (139.6, 35.8)], "snap_p29")
        assert pts.rows() == [
            ("B111210000013", "01", "校", None, "11201", "1", 139.5, 35.9, "snap_p29"),
            ("B111210000013", "02", "校", None, "11201", "1", 139.6, 35.8, "snap_p29"),
        ]

    def test_points_and_attributes_must_pair_up(self) -> None:
        """数がずれたら、別の学校の位置を付けることになるので止める."""
        from jp_address_crosswalk.build import points

        with pytest.raises(ValueError):
            points.build_school_points(_p29(("B111210000013", "00")), [], "snap_p29")

    def test_check_reports_unknown_schools_and_out_of_range_points(self) -> None:
        from jp_address_crosswalk.build import points

        pts = points.build_school_points(
            _p29(("B111210000013", "00"), ("C111210000012", "00")),
            [(139.5, 35.9), (10.0, 35.9)], "snap_p29",
        )
        known = pl.DataFrame({"school_code": ["B111210000013"]})
        problems = points.check_points({"school_point": pts}, {"mext_school": known})
        assert any("範囲外" in p for p in problems)
        assert any("一覧に無い" in p for p in problems)
