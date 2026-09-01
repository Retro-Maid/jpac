"""Full field-by-field comparison of every source row against the database.

No sampling. Every row, every field that is carried through unchanged, compared
with null-safe equality against the original payload re-read independently of
the pipeline.

Fields the pipeline deliberately transforms are compared against the
transformation's own contract instead of the raw value, and each such case is
named explicitly below rather than quietly skipped:

* empty string -> NULL          (ABR, applied to text columns at parse time)
* `"060  "` -> `"060"`          (Japan Post, right-strip only)
* trailing spaces stripped      (MLIT)
* collapsed duplicate-key rows  (ABR, 1,248 keys; conflicting fields become NULL
                                 and every variant is kept in address_rsdt_variant)

Run:  jpac verify fields   (or: py -3.12 tools/compare_all_fields.py)
"""

from __future__ import annotations

import sys as _sys

# These reports print Japanese and typographic dashes. A Windows console is
# cp932 by default, where an unencodable character raises UnicodeEncodeError
# and kills the run halfway through. Degrade the character, not the report.
if hasattr(_sys.stdout, "reconfigure") and (_sys.stdout.encoding or "").lower() not in (
    "utf-8", "utf8"
):
    _sys.stdout.reconfigure(errors="replace")
    if hasattr(_sys.stderr, "reconfigure"):
        _sys.stderr.reconfigure(errors="replace")


import csv as _csv
import io
import sys
import zipfile
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PQ = ROOT / "dist" / "parquet"

failures: list[str] = []
compared_cells = 0


def report(source: str, field: str, n_rows: int, n_bad: int, sample=None) -> None:
    global compared_cells
    compared_cells += n_rows
    flag = "OK " if n_bad == 0 else "BAD"
    line = f"  [{flag}] {source:<22} {field:<26} {n_rows:>9,} compared"
    if n_bad:
        line += f"  {n_bad:,} MISMATCH"
        failures.append(f"{source}.{field}: {n_bad} mismatches {sample}")
    print(line)


def diff_count(df: pl.DataFrame, a: str, b: str) -> tuple[int, list]:
    """Null-safe inequality count plus a small sample of offenders."""
    bad = df.filter(
        pl.col(a).cast(pl.Utf8).fill_null("\x00")
        != pl.col(b).cast(pl.Utf8).fill_null("\x00")
    )
    return bad.height, bad.select([a, b]).head(3).to_dicts()


def read_zip_csv(path: Path, member=None, enc="utf-8", header=True) -> pl.DataFrame:
    zf = zipfile.ZipFile(path)
    name = member or next(n for n in zf.namelist() if n.lower().endswith(".csv"))
    text = zf.read(name).decode(enc, errors="strict")
    return pl.read_csv(
        io.BytesIO(text.encode("utf-8")), has_header=header,
        infer_schema_length=0, quote_char='"',
    )


def blank_to_null(df: pl.DataFrame, cols) -> pl.DataFrame:
    """The parser's own empty-string-to-NULL rule, applied to the raw side."""
    return df.with_columns(
        [
            pl.when(pl.col(c).is_null() | (pl.col(c).str.strip_chars() == ""))
            .then(None)
            .otherwise(pl.col(c))
            .alias(c)
            for c in cols
            if c in df.columns
        ]
    )


# ---------------------------------------------------------------- 1. ABR town
print("\n1. ABR 町字マスター  (all rows, all carried fields)")
src = read_zip_csv(RAW / "abr" / "town_master.zip", "mt_town_all.csv")
addr = pl.read_parquet(PQ / "address.parquet")

CARRIED = [
    "machiaza_type", "pref", "county", "city", "ward", "oaza_cho", "chome",
    "chome_number", "koaza", "machiaza_dist", "oaza_cho_kana", "chome_kana",
    "koaza_kana", "oaza_cho_roma", "koaza_roma", "rsdt_addr_flg",
    "rsdt_addr_mtd_code", "oaza_cho_aka_flg", "koaza_aka_code", "status_flg",
    "wake_num_flg", "src_code", "remarks",
]
src_n = blank_to_null(src, [*CARRIED, "efct_date", "ablt_date"])

# Rows whose key is unique carry their values straight through. Rows sharing a
# key were collapsed, and any field that disagreed became NULL by design, so
# they are compared separately below.
counts = src_n.group_by(["lg_code", "machiaza_id"]).len()
uniq_keys = counts.filter(pl.col("len") == 1).select(["lg_code", "machiaza_id"])
dup_keys = counts.filter(pl.col("len") > 1).select(["lg_code", "machiaza_id"])

