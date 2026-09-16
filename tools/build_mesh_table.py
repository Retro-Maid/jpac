"""メッシュ → 市区町村 の対応表と、その配布用バイナリを作る.

docs/MESH_MUNICIPALITY_LOOKUP.md の R4/R6/R8 の実装。境界ポリゴンを読むのはここ
だけで、出るのは表とバイナリ。**ジオメトリは一切書き出さない**（POLICY.md §3.1）。

これはリリースパイプラインの一部ではない。jpac の成果物は対応表であって地図では
なく、本件は別プロダクトとして切り出す前提（MESH_MUNICIPALITY_LOOKUP.md P3）。
`jpac build` の出力は変わらない。

Run:  py -3.12 tools/build_mesh_table.py [--out DIR]
"""

from __future__ import annotations

import sys as _sys

if hasattr(_sys.stdout, "reconfigure") and (_sys.stdout.encoding or "").lower() not in (
    "utf-8", "utf8"
):
    _sys.stdout.reconfigure(errors="replace")
    if hasattr(_sys.stderr, "reconfigure"):
        _sys.stderr.reconfigure(errors="replace")

import argparse
import base64
import json
import sys
import time
from datetime import date
from pathlib import Path

import polars as pl
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jp_address_crosswalk.build.estat import KIGO_D_HOLE, is_land_polygon  # noqa: E402
from jp_address_crosswalk.build.mesh import (  # noqa: E402
    UNKNOWN_INDEX,
    build_mesh_municipality,
    pack_lookup,
    successors_from_lineage,
)
from jp_address_crosswalk.payload import (  # noqa: E402
    SHP_POLYGON,
    read_dbf_member,
    read_prj_member,
    read_shp_member,
)

HCODE_WATER = "8154"

ap = argparse.ArgumentParser()
ap.add_argument("--out", default=str(ROOT / "dist" / "mesh"))
ap.add_argument("--raw", default=str(ROOT / "data" / "raw" / "estat_boundary"))
args = ap.parse_args()
OUT = Path(args.out)
RAW = Path(args.raw)

if not RAW.is_dir():
    sys.exit(f"境界データが見つからない: {RAW}")

t0 = time.time()

# ---------------------------------------------------------------- boundaries
# R3: 水面調査区と抜け地を落とし、飛び地・島・重複境域は残す。落とす2つは
# 「陸地ではない」と「他自治体の領域が食い込んだ穴」で、どちらも中の点を
# 外側の市区町村と判定してはならないもの。
boundaries: list[tuple[str, tuple[float, float, float, float], list]] = []
datums: set[str] = set()
dropped = {"water": 0, "hole": 0}
estat_names: dict[str, str] = {}   # JIS 5桁 → e-Stat の CITY_NAME（lineage の目視確認用）

for n, path in enumerate(sorted(RAW.glob("*.zip")), 1):
    datums.add(read_prj_member(path).split(",", 1)[0].strip())
    cols, rows = read_dbf_member(path)
    ix = {c: cols.index(c) for c in ("PREF", "CITY", "HCODE", "KIGO_D", "CITY_NAME")}
    for row in rows:
        estat_names.setdefault(row[ix["PREF"]] + row[ix["CITY"]], row[ix["CITY_NAME"]])
    shapes = read_shp_member(path, expect=SHP_POLYGON)
    if len(shapes) != len(rows):
        sys.exit(f"{path.name}: 図形と属性の件数が一致しない")
    for row, shape in zip(rows, shapes, strict=True):
        if shape is None:
            continue
        if row[ix["HCODE"]] == HCODE_WATER:
            dropped["water"] += 1
            continue
        if not is_land_polygon(row[ix["HCODE"]], row[ix["KIGO_D"]]):
            dropped["hole"] += row[ix["KIGO_D"]] == KIGO_D_HOLE
            continue
        boundaries.append((row[ix["PREF"]] + row[ix["CITY"]], shape[0], shape[1]))
    print(f"  [{n:2}/47] {path.name}  累計 {len(boundaries):,}", flush=True)

