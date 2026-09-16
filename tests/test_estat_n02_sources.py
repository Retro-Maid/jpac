"""DBF reading and the two V2 source adapters (docs/STATION_JOIN_PLAN.md §6).

The DBF reader is hand-written rather than a dependency, so the header
arithmetic it relies on is asserted here rather than assumed. The adapter tests
cover the two mistakes that would not announce themselves: reading the wrong
copy of a table that ships twice under different encodings, and counting a
station once per platform.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import pytest

from jp_address_crosswalk.errors import SourceFetchFailed
from jp_address_crosswalk.payload import FetchResult, read_dbf, read_dbf_member
from jp_address_crosswalk.sources.estat import EstatBoundarySource
from jp_address_crosswalk.sources.mlit_ksj_n02 import MlitKsjN02Source


def build_dbf(fields: list[tuple[str, int]], rows: list[list[str]], encoding: str = "cp932") -> bytes:
    """A minimal but valid level-5 DBF, so the reader is tested against the format."""
    header_len = 32 + 32 * len(fields) + 1
    record_len = sum(w for _, w in fields) + 1
    out = bytearray(struct.pack("<BBBBIHH", 0x03, 26, 9, 5, len(rows), header_len, record_len))
    out += b"\x00" * 20
    for name, width in fields:
        out += name.encode("ascii").ljust(11, b"\x00")
        out += b"C"
        out += b"\x00" * 4
        out += bytes([width, 0])
        out += b"\x00" * 14
    out += b"\x0d"
    for row in rows:
        out += b" "
        for (_, width), value in zip(fields, row, strict=True):
            out += value.encode(encoding).ljust(width, b" ")[:width]
    return bytes(out)


def zip_with(tmp_path: Path, members: dict[str, bytes], name: str = "a.zip") -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        for member, data in members.items():
            zf.writestr(member, data)
    return path


def fetch_result(path: Path) -> FetchResult:
    return FetchResult(
        url="https://example.invalid/f", final_url="https://example.invalid/f",
        path=path, sha256="0" * 64, size=path.stat().st_size,
        etag=None, last_modified=None, content_type=None,
    )


class TestReadDbf:
    def test_reads_fields_and_rows(self) -> None:
        data = build_dbf([("PREF", 2), ("CITY", 3)], [["01", "101"], ["13", "104"]])
        names, rows = read_dbf(data)
        assert names == ["PREF", "CITY"]
        assert rows == [["01", "101"], ["13", "104"]]

    def test_zero_padded_codes_survive(self) -> None:
        """The 013100 → 13100 corruption README.md warns about."""
        names, rows = read_dbf(build_dbf([("CODE", 6)], [["013100"]]))
        assert rows == [["013100"]]

    def test_deleted_records_are_skipped(self) -> None:
        data = bytearray(build_dbf([("A", 1)], [["x"], ["y"]]))
        header_len = int.from_bytes(bytes(data[8:10]), "little")
        data[header_len] = ord("*")          # tombstone the first record
        _, rows = read_dbf(bytes(data))
        assert rows == [["y"]]

    def test_a_header_that_does_not_add_up_is_an_error(self) -> None:
        """Silence here would mean every field offset is wrong but plausible."""
        data = bytearray(build_dbf([("A", 4)], [["abcd"]]))
        data[10:12] = (99).to_bytes(2, "little")   # lie about the record length
        with pytest.raises(SourceFetchFailed, match="do not add up"):
            read_dbf(bytes(data))

    def test_truncated_input_is_an_error(self) -> None:
        with pytest.raises(SourceFetchFailed, match="shorter than its own header"):
            read_dbf(b"short")


class TestReadDbfMember:
    def test_an_ambiguous_match_is_refused(self, tmp_path: Path) -> None:
        """N02 really ships Shift-JIS and UTF-8 copies of the same table."""
        dbf = build_dbf([("A", 1)], [["x"]])
        path = zip_with(tmp_path, {"g/Shift-JIS/X_Station.dbf": dbf, "g/UTF-8/X_Station.dbf": dbf})
        with pytest.raises(SourceFetchFailed, match="exactly one matching DBF"):
            read_dbf_member(path, contains="_Station")

    def test_several_substrings_disambiguate(self, tmp_path: Path) -> None:
        path = zip_with(
            tmp_path,
            {
                "g/Shift-JIS/X_Station.dbf": build_dbf([("A", 1)], [["s"]]),
                "g/UTF-8/X_Station.dbf": build_dbf([("A", 1)], [["u"]], encoding="utf-8"),
            },
        )
        _, rows = read_dbf_member(path, encoding="utf-8", contains=("UTF-8/", "_Station"))
        assert rows == [["u"]]


class TestN02Adapter:
    STATION_FIELDS = [
        ("N02_001", 2), ("N02_002", 2), ("N02_003", 12), ("N02_004", 12),
        ("N02_005", 12), ("N02_005c", 6), ("N02_005g", 6),
    ]
    SECTION_FIELDS = [("N02_001", 2), ("N02_002", 2), ("N02_003", 12), ("N02_004", 12)]

    def _archive(self, tmp_path: Path, station_rows: list[list[str]]) -> Path:
        return zip_with(
            tmp_path,
            {
                "N02-25_GML/UTF-8/N02-25_Station.dbf":
                    build_dbf(self.STATION_FIELDS, station_rows, "utf-8"),
                "N02-25_GML/Shift-JIS/N02-25_Station.dbf":
                    build_dbf(self.STATION_FIELDS, station_rows, "cp932"),
                "N02-25_GML/UTF-8/N02-25_RailroadSection.dbf":
                    build_dbf(self.SECTION_FIELDS, [["11", "2", "line", "op"]], "utf-8"),
            },
        )

    def test_features_are_grouped_into_stations(self, tmp_path: Path) -> None:
        """One station, three platform features — the ~11.6% inflation N02 carries."""
        rows = [
            ["11", "2", "線A", "会社A", "盛岡", "000001", "000792"],
            ["11", "2", "線B", "会社A", "盛岡", "000002", "000792"],
            ["11", "2", "線C", "会社B", "盛岡", "000003", "000792"],
            ["11", "2", "線A", "会社A", "花巻", "000004", "000800"],
        ]
        src = MlitKsjN02Source({})
        out = src.parse({"n02_2025": fetch_result(self._archive(tmp_path, rows))})
        stations = out["n02_station"]
        assert out["n02_station_feature"].height == 4
        assert stations.height == 2
        morioka = stations.filter(stations["n02_group_code"] == "000792")
        assert morioka["feature_count"][0] == 3
        # A shared station keeps the fact that it is shared, rather than the
        # single retained operator implying it is not.
        assert morioka["operator_variants"][0] == 2
        assert morioka["line_variants"][0] == 3

    def test_grouping_is_deterministic(self, tmp_path: Path) -> None:
        """`first()` must not depend on DBF row order (docs/ARCHITECTURE.md §6)."""
        forward = [
            ["11", "2", "線A", "会社A", "駅", "000002", "000792"],
            ["11", "2", "線B", "会社B", "駅", "000001", "000792"],
        ]
        other = tmp_path / "b"
        other.mkdir()
        src = MlitKsjN02Source({})
        a = src.parse({"n02_2025": fetch_result(self._archive(tmp_path, forward))})["n02_station"]
        b = src.parse(
            {"n02_2025": fetch_result(self._archive(other, list(reversed(forward))))}
        )["n02_station"]
        assert a.to_dicts() == b.to_dicts()

    def test_an_edition_without_group_codes_is_refused(self, tmp_path: Path) -> None:
        """v2.3 would otherwise silently return one station per platform."""
        fields = [f for f in self.STATION_FIELDS if f[0] not in ("N02_005c", "N02_005g")]
        path = zip_with(
            tmp_path,
            {
                "N02_GML/UTF-8/N02_Station.dbf":
                    build_dbf(fields, [["11", "2", "線", "会社", "駅"]], "utf-8"),
            },
        )
        with pytest.raises(SourceFetchFailed, match="grouping codes"):
            MlitKsjN02Source({}).parse({"n02": fetch_result(path)})

    def test_more_than_one_edition_is_refused(self, tmp_path: Path) -> None:
        """Two fiscal years in one build would mix two licence regimes."""
        archive = self._archive(tmp_path, [["11", "2", "線", "会社", "駅", "1", "1"]])
        with pytest.raises(SourceFetchFailed, match="exactly one N02 edition"):
            MlitKsjN02Source({}).parse(
                {"n02_2024": fetch_result(archive), "n02_2025": fetch_result(archive)}
            )


class TestEstatAdapter:
    FIELDS = [
        ("KEY_CODE", 9), ("PREF", 2), ("CITY", 3), ("S_AREA", 6), ("PREF_NAME", 8),
        ("CITY_NAME", 12), ("S_NAME", 12), ("HCODE", 4), ("KIGO_E", 2),
        ("AREA_MAX_F", 1), ("KIGO_D", 2), ("N_KEN", 2), ("N_CITY", 3), ("KIGO_I", 1),
    ]

    def test_jis_city_code_is_assembled_and_stays_zero_padded(self, tmp_path: Path) -> None:
        rows = [[
            "011010200", "01", "101", "020000", "北海道", "札幌市中央区", "円山",
            "8101", "", "M", "", "", "", "",
        ]]
        path = zip_with(tmp_path, {"A/r2ka01.dbf": build_dbf(self.FIELDS, rows)})
        out = EstatBoundarySource({}).parse({"pref_01": fetch_result(path)})
        df = out["estat_small_area"]
        assert df["jis_city_code"].to_list() == ["01101"]

    def test_a_missing_column_names_itself(self, tmp_path: Path) -> None:
        fields = [f for f in self.FIELDS if f[0] != "HCODE"]
        rows = [[
            "011010200", "01", "101", "020000", "北海道", "札幌市中央区", "円山",
            "", "M", "", "", "", "",
        ]]
        path = zip_with(tmp_path, {"A/r2ka01.dbf": build_dbf(fields, rows)})
        with pytest.raises(SourceFetchFailed, match="missing expected columns"):
            EstatBoundarySource({}).parse({"pref_01": fetch_result(path)})
