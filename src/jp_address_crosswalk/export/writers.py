"""Parquet / SQLite / CSV.gz export (spec §3, §47, §48).

Parquet is the normative form. SQLite keeps the same normalized tables and adds
the two flat views plus indexes. CSV.gz carries the accepted flat view only.

Every table is sorted on a total key immediately before writing, because Polars
join order is not stable and an unstable order would break byte-level
reproducibility (docs/ARCHITECTURE.md §6).
"""

from __future__ import annotations

import gzip
import hashlib
import sqlite3
from pathlib import Path

import polars as pl

from ..errors import ValidationFailed
from ..logging_setup import get_logger, stage_context

log = get_logger(__name__)

# Sorted deterministically before every write.
SORT_KEYS: dict[str, list[str]] = {
    "source_snapshot": ["source_snapshot_id"],
    "snapshot_license_artifact": ["artifact_id"],
    "match_run": ["match_run_id"],
    "match_run_input": ["match_run_id", "source_snapshot_id", "role"],
    "address_entity": ["address_id"],
    "address": ["address_id"],
    "address_code": ["address_id", "code_type", "code_value"],
    "address_lineage": ["lineage_id"],
    "address_rsdt_variant": ["rsdt_variant_id"],
    "address_key_conflict": ["lg_code", "machiaza_id"],
    "address_history": ["history_id"],
    "municipality": ["lg_code"],
    "municipality_version": ["municipality_version_id"],
    "postal_code_entity": ["postal_code"],
    "postal_record": ["postal_record_id"],
    "postal_record_version": ["postal_record_version_id"],
    "mlit_town": ["mlit_record_id"],
    "mlit_town_version": ["mlit_town_version_id"],
    "telephone_area": ["numbering_area_code"],
    "telephone_area_version": ["telephone_area_version_id"],
    "telephone_area_coverage": ["coverage_id"],
    "telephone_number_block": ["block_id"],
    "bridge_address_postal_code": ["bridge_id"],
    "bridge_address_postal": ["bridge_id"],
    "bridge_address_mlit": ["bridge_id"],
    "bridge_address_telephone": ["bridge_id"],
    "bridge_municipality_postal": ["bridge_id"],
    "bridge_municipality_telephone": ["bridge_id"],
    # V2. These were missing, which made _sorted() a no-op for them: their byte
    # reproducibility rested entirely on sorts inside the builders, so a builder
    # that forgot to sort would have produced a different file on every rebuild
    # with nothing reporting it (docs/ARCHITECTURE.md §6).
    "estat_small_area": ["key_code"],
    "n02_station": ["n02_group_code"],
    "bridge_station_municipality": ["n02_group_code", "boundary_jis_city_code"],
    "n02_railroad_line": ["line_name_raw", "operator_name_raw"],
    "bridge_station_line": ["n02_group_code", "line_name_raw", "operator_name_raw"],
    "bridge_line_municipality": [
        "line_name_raw", "operator_name_raw", "boundary_jis_city_code",
    ],
    "p11_bus_stop": ["p11_stop_id"],
    "bridge_bus_stop_municipality": ["p11_stop_id", "boundary_jis_city_code"],
}

# Columns that must never be written as a numeric type (docs/POLICY.md §8).
CODE_COLUMNS = {
    "postal_code", "old_postal_code", "old_postal_code_raw", "lg_code",
    "jis_city_code", "machiaza_id", "mlit_code", "area_code",
    "numbering_area_code", "local_code", "pref_code", "chome_number",
    "number", "aza_code",
}


def _sorted(name: str, df: pl.DataFrame) -> pl.DataFrame:
    keys = [k for k in SORT_KEYS.get(name, []) if k in df.columns]
    return df.sort(keys) if keys else df


def assert_code_columns_are_strings(tables: dict[str, pl.DataFrame]) -> list[str]:
    problems = []
    for name, df in tables.items():
        for col in df.columns:
            if col in CODE_COLUMNS and df.schema[col] != pl.Utf8:
                problems.append(f"{name}.{col} is {df.schema[col]}, expected Utf8")
    return problems


def write_parquet(tables: dict[str, pl.DataFrame], out_dir: Path) -> list[Path]:
    with stage_context("export", "parquet"):
        out_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for name in sorted(tables):
            path = out_dir / f"{name}.parquet"
            _sorted(name, tables[name]).write_parquet(path, compression="zstd")
            written.append(path)
        log.info("wrote parquet tables", count=len(written), dir=str(out_dir))
        return written


