"""Postal crosswalk (docs/MATCHING_RULES.md §5).

The Digital Agency publishes an official 町字↔郵便番号 conversion table precisely
because the relationship is many-to-many, so V1 leads with that table (P1/P1x/P2)
and uses names only for what it does not cover (P4–P7).

Nothing here ever collapses several plausible candidates to one. Where the data
is ambiguous the ambiguity is what gets stored.
"""

from __future__ import annotations

import hashlib
import re

import polars as pl

from ..logging_setup import get_logger, stage_context
from ..normalize import normalize_conservative, normalize_postal_town
from ..sources.japanpost import KEN_ALL_COLUMNS
from .common import BuildContext, bridge_id, candidate_group_id, finalize_bridge

log = get_logger(__name__)

# Parentheticals that describe geography restrict the record's extent, so they
# must block an `exact` claim. The classification is an explicit pattern list,
# never a guess (docs/MATCHING_RULES.md §5).
_GEOGRAPHIC_HINTS = (
    "丁目", "番地", "番", "地割", "を除く", "以外", "以上", "以下",
    "その他", "字", "区域", "地区", "全域", "無番地",
)
_NON_GEOGRAPHIC_HINTS = ("次のビルを除く", "ビル", "階", "地階", "を含む")
_PAREN_RE = re.compile(r"[（(]([^）)]*)[）)]")


def classify_parenthetical(town: str | None) -> tuple[str, str | None]:
    """Return ``(class, parenthetical_raw)``.

    ``geographic`` means the postal record covers only part of the town, so P4
    (``exact``) is not admissible for it.
    """
    if not town:
        return "none", None
    m = _PAREN_RE.search(town)
    if not m:
        return "none", None
    raw, inner = m.group(0), m.group(1)
    if any(h in inner for h in _NON_GEOGRAPHIC_HINTS):
        return "non_geographic", raw
    if any(h in inner for h in _GEOGRAPHIC_HINTS):
        return "geographic", raw
    return "unknown", raw