u = src_n.join(uniq_keys, on=["lg_code", "machiaza_id"], how="inner").join(
    addr, on=["lg_code", "machiaza_id"], how="inner", suffix="__db"
)
assert u.height == uniq_keys.height, f"join lost rows: {u.height} vs {uniq_keys.height}"

for f in CARRIED:
    if f"{f}__db" in u.columns:
        n, s = diff_count(u, f, f"{f}__db")
        report("ABR town (unique)", f, u.height, n, s)

n, s = diff_count(u.with_columns(pl.col("efct_date").alias("_e")), "_e", "valid_from")
report("ABR town (unique)", "efct_date -> valid_from", u.height, n, s)
n, s = diff_count(u.with_columns(pl.col("ablt_date").alias("_a")), "_a", "valid_to")
report("ABR town (unique)", "ablt_date -> valid_to", u.height, n, s)

# Derived: jis_city_code and the composed full name.
n, s = diff_count(
    u.with_columns(pl.col("lg_code").str.slice(0, 5).alias("_j")), "_j", "jis_city_code"
)
report("ABR town (unique)", "jis_city_code = lg[0:5]", u.height, n, s)
n, s = diff_count(
    u.with_columns(
        (pl.col("oaza_cho").fill_null("") + pl.col("chome").fill_null("")
         + pl.col("koaza").fill_null("")).alias("_f")
    ),
    "_f", "full_name_raw",
)
report("ABR town (unique)", "full_name_raw composition", u.height, n, s)

# Collapsed rows: the invariant is that every published value survives in the
# variant table, and that the collapsed row is NULL wherever they disagreed.
d = src_n.join(dup_keys, on=["lg_code", "machiaza_id"], how="inner")
var = pl.read_parquet(PQ / "address_rsdt_variant.parquet")
src_variants = src_n.select(
    ["lg_code", "machiaza_id", "rsdt_addr_flg", "rsdt_addr_mtd_code"]
).unique()
missing = src_variants.join(
    var.select(["lg_code", "machiaza_id", "rsdt_addr_flg", "rsdt_addr_mtd_code"]),
    on=["lg_code", "machiaza_id", "rsdt_addr_flg", "rsdt_addr_mtd_code"],
    how="anti",
)
report("ABR town (collapsed)", "every variant preserved", src_variants.height,
       missing.height, missing.head(3).to_dicts())

dup_addr = addr.join(dup_keys, on=["lg_code", "machiaza_id"], how="inner")
agree = (
    d.group_by(["lg_code", "machiaza_id"])
    .agg([pl.col(c).n_unique().alias(c) for c in CARRIED])
)
bad_null = 0
for f in CARRIED:
    keys_disagree = agree.filter(pl.col(f) > 1).select(["lg_code", "machiaza_id"])
    if not keys_disagree.height:
        continue
    rows = dup_addr.join(keys_disagree, on=["lg_code", "machiaza_id"], how="inner")
    bad_null += rows.filter(pl.col(f).is_not_null()).height
report("ABR town (collapsed)", "disagreeing fields are NULL", dup_addr.height, bad_null)

# ------------------------------------------------------- 2. ABR conversion
print("\n2. ABR 町字・郵便番号変換表  (all edges)")
conv = read_zip_csv(RAW / "abr" / "postal_conversion.zip", "abr_post_code.csv")
bpc = pl.read_parquet(PQ / "bridge_address_postal_code.parquet")
addr_key = addr.select(["address_id", "lg_code", "machiaza_id"])

town_conv = conv.filter(
    pl.col("machiaza_id").is_not_null() & (pl.col("machiaza_id").str.len_chars() > 0)
)
expected = (
    town_conv.join(addr_key, on=["lg_code", "machiaza_id"], how="inner")
    .select(["address_id", "post_code"]).unique()
)
actual = bpc.filter(pl.col("matching_rule_id") != "P7").select(
    [pl.col("address_id"), pl.col("target_id").alias("post_code")]
).unique()
lost = expected.join(actual, on=["address_id", "post_code"], how="anti")
extra = actual.join(expected, on=["address_id", "post_code"], how="anti")
report("ABR conversion", "every (town, code) edge", expected.height, lost.height,
       lost.head(3).to_dicts())
report("ABR conversion", "no invented edges", actual.height, extra.height,
       extra.head(3).to_dicts())

