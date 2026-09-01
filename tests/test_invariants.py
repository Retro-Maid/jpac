"""Invariants asserted against the exported Parquet tables (docs/TEST_STRATEGY.md §3).

These run against `dist/parquet/` when a build exists. They are the last line of
defence: they check the files that actually ship, not in-memory frames.
"""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl
import pytest

from jp_address_crosswalk.build.common import assert_bridge_invariants
from jp_address_crosswalk.build.quality import BRIDGES

DIST = Path(__file__).resolve().parents[1] / "dist" / "parquet"

pytestmark = pytest.mark.skipif(
    not DIST.exists() or not any(DIST.glob("*.parquet")),
    reason="no built database; run `jpac build` first",
)


def load(name: str) -> pl.DataFrame | None:
    p = DIST / f"{name}.parquet"
    return pl.read_parquet(p) if p.exists() else None


# --------------------------------------------------------------- code types

CODE_PATTERNS = {
    ("address", "lg_code"): r"^\d{6}$",
    ("address", "jis_city_code"): r"^\d{5}$",
    ("address", "machiaza_id"): r"^\d{7}$",
    ("postal_record_version", "postal_code"): r"^\d{7}$",
    ("postal_record_version", "jis_city_code"): r"^\d{5}$",
    ("mlit_town_version", "mlit_code"): r"^\d{12}$",
    ("mlit_town_version", "jis_city_code"): r"^\d{5}$",
    ("telephone_area_version", "area_code"): r"^0\d{1,4}$",
    ("telephone_area_version", "numbering_area_code"): r"^\d{3}(-\d)?$",
}


@pytest.mark.parametrize(("table", "column", "pattern"),
                         [(t, c, p) for (t, c), p in CODE_PATTERNS.items()])
def test_code_column_format(table, column, pattern):
    df = load(table)
    if df is None or column not in df.columns:
        pytest.skip(f"{table}.{column} absent")
    assert df.schema[column] == pl.Utf8, f"{table}.{column} must be Utf8"
    bad = df.filter(
        pl.col(column).is_not_null() & ~pl.col(column).str.contains(pattern)
    )
    assert bad.height == 0, f"{bad.height} bad values, e.g. {bad[column].head(3).to_list()}"


def test_old_postal_code_keeps_leading_zeros():
    """`"060  "` must become `"060"`, never `"60"` and never an integer."""
    df = load("postal_record_version")
    if df is None:
        pytest.skip("no postal data")
    assert df.schema["old_postal_code"] == pl.Utf8
    present = df.filter(pl.col("old_postal_code").is_not_null())
    bad = present.filter(~pl.col("old_postal_code").str.contains(r"^\d{3,5}$"))
    assert bad.height == 0
    # At least one genuinely zero-leading value must survive nationally.
    assert present.filter(pl.col("old_postal_code").str.starts_with("0")).height > 0


def test_area_codes_join_across_the_two_mic_datasets():
    """MIC publishes 市外局番 as `11` in the Word list and `011` in the XLS.

    Without normalization these never join and the telephone crosswalk is
    silently disconnected (0 of 387 matched before the fix).
    """
    area, block = load("telephone_area_version"), load("telephone_number_block")
    if area is None or block is None:
        pytest.skip("no telephone data")
    shared = set(area["area_code"]) & set(block["area_code"])
    coverage = len(shared) / area["area_code"].n_unique()
    assert coverage > 0.9, f"only {coverage:.1%} of area codes join"


def test_every_source_row_survives_to_its_table():
    """Row accounting against the raw payloads, not just internal consistency.

    Both row-loss defects this project has had (one Japan Post record, five MLIT
    records) came from an id that hashed only part of the row followed by
    `unique(keep="first")`. Counting rows against the original files is what
    catches that class; internal invariants never will.
    """
    import zipfile

    raw = Path(__file__).resolve().parents[1] / "data" / "raw"
    if not raw.exists():
        pytest.skip("no accepted payloads")

    ken = raw / "japanpost" / "ken_all.zip"
    if ken.exists():
        n = len(
            zipfile.ZipFile(ken).read("utf_ken_all.csv")
            .decode("utf-8-sig").splitlines()
        )
        rec = load("postal_record")
        if rec is not None:
            assert rec.height == n, f"ken_all {n} rows -> postal_record {rec.height}"

    isj = sorted((raw / "mlit").glob("isj_*.zip"))
    if isj:
        total = 0
        for p in isj:
            zf = zipfile.ZipFile(p)
            for member in zf.namelist():
                if member.lower().endswith(".csv"):
                    total += len(zf.read(member).decode("cp932").splitlines()) - 1
        mlit = load("mlit_town")
        if mlit is not None:
            assert mlit.height == total, (
                f"ISJ {total} rows -> mlit_town {mlit.height}"
            )


