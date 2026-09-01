"""Distributional sanity, and the flat view rebuilt from its own tables.

  7. Distribution
     A parser can lose a whole prefecture, or attach everything to one
     municipality, without breaking a single referential invariant. Shape checks
     catch that: is every prefecture present, is the spread plausible, do postal
     code prefixes agree with the prefecture they are attached to?

  8. Flat view reconstruction
     The shipped flat file is recomputed from the normalized tables and compared
     with what was shipped. A bug in the view definition would otherwise be
     invisible: every normalized table would be correct and the file users
     actually read would be wrong.

Run:  jpac verify distribution   (or: py -3.12 tools/verify_distribution.py)
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


import csv
import io
import sqlite3
import sys
import zipfile
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
PQ = ROOT / "dist" / "parquet"
DIST = ROOT / "dist"

from jp_address_crosswalk.export import writers  # noqa: E402

failures: list[str] = []
notes: list[str] = []


def ok(label: str, passed: bool, detail: str = "") -> None:
    print(f"  [{'OK ' if passed else 'BAD'}] {label}" + (f"  {detail}" if detail else ""))
    if not passed:
        failures.append(f"{label}: {detail}")


def note(label: str, clean: bool, detail: str = "") -> None:
    print(f"  [{'OK ' if clean else 'note'}] {label}" + (f"  {detail}" if detail else ""))
    if not clean:
        notes.append(f"{label}: {detail}")


addr = pl.read_parquet(PQ / "address.parquet")
post = pl.read_parquet(PQ / "postal_record_version.parquet").filter(pl.col("is_current"))
mlit = pl.read_parquet(PQ / "mlit_town_version.parquet").filter(pl.col("is_current"))

print("\n7. Distribution")

# --- every prefecture, in all three sources
pref_addr = addr.select(pl.col("lg_code").str.slice(0, 2).alias("p"))["p"].unique()
ok("ABR covers all 47 prefectures", pref_addr.len() == 47, f"{pref_addr.len()}")
ok("prefecture codes are 01..47",
   sorted(pref_addr.to_list()) == [f"{i:02d}" for i in range(1, 48)])
ok("Japan Post covers all 47",
   post.select(pl.col("jis_city_code").str.slice(0, 2))["jis_city_code"].n_unique() == 47)
ok("MLIT covers all 47", mlit["pref_code"].n_unique() == 47)

# --- spread: no prefecture should hold an implausible share of the towns
per_pref = (
    addr.with_columns(pl.col("lg_code").str.slice(0, 2).alias("p"))
    .group_by("p").agg(pl.len().alias("n")).sort("n", descending=True)
)
top, smallest = per_pref.row(0), per_pref.row(-1)
print(f"      most {top[0]}: {top[1]:,} towns    least {smallest[0]}: "
      f"{smallest[1]:,}    ratio {top[1] / smallest[1]:.0f}x")

# A 76x spread between prefectures is large enough to look like a parser fault,
# so it is checked against the publisher rather than against a threshold. Every
# prefecture's town count must equal the raw ABR row count minus exactly the
# rows collapsed into address_rsdt_variant, which retains every source row.
raw_counts: dict[str, int] = {}
with zipfile.ZipFile(ROOT / "data" / "raw" / "abr" / "town_master.zip") as z:
    name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
    with z.open(name) as fh:
        for row in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig")):
            p = row["lg_code"][:2]
            raw_counts[p] = raw_counts.get(p, 0) + 1

variants = pl.read_parquet(PQ / "address_rsdt_variant.parquet")
vp = variants.with_columns(pl.col("lg_code").str.slice(0, 2).alias("p"))
var_rows = dict(vp.group_by("p").agg(pl.len().alias("n")).rows())
var_keys = dict(
    vp.group_by("p")
    .agg(pl.struct(["lg_code", "machiaza_id"]).n_unique().alias("n"))
    .rows()
)
db_counts = dict(per_pref.rows())

ok("the same prefectures appear in the raw file and the database",
   set(raw_counts) == set(db_counts),
   f"raw-only {sorted(set(raw_counts) - set(db_counts))} "
   f"db-only {sorted(set(db_counts) - set(raw_counts))}")

# Two separate identities, so a compensating error cannot hide:
#   the variant table holds every raw row, and
#   the address table holds exactly its distinct keys.
mismatch = [
    (p, raw_counts[p], var_rows.get(p, 0))
    for p in sorted(raw_counts)
    if raw_counts[p] != var_rows.get(p, 0)
]
ok("every raw ABR row survives into address_rsdt_variant, prefecture by "
   "prefecture", not mismatch, f"{len(mismatch)} differ, e.g. {mismatch[:3]}")

mismatch = [
    (p, db_counts[p], var_keys.get(p, 0))
    for p in sorted(db_counts)
    if db_counts[p] != var_keys.get(p, 0)
]
ok("town counts equal the distinct source keys, prefecture by prefecture",
   not mismatch, f"{len(mismatch)} differ, e.g. {mismatch[:3]}")
print(f"      raw {sum(raw_counts.values()):,} rows -> {addr.height:,} towns "
      f"({sum(raw_counts.values()) - addr.height:,} 住居表示 variants collapsed); "
      f"the skew is the publisher's")

# --- towns per municipality
per_muni = addr.group_by("lg_code").agg(pl.len().alias("n"))
ok("no municipality holds an implausible share", per_muni["n"].max() < 20000,
   f"largest {per_muni['n'].max():,} towns")

# --- postal prefix vs prefecture. Japan Post's first two digits encode a
#     region, and the mapping is many-to-one, so this is derived from the data
#     and checked for consistency rather than against an invented table.
pp = (
    post.with_columns([
        pl.col("postal_code").str.slice(0, 2).alias("pp"),
        pl.col("jis_city_code").str.slice(0, 2).alias("pref"),
    ])
    .filter(pl.col("record_kind") == "town")
    .group_by(["pp", "pref"]).agg(pl.len().alias("n"))
)
# For each postal prefix, one prefecture should dominate overwhelmingly.
dom = (
    pp.group_by("pp")
    .agg([pl.col("n").sum().alias("total"), pl.col("n").max().alias("best")])
    .with_columns((pl.col("best") / pl.col("total")).alias("frac"))
)
weak = dom.filter(pl.col("frac") < 0.5)
note("each postal prefix maps predominantly to one prefecture", weak.height == 0,
     f"{weak.height} of {dom.height} prefixes are split, e.g. "
     f"{weak.head(3)['pp'].to_list()}")

# --- codes are all the right shape and all non-numeric-safe
for tbl, col, n in [(addr, "lg_code", 6), (addr, "machiaza_id", 7),
                    (post, "postal_code", 7), (mlit, "mlit_code", 12)]:
    bad = tbl.filter(pl.col(col).str.len_chars() != n)
    ok(f"{col} is always {n} characters", bad.height == 0, f"{bad.height} bad")

# --- nulls where they must not be
for tbl, name, cols in [
    (addr, "address", ["address_id", "lg_code", "machiaza_id", "pref", "city"]),
    (post, "postal_record_version", ["postal_record_id", "postal_code", "pref", "city"]),
    (mlit, "mlit_town_version", ["mlit_record_id", "mlit_code", "pref_name"]),
]:
    bad = [c for c in cols if tbl[c].null_count()]
    ok(f"{name}: required columns are never null", not bad, ", ".join(bad))

print("\n8. Flat view reconstruction")

tables = {p.stem: pl.read_parquet(p) for p in PQ.glob("*.parquet")}
rebuilt = writers.build_flat_view(tables, accepted_only=True)
shipped = pl.read_parquet(DIST / "jp_address_crosswalk.parquet")

ok("row count matches the shipped flat file",
   rebuilt.height == shipped.height, f"{rebuilt.height:,} vs {shipped.height:,}")
ok("column set matches", list(rebuilt.columns) == list(shipped.columns))

if rebuilt.height == shipped.height and list(rebuilt.columns) == list(shipped.columns):
    key = lambda df: sorted(  # noqa: E731
        df.select(
            pl.concat_str(
                [pl.col(c).cast(pl.Utf8).fill_null(chr(0)) for c in df.columns],
                separator=chr(31),
            ).alias("_k")
        )["_k"].to_list()
    )
    a, b = key(rebuilt), key(shipped)
    diff = sum(1 for x, y in zip(a, b, strict=False) if x != y)
    ok("every row identical", diff == 0, f"{diff:,} rows differ")

# Recomputing with build_flat_view only proves the file was not damaged after
# being written. The SQLite view (writers.FLAT_VIEWS) is a second, independently
# written implementation of the same definition — hand-written SQL rather than
# Polars joins — so comparing the two is what can actually catch a wrong view.
print("\n8b. Parquet flat view vs the independently written SQL view")
conn = sqlite3.connect(
    f"file:{(DIST / 'jp_address_crosswalk.sqlite').as_posix()}?mode=ro", uri=True
)
# The shipped flat file is the accepted-evidence view, so that is the one to
# compare against; address_crosswalk_all is the unfiltered counterpart.
cur = conn.execute("SELECT * FROM address_crosswalk")
sql_cols = [d[0] for d in cur.description]
ok("the SQL view exposes the same columns", sql_cols == list(shipped.columns),
   f"only in SQL {sorted(set(sql_cols) - set(shipped.columns))}, "
   f"only in Parquet {sorted(set(shipped.columns) - set(sql_cols))}")

if sql_cols == list(shipped.columns):
    def canon(v: object) -> str:
        # SQLite has no boolean type and stores REAL with its own repr, so both
        # sides are reduced to one spelling before they are compared.
        if v is None:
            return chr(0)
        if isinstance(v, bool):
            return "1" if v else "0"
        if isinstance(v, float):
            return f"{v:.9g}"
        if isinstance(v, int):
            return str(v)
        return str(v)

    sql_rows = sorted(chr(31).join(canon(v) for v in row) for row in cur)
    pq_rows = sorted(chr(31).join(canon(v) for v in row) for row in shipped.iter_rows())
    ok("the two implementations agree on row count",
       len(sql_rows) == len(pq_rows), f"{len(sql_rows):,} vs {len(pq_rows):,}")
    if len(sql_rows) == len(pq_rows):
        diff = [
            i for i, (x, y) in enumerate(zip(sql_rows, pq_rows, strict=True)) if x != y
        ]
        ok("the two implementations produce identical rows", not diff,
           f"{len(diff):,} of {len(pq_rows):,} rows differ")
        if diff:
            print("        sql:", sql_rows[diff[0]][:240])
            print("        pq :", pq_rows[diff[0]][:240])
# The filtered view is a subset of the unfiltered one, and — the property that
# a WHERE-after-JOIN implementation violates — it must still contain every
# address. Rejecting a match may blank a column; it may never delete a row.
n_all, n_acc, n_addr = (
    conn.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0]
    for q in ("address_crosswalk_all", "address_crosswalk",
              "(SELECT DISTINCT address_id FROM address_crosswalk)")
)
ok("the accepted view is no larger than the unfiltered one", n_acc <= n_all,
   f"{n_acc:,} vs {n_all:,}")
ok("no address is dropped by the accepted view", n_addr == addr.height,
   f"{n_addr:,} of {addr.height:,}")
conn.close()

# The view must not silently drop an address that has no bridges at all.
ok("every address appears in the flat view",
   set(addr["address_id"]) <= set(shipped["address_id"]),
   f"{len(set(addr['address_id']) - set(shipped['address_id']))} missing")

print("\n" + "=" * 78)
print(f"DEFECTS: {len(failures)}")
for f in failures:
    print("  !", f)
print(f"NOTES: {len(notes)}")
for n in notes:
    print("  -", n)
sys.exit(1 if failures else 0)
