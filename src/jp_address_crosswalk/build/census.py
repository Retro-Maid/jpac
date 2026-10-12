"""国勢調査 令和7年 人口等基本集計 → 市区町村・番号区画の人口（docs/POPULATION_PREFLIGHT.md）.

発行元の Excel（第1-1表・第2-7表）を読み、公表された件数だけを持つ。

守っていること:

- **按分しない。** 番号区画の人口は、区画に属する市区町村がすべて丸ごと含まれる
  （``relation_type = 'child'``）ときだけ、その和として出す。そうでなければ NULL と理由。
- **面積（参考）と人口密度は読まない。** 表の注記5のとおり面積は国土地理院の面積調で、
  人口密度はそこから算出される（docs/POLICY.md §3.3）。raw 層にも持たない。
- **区と親（政令市）は名称でも行の順でもなくコードで対にする。** 名称で探すと北海道の
  「泊村」が2つあるため行が重複する（preflight §3.3）。対にした結果は、区の合計 = 親
  であることを品質ゲートで確かめる。
- **「-」は凡例どおり読む。** ユーザーズガイド §3(1) で「該当数値がないもの」。件数では
  0、率では NULL（2026-10-12 の決定）。元の文字列は ``*_raw`` に残る。
- **原文は触らない。** セルの文字列は ``*_raw`` 列に発行元の XML のまま持つ。

xlsx は ZIP と XML なので標準ライブラリで読む。依存は足さない（preflight §7）。
"""

from __future__ import annotations

import io
import re
import zipfile
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

import polars as pl

CENSUS_YEAR = 2025
# 調査の期日。発行元が明記している実世界の基準日（docs/POLICY.md §7）。
VALID_FROM = "2025-10-01"

_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

# ---------------------------------------------------------------- xlsx


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _text(el: ET.Element | None) -> str:
    """``<si>`` / ``<is>`` の文字列。書式つきの断片（``<r><t>``）も順に連結する."""
    if el is None:
        return ""
    return "".join(t.text or "" for t in el.iter(f"{{{_NS['m']}}}t"))


def read_xlsx_rows(data: bytes) -> list[list[str | None]]:
    """最初のシートの全行を、セルの文字列のリストで返す.

    数値セルは ``<v>`` の文字列のまま返す（型を推測しない）。空のセルは ``None``。
    """
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        first = wb.find("m:sheets/m:sheet", _NS)
        if first is None:
            raise ValueError("xlsx にシートが無い")
        rid = first.get(f"{{{_NS['r']}}}id")
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = next(
            r.get("Target") for r in rels.findall("rel:Relationship", _NS)
            if r.get("Id") == rid
        )
        sheet_path = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = [_text(si) for si in sst.findall("m:si", _NS)]
        sheet = ET.fromstring(z.read(sheet_path))

    rows: list[list[str | None]] = []
    for row in sheet.iter(f"{{{_NS['m']}}}row"):
        r_index = int(row.get("r")) - 1
        while len(rows) < r_index:
            rows.append([])
        cells: list[str | None] = []
        for c in row.findall("m:c", _NS):
            i = _col_index(c.get("r"))
            while len(cells) < i:
                cells.append(None)
            kind = c.get("t")
            v = c.find("m:v", _NS)
            if kind == "s":
                value = shared[int(v.text)] if v is not None else None
            elif kind == "inlineStr":
                value = _text(c.find("m:is", _NS))
            else:
                value = v.text if v is not None else None
            cells.append(value)
        rows.append(cells)
    return rows


def _cell(row: list[str | None], i: int) -> str | None:
    v = row[i] if i < len(row) else None
    return None if v is None or v == "" else v


# ---------------------------------------------------------------- 表の形

