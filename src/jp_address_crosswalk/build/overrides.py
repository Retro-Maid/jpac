"""Manual overrides and stale-override detection (docs/MATCHING_RULES.md §10).

Rules P0/M0/T0 exist so a human decision can beat a rule, and spec §71 requires
that a decision stops applying once the sources it was based on have moved.
Both were documented but not implemented: nothing loaded the file, so a
populated `overrides/manual_overrides.yml` would have had no effect at all, and
`override_stale` was hardcoded false.

An override records the source state it was made against. On every build that
state is re-checked:

* still true  → the override is applied
* no longer true → the override is **not** applied, the row is marked
  ``override_stale`` and returned to the review queue

Silently reapplying a decision made against data that has since changed is the
same class of error as silently resolving an ambiguity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import polars as pl
import yaml

from ..errors import ValidationFailed
from ..logging_setup import get_logger

log = get_logger(__name__)

BRIDGE_TABLES = {
    "bridge_address_postal_code", "bridge_address_postal", "bridge_address_mlit",
    "bridge_address_telephone", "bridge_municipality_postal",
    "bridge_municipality_telephone",
}
ALLOWED_SET_KEYS = {
    "relation_type", "confidence", "verification_status", "match_method",
}
REQUIRED_KEYS = {"id", "bridge", "source", "target", "set", "reason", "created_at"}


@dataclass
class Override:
    id: str
    bridge: str
    source: dict
    target: dict
    set: dict
    reason: str
    evidence: str | None = None
    evidence_url: str | None = None
    observed_source_state: dict = field(default_factory=dict)
    created_at: str | None = None
    created_by: str | None = None


@dataclass
class OverrideOutcome:
    applied: int = 0
    stale: list[dict] = field(default_factory=list)
    unmatched: list[dict] = field(default_factory=list)


def load_overrides(path: Path) -> list[Override]:
    """Load and schema-check the override file. A malformed file fails the build."""
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("overrides") or []
    out: list[Override] = []
    problems: list[str] = []

    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            problems.append(f"override #{i} is not a mapping")
            continue
        missing = REQUIRED_KEYS - set(entry)
        if missing:
            problems.append(f"override #{i}: missing {sorted(missing)}")
            continue
        if entry["bridge"] not in BRIDGE_TABLES:
            problems.append(f"{entry['id']}: unknown bridge {entry['bridge']!r}")
        bad_keys = set(entry["set"]) - ALLOWED_SET_KEYS
        if bad_keys:
            problems.append(f"{entry['id']}: cannot override {sorted(bad_keys)}")
        conf = entry["set"].get("confidence")
        if conf is not None and not (0.0 <= float(conf) <= 1.0):
            problems.append(f"{entry['id']}: confidence {conf} out of range")
        if entry["id"] in seen:
            problems.append(f"duplicate override id {entry['id']!r}")
        seen.add(entry["id"])
        out.append(Override(**{k: v for k, v in entry.items() if k in Override.__annotations__}))

    if problems:
        raise ValidationFailed("manual_overrides.yml is invalid", problems=problems)
    log.info("manual overrides loaded", count=len(out), path=str(path))
    return out


def check_staleness(
    overrides: list[Override], snapshot_sha_by_dataset: dict[str, str]
) -> dict[str, bool]:
    """``override_id -> is_stale``.

    An override with no recorded source state is treated as stale: it cannot be
    shown to still hold, and applying an unverifiable human decision is exactly
    what spec §71 forbids.
    """
    stale: dict[str, bool] = {}
    for o in overrides:
        state = o.observed_source_state or {}
        if not state:
            stale[o.id] = True
            continue
        mismatched = [
            k for k, expected in state.items()
            if snapshot_sha_by_dataset.get(_dataset_for(k)) != expected
        ]
        stale[o.id] = bool(mismatched)
        if mismatched:
            log.warning(
                "override recorded a source state that no longer holds; not applied",
                override=o.id, fields=mismatched,
            )
    return stale


def _dataset_for(state_key: str) -> str:
    """``abr_snapshot_sha256`` -> ``abr_town_master``."""
    return {
        "abr_snapshot_sha256": "abr_town_master",
        "abr_conversion_snapshot_sha256": "abr_postal_conversion",
        "japanpost_snapshot_sha256": "japanpost_ken_all",
        "mlit_snapshot_sha256": "mlit_isj",
        "mic_snapshot_sha256": "mic_shigai_list",
    }.get(state_key, state_key)


def apply_overrides(
    tables: dict[str, pl.DataFrame],
    overrides: list[Override],
    stale: dict[str, bool],
) -> OverrideOutcome:
    """Apply fresh overrides in place; mark stale ones without applying them."""
    outcome = OverrideOutcome()
    if not overrides:
        return outcome

    for o in overrides:
        df = tables.get(o.bridge)
        if df is None or df.is_empty():
            outcome.unmatched.append({"override": o.id, "reason": "bridge absent"})
            continue

        cond = pl.lit(True)
        for col, value in {**o.source, **o.target}.items():
            if col not in df.columns:
                outcome.unmatched.append(
                    {"override": o.id, "reason": f"column {col} absent"}
                )
                cond = None
                break
            cond = cond & (pl.col(col) == value)
        if cond is None:
            continue

        n = df.filter(cond).height
        if n == 0:
            outcome.unmatched.append({"override": o.id, "reason": "no matching row"})
            continue

        if stale.get(o.id, True):
            tables[o.bridge] = df.with_columns(
                [
                    pl.when(cond).then(pl.lit(True)).otherwise(pl.col("override_stale"))
                    .alias("override_stale"),
                    pl.when(cond)
                    .then(pl.lit("review_required"))
                    .otherwise(pl.col("verification_status"))
                    .alias("verification_status"),
                ]
            )
            outcome.stale.append({"override": o.id, "rows": n, "bridge": o.bridge})
            continue

        exprs = [
            pl.when(cond).then(pl.lit(v)).otherwise(pl.col(k)).alias(k)
            for k, v in o.set.items()
        ]
        exprs.append(
            pl.when(cond).then(pl.lit("manual_override")).otherwise(
                pl.col("matching_rule_id")
            ).alias("matching_rule_id")
        )
        tables[o.bridge] = df.with_columns(exprs)
        outcome.applied += n

    log.info(
        "manual overrides processed",
        applied=outcome.applied, stale=len(outcome.stale),
        unmatched=len(outcome.unmatched),
    )
    return outcome


def write_stale_report(outcome: OverrideOutcome, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = outcome.stale + [
        {**u, "rows": 0, "bridge": u.get("bridge", "")} for u in outcome.unmatched
    ]
    if not rows:
        path.write_text("override,bridge,rows,reason\n", encoding="utf-8")
        return
    pl.DataFrame(
        [
            {
                "override": r.get("override"), "bridge": r.get("bridge", ""),
                "rows": r.get("rows", 0),
                "reason": r.get("reason", "recorded source state no longer holds"),
            }
            for r in rows
        ]
    ).sort("override").write_csv(path)
    log.info("wrote stale override report", path=str(path), rows=len(rows))
