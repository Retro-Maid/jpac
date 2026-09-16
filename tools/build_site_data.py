"""静的マップ用の jpac データ（駅・郵便番号・市外局番）を書き出す.

docs/POLICY.md §3.2 の範囲。地図でクリックした市区町村について、jpac が既に持っている
対応をその場で出せるようにするためのもので、出るのは `site/data/jpac_data.js` ひとつ。
`jpac build` の成果物は変わらない。

鍵は全部 `lg_code`（6桁の全国地方公共団体コード）で、jpac の他のテーブルと同じ。
町字（`address_id`）には降りない —— 郵便番号も市外局番も、公表されている粒度は
市区町村までだからである（docs/POLICY.md §4）。

出力の中身:

    stations      駅グループごとに 名前・事業者・代表点・lg_code。
                  代表点は N02 のポリラインから build/spatial.py の規則で求める
                  （長さ加重の重心。線から外れる場合は線上の中点）。§3.2 の例外として
                  site/ にのみ配る
    postal        lg_code → 郵便番号。bridge_municipality_postal の P2（7桁そのもの）と
                  P3（「以下に掲載がない場合」等のレコード）を、どちらも実際の7桁に
                  解決したうえで種別を付ける
    telephone     lg_code → 市外局番。番号区画を経由し、区画の一部だけを含む場合は
                  発行元の但し書きをそのまま持たせる

Run:  py -3.12 tools/build_site_data.py [--out DIR]
"""

from __future__ import annotations

import sys as _sys

if hasattr(_sys.stdout, "reconfigure") and (_sys.stdout.encoding or "").lower() not in (
    "utf-8", "utf8"
):
    _sys.stdout.reconfigure(errors="replace")

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jp_address_crosswalk.build.spatial import line_centroid, point_on_line  # noqa: E402
from jp_address_crosswalk.payload import (  # noqa: E402
    SHP_POLYLINE,
    read_dbf_member,
    read_prj_member,
    read_shp_member,
)

ap = argparse.ArgumentParser()
ap.add_argument("--out", default=str(ROOT / "site" / "data"))
ap.add_argument("--parquet", default=str(ROOT / "dist" / "parquet"))
ap.add_argument("--raw", default=str(ROOT / "data" / "raw" / "mlit_ksj_n02"))
args = ap.parse_args()
OUT = Path(args.out)
PQ = Path(args.parquet)
RAW = Path(args.raw)
t0 = time.time()


def say(*a: object) -> None:
    print(f"[{time.time() - t0:5.0f}s]", *a, flush=True)


rd = lambda name: pl.read_parquet(PQ / f"{name}.parquet")  # noqa: E731

# ------------------------------------------------------------------ 駅の代表点
# N02 は駅を「プラットフォームごとのポリライン」で持つので、駅グループ単位にまとめて
# から1点にする。規則は駅→市区町村の結合と同じものを使う（別の点を使うと、地図の
# ピンと bridge_station_municipality の答えが食い違いうる）。
say("N02 の駅ジオメトリを読み込み中…")
member = ("UTF-8/", "_Station")
geometry: dict[str, list] = {}
datums: set[str] = set()
for path in sorted(RAW.glob("*.zip")):
    datums.add(read_prj_member(path, contains=member).split(",", 1)[0].strip())
    names, rows = read_dbf_member(path, encoding="utf-8", contains=member)
    i_group = names.index("N02_005g")
    shapes = read_shp_member(path, contains=member, expect=SHP_POLYLINE)
    if len(shapes) != len(rows):
        sys.exit(f"{path.name}: 図形と属性の件数が一致しない")
    for row, shape in zip(rows, shapes, strict=True):
        if shape is not None:
            geometry.setdefault(row[i_group], []).extend(shape[1])
if len(datums) != 1:
    sys.exit(f"測地系が混在している: {sorted(datums)}")
say(f"駅グループ {len(geometry):,}  測地系 {datums.pop()}")

station = rd("n02_station")
bridge = rd("bridge_station_municipality")
lg_by_station: dict[str, list[str]] = {}
for gid, lg in bridge.select("n02_group_code", "lg_code").iter_rows():
    if lg:
        lg_by_station.setdefault(gid, []).append(lg)

