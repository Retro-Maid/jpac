"""Canonical address layer: address_entity, address, municipality.

ABR's ``efct_date`` / ``ablt_date`` are the only genuine real-world validity
dates in the whole project, so they — and nothing else — populate
``valid_from`` / ``valid_to`` (docs/POLICY.md §7).
"""

from __future__ import annotations

import pathlib

import polars as pl
import yaml

from ..identity import IdentityLedger
from ..logging_setup import get_logger, stage_context
from ..normalize import (
    NORMALIZATION_PROFILE_VERSION,
    normalize_conservative,
)

log = get_logger(__name__)

ADDRESS_TEXT_COLUMNS = [
    "machiaza_type", "pref", "county", "city", "ward",
    "oaza_cho", "chome", "chome_number", "koaza", "machiaza_dist",
    "oaza_cho_kana", "chome_kana", "koaza_kana", "oaza_cho_roma", "koaza_roma",
    "rsdt_addr_flg", "rsdt_addr_mtd_code", "oaza_cho_aka_flg", "koaza_aka_code",
    "status_flg", "wake_num_flg", "src_code", "remarks",
]


def _empty_to_null(col: str) -> pl.Expr:
    return (
        pl.when(pl.col(col).is_null() | (pl.col(col).str.strip_chars() == ""))
        .then(None)
        .otherwise(pl.col(col))
        .alias(col)
    )


def prepare_towns(abr_town: pl.DataFrame) -> pl.DataFrame:
    """Normalize ABR town rows and compose the comparable full name."""
    with stage_context("abr", "normalize"):
        df = abr_town
        for col in [*ADDRESS_TEXT_COLUMNS, "efct_date", "ablt_date"]:
            if col in df.columns:
                df = df.with_columns(_empty_to_null(col))

        df = df.with_columns(
            (
                pl.col("oaza_cho").fill_null("")
                + pl.col("chome").fill_null("")
                + pl.col("koaza").fill_null("")
            ).alias("full_name_raw")
        )
        df = df.with_columns(
            pl.col("full_name_raw")
            .map_elements(normalize_conservative, return_dtype=pl.Utf8)
            .alias("full_name_normalized")
        )
        # Sorted before identity resolution so the outcome cannot depend on the
        # order rows happened to arrive in.
        df = df.sort(["lg_code", "machiaza_id"])
        log.info("prepared ABR towns", rows=df.height)
        return df