# 第1-1表。データ列の位置 → (表頭の表章項目, 項目名, jpac の列名, 種類)。
# 種類: count は件数（「-」は 0）、rate は率（「-」は NULL）、excluded は読まない列。
T01_VALUE_COLUMNS: list[tuple[int, str, str | None, str, str]] = [
    (7, "人口", "0_総数", "population_total", "count"),
    (8, "人口", "1_男", "population_male", "count"),
    (9, "人口", "2_女", "population_female", "count"),
    (10, "2020年（令和2年）の人口（組替）", None, "population_2020_rebased", "count"),
    (11, "5年間の人口増減数", None, "population_change_5y", "count"),
    (12, "5年間の人口増減率", None, "population_change_rate_5y", "rate"),
    (13, "人口性比", None, "sex_ratio", "rate"),
    (14, "面積（参考）", None, None, "excluded"),
    (15, "人口密度", None, None, "excluded"),
    (16, "世帯数", "0_総数", "households_total", "count"),
    (17, "世帯数", "1_一般世帯", "households_general", "count"),
    (18, "世帯数", "2_施設等の世帯", "households_institutional", "count"),
    (19, "2020年（令和2年）の世帯数（組替）", None, "households_2020_rebased", "count"),
    (20, "5年間の世帯増減数", None, "households_change_5y", "count"),
    (21, "5年間の世帯増減率", None, "households_change_rate_5y", "rate"),
    (22, "世帯人員", "0_総数", "household_members_total", "count"),
    (23, "世帯人員", "1_一般世帯", "household_members_general", "count"),
    (24, "世帯人員", "2_施設等の世帯", "household_members_institutional", "count"),
]

# 地域を表す先頭7列（第1-1表）。第2-7表では先頭に国籍・男女の2列が足される。
REGION_HEADERS = [
    "地域識別コード", "2000年_都道府県", "2000年_地域コード", "2000年地域",
    "2025年_都道府県", "2025年_地域コード", "地域名",
]
REGION_COLUMNS = [
    "area_level_raw", "pref_2000_raw", "area_code_2000_raw", "year_2000_raw",
    "pref_2025_raw", "area_code_2025_raw", "area_name_raw",
]

# 地域識別コード（ユーザーズガイド §3(4)）。
AREA_LEVELS = {
    "a": "全国・都道府県",
    "1": "東京都特別区部・政令指定都市",
    "0": "東京都23区・政令指定都市の区",
    "2": "政令指定都市以外の市",
    "3": "町村",
    "9": "2000年（平成12年）現在の市区町村",
}
# 現行の市区町村として lg_code に結んでよい階層。
MUNICIPAL_LEVELS = ("1", "0", "2", "3")

# 第2-7表の年齢区分（21区分 + 不詳）。再掲と構成比は区分から作れるので持たない。
AGE_CLASSES = [
    "01_0～4歳", "02_5～9歳", "03_10～14歳", "04_15～19歳", "05_20～24歳",
    "06_25～29歳", "07_30～34歳", "08_35～39歳", "09_40～44歳", "10_45～49歳",
    "11_50～54歳", "12_55～59歳", "13_60～64歳", "14_65～69歳", "15_70～74歳",
    "16_75～79歳", "17_80～84歳", "18_85～89歳", "19_90～94歳", "20_95～99歳",
    "21_100歳以上", "22_年齢「不詳」",
]


# 第2-7表の、区分から作れない公式の値。平均年齢と年齢中位数は5歳階級からは再計算
# できない（各歳と不詳の扱いが要る）ので、縦持ちとは別の表に持つ。
SUMMARY_COLUMNS = {"平均年齢": "mean_age", "年齢中位数": "median_age"}

# 国勢調査の対象外として、結果表に載らないと分かっている市区町村（北方領土の6村）。
# これ以外の市区町村が表に無ければ、推測で埋めずに品質ゲートで止める。
EXPECTED_NOT_ENUMERATED = frozenset({"01695", "01696", "01697", "01698", "01699", "01700"})


class CensusLayoutError(ValueError):
    """発行元の表の形が、読めると確かめた形と違う。推測で読まずに止める."""


def _find_row(rows: list[list[str | None]], col: int, value: str) -> int:
    for i, r in enumerate(rows):
        if _cell(r, col) == value:
            return i
    raise CensusLayoutError(f"{value!r} の行が見つからない")


def table_notes(rows: list[list[str | None]]) -> list[str]:
    """表題と注記（表頭より上の行の1列目）。注記は面積の出どころなど意味を持つので、
    期待スキーマに含め、変われば止める（preflight §2.1）."""
    head = next(i for i, r in enumerate(rows) if "表章項目" in r)
    return [_cell(r, 0) for r in rows[:head] if _cell(r, 0)]