def build_flat_view(tables: dict[str, pl.DataFrame], accepted_only: bool) -> pl.DataFrame:
    """The user-facing denormalized view.

    Fans out on 1:N and N:M by design, and always carries the evidence columns
    so a row can never be read without its trustworthiness (docs/POLICY.md §4).
    """
    address = tables["address"]
    base = address.select(
        [
            "address_id", "lg_code", "jis_city_code",
            pl.col("pref").alias("pref_name"),
            pl.col("city").alias("city_name"),
            pl.col("ward").alias("ward_name"),
            pl.col("full_name_raw").alias("town_name"),
            pl.col("full_name_normalized").alias("town_name_normalized"),
            "machiaza_id",
        ]
    )

    def side(bridge: str, prefix: str, value_col: str, extra: list[str] | None = None) -> pl.DataFrame:
        df = tables.get(bridge)
        if df is None or df.is_empty():
            return pl.DataFrame(schema={"address_id": pl.Utf8})
        if accepted_only:
            df = df.filter(
                (pl.col("verification_status") != "manually_rejected")
                & pl.col("is_current")
            )
        cols = [
            pl.col("address_id"),
            pl.col("target_id").alias(value_col),
            pl.col("relation_type").alias(f"{prefix}_relation_type"),
            pl.col("match_method").alias(f"{prefix}_match_method"),
            pl.col("matching_rule_id").alias(f"{prefix}_rule"),
            pl.col("confidence").alias(f"{prefix}_confidence"),
            pl.col("candidate_count").alias(f"{prefix}_candidate_count"),
            pl.col("candidate_group_id").alias(f"{prefix}_candidate_group"),
            pl.col("is_unique_match").alias(f"{prefix}_is_unique"),
            pl.col("verification_status").alias(f"{prefix}_status"),
        ]
        for e in extra or []:
            cols.append(pl.col(e).alias(f"{prefix}_{e}"))
        return df.select(cols)

    out = base
    out = out.join(side("bridge_address_postal_code", "postal", "postal_code"),
                   on="address_id", how="left")
    out = out.join(side("bridge_address_mlit", "mlit", "mlit_record_id"),
                   on="address_id", how="left")
    out = out.join(
        side("bridge_address_telephone", "telephone", "numbering_area_code",
             ["coverage_type", "derivation"]),
        on="address_id", how="left",
    )

    mlv = tables.get("mlit_town_version")
    if mlv is not None and "mlit_record_id" in out.columns:
        # is_current filtered here too. Without it the Parquet flat view could
        # join a closed version while the SQLite view joined the live one, so
        # the two shipped artifacts disagreed.
        mlv = mlv.filter(pl.col("is_current"))
        out = out.join(
            mlv.select(
                [
                    "mlit_record_id",
                    pl.col("mlit_code"),
                    pl.col("latitude").alias("mlit_latitude"),
                    pl.col("longitude").alias("mlit_longitude"),
                ]
            ),
            on="mlit_record_id", how="left",
        ).drop("mlit_record_id")

    tav = tables.get("telephone_area_version")
    if tav is not None and "numbering_area_code" in out.columns:
        tav = tav.filter(pl.col("is_current"))
        out = out.join(
            tav.select(
                [
                    "numbering_area_code", "area_code",
                    pl.col("area_text_raw").alias("numbering_area_name"),
                ]
            ),
            on="numbering_area_code", how="left",
        )

    pcv = tables.get("postal_record_version")
    if pcv is not None and "postal_code" in out.columns:
        olds = (
            pcv.filter(pl.col("is_current") & pl.col("old_postal_code").is_not_null())
            .group_by("postal_code")
            .agg(pl.col("old_postal_code").unique().sort().str.join(";").alias("old_postal_code"))
        )
        out = out.join(olds, on="postal_code", how="left")

    sort_cols = [c for c in ["address_id", "postal_code", "mlit_code",
                             "numbering_area_code"] if c in out.columns]
    return out.sort(sort_cols)


