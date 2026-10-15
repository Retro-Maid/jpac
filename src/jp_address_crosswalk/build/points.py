"""駅・バス停・学校の座標 —— 本体とは別のリリースアセット (docs/POLICY.md §3.4・§3.5).

本体の成果物（SQLite・Parquet・CSV・テーブル別 zip）は座標を持たない。ここで作る3表は
``jp_address_crosswalk_points.zip`` にだけ入り、``dist/parquet`` には書かない。
``jpac export`` と差分の基準（data/previous）が読むのはそちらなので、置き場所を分ける
ことが「本体は変わらない」を保証する仕組みそのものである。

* **駅は代表点。** N02 は駅を線（GM_Curve）で公開しており、公式の点は無い。ここで出す
  点は ``bridge_station_municipality`` が市区町村の判定に実際に使った点と同じもの
  （``representative_point``）で、地図（tools/build_site_data.py）もこの関数を使う。
  3か所が同じ関数を通るので、ピンと表と判定が食い違わない。
* **バス停は公表された点そのもの。** P11 が停留所ごとに点を公表しているので、計算も
  丸めもしない。丸めは公表値を書き換えることになる。
* **学校は P29 の点そのもの。** 国土数値情報 P29（2023年度版）が学校コード・キャンパス
  ごとに公表している点を、計算も丸めもせずに出す。学校の一覧（``mext_school``）とは学校
  コードで結ぶ。キャンパスごとに1行なので、1校が複数行になることがある（POLICY.md §3.5）。
* **元の表と1対1。** 図形が無い駅・バス停も行を残し、座標を NULL にする
  （POLICY.md §5）。「点が無い」と「その駅が無い」は別の主張である。
* **境界から作った座標は出さない。** 市区町村や小地域の重心はここに無い
  （POLICY.md §3.1 の「not re-derived into coordinates」はそのまま）。

測地系は JGD2011 の経緯度（EPSG:6668）。両ソースの .prj で確かめてから読んでいる
（pipeline._assert_one_datum）。
"""

from __future__ import annotations

import polars as pl

from .spatial import line_centroid, point_on_line

POINTS_ZIP_NAME = "jp_address_crosswalk_points.zip"

PLACEMENT_CENTROID = "centroid"
PLACEMENT_ON_LINE = "point_on_line"

# 地図と同じ桁。0.000001 度は約 0.1 m で、線から計算した代表点にそれ以上の桁は意味が無い。
STATION_DIGITS = 6


def representative_point(parts, placement: str | None = None):
    """駅の代表点と、それがどの規則で取られたか。

    ``placement`` は ``bridge_station_municipality.placement_point``。判定が中点に
    落ちた駅（``point_on_line``）はその点を返し、それ以外（``centroid``、どの
    ポリゴンにも入らなかった ``none``、ブリッジが無い場合）は重心を返す。重心が
    取れない退化した図形だけ中点に落とす。図形が無ければ ``(None, None)``。
    """
    if placement == PLACEMENT_ON_LINE:
        order = ((point_on_line, PLACEMENT_ON_LINE), (line_centroid, PLACEMENT_CENTROID))
    else:
        order = ((line_centroid, PLACEMENT_CENTROID), (point_on_line, PLACEMENT_ON_LINE))
    for fn, how in order:
        pt = fn(parts)
        if pt is not None:
            return (round(pt[0], STATION_DIGITS), round(pt[1], STATION_DIGITS)), how
    return None, None


STATION_POINT_SCHEMA = {
    "n02_group_code": pl.Utf8,
    "station_name_raw": pl.Utf8,
    "operator_name_raw": pl.Utf8,
    "longitude": pl.Float64,
    "latitude": pl.Float64,
    # 'centroid' か 'point_on_line'。座標が NULL の行は NULL。
    "placement_point": pl.Utf8,
    "source_snapshot_id": pl.Utf8,
}

BUS_STOP_POINT_SCHEMA = {
    "p11_stop_id": pl.Utf8,
    "stop_name_raw": pl.Utf8,
    "operator_name_raw": pl.Utf8,
    "longitude": pl.Float64,
    "latitude": pl.Float64,
    "source_snapshot_id": pl.Utf8,
}


def build_station_points(
    stations: pl.DataFrame,
    geometry: dict[str, list],
    bridge: pl.DataFrame | None,
    snapshot_id: str | None,
) -> pl.DataFrame:
    """``n02_station`` 1行につき1行。点はブリッジの判定に使ったもの。"""
    used: dict[str, str] = {}
    if bridge is not None and "placement_point" in bridge.columns:
        used = dict(bridge.select("n02_group_code", "placement_point").unique().iter_rows())
    rows = []
    for r in stations.select("n02_group_code", "station_name_raw", "operator_name_raw").iter_rows(named=True):
        gid = r["n02_group_code"]
        pt, how = representative_point(geometry.get(gid, []), used.get(gid))
        rows.append({
            **r,
            "longitude": pt[0] if pt else None,
            "latitude": pt[1] if pt else None,
            "placement_point": how,
            "source_snapshot_id": snapshot_id,
        })
    return pl.DataFrame(rows, schema=STATION_POINT_SCHEMA).sort("n02_group_code")