if len(datums) != 1:
    sys.exit(f"測地系が混在している: {sorted(datums)}")
print(f"\nポリゴン {len(boundaries):,}  測地系 {datums.pop()}  "
      f"除外 水面{dropped['water']} 抜け地{dropped['hole']}  ({time.time()-t0:.0f}s)")

# ------------------------------------------------------------- municipality
# R7: 5桁 JIS → 6桁 lg_code。現行に無いコードは変換しない（NULL のまま残す）。
mv = pl.read_parquet(ROOT / "dist" / "parquet" / "municipality_version.parquet")
pairs = mv.filter(pl.col("is_current") == 1).select("jis_city_code", "lg_code").unique()
dupes = pairs.group_by("jis_city_code").len().filter(pl.col("len") > 1)["jis_city_code"]
if dupes.len():
    print(f"⚠️  1つの JIS コードが複数の lg_code を持つ: {sorted(dupes.to_list())}")
lg_by_jis = dict(pairs.filter(~pl.col("jis_city_code").is_in(dupes.to_list())).iter_rows())
print(f"現行市区町村 {len(lg_by_jis):,}")


def _find(node, key):
    """First value under `key` anywhere in the source spec (depth-first)."""
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for v in node.values():
            found = _find(v, key)
            if found is not None:
                return found
    return None


spec = yaml.safe_load((ROOT / "config" / "sources.yml").read_text(encoding="utf-8"))
spec = spec["sources"]["estat_boundary"]
attribution = _find(spec, "attribution") or {}
caveats = _find(spec, "publisher_caveats") or {}
if not attribution.get("processed") or not caveats.get("items"):
    sys.exit("config/sources.yml の estat_boundary に attribution.processed / publisher_caveats.items が無い")
ATTRIBUTION = " ".join(s.strip() for s in attribution["processed"].splitlines() if s.strip())
CAVEATS = [str(c) for c in caveats["items"]]

# R7: 現行に無いコードは lineage の承継先に読み替える。1:1 の transitions は
# 解決し、分割（unlisted の successors）は全承継先を候補に残す。
lineage = yaml.safe_load((ROOT / "overrides" / "municipality_lineage.yml")
                         .read_text(encoding="utf-8")) or {}
current_lg = set(lg_by_jis.values())
successors = successors_from_lineage(lineage, lg_by_jis)
stale = sorted({lg for v in successors.values() for lg in v} - current_lg)
if stale:
    sys.exit(f"lineage の承継先が現行市区町村に無い: {stale}")
# 旧名と承継先の名前を並べて出す。コードだけだと「北区→天竜区」のような取り違えが
# 目に入らない（実際に lineage.yml のラベルが入れ替わっていた。2026-09-15 修正）。
_cur = {r["lg_code"]: "".join(filter(None, [r["city"], r["ward"]]))
        for r in mv.filter(pl.col("is_current") == 1).iter_rows(named=True)}
print(f"lineage による読み替え {len(successors)} コード:")
for k, v in sorted(successors.items()):
    print(f"  {k} {estat_names.get(k, '?')} → " + " / ".join(f"{lg} {_cur.get(lg, '?')}" for lg in v))

# -------------------------------------------------------------------- build
def progress(done: int, total: int) -> None:
    print(f"    セル {done:,}/{total:,} ({time.time()-t0:.0f}s)", flush=True)


print("\nセルを分類中…", flush=True)
table = build_mesh_municipality(boundaries, lg_by_jis, progress=progress,
                                successors=successors)

uniform = table.filter(pl.col("relation_type") == "contains")
mixed = table.filter(pl.col("relation_type") == "ambiguous")
cells_uniform = uniform.height
cells_mixed = mixed["mesh_code"].n_unique()
land = cells_uniform + cells_mixed

print(f"\n陸地セル {land:,}  均一 {cells_uniform:,} ({cells_uniform/land:.1%})  "
      f"混在 {cells_mixed:,} ({cells_mixed/land:.1%})")
