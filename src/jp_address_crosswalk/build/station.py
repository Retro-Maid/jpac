"""駅 → 市区町村 (docs/STATION_JOIN_PLAN.md §4).

One spatial operation in the whole chain, and it happens here: a station's
representative point is placed in an e-Stat 小地域 polygon, the polygon carries
``PREF``+``CITY``, and everything after that is a key join into tables jpac
already has.

What this module refuses to do is as much of the design as what it does.

* **A station that lands in no polygon stays unresolved.** Snapping it to the
  nearest municipality would be confirming a match on proximity, which
  docs/POLICY.md §4 forbids outright. Measured: 4 stations, all on reclaimed
  land or water-borne track that the 2020 census tracts do not cover.
* **A station that lands in two stays ambiguous**, with both candidates kept.
  Measured: 1 — 大阪空港, because 伊丹空港 genuinely straddles the 大阪府/兵庫県
  border. That is the data being right, not a defect to resolve.
* **Nothing is attributed to a 水面調査区.** ``HCODE`` 8154 is 港湾区域 and 漁港
  の水域 (国勢調査の調査区の設定の基準等に関する省令 第一条4項), not land, so those
  polygons are excluded before the index is built.

Granularity stops at the municipality. There is no ``address_id`` column here
and there must not be: e-Stat's 231,668 町丁・字等 are ~3.1x coarser than jpac's
726,170 町字, so pushing a station down to a 町字 would be expanding a
municipality-level statement, which POLICY.md §4 lists as a defect.
"""

from __future__ import annotations

import polars as pl

from ..logging_setup import get_logger, stage_context
from .lineage import lineage_note, resolve_candidates, via_lineage_method
from .spatial import GridIndex, line_centroid, point_in_rings, point_on_line

log = get_logger(__name__)

RELATION_CONTAINS = "contains"
RELATION_AMBIGUOUS = "ambiguous"
RELATION_UNRESOLVED = "unresolved"

MATCH_SPATIAL = "spatial_containment"
MATCH_UNRESOLVED = "unresolved"


def _placement(parts, index: GridIndex, codes: list[str], ringsets: list) -> tuple[set[str], str]:
    """Municipality codes containing the station, and which point found them.

    The centroid is tried first because it is the station's own middle. When it
    falls outside the geometry — a platform bent around a building, or two
    platforms across a river — a point guaranteed to be *on* the line is the
    second attempt. Measured on the 2025 edition the fallback rescues nothing;
    it is kept because the geometry that defeats a centroid is ordinary, and a
    silent miss is worse than a cheap retry.
    """
    for point, how in (
        (line_centroid(parts), "centroid"),
        (point_on_line(parts), "point_on_line"),
    ):
        if point is None:
            continue
        x, y = point
        hit = {codes[i] for i in index.candidates(x, y) if point_in_rings(x, y, ringsets[i])}
        if hit:
            return hit, how
    return set(), "none"


def build_station_bridge(
    stations: pl.DataFrame,
    geometry: dict[str, list],
    boundaries: list[tuple[str, tuple[float, float, float, float], list]],
    lg_by_jis: dict[str, str],
    successors: dict[str, list[str]] | None = None,
) -> pl.DataFrame:
    """One row per (station, candidate municipality); unresolved stations kept.

    ``boundaries`` is ``(jis_city_code, bbox, rings)`` for land polygons only.
    ``geometry`` maps ``n02_group_code`` to the polyline parts of its features.
    ``lg_by_jis`` turns the polygon's 5-digit JIS code into the 6-digit
    ``lg_code`` the rest of the schema joins on; a code the current
    municipalities no longer contain simply has no entry.
    """
    with stage_context("station", "join"):
        codes = [c for c, _, _ in boundaries]
        ringsets = [r for _, _, r in boundaries]
        index = GridIndex(box for _, box, _ in boundaries)

        rows: list[dict] = []
        counts: dict[str, int] = {}
        for record in stations.iter_rows(named=True):
            gid = record["n02_group_code"]
            hit, how = _placement(geometry.get(gid, []), index, codes, ringsets)
            # A code the boundary set has but jpac does not is a cross-section
            # difference, not a match. Kept as a candidate so it is visible.
            candidates = sorted(hit)
            counts[how] = counts.get(how, 0) + 1
            if not candidates:
                rows.append(_row(record, None, RELATION_UNRESOLVED, MATCH_UNRESOLVED, 0, how))
                continue
            # 候補数は「含有したポリゴンの数」ではなく「解決後の候補の数」で数える。
            # 旧浜松市北区のように承継先が2つある旧コードは、ポリゴン1つでも候補2つに
            # なるからである（build/lineage.py）。
            resolved = [
                (code, lg, via)
                for code in candidates
                for lg, via in resolve_candidates(code, lg_by_jis, successors)
            ]
            relation = RELATION_CONTAINS if len(resolved) == 1 else RELATION_AMBIGUOUS
            for code, lg, via in resolved:
                rows.append(
                    _row(record, code, relation, MATCH_SPATIAL, len(resolved), how,
                         lg_code=lg, via_lineage=via)
                )

        bridge = pl.DataFrame(rows, schema=_SCHEMA).sort(
            ["n02_group_code", "boundary_jis_city_code"]
        )
        resolved = bridge.filter(pl.col("relation_type") != RELATION_UNRESOLVED)
        log.info(
            "built station -> municipality bridge",
            stations=stations.height,
            rows=bridge.height,
            contains=bridge.filter(pl.col("relation_type") == RELATION_CONTAINS).height,
            ambiguous=resolved.filter(pl.col("candidate_count") > 1)["n02_group_code"].n_unique(),
            unresolved=bridge.filter(pl.col("relation_type") == RELATION_UNRESOLVED).height,
            outside_jpac=bridge.filter(
                pl.col("boundary_jis_city_code").is_not_null() & pl.col("lg_code").is_null()
            )["n02_group_code"].n_unique(),
            by_point=counts,
            # drop_nulls() が要る: n_unique() は null を1種類として数えるので、
            # 断面のズレ（旧浜松市の区、54行）で lg_code が NULL になる行があるだけで
            # +1 される。1,403 と報告していたのは実際には 1,402 だった。
            municipalities_with_a_station=resolved["lg_code"].drop_nulls().n_unique(),
        )
        return bridge


