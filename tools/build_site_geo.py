"""静的マップ用の境界データ（e-Stat 小地域 → 市区町村）を書き出す.

docs/POLICY.md §3.2 の範囲で配る「表示用の境界線」と「境界付近の点判定用の形状」。
jpac のリリース成果物（`jpac build`）とは別物で、出力は site/data/geo/ だけ。

出力（すべて <script> で読める .js。file:// でも動く）:

    units.js              境界単位（= 現行 lg_code の候補集合）の一覧・外接矩形・チャンク一覧
    coarse/all.js         全国の簡略化した輪郭（小縮尺の表示用）
    line/PPUU.js          1次メッシュごとの簡略化した輪郭（大縮尺の表示用）
    exact/PPUU.js         1次メッシュごとの、境界が通る 3次セルの形状（非簡略・判定用）

「境界単位」は e-Stat の市区町村コードを lineage で現行コードに読み替えた後のまとまり。
旧浜松市中区〜南区は中央区ひとつに融合し、旧北区（分割）は「中央区または浜名区」の
単位として残る。2020年の境界に2024年の区界は無いので、そこは点でも決まらない。

Run:  py -3.12 tools/build_site_geo.py [--out DIR]
Needs: pip install -e ".[geo]"   (shapely — docs/ARCHITECTURE.md §5)
"""

from __future__ import annotations

import sys as _sys

if hasattr(_sys.stdout, "reconfigure") and (_sys.stdout.encoding or "").lower() not in (
    "utf-8", "utf8"
):
    _sys.stdout.reconfigure(errors="replace")

import argparse
import json
import shutil
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import shapely
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jp_address_crosswalk.build.estat import is_land_polygon  # noqa: E402
from jp_address_crosswalk.build.geo_pack import js_chunk, pack_exact, pack_lines  # noqa: E402
from jp_address_crosswalk.build.mesh import successors_from_lineage  # noqa: E402
from jp_address_crosswalk.payload import (  # noqa: E402
    SHP_POLYGON,
    read_dbf_member,
    read_prj_member,
    read_shp_member,
)

ap = argparse.ArgumentParser()
ap.add_argument("--out", default=str(ROOT / "site" / "data" / "geo"))
ap.add_argument("--raw", default=str(ROOT / "data" / "raw" / "estat_boundary"))
ap.add_argument("--fine-tol", type=float, default=0.0002, help="大縮尺表示の簡略化（度）")
ap.add_argument("--coarse-tol", type=float, default=0.003, help="小縮尺表示の簡略化（度）")
ap.add_argument("--cache", default=str(ROOT / "data" / "cache" / "geo_units.wkb"),
                help="融合結果のキャッシュ（入力が同じなら融合を省く）")
args = ap.parse_args()
OUT = Path(args.out)
RAW = Path(args.raw)
t0 = time.time()


def say(*a: object) -> None:
    print(f"[{time.time() - t0:5.0f}s]", *a, flush=True)


# ------------------------------------------------------------ current codes
mv = pl.read_parquet(ROOT / "dist" / "parquet" / "municipality_version.parquet")
pairs = mv.filter(pl.col("is_current") == 1).select("jis_city_code", "lg_code").unique()
dupes = set(pairs.group_by("jis_city_code").len().filter(pl.col("len") > 1)["jis_city_code"])
lg_by_jis = {j: lg for j, lg in pairs.iter_rows() if j not in dupes}
lineage = yaml.safe_load((ROOT / "overrides" / "municipality_lineage.yml").read_text("utf-8"))
successors = successors_from_lineage(lineage or {}, lg_by_jis)


def unit_key(jis: str) -> tuple[str, ...]:
    """The candidate lg_codes a boundary code stands for today. Unknown: '?JIS'."""
    if jis in lg_by_jis:
        return (lg_by_jis[jis],)
    if jis in successors:
        return tuple(sorted(successors[jis]))
    return (f"?{jis}",)


# ------------------------------------------------------------------ polygons
say("境界データを読み込み中…")
geoms: list = []
keys: list[tuple[str, ...]] = []
jis_of: list[str] = []
datums: set[str] = set()
for path in sorted(RAW.glob("*.zip")):
    datums.add(read_prj_member(path).split(",", 1)[0].strip())
    cols, rows = read_dbf_member(path)
    ix = {c: cols.index(c) for c in ("PREF", "CITY", "HCODE", "KIGO_D")}
    shapes = read_shp_member(path, expect=SHP_POLYGON)
    if len(shapes) != len(rows):
        sys.exit(f"{path.name}: 図形と属性の件数が一致しない")
    for row, shape in zip(rows, shapes, strict=True):
        if shape is None or not is_land_polygon(row[ix["HCODE"]], row[ix["KIGO_D"]]):
            continue
        rings = [shapely.Polygon(r) for r in shape[1] if len(r) >= 4]
        if not rings:
            continue
        # Even-odd over the rings: holes need no orientation rules.
        g = rings[0] if len(rings) == 1 else shapely.symmetric_difference_all(rings)
        if not g.is_valid:
            g = shapely.make_valid(g)
        jis = row[ix["PREF"]] + row[ix["CITY"]]
        geoms.append(g)
        keys.append(unit_key(jis))
        jis_of.append(jis)