def layout_t01(rows: list[list[str | None]]) -> tuple[int, list[str]]:
    """第1-1表の表頭を確かめ、データの開始行と列名（期待スキーマ用）を返す."""
    item_row = _find_row(rows, 6, "表章項目")
    name_row = _find_row(rows, 6, "項目名")
    header_row = _find_row(rows, 0, "地域識別コード")
    got = [_cell(rows[header_row], i) for i in range(len(REGION_HEADERS))]
    if got != REGION_HEADERS:
        raise CensusLayoutError(f"第1-1表の地域の列が違う: {got}")
    labels = list(REGION_HEADERS)
    for i, item, name, _col, _kind in T01_VALUE_COLUMNS:
        got_item, got_name = _cell(rows[item_row], i), _cell(rows[name_row], i)
        if got_item != item or got_name != name:
            raise CensusLayoutError(
                f"第1-1表の {i} 列目は {item}/{name} のはずが {got_item}/{got_name}"
            )
        labels.append(f"{item}/{name}" if name else item)
    return header_row + 1, labels


def layout_t07(rows: list[list[str | None]]) -> tuple[int, list[str], dict[str, int]]:
    """第2-7表の表頭を確かめ、開始行・列名・読む列の位置を返す.

    列名は読まない列（再掲・構成比）も含めて全部返す。期待スキーマの指紋に入れ、
    発行元が列を足したり並べ替えたりすれば止めるため。
    """
    item_row = _find_row(rows, 8, "表章項目")
    name_row = _find_row(rows, 8, "項目名")
    header_row = _find_row(rows, 2, "地域識別コード")
    got = [_cell(rows[header_row], i) for i in range(2 + len(REGION_HEADERS))]
    if got != ["国籍総数か日本人", "男女", *REGION_HEADERS]:
        raise CensusLayoutError(f"第2-7表の地域の列が違う: {got}")
    labels = ["国籍総数か日本人", "男女", *REGION_HEADERS]
    cols: dict[str, int] = {}
    for i in range(9, max(len(rows[item_row]), len(rows[name_row]))):
        item, name = _cell(rows[item_row], i), _cell(rows[name_row], i)
        if item is None:
            continue
        labels.append(f"{item}/{name}" if name else item)
        if item == "人口" and name in ("00_総数", *AGE_CLASSES):
            cols[name] = i
        elif item in SUMMARY_COLUMNS and name is None:
            cols[item] = i
    missing = [a for a in ["00_総数", *AGE_CLASSES, *SUMMARY_COLUMNS] if a not in cols]
    if missing:
        raise CensusLayoutError(f"第2-7表に読む列が無い: {missing}")
    return header_row + 1, labels, cols


# ---------------------------------------------------------------- 値


def parse_value(raw: str | None, kind: str) -> int | float | None:
    """セルの文字列を値にする。読めないものは推測せずに止める."""
    if raw is None:
        return None
    if raw == "-":
        return 0 if kind == "count" else None
    try:
        d = Decimal(raw)
    except InvalidOperation as e:
        raise CensusLayoutError(f"数値として読めないセル: {raw!r}") from e
    if kind == "count":
        if d != d.to_integral_value():
            raise CensusLayoutError(f"件数の列に整数でない値: {raw!r}")
        return int(d)
    return float(d)


def _region(row: list[str | None], offset: int) -> dict[str, str | None]:
    return {c: _cell(row, offset + i) for i, c in enumerate(REGION_COLUMNS)}


def _assert_blank(row: list[str | None], ordinal: int) -> None:
    """地域識別コードの無い行は、空行でなければ止める。黙って飛ばすと行が消える."""
    if any(_cell(row, i) for i in range(len(row))):
        raise CensusLayoutError(f"{ordinal} 行目: 地域識別コードが無いのに値がある")


def region_key(r: dict) -> str:
    """地域の行を一意に指す鍵。階層・2000年の地域コード・2025年の地域コードの組."""
    return f"{r['area_level_raw']}:{r['area_code_2000_raw'] or ''}:{r['area_code_2025_raw']}"


