"""鉄道路線 → 市区町村 と 駅 → 路線 (docs/POLICY.md §3.1).

``POLICY.md`` §3.1 already names 鉄道路線 as a V2 subject, and the adapter has
been parsing ``n02_railroad_section`` since that extension was written — it was
simply never built into a table. This module is that missing half.

Three decisions carry the design.

**A line's identity is (路線名, 運営会社), and nothing more.** N02 gives stations
a publisher grouping code (``N02_005g``) but gives lines none, so the identity
has to be assembled. The tempting composite is all four attributes, and it is
wrong: 10 lines carry two 鉄道区分 values across their own sections — 富山地方
鉄道の本線 is both ``12`` (普通鉄道) and ``21`` (軌道) — so including the class
columns splits 597 real lines into 607 rows. That is the same inflation
``N02_005g`` exists to prevent for stations, arrived at from the other
direction. The class columns are kept as "one of N" values beside
``*_variants`` counts, exactly as the station aggregate does.

**One publisher record has 路線名 and 運営会社 reversed, and the fix is derived,
not hand-written.** The 路線 file carries ``N02_003='えちぜん鉄道'``,
``N02_004='三国芦原線'`` — operator and line name swapped — while the 駅 file
carries that same line the right way round. So the rule is: *a (路線名, 運営会社)
pair that appears in the 路線 file only in reversed form relative to the 駅 file
takes the 駅 file's orientation.* Nothing is invented and no override list is
maintained; the 駅 file settles it. The anomaly is reported rather than silently
absorbed, and ``tests/test_railroad.py`` pins the count at exactly one so a
second occurrence fails loudly instead of being swallowed by the same rule.

**"Passes through" is sampled, and the step was chosen by measurement rather
than assumed.** A station is one point and a polygon either encloses it or does
not. A line is 27,845 km of track whose segments are median 47 m but run to
8.4 km, so testing vertices alone would miss a municipality the track crosses
between two distant vertices. Every vertex is tested, plus points interpolated
along any segment longer than ``SAMPLE_STEP_M``.

The step is load-bearing, which is why it is not a round guess: at 100 m the
京阪本線 misses 久御山町 (26322) altogether — the track clips the town between
two sample points — and a finer run finds it. The value used here is the one the
nationwide comparison in ``docs/RAILROAD_LINE_PLAN.md`` converged on. Sampling
cannot promise that no municipality is ever clipped between two points; what it
can do is state the step at which refining stops changing the answer, and that
measurement is what the doc records.

Granularity stops at the municipality for the same reason it does in
``station.py``: there is no ``address_id`` column here and there must not be.
"""

from __future__ import annotations

import math

import polars as pl

from ..logging_setup import get_logger, stage_context
from .spatial import GridIndex, point_in_rings

log = get_logger(__name__)

RELATION_OVERLAP = "overlap"
RELATION_UNRESOLVED = "unresolved"

MATCH_SAMPLED = "spatial_sampling"
MATCH_UNRESOLVED = "unresolved"
MATCH_ATTRIBUTE = "n02_attribute"

# Metres between interpolated points on a segment longer than this. Measured,
# not chosen: 100 m loses 久御山町 from the 京阪本線, and 25/10/5/2 m all return
# the identical 3,380 rows — so this is the coarsest step that still converges,
# and the finer ones only cost time (68 s here against 710 s at 2 m).
SAMPLE_STEP_M = 25.0
_M_PER_DEG = 111_320.0

LINE_KEY = ["line_name_raw", "operator_name_raw"]


def detect_swapped_pairs(
    sections: pl.DataFrame, features: pl.DataFrame
) -> list[tuple[str, str]]:
    """(路線名, 運営会社) pairs the 路線 file carries in reversed form.

    Derived wholly from the two files: a pair present in the sections and absent
    from the stations, whose reverse *is* present in the stations, is reversed.
    A genuinely station-less line does not qualify, because its reverse would
    not appear on a station either.
    """
    sec = set(zip(sections["line_name_raw"], sections["operator_name_raw"], strict=True))
    sta = set(zip(features["line_name_raw"], features["operator_name_raw"], strict=True))
    return sorted((a, b) for (a, b) in sec - sta if (b, a) in sta)


def correct_pairs(df: pl.DataFrame, swapped: list[tuple[str, str]]) -> pl.DataFrame:
    """Put the reversed rows the right way round, leaving every other row alone."""
    if not swapped:
        return df
    hit = pl.any_horizontal(
        [
            (pl.col("line_name_raw") == a) & (pl.col("operator_name_raw") == b)
            for a, b in swapped
        ]
    )
    return df.with_columns(
        pl.when(hit).then(pl.col("operator_name_raw")).otherwise(pl.col("line_name_raw"))
        .alias("line_name_raw"),
        pl.when(hit).then(pl.col("line_name_raw")).otherwise(pl.col("operator_name_raw"))
        .alias("operator_name_raw"),
    )