def prepare_postal(ken_all: pl.DataFrame, snapshot_id: str, observed_from: str) -> dict[str, pl.DataFrame]:
    """Build postal_code_entity, postal_record and postal_record_version."""
    with stage_context("japanpost", "normalize"):
        df = ken_all.with_columns(
            [
                pl.col("town").alias("town_raw"),
                pl.col("town")
                .map_elements(normalize_postal_town, return_dtype=pl.Utf8)
                .alias("town_normalized"),
                pl.col("town")
                .map_elements(lambda t: classify_parenthetical(t)[0], return_dtype=pl.Utf8)
                .alias("parenthetical_class"),
                pl.col("town")
                .map_elements(lambda t: classify_parenthetical(t)[1], return_dtype=pl.Utf8)
                .alias("parenthetical_raw"),
            ]
        )
        # Deterministic surrogate id over the **complete** source row, plus an
        # ordinal for genuinely identical rows. Hashing a subset of fields made
        # two 明石市和坂 records that differ only in town_kana and
        # flag_has_chome collide, and unique() then discarded one of them:
        # 124,513 source rows became 124,512. One ken_all row must always
        # produce exactly one postal_record (docs/POLICY.md §5).
        id_fields = [c for c in KEN_ALL_COLUMNS if c in df.columns]
        df = df.with_columns(
            pl.concat_str(
                [pl.col(c).fill_null("\x00") for c in id_fields], separator="\x1f"
            ).alias("_row_key")
        )
        df = df.with_columns(
            pl.col("_row_key").cum_count().over("_row_key").alias("_dupe_ordinal")
        )
        n_exact_dupes = df.filter(pl.col("_dupe_ordinal") > 1).height
        if n_exact_dupes:
            log.warning(
                "Japan Post publishes byte-identical duplicate rows; each is kept "
                "and distinguished by an ordinal",
                rows=n_exact_dupes,
            )
        df = df.with_columns(
            pl.concat_str(
                [pl.col("_row_key"), pl.col("_dupe_ordinal").cast(pl.Utf8)],
                separator="#",
            )
            .map_elements(
                lambda s: "pr_"
                + hashlib.blake2s(s.encode("utf-8"), digest_size=12).hexdigest(),
                return_dtype=pl.Utf8,
            )
            .alias("postal_record_id")
        ).drop(["_row_key", "_dupe_ordinal"]).sort("postal_record_id")

        if df["postal_record_id"].n_unique() != df.height:
            raise ValueError("postal_record_id is not unique after construction")

        code_entity = (
            df.group_by("postal_code")
            .agg(pl.len().alias("record_count"))
            .with_columns(
                [
                    pl.lit(observed_from).alias("observed_from"),
                    pl.lit(None, dtype=pl.Utf8).alias("observed_to"),
                    pl.lit(snapshot_id).alias("source_snapshot_id"),
                ]
            )
            .sort("postal_code")
        )

        record = df.select(
            [
                "postal_record_id", "postal_code", "jis_city_code",
                pl.lit(snapshot_id).alias("first_observed_snapshot_id"),
            ]
        )

        version = df.with_columns(
            [
                pl.format("prv_{}_{}", pl.col("postal_record_id"), pl.lit(observed_from))
                .alias("postal_record_version_id"),
                # Japan Post states no effective date, so these stay NULL. A
                # download date is not a validity date (docs/POLICY.md §7).
                pl.lit(None, dtype=pl.Utf8).alias("valid_from"),
                pl.lit(None, dtype=pl.Utf8).alias("valid_to"),
                pl.lit(observed_from).alias("observed_from"),
                pl.lit(None, dtype=pl.Utf8).alias("observed_to"),
                pl.lit(True).alias("is_current"),
                pl.lit(snapshot_id).alias("source_snapshot_id"),
            ]
        ).select(
            [
                "postal_record_version_id", "postal_record_id", "postal_code",
                "jis_city_code", "old_postal_code_raw", "old_postal_code",
                "pref_kana", "city_kana", "town_kana", "pref", "city", "town",
                "town_raw", "town_normalized", "parenthetical_raw",
                "parenthetical_class",
                "flag_multi_code", "flag_koaza_banchi", "flag_has_chome",
                "flag_multi_town", "update_flag", "change_reason", "record_kind",
                "valid_from", "valid_to", "observed_from", "observed_to",
                "is_current", "source_snapshot_id",
            ]
        )

        log.info(
            "prepared postal records",
            records=record.height, codes=code_entity.height,
            geographic_parentheticals=df.filter(
                pl.col("parenthetical_class") == "geographic"
            ).height,
        )
        return {
            "postal_code_entity": code_entity,
            "postal_record": record,
            "postal_record_version": version,
        }


def collapse_conversion_edges(conversion: pl.DataFrame, key: list[str]) -> pl.DataFrame:
    """One row per semantic edge, with the published date range preserved.

    Observed nationally: ABR asserts some (町字, 郵便番号) pairs on several rows
    that differ only in ``koaza`` or in ``add_date`` / ``dlt_date``. They are the
    same edge, so emitting one bridge row per source row would only duplicate
    ids. Collapsing is safe *because* the edge is identical — but the dates are
    aggregated rather than picked:

    * ``valid_from`` takes the earliest stated ``add_date``.
    * ``valid_to`` is NULL if **any** row leaves ``dlt_date`` empty, because one
      open row means the edge is still open. Taking a max there would silently
      close a live mapping.

    ``source_row_count`` keeps the duplication visible instead of hiding it.
    """
    return (
        conversion.group_by(key)
        .agg(
            [
                pl.col("add_date").min().alias("add_date"),
                pl.when(pl.col("dlt_date").is_null().any())
                .then(None)
                .otherwise(pl.col("dlt_date").max())
                .alias("dlt_date"),
                pl.len().alias("source_row_count"),
            ]
        )
        .sort(key)
    )


