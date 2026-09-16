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

    Two sources, unioned for every cell:

    * **Edges** — a polygon whose boundary passes through the cell reaches into it.
    * **The centre** — every polygon containing the cell's centre. A cell no edge
      enters lies wholly within one 小地域, so this alone identifies it.

    The centre test must also run on cells edges already claimed. e-Stat does not
    join prefecture seams, so neighbouring prefectures' polygons overlap: a
    sliver of one prefecture's edge can enter a cell that another prefecture's
    polygon covers entirely, with none of *that* polygon's edges inside. Edges
    alone then name only the sliver. Measured before this was fixed: 51 border
    cells shipped as ``contains``/``auto`` for a municipality holding under 1% of
    the cell, while the one holding ~100% was missing.

    Cells whose centre falls in no polygon and that no edge enters are absent
    rather than present-and-empty — they are sea, or a seam gap, and the caller
    must not fill them in.
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

    todo = sorted(candidates)
    for n, cell in enumerate(todo, 1):
        x, y = cell_centre(cell)
        for i in index.candidates(x, y):
            if point_in_rings(x, y, ringsets[i]):
                out.setdefault(cell, set()).add(codes[i])
        if progress and n % 50_000 == 0:
            progress(n, len(todo))
    return out


def _candidates(
    jis_codes: Iterable[str], lg_by_jis: dict[str, str], successors: dict[str, list[str]]
) -> list[tuple[str | None, list[str], bool]]:
    """``(lg_code, source JIS codes, via_lineage)`` per distinct candidate.

    A cell is decided in *current* municipalities, so two old codes carried to
    the same successor (浜松市中区・東区 → 中央区) are one candidate, not two. A
    code with no current match and no successor stays a NULL candidate of its
    own — never merged with another, never dropped.
    """
    by_lg: dict[str, tuple[list[str], bool]] = {}
    unknown: list[tuple[None, list[str], bool]] = []
    for jis in sorted(jis_codes):
        if jis in lg_by_jis:
            targets, via = [lg_by_jis[jis]], False
        elif jis in successors:
            targets, via = successors[jis], True
        else:
            unknown.append((None, [jis], False))
            continue
        for lg in targets:
            sources, was_via = by_lg.get(lg, ([], False))
            by_lg[lg] = (sources + [jis], was_via or via)
    return [(lg, s, v) for lg, (s, v) in sorted(by_lg.items())] + unknown


def build_mesh_municipality(
    boundaries: Sequence[tuple[str, tuple[float, float, float, float], list]],
    lg_by_jis: dict[str, str],
    progress: Callable[[int, int], None] | None = None,
    *,
    successors: dict[str, list[str]] | None = None,
) -> pl.DataFrame:
    """One row per (mesh cell, candidate municipality).

    Deliberately the same shape as ``bridge_station_municipality``: the two
    answer the same question about different inputs, and a reader who has
    learned one should not have to learn the other.

    ``successors`` maps a boundary JIS code the current municipalities lack to
    the current ``lg_code``\\s that took over its area, from
    ``overrides/municipality_lineage.yml`` (docs/MESH_MUNICIPALITY_LOOKUP.md R7).
    One successor is a 1:1 transition and resolves the cell; several (旧浜松市
    北区) make every one of them a candidate. A containment carried through an
    attested transition is still a containment — the old ward's area lies inside
    the new ward's — so a uniform cell resolved that way stays ``auto``.
    ``boundary_jis_city_code`` holds the first source code; the note lists all.
    """
    successors = successors or {}
    with stage_context("mesh", "classify"):
        cells = classify_cells(boundaries, progress=progress)

        rows: list[dict] = []
        uniform = 0
        for cell, jis_codes in cells.items():
            cands = _candidates(jis_codes, lg_by_jis, successors)
            n = len(cands)
            uniform += n == 1
            relation = RELATION_CONTAINS if n == 1 else RELATION_AMBIGUOUS
            code = cell_code(cell)
            for lg, sources, via in cands:
                rows.append({
                    "mesh_code": code,
                    "lg_code": lg,
                    "boundary_jis_city_code": sources[0],
                    "relation_type": relation,
                    "match_method": MATCH_MESH,
                    # Containment held or it did not (docs/POLICY.md §6).
                    "confidence": 1.0,
                    "candidate_count": n,
                    "is_unique_match": 1 if n == 1 else 0,
                    "verification_status": "auto" if n == 1 and lg else "review_required",
                    "mismatch_note": _note(n, lg, sources, via),
                })

        table = pl.DataFrame(rows, schema=_SCHEMA).sort(
            ["mesh_code", "lg_code", "boundary_jis_city_code"], nulls_last=True
        )
        log.info(
            "built mesh -> municipality table",
            land_cells=len(cells),
            uniform=uniform,
            mixed=len(cells) - uniform,
            rows=table.height,
            unresolved_lg_code=table.filter(pl.col("lg_code").is_null()).height,
            via_lineage=table.filter(
                pl.col("mismatch_note").str.contains("lineage", literal=True)
            ).height,
        )
        return table


