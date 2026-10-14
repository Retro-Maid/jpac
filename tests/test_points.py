"""駅・バス停の座標アセット (src/jp_address_crosswalk/build/points.py, POLICY.md §3.4).

The asset is allowed only because it is separate: the core artifacts keep
exactly the coordinates they had before (MLIT 位置参照情報's own points), and the
points never pass through ``dist/parquet``, which ``jpac export`` and the diff
baseline read wholesale. Most of these tests pin that separation.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import zipfile
from pathlib import Path

import polars as pl

from jp_address_crosswalk.build import points as P
from jp_address_crosswalk.pipeline import RELEASE_ARTIFACT_NAMES, Paths, write_points_asset

ROOT = Path(__file__).resolve().parents[1]

# An L: 2 units east, then 2 units north. Centroid (1.5, 0.5) is off the line;
# the halfway point by length is the corner (2, 0).
L_SHAPE = [[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)]]


def stations(*codes: str) -> pl.DataFrame:
    return pl.DataFrame(
        [{"n02_group_code": c, "station_name_raw": f"駅{c}", "operator_name_raw": "会社"}
         for c in codes],
        schema={"n02_group_code": pl.Utf8, "station_name_raw": pl.Utf8,
                "operator_name_raw": pl.Utf8},
    )


def stops(*ids: str) -> pl.DataFrame:
    return pl.DataFrame(
        [{"p11_stop_id": i, "stop_name_raw": f"停{i}", "operator_name_raw": "バス"} for i in ids],
        schema={"p11_stop_id": pl.Utf8, "stop_name_raw": pl.Utf8, "operator_name_raw": pl.Utf8},
    )


def bridge(placements: dict[str, str]) -> pl.DataFrame:
    return pl.DataFrame(
        [{"n02_group_code": g, "placement_point": h} for g, h in placements.items()]
    )


class TestRepresentativePoint:
    def test_centroid_by_default(self):
        pt, how = P.representative_point(L_SHAPE)
        assert (pt, how) == ((1.5, 0.5), "centroid")

    def test_follows_the_point_the_bridge_used(self):
        # 判定が中点で決まった駅は、配る点も中点。重心を配ると、点と判定が食い違う。
        pt, how = P.representative_point(L_SHAPE, "point_on_line")
        assert (pt, how) == ((2.0, 0.0), "point_on_line")

    def test_unresolved_station_still_gets_its_centroid(self):
        assert P.representative_point(L_SHAPE, "none")[1] == "centroid"

    def test_rounded_to_six_digits(self):
        pt, _ = P.representative_point([[(139.1234567891, 35.0), (139.1234567891, 35.0000002)]])
        assert pt == (139.123457, 35.0)

    def test_no_geometry_is_none_not_a_guess(self):
        assert P.representative_point([]) == (None, None)


class TestTables:
    def test_one_row_per_station_including_those_without_geometry(self):
        out = P.build_station_points(
            stations("B", "A"), {"A": L_SHAPE}, bridge({"A": "centroid"}), "snap"
        )
        assert out["n02_group_code"].to_list() == ["A", "B"]
        assert out.row(1, named=True)["longitude"] is None
        assert out.row(1, named=True)["placement_point"] is None
        assert out["source_snapshot_id"].to_list() == ["snap", "snap"]
        assert out.schema == pl.Schema(P.STATION_POINT_SCHEMA)

    def test_bus_stop_point_is_the_published_value_unrounded(self):
        out = P.build_bus_stop_points(stops("s1", "s2"), {"s1": (139.12345678912, 35.98765432198)}, "snap")
        row = out.row(0, named=True)
        assert (row["longitude"], row["latitude"]) == (139.12345678912, 35.98765432198)
        assert out.row(1, named=True)["longitude"] is None
        assert out.schema == pl.Schema(P.BUS_STOP_POINT_SCHEMA)


class TestCheck:
    def test_clean_input_passes(self):
        st = stations("A")
        br = bridge({"A": "centroid"})
        pts = {"station_point": P.build_station_points(st, {"A": [[(139.0, 35.0), (139.1, 35.0)]]}, br, "s")}
        assert P.check_points(pts, {"n02_station": st, "bridge_station_municipality": br}) == []

    def test_row_count_and_disagreement_with_bridge_are_reported(self):
        st = stations("A", "B")
        pts = {"station_point": P.build_station_points(
            stations("A"), {"A": [[(139.0, 35.0), (139.1, 35.0)]]}, None, "s")}
        problems = P.check_points(
            pts, {"n02_station": st, "bridge_station_municipality": bridge({"A": "point_on_line"})}
        )
        assert any("1 行" in p for p in problems)
        assert any("ブリッジの判定" in p for p in problems)

    def test_coordinates_outside_japan_are_reported(self):
        st = stops("s")
        pts = {"bus_stop_point": P.build_bus_stop_points(st, {"s": (35.0, 139.0)}, "s")}
        assert any("範囲外" in p for p in P.check_points(pts, {"p11_bus_stop": st}))


class TestSeparation:
    def test_asset_is_listed_for_sha256sums(self):
        assert P.POINTS_ZIP_NAME in RELEASE_ARTIFACT_NAMES

    def test_points_never_land_in_dist_parquet(self, tmp_path):
        paths = Paths(tmp_path)
        st = stations("A")
        pts = {"station_point": P.build_station_points(st, {"A": [[(139.0, 35.0), (139.1, 35.0)]]}, None, "s")}
        out, problems = write_points_asset(paths, pts, {"n02_station": st})
        assert problems == []
        assert not paths.parquet.exists() or not list(paths.parquet.glob("*point*"))
        assert zipfile.ZipFile(out).namelist() == ["station_point.parquet"]

    def test_zip_is_byte_deterministic(self, tmp_path):
        st = stations("A", "B")
        pts = {"station_point": P.build_station_points(st, {"A": L_SHAPE}, None, "s")}
        digests = []
        for sub in ("one", "two"):
            out, _ = write_points_asset(Paths(tmp_path / sub), pts, {"n02_station": st})
            digests.append(hashlib.sha256(out.read_bytes()).hexdigest())
        assert digests[0] == digests[1]

    def test_a_build_without_points_removes_the_previous_asset(self, tmp_path):
        paths = Paths(tmp_path)
        stale = paths.dist / P.POINTS_ZIP_NAME
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"old")
        assert write_points_asset(paths, {}, {}) == (None, [])
        assert not stale.exists()

    def test_core_schema_gains_no_coordinates(self):
        # 本体で座標を持つのは国交省 位置参照情報の町字の点だけ（元から）。駅・路線・
        # バス停の表に座標の列が足されたら、それは §3.4 の「別アセット」を破っている。
        con = sqlite3.connect(":memory:")
        con.executescript((ROOT / "docs" / "schema.sql").read_text(encoding="utf-8"))
        coord = re.compile(r"(^|_)(lat|lon|lng|latitude|longitude|x|y)$", re.I)
        with_coords = {
            t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if any(coord.search(c[1]) for c in con.execute(f'PRAGMA table_info("{t}")'))
        }
        assert with_coords == {"mlit_town_version"}