if len(datums) != 1:
    sys.exit(f"測地系が混在している: {sorted(datums)}")
geoms_a = np.asarray(geoms, dtype=object)
unit_keys = sorted(set(keys))
unit_index = {k: i for i, k in enumerate(unit_keys)}
unit_of = np.asarray([unit_index[k] for k in keys])
pref_of_unit = {}
jis_of_unit: dict[int, set[str]] = defaultdict(set)
for k, j in zip(keys, jis_of, strict=True):
    pref_of_unit[unit_index[k]] = j[:2]
    jis_of_unit[unit_index[k]].add(j)
say(f"ポリゴン {len(geoms):,}  境界単位 {len(unit_keys):,}  測地系 {datums.pop()}")


def dissolve(parts):
    try:
        g = shapely.coverage_union_all(parts)
    except Exception:
        g = shapely.union_all(parts)
    return g if g.is_valid else shapely.make_valid(g)


def polygons_of(g) -> list:
    """Polygonal parts only (make_valid / intersection can add lines and points)."""
    if g is None or g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    if g.geom_type == "MultiPolygon":
        return list(g.geoms)
    if g.geom_type == "GeometryCollection":
        return [p for part in g.geoms for p in polygons_of(part)]
    return []


def rings_of(g) -> list[list[tuple[float, float]]]:
    out = []
    for p in polygons_of(g):
        out.append(list(p.exterior.coords))
        out.extend(list(r.coords) for r in p.interiors)
    return out


# ---------------------------------------------------------- dissolve units
# The union is the slow step (~3-4 min). It depends only on the polygons and
# the unit keys, so it is cached under data/cache/ keyed by both.
import hashlib  # noqa: E402

cache = Path(args.cache)
digest = hashlib.sha256()
for p in sorted(RAW.glob("*.zip")):
    digest.update(f"{p.name}:{p.stat().st_size}:{p.stat().st_mtime_ns}".encode())
digest.update(json.dumps(unit_keys).encode())
digest = digest.hexdigest()
units = None
if cache.exists() and cache.with_suffix(".sha256").exists() \
        and cache.with_suffix(".sha256").read_text().strip() == digest:
    units = np.asarray(shapely.from_wkb(np.load(cache, allow_pickle=True)), dtype=object)
    if len(units) != len(unit_keys):
        units = None
    else:
        say("融合結果をキャッシュから読み込み")
if units is None:
    say("市区町村単位に融合中…")
    order = np.argsort(unit_of, kind="stable")
    cuts = np.flatnonzero(np.r_[True, unit_of[order][1:] != unit_of[order][:-1], True])
    units = np.empty(len(unit_keys), dtype=object)
    for a, b in zip(cuts[:-1], cuts[1:], strict=True):
        units[unit_of[order[a]]] = dissolve(geoms_a[order[a:b]])
    cache.parent.mkdir(parents=True, exist_ok=True)
    with open(cache, "wb") as fh:
        np.save(fh, np.asarray(shapely.to_wkb(units), dtype=object), allow_pickle=True)
    cache.with_suffix(".sha256").write_text(digest)
say(f"融合完了  頂点 {shapely.get_num_coordinates(units).sum():,}")

# ----------------------------------------------------------- exact cells
# A cell needs point-level data unless one unit covers all of it. Deciding
# that needs the area each unit holds inside the cell, which is computed from
# the small polygons (cheap to clip) rather than the dissolved units (large).
say("境界が通る 3次セルを抽出中…")
b = shapely.bounds(geoms_a)
cellset = set()
for x0, y0, x1, y1 in b:
    for r in range(int(np.floor(y0 * 120)), int(np.floor(y1 * 120)) + 1):
        for c in range(int(np.floor(x0 * 80)), int(np.floor(x1 * 80)) + 1):
            cellset.add((r, c))
cells = np.asarray(sorted(cellset))
boxes = shapely.box(cells[:, 1] / 80, cells[:, 0] / 120, (cells[:, 1] + 1) / 80, (cells[:, 0] + 1) / 120)
cell_area = (1 / 80) * (1 / 120)
tree = shapely.STRtree(geoms_a)
ci, pi = tree.query(boxes, predicate="intersects")
inside = shapely.contains_properly(geoms_a[pi], boxes[ci])
area = np.where(inside, cell_area, 0.0)
rest = np.flatnonzero(~inside)
clipped = np.empty(len(ci), dtype=object)
for s in range(0, len(rest), 200_000):
    k = rest[s:s + 200_000]
    clipped[k] = shapely.intersection(geoms_a[pi[k]], boxes[ci[k]])
    area[k] = shapely.area(clipped[k])
