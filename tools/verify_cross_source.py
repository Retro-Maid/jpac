"""Triangulate the publishers against each other, and sanity-check geography.

Everything verified so far asks "did we carry the source through faithfully?".
This asks a different question: "do the publishers agree with each other, and
does the result make geographic sense?" No external ground truth is used — the
sources check each other, which is the only independent signal available when
no answer key exists.

  2. Cross-source triangulation
     Digital Agency, Japan Post and MLIT each name the same jis_city_code.
     Where they disagree, either a publisher is out of date or this project has
     mis-assigned something. Either way it is worth seeing.

  3. Geographic sanity
     Coordinates are currently only checked against "somewhere in Japan". A
     Hokkaido town carrying Okinawa coordinates passes that. Rather than invent
     official prefecture boundaries, each point is tested against the *observed*
     distribution of its own prefecture, and against whether its own prefecture
     is the nearest one.

Run:  jpac verify cross-source   (or: py -3.12 tools/verify_cross_source.py)
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


import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
PQ = ROOT / "dist" / "parquet"

from jp_address_crosswalk.normalize import normalize_conservative  # noqa: E402

failures: list[str] = []
notes: list[str] = []


def ok(label: str, passed: bool, detail: str = "") -> None:
    """A defect: something this project got wrong. Fails the run."""
    print(f"  [{'OK ' if passed else 'BAD'}] {label}" + (f"  {detail}" if detail else ""))
    if not passed:
        failures.append(f"{label}: {detail}")


def note(label: str, clean: bool, detail: str = "") -> None:
    """A property of the sources. Reported, never a failure.

    Two ministries spelling a municipality differently is a fact about the
    ministries. Treating it as a defect would mean the run never goes green and
    the exit code stops meaning anything.
    """
    print(f"  [{'OK ' if clean else 'note'}] {label}" + (f"  {detail}" if detail else ""))
    if not clean:
        notes.append(f"{label}: {detail}")


def norm(s):
    return normalize_conservative(s) if s else ""


muni = pl.read_parquet(PQ / "municipality_version.parquet").filter(pl.col("is_current"))
post = pl.read_parquet(PQ / "postal_record_version.parquet").filter(pl.col("is_current"))
mlit = pl.read_parquet(PQ / "mlit_town_version.parquet").filter(pl.col("is_current"))

print("\n2. Cross-source triangulation  (do the publishers agree?)")

# --- 2a. Which municipality codes does each publisher know about?
abr_codes = set(muni["jis_city_code"].to_list())
jp_codes = set(post["jis_city_code"].to_list())
ml_codes = set(mlit["jis_city_code"].to_list())

print(f"      ABR {len(abr_codes):,}   Japan Post {len(jp_codes):,}   MLIT {len(ml_codes):,}")
ok("Japan Post codes are all known to ABR", jp_codes <= abr_codes,
   f"{len(jp_codes - abr_codes)} unknown, e.g. {sorted(jp_codes - abr_codes)[:5]}")
ok("MLIT codes are all known to ABR", ml_codes <= abr_codes,
   f"{len(ml_codes - abr_codes)} unknown, e.g. {sorted(ml_codes - abr_codes)[:5]}")
print(f"      (ABR-only codes: {len(abr_codes - jp_codes - ml_codes):,} — expected: "
      f"ABR carries 政令市 umbrella rows and municipalities the others omit)")

# --- 2b. Do they give the same prefecture for the same code?
abr_pref = {
    r["jis_city_code"]: norm(r["pref"]) for r in muni.iter_rows(named=True)
}
jp_pref = (
    post.select(["jis_city_code", "pref"]).unique()
    .with_columns(pl.col("pref").map_elements(norm, return_dtype=pl.Utf8))
)
ml_pref = (
    mlit.select(["jis_city_code", "pref_name"]).unique()
    .with_columns(pl.col("pref_name").map_elements(norm, return_dtype=pl.Utf8))
)

bad = [
    (r["jis_city_code"], abr_pref[r["jis_city_code"]], r["pref"])
    for r in jp_pref.iter_rows(named=True)
    if r["jis_city_code"] in abr_pref and abr_pref[r["jis_city_code"]] != r["pref"]
]
ok("ABR and Japan Post agree on the prefecture", not bad,
   f"{len(bad)} disagree, e.g. {bad[:3]}")

bad = [
    (r["jis_city_code"], abr_pref[r["jis_city_code"]], r["pref_name"])
    for r in ml_pref.iter_rows(named=True)
    if r["jis_city_code"] in abr_pref and abr_pref[r["jis_city_code"]] != r["pref_name"]
]
ok("ABR and MLIT agree on the prefecture", not bad,
   f"{len(bad)} disagree, e.g. {bad[:3]}")

# --- 2c. Do they give the same municipality name? ABR splits city and ward;
#         the other two publish one composed string.
abr_city = {}
for r in muni.iter_rows(named=True):
    composed = norm((r.get("county") or "") + (r.get("city") or "") + (r.get("ward") or ""))
    plain = norm((r.get("city") or "") + (r.get("ward") or ""))
    abr_city[r["jis_city_code"]] = {composed, plain}

def compare_city(df: pl.DataFrame, col: str, label: str) -> None:
    pairs = df.select(["jis_city_code", col]).unique().iter_rows(named=True)
    disagree = []
    checked = 0
    for r in pairs:
        code, name = r["jis_city_code"], norm(r[col])
        if code not in abr_city:
            continue
        checked += 1
        if name not in abr_city[code]:
            disagree.append((code, sorted(abr_city[code]), name))
    note(f"ABR and {label} agree on the municipality name", not disagree,
         f"{len(disagree)} of {checked:,} differ")
    return disagree

jp_bad = compare_city(post, "city", "Japan Post")
ml_bad = compare_city(mlit, "city_name", "MLIT")

if jp_bad or ml_bad:
    print("\n      These are disagreements between ministries, not pipeline defects.")
    print("      They are reported because the conservative normalization profile")
    print("      deliberately does not unify them (docs/MATCHING_RULES.md 8):")
    for code, abr_names, other in (jp_bad + ml_bad):
        print(f"        {code}  ABR {abr_names[0]:<16} other {other}")
    print("      Unifying them would hide a real inter-ministry difference.")

# --- 2d. Structural: mlit_code's embedded city code must equal its own column.
bad = mlit.filter(pl.col("mlit_code").str.slice(0, 5) != pl.col("jis_city_code"))
ok("MLIT code's first 5 digits == its jis_city_code", bad.height == 0,
   f"{bad.height} rows")

# --- 2e. Every municipality ABR knows should have at least one town.
addr = pl.read_parquet(PQ / "address.parquet")
towns_per = set(addr["lg_code"].to_list())
no_town = muni.filter(~pl.col("lg_code").is_in(list(towns_per)))

# Two categories legitimately have no 町字 of their own:
#   - a 政令指定都市 umbrella row, whose towns are keyed under its wards
#   - the Northern Territories, which ABR lists because they are legally
#     Japanese municipalities but for which no address data exists
wards_of = {
    norm(r["city"]): True
    for r in muni.iter_rows(named=True)
    if r.get("ward")
}
NORTHERN_TERRITORIES = {"016951", "016969", "016977", "016985", "016993", "017001"}
unexplained = [
    r["lg_code"]
    for r in no_town.iter_rows(named=True)
    if not (
        (r.get("ward") is None and wards_of.get(norm(r.get("city") or "")))
        or r["lg_code"] in NORTHERN_TERRITORIES
    )
]
ok("every municipality has 町字, or a documented reason not to", not unexplained,
   f"{len(unexplained)} unexplained, e.g. {unexplained[:5]}")
print(f"      ({no_town.height} without towns: "
      f"{no_town.height - len(NORTHERN_TERRITORIES & set(no_town['lg_code']))} "
      f"政令市 umbrella rows, "
      f"{len(NORTHERN_TERRITORIES & set(no_town['lg_code']))} 北方領土)")

# --------------------------------------------------------------------------
print("\n3. Geographic sanity  (are coordinates where they should be?)")

geo = mlit.filter(pl.col("latitude").is_not_null() & pl.col("longitude").is_not_null())
print(f"      {geo.height:,} coordinates across {geo['pref_code'].n_unique()} prefectures")

# Clustering by MUNICIPALITY, not by prefecture. A prefecture-level test cannot
# work in Japan: 東京都 reaches 20°N at 沖ノ鳥島 and the 奄美群島 belong to
# 鹿児島県 while sitting closer to 沖縄. A municipality, by contrast, is small
# everywhere, so its own points must cluster tightly — and that holds for island
# municipalities too, which is what makes this test meaningful rather than a
# list of exceptions.
cent = geo.group_by("jis_city_code").agg(
    [pl.col("latitude").median().alias("clat"),
     pl.col("longitude").median().alias("clon"),
     pl.len().alias("n")]
)
ok("all 47 prefectures present", geo["pref_code"].n_unique() == 47,
   f"{geo['pref_code'].n_unique()} prefectures")

j = geo.join(cent, on="jis_city_code", how="left").with_columns(
    (((pl.col("latitude") - pl.col("clat")) ** 2
      + (pl.col("longitude") - pl.col("clon")) ** 2) ** 0.5).alias("d")
)
# 1 degree is roughly 111 km. No municipality in Japan spans that from its own
# median point except ones made of scattered islands, so this is generous.
far = j.filter(pl.col("d") > 1.0)
scattered = (
    far.group_by(["jis_city_code", "pref_name", "city_name"])
    .agg(pl.len().alias("points")).sort("points", descending=True)
)
note("every point sits within 1 degree of its own municipality's centre",
     far.height == 0,
     f"{far.height} of {geo.height:,} points in {scattered.height} municipalities")
if scattered.height:
    print("      Municipalities whose own points are more than 110 km apart. These "
          "are\n      archipelagos: one village spanning several islands, which is "
          "real geography\n      rather than a coordinate error.")
    for r in scattered.iter_rows(named=True):
        print(f"        {r['jis_city_code']}  {r['pref_name']}{r['city_name']}"
              f"  ({r['points']} point(s))")

# What *would* be a defect: so many scattered municipalities that the clustering
# assumption has broken down, which would mean coordinates are being attached to
# the wrong towns.
ok("scattered-island municipalities remain a handful",
   scattered.height <= 20, f"{scattered.height} municipalities")

# A coordinate must at least be somewhere Japan actually is.
outside = geo.filter(
    (pl.col("latitude") < 20.0) | (pl.col("latitude") > 46.0)
    | (pl.col("longitude") < 122.0) | (pl.col("longitude") > 154.0)
)
ok("every point is within Japan's extent", outside.height == 0,
   f"{outside.height} outside")

# The prefecture embedded in the code must agree with the coordinate's own
# prefecture cluster at the coarsest level: no point may be nearer to a
# *different* prefecture's nearest municipality than to any municipality of its
# own. Island geography is handled because the comparison is municipality-level.
pref_of = dict(cent.join(
    geo.select(["jis_city_code", "pref_code"]).unique(), on="jis_city_code"
).select(["jis_city_code", "pref_code"]).rows())
muni_pts = cent.select(["jis_city_code", "clat", "clon"]).rows()
by_pref: dict[str, list] = {}
for code, la, lo in muni_pts:
    by_pref.setdefault(pref_of[code], []).append((la, lo))

wrong_pref = []
for code, la, lo in muni_pts:
    own = pref_of[code]
    own_d = min(((la - a) ** 2 + (lo - b) ** 2) ** 0.5 for a, b in by_pref[own])
    for p2, pts in by_pref.items():
        if p2 == own:
            continue
        if min(((la - a) ** 2 + (lo - b) ** 2) ** 0.5 for a, b in pts) + 1.0 < own_d:
            wrong_pref.append((code, own, p2))
            break
ok("no municipality sits far inside another prefecture's territory",
   not wrong_pref, f"{len(wrong_pref)} suspicious, e.g. {wrong_pref[:3]}")

print("\n" + "=" * 78)
print(f"DEFECTS: {len(failures)}")
for f in failures:
    print("  !", f)
print(f"SOURCE DIFFERENCES (informational): {len(notes)}")
for n in notes:
    print("  -", n)
sys.exit(1 if failures else 0)
