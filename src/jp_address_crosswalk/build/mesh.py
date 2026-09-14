"""緯度経度 → 市区町村 (docs/MESH_MUNICIPALITY_LOOKUP.md).

Every containment decision is made here, at build time, and what leaves is a
table of 標準地域メッシュ (JIS X 0410) cells. A consumer computes the mesh code
from a coordinate by arithmetic alone and looks the answer up; no geometry
travels with the result.

The split that makes this worth doing is that a cell either sits wholly inside
one municipality or straddles a boundary, and the first case is the common one.
Measured on the 2020 census boundaries: 380,894 land cells at 3次 (1km), of
which 312,366 (82.0%) are uniform.

Two refusals carried over from ``station.py``, for the same reasons:

* **A cell no polygon covers is not in the table.** It is not assigned to the
  nearest municipality — proximity is not containment (docs/POLICY.md §4). The
  common cause is the prefecture seams e-Stat does not join (§4.2 of the
  publisher's caveats), so snapping would cross a prefecture to do it.
* **A cell two municipalities share yields two rows**, not a winner. Which of
  them owns more of the cell is a question this table does not answer, because
  answering it would state a containment that is not true of the whole cell.

Granularity stops at the municipality, and the reason is the same one that
stops ``station.py``: e-Stat's 231,668 町丁・字等 are ~3.1x coarser than jpac's
726,170 町字, so a cell cannot name an address without expanding a
municipality-level statement into one about a 町字.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

import polars as pl

from ..logging_setup import get_logger, stage_context
from .spatial import GridIndex, point_in_rings

log = get_logger(__name__)

RELATION_CONTAINS = "contains"
RELATION_AMBIGUOUS = "ambiguous"
MATCH_MESH = "mesh_containment"

# JIS X 0410. A 1次 cell is 40 minutes of latitude by one degree of longitude;
# 2次 divides it by 8, 3次 by a further 10.
LAT_1 = 2.0 / 3.0
LNG_1 = 1.0
DIV_3 = 80                      # 8 * 10
DLAT_3 = LAT_1 / DIV_3
DLNG_3 = LNG_1 / DIV_3

# Wide enough that no Japanese longitude index collides with the next latitude
# row when the two are packed into one integer.
_PACK = 1 << 20


def mesh_code(lat: float, lng: float) -> str:
    """3次メッシュコード (8 digits) for a coordinate.

    Arithmetic only — no data, no licence, no cross-section. 東京駅
    (35.681236, 139.767125) is 53394611.
    """
    p, rem_lat = divmod(lat * 1.5, 1.0)
    u = int(lng) - 100
    q, rem_lat = divmod(rem_lat * 8.0, 1.0)
    rem_lng = lng - 100.0 - u
    v, rem_lng = divmod(rem_lng * 8.0, 1.0)
    r = int(rem_lat * 10.0)
    w = int(rem_lng * 10.0)
    return f"{int(p):02d}{u:02d}{int(q)}{int(v)}{r}{w}"


def cell_of(lat: float, lng: float) -> int:
    """Packed (row, col) index of the 3次 cell holding a coordinate."""
    return int(lat // DLAT_3) * _PACK + int(lng // DLNG_3)


def cell_centre(cell: int) -> tuple[float, float]:
    row, col = divmod(cell, _PACK)
    return (col + 0.5) * DLNG_3, (row + 0.5) * DLAT_3


def cell_code(cell: int) -> str:
    """Mesh code of a packed cell, via its centre."""
    x, y = cell_centre(cell)
    return mesh_code(y, x)


def _cells_along(p0: Sequence[float], p1: Sequence[float]) -> Iterable[int]:
    """Cells a segment passes through.

    Sampled at a quarter of a cell rather than traversed analytically. The bias
    is one-directional and known: a segment that only clips a corner can be
    missed, so a cell can be called uniform when it is really shared. It cannot
    invent a crossing, so no cell is called ambiguous that is not.
    """
    dx = abs(p1[0] - p0[0]) / DLNG_3
    dy = abs(p1[1] - p0[1]) / DLAT_3
    steps = max(1, int(max(dx, dy) * 4.0) + 1)
    for i in range(steps + 1):
        t = i / steps
        yield cell_of(p0[1] + (p1[1] - p0[1]) * t, p0[0] + (p1[0] - p0[0]) * t)


def classify_cells(
    boundaries: Sequence[tuple[str, tuple[float, float, float, float], list]],
    progress: Callable[[int, int], None] | None = None,
) -> dict[int, set[str]]:
    """Packed cell -> the JIS city codes that actually reach into it.

    Edges decide the shared cells; a cell no edge enters lies wholly within one
    小地域, so its centre alone identifies it. Cells whose centre falls in no
    polygon are absent rather than present-and-empty — they are sea, or one of
    the prefecture seams, and the caller must not fill them in.
    """
    out: dict[int, set[str]] = {}
    for code, _box, rings in boundaries:
        for ring in rings:
            for a, b in zip(ring, ring[1:], strict=False):
                for cell in _cells_along(a, b):
                    out.setdefault(cell, set()).add(code)

    index = GridIndex(box for _, box, _ in boundaries)
    ringsets = [r for _, _, r in boundaries]
    codes = [c for c, _, _ in boundaries]

    candidates: set[int] = set()
    for _code, (x0, y0, x1, y1), _rings in boundaries:
        for row in range(int(y0 // DLAT_3), int(y1 // DLAT_3) + 1):
            for col in range(int(x0 // DLNG_3), int(x1 // DLNG_3) + 1):
                candidates.add(row * _PACK + col)

    todo = [c for c in candidates if c not in out]
    for n, cell in enumerate(todo, 1):
        x, y = cell_centre(cell)
        for i in index.candidates(x, y):
            if point_in_rings(x, y, ringsets[i]):
                out[cell] = {codes[i]}
                break
        if progress and n % 50_000 == 0:
            progress(n, len(todo))
    return out


def build_mesh_municipality(
    boundaries: Sequence[tuple[str, tuple[float, float, float, float], list]],
    lg_by_jis: dict[str, str],
    progress: Callable[[int, int], None] | None = None,
) -> pl.DataFrame:
    """One row per (mesh cell, candidate municipality).

    Deliberately the same shape as ``bridge_station_municipality``: the two
    answer the same question about different inputs, and a reader who has
    learned one should not have to learn the other.
    """
    with stage_context("mesh", "classify"):
        cells = classify_cells(boundaries, progress=progress)

        rows: list[dict] = []
        for cell, jis_codes in cells.items():
            ordered = sorted(jis_codes)
            n = len(ordered)
            relation = RELATION_CONTAINS if n == 1 else RELATION_AMBIGUOUS
            code = cell_code(cell)
            for jis in ordered:
                lg = lg_by_jis.get(jis)
                rows.append({
                    "mesh_code": code,
                    "lg_code": lg,
                    "boundary_jis_city_code": jis,
                    "relation_type": relation,
                    "match_method": MATCH_MESH,
                    # Containment held or it did not (docs/POLICY.md §6).
                    "confidence": 1.0,
                    "candidate_count": n,
                    "is_unique_match": 1 if n == 1 else 0,
                    "verification_status": "auto" if n == 1 and lg else "review_required",
                    "mismatch_note": _note(n, lg, jis),
                })

        table = pl.DataFrame(rows, schema=_SCHEMA).sort(
            ["mesh_code", "boundary_jis_city_code"]
        )
        uniform = sum(1 for v in cells.values() if len(v) == 1)
        log.info(
            "built mesh -> municipality table",
            land_cells=len(cells),
            uniform=uniform,
            mixed=len(cells) - uniform,
            rows=table.height,
            unresolved_lg_code=table.filter(pl.col("lg_code").is_null()).height,
        )
        return table


_SCHEMA = {
    "mesh_code": pl.Utf8,
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


def _note(n: int, lg: str | None, jis: str) -> str | None:
    if lg is None:
        return (
            f"境界データ側のコード {jis} が jpac の現行市区町村に無いため lg_code は NULL。"
            "断面のズレ。overrides/municipality_lineage.yml を参照"
        )
    if n > 1:
        return "セルが複数の市区町村にまたがる。候補を残す（POLICY.md §4）"
    return None
