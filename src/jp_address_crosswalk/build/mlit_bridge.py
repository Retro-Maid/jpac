"""MLIT crosswalk (docs/MATCHING_RULES.md §6).

``mlit_code[0:5]`` looks like ``jis_city_code`` and ``mlit_code[5:12]`` looks
like ``machiaza_id``. That resemblance is a *hint*, never a join: M1 requires the
normalized names to agree as well, and a code match with a name disagreement is
M2 — ``equivalent`` at 0.90 and flagged for review, not ``exact``.
"""

from __future__ import annotations

import hashlib

import polars as pl

from ..logging_setup import get_logger, stage_context
from ..normalize import normalize_conservative, normalize_mlit_relaxed
from .common import BuildContext, bridge_id, candidate_group_id, finalize_bridge

log = get_logger(__name__)


def prepare_mlit(
    isj: pl.DataFrame, snapshot_id: str, observed_from: str, isj_version: str
) -> dict[str, pl.DataFrame]:
    with stage_context("mlit", "normalize"):
        df = isj.with_columns(
            pl.col("town_name_raw")
            .map_elements(normalize_mlit_relaxed, return_dtype=pl.Utf8)
            .alias("town_name_normalized")
        )
        if "isj_version" not in df.columns:
            df = df.with_columns(pl.lit(isj_version).alias("isj_version"))
        # The id covers the whole source row, not just mlit_code. Five codes in
        # the national data appear twice with different names AND different
        # coordinates — 232040077003 is published as both 塩草が丘4丁目 and
        # 塩草が丘三丁目 — so keying on the code alone silently discarded one of
        # each pair (191,106 source rows became 191,101). A publisher
        # inconsistency is data to record, not a row to drop (docs/POLICY.md §5).
        id_fields = [
            c for c in [
                "mlit_code", "jis_city_code", "pref_code", "pref_name", "city_name",
                "town_name_raw", "latitude", "longitude", "source_material_code",
                "aza_class_code", "fiscal_year",
            ] if c in df.columns
        ]
        df = df.with_columns(
            pl.concat_str(
                [pl.col(c).cast(pl.Utf8).fill_null("") for c in id_fields],
                separator="",
            ).alias("_row_key")
        )
        df = df.with_columns(
            pl.col("_row_key").cum_count().over("_row_key").alias("_ordinal")
        )
        df = df.with_columns(
            pl.concat_str([pl.col("_row_key"), pl.col("_ordinal").cast(pl.Utf8)],
                          separator="#")
            .map_elements(
                lambda s: "ml_"
                + hashlib.blake2s(s.encode("utf-8"), digest_size=12).hexdigest(),
                return_dtype=pl.Utf8,
            )
            .alias("mlit_record_id")
        ).drop(["_row_key", "_ordinal"]).sort("mlit_record_id")

        dupe_codes = (
            df.group_by("mlit_code").len().filter(pl.col("len") > 1)
        )
        if dupe_codes.height:
            log.warning(
                "MLIT publishes the same 大字町丁目コード on more than one row with "
                "differing attributes; every row is kept and the code becomes an "
                "ambiguous match target",
                codes=dupe_codes.height,
                sample=dupe_codes.head(3)["mlit_code"].to_list(),
            )

        if df["mlit_record_id"].n_unique() != df.height:
            raise ValueError("mlit_record_id is not unique after construction")

        entity = df.select(
            [
                "mlit_record_id", "mlit_code", "jis_city_code",
                pl.lit(snapshot_id).alias("first_observed_snapshot_id"),
            ]
        )
        version = df.with_columns(
            [
                pl.format("mlv_{}_{}", pl.col("mlit_record_id"), pl.lit(observed_from))
                .alias("mlit_town_version_id"),
                pl.lit(observed_from).alias("observed_from"),
                pl.lit(None, dtype=pl.Utf8).alias("observed_to"),
                pl.lit(True).alias("is_current"),
                pl.lit(snapshot_id).alias("source_snapshot_id"),
            ]
        ).select(
            [
                "mlit_town_version_id", "mlit_record_id", "mlit_code", "jis_city_code",
                "pref_code", "pref_name", "city_name", "town_name_raw",
                "town_name_normalized", "latitude", "longitude",
                "source_material_code", "aza_class_code", "fiscal_year", "isj_version",
                "observed_from", "observed_to", "is_current", "source_snapshot_id",
            ]
        )
        log.info("prepared MLIT towns", rows=entity.height)
        return {"mlit_town": entity, "mlit_town_version": version}