def test_duplicate_mlit_codes_are_kept_as_ambiguous_not_dropped():
    """MLIT publishes 5 codes twice with different names and coordinates."""
    mlit = load("mlit_town")
    if mlit is None:
        pytest.skip("no MLIT data")
    dupes = mlit.group_by("mlit_code").len().filter(pl.col("len") > 1)
    if dupes.is_empty():
        pytest.skip("no duplicate codes in this snapshot")
    # Both rows must exist; the code being ambiguous is the point.
    assert mlit["mlit_record_id"].n_unique() == mlit.height


def test_jis_city_code_is_lg_code_prefix():
    df = load("address")
    if df is None:
        pytest.skip("no address data")
    bad = df.filter(pl.col("lg_code").str.slice(0, 5) != pl.col("jis_city_code"))
    assert bad.height == 0


# ------------------------------------------------------------------ identity

ID_RE = r"^jpa1[0-9a-hjkmnp-tv-z]{16}$"


def test_address_id_format_and_uniqueness():
    df = load("address")
    if df is None:
        pytest.skip("no address data")
    assert df.filter(~pl.col("address_id").str.contains(ID_RE)).height == 0
    assert df["address_id"].n_unique() == df.height


def test_address_natural_key_is_unique():
    df = load("address")
    if df is None:
        pytest.skip("no address data")
    assert df.select(["lg_code", "machiaza_id"]).unique().height == df.height


def test_every_address_has_an_entity():
    addr, ent = load("address"), load("address_entity")
    if addr is None or ent is None:
        pytest.skip("no canonical data")
    missing = set(addr["address_id"]) - set(ent["address_id"])
    assert not missing


# ------------------------------------------------------------------- bridges

@pytest.mark.parametrize("bridge", BRIDGES)
def test_bridge_invariants(bridge):
    df = load(bridge)
    if df is None:
        pytest.skip(f"{bridge} absent")
    assert assert_bridge_invariants(df, bridge) == []


@pytest.mark.parametrize("bridge", BRIDGES)
def test_no_auto_accept_with_multiple_candidates(bridge):
    df = load(bridge)
    if df is None:
        pytest.skip(f"{bridge} absent")
    bad = df.filter(
        (pl.col("verification_status") == "auto") & (pl.col("candidate_count") > 1)
    )
    assert bad.height == 0


@pytest.mark.parametrize("bridge", BRIDGES)
def test_confidence_in_range(bridge):
    df = load(bridge)
    if df is None:
        pytest.skip(f"{bridge} absent")
    assert df.filter((pl.col("confidence") < 0) | (pl.col("confidence") > 1)).height == 0


@pytest.mark.parametrize("bridge", BRIDGES)
def test_every_bridge_row_cites_a_snapshot(bridge):
    df, snaps = load(bridge), load("source_snapshot")
    if df is None or snaps is None:
        pytest.skip("missing data")
    known = set(snaps["source_snapshot_id"])
    assert set(df["source_snapshot_id"]) <= known


@pytest.mark.parametrize("bridge", BRIDGES)
def test_candidate_count_matches_the_actual_group_size(bridge):
    """`candidate_count` must be the truth, not a number chosen to force a status.

    Inflating it to block auto-accept made the column disagree with the number of
    rows actually in the group, which would mislead anyone filtering on it.
    """
    df = load(bridge)
    if df is None or df.is_empty():
        pytest.skip(f"{bridge} absent")
    grouped = df.filter(pl.col("candidate_group_id").is_not_null())
    if grouped.is_empty():
        pytest.skip("no candidate groups")
    sizes = grouped.group_by("candidate_group_id").agg(
        [pl.len().alias("actual"), pl.col("candidate_count").max().alias("claimed")]
    )
    bad = sizes.filter(pl.col("actual") != pl.col("claimed"))
    assert bad.height == 0, (
        f"{bad.height} groups where candidate_count disagrees with the group size, "
        f"e.g. {bad.head(3).to_dicts()}"
    )