def successors_from_lineage(lineage: dict, lg_by_jis: dict[str, str]) -> dict[str, list[str]]:
    """Boundary JIS code -> the current ``lg_code``\\s that took over its area.

    Read from ``overrides/municipality_lineage.yml`` (docs/MESH_MUNICIPALITY_LOOKUP.md
    R7): a 1:1 ``transitions`` entry resolves the code; an ``unlisted`` entry
    with ``successors`` (a split) makes every successor a candidate. A code the
    current municipalities still carry is never overridden.
    """
    out: dict[str, list[str]] = {}
    for e in lineage.get("transitions") or []:
        old, new = str(e["old_lg_code"]), str(e["new_lg_code"])
        if old[:5] not in lg_by_jis:
            out[old[:5]] = [new]
    for e in lineage.get("unlisted") or []:
        if e.get("successors") and str(e["lg_code"])[:5] not in lg_by_jis:
            out[str(e["lg_code"])[:5]] = [str(x) for x in e["successors"]]
    return out


UNKNOWN_INDEX = 0xFFFF


def _uvarint(n: int, out: bytearray) -> None:
    """Unsigned LEB128."""
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return


def _runs(items: list[tuple[int, tuple[int, ...]]]) -> list[tuple[int, int, tuple[int, ...]]]:
    """(start code, length, value) for maximal runs of consecutive codes with one value.

    Consecutive in the numeric sense: the last two digits of a 3次 code are its
    row and column inside a 2次 mesh, so 00..99 is one whole 2次 mesh, and a
    municipality usually holds long stretches of it.
    """
    runs: list[tuple[int, int, tuple[int, ...]]] = []
    for code, val in items:
        if runs and runs[-1][2] == val and runs[-1][0] + runs[-1][1] == code:
            s, n, v = runs[-1]
            runs[-1] = (s, n + 1, v)
        else:
            runs.append((code, 1, val))
    return runs


def pack_lookup(table: pl.DataFrame) -> tuple[bytes, bytes, list[str]]:
    """The two binaries the static map reads (docs/MESH_MUNICIPALITY_LOOKUP.md R8).

    Both are streams of unsigned LEB128 varints, holding **runs** of
    consecutive mesh codes that share one answer, in ascending order:

    * ``mesh_uniform.bin`` — cells with exactly one candidate that names a
      current municipality. Run count, then per run: gap from the previous
      run's end, run length, municipality index.
    * ``mesh_mixed.bin`` — every other land cell. Run count, then per run: gap,
      length, candidate count, candidate indices.

    Measured: the fixed-width first version (``<IH`` per cell) was 1.87 MB +
    0.63 MB; as runs the page's initial payload shrinks several-fold, which is
    most of what the page parses before it can answer anything.

    A candidate with no ``lg_code`` is written as ``UNKNOWN_INDEX``, never
    dropped. Dropped, it shrinks a mixed cell to one candidate — which reads as
    settled — or removes the cell altogether, which reads as sea.

    Returns both byte strings and the ``lg_code`` each index stands for. The
    reader is ``site/lookup.js``; tests/test_site_lookup.py runs one against
    the other.
    """
    order = sorted(table["lg_code"].drop_nulls().unique().to_list())
    if len(order) >= UNKNOWN_INDEX:
        raise ValueError(f"{len(order)} municipalities do not fit a uint16 index")
    index = {lg: i for i, lg in enumerate(order)}

    uniform = table.filter(
        (pl.col("candidate_count") == 1) & pl.col("lg_code").is_not_null()
    ).sort("mesh_code")
    u_items = [(int(c), (index[lg],)) for c, lg in uniform.select("mesh_code", "lg_code").iter_rows()]

    rest = (
        table.join(uniform.select("mesh_code"), on="mesh_code", how="anti")
        .group_by("mesh_code")
        .agg(pl.col("lg_code"))
        .sort("mesh_code")
    )
    m_items = [
        (int(c), tuple(sorted({UNKNOWN_INDEX if lg is None else index[lg] for lg in lgs})))
        for c, lgs in rest.iter_rows()
    ]

    def encode(items: list[tuple[int, tuple[int, ...]]], with_count: bool) -> bytes:
        runs = _runs(items)
        out = bytearray()
        _uvarint(len(runs), out)
        end = 0
        for start, n, val in runs:
            _uvarint(start - end, out)
            _uvarint(n, out)
            if with_count:
                _uvarint(len(val), out)
            for v in val:
                _uvarint(v, out)
            end = start + n
        return bytes(out)

    return encode(u_items, False), encode(m_items, True), order


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


def _note(n: int, lg: str | None, sources: list[str], via: bool) -> str | None:
    parts: list[str] = []
    if lg is None:
        parts.append(
            f"境界データ側のコード {sources[0]} が jpac の現行市区町村に無いため lg_code は NULL。"
            "断面のズレ。overrides/municipality_lineage.yml を参照"
        )
    elif via:
        parts.append(
            f"境界データ側のコード {', '.join(sources)} を廃置分合の承継先 {lg} に読み替えた"
            "（overrides/municipality_lineage.yml）"
        )
    if n > 1:
        parts.append("セルが複数の市区町村にまたがる。候補を残す（POLICY.md §4）")
    return "。".join(parts) or None