def parse_t01(data: bytes) -> pl.DataFrame:
    """第1-1表 → ``census_population_raw``（1行 = 1行、全セルを文字列で）."""
    rows = read_xlsx_rows(data)
    start, _labels = layout_t01(rows)
    out = []
    for ordinal, row in enumerate(rows[start:], start=start + 1):
        if not _cell(row, 0):
            _assert_blank(row, ordinal)
            continue
        rec = {"table_id": "b01_01", "row_ordinal": ordinal, **_region(row, 0)}
        if rec["area_level_raw"] not in AREA_LEVELS:
            raise CensusLayoutError(f"未知の地域識別コード: {rec['area_level_raw']!r}")
        for i, _item, _name, col, kind in T01_VALUE_COLUMNS:
            if kind != "excluded":
                rec[f"{col}_raw"] = _cell(row, i)
        rec["region_key"] = region_key(rec)
        out.append(rec)
    schema = {
        "table_id": pl.Utf8, "row_ordinal": pl.Int64,
        **dict.fromkeys(REGION_COLUMNS, pl.Utf8),
        **{f"{c}_raw": pl.Utf8 for _i, _t, _n, c, k in T01_VALUE_COLUMNS if k != "excluded"},
        "region_key": pl.Utf8,
    }
    df = pl.DataFrame(out, schema=schema)
    if df["region_key"].n_unique() != df.height:
        raise CensusLayoutError("第1-1表で地域の鍵が重複している")
    return df


def parse_t07(data: bytes) -> tuple[pl.DataFrame, pl.DataFrame]:
    """第2-7表 → 縦持ちの年齢別人口と、平均年齢・年齢中位数（全地域。lg_code は後で結ぶ）."""
    rows = read_xlsx_rows(data)
    start, _labels, cols = layout_t07(rows)
    out, summary = [], []
    for ordinal, row in enumerate(rows[start:], start=start + 1):
        if not _cell(row, 2):
            _assert_blank(row, ordinal)
            continue
        reg = _region(row, 2)
        nationality, sex = _cell(row, 0), _cell(row, 1)
        key = {"region_key": region_key(reg), **reg,
               "nationality_raw": nationality, "sex_raw": sex}
        parts = []
        for age in AGE_CLASSES:
            raw = _cell(row, cols[age])
            parts.append(parse_value(raw, "count"))
            out.append({**key, "age_class_raw": age, "population_raw": raw,
                        "population": parts[-1]})
        total = parse_value(_cell(row, cols["00_総数"]), "count")
        if None not in parts and total != sum(parts):
            raise CensusLayoutError(
                f"第2-7表 {reg['area_name_raw']} {nationality} {sex}: "
                f"年齢区分の和 {sum(parts)} ≠ 総数 {total}"
            )
        rec = dict(key)
        for item, col in SUMMARY_COLUMNS.items():
            raw = _cell(row, cols[item])
            rec[col] = parse_value(raw, "rate")
            rec[f"{col}_raw"] = raw
        summary.append(rec)
    key_schema = {
        "region_key": pl.Utf8, **dict.fromkeys(REGION_COLUMNS, pl.Utf8),
        "nationality_raw": pl.Utf8, "sex_raw": pl.Utf8,
    }
    summary_schema = dict(key_schema)
    for col in SUMMARY_COLUMNS.values():
        summary_schema |= {col: pl.Float64, f"{col}_raw": pl.Utf8}
    return (
        pl.DataFrame(out, schema={**key_schema, "age_class_raw": pl.Utf8,
                                  "population_raw": pl.Utf8, "population": pl.Int64}),
        pl.DataFrame(summary, schema=summary_schema),
    )


# ---------------------------------------------------------------- jpac に結ぶ

COUNT_COLUMNS = [c for _i, _t, _n, c, k in T01_VALUE_COLUMNS if k == "count"]


def _typed(raw: pl.DataFrame) -> pl.DataFrame:
    return raw.with_columns(
        pl.col(f"{c}_raw").map_elements(
            lambda v: parse_value(v, "count"), return_dtype=pl.Int64
        ).alias(c)
        for c in COUNT_COLUMNS
    )


def _current_municipalities(municipality: pl.DataFrame) -> pl.DataFrame:
    cur = municipality
    if "is_current" in cur.columns:
        cur = cur.filter(pl.col("is_current").cast(pl.Boolean))
    cur = cur.select("lg_code", "jis_city_code").unique()
    if cur["jis_city_code"].n_unique() != cur.height:
        raise CensusLayoutError("現行の市区町村で jis_city_code が重複している")
    return cur