def split_rsdt_variants(towns: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame, list[dict]]:
    """Collapse ABR's duplicate 町字 rows without discarding what they say.

    Observed in the national data (2026-08-20 snapshot): 1,248 of 727,418 rows
    share a ``(lg_code, machiaza_id)`` with another row and differ **only** in
    ``rsdt_addr_flg`` / ``rsdt_addr_mtd_code``. ABR is describing one 町字 that
    has both a 住居表示 and a 地番 aspect, not two places.

    Picking one row would silently drop a published fact, and keeping both as
    separate entities would invent a second town. So the town collapses to one
    canonical row and every published flag combination is preserved in
    ``address_rsdt_variant``. Where the variants disagree, the collapsed row
    carries NULL rather than an arbitrary winner, and ``rsdt_variant_count``
    tells the reader to look at the variant table.

    Returns ``(unique_towns, variants, conflicts)``.
    """
    key = ["lg_code", "machiaza_id"]
    variant_cols = VARIANT_COLUMNS

    counts = towns.group_by(key).agg(pl.len().alias("row_count"))
    dup_keys = counts.filter(pl.col("row_count") > 1).select(key)

    variants = (
        towns.select([*key, *variant_cols])
        .unique()
        .sort([*key, *variant_cols])
        .with_columns(
            pl.format(
                "rv_{}_{}_{}_{}",
                pl.col("lg_code"), pl.col("machiaza_id"),
                pl.col("rsdt_addr_flg").fill_null("_"),
                pl.col("rsdt_addr_mtd_code").fill_null("_"),
            ).alias("rsdt_variant_id")
        )
        .select(["rsdt_variant_id", *key, *variant_cols])
    )

    variant_counts = variants.group_by(key).agg(
        pl.len().alias("rsdt_variant_count")
    )

    # Any column that disagrees within a key becomes NULL on the collapsed row.
    disagreeing = [
        c for c in towns.columns
        if c not in key
        and towns.join(dup_keys, on=key, how="inner")
        .group_by(key)
        .agg(pl.col(c).n_unique().alias("n"))
        .filter(pl.col("n") > 1)
        .height
        > 0
    ]

    conflicts: list[dict] = []
    unmodelled = [c for c in disagreeing if c not in variant_cols]
    if disagreeing:
        log.warning(
            "ABR publishes disagreeing values for a shared natural key",
            columns=disagreeing, unmodelled=unmodelled, keys=dup_keys.height,
        )
        conflicting = towns.join(dup_keys, on=key, how="inner")
        for c in unmodelled:
            bad = (
                conflicting.group_by(key)
                .agg(pl.col(c).n_unique().alias("n"))
                .filter(pl.col("n") > 1)
            )
            for row in bad.iter_rows(named=True):
                conflicts.append(
                    {
                        "reason": "abr_duplicate_key_field_conflict",
                        "lg_code": row["lg_code"],
                        "machiaza_id": row["machiaza_id"],
                        "field": c,
                        "note": "ABR publishes two rows for this 町字 with different "
                                f"{c}; the collapsed row carries NULL and every "
                                "source row is preserved in address_key_conflict",
                    }
                )

    unique_towns = towns.unique(subset=key, keep="first").sort(key)
    null_cols = [c for c in disagreeing if c in towns.columns]
    if null_cols:
        conflict_keys = set(
            zip(dup_keys["lg_code"].to_list(), dup_keys["machiaza_id"].to_list(),
                strict=False)
        )
        mask = pl.struct(key).map_elements(
            lambda s: (s["lg_code"], s["machiaza_id"]) in conflict_keys,
            return_dtype=pl.Boolean,
        )
        unique_towns = unique_towns.with_columns(
            [
                pl.when(mask).then(None).otherwise(pl.col(c)).alias(c)
                for c in null_cols
            ]
        )

    unique_towns = unique_towns.join(variant_counts, on=key, how="left")

    log.info(
        "collapsed ABR rsdt variants",
        input_rows=towns.height, unique_towns=unique_towns.height,
        duplicate_keys=dup_keys.height, variants=variants.height,
        field_conflicts=len(conflicts),
    )
    return unique_towns, variants, conflicts


# Fields whose disagreement is understood and modelled: ABR describes one 町字
# with both a 住居表示 and a 地番 aspect. A disagreement in ANY other field is
# not understood, so it must not be collapsed away silently.
#
# efct_date and ablt_date belong here because each aspect carries its own
# effective date. The single national instance (群馬県前橋市, machiaza_id
# 0106005) reads 1947-04-17 on the 住居表示 row and 2022-02-02 on the 地番 row:
# the town has existed since 1947 and the other aspect took effect in 2022.
# Both are real, so both are kept per variant in address_rsdt_variant and the
# collapsed row carries NULL — "the town's valid_from" is not a question ABR
# answers with one value here.
VARIANT_COLUMNS = ["rsdt_addr_flg", "rsdt_addr_mtd_code", "efct_date", "ablt_date"]
MODELLED_VARIANT_FIELDS = set(VARIANT_COLUMNS)


def load_reviewed_conflicts(path: pathlib.Path) -> set[tuple[str, str, str]]:
    """Human sign-offs for conflicts this project does not model.

    Fail-closed is only workable if there is a way through it. A reviewer looks
    at the retained rows, records what the publisher actually said and why
    proceeding is safe, and the build stops blocking on that exact
    ``(lg_code, machiaza_id, field)``. Anything not signed off still blocks.
    """
    if not path.exists():
        return set()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: set[tuple[str, str, str]] = set()
    for e in data.get("reviewed") or []:
        for field in e.get("fields", []):
            out.add((str(e["lg_code"]), str(e["machiaza_id"]), field))
    if out:
        log.info("reviewed source conflicts loaded", count=len(out), path=str(path))
    return out