# The SQLite views and build_flat_view are two independent implementations of
# one definition, so they are kept honest by tools/verify_distribution.py, which
# compares them row for row. Three things they must agree on, each of which they
# once did not:
#
#   * the column list, including old_postal_code, which SQLite users previously
#     could not see at all;
#   * the filtered view's semantics — see _accepted() below;
#   * nothing extra. Exposing *_is_current here and not in Parquet meant the two
#     shipped artifacts had different schemas.
_FLAT_SELECT = """
SELECT
  a.address_id, a.lg_code, a.jis_city_code,
  a.pref AS pref_name, a.city AS city_name, a.ward AS ward_name,
  a.full_name_raw AS town_name, a.full_name_normalized AS town_name_normalized,
  a.machiaza_id,
  bp.target_id AS postal_code,
  bp.relation_type AS postal_relation_type, bp.match_method AS postal_match_method,
  bp.matching_rule_id AS postal_rule, bp.confidence AS postal_confidence,
  bp.candidate_count AS postal_candidate_count,
  bp.candidate_group_id AS postal_candidate_group,
  bp.is_unique_match AS postal_is_unique, bp.verification_status AS postal_status,
  bm.relation_type AS mlit_relation_type, bm.match_method AS mlit_match_method,
  bm.matching_rule_id AS mlit_rule, bm.confidence AS mlit_confidence,
  bm.candidate_count AS mlit_candidate_count,
  bm.candidate_group_id AS mlit_candidate_group,
  bm.is_unique_match AS mlit_is_unique, bm.verification_status AS mlit_status,
  bt.target_id AS numbering_area_code,
  bt.relation_type AS telephone_relation_type,
  bt.match_method AS telephone_match_method,
  bt.matching_rule_id AS telephone_rule, bt.confidence AS telephone_confidence,
  bt.candidate_count AS telephone_candidate_count,
  bt.candidate_group_id AS telephone_candidate_group,
  bt.is_unique_match AS telephone_is_unique,
  bt.verification_status AS telephone_status,
  bt.coverage_type AS telephone_coverage_type,
  bt.derivation AS telephone_derivation,
  mv.mlit_code, mv.latitude AS mlit_latitude, mv.longitude AS mlit_longitude,
  tv.area_code, tv.area_text_raw AS numbering_area_name,
  op.old_postal_code
FROM address a
LEFT JOIN {bp} bp                ON bp.address_id = a.address_id
LEFT JOIN {bm} bm                ON bm.address_id = a.address_id
LEFT JOIN mlit_town_version mv   ON mv.mlit_record_id = bm.target_id
                                AND mv.is_current = 1
LEFT JOIN {bt} bt                ON bt.address_id = a.address_id
LEFT JOIN telephone_area_version tv ON tv.numbering_area_code = bt.target_id
                                AND tv.is_current = 1
LEFT JOIN (
  -- group_concat has no defined order, so the rows are ordered in a subquery
  -- first. 19 postal codes currently carry two former codes and the flat file
  -- must spell them the same way in both artifacts. The ORDER BY inside
  -- aggregate syntax would be clearer but needs SQLite 3.44, and this view text
  -- is parsed by whatever SQLite the reader has, not the one that built the
  -- file. The ordered-subquery idiom is checked at build time on the build
  -- machine only; a reader whose planner flattens the subquery could in
  -- principle see those 19 pairs in the other order.
  SELECT postal_code, group_concat(old_postal_code, ';') AS old_postal_code
    FROM (SELECT DISTINCT postal_code, old_postal_code
            FROM postal_record_version
           WHERE is_current = 1 AND old_postal_code IS NOT NULL
           ORDER BY postal_code, old_postal_code)
   GROUP BY postal_code
) op ON op.postal_code = bp.target_id
"""


def _accepted(table: str) -> str:
    """Accepted, current rows of one bridge.

    Filtering happens here, before the LEFT JOIN, and not in a WHERE clause over
    the joined result. The difference is not cosmetic: filtering afterwards
    removes the entire address row when its only bridge is rejected, so an
    address would silently vanish from the SQLite artifact while remaining in
    the Parquet one. docs/POLICY.md §4 forbids dropping a row to make a match go away.

    ``<>`` also excludes NULL, matching Polars' ``!=`` on a null.
    """
    return (
        f"(SELECT * FROM {table} WHERE is_current = 1"
        f" AND verification_status <> 'manually_rejected')"
    )


FLAT_VIEWS = (
    "CREATE VIEW address_crosswalk_all AS"
    + _FLAT_SELECT.format(
        bp="bridge_address_postal_code",
        bm="bridge_address_mlit",
        bt="bridge_address_telephone",
    )
    + ";\n\n-- The plain name is the safe one: accepted, current evidence only.\n"
    "CREATE VIEW address_crosswalk AS"
    + _FLAT_SELECT.format(
        bp=_accepted("bridge_address_postal_code"),
        bm=_accepted("bridge_address_mlit"),
        bt=_accepted("bridge_address_telephone"),
    )
    + ";\n"
)


