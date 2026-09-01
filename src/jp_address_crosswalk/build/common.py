"""Shared bridge construction (docs/DATA_MODEL.md §4, docs/DB_SCHEMA.md §5).

Two things every bridge row must satisfy, enforced here so no individual rule
has to remember them:

* ``bridge_id`` is a hash of the semantic key, never a counter, so a rebuild in
  a different execution order produces identical ids (spec §46).
* The auto-accept gate is the full documented conjunction. It is applied in one
  place and mirrored as DDL ``CHECK`` constraints, so neither layer can drift
  from the other.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import polars as pl

BRIDGE_COLUMNS: dict[str, pl.DataType] = {
    "bridge_id": pl.Utf8,
    "address_id": pl.Utf8,
    "lg_code": pl.Utf8,
    "target_id": pl.Utf8,
    "direction": pl.Utf8,
    "relation_type": pl.Utf8,
    "match_method": pl.Utf8,
    "matching_rule_id": pl.Utf8,
    "confidence": pl.Float64,
    "candidate_group_id": pl.Utf8,
    "candidate_count": pl.Int64,
    "candidate_count_is_complete": pl.Boolean,
    "is_unique_match": pl.Boolean,
    "verification_status": pl.Utf8,
    "override_stale": pl.Boolean,
    "derivation": pl.Utf8,
    "coverage_type": pl.Utf8,
    "normalization_profile": pl.Utf8,
    "mismatch_note": pl.Utf8,
    "valid_from": pl.Utf8,
    "valid_to": pl.Utf8,
    "observed_from": pl.Utf8,
    "observed_to": pl.Utf8,
    "is_current": pl.Boolean,
    "match_run_id": pl.Utf8,
    "source_snapshot_id": pl.Utf8,
    "matching_rule_version": pl.Utf8,
    "normalization_profile_version": pl.Utf8,
    "created_at": pl.Utf8,
    "updated_at": pl.Utf8,
}

AUTO_ACCEPT_RELATIONS = ("exact", "equivalent")
AUTO_ACCEPT_MIN_CONFIDENCE = 0.98


@dataclass(frozen=True)
class BuildContext:
    """Everything a rule needs that is constant for the whole run."""

    match_run_id: str
    snapshot_id: str
    observed_from: str
    built_at: str
    matching_rule_version: str
    normalization_profile_version: str


def bridge_id(bridge: str, *parts: str | None) -> str:
    """Content-addressed id for one semantic edge.

    A missing endpoint and an empty-string endpoint are different states — the
    first means "no counterpart", the second is a value — so NULL is encoded
    distinctly rather than folded to "".
    """
    key = bridge + "|" + "|".join("<null>" if p is None else str(p) for p in parts)
    return "brg_" + hashlib.blake2s(key.encode("utf-8"), digest_size=12).hexdigest()


def candidate_group_id(bridge: str, *parts: str | None) -> str:
    key = bridge + "|grp|" + "|".join("<null>" if p is None else str(p) for p in parts)
    return "grp_" + hashlib.blake2s(key.encode("utf-8"), digest_size=10).hexdigest()


def expr_verification_status() -> pl.Expr:
    """The auto-accept gate as a single Polars expression.

    A conjunction on purpose: no individual condition can carry a row through,
    and a high confidence score cannot override ``candidate_count > 1``
    (docs/MATCHING_RULES.md §4).
    """
    gate = (
        (pl.col("confidence") >= AUTO_ACCEPT_MIN_CONFIDENCE)
        & (pl.col("candidate_count") == 1)
        & pl.col("candidate_count_is_complete")
        & pl.col("is_unique_match")
        & ~pl.col("override_stale")
        & pl.col("relation_type").is_in(list(AUTO_ACCEPT_RELATIONS))
    )
    return pl.when(gate).then(pl.lit("auto")).otherwise(pl.lit("review_required"))


def finalize_bridge(
    df: pl.DataFrame, ctx: BuildContext, sort_keys: list[str]
) -> pl.DataFrame:
    """Fill defaults, apply the gate, enforce column order, sort deterministically."""
    if df.is_empty():
        return pl.DataFrame(schema=BRIDGE_COLUMNS)

    defaults: dict[str, pl.Expr] = {
        "candidate_count_is_complete": pl.lit(True),
        "override_stale": pl.lit(False),
        "derivation": pl.lit(None, dtype=pl.Utf8),
        "coverage_type": pl.lit(None, dtype=pl.Utf8),
        "mismatch_note": pl.lit(None, dtype=pl.Utf8),
        "normalization_profile": pl.lit("conservative"),
        "valid_from": pl.lit(None, dtype=pl.Utf8),
        "valid_to": pl.lit(None, dtype=pl.Utf8),
        "observed_to": pl.lit(None, dtype=pl.Utf8),
        "is_current": pl.lit(True),
        "address_id": pl.lit(None, dtype=pl.Utf8),
        "lg_code": pl.lit(None, dtype=pl.Utf8),
        "target_id": pl.lit(None, dtype=pl.Utf8),
        "candidate_group_id": pl.lit(None, dtype=pl.Utf8),
    }
    for name, expr in defaults.items():
        if name not in df.columns:
            df = df.with_columns(expr.alias(name))

    df = df.with_columns(
        [
            pl.lit(ctx.observed_from).alias("observed_from"),
            pl.lit(ctx.match_run_id).alias("match_run_id"),
            pl.lit(ctx.snapshot_id).alias("source_snapshot_id"),
            pl.lit(ctx.matching_rule_version).alias("matching_rule_version"),
            pl.lit(ctx.normalization_profile_version).alias(
                "normalization_profile_version"
            ),
            pl.lit(ctx.built_at).alias("created_at"),
            pl.lit(ctx.built_at).alias("updated_at"),
            (pl.col("candidate_count") == 1).alias("is_unique_match"),
        ]
    )
    df = df.with_columns(
        pl.when(pl.col("candidate_count") > 1)
        .then(pl.col("candidate_group_id"))
        .otherwise(None)
        .alias("candidate_group_id")
    )
    # verification_status is derived last so it always reflects the final values.
    df = df.with_columns(expr_verification_status().alias("verification_status"))

    df = df.select(
        [pl.col(name).cast(dtype) for name, dtype in BRIDGE_COLUMNS.items()]
    )
    # Explicit total-order sort: Polars joins are not order-stable, and an
    # unstable order would break byte-level reproducibility (spec §46).
    return df.sort([*sort_keys, "bridge_id"])


def assert_bridge_invariants(df: pl.DataFrame, name: str) -> list[str]:
    """Return a list of violated invariants (docs/TEST_STRATEGY.md §3)."""
    problems: list[str] = []
    if df.is_empty():
        return problems

    def count(expr: pl.Expr) -> int:
        return df.filter(expr).height

    checks = {
        "confidence out of [0,1]": (pl.col("confidence") < 0) | (pl.col("confidence") > 1),
        "auto with candidate_count>1": (pl.col("verification_status") == "auto")
        & (pl.col("candidate_count") > 1),
        "auto with low confidence": (pl.col("verification_status") == "auto")
        & (pl.col("confidence") < AUTO_ACCEPT_MIN_CONFIDENCE),
        "auto with non-equivalent relation": (pl.col("verification_status") == "auto")
        & ~pl.col("relation_type").is_in(list(AUTO_ACCEPT_RELATIONS)),
        "auto with stale override": (pl.col("verification_status") == "auto")
        & pl.col("override_stale"),
        "auto with incomplete candidate set": (pl.col("verification_status") == "auto")
        & ~pl.col("candidate_count_is_complete"),
        "unique_match with candidate_count>1": pl.col("is_unique_match")
        & (pl.col("candidate_count") > 1),
        "ambiguous without candidate_group_id": (pl.col("candidate_count") > 1)
        & pl.col("candidate_group_id").is_null(),
        "unresolved with a target": (pl.col("relation_type") == "unresolved")
        & pl.col("target_id").is_not_null()
        & pl.col("address_id").is_not_null(),
        "resolved without a target": (pl.col("relation_type") != "unresolved")
        & pl.col("target_id").is_null(),
        "row with no endpoint at all": pl.col("address_id").is_null()
        & pl.col("lg_code").is_null()
        & pl.col("target_id").is_null(),
    }
    for label, expr in checks.items():
        n = count(expr)
        if n:
            problems.append(f"{name}: {label} ({n} rows)")

    dupes = df.height - df["bridge_id"].n_unique()
    if dupes:
        problems.append(f"{name}: duplicate bridge_id ({dupes} rows)")
    return problems
