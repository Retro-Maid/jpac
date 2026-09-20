"""バス停留所 → 市区町村 (docs/POLICY.md §3.1).

The sibling of ``build/station.py``, and deliberately the simpler one: a bus stop
is a single published point, so there is no representative point to derive and no
fallback to choose. The polygon either encloses the point or it does not.

That difference is visible in the schema. ``bridge_station_municipality`` carries
``placement_point`` because a station is a set of polylines and the answer
depends on which point was used; this table has no such column, because there is
nothing to disclose — the publisher gave the point.

Everything else is held to the station bridge's rules, and for the same reasons:

* **A stop in no polygon stays unresolved.** Snapping to the nearest municipality
  would be confirming a match on proximity, which ``docs/POLICY.md`` §4 forbids.
* **A stop in two stays ambiguous**, with both candidates kept.
* **A polygon whose code jpac no longer has** keeps its row with ``lg_code`` NULL
  and the 5-digit code retained, so a cross-section difference stays visible
  instead of being silently dropped.
* **Nothing is attributed to a 水面調査区**; those polygons are excluded before
  the index is built (``estat.is_land_polygon``).

Granularity stops at the municipality. There is no ``address_id`` column and
there must not be — the reasoning is in ``station.py`` and it is unchanged here.
"""

from __future__ import annotations

import polars as pl

from ..logging_setup import get_logger, stage_context
from .lineage import lineage_note, resolve_candidates, via_lineage_method
from .spatial import GridIndex, point_in_rings

log = get_logger(__name__)

RELATION_CONTAINS = "contains"
RELATION_AMBIGUOUS = "ambiguous"
RELATION_UNRESOLVED = "unresolved"

MATCH_SPATIAL = "spatial_containment"
MATCH_UNRESOLVED = "unresolved"


def build_bus_stop_bridge(
    stops: pl.DataFrame,
    geometry: dict[str, tuple[float, float]],
    boundaries: list[tuple[str, tuple[float, float, float, float], list]],
    lg_by_jis: dict[str, str],
    successors: dict[str, list[str]] | None = None,
) -> pl.DataFrame:
    """One row per (bus stop, candidate municipality); unresolved stops kept.

    ``geometry`` maps ``p11_stop_id`` to the stop's published point. A stop with
    no entry is treated as unresolved rather than skipped: dropping it would make
    "we have no geometry for this stop" indistinguishable from "this stop is in
    no municipality", and the two are not the same claim.
    """
    with stage_context("busstop", "join"):
        codes = [c for c, _, _ in boundaries]
        ringsets = [r for _, _, r in boundaries]
        index = GridIndex(box for _, box, _ in boundaries)

        rows: list[dict] = []
        missing_geometry = 0
        for record in stops.iter_rows(named=True):
            point = geometry.get(record["p11_stop_id"])
            if point is None:
                missing_geometry += 1
                rows.append(_row(record, None, RELATION_UNRESOLVED, MATCH_UNRESOLVED, 0))
                continue
            x, y = point
            hit = {codes[i] for i in index.candidates(x, y) if point_in_rings(x, y, ringsets[i])}
            candidates = sorted(hit)
            if not candidates:
                rows.append(_row(record, None, RELATION_UNRESOLVED, MATCH_UNRESOLVED, 0))
                continue
            # 候補数は解決後の数で数える。承継先が2つある旧コード（旧浜松市北区）は
            # ポリゴン1つでも候補2つになるため（build/lineage.py）。
            resolved = [
                (code, lg, via)
                for code in candidates
                for lg, via in resolve_candidates(code, lg_by_jis, successors)
            ]
            relation = RELATION_CONTAINS if len(resolved) == 1 else RELATION_AMBIGUOUS
            for code, lg, via in resolved:
                rows.append(
                    _row(record, code, relation, MATCH_SPATIAL, len(resolved),
                         lg_code=lg, via_lineage=via)
                )

        bridge = pl.DataFrame(rows, schema=_SCHEMA).sort(
            ["p11_stop_id", "boundary_jis_city_code"]
        )
        resolved = bridge.filter(pl.col("relation_type") != RELATION_UNRESOLVED)
        log.info(
            "built bus stop -> municipality bridge",
            stops=stops.height,
            rows=bridge.height,
            contains=bridge.filter(pl.col("relation_type") == RELATION_CONTAINS).height,
            ambiguous=resolved.filter(pl.col("candidate_count") > 1)["p11_stop_id"].n_unique(),
            unresolved=bridge.filter(pl.col("relation_type") == RELATION_UNRESOLVED).height,
            missing_geometry=missing_geometry,
            outside_jpac=bridge.filter(
                pl.col("boundary_jis_city_code").is_not_null() & pl.col("lg_code").is_null()
            )["p11_stop_id"].n_unique(),
            # drop_nulls() が要る: n_unique() は null を1種類として数えるので、
            # 断面のズレで lg_code が NULL の行があるだけで +1 される。
            municipalities_with_a_stop=resolved["lg_code"].drop_nulls().n_unique(),
        )
        return bridge


_SCHEMA = {
    "p11_stop_id": pl.Utf8,
    "stop_name_raw": pl.Utf8,
    "operator_name_raw": pl.Utf8,
    # Two codes, for the same reason bridge_station_municipality carries two.
    "lg_code": pl.Utf8,
    "boundary_jis_city_code": pl.Utf8,
    "relation_type": pl.Utf8,
    "match_method": pl.Utf8,
    "confidence": pl.Float64,
    "candidate_count": pl.Int64,
    "is_unique_match": pl.Int64,
    "verification_status": pl.Utf8,
    "mismatch_note": pl.Utf8,
}


def _row(record, code, relation, method, candidate_count, lg_code=None,
         via_lineage=False) -> dict:
    """One bridge row. ``auto`` means a single candidate whose code jpac has."""
    unique = candidate_count == 1
    settled = unique and lg_code is not None
    return {
        "p11_stop_id": record["p11_stop_id"],
        "stop_name_raw": record["stop_name_raw"],
        "operator_name_raw": record["operator_name_raw"],
        "lg_code": lg_code,
        "boundary_jis_city_code": code,
        "relation_type": relation,
        # 承継記録を経たことは列に残す。注記にすると auto の CHECK に抵触する。
        "match_method": via_lineage_method(method) if via_lineage else method,
        # Containment held or it did not (docs/POLICY.md §6); never a probability.
        "confidence": 1.0 if relation != RELATION_UNRESOLVED else 0.0,
        "candidate_count": candidate_count,
        "is_unique_match": 1 if unique else 0,
        "verification_status": "auto" if settled else "review_required",
        "mismatch_note": _note(
            relation, unique, lg_code is not None, code, lg_code, via_lineage
        ),
    }


def _note(relation: str, unique: bool, resolved: bool, code: str | None,
          lg_code: str | None = None, via_lineage: bool = False) -> str | None:
    if relation == RELATION_UNRESOLVED:
        return "どのポリゴンにも含まれない。最寄りへの割り当てはしない（POLICY.md §4）"
    # 分割を経た行だけ注記を付ける。1:1 の経路は match_method が持つ。
    if via_lineage and lg_code and not unique:
        return lineage_note(code, lg_code)
    if not resolved:
        return (
            f"境界データ側のコード {code} が jpac の現行市区町村に無いため lg_code は NULL。"
            "断面のズレ。boundary_jis_city_code と "
            "overrides/municipality_lineage.yml を参照"
        )
    if not unique:
        return "複数の市区町村に含まれる。候補を残す（POLICY.md §4）"
    return None