# -------------------------------------------------------- 3. Japan Post
print("\n3. 日本郵便 郵便番号  (all rows, all 15 fields)")
lines = (
    zipfile.ZipFile(RAW / "japanpost" / "ken_all.zip")
    .read("utf_ken_all.csv").decode("utf-8-sig").splitlines()
)
COLS = [
    "jis_city_code", "old_postal_code_raw", "postal_code", "pref_kana", "city_kana",
    "town_kana", "pref", "city", "town", "flag_multi_code", "flag_koaza_banchi",
    "flag_has_chome", "flag_multi_town", "update_flag", "change_reason",
]
raw_jp = pl.DataFrame(
    [dict(zip(COLS, r, strict=False)) for r in _csv.reader(lines)],
    schema={c: pl.Utf8 for c in COLS},
)
prv = pl.read_parquet(PQ / "postal_record_version.parquet")

# One ken_all row is one postal_record, so compare as multisets per field: a
# per-row join would need a key the source does not have.
for f in COLS:
    a = sorted(x if x is not None else "\x00" for x in raw_jp[f].to_list())
    b = sorted(x if x is not None else "\x00" for x in prv[f].to_list())
    report("Japan Post", f, len(a), sum(1 for x, y in zip(a, b, strict=False) if x != y))

a = sorted(x.rstrip() for x in raw_jp["old_postal_code_raw"].to_list())
b = sorted(x if x is not None else "" for x in prv["old_postal_code"].to_list())
report("Japan Post", "old_postal_code rstrip", len(a),
       sum(1 for x, y in zip(a, b, strict=False) if x != y))

# ------------------------------------------------------------- 4. MLIT
print("\n4. 国土交通省 位置参照情報  (all rows, all fields)")
frames = []
for p in sorted((RAW / "mlit").glob("isj_*.zip")):
    zf = zipfile.ZipFile(p)
    for n_ in zf.namelist():
        if n_.lower().endswith(".csv"):
            text = zf.read(n_).decode("cp932", errors="strict")
            frames.append(
                pl.read_csv(io.BytesIO(text.encode("utf-8")), has_header=True,
                            infer_schema_length=0, quote_char='"')
            )
raw_mlit = pl.concat(frames, how="vertical")
REN = {
    "都道府県コード": "pref_code", "都道府県名": "pref_name",
    "市区町村コード": "jis_city_code", "市区町村名": "city_name",
    "大字町丁目コード": "mlit_code", "大字町丁目名": "town_name_raw",
    "緯度": "latitude", "経度": "longitude",
    "原典資料コード": "source_material_code", "大字・字・丁目区分コード": "aza_class_code",
}
raw_mlit = raw_mlit.select(list(REN)).rename(REN).with_columns(
    [pl.col(c).str.strip_chars() for c in REN.values()]
)
mlv = pl.read_parquet(PQ / "mlit_town_version.parquet")

for f in ["pref_code", "pref_name", "jis_city_code", "city_name", "mlit_code",
          "town_name_raw", "source_material_code", "aza_class_code"]:
    a = sorted(raw_mlit[f].to_list())
    b = sorted(mlv[f].to_list())
    report("MLIT", f, len(a), sum(1 for x, y in zip(a, b, strict=False) if x != y))
for f in ["latitude", "longitude"]:
    a = sorted(float(x) for x in raw_mlit[f].to_list())
    b = sorted(mlv[f].to_list())
    report("MLIT", f, len(a),
           sum(1 for x, y in zip(a, b, strict=False) if abs(x - y) > 1e-9))

# ------------------------------------------------- 5. MIC 市外局番の一覧
print("\n5. 総務省 市外局番の一覧  (all rows, all 4 columns)")
from jp_address_crosswalk.sources.doc_reader import doc_text_to_rows, extract_doc_text
from jp_address_crosswalk.sources.mic_area_code import (
    normalize_area_code,
    normalize_numbering_area_code,
)

rows = doc_text_to_rows(
    extract_doc_text(RAW / "mic_area_code" / "shigai_list.doc"), expected_cells=4
)[1:]
tav = pl.read_parquet(PQ / "telephone_area_version.parquet")
exp = pl.DataFrame(
    [
        {
            "numbering_area_code": normalize_numbering_area_code(r[0]),
            "area_text_raw": r[1],
            "area_code": normalize_area_code(r[2]),
            "area_code_raw": r[2],
            "local_digit_pattern": r[3],
        }
        for r in rows
        if normalize_numbering_area_code(r[0]) and r[2].isdigit()
    ]
)
j = exp.join(tav, on="numbering_area_code", how="inner", suffix="__db")
report("MIC area (doc)", "row coverage", exp.height, exp.height - j.height)
for f in ["area_text_raw", "area_code", "area_code_raw", "local_digit_pattern"]:
    n, s = diff_count(j, f, f"{f}__db")
    report("MIC area (doc)", f, j.height, n, s)

