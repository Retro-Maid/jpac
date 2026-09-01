"""Diff against the previous release (docs/QUALITY_POLICY.md §7, spec §42).

A removal that no lineage row or source-level removal explains is
``DATA_LOSS_SUSPECTED`` and blocks the release. There is no percentage
allowance: "under 1% is fine" would have let thousands of canonical addresses
disappear quietly.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from ..errors import DataLossSuspected
from ..logging_setup import get_logger, stage_context

log = get_logger(__name__)


def _read(prev_dir: Path, name: str) -> pl.DataFrame | None:
    path = prev_dir / f"{name}.parquet"
    if not path.exists():
        return None
    return pl.read_parquet(path)


def build_diff(
    tables: dict[str, pl.DataFrame], previous_dir: Path | None, strict: bool
) -> dict:
    with stage_context("diff", "compare"):
        if previous_dir is None or not previous_dir.exists():
            log.info("no previous release; diff is a baseline record")
            return {
                "baseline": True,
                "detail": "no previous release directory; nothing to compare",
                "current_counts": {k: v.height for k, v in sorted(tables.items())},
            }

        report: dict = {"baseline": False, "sections": {}}

        addr_prev = _read(previous_dir, "address")
        addr_cur = tables.get("address")
        if addr_prev is not None and addr_cur is not None:
            report["sections"]["address"] = _diff_addresses(
                addr_prev, addr_cur, tables.get("address_lineage"), strict
            )

        for name, key in [
            ("postal_code_entity", "postal_code"),
            ("mlit_town", "mlit_record_id"),
            ("telephone_area", "numbering_area_code"),
        ]:
            prev, cur = _read(previous_dir, name), tables.get(name)
            if prev is not None and cur is not None and key in prev.columns:
                report["sections"][name] = _diff_keys(prev, cur, key)

        for name in [
            "bridge_address_postal_code", "bridge_address_postal",
            "bridge_address_mlit", "bridge_address_telephone",
            "bridge_municipality_postal", "bridge_municipality_telephone",
        ]:
            prev, cur = _read(previous_dir, name), tables.get(name)
            if prev is not None and cur is not None:
                report["sections"][name] = _diff_bridge(prev, cur)

        log.info("diff built", sections=list(report["sections"]))
        return report


def _diff_keys(prev: pl.DataFrame, cur: pl.DataFrame, key: str) -> dict:
    p = set(prev[key].to_list())
    c = set(cur[key].to_list())
    added, removed = sorted(c - p), sorted(p - c)
    return {
        "added_count": len(added), "removed_count": len(removed),
        "added_sample": added[:25], "removed_sample": removed[:25],
    }


def _diff_addresses(
    prev: pl.DataFrame, cur: pl.DataFrame, lineage: pl.DataFrame | None, strict: bool
) -> dict:
    p_ids = set(prev["address_id"].to_list())
    c_ids = set(cur["address_id"].to_list())
    added, removed = sorted(c_ids - p_ids), sorted(p_ids - c_ids)

    explained: set[str] = set()
    if lineage is not None and lineage.height:
        explained = set(
            lineage.filter(pl.col("old_address_id").is_not_null())["old_address_id"].to_list()
        )
    unexplained = [a for a in removed if a not in explained]

    # Attribute changes on surviving ids.
    joined = prev.select(["address_id", "lg_code", "machiaza_id", "full_name_raw"]).join(
        cur.select(
            [
                "address_id",
                pl.col("lg_code").alias("lg_code_new"),
                pl.col("machiaza_id").alias("machiaza_id_new"),
                pl.col("full_name_raw").alias("full_name_new"),
            ]
        ),
        on="address_id", how="inner",
    )
    renamed = joined.filter(pl.col("full_name_raw") != pl.col("full_name_new"))
    recoded = joined.filter(
        (pl.col("lg_code") != pl.col("lg_code_new"))
        | (pl.col("machiaza_id") != pl.col("machiaza_id_new"))
    )

    out = {
        "added_count": len(added),
        "removed_count": len(removed),
        "removed_explained_count": len(removed) - len(unexplained),
        "removed_unexplained_count": len(unexplained),
        "renamed_count": renamed.height,
        "code_changed_count": recoded.height,
        "added_sample": added[:25],
        "removed_unexplained_sample": unexplained[:25],
        "renamed_sample": renamed.head(25).to_dicts(),
        "code_changed_sample": recoded.head(25).to_dicts(),
    }
    if unexplained:
        log.error(
            "addresses removed with no lineage explanation",
            count=len(unexplained), sample=unexplained[:10],
        )
        if strict:
            raise DataLossSuspected(
                "addresses disappeared with no lineage row or source-level removal",
                count=len(unexplained), sample=unexplained[:10],
            )
    return out


def data_loss_threshold(report: dict) -> list[dict]:
    """Turn unexplained removals into a gate row.

    Raising inside build_diff only protects strict mode. Expressing it as a
    threshold means lenient mode also records the failure, so a build that lost
    addresses can never be promoted as the next baseline
    (docs/QUALITY_POLICY.md §7).
    """
    section = (report.get("sections") or {}).get("address") or {}
    unexplained = int(section.get("removed_unexplained_count", 0))
    return [
        {
            "check": "addresses_removed_without_lineage",
            "observed": unexplained,
            "limit": 0,
            "status": "pass" if unexplained == 0 else "fail",
        }
    ]


def _diff_bridge(prev: pl.DataFrame, cur: pl.DataFrame) -> dict:
    p = prev.select(["bridge_id", "relation_type", "confidence", "verification_status"])
    c = cur.select(
        [
            "bridge_id",
            pl.col("relation_type").alias("relation_new"),
            pl.col("confidence").alias("confidence_new"),
            pl.col("verification_status").alias("status_new"),
        ]
    )
    both = p.join(c, on="bridge_id", how="inner")
    return {
        "added_count": cur.height - both.height,
        "removed_count": prev.height - both.height,
        "relation_changed_count": both.filter(
            pl.col("relation_type") != pl.col("relation_new")
        ).height,
        "confidence_changed_count": both.filter(
            pl.col("confidence") != pl.col("confidence_new")
        ).height,
        "status_changed_count": both.filter(
            pl.col("verification_status") != pl.col("status_new")
        ).height,
    }


def write_diff(report: dict, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8", newline="\n",
    )
    lines = ["# DIFF_REPORT", ""]
    if report.get("baseline"):
        lines += [
            "No previous release to compare against; this is the baseline.",
            "",
            "| Table | Rows |", "|---|---:|",
        ]
        for k, v in sorted(report.get("current_counts", {}).items()):
            lines.append(f"| {k} | {v:,} |")
    else:
        for section, data in sorted(report.get("sections", {}).items()):
            lines += [f"## {section}", "", "| Metric | Value |", "|---|---:|"]
            for k, v in sorted(data.items()):
                if k.endswith("_sample"):
                    continue
                lines.append(f"| {k} | {v} |")
            lines.append("")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    log.info("wrote diff reports", json=str(json_path), md=str(md_path))