@pytest.mark.parametrize("bridge", BRIDGES)
def test_single_candidate_rows_have_no_group(bridge):
    df = load(bridge)
    if df is None or df.is_empty():
        pytest.skip(f"{bridge} absent")
    bad = df.filter(
        (pl.col("candidate_count") == 1) & pl.col("candidate_group_id").is_not_null()
    )
    assert bad.height == 0


def test_bridge_address_ids_exist():
    ent = load("address_entity")
    if ent is None:
        pytest.skip("no entities")
    known = set(ent["address_id"])
    for bridge in BRIDGES:
        df = load(bridge)
        if df is None or "address_id" not in df.columns:
            continue
        ids = set(df.filter(pl.col("address_id").is_not_null())["address_id"])
        assert ids <= known, f"{bridge} references unknown address_id"


# ------------------------------------------------------------------ semantics

def test_special_postal_records_never_reach_the_town_bridge():
    """以下に掲載がない場合 / 一円 are municipality statements (docs/POLICY.md §4)."""
    prv, bridge = load("postal_record_version"), load("bridge_address_postal")
    if prv is None or bridge is None:
        pytest.skip("missing data")
    specials = set(prv.filter(pl.col("record_kind") != "town")["postal_record_id"])
    used = set(bridge.filter(pl.col("target_id").is_not_null())["target_id"])
    assert not (specials & used)


def test_municipality_telephone_evidence_never_expands_to_towns():
    """MIC statements stop at municipality level (docs/POLICY.md §4)."""
    df = load("bridge_address_telephone")
    if df is None:
        pytest.skip("no telephone bridge")
    assert df["target_id"].null_count() == df.height
    assert df["derivation"].null_count() == df.height
    assert df["relation_type"].unique().to_list() == ["unresolved"]
    assert df["matching_rule_id"].unique().to_list() == ["T10"]
    assert df["coverage_type"].unique().to_list() == ["municipality_only"]
    assert df["candidate_count"].sum() == 0


def test_postal_validity_dates_are_null():
    """Japan Post states no effective date; a download date must not stand in."""
    df = load("postal_record_version")
    if df is None:
        pytest.skip("no postal data")
    assert df["valid_from"].null_count() == df.height
    assert df["valid_to"].null_count() == df.height


def test_abr_validity_dates_are_not_the_download_date():
    df, snaps = load("address"), load("source_snapshot")
    if df is None or snaps is None:
        pytest.skip("missing data")
    downloaded = {s[:10] for s in snaps["downloaded_at"].to_list() if s}
    present = df.filter(pl.col("valid_from").is_not_null())
    if present.is_empty():
        pytest.skip("no validity dates")
    # Some towns may legitimately take effect today, but not all of them.
    assert not set(present["valid_from"].unique().to_list()) <= downloaded


def test_coordinates_are_inside_japan():
    df = load("mlit_town_version")
    if df is None:
        pytest.skip("no MLIT data")
    bad = df.filter(
        pl.col("latitude").is_not_null()
        & ((pl.col("latitude") < 20) | (pl.col("latitude") > 46)
           | (pl.col("longitude") < 122) | (pl.col("longitude") > 154))
    )
    assert bad.height == 0


def test_ambiguity_is_actually_retained():
    """A database with zero ambiguous rows would mean the rules are forcing matches."""
    df = load("bridge_address_postal")
    if df is None:
        pytest.skip("no postal bridge")
    ambiguous = df.filter(pl.col("relation_type").is_in(["ambiguous", "parent"]))
    assert ambiguous.height > 0, "expected retained ambiguity in national data"
    assert (ambiguous["verification_status"] == "auto").sum() == 0


def test_flat_view_exposes_evidence_columns():
    p = DIST.parent / "jp_address_crosswalk.parquet"
    if not p.exists():
        pytest.skip("no flat view")
    cols = set(pl.read_parquet(p).columns)
    for required in [
        "postal_relation_type", "postal_confidence", "postal_candidate_count",
        "mlit_relation_type", "mlit_confidence",
        "telephone_relation_type", "telephone_confidence",
    ]:
        assert required in cols, f"flat view must expose {required}"


def test_ledger_covers_every_address():
    import gzip

    ledger = Path(__file__).resolve().parents[1] / "identity" / "address_id_ledger.csv.gz"
    addr = load("address")
    if not ledger.exists() or addr is None:
        pytest.skip("no ledger")
    with gzip.open(ledger, "rb") as fh:
        led = pl.read_csv(fh.read(), schema_overrides={"address_id": pl.Utf8})
    assert set(addr["address_id"]) <= set(led["address_id"])
    assert re.match(ID_RE, led["address_id"][0])