def build_mlit_bridge(
    address: pl.DataFrame, mlit_version: pl.DataFrame, ctx: BuildContext
) -> pl.DataFrame:
    with stage_context("mlit", "bridge"):
        addr = address.select(
            ["address_id", "jis_city_code", "machiaza_id", "full_name_normalized"]
        ).with_columns(
            # MLIT composes 大字・町丁目 into one string while ABR splits it, so the
            # comparison happens under the relaxed profile on the ABR side too.
            pl.col("full_name_normalized")
            .map_elements(normalize_mlit_relaxed, return_dtype=pl.Utf8)
            .alias("name_for_mlit")
        )
        mlit = mlit_version.select(
            ["mlit_record_id", "mlit_code", "jis_city_code", "town_name_normalized"]
        ).with_columns(pl.col("mlit_code").str.slice(5, 7).alias("aza_code"))

        rows: list[dict] = []
        matched_addresses: set[str] = set()
        matched_mlit: set[str] = set()

        # --- M1 / M2: code structure agrees. Name decides which.
        code_join = addr.join(
            mlit,
            left_on=["jis_city_code", "machiaza_id"],
            right_on=["jis_city_code", "aza_code"],
            how="inner",
        )
        for r in code_join.iter_rows(named=True):
            same_name = r["name_for_mlit"] == r["town_name_normalized"]
            rule = "M1" if same_name else "M2"
            rows.append(
                {
                    "bridge_id": bridge_id(
                        "bridge_address_mlit", r["address_id"], r["mlit_record_id"], rule
                    ),
                    "address_id": r["address_id"],
                    "target_id": r["mlit_record_id"],
                    "direction": "address_to_mlit",
                    "relation_type": "exact" if same_name else "equivalent",
                    "match_method": "composite" if same_name else "direct_code",
                    "matching_rule_id": rule,
                    "confidence": 1.00 if same_name else 0.90,
                    "candidate_group_id": None,
                    "candidate_count": 1,
                    "mismatch_note": None
                    if same_name
                    else f"ABR '{r['name_for_mlit']}' vs MLIT '{r['town_name_normalized']}'",
                    "force_review": not same_name,
                }
            )
            matched_addresses.add(r["address_id"])
            matched_mlit.add(r["mlit_record_id"])

        # --- M3 / M4: no code match; fall back to name, unique in BOTH directions.
        rest_addr = addr.filter(~pl.col("address_id").is_in(list(matched_addresses)))
        rest_mlit = mlit.filter(~pl.col("mlit_record_id").is_in(list(matched_mlit)))
        name_join = rest_addr.join(
            rest_mlit,
            left_on=["jis_city_code", "name_for_mlit"],
            right_on=["jis_city_code", "town_name_normalized"],
            how="inner",
        )
        per_addr = dict(
            name_join.group_by("address_id").agg(pl.len().alias("n")).iter_rows()
        )
        per_mlit = dict(
            name_join.group_by("mlit_record_id").agg(pl.len().alias("n")).iter_rows()
        )
        for r in name_join.iter_rows(named=True):
            aid, mid = r["address_id"], r["mlit_record_id"]
            na, nm = per_addr.get(aid, 1), per_mlit.get(mid, 1)
            if na == 1 and nm == 1:
                rule, rel, conf, group, count = "M3", "exact", 0.97, None, 1
            else:
                rule, rel, conf = "M4", "ambiguous", 0.50
                count = max(na, nm)
                group = candidate_group_id(
                    "bridge_address_mlit", aid if na >= nm else mid
                )
            rows.append(
                {
                    "bridge_id": bridge_id("bridge_address_mlit", aid, mid, rule),
                    "address_id": aid, "target_id": mid,
                    "direction": "address_to_mlit",
                    "relation_type": rel, "match_method": "normalized_name",
                    "matching_rule_id": rule, "confidence": conf,
                    "candidate_group_id": group, "candidate_count": count,
                    "mismatch_note": None, "force_review": False,
                }
            )
            matched_addresses.add(aid)
            matched_mlit.add(mid)

        # --- M5 both ways. Unmatched rows are kept, not deleted.
        for aid in sorted(set(addr["address_id"].to_list()) - matched_addresses):
            rows.append(_unresolved("bridge_address_mlit", aid, None,
                                    "address_to_mlit", "M5",
                                    "no MLIT record matched this address"))
        for mid in sorted(set(mlit["mlit_record_id"].to_list()) - matched_mlit):
            rows.append(_unresolved("bridge_address_mlit", None, mid,
                                    "mlit_to_address", "M5",
                                    "no ABR town matched this MLIT record"))

        df = pl.DataFrame(
            rows,
            schema={
                "bridge_id": pl.Utf8, "address_id": pl.Utf8, "target_id": pl.Utf8,
                "direction": pl.Utf8, "relation_type": pl.Utf8,
                "match_method": pl.Utf8, "matching_rule_id": pl.Utf8,
                "confidence": pl.Float64, "candidate_group_id": pl.Utf8,
                "candidate_count": pl.Int64, "mismatch_note": pl.Utf8,
                "force_review": pl.Boolean,
            },
        )
        out = finalize_bridge(df.drop("force_review"), ctx, ["address_id", "target_id"])

        # M2 is never `auto` even though it would otherwise clear the gate: a name
        # disagreement between two publishers is information, not noise.
        out = out.with_columns(
            pl.when(pl.col("matching_rule_id") == "M2")
            .then(pl.lit("review_required"))
            .otherwise(pl.col("verification_status"))
            .alias("verification_status")
        )
        log.info(
            "built address<->MLIT bridge",
            rows=out.height,
            by_rule=dict(out.group_by("matching_rule_id").agg(pl.len()).iter_rows()),
        )
        return out


def _unresolved(bridge, address_id, target_id, direction, rule, note) -> dict:
    return {
        "bridge_id": bridge_id(bridge, address_id, target_id, rule),
        "address_id": address_id, "target_id": target_id,
        "direction": direction, "relation_type": "unresolved",
        "match_method": "unresolved", "matching_rule_id": rule,
        "confidence": 0.0, "candidate_group_id": None, "candidate_count": 0,
        "mismatch_note": note, "force_review": False,
    }


NORMALIZE = normalize_conservative