DDL_VIEWS = """
CREATE VIEW IF NOT EXISTS unmatched_records AS
  SELECT 'postal_record' AS kind, target_id AS record_id, matching_rule_id
    FROM bridge_address_postal
   WHERE relation_type = 'unresolved' AND address_id IS NULL
  UNION ALL
  SELECT 'mlit_town', target_id, matching_rule_id
    FROM bridge_address_mlit
   WHERE relation_type = 'unresolved' AND address_id IS NULL
  UNION ALL
  SELECT 'address', address_id, matching_rule_id
    FROM bridge_address_mlit
   WHERE relation_type = 'unresolved' AND target_id IS NULL;
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_addr_lg ON address(lg_code)",
    "CREATE INDEX IF NOT EXISTS idx_addr_jis_norm ON address(jis_city_code, full_name_normalized)",
    "CREATE INDEX IF NOT EXISTS idx_pcv_code ON postal_record_version(postal_code)",
    "CREATE INDEX IF NOT EXISTS idx_pcv_old ON postal_record_version(old_postal_code)",
    "CREATE INDEX IF NOT EXISTS idx_pcv_jis_norm ON postal_record_version(jis_city_code, town_normalized)",
    "CREATE INDEX IF NOT EXISTS idx_mlv_code ON mlit_town_version(mlit_code)",
    "CREATE INDEX IF NOT EXISTS idx_mlv_jis_norm ON mlit_town_version(jis_city_code, town_name_normalized)",
    "CREATE INDEX IF NOT EXISTS idx_tav_area ON telephone_area_version(area_code)",
    "CREATE INDEX IF NOT EXISTS idx_tnb_area ON telephone_number_block(area_code, local_code)",
    "CREATE INDEX IF NOT EXISTS idx_bapc_addr ON bridge_address_postal_code(address_id)",
    "CREATE INDEX IF NOT EXISTS idx_bapc_code ON bridge_address_postal_code(target_id)",
    "CREATE INDEX IF NOT EXISTS idx_bap_addr ON bridge_address_postal(address_id)",
    "CREATE INDEX IF NOT EXISTS idx_bap_rec ON bridge_address_postal(target_id)",
    "CREATE INDEX IF NOT EXISTS idx_bap_rel ON bridge_address_postal(relation_type)",
    "CREATE INDEX IF NOT EXISTS idx_bam_addr ON bridge_address_mlit(address_id)",
    "CREATE INDEX IF NOT EXISTS idx_bam_rec ON bridge_address_mlit(target_id)",
    "CREATE INDEX IF NOT EXISTS idx_bat_addr ON bridge_address_telephone(address_id)",
    "CREATE INDEX IF NOT EXISTS idx_bat_area ON bridge_address_telephone(target_id)",
    "CREATE INDEX IF NOT EXISTS idx_bmt_area ON bridge_municipality_telephone(target_id)",
    "CREATE INDEX IF NOT EXISTS idx_bmp_lg ON bridge_municipality_postal(lg_code)",
    "CREATE INDEX IF NOT EXISTS idx_lineage_old ON address_lineage(old_address_id)",
    "CREATE INDEX IF NOT EXISTS idx_lineage_new ON address_lineage(new_address_id)",
]

# V2 tables are optional, so their indexes are created only when the tables were
# actually written. Adding them to INDEXES above would fail a V1-only build.
V2_INDEXES = {
    "estat_small_area": [
        "CREATE INDEX IF NOT EXISTS idx_esa_jis ON estat_small_area(jis_city_code)",
        "CREATE INDEX IF NOT EXISTS idx_esa_rec ON estat_small_area(reconcile_status)",
    ],
    "bridge_station_municipality": [
        "CREATE INDEX IF NOT EXISTS idx_bsm_station"
        " ON bridge_station_municipality(n02_group_code)",
        "CREATE INDEX IF NOT EXISTS idx_bsm_lg ON bridge_station_municipality(lg_code)",
    ],
    "bridge_station_line": [
        "CREATE INDEX IF NOT EXISTS idx_bsl_station"
        " ON bridge_station_line(n02_group_code)",
        "CREATE INDEX IF NOT EXISTS idx_bsl_line"
        " ON bridge_station_line(line_name_raw, operator_name_raw)",
    ],
    "bridge_line_municipality": [
        "CREATE INDEX IF NOT EXISTS idx_blm_line"
        " ON bridge_line_municipality(line_name_raw, operator_name_raw)",
        "CREATE INDEX IF NOT EXISTS idx_blm_lg ON bridge_line_municipality(lg_code)",
    ],
    "bridge_bus_stop_municipality": [
        "CREATE INDEX IF NOT EXISTS idx_bbm_stop"
        " ON bridge_bus_stop_municipality(p11_stop_id)",
        "CREATE INDEX IF NOT EXISTS idx_bbm_lg ON bridge_bus_stop_municipality(lg_code)",
    ],
}


def write_sqlite(
    tables: dict[str, pl.DataFrame],
    flat_accepted: pl.DataFrame,
    flat_all: pl.DataFrame,
    path: Path,
) -> Path:
    """Normalized tables plus the flat views.

    The flat views are real SQL ``VIEW``s over the normalized tables, not
    materialized copies. Materializing them tripled the file size (2.1 GiB,
    past GitHub's 2 GiB asset limit) to store a join SQLite can compute on
    demand from indexed tables.
    """
    with stage_context("export", "sqlite"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA journal_mode=OFF")
            conn.execute("PRAGMA synchronous=OFF")
            for name in sorted(tables):
                _write_table(
                    conn, name, _sorted(name, tables[name]), present=set(tables)
                )
            conn.executescript(FLAT_VIEWS)
            conn.executescript(DDL_VIEWS)
            planned = list(INDEXES)
            for table, statements in V2_INDEXES.items():
                if table in tables and not tables[table].is_empty():
                    planned.extend(statements)
            for stmt in planned:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as exc:
                    log.warning("index skipped", stmt=stmt, error=str(exc))
            conn.commit()
            # 宣言した外部キーを、書き終えた実物に対して検査する。SQLite の
            # `PRAGMA foreign_keys` は接続ごとに既定で OFF なので、宣言しただけでは
            # 何も確かめたことにならない —— 利用者が ON にした瞬間に初めて壊れて
            # いたことが分かる、という配り方をしない。挿入中は OFF のままにして
            # おき（そうしないとテーブルを書く順序に依存する）、最後にまとめて
            # 検査する。
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                by_table: dict[str, int] = {}
                for row in violations:
                    by_table[row[0]] = by_table.get(row[0], 0) + 1
                raise ValidationFailed(
                    "declared foreign keys are not satisfied by the data",
                    tables=by_table, sample=violations[:3],
                )
            conn.execute("VACUUM")
            conn.commit()
        finally:
            conn.close()
        log.info(
            "wrote sqlite", path=str(path), size=path.stat().st_size,
            foreign_keys_checked=True,
        )
        return path


# Constraints the database enforces itself. A gate that lives only in Python is
# a gate a future refactor can remove; these make an invalid row unwritable.
PRIMARY_KEYS = {
    "address": "address_id", "address_entity": "address_id",
    "municipality": "lg_code", "municipality_version": "municipality_version_id",
    "postal_code_entity": "postal_code", "postal_record": "postal_record_id",
    "postal_record_version": "postal_record_version_id",
    "mlit_town": "mlit_record_id", "mlit_town_version": "mlit_town_version_id",
    "telephone_area": "numbering_area_code",
    "telephone_area_version": "telephone_area_version_id",
    "telephone_area_coverage": "coverage_id",
    "telephone_number_block": "block_id",
    "source_snapshot": "source_snapshot_id", "match_run": "match_run_id",
    # One row per 駅グループ, so the group code is the key (docs/schema.sql).
    "n02_station": "n02_group_code",
    # N02 codes stations but not lines, so a line's identity has to be the
    # publisher's own (路線名, 運営会社) — verified unique across all 597. A
    # surrogate id would invent an identifier the source already supplies, and
    # the four-column composite splits 10 real lines that change 鉄道区分 along
    # their length (src/jp_address_crosswalk/build/railroad.py).
    "n02_railroad_line": ("line_name_raw", "operator_name_raw"),
    # A surrogate row id, not an entity identity: P11 gives a bus stop no
    # identifier and no combination of its attributes is unique — even
    # (name, operator, coordinates) collides 12 times nationwide
    # (src/jp_address_crosswalk/sources/mlit_ksj_p11.py).
    "p11_bus_stop": "p11_stop_id",
    "address_lineage": "lineage_id", "address_history": "history_id",
    "address_rsdt_variant": "rsdt_variant_id",
    **{b: "bridge_id" for b in [
        "bridge_address_postal_code", "bridge_address_postal", "bridge_address_mlit",
        "bridge_address_telephone", "bridge_municipality_postal",
        "bridge_municipality_telephone",
    ]},
}

_RELATION_TYPES = ("exact', 'equivalent', 'parent', 'child', 'contains', "
                   "'overlap', 'candidate', 'ambiguous', 'unresolved")
_MATCH_METHODS = ("direct_code', 'exact_name', 'normalized_name', 'parent_child', "
                  "'composite', 'official_area_rule', 'manual_override', 'unresolved")
_STATUSES = "auto', 'review_required', 'manually_verified', 'manually_rejected"

BRIDGE_CHECKS = [
    "CHECK (confidence >= 0.0 AND confidence <= 1.0)",
    "CHECK (candidate_count >= 0)",
    # At least one endpoint always exists, so nothing is ever dropped.
    "CHECK (address_id IS NOT NULL OR lg_code IS NOT NULL OR target_id IS NOT NULL)",
    "CHECK (NOT (is_unique_match = 1 AND candidate_count > 1))",
    "CHECK (candidate_count <= 1 OR candidate_group_id IS NOT NULL)",
    "CHECK (candidate_count > 1 OR candidate_group_id IS NULL)",
    # The full documented auto-accept conjunction (docs/MATCHING_RULES.md §4).
    "CHECK (verification_status <> 'auto' OR ("
    " candidate_count = 1 AND is_unique_match = 1"
    " AND candidate_count_is_complete = 1 AND confidence >= 0.98"
    " AND override_stale = 0"
    " AND relation_type IN ('exact','equivalent')))",
    f"CHECK (relation_type IN ('{_RELATION_TYPES}'))",
    f"CHECK (match_method IN ('{_MATCH_METHODS}'))",
    f"CHECK (verification_status IN ('{_STATUSES}'))",
]

# The station bridge is a different shape and needs its own constraints rather
# than the address-bridge set: it has no address_id / target_id /
# candidate_group_id / override_stale, so BRIDGE_CHECKS would reference columns
# that do not exist. The differences are deliberate, not omissions:
#
# * `contains` is the only positive relation. A station is inside a municipality,
#   never identical to one, so the exact/equivalent vocabulary does not apply.
# * `auto` is therefore admitted for `contains`. The address bridges withhold it
#   because they assert two identifiers name the same thing; containment is a
#   deterministic geometric test with nothing for a reviewer to add. It still
#   requires a single settled candidate and a clean note.
# * There is no address_id column at all — 町字 granularity is unreachable
#   through this source (docs/POLICY.md §3.1), and a table that cannot express
#   the wrong answer cannot drift into it.
STATION_BRIDGE_CHECKS = [
    "CHECK (confidence >= 0.0 AND confidence <= 1.0)",
    "CHECK (candidate_count >= 0)",
    "CHECK (NOT (is_unique_match = 1 AND candidate_count > 1))",
    "CHECK (relation_type IN ('contains','ambiguous','unresolved'))",
    # *_via_lineage は「境界データの旧コードを、人が署名した承継記録で現行コードに
    # 読み替えた」ことを示す。語彙を増やしているのであって、auto の条件を緩めては
    # いない —— 承継を経ても含有は含有で、注記は空のままである。
    "CHECK (match_method IN ('spatial_containment','spatial_containment_via_lineage',"
    "'unresolved'))",
    f"CHECK (verification_status IN ('{_STATUSES}'))",
    "CHECK (verification_status <> 'auto' OR ("
    " relation_type = 'contains' AND candidate_count = 1 AND is_unique_match = 1"
    " AND confidence = 1.0 AND mismatch_note IS NULL))",
    # An unresolved station keeps its row and names no municipality, which is
    # what makes "not placed" different from "placed nowhere in particular".
    "CHECK (relation_type <> 'unresolved' OR (lg_code IS NULL AND confidence = 0.0))",
]

SMALL_AREA_CHECKS = [
    "CHECK (hcode IN ('8101','8154'))",
    "CHECK (reconcile_status IN ('matched','superseded','split_no_single_successor',"
    "'unassigned_area','estat_only'))",
]

N02_STATION_CHECKS = ["CHECK (feature_count >= 1)"]

BUS_STOP_CHECKS = [
    "CHECK (length(pref_code) = 2)",
    "CHECK (length(stop_name_raw) > 0)",
]

RAILROAD_LINE_CHECKS = [
    "CHECK (section_count >= 1)",
    "CHECK (railway_class_variants >= 1)",
    "CHECK (operator_class_variants >= 1)",
]

# 駅 → 路線 is an attribute join, not a spatial one: N02 states each feature's
# line on the feature itself. It carries no relation_type because there is no
# relation to characterise — the publisher said so directly.
STATION_LINE_CHECKS = [
    "CHECK (feature_count >= 1)",
    "CHECK (match_method = 'n02_attribute')",
]

# A line is not a point, so this differs from STATION_BRIDGE_CHECKS in the one
# place that matters: there is no uniqueness requirement. A line running through
# 40 municipalities is 40 correct rows, not an ambiguity — so `auto` asks only
# that the municipality resolved and nothing needed noting.
LINE_BRIDGE_CHECKS = [
    "CHECK (confidence >= 0.0 AND confidence <= 1.0)",
    "CHECK (municipality_count >= 0)",
    "CHECK (sample_hits IS NULL OR sample_hits >= 1)",
    "CHECK (relation_type IN ('overlap','unresolved'))",
    "CHECK (match_method IN ('spatial_sampling','spatial_sampling_via_lineage',"
    "'unresolved'))",
    f"CHECK (verification_status IN ('{_STATUSES}'))",
    "CHECK (verification_status <> 'auto' OR ("
    " relation_type = 'overlap' AND lg_code IS NOT NULL"
    " AND confidence = 1.0 AND mismatch_note IS NULL))",
    "CHECK (relation_type <> 'unresolved' OR ("
    " lg_code IS NULL AND boundary_jis_city_code IS NULL AND confidence = 0.0))",
]


# Referential integrity, declared in the database rather than asserted only by the
# test suite (docs/LIMITATIONS.md item 8, independent review 3).
#
# Column name -> the table that owns it. These names are the project's own
# vocabulary: an `address_id` anywhere is an address_entity, a `source_snapshot_id`
# is a source_snapshot. Expressed as a convention rather than 67 hand-written
# entries because a per-table list is a list somebody forgets to extend — a new
# table carrying `lg_code` gets its foreign key without anybody remembering.
#
# The convention is checked, not assumed: write_sqlite runs PRAGMA
# foreign_key_check over the finished database, so a column that happens to share
# one of these names while meaning something else fails the build rather than
# shipping a false claim.
_FK_BY_COLUMN = {
    "address_id": ("address_entity", "address_id"),
    "old_address_id": ("address_entity", "address_id"),
    "new_address_id": ("address_entity", "address_id"),
    "lg_code": ("municipality", "lg_code"),
    "match_run_id": ("match_run", "match_run_id"),
    "postal_record_id": ("postal_record", "postal_record_id"),
    "postal_code": ("postal_code_entity", "postal_code"),
    "mlit_record_id": ("mlit_town", "mlit_record_id"),
    "numbering_area_code": ("telephone_area", "numbering_area_code"),
    "n02_group_code": ("n02_station", "n02_group_code"),
    "p11_stop_id": ("p11_bus_stop", "p11_stop_id"),
}

# The one composite reference. A 路線 is keyed by the publisher's own
# (路線名, 運営会社) rather than by a surrogate (see PRIMARY_KEYS), so a bridge
# pointing at a line needs a two-column foreign key.
_COMPOSITE_FKS = {
    "bridge_station_line": [(("line_name_raw", "operator_name_raw"),
                             "n02_railroad_line",
                             ("line_name_raw", "operator_name_raw"))],
    "bridge_line_municipality": [(("line_name_raw", "operator_name_raw"),
                                  "n02_railroad_line",
                                  ("line_name_raw", "operator_name_raw"))],
}

# `target_id` is deliberately absent. It is polymorphic — a postal record id in one
# bridge, an area code in another — and a polymorphic column cannot carry a foreign
# key at all. That is the open half of item 8: the fix is the typed endpoint columns
# `docs/DB_SCHEMA.md` §5.1 describes, which is a breaking change for consumers and
# not something to slip into an export.
#
# The snapshot columns (`source_snapshot_id`, `first_observed_snapshot_id`,
# `last_observed_snapshot_id`) are deliberately absent too, and this one is easy to
# get wrong: nationally they satisfy the constraint today, so declaring the key
# would pass and ship. It would still be false. `source_snapshot` carries **this
# build's** payloads, while those columns cite *when something was first observed*
# and are carried forward untouched — by `carry_forward` for the version tables, by
# `carry_forward_observations` for address_code, by the identity ledger for
# address_entity. The first release whose payloads change would leave every
# carried-forward row pointing at a snapshot the shipped table no longer contains.
# Making these real means making `source_snapshot` append-only, which is a change to
# what provenance means and not a detail of the DDL.
#
# The same argument, found by review to apply to a second case: a table that is
# **carried forward** must not reference a parent that is **rebuilt**. The
# `*_version` tables keep a superseded row forever (`build/versioning.py`), while
# `postal_record`, `postal_code_entity`, `mlit_town`, `telephone_area` and
# `municipality` are derived from this build's payload alone. `postal_record_id` is a
# hash over the whole ken_all row, so Japan Post changing one character retires that
# id from `postal_record` while the closed version row still names it — and
# `foreign_key_check` would fail the export. Nothing national exercises this today
# because the payloads have not changed since v1.0.0, which is exactly why it has to
# be reasoned about rather than measured: the first real update would be the test.
#
# Not excluded by the same rule: `address_code` and `address_lineage` are carried
# forward but point at `address_entity`, which is append-only by construction (a
# retired entity keeps its row — docs/IDENTITY_MODEL.md §5). A carried-forward row
# may name a retired entity; it may not name a row that no longer exists.
_CARRIED_FORWARD_TABLES = frozenset({
    "municipality_version",
    "postal_record_version",
    "mlit_town_version",
    "telephone_area_version",
})


def foreign_keys_for(
    name: str, cols: list[str], present: set[str] | None = None
) -> list[str]:
    """The FOREIGN KEY clauses for one table, in a deterministic order.

    ``present`` names the tables this database will actually contain. A key
    pointing at a table that is not there is not a constraint — it is a claim
    every row violates, and SQLite's ``foreign_key_check`` reports exactly that.
    Two real cases, not hypotheticals:

    * a build without the optional V2 payloads ships 28 tables, so nothing may
      reference ``n02_station`` (README: V2 の元データがあるときだけ出力します)
    * a test or a tool that writes a couple of tables to look at them

    ``None`` means "assume the full release" and is what ``docs/schema.sql``
    documents.
    """
    if name in _CARRIED_FORWARD_TABLES:
        # 引き継がれる表は、作り直される親を指せない（上のコメント）。この表が持つ
        # 参照はどれもそれなので、1本も宣言しない。
        return []
    own_pk = PRIMARY_KEYS.get(name)
    own = {own_pk} if isinstance(own_pk, str) else set(own_pk or ())
    out = []
    for col in cols:
        ref = _FK_BY_COLUMN.get(col)
        if not ref:
            continue
        parent, parent_col = ref
        # A table never references itself through its own key, and a parent table
        # does not reference itself (municipality.lg_code IS the parent).
        if parent == name or col in own:
            continue
        if present is not None and parent not in present:
            continue
        out.append(
            f'FOREIGN KEY ("{col}") REFERENCES "{parent}"("{parent_col}")'
        )
    for child_cols, parent, parent_cols in _COMPOSITE_FKS.get(name, []):
        if present is not None and parent not in present:
            continue
        if all(c in cols for c in child_cols):
            out.append(
                "FOREIGN KEY ("
                + ", ".join(f'"{c}"' for c in child_cols)
                + f') REFERENCES "{parent}"('
                + ", ".join(f'"{c}"' for c in parent_cols)
                + ")"
            )
    return out


def _write_table(
    conn: sqlite3.Connection, name: str, df: pl.DataFrame,
    present: set[str] | None = None,
) -> None:
    if df.is_empty() and not df.columns:
        return
    cols = df.columns
    types = []
    pk = PRIMARY_KEYS.get(name)
    pk_cols = (pk,) if isinstance(pk, str) else tuple(pk or ())
    absent = [c for c in pk_cols if c not in cols]
    if absent:
        # Asserted rather than skipped: a typo here would ship a table with no
        # key at all, which is exactly how n02_station lost its PRIMARY KEY once.
        raise ValueError(f"{name}: PRIMARY KEY columns absent from frame: {absent}")
    inline_pk = pk_cols[0] if len(pk_cols) == 1 else None
    for c in cols:
        dt = df.schema[c]
        if dt in (pl.Float64, pl.Float32):
            sql_type = "REAL"
        elif dt in (pl.Int64, pl.Int32, pl.Int16, pl.Int8,
                    pl.UInt64, pl.UInt32, pl.UInt16, pl.UInt8, pl.Boolean):
            # Unsigned counts are counts. Leaving them out landed n02_station's
            # feature_count in a TEXT column, where CHECK (feature_count >= 1)
            # compares strings and stops being the constraint it claims to be.
            sql_type = "INTEGER"
        else:
            sql_type = "TEXT"
        suffix = " PRIMARY KEY" if c == inline_pk else ""
        types.append(f'"{c}" {sql_type}{suffix}')
    if len(pk_cols) > 1:
        types.append("PRIMARY KEY (" + ", ".join(f'"{c}"' for c in pk_cols) + ")")

    if name in ("bridge_station_municipality", "bridge_bus_stop_municipality"):
        # Same shape and same vocabulary: a published point either falls inside a
        # polygon or it does not. Shared rather than copied so the two cannot
        # drift apart into differently-guarded versions of one claim.
        types.extend(STATION_BRIDGE_CHECKS)
    elif name == "bridge_line_municipality":
        types.extend(LINE_BRIDGE_CHECKS)
    elif name == "bridge_station_line":
        types.extend(STATION_LINE_CHECKS)
    elif name.startswith("bridge_") and "relation_type" in cols:
        types.extend(BRIDGE_CHECKS)
    if name == "n02_railroad_line":
        types.extend(RAILROAD_LINE_CHECKS)
    if name == "p11_bus_stop":
        types.extend(BUS_STOP_CHECKS)
    if name == "estat_small_area":
        types.extend(SMALL_AREA_CHECKS)
    if name == "n02_station":
        types.extend(N02_STATION_CHECKS)
    if name == "postal_code_entity":
        types.append("CHECK (length(postal_code) = 7)")
    if name == "address":
        types.append("CHECK (length(address_id) = 20)")
    if name == "mlit_town_version":
        types.append("CHECK (latitude IS NULL OR (latitude BETWEEN 20 AND 46))")
        types.append("CHECK (longitude IS NULL OR (longitude BETWEEN 122 AND 154))")

    types.extend(foreign_keys_for(name, cols, present))

    conn.execute(f'CREATE TABLE "{name}" ({", ".join(types)})')
    if df.height:
        placeholders = ",".join("?" * len(cols))
        conn.executemany(
            f'INSERT INTO "{name}" VALUES ({placeholders})',
            list(df.iter_rows()),
        )


def write_csv_gz(flat: pl.DataFrame, path: Path) -> Path:
    with stage_context("export", "csv_gz"):
        path.parent.mkdir(parents=True, exist_ok=True)
        # mtime=0 and an empty embedded filename so the container is byte-stable
        # across builds; otherwise identical data would hash differently.
        payload = flat.write_csv().encode("utf-8")
        with path.open("wb") as raw, gzip.GzipFile(
            filename="", mode="wb", fileobj=raw, mtime=0
        ) as fh:
            fh.write(payload)
        log.info("wrote csv.gz", path=str(path), rows=flat.height)
        return path


def write_sqlite_gz(src: Path, out: Path) -> Path:
    """The SQLite database, gzipped, because that is the file a release ships.

    GitHub's documented limit is "Each file included in a release must be under
    2 GiB", and the uncompressed database reached 2,082,521,088 bytes in
    v1.2.0 — 62 MiB of headroom on an artifact that grows every time a table is
    added. Measured on that build, gzip -6 takes it to 418,894,570 bytes (5.0x),
    so the limit next binds around 10 GB uncompressed rather than 2.

    Compressing rather than dropping the indexes or splitting the file is the
    choice that changes least for a consumer: `jp_address_crosswalk.csv.gz`
    already ships this way, so this applies an existing convention to a second
    file instead of introducing a second convention.

    Streamed in chunks. Reading 2 GB into memory to hand it to
    ``gzip.compress`` would double the build's peak, which ``ARCHITECTURE.md``
    §8 already puts at 4–6 GB.
    """
    with stage_context("export", "sqlite_gz"):
        # mtime=0 and an empty embedded filename, for the same reason as
        # write_csv_gz: the container must not make identical data hash
        # differently from one build to the next.
        with src.open("rb") as raw_in, out.open("wb") as raw_out, gzip.GzipFile(
            filename="", mode="wb", fileobj=raw_out, mtime=0
        ) as fh:
            for chunk in iter(lambda: raw_in.read(1 << 22), b""):
                fh.write(chunk)
        before, after = src.stat().st_size, out.stat().st_size
        log.info(
            "wrote sqlite.gz", path=str(out), size=after, uncompressed=before,
            ratio=round(before / after, 2) if after else None,
        )
        return out


def write_sha256sums(paths: list[Path], out: Path) -> Path:
    lines = []
    for p in sorted(paths, key=lambda x: x.name):
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        lines.append(f"{h.hexdigest()}  {p.name}")
    # LF, not the platform default: on Windows write_text turns every "\n"
    # into "\r\n", and `sha256sum -c SHA256SUMS` then fails on every line
    # because each filename carries a trailing carriage return.
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return out