say(f"  セル×ポリゴン {len(ci):,} 組")

per_cell: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
for c, p, a in zip(ci, pi, area, strict=True):
    if a > 0:
        per_cell[c][unit_of[p]] += a
exact_cells = [
    c for c, by_unit in per_cell.items()
    if len(by_unit) > 1 or next(iter(by_unit.values())) < cell_area * (1 - 1e-9)
]
exact_set = set(exact_cells)
say(f"  陸地セル {len(per_cell):,}  うち境界・海岸線が通るセル {len(exact_cells):,}")

# Pieces of each unit inside each exact cell: its small polygons clipped to the
# cell grown by MARGIN on every side, then unioned.
#
# Why grown: cell edges lie on 1/120° and 1/80° lines, which the 1e-6° grid of
# geo_pack cannot represent exactly, so a piece clipped to the exact cell has
# its edge rounded by up to 5e-7°. A point that close to the cell edge then
# fell in no piece (measured once in 35,000 random points: 福井市, reported as
# 判定なし). Growing the clip past the edge makes every point of the cell land in
# its own cell's pieces. It cannot add a wrong candidate: only the clicked
# cell's pieces are ever tested, and the margin holds only the same land.
MARGIN = 3e-6
eboxes = shapely.box(cells[:, 1] / 80 - MARGIN, cells[:, 0] / 120 - MARGIN,
                     (cells[:, 1] + 1) / 80 + MARGIN, (cells[:, 0] + 1) / 120 + MARGIN)
sel = np.fromiter((c in exact_set and a > 0 for c, a in zip(ci, area, strict=True)), bool, len(ci))
piece_parts = shapely.intersection(geoms_a[pi[sel]], eboxes[ci[sel]])
groups: dict[tuple[int, int], list] = defaultdict(list)
for c, p, g in zip(ci[sel], pi[sel], piece_parts, strict=True):
    groups[(c, unit_of[p])].append(g)
by_chunk: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))


def code_of(row: int, col: int) -> int:
    p, q, r = row // 80, (row % 80) // 10, row % 10
    u, v, w = col // 80 - 100, (col % 80) // 10, col % 10
    return p * 1_000_000 + u * 10_000 + q * 1000 + v * 100 + r * 10 + w


for (c, u), parts in groups.items():
    g = parts[0] if len(parts) == 1 else dissolve(np.asarray(parts, dtype=object))
    rings = rings_of(g)
    if not rings:
        continue
    row, col = int(cells[c][0]), int(cells[c][1])
    code = code_of(row, col)
    # 2次メッシュ chunks: one click fetches ~1/64 of what a 1次 chunk would be.
    by_chunk[f"{code // 100:06d}"][code].append((int(u), rings))
say(f"  判定用の断片 {sum(len(v) for ch in by_chunk.values() for v in ch.values()):,}")

# ------------------------------------------------------- display outlines
def simplified(tol: float):
    out = np.empty(len(units), dtype=object)
    by_pref: dict[str, list[int]] = defaultdict(list)
    for u, p in pref_of_unit.items():
        by_pref[p].append(u)
    # Per prefecture: e-Stat does not join prefecture seams, so a nationwide
    # coverage would not be valid. Within one, shared edges stay shared.
    for idx in by_pref.values():
        sel = units[idx]
        try:
            res = shapely.coverage_simplify(sel, tol)
        except Exception:
            res = shapely.simplify(sel, tol, preserve_topology=True)
        for u, g in zip(idx, res, strict=True):
            out[u] = g
    return out


def coarse_outline(tol: float):
    """Small-scale outlines: plain simplification, and no speck survives.

    Coverage-preserving simplification barely shrinks this data (measured:
    12.8M -> 3.2M vertices even at 0.003°) because every island and hole
    keeps its own ring. At the zooms this is drawn (national to prefectural)
    a ring smaller than a few tolerance-squares is under a pixel, so it is
    dropped — except each unit's largest part, so no municipality vanishes.
    Neighbouring outlines may not coincide exactly here; that is invisible at
    these scales and this layer never decides anything.
    """
    out = np.empty(len(units), dtype=object)
    min_area = (tol * 3) ** 2
    for u, g in enumerate(units):
        s = shapely.simplify(g, tol, preserve_topology=False)
        parts = sorted(polygons_of(s), key=lambda p: p.area, reverse=True)
        keep = []
        for n, p in enumerate(parts):
            if n and p.area < min_area:
                continue
            holes = [h for h in p.interiors if shapely.Polygon(h).area >= min_area]
            keep.append(shapely.Polygon(p.exterior, holes))
        if not keep:   # simplified away entirely: fall back to the hull of the original
            keep = [shapely.convex_hull(g)]
        out[u] = shapely.MultiPolygon(keep) if len(keep) > 1 else keep[0]
    return out