def key_conflict_rows(
    towns: pl.DataFrame, reviewed: set[tuple[str, str, str]] | None = None
) -> pl.DataFrame:
    """Every source row involved in an unmodelled natural-key conflict.

    Kept losslessly so nothing is discarded before the duplicate gate runs, and
    so a reviewer can see exactly what the publisher said (docs/POLICY.md §5).
    """
    key = ["lg_code", "machiaza_id"]
    dup_keys = (
        towns.group_by(key).agg(pl.len().alias("n")).filter(pl.col("n") > 1).select(key)
    )
    if not dup_keys.height:
        return pl.DataFrame(schema={c: pl.Utf8 for c in towns.columns})

    conflicting = towns.join(dup_keys, on=key, how="inner")
    unmodelled = [
        c
        for c in towns.columns
        if c not in key
        and c not in MODELLED_VARIANT_FIELDS
        and conflicting.group_by(key)
        .agg(pl.col(c).n_unique().alias("n"))
        .filter(pl.col("n") > 1)
        .height
        > 0
    ]
    if not unmodelled:
        return pl.DataFrame(schema={c: pl.Utf8 for c in towns.columns})

    affected = (
        conflicting.group_by(key)
        .agg([pl.col(c).n_unique().alias(f"_n_{c}") for c in unmodelled])
        .filter(
            pl.any_horizontal([pl.col(f"_n_{c}") > 1 for c in unmodelled])
        )
        .select(key)
    )
    out = towns.join(affected, on=key, how="inner").with_columns(
        pl.lit(",".join(unmodelled)).alias("conflicting_fields")
    )

    # Drop anything a reviewer has already signed off for this key and field.
    signed = {(lg, mid) for lg, mid, f in (reviewed or set()) if f in unmodelled}
    if signed:
        out = out.filter(
            ~pl.struct(key).map_elements(
                lambda s: (s["lg_code"], s["machiaza_id"]) in signed,
                return_dtype=pl.Boolean,
            )
        )
    if out.is_empty():
        return out
    log.error(
        "ABR rows share a natural key and disagree on fields this project does "
        "not model; every source row is retained and the build must not release",
        fields=unmodelled, rows=out.height,
    )
    return out


def build_canonical(
    towns: pl.DataFrame,
    abr_city: pl.DataFrame,
    ledger: IdentityLedger,
    snapshot_id: str,
    observed_from: str,
    municipality_lineage: dict[str, str] | None = None,
    city_snapshot_id: str | None = None,
) -> dict[str, pl.DataFrame]:
    """Produce address_entity, address, municipality* and the lineage rows."""
    with stage_context("canonical", "identity"):
        result = ledger.resolve(towns, snapshot_id, municipality_lineage)

    id_map = {
        (r.current_lg_code, r.current_machiaza_id): r.address_id
        for r in result.rows
        if r.entity_status == "active"
    }
    towns = towns.with_columns(
        pl.struct(["lg_code", "machiaza_id"])
        .map_elements(
            lambda s: id_map.get((s["lg_code"], s["machiaza_id"])),
            return_dtype=pl.Utf8,
        )
        .alias("address_id")
    )

    address = (
        towns.with_columns(
            [
                # The only place valid_from/valid_to come from a real source field.
                pl.col("efct_date").alias("valid_from"),
                pl.col("ablt_date").alias("valid_to"),
                pl.lit(observed_from).alias("observed_from"),
                pl.lit(None, dtype=pl.Utf8).alias("observed_to"),
                pl.lit("conservative").alias("normalization_profile"),
                pl.lit(snapshot_id).alias("source_snapshot_id"),
            ]
        )
        .select(
            [
                "address_id", "lg_code", "jis_city_code", "machiaza_id",
                *ADDRESS_TEXT_COLUMNS,
                "full_name_raw", "full_name_normalized", "normalization_profile",
                "rsdt_variant_count",
                "valid_from", "valid_to", "observed_from", "observed_to",
                "source_snapshot_id",
            ]
        )
        .sort("address_id")
    )

    entity = pl.DataFrame(
        [
            {
                "address_id": r.address_id,
                "genesis_lg_code": r.genesis_lg_code,
                "genesis_machiaza_id": r.genesis_machiaza_id,
                "entity_status": r.entity_status,
                "identity_match_rule": result.rule_by_address_id.get(r.address_id, "I5"),
                "first_observed_snapshot_id": r.first_observed_snapshot_id,
                "last_observed_snapshot_id": r.last_observed_snapshot_id,
                "created_at": observed_from,
                "retired_at": r.retired_at,
                "retire_reason": r.retire_reason,
            }
            for r in result.rows
        ],
        schema={
            "address_id": pl.Utf8, "genesis_lg_code": pl.Utf8,
            "genesis_machiaza_id": pl.Utf8, "entity_status": pl.Utf8,
            "identity_match_rule": pl.Utf8, "first_observed_snapshot_id": pl.Utf8,
            "last_observed_snapshot_id": pl.Utf8, "created_at": pl.Utf8,
            "retired_at": pl.Utf8, "retire_reason": pl.Utf8,
        },
    ).unique(subset=["address_id"], keep="first").sort("address_id")

    municipality, municipality_version = _build_municipality(
        abr_city, city_snapshot_id or snapshot_id, observed_from
    )

    lineage = pl.DataFrame(
        [
            {
                **row,
                "lineage_id": f"lin_{i:09d}",
                "effective_date": None,
                "observed_at": observed_from,
                "source_snapshot_id": snapshot_id,
            }
            for i, row in enumerate(
                sorted(
                    result.lineage,
                    key=lambda r: (
                        r.get("old_address_id") or "",
                        r.get("new_address_id") or "",
                        r["relation_type"],
                    ),
                )
            )
        ],
        schema={
            "old_address_id": pl.Utf8, "new_address_id": pl.Utf8,
            "relation_type": pl.Utf8, "evidence": pl.Utf8, "evidence_source": pl.Utf8,
            "lineage_id": pl.Utf8, "effective_date": pl.Utf8, "observed_at": pl.Utf8,
            "source_snapshot_id": pl.Utf8,
        },
    ).select(
        ["lineage_id", "old_address_id", "new_address_id", "relation_type",
         "effective_date", "observed_at", "evidence", "evidence_source",
         "source_snapshot_id"]
    ).sort("lineage_id")

    address_code = _build_address_code(address, snapshot_id, observed_from)

    log.info(
        "canonical layer built",
        addresses=address.height, entities=entity.height,
        municipalities=municipality.height, lineage=lineage.height,
    )
    return {
        "address_entity": entity,
        "address": address,
        "municipality": municipality,
        "municipality_version": municipality_version,
        "address_lineage": lineage,
        "address_code": address_code,
        "_identity_review": result.review_required,
    }


