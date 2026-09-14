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
import json
import struct
import sys
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jp_address_crosswalk.build.mesh import (  # noqa: E402
    build_mesh_municipality,
)
from jp_address_crosswalk.payload import (  # noqa: E402
    SHP_POLYGON,
    read_dbf_member,
    read_prj_member,
    read_shp_member,
)

HCODE_TOWN = "8101"
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

for n, path in enumerate(sorted(RAW.glob("*.zip")), 1):
    datums.add(read_prj_member(path).split(",", 1)[0].strip())
    cols, rows = read_dbf_member(path)
    ix = {c: cols.index(c) for c in ("PREF", "CITY", "HCODE", "KIGO_D")}
    shapes = read_shp_member(path, expect=SHP_POLYGON)
    if len(shapes) != len(rows):
        sys.exit(f"{path.name}: 図形と属性の件数が一致しない")
    for row, shape in zip(rows, shapes, strict=True):
        if shape is None:
            continue
        if row[ix["HCODE"]] == HCODE_WATER:
            dropped["water"] += 1
            continue
        if row[ix["HCODE"]] != HCODE_TOWN:
            continue
        if row[ix["KIGO_D"]] == "D1":
            dropped["hole"] += 1
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

# -------------------------------------------------------------------- build
def progress(done: int, total: int) -> None:
    print(f"    内部セル {done:,}/{total:,} ({time.time()-t0:.0f}s)", flush=True)


print("\nセルを分類中…", flush=True)
table = build_mesh_municipality(boundaries, lg_by_jis, progress=progress)

uniform = table.filter(pl.col("relation_type") == "contains")
mixed = table.filter(pl.col("relation_type") == "ambiguous")
cells_uniform = uniform.height
cells_mixed = mixed["mesh_code"].n_unique()
land = cells_uniform + cells_mixed

print(f"\n陸地セル {land:,}  均一 {cells_uniform:,} ({cells_uniform/land:.1%})  "
      f"混在 {cells_mixed:,} ({cells_mixed/land:.1%})")
print(f"lg_code が NULL の行: {table.filter(pl.col('lg_code').is_null()).height:,}")

# ------------------------------------------------------------------- write
OUT.mkdir(parents=True, exist_ok=True)
table.write_parquet(OUT / "mesh_municipality.parquet")

# R8: 均一セルは mesh_code(uint32) + 市区町村インデックス(uint16) の昇順配列。
# 市区町村は 1,918 件なので uint16 に収まる。lg_code を文字列のまま配ると
# 6倍になるうえ、先頭ゼロを落とす実装に出会う危険がある。
index: dict[str, int] = {}
order: list[str] = []
for lg in sorted(x for x in table["lg_code"].drop_nulls().unique().to_list()):
    index[lg] = len(order)
    order.append(lg)

u = uniform.filter(pl.col("lg_code").is_not_null()).sort("mesh_code")
buf = bytearray()
for code, lg in zip(u["mesh_code"].to_list(), u["lg_code"].to_list(), strict=True):
    buf += struct.pack("<IH", int(code), index[lg])
(OUT / "mesh_uniform.bin").write_bytes(bytes(buf))

# 混在セルは可変長。mesh_code(uint32) + 候補数(uint8) + 候補(uint16 * n)。
grouped = (mixed.filter(pl.col("lg_code").is_not_null())
                .group_by("mesh_code")
                .agg(pl.col("lg_code"))
                .sort("mesh_code"))
buf = bytearray()
for code, lgs in grouped.iter_rows():
    ids = sorted({index[x] for x in lgs})
    buf += struct.pack("<IB", int(code), len(ids))
    for i in ids:
        buf += struct.pack("<H", i)
(OUT / "mesh_mixed.bin").write_bytes(bytes(buf))

names = (mv.filter(pl.col("is_current") == 1)
           .select("lg_code", "jis_city_code", "pref", "county", "city")
           .unique(subset=["lg_code"]))
by_lg = {r["lg_code"]: r for r in names.iter_rows(named=True)}
(OUT / "municipality.json").write_text(json.dumps({
    "generated": "2026-09-14",
    "boundary_edition": "令和2年国勢調査 小地域（町丁・字等別）JGD2011",
    "attribution": "出典：「令和2年国勢調査 小地域（町丁・字等別）境界データ」"
                   "（総務省統計局 e-Stat）を加工して作成",
    "caveats": [
        "調査区の境界を基に作成しているため、実際の町丁・字の境界および名称と一致しない場合があります。",
        "一つの市区町村内に同一の基本単位区又は町丁・字番号を持つ境域が複数存在する場合があります。",
        "面積は境界データの図郭により算出したものであり、国土地理院等の公式な面積と一致しません。",
        "都道府県の境界線は接合処理を行っていないため、県境にずれが生じる場合があります。",
        "他県の飛び地の境域および水面調査区の情報が含まれます。",
    ],
    "municipalities": [
        {
            "lg_code": lg,
            "jis_city_code": (by_lg.get(lg) or {}).get("jis_city_code"),
            "name": "".join(filter(None, [
                (by_lg.get(lg) or {}).get("pref"),
                (by_lg.get(lg) or {}).get("county"),
                (by_lg.get(lg) or {}).get("city"),
            ])),
        }
        for lg in order
    ],
}, ensure_ascii=False, indent=1), encoding="utf-8")

for f in ("mesh_uniform.bin", "mesh_mixed.bin", "municipality.json",
          "mesh_municipality.parquet"):
    p = OUT / f
    print(f"  {f:28} {p.stat().st_size/1024/1024:7.2f} MB")
print(f"\n出力先 {OUT}  ({time.time()-t0:.0f}s)")