def municipality_population(
    raw: pl.DataFrame, municipality: pl.DataFrame
) -> pl.DataFrame:
    """``census_population_municipality``: 現行の市区町村 1行ずつ.

    結果表に無い市区町村も行を持ち、値を NULL にする。国勢調査の対象外と分かっている
    北方領土の6村は ``not_enumerated``、それ以外は ``missing_from_table`` で、後者は
    ``missing_from_census`` が品質ゲートを止める。
    """
    census = _typed(
        raw.filter(pl.col("area_level_raw").is_in(MUNICIPAL_LEVELS))
    ).rename({"area_code_2025_raw": "jis_city_code", "area_level_raw": "area_level"})
    joined = _current_municipalities(municipality).join(
        census.select("jis_city_code", "area_level", *COUNT_COLUMNS),
        on="jis_city_code", how="left",
    )
    return joined.select(
        pl.lit(CENSUS_YEAR).alias("census_year"),
        "lg_code",
        "area_level",
        *COUNT_COLUMNS,
        pl.when(pl.col("area_level").is_not_null()).then(pl.lit("published"))
        .when(pl.col("jis_city_code").is_in(list(EXPECTED_NOT_ENUMERATED)))
        .then(pl.lit("not_enumerated"))
        .otherwise(pl.lit("missing_from_table")).alias("value_status"),
        pl.lit(VALID_FROM).alias("valid_from"),
    ).sort("lg_code")


def missing_from_census(raw: pl.DataFrame, municipality: pl.DataFrame) -> list[str]:
    """jpac の現行市区町村のうち、結果表に無く、対象外とも分かっていないもの.

    国勢調査の期日（2025-10-01）より後に新設された市区町村はここに出て、品質ゲートを
    止める。期日の後の変更をどう扱うかは人が決める（preflight §4）。
    """
    present = set(
        raw.filter(pl.col("area_level_raw").is_in(MUNICIPAL_LEVELS))["area_code_2025_raw"]
    )
    return sorted(
        c for c in _current_municipalities(municipality)["jis_city_code"]
        if c not in present and c not in EXPECTED_NOT_ENUMERATED
    )


def unmatched_census_codes(raw: pl.DataFrame, municipality: pl.DataFrame) -> list[str]:
    """国勢調査にあって jpac の現行市区町村に無い地域コード（特別区部の集計行を除く）."""
    cur = set(_current_municipalities(municipality)["jis_city_code"])
    rows = raw.filter(pl.col("area_level_raw").is_in(MUNICIPAL_LEVELS))
    return sorted(
        f"{r['area_code_2025_raw']} {r['area_name_raw']}"
        for r in rows.iter_rows(named=True)
        if r["area_code_2025_raw"] not in cur and not _is_special_ward_total(r)
    )


def _is_special_ward_total(r: dict) -> bool:
    # 東京都特別区部（13100）は 23区の集計行で、jpac に対応する市区町村は無い。
    return r["area_level_raw"] == "1" and r["area_code_2025_raw"] == "13100"


def ward_parents(raw: pl.DataFrame) -> dict[str, str]:
    """区（階層 0）の地域コード → 親（階層 1）の地域コード. **コードだけで決める.**

    同じ都道府県の階層 1 のうち、区のコード以下で最大のもの（横浜市 14100 の区は
    14101〜14118、川崎市 14130 の区は 14131〜）。名称は使わない。正しいことは
    ``consistency_problems`` が「区の合計 = 親」で確かめる。
    """
    rows = raw.filter(pl.col("area_level_raw").is_in(["0", "1"]))
    parents: dict[str, list[str]] = {}
    for r in rows.filter(pl.col("area_level_raw") == "1").iter_rows(named=True):
        code = r["area_code_2025_raw"]
        parents.setdefault(code[:2], []).append(code)
    out = {}
    for r in rows.filter(pl.col("area_level_raw") == "0").iter_rows(named=True):
        code = r["area_code_2025_raw"]
        below = [p for p in parents.get(code[:2], []) if p < code]
        if not below:
            raise CensusLayoutError(f"区 {code} の親（政令市・特別区部）が無い")
        out[code] = max(below)
    return out