def build_railroad_lines(
    sections: pl.DataFrame, features: pl.DataFrame
) -> tuple[pl.DataFrame, list[tuple[str, str]]]:
    """One row per 路線, plus the reversed pairs that were corrected."""
    with stage_context("railroad", "lines"):
        swapped = detect_swapped_pairs(sections, features)
        if swapped:
            # Reported, never silent: a second occurrence means the rule is
            # covering something it was not derived for.
            log.warning(
                "N02 路線 records carry 路線名/運営会社 reversed; corrected from the "
                "station file's orientation",
                pairs=[f"{a} / {b}" for a, b in swapped],
                count=len(swapped),
            )
        lines = (
            correct_pairs(sections, swapped)
            .sort([*LINE_KEY, "railway_class", "operator_class"])
            .group_by(LINE_KEY)
            .agg(
                pl.len().alias("section_count"),
                # "One of N", like the station aggregate: a line can be part 普通
                # 鉄道 and part 軌道 along its length, so the variants count beside
                # it is what tells a reader the single value is a sample.
                pl.col("railway_class").first(),
                pl.col("operator_class").first(),
                pl.col("railway_class").n_unique().alias("railway_class_variants"),
                pl.col("operator_class").n_unique().alias("operator_class_variants"),
            )
            .sort(LINE_KEY)
        )
        log.info(
            "built railroad lines",
            lines=lines.height,
            sections=sections.height,
            swapped_pairs=len(swapped),
            lines_spanning_classes=lines.filter(
                pl.col("railway_class_variants") > 1
            ).height,
        )
        return lines, swapped


def build_station_line_bridge(features: pl.DataFrame) -> pl.DataFrame:
    """駅グループ → 路線, straight from the attributes.

    No geometry: N02 states each station feature's line on the feature itself.
    A station group legitimately spans several lines (乗換駅), so this is the
    table that makes ``n02_station.line_variants`` navigable instead of merely
    countable — and it is what makes the adapter's claim that nothing is
    discarded by grouping actually true.
    """
    with stage_context("railroad", "station_line"):
        bridge = (
            features.sort(["n02_group_code", *LINE_KEY, "n02_station_code"])
            .group_by(["n02_group_code", *LINE_KEY])
            .agg(
                pl.col("station_name_raw").first(),
                pl.len().alias("feature_count"),
            )
            .with_columns(pl.lit(MATCH_ATTRIBUTE).alias("match_method"))
            .select(
                "n02_group_code", "station_name_raw", "line_name_raw",
                "operator_name_raw", "feature_count", "match_method",
            )
            .sort(["n02_group_code", *LINE_KEY])
        )
        log.info(
            "built station -> line bridge",
            rows=bridge.height,
            stations=bridge["n02_group_code"].n_unique(),
            lines=bridge.select(LINE_KEY).unique().height,
            interchange_stations=bridge.group_by("n02_group_code").len()
            .filter(pl.col("len") > 1).height,
        )
        return bridge