_SCHEMA = {
    "n02_group_code": pl.Utf8,
    "station_name_raw": pl.Utf8,
    "operator_name_raw": pl.Utf8,
    # Two codes on purpose, and they are not the same kind of thing.
    #
    # `lg_code` is the 6-digit 全国地方公共団体コード every other table in this
    # project means by that name, so a join to `municipality` resolves here the
    # way it does everywhere else. It is NULL when the boundary edition names a
    # municipality that no longer exists.
    #
    # `boundary_jis_city_code` is the 5-digit JIS code the polygon actually
    # carried. Keeping it is what makes the NULL above recoverable rather than a
    # silent loss (CLAUDE.md §5): the 54 旧浜松区 rows still say which area of
    # the 2020 census they came from.
    "lg_code": pl.Utf8,
    "boundary_jis_city_code": pl.Utf8,
    "relation_type": pl.Utf8,
    "match_method": pl.Utf8,
    "confidence": pl.Float64,
    "candidate_count": pl.Int64,
    "is_unique_match": pl.Int64,
    "verification_status": pl.Utf8,
    "placement_point": pl.Utf8,
    "mismatch_note": pl.Utf8,
}


def _row(record, code, relation, method, candidate_count, how, lg_code=None,
         via_lineage=False) -> dict:
    """One bridge row.

    ``verification_status`` is ``auto`` only for a station contained by exactly
    one municipality whose code jpac also has. The address bridges restrict
    ``auto`` to ``exact``/``equivalent`` because those assert that two
    identifiers name the same thing, and a machine should not assert identity
    unreviewed. Containment is a different claim: the geometry either encloses
    the point or it does not, the test is deterministic, and there is nothing a
    reviewer could add. ``manually_verified`` is never written by code — that
    value means a person looked, and none has.
    """
    unique = candidate_count == 1
    resolved = lg_code is not None
    settled = unique and resolved
    return {
        "n02_group_code": record["n02_group_code"],
        "station_name_raw": record["station_name_raw"],
        "operator_name_raw": record["operator_name_raw"],
        "lg_code": lg_code,
        "boundary_jis_city_code": code,
        "relation_type": relation,
        # 承継記録を経たことは列に残す。注記にすると auto の CHECK に抵触する。
        "match_method": via_lineage_method(method) if via_lineage else method,
        # Not a probability (docs/POLICY.md §6). Containment either held or it
        # did not, so the only two values are 1.0 and 0.0 — an ambiguous station
        # is two rows each of which is a true containment, distinguished by
        # candidate_count rather than by a softened score.
        "confidence": 1.0 if relation != RELATION_UNRESOLVED else 0.0,
        "candidate_count": candidate_count,
        # Geometric uniqueness. A code jpac lacks is still a unique containment;
        # what is unsettled there is the cross-section, recorded in the note.
        "is_unique_match": 1 if unique else 0,
        "verification_status": "auto" if settled else "review_required",
        "placement_point": how,
        "mismatch_note": _note(relation, unique, resolved, code, lg_code, via_lineage),
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