def _build_municipality(
    abr_city: pl.DataFrame, snapshot_id: str, observed_from: str
) -> tuple[pl.DataFrame, pl.DataFrame]:
    cols = ["lg_code", "jis_city_code", "pref", "county", "city", "ward",
            "pref_kana", "county_kana", "city_kana", "ward_kana",
            "pref_roma", "county_roma", "city_roma", "ward_roma"]
    df = abr_city
    for c in cols:
        if c not in df.columns:
            df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias(c))
        else:
            df = df.with_columns(_empty_to_null(c))
    df = df.select(cols).unique(subset=["lg_code"], keep="first").sort("lg_code")

    entity = df.select(
        [
            "lg_code",
            "jis_city_code",
            pl.lit(snapshot_id).alias("first_observed_snapshot_id"),
        ]
    )
    version = df.with_columns(
        [
            pl.format("mv_{}_{}", pl.col("lg_code"), pl.lit(observed_from))
            .alias("municipality_version_id"),
            pl.lit(None, dtype=pl.Utf8).alias("valid_from"),
            pl.lit(None, dtype=pl.Utf8).alias("valid_to"),
            pl.lit(observed_from).alias("observed_from"),
            pl.lit(None, dtype=pl.Utf8).alias("observed_to"),
            pl.lit(True).alias("is_current"),
            pl.lit(snapshot_id).alias("source_snapshot_id"),
        ]
    ).select(
        ["municipality_version_id", *cols, "valid_from", "valid_to",
         "observed_from", "observed_to", "is_current", "source_snapshot_id"]
    )
    return entity, version


def _build_address_code(
    address: pl.DataFrame, snapshot_id: str, observed_from: str
) -> pl.DataFrame:
    frames = []
    for code_type, col in [
        ("abr_machiaza_id", "machiaza_id"),
        ("abr_lg_code", "lg_code"),
        ("jis_city_code", "jis_city_code"),
    ]:
        frames.append(
            address.select(
                [
                    pl.col("address_id"),
                    pl.lit(code_type).alias("code_type"),
                    pl.col(col).alias("code_value"),
                    pl.col("valid_from"),
                    pl.col("valid_to"),
                    pl.lit(observed_from).alias("observed_from"),
                    pl.lit(None, dtype=pl.Utf8).alias("observed_to"),
                    pl.lit(snapshot_id).alias("source_snapshot_id"),
                ]
            )
        )
    return pl.concat(frames, how="vertical").sort(
        ["address_id", "code_type", "code_value"]
    )


NORMALIZATION_VERSION = NORMALIZATION_PROFILE_VERSION