def consistency_problems(raw: pl.DataFrame, age: pl.DataFrame | None = None) -> list[str]:
    """表の内側の整合（preflight §3.2）。1件でもあれば品質ゲートを止める."""
    t = _typed(raw)
    problems: list[str] = []

    def label(r: dict) -> str:
        return f"{r['area_code_2025_raw']} {r['area_name_raw']}"

    for total, parts in [
        ("population_total", ["population_male", "population_female"]),
        ("households_total", ["households_general", "households_institutional"]),
        ("household_members_total",
         ["household_members_general", "household_members_institutional"]),
    ]:
        for r in t.iter_rows(named=True):
            if r[total] is not None and r[total] != sum(r[p] or 0 for p in parts):
                problems.append(f"{label(r)}: {total} ≠ {' + '.join(parts)}")

    cur = t.filter(pl.col("area_level_raw") != "9")
    pop = {r["area_code_2025_raw"]: r["population_total"] for r in cur.iter_rows(named=True)
           if r["area_level_raw"] != "a"}
    names = {r["area_code_2025_raw"]: r["area_name_raw"] for r in cur.iter_rows(named=True)}

    wards: dict[str, int] = {}
    for ward, parent in ward_parents(raw).items():
        wards[parent] = wards.get(parent, 0) + pop[ward]
    for parent, s in sorted(wards.items()):
        if s != pop[parent]:
            problems.append(f"{parent} {names[parent]}: 区の合計 {s} ≠ {pop[parent]}")

    pref_rows = {r["area_code_2025_raw"][:2]: r["population_total"]
                 for r in cur.filter(pl.col("area_level_raw") == "a").iter_rows(named=True)}
    national = pref_rows.pop("00", None)
    sums: dict[str, int] = {}
    for r in cur.filter(pl.col("area_level_raw").is_in(["2", "3", "0"])).iter_rows(named=True):
        sums[r["area_code_2025_raw"][:2]] = sums.get(r["area_code_2025_raw"][:2], 0) \
            + r["population_total"]
    for p, v in sorted(pref_rows.items()):
        if sums.get(p, 0) != v:
            problems.append(f"{p}000: 市区町村の合計 {sums.get(p, 0)} ≠ 都道府県 {v}")
    if national is not None and national != sum(pref_rows.values()):
        problems.append(f"00000: 都道府県の合計 {sum(pref_rows.values())} ≠ 全国 {national}")

    if age is not None:
        a = age.filter(pl.col("area_level_raw").is_in(MUNICIPAL_LEVELS)).filter(
            (pl.col("nationality_raw") == "0_国籍総数") & (pl.col("sex_raw") == "0_総数")
        ).group_by("area_code_2025_raw").agg(pl.col("population").sum())
        for r in a.iter_rows(named=True):
            if r["population"] != pop.get(r["area_code_2025_raw"]):
                problems.append(
                    f"{r['area_code_2025_raw']}: 第2-7表の年齢の和 {r['population']} ≠ "
                    f"第1-1表の人口 {pop.get(r['area_code_2025_raw'])}"
                )
    return problems


def _attach_lg_code(df: pl.DataFrame, municipality: pl.DataFrame) -> pl.DataFrame:
    cur = _current_municipalities(municipality)
    return df.join(
        cur.rename({"jis_city_code": "area_code_2025_raw"}),
        on="area_code_2025_raw", how="left",
    ).with_columns(
        # 2000年の市区町村（階層 9）と集計行（a、特別区部）は現行の市区町村ではない。
        pl.when(pl.col("area_level_raw").is_in(MUNICIPAL_LEVELS))
        .then(pl.col("lg_code")).alias("lg_code"),
        pl.lit(CENSUS_YEAR).alias("census_year"),
        pl.lit(VALID_FROM).alias("valid_from"),
    )


def age_population(age: pl.DataFrame, municipality: pl.DataFrame) -> pl.DataFrame:
    """``census_population_age``: 全地域の縦持ち。現行の市区町村の行だけ ``lg_code`` を持つ."""
    return _attach_lg_code(age, municipality).select(
        "census_year", "region_key", "lg_code", *REGION_COLUMNS,
        "nationality_raw", "sex_raw", "age_class_raw", "population", "population_raw",
        "valid_from",
    )