say("表示用の輪郭を簡略化中…")
coarse = coarse_outline(args.coarse_tol)
fine = simplified(args.fine_tol)

# ------------------------------------------------------------------ write
if OUT.exists():
    shutil.rmtree(OUT)
for d in ("exact", "line", "coarse"):
    (OUT / d).mkdir(parents=True, exist_ok=True)

sizes: dict[str, int] = defaultdict(int)
for key, cellmap in sorted(by_chunk.items()):
    s = js_chunk("exact", key, pack_exact(key, sorted(cellmap.items())))
    (OUT / "exact" / f"{key}.js").write_text(s, encoding="ascii")
    sizes["exact"] += len(s)

coarse_lines = [(u, ring) for u in range(len(units)) for ring in rings_of(coarse[u])]
s = js_chunk("coarse", "all", pack_lines("all", coarse_lines))
(OUT / "coarse" / "all.js").write_text(s, encoding="ascii")
sizes["coarse"] = len(s)

# Fine outlines are cut into 1次メッシュ chunks as lines, not polygons: cutting
# a line adds no edge, so no artificial boundary appears along a chunk border.
def display_rings(g, min_area: float) -> list[list[tuple[float, float]]]:
    """Rings worth drawing. Removing 水面調査区 and 抜け地 leaves pinholes in
    the land (measured: they show as specks of dashes inside a highlighted
    municipality); anything under ``min_area`` is dropped, except each unit's
    largest part so no municipality disappears."""
    parts = sorted(polygons_of(g), key=lambda p: p.area, reverse=True)
    out = []
    for n, p in enumerate(parts):
        if n and p.area < min_area:
            continue
        out.append(list(p.exterior.coords))
        out.extend(list(r.coords) for r in p.interiors if shapely.Polygon(r).area >= min_area)
    return out


FINE_MIN_AREA = (args.fine_tol * 5) ** 2      # ~1 ha at the default tolerance
fine_chunks: dict[str, list] = defaultdict(list)
for u in range(len(units)):
    for ring in display_rings(fine[u], FINE_MIN_AREA):
        line = shapely.LineString(ring)
        x0, y0, x1, y1 = line.bounds
        for pp in range(int(np.floor(y0 * 1.5)), int(np.floor(y1 * 1.5)) + 1):
            for uu in range(int(np.floor(x0)) - 100, int(np.floor(x1)) - 100 + 1):
                piece = shapely.clip_by_rect(line, uu + 100, pp / 1.5, uu + 101, (pp + 1) / 1.5)
                for part in (piece.geoms if hasattr(piece, "geoms") else [piece]):
                    if not part.is_empty and part.geom_type == "LineString":
                        fine_chunks[f"{pp:02d}{uu:02d}"].append((u, list(part.coords)))
for key, lines in sorted(fine_chunks.items()):
    s = js_chunk("line", key, pack_lines(key, lines))
    (OUT / "line" / f"{key}.js").write_text(s, encoding="ascii")
    sizes["line"] += len(s)

meta = {
    "generated": date.today().isoformat(),
    "source": "令和2年国勢調査 小地域（町丁・字等別）境界データ（e-Stat）を市区町村単位に融合",
    "scale": 10_000_000,
    "tolerance_deg": {"coarse": args.coarse_tol, "line": args.fine_tol, "exact": 0},
    "units": [
        {
            "lg": [k for k in unit_keys[u] if not k.startswith("?")],
            "jis": sorted(jis_of_unit[u]),
            "bbox": [round(v, 5) for v in shapely.bounds(units[u]).tolist()],
        }
        for u in range(len(units))
    ],
    "chunks": {
        "exact": sorted(by_chunk),
        "line": sorted(fine_chunks),
        "coarse": ["all"],
    },
}
(OUT / "units.js").write_text(
    "// generated by tools/build_site_geo.py — do not edit\n"
    f"MeshGeo.units({json.dumps(meta, ensure_ascii=False, separators=(',', ':'))});\n",
    encoding="utf-8")
sizes["units"] = (OUT / "units.js").stat().st_size

say("書き出し完了")
for k, v in sizes.items():
    n = len(list((OUT / k).glob("*.js"))) if (OUT / k).is_dir() else 1
    print(f"  {k:7} {v / 1e6:7.2f} MB  {n} ファイル")
print(f"出力先 {OUT}")