print(f"lg_code が NULL の行: {table.filter(pl.col('lg_code').is_null()).height:,}  "
      f"内訳 {dict(table.filter(pl.col('lg_code').is_null())['boundary_jis_city_code'].value_counts().iter_rows())}")

# ------------------------------------------------------------------- write
OUT.mkdir(parents=True, exist_ok=True)
table.write_parquet(OUT / "mesh_municipality.parquet")

# R8: 均一セルは mesh_code(uint32) + 市区町村インデックス(uint16) の昇順配列。
# 市区町村は 1,918 件なので uint16 に収まる。lg_code を文字列のまま配ると
# 6倍になるうえ、先頭ゼロを落とす実装に出会う危険がある。
ub, mb, order = pack_lookup(table)
(OUT / "mesh_uniform.bin").write_bytes(ub)
(OUT / "mesh_mixed.bin").write_bytes(mb)
print(f"mesh_uniform.bin {len(ub):,} B  mesh_mixed.bin {len(mb):,} B  "
      f"lg_code が NULL の候補を含むセル {table.filter(pl.col('lg_code').is_null())['mesh_code'].n_unique():,}")

# 政令指定都市の区は `ward` 列にある。落とすと 浜松市中央区 と 浜名区 が
# どちらも「静岡県浜松市」になり、候補2件が同じ名前で並ぶ。
names = (mv.filter(pl.col("is_current") == 1)
           .select("lg_code", "jis_city_code", "pref", "county", "city", "ward")
           .unique(subset=["lg_code"]))
by_lg = {r["lg_code"]: r for r in names.iter_rows(named=True)}
meta = {
    "generated": date.today().isoformat(),
    "boundary_edition": "令和2年国勢調査 小地域（町丁・字等別）JGD2011",
    # mesh_mixed.bin の候補にこの値が現れたら「市区町村を特定できない候補」。
    "unknown_index": UNKNOWN_INDEX,
    # 出典の文言と注意事項は人がレビューした config/sources.yml から取る。
    # ここに書き写すと、レビュー済みの文言と黙って食い違いうる（R9）。
    "attribution": ATTRIBUTION,
    "caveats": CAVEATS,
    "municipalities": [
        {
            "lg_code": lg,
            "jis_city_code": (by_lg.get(lg) or {}).get("jis_city_code"),
            "name": "".join(filter(None, [
                (by_lg.get(lg) or {}).get("pref"),
                (by_lg.get(lg) or {}).get("county"),
                (by_lg.get(lg) or {}).get("city"),
                (by_lg.get(lg) or {}).get("ward"),
            ])),
        }
        for lg in order
    ],
}
(OUT / "municipality.json").write_text(
    json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

# 静的マップ（site/）用。中身は上の2つの .bin と municipality.json を1本にしたもの。
# fetch ではなく <script> で読むので、index.html をダブルクリックで開いた file:// でも
# 動く（ブラウザは file:// からの fetch を CORS で塞ぐ）。base64 で約 1.33 倍になるが、
# GitHub Pages は .js を gzip で配るので転送量はむしろ減る。
(OUT / "mesh_data.js").write_text(
    "// generated by tools/build_mesh_table.py — do not edit\n"
    "window.MESH_DATA = " + json.dumps({
        "uniform": base64.b64encode(ub).decode("ascii"),
        "mixed": base64.b64encode(mb).decode("ascii"),
        "meta": meta,
    }, ensure_ascii=False, separators=(",", ":")) + ";\n",
    encoding="utf-8")

for f in ("mesh_uniform.bin", "mesh_mixed.bin", "municipality.json", "mesh_data.js",
          "mesh_municipality.parquet"):
    p = OUT / f
    print(f"  {f:28} {p.stat().st_size/1024/1024:7.2f} MB")
print(f"\n出力先 {OUT}  ({time.time()-t0:.0f}s)")