def age_summary(summary: pl.DataFrame, municipality: pl.DataFrame) -> pl.DataFrame:
    """``census_population_age_summary``: 平均年齢・年齢中位数（発行元の値のまま）."""
    return _attach_lg_code(summary, municipality).select(
        "census_year", "region_key", "lg_code", *REGION_COLUMNS,
        "nationality_raw", "sex_raw",
        *[c for col in SUMMARY_COLUMNS.values() for c in (col, f"{col}_raw")],
        "valid_from",
    )


NUMBERING_AREA_VALUES = (
    "population_total", "population_male", "population_female", "households_total",
)


def numbering_area_population(
    municipal: pl.DataFrame,
    bridge_tel: pl.DataFrame,
    municipality: pl.DataFrame,
    raw: pl.DataFrame,
    areas: list[str] | None = None,
) -> pl.DataFrame:
    """``census_population_numbering_area``: 番号区画ごとの人口. **按分しない.**

    区画に親（政令市）が丸ごと含まれ、その区も載るときは親の行を外す（区の合計 = 親
    なので、区の行だけで同じ範囲を表す）。残る行がすべて ``child`` で、どの市区町村にも人口が
    あるときだけ和を出す。そうでなければ NULL と理由。

    区画の構成が欠けていないことは、ブリッジの作り方に頼っている。条文を市区町村に
    解決できなければ ``build/telephone.py`` は行を落とさず T7（``unresolved``、
    ``lg_code`` NULL）の行を出すので、その区画は ``unresolved_member`` になる。
    ``areas`` を渡すと、ブリッジに1行も無い区画も ``no_municipality`` として行を持つ。
    """
    cur = _current_municipalities(municipality)
    lg_of = dict(zip(cur["jis_city_code"], cur["lg_code"], strict=True))
    parent_of = {
        lg_of[w]: lg_of[p] for w, p in ward_parents(raw).items() if w in lg_of and p in lg_of
    }
    b = bridge_tel.select("numbering_area_code", "lg_code", "relation_type")
    b = b.with_columns(
        pl.col("lg_code").replace_strict(parent_of, default=None).alias("_parent")
    )
    wards_present = b.filter(pl.col("_parent").is_not_null()).select(
        "numbering_area_code", pl.col("_parent").alias("lg_code")
    ).unique().with_columns(pl.lit(True).alias("_covered_by_wards"))
    # 外してよいのは、親が丸ごと含まれる（child）ときだけ。親が overlap なら、市の
    # どの部分が区画に入るかは区の行が言い尽くしているとは限らない（条文が区ではなく
    # 町名で切ることがある）ので、親の行を残して区画を partial のままにする。
    b = b.join(wards_present, on=["numbering_area_code", "lg_code"], how="left").filter(
        pl.col("_covered_by_wards").is_null() | (pl.col("relation_type") != "child")
    ).join(
        municipal.select("lg_code", "value_status", *NUMBERING_AREA_VALUES),
        on="lg_code", how="left",
    )
    agg = b.group_by("numbering_area_code").agg(
        pl.len().alias("municipality_count"),
        pl.col("relation_type").is_in(["unresolved", "ambiguous"]).any().alias("_unresolved"),
        (pl.col("relation_type") == "child").all().alias("_all_child"),
        (pl.col("value_status") == "published").all().alias("_all_published"),
        *[pl.col(c).sum().alias(c) for c in NUMBERING_AREA_VALUES],
    )
    if areas is not None:
        agg = pl.DataFrame(
            {"numbering_area_code": sorted(set(areas) | set(agg["numbering_area_code"]))}
        ).join(agg, on="numbering_area_code", how="left")
    status = (
        pl.when(pl.col("municipality_count").is_null()).then(pl.lit("no_municipality"))
        .when(pl.col("_unresolved")).then(pl.lit("unresolved_member"))
        .when(~pl.col("_all_child")).then(pl.lit("partial_municipality"))
        .when(~pl.col("_all_published")).then(pl.lit("member_not_enumerated"))
        .otherwise(pl.lit("complete"))
    )
    keep = status == "complete"
    return agg.select(
        pl.lit(CENSUS_YEAR).alias("census_year"),
        "numbering_area_code",
        pl.col("municipality_count").fill_null(0),
        *[pl.when(keep).then(pl.col(c)).alias(c) for c in NUMBERING_AREA_VALUES],
        status.alias("value_status"),
        pl.lit(VALID_FROM).alias("valid_from"),
    ).sort("numbering_area_code")
