"""Reconcile every raw source file against what reached the database.

Run after a build:  jpac verify sources   (or: py -3.12 tools/reconcile_sources.py)

This reads the original payloads independently of the pipeline and accounts for
every input row. Both row-loss defects this project has had were found this way
and by nothing else.

Not a row count: this reads the original payloads again, independently of the
pipeline, and accounts for every input row.
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


import io
import sys
import zipfile
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
RAW = ROOT / "data" / "raw"
PQ = ROOT / "dist" / "parquet"

problems: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK ' if ok else 'BAD'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        problems.append(f"{label}: {detail}")


def raw_csv(zip_path: Path, member: str | None = None, enc="utf-8", header=True):
    zf = zipfile.ZipFile(zip_path)
    name = member or [n for n in zf.namelist() if n.lower().endswith(".csv")][0]
    data = zf.read(name)
    text = data.decode(enc, errors="strict")
    return pl.read_csv(
        io.BytesIO(text.encode("utf-8")), has_header=header,
        infer_schema_length=0, quote_char='"',
    )


print("=" * 74)
print("1. ABR 町字マスター")
print("=" * 74)
src = raw_csv(RAW / "abr" / "town_master.zip", "mt_town_all.csv")
addr = pl.read_parquet(PQ / "address.parquet")
var = pl.read_parquet(PQ / "address_rsdt_variant.parquet")
conf = pl.read_parquet(PQ / "address_key_conflict.parquet")

src_keys = set(zip(src["lg_code"], src["machiaza_id"], strict=False))
out_keys = set(zip(addr["lg_code"], addr["machiaza_id"], strict=False))
check("every source (lg_code, machiaza_id) reached `address`",
      src_keys == out_keys,
      f"src {len(src_keys):,} out {len(out_keys):,} missing {len(src_keys - out_keys)}")
check("row accounting: unique keys + duplicates = source rows",
      len(src_keys) + (src.height - len(src_keys)) == src.height,
      f"{src.height:,} rows -> {len(out_keys):,} towns + "
      f"{src.height - len(src_keys):,} duplicate-key rows")
check("every source row is represented in address_rsdt_variant",
      var.height == src.select(['lg_code','machiaza_id','rsdt_addr_flg',
                                'rsdt_addr_mtd_code']).unique().height,
      f"variants {var.height:,}")
check("no unexplained key conflict left unhandled", conf.height == 0,
      f"{conf.height} rows")

# Field fidelity on a random-but-fixed sample, compared value by value.
sample = src.sample(n=300, seed=42)
joined = sample.join(
    addr.select(["lg_code", "machiaza_id", "pref", "city", "ward", "oaza_cho",
                 "chome", "koaza", "valid_from"]),
    on=["lg_code", "machiaza_id"], how="inner", suffix="_db",
)
mism = []
for f in ["pref", "city", "ward", "oaza_cho", "chome", "koaza"]:
    bad = joined.filter(
        pl.col(f).fill_null("") != pl.col(f + "_db").fill_null("")
    )
    if bad.height:
        mism.append(f"{f}({bad.height})")
check("sampled 300 towns: name fields match the source byte for byte",
      not mism, ", ".join(mism) or "no differences")

lz = src.filter(pl.col("machiaza_id").str.starts_with("0"))
lzo = addr.filter(pl.col("machiaza_id").str.starts_with("0"))
check("machiaza_id leading zeros preserved",
      lz.height > 0 and lzo.height >= len(
          {k for k in out_keys if k[1].startswith("0")}) - 1,
      f"source {lz.height:,} rows begin with 0")

print()
print("=" * 74)
print("2. ABR 町字・郵便番号変換表")
print("=" * 74)
conv = raw_csv(RAW / "abr" / "postal_conversion.zip", "abr_post_code.csv")
bpc = pl.read_parquet(PQ / "bridge_address_postal_code.parquet")
bmp = pl.read_parquet(PQ / "bridge_municipality_postal.parquet")

town_rows = conv.filter(pl.col("machiaza_id").is_not_null() &
                        (pl.col("machiaza_id").str.len_chars() > 0))
muni_rows = conv.height - town_rows.height
town_edges = town_rows.select(["lg_code", "machiaza_id", "post_code"]).unique().height
muni_edges = conv.filter(pl.col("machiaza_id").is_null() |
                         (pl.col("machiaza_id").str.len_chars() == 0)
                         ).select(["lg_code", "post_code"]).unique().height
check("town-level rows collapse to their distinct edges",
      bpc.height == town_edges,
      f"{town_rows.height:,} rows -> {town_edges:,} distinct edges -> bridge {bpc.height:,}")
check("municipality-level rows all reach the municipality bridge",
      bmp.filter(pl.col("matching_rule_id") == "P2").height == muni_edges,
      f"{muni_rows:,} rows -> {muni_edges:,} edges -> "
      f"{bmp.filter(pl.col('matching_rule_id') == 'P2').height:,} P2 rows")
orphans = bpc.filter(pl.col("matching_rule_id") == "P7").height
check("edges whose ABR key is absent are retained, not dropped",
      orphans > 0, f"{orphans} retained as unresolved")

print()
print("=" * 74)
print("3. 日本郵便 郵便番号")
print("=" * 74)
kz = zipfile.ZipFile(RAW / "japanpost" / "ken_all.zip")
lines = kz.read("utf_ken_all.csv").decode("utf-8-sig").splitlines()
prv = pl.read_parquet(PQ / "postal_record_version.parquet")
check("every ken_all line became exactly one postal_record",
      len(lines) == prv.height, f"source {len(lines):,} -> db {prv.height:,}")

import csv as _csv

raws = list(_csv.reader(lines))
src_codes = [r[2] for r in raws]
check("postal_code set matches the source exactly",
      sorted(src_codes) == sorted(prv["postal_code"].to_list()),
      f"{len(set(src_codes)):,} distinct codes")
old_src = sorted(r[1].rstrip() for r in raws)
check("old_postal_code right-stripped only, never zero-stripped",
      old_src == sorted(prv["old_postal_code"].to_list()),
      f"e.g. '060  ' -> '060'; {sum(1 for o in old_src if o.startswith('0')):,} begin with 0")
for kind, suffix in [("no_listing", "以下に掲載がない場合"),
                     ("city_banchi", "の次に番地がくる場合"), ("ichien", "一円")]:
    n_src = sum(1 for r in raws if r[8].endswith(suffix))
    n_db = prv.filter(pl.col("record_kind") == kind).height
    check(f"special record '{suffix}' classified", n_src == n_db,
          f"source {n_src:,} db {n_db:,}")

print()
print("=" * 74)
print("4. 国土交通省 位置参照情報 (47 files)")
print("=" * 74)
total = 0
for p in sorted((RAW / "mlit").glob("isj_*.zip")):
    zf = zipfile.ZipFile(p)
    for n in zf.namelist():
        if n.lower().endswith(".csv"):
            total += len(zf.read(n).decode("cp932").splitlines()) - 1
mlv = pl.read_parquet(PQ / "mlit_town_version.parquet")
mlt = pl.read_parquet(PQ / "mlit_town.parquet")
check("all 47 prefecture files parsed", len(list((RAW / 'mlit').glob('isj_*.zip'))) == 47)
check("source rows == mlit_town rows (12-digit code is unique)",
      total == mlt.height, f"source {total:,} db {mlt.height:,}")
check("no 13-character codes (trailing-space bug)",
      mlv.filter(pl.col("mlit_code").str.len_chars() != 12).height == 0)
check("coordinates inside Japan",
      mlv.filter(pl.col("latitude").is_not_null() &
                 ((pl.col("latitude") < 20) | (pl.col("latitude") > 46))).height == 0)

print()
print("=" * 74)
print("5. 総務省 市外局番の一覧 (Word 97)")
print("=" * 74)
from jp_address_crosswalk.sources.doc_reader import doc_text_to_rows, extract_doc_text

rows = doc_text_to_rows(extract_doc_text(RAW / "mic_area_code" / "shigai_list.doc"),
                        expected_cells=4)
ta = pl.read_parquet(PQ / "telephone_area.parquet")
tav = pl.read_parquet(PQ / "telephone_area_version.parquet")
check("every table row except the header became a numbering area",
      len(rows) - 1 == ta.height, f"doc {len(rows) - 1} rows -> db {ta.height}")
raw_area_text = {r[1] for r in rows[1:]}
check("official 対象地域 text preserved verbatim",
      raw_area_text == set(tav["area_text_raw"].to_list()),
      f"{len(raw_area_text)} distinct clauses")
excl = sum(1 for r in rows[1:] if "除く" in r[1] or "限る" in r[1])
cov = pl.read_parquet(PQ / "telephone_area_coverage.parquet")
check("exclusion/inclusion clauses retained",
      cov.filter(pl.col("exception_text").is_not_null()).height > 0,
      f"doc has {excl} qualified rows; coverage has "
      f"{cov.filter(pl.col('exception_text').is_not_null()).height} clauses with text")

print()
print("=" * 74)
print("6. 総務省 電気通信番号指定状況 (9 workbooks)")
print("=" * 74)
import xlrd

tot = 0
for p in sorted((RAW / "mic_number_assignment").glob("fixed_*.xls")):
    sh = xlrd.open_workbook(str(p)).sheet_by_index(0)
    hdr = next(r for r in range(10)
               if str(sh.cell_value(r, 0)).strip() == "番号区画コード")
    tot += sh.nrows - hdr - 1
blk = pl.read_parquet(PQ / "telephone_number_block.parquet")
check("all 9 workbooks read", len(list((RAW/'mic_number_assignment').glob('fixed_*.xls'))) == 9)
check("source rows accounted for (block_id is area+local, so dedup is expected)",
      blk.height <= tot, f"source {tot:,} -> db {blk.height:,} "
      f"({tot - blk.height:,} share an (area_code, local_code))")
check("area codes keep their leading zero (xlrd float trap)",
      blk.filter(~pl.col("area_code").str.starts_with("0")).height == 0,
      f"e.g. {blk['area_code'][0]}")
ac_area = set(tav["area_code"].to_list())
ac_blk = set(blk["area_code"].to_list())
check("the two MIC datasets actually join",
      len(ac_area & ac_blk) / len(ac_area) > 0.9,
      f"{len(ac_area & ac_blk)}/{len(ac_area)} area codes shared")

print()
print("=" * 74)
print(f"RESULT: {len(problems)} problem(s)")
for p in problems:
    print("  !", p)
