"""静的マップ用の jpac データ（駅・路線・バス停・郵便番号・市外局番）を書き出す.

docs/POLICY.md §3.2 の範囲。地図でクリックした市区町村について、jpac が既に持っている
対応をその場で出せるようにするためのもの。`jpac build` の成果物は変わらない。

鍵は全部 `lg_code`（6桁の全国地方公共団体コード）で、jpac の他のテーブルと同じ。
町字（`address_id`）には降りない —— 郵便番号も市外局番も、公表されている粒度は
市区町村までだからである（docs/POLICY.md §4）。

出るファイルは2種類ある。

**`site/data/jpac_data.js`** —— ページが最初に読む1ファイル。

    stations      駅グループごとに 名前・事業者・代表点・lg_code。
                  代表点は N02 のポリラインから build/spatial.py の規則で求める
                  （長さ加重の重心。線から外れる場合は線上の中点）。§3.2 の例外として
                  site/ にのみ配る
    lineNames     路線 (路線名, 運営会社) の配列。596 種類しかないので、各行に
                  名前を持たせず添字で指す —— 素直に持つと 606 KB、集約すると 1/4 以下
    linesByLg     lg_code → lineNames の添字
    linesByStation 駅グループコード → lineNames の添字（乗換駅は複数）
    postal        lg_code → 郵便番号。bridge_municipality_postal_code（7桁そのもの）と
                  P3（「以下に掲載がない場合」等のレコード）を、どちらも実際の7桁に
                  解決したうえで種別を付ける
    telephone     lg_code → 市外局番。番号区画を経由し、区画の一部だけを含む場合は
                  発行元の但し書きをそのまま持たせる
    busCounts     lg_code → バス停の数。点そのものは下のチャンクにある
    busMeshes     存在するチャンクの鍵。無い鍵を要求して 404 を出さないため

**`site/data/bus/<2次メッシュ>.js`** —— バス停の点。ページが見えている範囲だけ読む。

    27万件は即時読み込みに載らない（全部で 16.4 MB）。geo.js と同じ 2次メッシュ単位の
    チャンクにすると中央値 2.1 KB・p90 11 KB に収まる。「多すぎるから間引く」はしない ——
    欠けた集合は「ここにあるバス停」という問いに、完全に見えるまま誤答する。

バス停の座標は parquet に無い（§3.1 によりリリース成果物には出さない）ので、生の P11 から
読む。その際 `pipeline._read_p11_geometry` をそのまま使う —— `p11_stop_id` の作り方が
ビルダー側とずれると、全停留所が別の点に付いたまま誰も気づかない。

Run:  py -3.12 tools/build_site_data.py [--out DIR] [--parquet DIR]
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

# 位置による突き合わせは1か所にしか置かない。バス停の点は p11_stop_id で引くので、
# id の作り方がビルダーとパイプラインでずれると全停留所が別の点に付く。
from jp_address_crosswalk.pipeline import Paths as _Paths  # noqa: E402
from jp_address_crosswalk.pipeline import _read_p11_geometry  # noqa: E402

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
# v2.0.0 で2つの表に分かれた（docs/BRIDGE_ENDPOINT_MIGRATION.md A2）。
# `bridge_municipality_postal_code` は7桁をそのまま持つ。`bridge_municipality_postal` は
# 日本郵便のレコード（「以下に掲載がない場合」など）を指すので、postal_record_version を
# 引いて実際の7桁に直し、種別を残す。
say("郵便番号を解決中…")
prv = rd("postal_record_version").filter(pl.col("is_current") == 1)
direct = rd("bridge_municipality_postal_code").select(
    pl.col("lg_code"),
    pl.col("postal_code").alias("code"),
    pl.lit(None, dtype=pl.Utf8).alias("parenthetical_class"),
)
special = (
    rd("bridge_municipality_postal")
    .join(
        prv.select("postal_record_id", "postal_code", "parenthetical_class"),
        on="postal_record_id", how="left",
    )
    .select(
        pl.col("lg_code"),
        pl.col("postal_code").alias("code"),
        pl.col("parenthetical_class"),
    )
)
resolved = pl.concat([direct, special], how="vertical")
bad = resolved.filter(
    pl.col("code").is_null() | ~pl.col("code").str.contains(r"^\d{7}$")
)
if bad.height:
    sys.exit(f"7桁に解決できない郵便番号が {bad.height} 件: {bad.head(3).to_dicts()}")
postal: dict[str, list] = {}
for lg, code, klass in resolved.select(
    "lg_code", "code", "parenthetical_class"
).iter_rows():
    entry = [code] if not klass else [code, klass]
    postal.setdefault(lg, []).append(entry)
for lg in postal:
    # 郵便番号だけで並べると、同じ番号の「種別つき」と「種別なし」の相対順が入力順に
    # 依存する（2表に分けたときに実際に入れ替わった）。総キーで並べる。
    postal[lg] = sorted(postal[lg], key=lambda e: (e[0], e[1] if len(e) > 1 else ""))
say(f"郵便番号 {resolved.height:,} 行 / {len(postal):,} 市区町村"
    f"（うち種別つき {resolved.filter(pl.col('parenthetical_class').is_not_null()).height:,}）")

# --------------------------------------------------------------- 市外局番
# 番号区画を経由する。区画の一部の町だけを含む場合、発行元の但し書きをそのまま持たせる
# —— 「この市区町村はこの局番」と言い切れないことが、その文そのものだからである。
say("市外局番を解決中…")
bt = rd("bridge_municipality_telephone")
tav = rd("telephone_area_version").filter(pl.col("is_current") == 1)
tel = bt.join(
    tav.select("numbering_area_code", "area_code"), on="numbering_area_code", how="left",
)
if tel.filter(pl.col("area_code").is_null()).height:
    sys.exit("番号区画に対応する市外局番が見つからない行がある")
telephone: dict[str, list] = {}
for lg, area, code, coverage, note in tel.select(
    "lg_code", "area_code", "numbering_area_code", "coverage_type", "mismatch_note"
).iter_rows():
    entry = {"a": area, "z": code, "c": coverage}
    if note:
        entry["t"] = note
    telephone.setdefault(lg, []).append(entry)
for lg in telephone:
    telephone[lg] = sorted(telephone[lg], key=lambda e: e["a"])
say(f"市外局番 {tel.height:,} 行 / {len(telephone):,} 市区町村"
    f"（区画の一部のみ {tel.filter(pl.col('coverage_type') == 'partial').height:,}）")

# --------------------------------------------------------------- 鉄道路線
# 路線は596種類しかないのに、(路線名, 運営会社) をそのまま各行に持つと 606 KB になる。
# 名前の配列を1つ持って添字で指すと 1/4 以下になり、即時読み込みのファイルに収まる。
say("路線を解決中…")
line_index: dict[tuple[str, str], int] = {}
line_names: list[list[str]] = []


def _line_id(name: str, operator: str) -> int:
    key = (name, operator)
    if key not in line_index:
        line_index[key] = len(line_names)
        line_names.append([name, operator])
    return line_index[key]


lines_by_lg: dict[str, list[int]] = {}
for lg, ln, op in (
    rd("bridge_line_municipality")
    .filter(pl.col("lg_code").is_not_null())
    .select("lg_code", "line_name_raw", "operator_name_raw")
    .iter_rows()
):
    lines_by_lg.setdefault(lg, []).append(_line_id(ln, op))
lines_by_station: dict[str, list[int]] = {}
for gid, ln, op in (
    rd("bridge_station_line")
    .select("n02_group_code", "line_name_raw", "operator_name_raw")
    .iter_rows()
):
    lines_by_station.setdefault(gid, []).append(_line_id(ln, op))
for d in (lines_by_lg, lines_by_station):
    for k in d:
        d[k] = sorted(set(d[k]))
say(f"路線 {len(line_names):,}  市区町村 {len(lines_by_lg):,}  駅 {len(lines_by_station):,}")

# --------------------------------------------------------------- バス停留所
# 27万件は即時読み込みに載らない（全部で 16.4 MB）。geo.js と同じく2次メッシュごとの
# チャンクにして、見えている範囲だけ読む —— 中央値 2.1 KB、p90 11 KB。
# 「多すぎるから間引く」はしない。欠けた集合は「ここにあるバス停」という問いに、
# 完全に見えるまま誤答する（POLICY.md §3.2）。
say("バス停の点を読み込み中…")
stop_points = _read_p11_geometry(_Paths(root=ROOT))
# 候補が2つある停留所（府県境の10件）は両方に数える。dict にすると後勝ちで片方が
# 黙って消え、POLICY.md §4 が禁じる「候補を1つに決める」ことになる。駅が lg を
# 配列で持っているのと同じ理由。
bus_lg: dict[str, list[str]] = {}
for _sid, _lg in (
    rd("bridge_bus_stop_municipality")
    .filter(pl.col("lg_code").is_not_null())
    .select("p11_stop_id", "lg_code")
    .iter_rows()
):
    bus_lg.setdefault(_sid, []).append(_lg)
# 同じ地点・同じ名称のレコードは、表示のためにまとめる。実測で 278,515 行のうち
# 60,472 行が「名称も座標も同じで事業者だけ違う」—— コミュニティバスと民間路線が同じ
# 停留所に立っている類で、最大12件が1点に重なる。そのまま描くと4分の1近くが同じ
# ピクセルに埋もれ、描画上限も重なりで食い潰される。
#
# まとめるのは*表示*のためであって、捨てるのではない: 事業者は全部ツールチップに残る。
# 行を落とすのは「ここにあるバス停」への誤答になる（POLICY.md §3.2）。
bus_counts: dict[str, int] = {}
merged: dict[str, dict[tuple[str, float, float], list[str]]] = {}
placed = 0
unplaced = 0
for sid, name, operator in (
    rd("p11_bus_stop").select("p11_stop_id", "stop_name_raw", "operator_name_raw").iter_rows()
):
    point = stop_points.get(sid)
    if point is None:
        unplaced += 1
        continue
    placed += 1
    x, y = point
    for lg in bus_lg.get(sid, ()):
        bus_counts[lg] = bus_counts.get(lg, 0) + 1
    p, u = int(y * 1.5), int(x) - 100
    q, v = int((y * 1.5 - p) * 8), int((x - int(x)) * 8)
    key = f"{p:02d}{u:02d}{q}{v}"
    merged.setdefault(key, {}).setdefault(
        (name, round(x, 6), round(y, 6)), []
    ).append(operator)
chunks: dict[str, list] = {
    key: [
        [name, sorted(set(operators)), x, y]
        for (name, x, y), operators in sorted(group.items())
    ]
    for key, group in merged.items()
}
pins = sum(len(v) for v in chunks.values())
say(f"バス停 {placed:,}（点が無いもの {unplaced}）  ピン {pins:,}"
    f"（同一地点をまとめて {placed - pins:,} 行ぶん圧縮）  "
    f"チャンク {len(chunks):,}  市区町村に付くもの {sum(bus_counts.values()):,}")

BUS_DIR = OUT / "bus"
if BUS_DIR.exists():
    for stale in BUS_DIR.glob("*.js"):
        stale.unlink()
BUS_DIR.mkdir(parents=True, exist_ok=True)
largest = 0
for key in sorted(chunks):
    body = json.dumps(sorted(chunks[key]), ensure_ascii=False, separators=(",", ":"))
    text = f'window.JPAC_BUS&&window.JPAC_BUS.put("{key}",{body});\n'
    (BUS_DIR / f"{key}.js").write_text(text, encoding="utf-8", newline="\n")
    largest = max(largest, len(text.encode()))
say(f"チャンク書き出し完了  最大 {largest / 1024:,.0f} KB")

# ------------------------------------------------------------------- write
OUT.mkdir(parents=True, exist_ok=True)
payload = {
    "generated": date.today().isoformat(),
    "stations": stations,
    "postal": postal,
    "telephone": telephone,
    "lineNames": line_names,
    "linesByLg": lines_by_lg,
    "linesByStation": lines_by_station,
    "busCounts": bus_counts,
    # どのチャンクが存在するかを持たせる。無い鍵を要求して 404 を出さないため。
    "busMeshes": sorted(chunks),
    "counts": {
        "stations": len(stations),
        "postal_rows": resolved.height,
        "telephone_rows": tel.height,
        "lines": len(line_names),
        "bus_stops": placed,
    },
}
text = ("// generated by tools/build_site_data.py — do not edit\n"
        "window.JPAC_DATA = "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n")
# newline="\n" は明示する。Windows の既定では "\n" が CRLF に変換され、同じ入力から
# 別のバイト列が出る。`.gitattributes` が「ビルドは環境に依らず LF で書く」と宣言して
# おり、パイプライン側（export/writers.py, pipeline.py）は既にそうしている。ここが
# 抜けていたため、Windows で走らせるたびに 3,600 個のチャンクが行末だけ変わった差分に
# なっていた。
(OUT / "jpac_data.js").write_text(text, encoding="utf-8", newline="\n")
size = (OUT / "jpac_data.js").stat().st_size
say(f"書き出し完了  jpac_data.js {size / 1e6:.2f} MB")
print(f"出力先 {OUT / 'jpac_data.js'}")