stations = []
missing_point = 0
for r in station.select("n02_group_code", "station_name_raw", "operator_name_raw").iter_rows(named=True):
    gid = r["n02_group_code"]
    parts = geometry.get(gid, [])
    pt = line_centroid(parts) or point_on_line(parts)
    if pt is None:
        missing_point += 1
        continue
    stations.append({
        "id": gid,
        "n": r["station_name_raw"],
        "o": r["operator_name_raw"],
        "x": round(pt[0], 6),
        "y": round(pt[1], 6),
        "lg": sorted(lg_by_station.get(gid, [])),
    })
stations.sort(key=lambda s: s["id"])
say(f"駅 {len(stations):,}（代表点が取れなかったもの {missing_point}）"
    f"  市区町村に結び付いた駅 {sum(1 for s in stations if s['lg']):,}")

# --------------------------------------------------------------- 郵便番号
# P2 は target_id がそのまま7桁。P3 は日本郵便のレコード（「以下に掲載がない場合」など）
# を指す ID なので、postal_record_version を引いて実際の7桁に直し、種別を残す。
say("郵便番号を解決中…")
bp = rd("bridge_municipality_postal")
prv = rd("postal_record_version").filter(pl.col("is_current") == 1)
resolved = (
    bp.join(
        prv.select("postal_record_id", "postal_code", "parenthetical_class", "town_raw"),
        left_on="target_id", right_on="postal_record_id", how="left",
    )
    .with_columns(
        pl.coalesce(pl.col("postal_code"), pl.col("target_id")).alias("code"),
    )
)
bad = resolved.filter(~pl.col("code").str.contains(r"^\d{7}$"))
if bad.height:
    sys.exit(f"7桁に解決できない郵便番号が {bad.height} 件: {bad['target_id'].head(3).to_list()}")
postal: dict[str, list] = {}
for lg, code, klass in resolved.select(
    "lg_code", "code", "parenthetical_class"
).iter_rows():
    entry = [code] if not klass else [code, klass]
    postal.setdefault(lg, []).append(entry)
for lg in postal:
    postal[lg] = sorted(postal[lg], key=lambda e: e[0])
say(f"郵便番号 {resolved.height:,} 行 / {len(postal):,} 市区町村"
    f"（うち種別つき {resolved.filter(pl.col('parenthetical_class').is_not_null()).height:,}）")

# --------------------------------------------------------------- 市外局番
# 番号区画を経由する。区画の一部の町だけを含む場合、発行元の但し書きをそのまま持たせる
# —— 「この市区町村はこの局番」と言い切れないことが、その文そのものだからである。
say("市外局番を解決中…")
bt = rd("bridge_municipality_telephone")
tav = rd("telephone_area_version").filter(pl.col("is_current") == 1)
tel = bt.join(
    tav.select("numbering_area_code", "area_code"),
    left_on="target_id", right_on="numbering_area_code", how="left",
)
if tel.filter(pl.col("area_code").is_null()).height:
    sys.exit("番号区画に対応する市外局番が見つからない行がある")
telephone: dict[str, list] = {}
for lg, area, code, coverage, note in tel.select(
    "lg_code", "area_code", "target_id", "coverage_type", "mismatch_note"
).iter_rows():
    entry = {"a": area, "z": code, "c": coverage}
    if note:
        entry["t"] = note
    telephone.setdefault(lg, []).append(entry)
for lg in telephone:
    telephone[lg] = sorted(telephone[lg], key=lambda e: e["a"])
say(f"市外局番 {tel.height:,} 行 / {len(telephone):,} 市区町村"
    f"（区画の一部のみ {tel.filter(pl.col('coverage_type') == 'partial').height:,}）")

# ------------------------------------------------------------------- write
OUT.mkdir(parents=True, exist_ok=True)
payload = {
    "generated": date.today().isoformat(),
    "stations": stations,
    "postal": postal,
    "telephone": telephone,
    "counts": {
        "stations": len(stations),
        "postal_rows": resolved.height,
        "telephone_rows": tel.height,
    },
}
text = ("// generated by tools/build_site_data.py — do not edit\n"
        "window.JPAC_DATA = "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n")
(OUT / "jpac_data.js").write_text(text, encoding="utf-8")
size = (OUT / "jpac_data.js").stat().st_size
say(f"書き出し完了  jpac_data.js {size / 1e6:.2f} MB")
print(f"出力先 {OUT / 'jpac_data.js'}")
