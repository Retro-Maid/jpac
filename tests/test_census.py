"""国勢調査 令和7年 人口等基本集計（build/census.py）.

xlsx は合成したもので、発行元の表の形（注記の行、5段の表頭、地域識別コード、「-」）
だけを写している。署名前に e-Stat の実データをリポジトリへ入れないため。ここで
確かめるのは、公表された件数だけを持ち、按分も推測もしないことである。
"""

from __future__ import annotations

import io
import sqlite3
import zipfile
from xml.sax.saxutils import escape

import polars as pl
import pytest

from jp_address_crosswalk.build import census
from jp_address_crosswalk.export.writers import _write_table

# ---------------------------------------------------------------- xlsx を作る


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def xlsx(rows: list[list]) -> bytes:
    """文字列は共有文字列表へ、数値は ``<v>`` へ。発行元と同じ書き分け."""
    shared: list[str] = []
    body = []
    for ri, row in enumerate(rows, start=1):
        cells = []
        for ci, v in enumerate(row):
            if v is None:
                continue
            ref = f"{_col(ci)}{ri}"
            if isinstance(v, str):
                shared.append(v)
                cells.append(f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
        body.append(f'<row r="{ri}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml",
                   f'<workbook {ns} {rns}><sheets><sheet name="s" sheetId="1" r:id="rId1"/>'
                   "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                   'relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" '
                   'Type="x"/></Relationships>')
        z.writestr("xl/sharedStrings.xml",
                   f"<sst {ns}>" + "".join(f"<si><t>{escape(s)}</t></si>" for s in shared)
                   + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml",
                   f"<worksheet {ns}><sheetData>{''.join(body)}</sheetData></worksheet>")
    return buf.getvalue()


# ---------------------------------------------------------------- 第1-1表

T01_HEAD = [None] * 7
_ITEMS = {i: (item, name) for i, item, name, _c, _k in census.T01_VALUE_COLUMNS}


def t01_rows(regions: list[list]) -> list[list]:
    """regions: [地域識別コード, 2025年コード, 地域名, 2000年コード, 値18個]."""
    item = [*T01_HEAD[:6], "表章項目", *[_ITEMS[i][0] for i in range(7, 25)]]
    name = [*T01_HEAD[:6], "項目名", *[_ITEMS[i][1] for i in range(7, 25)]]
    out = [
        ["【原数値】令和７年国勢調査　人口等基本集計"],
        ["第１－１表　男女別人口…"],
        [],
        ["5) 「面積（参考）」については、国土交通省国土地理院「…面積調」による。"],
        item,
        [*T01_HEAD[:6], "事項名"],
        name,
        [*T01_HEAD[:6], "階層レベル"],
        [*T01_HEAD[:6], "表章単位"],
        [*census.REGION_HEADERS, " "],
    ]
    for level, code, area_name, code2000, values in regions:
        out.append([level, f"{code[:2]}_x", code2000 or code, "2000" if level != "2" else None,
                    f"{code[:2]}_x", code, area_name, *values])
    return out


def vals(total, male, female, *, rebased=None, inst=0, area=1.0, density=1.0):
    """1地域の18値。世帯数・世帯人員は人口と同じにして、施設等の世帯だけ指定できる."""
    rebased = total if rebased is None else rebased
    hh_gen = total if inst == "-" else total - inst
    return [total, male, female, rebased, 0, 0.0, 100.0, area, density,
            total, hh_gen, inst, total, 0, 0.0, total, hh_gen, inst]


NATIONAL = [
    ["a", "00000", "0001_全国", None, vals(1100, 550, 550)],
    ["a", "01000", "0002_北海道", None, vals(1000, 500, 500)],
    ["1", "01100", "0003_札幌市", None, vals(600, 300, 300)],
    ["0", "01101", "0004_札幌市中央区", None, vals(400, 200, 200)],
    ["0", "01102", "0005_札幌市北区", None, vals(200, 100, 100)],
    ["2", "01202", "0014_函館市", None, vals(300, 150, 150)],
    ["3", "01403", "0090_泊村", None, vals(100, 50, 50, inst="-")],
    ["9", "01202", "0015_（旧：函館市）", "01202", vals(250, 125, 125)],
    ["a", "13000", "0003_東京都", None, vals(100, 50, 50)],
    ["1", "13100", "1213_特別区部", None, vals(100, 50, 50)],
    ["0", "13101", "1214_千代田区", None, vals(100, 50, 50)],
]

MUNICIPALITY = pl.DataFrame({
    "lg_code": ["011002", "011011", "011029", "012025", "014036", "016969", "131016"],
    "jis_city_code": ["01100", "01101", "01102", "01202", "01403", "01696", "13101"],
    "is_current": [1] * 7,
})


@pytest.fixture
def raw() -> pl.DataFrame:
    return census.parse_t01(xlsx(t01_rows(NATIONAL)))


class TestRead:
    def test_area_and_density_are_never_read(self, raw):
        assert not [c for c in raw.columns if "area_km2" in c or "density" in c]
        assert "population_total_raw" in raw.columns

    def test_cells_are_kept_as_published_text(self, raw):
        tomari = raw.filter(pl.col("area_code_2025_raw") == "01403").row(0, named=True)
        assert tomari["households_institutional_raw"] == "-"
        assert tomari["population_total_raw"] == "100"

    def test_every_row_is_kept_including_aggregates_and_2000_areas(self, raw):
        assert raw.height == len(NATIONAL)
        assert set(raw["area_level_raw"]) == {"a", "1", "0", "2", "3", "9"}

    def test_a_changed_header_stops_rather_than_guessing(self):
        rows = t01_rows(NATIONAL)
        rows[4][14] = "可住地面積"
        with pytest.raises(census.CensusLayoutError):
            census.parse_t01(xlsx(rows))

    def test_a_row_with_values_but_no_level_code_stops(self):
        rows = t01_rows(NATIONAL)
        rows.append([None, "01_x", "01999", None, "01_x", "01999", "謎の地域", 1])
        with pytest.raises(census.CensusLayoutError):
            census.parse_t01(xlsx(rows))

    def test_notes_are_part_of_the_layout(self):
        notes = census.table_notes(census.read_xlsx_rows(xlsx(t01_rows(NATIONAL))))
        assert any("国土地理院" in n for n in notes)


class TestValues:
    @pytest.mark.parametrize("raw_value, kind, expected", [
        ("-", "count", 0),       # 該当数値がないもの（ユーザーズガイド §3(1)）
        ("-", "rate", None),     # 率は計算できない
        ("123", "count", 123),
        ("-2.51579", "rate", -2.51579),
        (None, "count", None),
    ])
    def test_legend(self, raw_value, kind, expected):
        assert census.parse_value(raw_value, kind) == expected

    @pytest.mark.parametrize("bad", ["X", "1.5", "…"])
    def test_unreadable_counts_stop(self, bad):
        with pytest.raises(census.CensusLayoutError):
            census.parse_value(bad, "count")


class TestMunicipality:
    def test_one_row_per_current_municipality(self, raw):
        m = census.municipality_population(raw, MUNICIPALITY)
        assert m.height == MUNICIPALITY.height
        assert m["lg_code"].n_unique() == m.height

    def test_not_enumerated_is_null_with_a_reason(self, raw):
        m = census.municipality_population(raw, MUNICIPALITY)
        northern = m.filter(pl.col("lg_code") == "016969").row(0, named=True)
        assert northern["population_total"] is None
        assert northern["value_status"] == "not_enumerated"

    def test_2000_areas_and_aggregates_are_not_attached_to_lg_code(self, raw):
        m = census.municipality_population(raw, MUNICIPALITY)
        hakodate = m.filter(pl.col("lg_code") == "012025").row(0, named=True)
        assert hakodate["population_total"] == 300   # 2025年の函館市。旧：函館市の 250 ではない
        assert census.unmatched_census_codes(raw, MUNICIPALITY) == []

    def test_only_the_known_northern_villages_may_be_absent(self, raw):
        extra = pl.concat([MUNICIPALITY, pl.DataFrame({
            "lg_code": ["999990"], "jis_city_code": ["99999"], "is_current": [1],
        })])
        assert census.missing_from_census(raw, MUNICIPALITY) == []
        assert census.missing_from_census(raw, extra) == ["99999"]
        m = census.municipality_population(raw, extra)
        assert m.filter(pl.col("lg_code") == "999990")["value_status"].item() ==             "missing_from_table"

    def test_dash_counts_are_zero(self, raw):
        m = census.municipality_population(raw, MUNICIPALITY)
        assert m.filter(pl.col("lg_code") == "014036")["households_institutional"].item() == 0

    def test_valid_from_is_the_census_date(self, raw):
        m = census.municipality_population(raw, MUNICIPALITY)
        assert set(m["valid_from"]) == {"2025-10-01"}


class TestConsistency:
    def test_clean_table_has_no_problems(self, raw):
        assert census.consistency_problems(raw) == []

    def test_wards_pair_with_their_parent_by_code(self, raw):
        assert census.ward_parents(raw) == {
            "01101": "01100", "01102": "01100", "13101": "13100",
        }

    def test_ward_sum_mismatch_is_reported(self):
        rows = [r if r[1] != "01102" else ["0", "01102", r[2], None, vals(150, 75, 75)]
                for r in NATIONAL]
        problems = census.consistency_problems(census.parse_t01(xlsx(t01_rows(rows))))
        assert any("01100" in p and "区の合計" in p for p in problems)

    def test_male_plus_female_mismatch_is_reported(self):
        rows = [r if (r[0], r[1]) != ("2", "01202") else ["2", "01202", r[2], None, vals(300, 150, 149)]
                for r in NATIONAL]
        problems = census.consistency_problems(census.parse_t01(xlsx(t01_rows(rows))))
        assert any("01202" in p and "population_total" in p for p in problems)


class TestNumberingArea:
    def bridge(self, rows):
        return pl.DataFrame(rows, schema=["numbering_area_code", "lg_code", "relation_type"],
                            orient="row")

    def test_whole_municipalities_sum(self, raw):
        m = census.municipality_population(raw, MUNICIPALITY)
        b = self.bridge([("011", "012025", "child"), ("011", "014036", "child")])
        out = census.numbering_area_population(m, b, MUNICIPALITY, raw).row(0, named=True)
        assert out["population_total"] == 400
        assert out["value_status"] == "complete"

    def test_a_partial_municipality_leaves_the_area_null(self, raw):
        """按分しない。一部だけを含む市区町村が1つでもあれば値を出さない."""
        m = census.municipality_population(raw, MUNICIPALITY)
        b = self.bridge([("012", "012025", "child"), ("012", "014036", "overlap")])
        out = census.numbering_area_population(m, b, MUNICIPALITY, raw).row(0, named=True)
        assert out["population_total"] is None
        assert out["value_status"] == "partial_municipality"

    def test_parent_and_wards_are_not_double_counted(self, raw):
        m = census.municipality_population(raw, MUNICIPALITY)
        b = self.bridge([("001", "011002", "child"), ("001", "011011", "child"),
                         ("001", "011029", "child"), ("001", "014036", "child")])
        out = census.numbering_area_population(m, b, MUNICIPALITY, raw).row(0, named=True)
        assert out["population_total"] == 700   # 札幌市 600 + 泊村 100。1,300 ではない

    def test_a_partial_parent_is_not_replaced_by_its_wards(self, raw):
        """親が overlap なら、区がすべて child でも親の行を外さない。条文が区ではなく
        町名で切っていれば、区の行だけでは区画の範囲を言い尽くせない."""
        m = census.municipality_population(raw, MUNICIPALITY)
        b = self.bridge([("003", "011002", "overlap"), ("003", "011011", "child")])
        out = census.numbering_area_population(m, b, MUNICIPALITY, raw).row(0, named=True)
        assert out["value_status"] == "partial_municipality"
        assert out["population_total"] is None

    def test_a_partial_parent_is_judged_by_its_wards(self, raw):
        m = census.municipality_population(raw, MUNICIPALITY)
        b = self.bridge([("002", "011002", "overlap"), ("002", "011011", "child"),
                         ("002", "011029", "overlap")])
        out = census.numbering_area_population(m, b, MUNICIPALITY, raw).row(0, named=True)
        assert out["value_status"] == "partial_municipality"

    def test_an_unresolved_clause_leaves_the_area_null(self, raw):
        """T7 の行（lg_code NULL）がある区画は、構成が分からないので出さない."""
        m = census.municipality_population(raw, MUNICIPALITY)
        b = pl.DataFrame({"numbering_area_code": ["014", "014"],
                          "lg_code": ["012025", None],
                          "relation_type": ["child", "unresolved"]})
        out = census.numbering_area_population(m, b, MUNICIPALITY, raw).row(0, named=True)
        assert out["value_status"] == "unresolved_member"
        assert out["population_total"] is None

    def test_an_area_without_rows_is_kept(self, raw):
        m = census.municipality_population(raw, MUNICIPALITY)
        b = self.bridge([("011", "012025", "child")])
        out = census.numbering_area_population(m, b, MUNICIPALITY, raw, areas=["011", "099"])
        assert out.filter(pl.col("numbering_area_code") == "099")["value_status"].item() ==             "no_municipality"

    def test_a_member_without_a_count_leaves_the_area_null(self, raw):
        m = census.municipality_population(raw, MUNICIPALITY)
        b = self.bridge([("013", "014036", "child"), ("013", "016969", "child")])
        out = census.numbering_area_population(m, b, MUNICIPALITY, raw).row(0, named=True)
        assert out["population_total"] is None
        assert out["value_status"] == "member_not_enumerated"


# ---------------------------------------------------------------- 第2-7表


def t07_rows(regions: list[tuple[str, str, str, list[int]]]) -> list[list]:
    """regions: (地域識別コード, 2025年コード, 地域名, [総数, 21区分, 不詳])."""
    labels = ["00_総数", *census.AGE_CLASSES]
    head = [None] * 8
    out = [
        ["【原数値】令和７年国勢調査　人口等基本集計"],
        [],
        [*head, "表章項目", *(["人口"] * len(labels)), "平均年齢", "年齢中位数"],
        [*head, "項目名", *labels],
        [*head, "階層レベル"],
        [*head, "表章単位"],
        [],
        [],
        [*head, "項目名", *labels],
        [],
        [],
        ["国籍総数か日本人", "男女", *census.REGION_HEADERS, " "],
    ]
    for level, code, name, values in regions:
        for nat in ("0_国籍総数", "1_うち日本人"):
            for sex in ("0_総数", "1_男", "2_女"):
                out.append([nat, sex, level, f"{code[:2]}_x", code, "2000", f"{code[:2]}_x",
                            code, name, *values, 48.5, "-"])
    return out


def ages(total: int) -> list[int]:
    parts = [0] * 21 + [0]
    parts[5] = total - 3
    parts[21] = 3    # 年齢「不詳」
    return [total, *parts]


class TestAge:
    def test_long_form_with_unknown_age_kept(self):
        age, _summary = census.parse_t07(xlsx(t07_rows([("3", "01403", "0090_泊村", ages(100))])))
        assert age.height == 6 * 22
        unknown = age.filter(pl.col("age_class_raw") == "22_年齢「不詳」")
        assert set(unknown["population"]) == {3}

    def test_classes_must_add_up_to_the_total(self):
        bad = ages(100)
        bad[1] += 1
        with pytest.raises(census.CensusLayoutError):
            census.parse_t07(xlsx(t07_rows([("3", "01403", "0090_泊村", bad)])))

    def test_only_current_municipalities_get_lg_code(self):
        age, _summary = census.parse_t07(xlsx(t07_rows([
            ("3", "01403", "0090_泊村", ages(100)),
            ("9", "01202", "0015_（旧：函館市）", ages(250)),
        ])))
        out = census.age_population(age, MUNICIPALITY)
        assert set(out.filter(pl.col("area_level_raw") == "9")["lg_code"]) == {None}
        assert set(out.filter(pl.col("area_level_raw") == "3")["lg_code"]) == {"014036"}

    def test_mean_and_median_age_are_carried_as_published(self):
        """5歳階級から再計算できない公式の値なので、捨てずに別の表に持つ."""
        _age, summary = census.parse_t07(xlsx(t07_rows([("3", "01403", "0090_泊村", ages(100))])))
        out = census.age_summary(summary, MUNICIPALITY)
        assert out.height == 6
        assert set(out["mean_age"]) == {48.5}
        assert set(out["median_age"]) == {None}     # 「-」は率では NULL
        assert set(out["median_age_raw"]) == {"-"}

    def test_dropped_columns_are_still_fingerprinted(self):
        _start, labels, _cols = census.layout_t07(
            census.read_xlsx_rows(xlsx(t07_rows([("3", "01403", "0090_泊村", ages(100))])))
        )
        assert "平均年齢" in labels and "年齢中位数" in labels

    def test_age_total_must_match_table_1(self, raw):
        age, _summary = census.parse_t07(xlsx(t07_rows([("3", "01403", "0090_泊村", ages(99))])))
        problems = census.consistency_problems(raw, age)
        assert any("01403" in p and "第2-7表" in p for p in problems)


class TestSqlite:
    def test_constraints_reject_a_value_without_its_status(self, raw):
        m = census.municipality_population(raw, MUNICIPALITY).with_columns(
            pl.lit("snap").alias("source_snapshot_id")
        )
        conn = sqlite3.connect(":memory:")
        _write_table(conn, "census_population_municipality", m, present={"x"})
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE census_population_municipality SET value_status = 'published' "
                "WHERE lg_code = '016969'"
            )
