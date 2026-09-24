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

# どのブリッジがどの表を指しているか。これが列名になる
# （docs/BRIDGE_ENDPOINT_MIGRATION.md A1）。
#
# 多態な `target_id` 1列をやめた理由は3つ、いずれも実測である:
#
# * 外部キーを付けられない —— 行によって指す表が変わる列に `REFERENCES` は書けない。
#   v1.3.0 で20テーブルに外部キーが付いたあと、ここが唯一残った穴だった
# * どの表に join するのか列が言っていない —— 出荷しているクエリ例が
#   `b.target_id AS numbering_area_code` と別名を付けて読み手に教えていた
# * `bridge_municipality_postal` は**表の内側で**多態だった —— P2 が郵便番号 8,207行、
#   P3 が postal_record_id 1,910行を同じ列に入れていた。知らずに join すると 1,910行が
#   静かに落ちる。この表は2つに分ける（A2）
#
# 1か所に置くのは、CHECK・外部キー・不変条件テストがこの同じ表を読むためである。
# 7本それぞれに手書きの対応を持たせると、誰かが拡張を忘れる一覧が3つ増える。
BRIDGE_ENDPOINTS: dict[str, str] = {
    "bridge_address_postal_code": "postal_code",
    "bridge_address_postal": "postal_record_id",
    "bridge_address_mlit": "mlit_record_id",
    "bridge_address_telephone": "numbering_area_code",
    "bridge_municipality_postal_code": "postal_code",
    "bridge_municipality_postal": "postal_record_id",
    "bridge_municipality_telephone": "numbering_area_code",
}

# 主語の側。A9 のとおり、使わないほうの列も残す（v2.0.0 では落とさない）。
BRIDGE_SUBJECTS: dict[str, str] = {
    name: ("lg_code" if name.startswith("bridge_municipality_") else "address_id")
    for name in BRIDGE_ENDPOINTS
}

ENDPOINT_PLACEHOLDER = "target_id"

BRIDGE_COLUMNS: dict[str, pl.DataType] = {
    "bridge_id": pl.Utf8,
    "address_id": pl.Utf8,
    "lg_code": pl.Utf8,
    ENDPOINT_PLACEHOLDER: pl.Utf8,
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


def endpoint_of(bridge: str) -> str:
    """そのブリッジの端点列名。未登録のブリッジは黙って通さない。

    知らない名前に `target_id` を返すと、新しいブリッジが多態な列を持って出荷される。
    レジストリに足すのを忘れたことが、出荷物ではなく例外として現れるようにする。
    """
    try:
        return BRIDGE_ENDPOINTS[bridge]
    except KeyError:
        raise KeyError(
            f"{bridge} is not in BRIDGE_ENDPOINTS; a bridge must name the table its "
            "endpoint points at (docs/BRIDGE_ENDPOINT_MIGRATION.md A1)"
        ) from None


def bridge_columns(bridge: str) -> dict[str, pl.DataType]:
    """共通スキーマの端点列を、そのブリッジの名前にしたもの。

    列の**順序**も共通のままにしてある。30列の並びは `docs/schema.sql` が公開している
    定義そのもので、端点の位置だけが名前を変える。
    """
    endpoint = endpoint_of(bridge)
    return {
        (endpoint if name == ENDPOINT_PLACEHOLDER else name): dtype
        for name, dtype in BRIDGE_COLUMNS.items()
    }


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
    df: pl.DataFrame, ctx: BuildContext, sort_keys: list[str], bridge: str
) -> pl.DataFrame:
    """Fill defaults, apply the gate, enforce column order, sort deterministically.

    ``bridge`` はレジストリを引くための名前で、端点列がどう呼ばれるかを決める。
    呼び出し側は端点列を**その名前で**渡す（`target_id` ではない）。
    """
    schema = bridge_columns(bridge)
    endpoint = endpoint_of(bridge)
    if df.is_empty():
        return pl.DataFrame(schema=schema)

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
        endpoint: pl.lit(None, dtype=pl.Utf8),
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

    df = df.select([pl.col(name).cast(dtype) for name, dtype in schema.items()])
    # Explicit total-order sort: Polars joins are not order-stable, and an
    # unstable order would break byte-level reproducibility (spec §46).
    return df.sort([*sort_keys, "bridge_id"])


def assert_bridge_invariants(df: pl.DataFrame, name: str) -> list[str]:
    """Return a list of violated invariants (docs/TEST_STRATEGY.md §3).

    端点列はレジストリから引く。リテラルの ``target_id`` を書いていると、列名が
    変わった瞬間に**何も見ない検査**になって緑のまま通る。
    """
    problems: list[str] = []
    if df.is_empty():
        return problems
    endpoint = endpoint_of(name)
    if endpoint not in df.columns:
        return [f"{name}: endpoint column {endpoint} is absent"]

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
        & pl.col(endpoint).is_not_null()
        & pl.col("address_id").is_not_null(),
        "resolved without a target": (pl.col("relation_type") != "unresolved")
        & pl.col(endpoint).is_null(),
        "row with no endpoint at all": pl.col("address_id").is_null()
        & pl.col("lg_code").is_null()
        & pl.col(endpoint).is_null(),
    }
    for label, expr in checks.items():
        n = count(expr)
        if n:
            problems.append(f"{name}: {label} ({n} rows)")

    dupes = df.height - df["bridge_id"].n_unique()
    if dupes:
        problems.append(f"{name}: duplicate bridge_id ({dupes} rows)")
    return problems