def build_postal_code_bridge(
    address: pl.DataFrame,
    conversion: pl.DataFrame,
    postal_code_entity: pl.DataFrame,
    ctx: BuildContext,
) -> pl.DataFrame:
    """Rules P1 / P1x — the official ABR conversion table, code-level.

    Targets the 7-digit code, not a ken_all record: the publisher named a code,
    and one code appears in many records (docs/MATCHING_RULES.md §5).
    """
    with stage_context("postal", "bridge_code"):
        towns = collapse_conversion_edges(
            conversion.filter(pl.col("machiaza_id").is_not_null()),
            ["lg_code", "machiaza_id", "post_code"],
        )
        duplicated = towns.filter(pl.col("source_row_count") > 1).height
        if duplicated:
            log.info(
                "ABR asserts some town-postal edges on multiple rows; "
                "collapsed to one edge each with the date range preserved",
                edges=duplicated,
            )
        addr = address.select(["address_id", "lg_code", "machiaza_id"])
        joined = towns.join(addr, on=["lg_code", "machiaza_id"], how="inner")

        # Conversion rows whose ABR key is not in the current address table
        # (retired or otherwise absent towns) used to vanish at this inner join.
        # 27 official edges disappeared nationally, several carrying a
        # source-stated dlt_date — precisely the history the build exists to
        # keep. They are retained as source-side unresolved rows instead.
        orphans = towns.join(addr, on=["lg_code", "machiaza_id"], how="anti")
        if orphans.height:
            log.info(
                "official conversion rows reference ABR keys absent from the "
                "current address table; retained as unresolved",
                rows=orphans.height,
            )

        known_codes = set(postal_code_entity["postal_code"].to_list())
        joined = joined.with_columns(
            pl.col("post_code").is_in(list(known_codes)).alias("corroborated")
        )

        # Candidate count is per (address, direction): how many codes does this
        # town have? A town with several postal codes is a real many-to-many
        # fact, not an error, but it must not auto-accept.
        counts = joined.group_by("address_id").agg(pl.len().alias("candidate_count"))
        joined = joined.join(counts, on="address_id", how="left")

        df = joined.select(
            [
                pl.struct(["address_id", "post_code"])
                .map_elements(
                    lambda s: bridge_id(
                        "bridge_address_postal_code", s["address_id"], s["post_code"]
                    ),
                    return_dtype=pl.Utf8,
                )
                .alias("bridge_id"),
                pl.col("address_id"),
                pl.col("post_code").alias("target_id"),
                pl.lit("address_to_postal_code").alias("direction"),
                pl.when(pl.col("corroborated"))
                .then(pl.lit("equivalent"))
                .otherwise(pl.lit("candidate"))
                .alias("relation_type"),
                pl.lit("direct_code").alias("match_method"),
                pl.when(pl.col("corroborated"))
                .then(pl.lit("P1"))
                .otherwise(pl.lit("P1x"))
                .alias("matching_rule_id"),
                pl.when(pl.col("corroborated"))
                .then(pl.lit(0.99))
                .otherwise(pl.lit(0.70))
                .alias("confidence"),
                pl.when(pl.col("candidate_count") > 1)
                .then(
                    pl.col("address_id").map_elements(
                        lambda a: candidate_group_id("bridge_address_postal_code", a),
                        return_dtype=pl.Utf8,
                    )
                )
                .otherwise(None)
                .alias("candidate_group_id"),
                pl.col("candidate_count"),
                # Source-stated dates: the conversion table publishes them.
                pl.col("add_date").alias("valid_from"),
                pl.col("dlt_date").alias("valid_to"),
                pl.lit(None, dtype=pl.Utf8).alias("mismatch_note"),
            ]
        )
        if orphans.height:
            orphan_df = orphans.select(
                [
                    pl.struct(["lg_code", "machiaza_id", "post_code"])
                    .map_elements(
                        lambda s: bridge_id(
                            "bridge_address_postal_code", None,
                            f"{s['lg_code']}:{s['machiaza_id']}", s["post_code"],
                        ),
                        return_dtype=pl.Utf8,
                    )
                    .alias("bridge_id"),
                    pl.lit(None, dtype=pl.Utf8).alias("address_id"),
                    pl.col("post_code").alias("target_id"),
                    pl.lit("postal_code_to_address").alias("direction"),
                    pl.lit("unresolved").alias("relation_type"),
                    pl.lit("unresolved").alias("match_method"),
                    pl.lit("P7").alias("matching_rule_id"),
                    pl.lit(0.0).alias("confidence"),
                    pl.lit(None, dtype=pl.Utf8).alias("candidate_group_id"),
                    pl.lit(0, dtype=pl.Int64).alias("candidate_count"),
                    pl.col("add_date").alias("valid_from"),
                    pl.col("dlt_date").alias("valid_to"),
                    pl.format(
                        "ABR conversion row references {}:{}, which is not a current "
                        "ABR town",
                        pl.col("lg_code"), pl.col("machiaza_id"),
                    ).alias("mismatch_note"),
                ]
            )
            # pl.len() yields UInt32 upstream; align dtypes explicitly rather
            # than relying on a relaxed concat.
            df = pl.concat(
                [
                    df.with_columns(pl.col("candidate_count").cast(pl.Int64)),
                    orphan_df.with_columns(pl.col("candidate_count").cast(pl.Int64)),
                ],
                how="vertical",
            )

        out = finalize_bridge(df, ctx, ["address_id", "target_id"])
        log.info(
            "built address->postal_code bridge",
            rows=out.height, retained_orphans=orphans.height,
        )
        return out