def _metres(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(
        (x2 - x1) * math.cos(math.radians(y1)) * _M_PER_DEG,
        (y2 - y1) * _M_PER_DEG,
    )


def sample_points(part: list[tuple[float, float]], step_m: float = SAMPLE_STEP_M):
    """Every vertex, plus interpolated points on segments longer than ``step_m``."""
    if not part:
        return
    yield part[0]
    # Consecutive pairs, so the second argument is deliberately one shorter.
    for (x1, y1), (x2, y2) in zip(part, part[1:]):  # noqa: B905
        distance = _metres(x1, y1, x2, y2)
        if distance > step_m:
            for k in range(1, int(distance // step_m) + 1):
                t = k * step_m / distance
                if t < 1.0:
                    yield (x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
        yield (x2, y2)


def build_line_municipality_bridge(
    lines: pl.DataFrame,
    geometry: dict[tuple[str, str], list],
    boundaries: list[tuple[str, tuple[float, float, float, float], list]],
    lg_by_jis: dict[str, str],
    step_m: float = SAMPLE_STEP_M,
) -> pl.DataFrame:
    """One row per (路線, 市区町村 it passes through); a line placed nowhere keeps a row.

    ``geometry`` maps the *corrected* (路線名, 運営会社) to its polyline parts.
    The refusals match ``station.py``: a line in no polygon is ``unresolved``
    rather than snapped, and a boundary code jpac no longer has keeps its row
    with ``lg_code`` NULL so the cross-section stays visible.
    """
    with stage_context("railroad", "join"):
        codes = [c for c, _, _ in boundaries]
        ringsets = [r for _, _, r in boundaries]
        index = GridIndex(box for _, box, _ in boundaries)

        rows: list[dict] = []
        sampled = 0
        for record in lines.iter_rows(named=True):
            key = (record["line_name_raw"], record["operator_name_raw"])
            inside: dict[str, int] = {}
            for part in geometry.get(key, []):
                for x, y in sample_points(part, step_m):
                    sampled += 1
                    for i in index.candidates(x, y):
                        if point_in_rings(x, y, ringsets[i]):
                            inside[codes[i]] = inside.get(codes[i], 0) + 1
            if not inside:
                rows.append(_row(record, None, None, len(inside)))
                continue
            for code in sorted(inside):
                rows.append(_row(record, code, inside[code], len(inside),
                                 lg_code=lg_by_jis.get(code)))

        bridge = pl.DataFrame(rows, schema=_SCHEMA).sort(
            [*LINE_KEY, "boundary_jis_city_code"]
        )
        resolved = bridge.filter(pl.col("relation_type") != RELATION_UNRESOLVED)
        log.info(
            "built line -> municipality bridge",
            lines=lines.height,
            rows=bridge.height,
            points_tested=sampled,
            unresolved=bridge.filter(
                pl.col("relation_type") == RELATION_UNRESOLVED
            ).height,
            outside_jpac=bridge.filter(
                pl.col("boundary_jis_city_code").is_not_null() & pl.col("lg_code").is_null()
            ).select(LINE_KEY).unique().height,
            # drop_nulls() が要る: n_unique() は null を1種類として数えるので、
            # 断面のズレで lg_code が NULL の行があるだけで +1 される。
            municipalities_with_a_line=resolved["lg_code"].drop_nulls().n_unique(),
            max_municipalities_per_line=int(
                resolved.group_by(LINE_KEY).len()["len"].max() or 0
            ),
        )
        return bridge


_SCHEMA = {
    "line_name_raw": pl.Utf8,
    "operator_name_raw": pl.Utf8,
    # Same two-code arrangement as bridge_station_municipality, for the same
    # reason: lg_code is what the rest of the schema joins on, and the 5-digit
    # JIS code the polygon carried is what makes a NULL recoverable.
    "lg_code": pl.Utf8,
    "boundary_jis_city_code": pl.Utf8,
    "relation_type": pl.Utf8,
    "match_method": pl.Utf8,
    "confidence": pl.Float64,
    # How many sampled points of this line fell inside this municipality. Not a
    # length and not a share: it is the evidence count behind the row, and it
    # makes a one-point clip distinguishable from a line that runs for 40 km
    # through the municipality.
    "sample_hits": pl.Int64,
    "municipality_count": pl.Int64,
    "verification_status": pl.Utf8,
    "mismatch_note": pl.Utf8,
}


def _row(record, code, hits, count, lg_code=None) -> dict:
    """One bridge row.

    ``auto`` needs a resolved ``lg_code`` and nothing else to note. Unlike the
    station bridge there is no uniqueness requirement: a line running through 40
    municipalities is 40 correct rows, not an ambiguity to resolve.
    """
    unresolved = code is None
    return {
        "line_name_raw": record["line_name_raw"],
        "operator_name_raw": record["operator_name_raw"],
        "lg_code": lg_code,
        "boundary_jis_city_code": code,
        "relation_type": RELATION_UNRESOLVED if unresolved else RELATION_OVERLAP,
        "match_method": MATCH_UNRESOLVED if unresolved else MATCH_SAMPLED,
        # Containment of a sampled point either held or it did not
        # (docs/POLICY.md §6): the only values are 1.0 and 0.0.
        "confidence": 0.0 if unresolved else 1.0,
        "sample_hits": hits,
        "municipality_count": count,
        "verification_status": "auto" if (not unresolved and lg_code) else "review_required",
        "mismatch_note": _note(unresolved, lg_code, code),
    }


def _note(unresolved: bool, lg_code: str | None, code: str | None) -> str | None:
    if unresolved:
        return "どの市区町村のポリゴンにも入らない。最寄りへの割り当てはしない（POLICY.md §4）"
    if lg_code is None:
        return (
            f"境界データ側のコード {code} が jpac の現行市区町村に無いため lg_code は NULL。"
            "断面のズレ。boundary_jis_city_code と "
            "overrides/municipality_lineage.yml を参照"
        )
    return None