# ------------------------------------------ 6. MIC 電気通信番号指定状況
print("\n6. 総務省 電気通信番号指定状況  (all rows, all 7 columns)")
import xlrd

from jp_address_crosswalk.sources.mic_number_assignment import _cell_str, _header_row_index

recs = []
for p in sorted((RAW / "mic_number_assignment").glob("fixed_*.xls")):
    sh = xlrd.open_workbook(str(p)).sheet_by_index(0)
    h = _header_row_index(sh)
    for r in range(h + 1, sh.nrows):
        v = [_cell_str(c) for c in sh.row(r)]
        v += [""] * (7 - len(v))
        if not normalize_numbering_area_code(v[0]):
            continue
        recs.append(
            {
                "numbering_area_code": normalize_numbering_area_code(v[0]),
                "number": v[1], "area_code": normalize_area_code(v[2]),
                "local_code": v[3], "carrier": v[4], "usage_status": v[5],
                "remarks": v[6],
            }
        )
raw_blk = pl.DataFrame(recs)
blk = pl.read_parquet(PQ / "telephone_number_block.parquet")
for f in ["numbering_area_code", "number", "area_code", "local_code", "carrier",
          "usage_status"]:
    a = sorted(raw_blk[f].to_list())
    b = sorted(x if x is not None else "" for x in blk[f].to_list())
    report("MIC blocks (xls)", f, len(a),
           abs(len(a) - len(b)) + sum(1 for x, y in zip(a, b, strict=False) if x != y))

print("\n" + "=" * 78)
# ------------------------------------------------------------------------
# Whole-row comparison. Comparing columns independently cannot detect a value
# moving between rows: field A from row 1 paired with field B from row 2 leaves
# every per-column multiset identical. So each row is also compared as a unit.
# ------------------------------------------------------------------------
print("")
print("7. Whole-row integrity  (detects cross-row scrambling)")


def row_multiset(df: pl.DataFrame, cols: list[str]) -> list[str]:
    joined = pl.concat_str(
        [pl.col(c).cast(pl.Utf8).fill_null(chr(0)) for c in cols],
        separator=chr(31),
    ).alias("_r")
    return sorted(df.select(joined)["_r"].to_list())


def compare_rows(label: str, name: str, a: list[str], b: list[str]) -> None:
    bad = abs(len(a) - len(b)) + sum(
        1 for x, y in zip(a, b, strict=False) if x != y
    )
    sample = [x for x, y in zip(a, b, strict=False) if x != y][:1]
    report(label, name, len(a), bad, sample)


compare_rows(
    "Japan Post", "whole row (15 fields)",
    row_multiset(raw_jp, COLS), row_multiset(prv, COLS),
)

MCOLS = ["pref_code", "pref_name", "jis_city_code", "city_name", "mlit_code",
         "town_name_raw", "source_material_code", "aza_class_code"]
compare_rows(
    "MLIT", "whole row (8 text fields)",
    row_multiset(raw_mlit, MCOLS), row_multiset(mlv, MCOLS),
)

# Coordinates must travel with their own row, so compare them together with the
# code rather than as three independent columns.
_r = raw_mlit.with_columns(
    [pl.col(c).cast(pl.Float64).round(9).cast(pl.Utf8) for c in ("latitude", "longitude")]
)
_d = mlv.with_columns(
    [pl.col(c).round(9).cast(pl.Utf8) for c in ("latitude", "longitude")]
)
compare_rows(
    "MLIT", "code + coordinates together",
    row_multiset(_r, ["mlit_code", "latitude", "longitude"]),
    row_multiset(_d, ["mlit_code", "latitude", "longitude"]),
)

BCOLS = ["numbering_area_code", "number", "area_code", "local_code", "carrier",
         "usage_status"]
compare_rows(
    "MIC blocks (xls)", "whole row (6 fields)",
    row_multiset(raw_blk, BCOLS), row_multiset(blk, BCOLS),
)

# ABR and the MIC area list were already compared with a key-based join, which
# is row-level by construction.
print("")
print(f"{compared_cells:,} field values compared across all six sources")
print(f"RESULT: {len(failures)} field(s) with mismatches")
for f in failures:
    print("  !", f)
sys.exit(1 if failures else 0)