def build_municipality_postal_bridge(
    conversion: pl.DataFrame,
    postal_version: pl.DataFrame,
    municipality: pl.DataFrame,
    ctx: BuildContext,
) -> pl.DataFrame:
    """Rules P2 / P3 — statements about a municipality, never about a town."""
    with stage_context("postal", "bridge_municipality"):
        lg_by_jis = dict(
            zip(
                municipality["jis_city_code"].to_list(),
                municipality["lg_code"].to_list(),
                strict=False,
            )
        )

        p2 = collapse_conversion_edges(
            conversion.filter(pl.col("machiaza_id").is_null()),
            ["lg_code", "post_code"],
        ).select(
            [
                pl.col("lg_code"),
                pl.col("post_code").alias("target_id"),
                pl.lit("P2").alias("matching_rule_id"),
                pl.lit("direct_code").alias("match_method"),
                pl.col("add_date").alias("valid_from"),
                pl.col("dlt_date").alias("valid_to"),
            ]
        )

        specials = postal_version.filter(pl.col("record_kind") != "town")
        p3 = specials.select(
            [
                pl.col("jis_city_code")
                .map_elements(lambda j: lg_by_jis.get(j), return_dtype=pl.Utf8)
                .alias("lg_code"),
                pl.col("postal_record_id").alias("target_id"),
                pl.lit("P3").alias("matching_rule_id"),
                pl.lit("official_area_rule").alias("match_method"),
                pl.lit(None, dtype=pl.Utf8).alias("valid_from"),
                pl.lit(None, dtype=pl.Utf8).alias("valid_to"),
            ]
        ).filter(pl.col("lg_code").is_not_null())

        both = pl.concat([p2, p3], how="vertical").unique(
            subset=["lg_code", "target_id", "matching_rule_id"], keep="first"
        )
        counts = both.group_by("lg_code").agg(pl.len().alias("candidate_count"))
        both = both.join(counts, on="lg_code", how="left")

        df = both.with_columns(
            [
                pl.struct(["lg_code", "target_id", "matching_rule_id"])
                .map_elements(
                    lambda s: bridge_id(
                        "bridge_municipality_postal",
                        s["lg_code"], s["target_id"], s["matching_rule_id"],
                    ),
                    return_dtype=pl.Utf8,
                )
                .alias("bridge_id"),
                pl.lit("municipality_to_postal").alias("direction"),
                pl.lit("parent").alias("relation_type"),
                pl.lit(0.99).alias("confidence"),
                pl.when(pl.col("candidate_count") > 1)
                .then(
                    pl.col("lg_code").map_elements(
                        lambda a: candidate_group_id("bridge_municipality_postal", a),
                        return_dtype=pl.Utf8,
                    )
                )
                .otherwise(None)
                .alias("candidate_group_id"),
            ]
        )
        out = finalize_bridge(df, ctx, ["lg_code", "target_id"])
        log.info("built municipality->postal bridge", rows=out.height)
        return out