def build_bus_stop_points(
    stops: pl.DataFrame,
    geometry: dict[str, tuple[float, float]],
    snapshot_id: str | None,
) -> pl.DataFrame:
    """``p11_bus_stop`` 1行につき1行。点は P11 の公表値をそのまま。"""
    rows = []
    for r in stops.select("p11_stop_id", "stop_name_raw", "operator_name_raw").iter_rows(named=True):
        pt = geometry.get(r["p11_stop_id"])
        rows.append({
            **r,
            "longitude": pt[0] if pt else None,
            "latitude": pt[1] if pt else None,
            "source_snapshot_id": snapshot_id,
        })
    return pl.DataFrame(rows, schema=BUS_STOP_POINT_SCHEMA).sort("p11_stop_id")


SCHOOL_POINT_SCHEMA = {
    "school_code": pl.Utf8,
    # 00 キャンパスの区別なし ／ 01 本校 ／ 02〜 それ以降のキャンパス（P29 のコード表）
    "campus_code": pl.Utf8,
    "school_name_raw": pl.Utf8,
    "campus_note_raw": pl.Utf8,
    # P29 作成時点（2023年度）の5桁の行政区域コード。jpac の lg_code ではない。
    "p29_admin_code": pl.Utf8,
    # 0 調査なし ／ 1 開校中 ／ 2 休校中（9 はコード表に無いが発行元の値のまま）
    "closed_code_raw": pl.Utf8,
    "longitude": pl.Float64,
    "latitude": pl.Float64,
    "source_snapshot_id": pl.Utf8,
}


def build_school_points(
    p29: pl.DataFrame,
    geometry: list[tuple[float, float] | None],
    snapshot_id: str | None,
) -> pl.DataFrame:
    """P29 の1行（学校 × キャンパス）につき1行。点は公表値をそのまま。

    ``geometry`` は P29 のシェープファイルの点を DBF と同じ順に並べたもの。
    行数が違えば、属性と点がずれて別の学校の位置になるので止める。
    """
    if len(geometry) != p29.height:
        raise ValueError(
            f"P29 の点 {len(geometry)} 個と属性 {p29.height} 行が対にならない"
        )
    rows = []
    for r in p29.iter_rows(named=True):
        pt = geometry[r["p29_row"]]
        rows.append({
            "school_code": r["school_code"],
            "campus_code": r["campus_code"],
            "school_name_raw": r["school_name_raw"],
            "campus_note_raw": r["campus_note_raw"] or None,
            "p29_admin_code": r["p29_admin_code"],
            "closed_code_raw": r["closed_code_raw"],
            "longitude": pt[0] if pt else None,
            "latitude": pt[1] if pt else None,
            "source_snapshot_id": snapshot_id,
        })
    return pl.DataFrame(rows, schema=SCHOOL_POINT_SCHEMA).sort(["school_code", "campus_code"])


POINT_TABLES = {
    "station_point": STATION_POINT_SCHEMA,
    "bus_stop_point": BUS_STOP_POINT_SCHEMA,
    "school_point": SCHOOL_POINT_SCHEMA,
}


def check_points(points: dict[str, pl.DataFrame], tables: dict[str, pl.DataFrame]) -> list[str]:
    """座標の表が元の表・ブリッジと食い違っていないか。問題の文のリスト（空なら合格）。"""
    problems: list[str] = []
    pairs = (("station_point", "n02_station", "n02_group_code"),
             ("bus_stop_point", "p11_bus_stop", "p11_stop_id"))
    for name, source, key in pairs:
        if name not in points:
            continue
        pts, src = points[name], tables.get(source)
        if src is None:
            problems.append(f"{name}: 元の表 {source} が無い")
            continue
        if pts.height != src.height or pts[key].n_unique() != pts.height:
            problems.append(f"{name}: {pts.height} 行・キー {pts[key].n_unique()} 種。{source} は {src.height} 行")
        elif set(pts[key].to_list()) != set(src[key].to_list()):
            problems.append(f"{name}: キーの集合が {source} と一致しない")
        bad = pts.filter(
            pl.col("longitude").is_not_null()
            & ~(pl.col("longitude").is_between(122, 154) & pl.col("latitude").is_between(20, 46))
        ).height
        if bad:
            problems.append(f"{name}: 日本の範囲外の座標が {bad} 行")
    if "school_point" in points:
        pts = points["school_point"]
        keys = pts.select("school_code", "campus_code").unique().height
        if keys != pts.height:
            problems.append(f"school_point: (school_code, campus_code) が {pts.height - keys} 行重複")
        bad = pts.filter(
            pl.col("longitude").is_not_null()
            & ~(pl.col("longitude").is_between(122, 154) & pl.col("latitude").is_between(20, 46))
        ).height
        if bad:
            problems.append(f"school_point: 日本の範囲外の座標が {bad} 行")
        schools = tables.get("mext_school")
        if schools is not None:
            unknown = pts.filter(
                ~pl.col("school_code").is_in(schools["school_code"].implode())
            )["school_code"].n_unique()
            if unknown:
                problems.append(f"school_point: 学校コードの一覧に無い学校が {unknown} 校")
    bridge = tables.get("bridge_station_municipality")
    if "station_point" in points and bridge is not None:
        used = bridge.filter(pl.col("placement_point").is_in([PLACEMENT_CENTROID, PLACEMENT_ON_LINE]))
        joined = used.select("n02_group_code", "placement_point").unique().join(
            points["station_point"].select("n02_group_code", pl.col("placement_point").alias("p")),
            on="n02_group_code", how="left",
        )
        diff = joined.filter(pl.col("placement_point") != pl.col("p")).height
        if diff:
            problems.append(f"station_point: ブリッジの判定と違う点の駅が {diff}")
    return problems
