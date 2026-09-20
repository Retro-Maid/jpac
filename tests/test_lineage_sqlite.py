"""系譜を経た行が、出荷する SQLite に実際に入ること.

``tests/test_lineage.py`` は綴りを固定し、各ブリッジのテストは DataFrame を見る。
どちらも **CHECK を一度も実行していない**。落ちるのは SQLite の書き込みであり、
実際に一度そうなった（``tests/test_lineage.py`` の
``test_the_spelling_matches_the_shipped_ddl`` の docstring）。

危ないのは2つで、どちらも DataFrame を見ているだけでは見えない:

* ``match_method`` の新しい語 —— CHECK の語彙に無ければ INSERT が落ちる
* **分割を経た行の注記** —— 駅・バス停は ``review_required`` なので注記を付けられる
  が、路線は一意性を要求しないので分割でも ``auto`` になる。そこに注記を付けると
  「auto なら注記は NULL」の CHECK に抵触する。付けていないことをここで固定する
"""

from __future__ import annotations

import polars as pl

from jp_address_crosswalk.build.busstop import build_bus_stop_bridge
from jp_address_crosswalk.build.railroad import build_line_municipality_bridge
from jp_address_crosswalk.build.station import build_station_bridge
from jp_address_crosswalk.export import writers

# 旧浜松市と同じ形。99999 は現行の市区町村に無く、承継先が2つある（分割）。
WEST = ("11111", (0.0, 0.0, 1.0, 1.0),
        [[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]])
GONE = ("99999", (5.0, 5.0, 6.0, 6.0),
        [[(5.0, 5.0), (6.0, 5.0), (6.0, 6.0), (5.0, 6.0), (5.0, 5.0)]])
LG_BY_JIS = {"11111": "111116"}
SPLIT = {"99999": ["888886", "999996"]}      # 旧北区にあたる
ONE_TO_ONE = {"99999": ["999996"]}           # 旧天竜区にあたる


def _station_bridge(successors: dict[str, list[str]]) -> pl.DataFrame:
    stations = pl.DataFrame(
        [{"n02_group_code": "s1", "station_name_raw": "駅", "operator_name_raw": "会社"}],
        schema={"n02_group_code": pl.Utf8, "station_name_raw": pl.Utf8,
                "operator_name_raw": pl.Utf8},
    )
    return build_station_bridge(
        stations, {"s1": [[(5.2, 5.5), (5.8, 5.5)]]}, [WEST, GONE], LG_BY_JIS, successors
    )


def _bus_stop_bridge(successors: dict[str, list[str]]) -> pl.DataFrame:
    stops = pl.DataFrame(
        [{"p11_stop_id": "b1", "stop_name_raw": "停留所", "operator_name_raw": "バス会社"}],
        schema={"p11_stop_id": pl.Utf8, "stop_name_raw": pl.Utf8,
                "operator_name_raw": pl.Utf8},
    )
    return build_bus_stop_bridge(
        stops, {"b1": (5.5, 5.5)}, [WEST, GONE], LG_BY_JIS, successors
    )


def _line_bridge(successors: dict[str, list[str]]) -> pl.DataFrame:
    lines = pl.DataFrame(
        [{"line_name_raw": "本線", "operator_name_raw": "会社"}],
        schema={"line_name_raw": pl.Utf8, "operator_name_raw": pl.Utf8},
    )
    return build_line_municipality_bridge(
        lines, {("本線", "会社"): [[(5.2, 5.5), (5.8, 5.5)]]}, [WEST, GONE], LG_BY_JIS,
        successors=successors,
    )


def _write(tmp_path, tables: dict[str, pl.DataFrame]):
    """出荷と同じ経路で書く。CHECK はここで初めて効く."""
    empty = next(iter(tables.values())).head(0)
    path = tmp_path / "lineage.sqlite"
    writers.write_sqlite(tables, empty, empty, path)
    return path


class TestLineageRowsSurviveTheShippedChecks:
    def test_a_one_to_one_succession_inserts_in_all_three_bridges(self, tmp_path) -> None:
        """``*_via_lineage`` が CHECK の語彙にあること。無ければ INSERT が落ちる."""
        tables = {
            "bridge_station_municipality": _station_bridge(ONE_TO_ONE),
            "bridge_bus_stop_municipality": _bus_stop_bridge(ONE_TO_ONE),
            "bridge_line_municipality": _line_bridge(ONE_TO_ONE),
        }
        for name, frame in tables.items():
            assert frame.height == 1, name
            assert frame["match_method"][0].endswith("_via_lineage"), name
            # 1:1 は含有のまま。ここが auto でなくなっていたら、承継を経ただけで
            # 判定が緩くなった（あるいは厳しくなった）ことになる。
            assert frame["verification_status"][0] == "auto", name
        _write(tmp_path, tables)

    def test_a_split_inserts_too(self, tmp_path) -> None:
        """分割は候補2つ。駅・バス停は注記つき、路線は注記なしで通ること.

        路線が ``auto`` のまま注記を持つと「auto なら注記は NULL」に抵触する。
        DataFrame のテストでは通り、ここで初めて落ちる種類の食い違いである。
        """
        station, stop, line = (
            _station_bridge(SPLIT), _bus_stop_bridge(SPLIT), _line_bridge(SPLIT)
        )
        assert station.height == 2 and stop.height == 2 and line.height == 2
        assert all(n for n in station["mismatch_note"])
        assert all(n for n in stop["mismatch_note"])
        # 路線は一意性を要求しないので分割でも auto。だから注記は付けられない。
        assert set(line["verification_status"]) == {"auto"}
        assert all(n is None for n in line["mismatch_note"])
        _write(tmp_path, {
            "bridge_station_municipality": station,
            "bridge_bus_stop_municipality": stop,
            "bridge_line_municipality": line,
        })