def build_postal_record_bridge(
    address: pl.DataFrame,
    postal_version: pl.DataFrame,
    covered_address_ids: set[str],
    ctx: BuildContext,
) -> pl.DataFrame:
    """Rules P4 / P4p / P5 / P6a / P6b / P7 — record-level name matching.

    Only ordinary ``town`` records participate; specials went to the
    municipality bridge. Every unmatched record on either side is retained with
    ``relation_type='unresolved'`` and the opposite endpoint NULL, so nothing is
    dropped to improve a match rate (docs/POLICY.md §5).
    """
    with stage_context("postal", "bridge_record"):
        towns = postal_version.filter(pl.col("record_kind") == "town").select(
            ["postal_record_id", "jis_city_code", "town_normalized",
             "parenthetical_class", "town_raw"]
        )
        addr = address.select(
            ["address_id", "jis_city_code", "full_name_normalized", "oaza_cho"]
        ).with_columns(
            pl.col("oaza_cho")
            .map_elements(normalize_conservative, return_dtype=pl.Utf8)
            .alias("oaza_normalized")
        )

        rows: list[dict] = []
        matched_records: set[str] = set()
        matched_addresses: set[str] = set()

        # --- exact normalized-name join, evaluated in BOTH directions.
        exact = towns.join(
            addr,
            left_on=["jis_city_code", "town_normalized"],
            right_on=["jis_city_code", "full_name_normalized"],
            how="inner",
        )
        per_record = dict(
            exact.group_by("postal_record_id").agg(pl.len().alias("n")).iter_rows()
        )
        per_address = dict(
            exact.group_by("address_id").agg(pl.len().alias("n")).iter_rows()
        )

        for r in exact.iter_rows(named=True):
            rid, aid = r["postal_record_id"], r["address_id"]
            n_rec, n_addr = per_record.get(rid, 1), per_address.get(aid, 1)
            pclass = r["parenthetical_class"]

            if n_rec == 1 and n_addr == 1:
                if pclass in ("none", "non_geographic"):
                    rule, rel, conf = "P4", "exact", 0.97
                else:
                    # A geographic parenthetical means the record covers part of
                    # the town, so it cannot be `exact`.
                    rule, rel, conf = "P4p", "overlap", 0.70
                group, count = None, 1
            elif n_rec > 1:
                rule, rel, conf = "P6a", "ambiguous", 0.50
                group = candidate_group_id("bridge_address_postal", "rec", rid)
                count = n_rec
            else:
                rule, rel, conf = "P6b", "ambiguous", 0.50
                group = candidate_group_id("bridge_address_postal", "addr", aid)
                count = n_addr

            rows.append(
                {
                    "bridge_id": bridge_id("bridge_address_postal", aid, rid, rule),
                    "address_id": aid, "target_id": rid,
                    "direction": "postal_to_address",
                    "relation_type": rel, "match_method": "normalized_name",
                    "matching_rule_id": rule, "confidence": conf,
                    "candidate_group_id": group, "candidate_count": count,
                    "mismatch_note": None,
                }
            )
            matched_records.add(rid)
            matched_addresses.add(aid)

        # --- P5: the 西新宿 case. The postal town names the 大字; ABR has it split
        # into N chome. Emit one row per chome, never a single winner.
        unmatched_towns = towns.filter(~pl.col("postal_record_id").is_in(list(matched_records)))
        chome = unmatched_towns.join(
            addr.filter(pl.col("oaza_normalized").is_not_null()),
            left_on=["jis_city_code", "town_normalized"],
            right_on=["jis_city_code", "oaza_normalized"],
            how="inner",
        )
        per_record5 = dict(
            chome.group_by("postal_record_id").agg(pl.len().alias("n")).iter_rows()
        )
        for r in chome.iter_rows(named=True):
            rid, aid = r["postal_record_id"], r["address_id"]
            n = per_record5.get(rid, 1)
            rows.append(
                {
                    "bridge_id": bridge_id("bridge_address_postal", aid, rid, "P5"),
                    "address_id": aid, "target_id": rid,
                    "direction": "postal_to_address",
                    "relation_type": "parent", "match_method": "parent_child",
                    "matching_rule_id": "P5", "confidence": 0.90,
                    # The true count, not an inflated one. An earlier version
                    # forced this to >= 2 to block auto-accept, which made
                    # candidate_count disagree with the number of rows actually
                    # in the group. Auto-accept is already impossible here:
                    # relation_type='parent' is not an accepted relation and
                    # 0.90 is below the 0.98 threshold.
                    "candidate_group_id": (
                        candidate_group_id("bridge_address_postal", "chome", rid)
                        if n > 1 else None
                    ),
                    "candidate_count": n,
                    "mismatch_note": None,
                }
            )
            matched_records.add(rid)
            matched_addresses.add(aid)

        # --- P7 from the postal side: an unmatched Japan Post record.
        for r in towns.filter(
            ~pl.col("postal_record_id").is_in(list(matched_records))
        ).iter_rows(named=True):
            rid = r["postal_record_id"]
            rows.append(
                {
                    "bridge_id": bridge_id("bridge_address_postal", None, rid, "P7"),
                    "address_id": None, "target_id": rid,
                    "direction": "postal_to_address",
                    "relation_type": "unresolved", "match_method": "unresolved",
                    "matching_rule_id": "P7", "confidence": 0.0,
                    "candidate_group_id": None, "candidate_count": 0,
                    "mismatch_note": "no ABR town matched this postal record",
                }
            )

        # --- P7 from the address side: a town with no postal evidence at all,
        # neither from the official conversion table nor by name.
        for aid in sorted(
            set(addr["address_id"].to_list()) - matched_addresses - covered_address_ids
        ):
            rows.append(
                {
                    "bridge_id": bridge_id("bridge_address_postal", aid, None, "P7"),
                    "address_id": aid, "target_id": None,
                    "direction": "address_to_postal",
                    "relation_type": "unresolved", "match_method": "unresolved",
                    "matching_rule_id": "P7", "confidence": 0.0,
                    "candidate_group_id": None, "candidate_count": 0,
                    "mismatch_note": "no postal record matched this address",
                }
            )

        df = pl.DataFrame(
            rows,
            schema={
                "bridge_id": pl.Utf8, "address_id": pl.Utf8, "target_id": pl.Utf8,
                "direction": pl.Utf8, "relation_type": pl.Utf8,
                "match_method": pl.Utf8, "matching_rule_id": pl.Utf8,
                "confidence": pl.Float64, "candidate_group_id": pl.Utf8,
                "candidate_count": pl.Int64, "mismatch_note": pl.Utf8,
            },
        )
        out = finalize_bridge(df, ctx, ["target_id", "address_id"])
        log.info(
            "built address<->postal record bridge",
            rows=out.height,
            by_rule=dict(out.group_by("matching_rule_id").agg(pl.len()).iter_rows()),
        )
        return out
