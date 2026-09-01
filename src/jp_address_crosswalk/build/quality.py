"""Quality metrics, threshold gating and reports (docs/QUALITY_POLICY.md).

The target is not a high match rate. A rising ``exact`` rate with a collapsing
``ambiguous`` rate is treated as a suspected regression in strictness, not as
good news, and it blocks the release until explained.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from ..errors import RowCountAnomaly, ValidationFailed
from ..logging_setup import get_logger, stage_context

log = get_logger(__name__)

BRIDGES = [
    "bridge_address_postal_code",
    "bridge_address_postal",
    "bridge_address_mlit",
    "bridge_address_telephone",
    "bridge_municipality_postal",
    "bridge_municipality_telephone",
]

RELATION_TYPES = [
    "exact", "equivalent", "parent", "child", "contains",
    "overlap", "candidate", "ambiguous", "unresolved",
]


@dataclass
class QualityReport:
    code_version: str
    data_version: str
    built_at: str
    matching_rule_version: str
    sources: dict = field(default_factory=dict)
    tables: dict = field(default_factory=dict)
    bridges: dict = field(default_factory=dict)
    thresholds: list = field(default_factory=list)
    identity: dict = field(default_factory=dict)
    passed: bool = True

    def as_dict(self) -> dict:
        return {
            "code_version": self.code_version,
            "data_version": self.data_version,
            "built_at": self.built_at,
            "matching_rule_version": self.matching_rule_version,
            "sources": self.sources,
            "tables": self.tables,
            "bridges": self.bridges,
            "thresholds": self.thresholds,
            "identity": self.identity,
            "passed": self.passed,
        }


def table_metrics(name: str, df: pl.DataFrame, key: list[str] | None) -> dict:
    nulls = {c: int(df[c].null_count()) for c in df.columns} if df.height else {}
    unique_key_count = None
    duplicate_count = 0
    if key and all(k in df.columns for k in key) and df.height:
        unique_key_count = df.select(key).unique().height
        duplicate_count = df.height - unique_key_count
    return {
        "row_count": df.height,
        "column_count": df.width,
        "duplicate_count": duplicate_count,
        "unique_key_count": unique_key_count,
        "null_count": nulls,
    }


def bridge_metrics(df: pl.DataFrame) -> dict:
    if df.is_empty():
        return {"row_count": 0}
    rel = dict(df.group_by("relation_type").agg(pl.len()).iter_rows())
    status = dict(df.group_by("verification_status").agg(pl.len()).iter_rows())
    rules = dict(df.group_by("matching_rule_id").agg(pl.len()).iter_rows())
    total = df.height
    resolved = total - rel.get("unresolved", 0)
    return {
        "row_count": total,
        "by_relation_type": {k: rel.get(k, 0) for k in RELATION_TYPES},
        "by_verification_status": status,
        "by_rule": rules,
        "auto_accept_count": status.get("auto", 0),
        "review_required_count": status.get("review_required", 0),
        "override_stale_count": int(df["override_stale"].sum())
        if "override_stale" in df.columns else 0,
        "confidence_histogram": _histogram(df["confidence"]),
        # Rates always carry their denominator (docs/QUALITY_POLICY.md §8).
        "rates": {
            "denominator": total,
            "exact_or_equivalent_pct": _pct(
                rel.get("exact", 0) + rel.get("equivalent", 0), total
            ),
            "ambiguous_pct": _pct(rel.get("ambiguous", 0), total),
            "unresolved_pct": _pct(rel.get("unresolved", 0), total),
            "resolved_pct": _pct(resolved, total),
        },
    }


def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 3) if d else 0.0


def _histogram(series: pl.Series) -> dict:
    buckets = {"0.00-0.49": 0, "0.50-0.69": 0, "0.70-0.89": 0,
               "0.90-0.97": 0, "0.98-1.00": 0}
    for v in series.to_list():
        if v is None:
            continue
        if v < 0.5:
            buckets["0.00-0.49"] += 1
        elif v < 0.7:
            buckets["0.50-0.69"] += 1
        elif v < 0.9:
            buckets["0.70-0.89"] += 1
        elif v < 0.98:
            buckets["0.90-0.97"] += 1
        else:
            buckets["0.98-1.00"] += 1
    return buckets


def check_genesis_minimums(tables: dict[str, pl.DataFrame], thresholds: dict) -> list[dict]:
    """Absolute floors for a first release, which has nothing to compare against.

    Without these, a truncated first download would install itself as the
    baseline and every relative check would pass.
    """
    minimums = thresholds.get("genesis_minimums", {})
    mapping = {
        "abr_town_rows": "address",
        "abr_city_rows": "municipality",
        "abr_postal_conversion_rows": "bridge_address_postal_code",
        "japanpost_ken_all_rows": "postal_record",
        "mlit_isj_rows": "mlit_town",
        "mic_telephone_areas": "telephone_area",
        "mic_number_blocks": "telephone_number_block",
    }
    results = []
    for key, table in mapping.items():
        floor = minimums.get(key)
        if floor is None:
            continue
        if table not in tables:
            # A configured floor for a table that is absent is a failure, not a
            # reason to skip: a missing required output would otherwise pass.
            results.append(
                {"check": f"genesis_minimum.{key}", "table": table,
                 "observed": 0, "floor": floor, "status": "fail",
                 "detail": "table absent from the build"}
            )
            continue
        observed = tables[table].height
        results.append(
            {"check": f"genesis_minimum.{key}", "table": table,
             "observed": observed, "floor": floor,
             "status": "pass" if observed >= floor else "fail"}
        )

    # Any floor naming a table this mapping does not cover is a config error;
    # silently ignoring it would make the threshold file lie about what it gates.
    unknown = sorted(set(minimums) - set(mapping))
    if unknown:
        results.append(
            {"check": "genesis_minimum.unknown_keys", "status": "fail",
             "detail": f"unrecognised threshold keys: {unknown}"}
        )
    return results


def check_required_and_duplicates(
    tables: dict[str, pl.DataFrame], thresholds: dict, keys: dict[str, list[str]]
) -> list[dict]:
    """Consume the blocking keys that were configured but never read."""
    blocking = thresholds.get("blocking", {})
    out: list[dict] = []

    required_tables = {
        "abr": "address", "japanpost": "postal_record", "mlit": "mlit_town",
        "mic_area_code": "telephone_area",
        "mic_number_assignment": "telephone_number_block",
    }
    for source in blocking.get("required_sources", []):
        table = required_tables.get(source)
        present = bool(table and table in tables and tables[table].height)
        out.append(
            {"check": f"required_source.{source}", "table": table,
             "status": "pass" if present else "fail"}
        )

    max_dupes = int(blocking.get("duplicate_natural_keys_max", 0))
    for table, key in keys.items():
        if table not in tables or not tables[table].height:
            continue
        df = tables[table]
        if not all(k in df.columns for k in key):
            continue
        dupes = df.height - df.select(key).unique().height
        out.append(
            {"check": f"duplicate_natural_keys.{table}", "duplicates": dupes,
             "limit": max_dupes, "status": "pass" if dupes <= max_dupes else "fail"}
        )
    return out


def compare_with_previous(
    current: dict, previous: dict | None, thresholds: dict
) -> list[dict]:
    """Relative gates against the previous release."""
    if not previous:
        return [{"check": "previous_release", "status": "skip",
                 "detail": "no previous release to compare against"}]

    out = []
    blocking = thresholds.get("blocking", {})
    src_pct = float(blocking.get("source_row_count_change_pct", 5.0))
    town_pct = float(blocking.get("abr_town_count_change_pct", 2.0))

    for table, cur in current.get("tables", {}).items():
        prev = previous.get("tables", {}).get(table)
        if not prev or not prev.get("row_count"):
            continue
        limit = town_pct if table == "address" else src_pct
        change = 100.0 * (cur["row_count"] - prev["row_count"]) / prev["row_count"]
        row = {
            "check": f"row_count_change.{table}", "change_pct": round(change, 3),
            "limit_pct": limit,
            "status": "pass" if abs(change) <= limit else "fail",
        }
        if row["status"] == "fail":
            # Same shape as approved_rate_changes: one reviewed transition, named
            # by its exact before and after counts. A percentage allowance would
            # widen the gate for every future release; this matches once and then
            # can never match again, because the new count becomes the baseline.
            for approval in thresholds.get("approved_row_count_changes", []):
                tolerance = int(approval.get("tolerance_rows", 0))
                if (
                    approval.get("table") == table
                    and abs(int(approval.get("from_rows", -1)) - prev["row_count"])
                    <= tolerance
                    and abs(int(approval.get("to_rows", -1)) - cur["row_count"])
                    <= tolerance
                ):
                    row["status"] = "pass"
                    row["approved_migration"] = True
                    row["rationale"] = approval.get("rationale")
                    row["attested_by"] = approval.get("attested_by")
                    break
        out.append(row)

    # Source-level volume: compares each publisher snapshot's own row count with
    # the matching prior snapshot. Comparing only derived tables let a parser
    # silently lose source rows whenever the derived count happened to hold.
    for dataset, cur_src in (current.get("sources") or {}).items():
        prev_src = (previous.get("sources") or {}).get(dataset)
        if not prev_src or not prev_src.get("row_count") or not cur_src.get("row_count"):
            continue
        change = (
            100.0
            * (cur_src["row_count"] - prev_src["row_count"])
            / prev_src["row_count"]
        )
        out.append(
            {
                "check": f"source_row_count_change.{dataset}",
                "change_pct": round(change, 3),
                "limit_pct": src_pct,
                "status": "pass" if abs(change) <= src_pct else "fail",
            }
        )

    retired_pct = float(blocking.get("addresses_retired_with_lineage_pct", 2.0))
    prev_addr = (previous.get("tables") or {}).get("address", {}).get("row_count")
    retired = current.get("retired_with_lineage")
    if prev_addr and retired is not None:
        pct = 100.0 * retired / prev_addr
        out.append(
            {
                "check": "addresses_retired_with_lineage",
                "observed_pct": round(pct, 3),
                "limit_pct": retired_pct,
                "status": "pass" if pct <= retired_pct else "fail",
            }
        )

    rise = float(blocking.get("unresolved_rate_rise_points", 3.0))
    amb = float(blocking.get("ambiguous_rate_rise_points", 3.0))
    fall = float(blocking.get("exact_rate_fall_points", 3.0))
    jump = float(blocking.get("exact_rate_rise_points_without_rule_bump", 5.0))
    rule_bump = current.get("matching_rule_version") != previous.get("matching_rule_version")

    for bridge, cur in current.get("bridges", {}).items():
        prev = previous.get("bridges", {}).get(bridge)
        if not prev or "rates" not in cur or "rates" not in prev:
            continue
        for metric, limit, direction in [
            ("unresolved_pct", rise, "rise"),
            ("ambiguous_pct", amb, "rise"),
            ("exact_or_equivalent_pct", fall, "fall"),
        ]:
            delta = cur["rates"][metric] - prev["rates"][metric]
            bad = delta > limit if direction == "rise" else -delta > limit
            row = {
                "check": f"{metric}.{bridge}",
                "delta_points": round(delta, 3),
                "limit_points": limit,
                "status": "fail" if bad else "pass",
            }
            if bad:
                for approval in thresholds.get("approved_rate_changes", []):
                    expected = float(approval.get("expected_delta_points", -1))
                    tolerance = float(approval.get("tolerance_points", 0))
                    if (
                        approval.get("from_matching_rule_version")
                        == previous.get("matching_rule_version")
                        and approval.get("to_matching_rule_version")
                        == current.get("matching_rule_version")
                        and approval.get("bridge") == bridge
                        and approval.get("metric") == metric
                        and abs(delta - expected) <= tolerance
                    ):
                        row["status"] = "pass"
                        row["approved_migration"] = True
                        row["rationale"] = approval.get("rationale")
                        break
            out.append(row)
        delta = cur["rates"]["exact_or_equivalent_pct"] - prev["rates"]["exact_or_equivalent_pct"]
        if delta > jump and not rule_bump:
            out.append(
                {"check": f"exact_rate_unexplained_jump.{bridge}",
                 "delta_points": round(delta, 3), "limit_points": jump,
                 "status": "fail",
                 "detail": "exact rate rose sharply with no matching-rule version bump; "
                           "suspected loss of strictness"}
            )
    return out


def evaluate(report: QualityReport, strict: bool) -> bool:
    """Set ``report.passed`` and, in strict mode, raise on the first failure.

    Returns whether the build passed, so the caller can refuse to promote a
    failed build even in lenient mode.
    """
    failures = [t for t in report.thresholds if t.get("status") == "fail"]
    report.passed = not failures
    if failures:
        log.error("quality gates failed", count=len(failures), failures=failures[:10])
        if strict:
            first = failures[0]
            if str(first.get("check", "")).startswith(("genesis_minimum", "row_count")):
                raise RowCountAnomaly("quality gate failed", **first)
            raise ValidationFailed("quality gate failed", **first)
    else:
        log.info("quality gates passed", checks=len(report.thresholds))
    return report.passed


def write_reports(report: QualityReport, json_path: Path, md_path: Path) -> None:
    with stage_context("quality", "report"):
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8", newline="\n",
        )
        md_path.write_text(_render_markdown(report), encoding="utf-8", newline="\n")
        log.info("wrote quality reports", json=str(json_path), md=str(md_path))


def _render_markdown(r: QualityReport) -> str:
    lines = [
        "# QUALITY_REPORT",
        "",
        f"- Code version: `{r.code_version}`",
        f"- Data version: `{r.data_version}`",
        f"- Matching rule version: `{r.matching_rule_version}`",
        f"- Built at: {r.built_at}",
        f"- Gates: **{'PASS' if r.passed else 'FAIL'}**",
        "",
        "This project does not target a 100% match rate. `ambiguous` and `unresolved`",
        "are normal outcomes: they are how the database avoids being confidently wrong.",
        "",
        "## Sources",
        "",
        "| Dataset | Version | Rows | SHA-256 (first 16) | License |",
        "|---|---|---:|---|---|",
    ]
    for name, s in sorted(r.sources.items()):
        lines.append(
            f"| {name} | {s.get('source_version') or '—'} | {s.get('row_count') or '—'} "
            f"| `{(s.get('sha256') or '')[:16]}` | {s.get('license_name') or '—'} |"
        )

    lines += ["", "## Tables", "", "| Table | Rows | Duplicates |", "|---|---:|---:|"]
    for name, t in sorted(r.tables.items()):
        lines.append(f"| {name} | {t['row_count']:,} | {t['duplicate_count']:,} |")

    lines += [
        "", "## Bridges", "",
        "| Bridge | Rows | exact+equiv | ambiguous | unresolved | auto | review |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, b in sorted(r.bridges.items()):
        if not b.get("row_count"):
            lines.append(f"| {name} | 0 | — | — | — | — | — |")
            continue
        rates = b["rates"]
        lines.append(
            f"| {name} | {b['row_count']:,} | {rates['exact_or_equivalent_pct']}% "
            f"| {rates['ambiguous_pct']}% | {rates['unresolved_pct']}% "
            f"| {b['auto_accept_count']:,} | {b['review_required_count']:,} |"
        )

    if r.identity:
        lines += [
            "", "## Identity", "",
            f"- minted: {r.identity.get('minted', 0):,}",
            f"- reused: {r.identity.get('reused', 0):,}",
            f"- retired: {r.identity.get('retired', 0):,}",
            f"- review required: {r.identity.get('review_required', 0):,}",
        ]

    lines += ["", "## Gate results", "", "| Check | Status | Detail |", "|---|---|---|"]
    for t in r.thresholds:
        detail = ", ".join(
            f"{k}={v}" for k, v in t.items() if k not in ("check", "status")
        )
        lines.append(f"| `{t['check']}` | {t['status'].upper()} | {detail} |")
    return "\n".join(lines) + "\n"


def write_review_queue(bridges: dict[str, pl.DataFrame], path: Path) -> int:
    """Rows a human should look at, most impactful first (spec §70).

    Only rows where a human decision could actually change the outcome. Listing
    every non-`auto` row produced 1.66 million entries — mostly `unresolved`
    rows meaning "MIC does not state town-level assignment" or "no MLIT
    counterpart exists", which no reviewer can act on and which are already
    queryable through `unmatched_records`. A queue nobody can work through is
    not a queue.

    Also excluded: correct edges that simply sit below the auto-accept bar —
    P5's chome fan-out, P4 at 0.97, P2/P3's official municipality mappings. They
    are not uncertain, they are just not auto-accepted, and their counts belong
    in the quality report rather than in someone's worklist.

    What remains: name disagreements between publishers (M2), genuine ambiguity
    (P6a/P6b/M4/T6), and partial-coverage overlaps (P4p/T3).
    """
    frames = []
    for name, df in bridges.items():
        if df.is_empty():
            continue
        sel = df.filter(
            (pl.col("verification_status") == "review_required")
            # Derived rows restate a municipality-level assertion; there is
            # nothing for a reviewer to decide about them.
            & pl.col("derivation").is_null()
            & (
                # Genuinely uncertain outcomes...
                pl.col("relation_type").is_in(["ambiguous", "overlap", "candidate"])
                # ...plus M2, where two publishers give the same coded town
                # different names and someone has to say which is right.
                | (pl.col("matching_rule_id") == "M2")
            )
        )
        if sel.is_empty():
            continue
        frames.append(
            sel.select(
                [
                    pl.lit(name).alias("bridge"),
                    pl.col("bridge_id"),
                    pl.col("address_id"),
                    pl.col("lg_code"),
                    pl.col("target_id"),
                    pl.col("relation_type"),
                    pl.col("match_method"),
                    pl.col("matching_rule_id"),
                    pl.col("confidence"),
                    pl.col("candidate_group_id"),
                    pl.col("candidate_count"),
                    pl.col("candidate_count_is_complete"),
                    pl.col("mismatch_note").alias("reason"),
                ]
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    if not frames:
        path.write_text("bridge,bridge_id\n", encoding="utf-8")
        return 0
    out = pl.concat(frames, how="vertical").sort(
        ["candidate_count", "bridge", "bridge_id"], descending=[True, False, False]
    )
    out.write_csv(path)
    log.info(
        "wrote review queue",
        path=str(path), rows=out.height,
        by_rule=dict(out.group_by("matching_rule_id").agg(pl.len()).iter_rows()),
    )
    return out.height
