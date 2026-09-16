"""国土数値情報 N02 鉄道（駅・鉄道区間）adapter.

Only the 2025年度 edition is ever read. The licence splits by fiscal year —
「2020年（令和2年）以降：オープンデータ（CC_BY_4.0）／上記以外：商用可」 — and the
publisher ships one zip per year, so taking a single file is what keeps the two
regimes from being mixed (``config/sources.yml``: ``mlit_ksj_n02.year_note``).

**A station is a line, not a point, and one station is several lines.** The
payload has 10,234 features for 9,046 stations: platforms and tracks are
separate polylines, up to 11 for a single station. ``N02_005g`` (駅グループ
コード) is the publisher's own grouping, and joining before applying it inflates
every downstream count by ~11.6%. That is the reason this adapter refuses to run
against an edition without the code: v2.3 lacks both ``N02_005c`` and
``N02_005g``, and would silently produce the inflated answer.

Geometry is not read here. Grouping and every attribute the station table needs
come from the DBF; the .shp is only required by the later point-in-polygon
stage, which is a separate decision with a separate dependency.
"""

from __future__ import annotations

import polars as pl

from ..errors import SourceFetchFailed
from ..logging_setup import get_logger, stage_context
from ..payload import FetchResult, read_dbf_member
from ..snapshot import SchemaInfo, SourceSnapshot, make_snapshot_id, utcnow
from .base import BaseSource

log = get_logger(__name__)

ENCODING = "utf-8"
# The archive ships the same tables twice, under Shift-JIS/ and UTF-8/. Naming
# the directory is not cosmetic: decoding the Shift-JIS copy as UTF-8 yields
# mojibake that still parses, so the station names would be silently wrong.
ENCODING_DIR = "UTF-8/"

STATION_COLUMNS = [
    "N02_001",   # 鉄道区分
    "N02_002",   # 事業者種別
    "N02_003",   # 路線名
    "N02_004",   # 運営会社
    "N02_005",   # 駅名
    "N02_005c",  # 駅コード
    "N02_005g",  # 駅グループコード — the reason this edition is used
]
STATION_RENAME = {
    "N02_001": "railway_class",
    "N02_002": "operator_class",
    "N02_003": "line_name_raw",
    "N02_004": "operator_name_raw",
    "N02_005": "station_name_raw",
    "N02_005c": "n02_station_code",
    "N02_005g": "n02_group_code",
}

SECTION_COLUMNS = ["N02_001", "N02_002", "N02_003", "N02_004"]
SECTION_RENAME = {
    "N02_001": "railway_class",
    "N02_002": "operator_class",
    "N02_003": "line_name_raw",
    "N02_004": "operator_name_raw",
}

# Present in v2.3 too; their absence is what identifies an edition that cannot
# be grouped. Checked explicitly so the failure names the cause rather than
# surfacing as a wrong count.
GROUPING_COLUMNS = ("N02_005c", "N02_005g")


def _frame(fr: FetchResult, contains: str, columns: list[str], rename: dict[str, str]) -> pl.DataFrame:
    names, rows = read_dbf_member(fr.path, encoding=ENCODING, contains=(ENCODING_DIR, contains))
    missing = [c for c in columns if c not in names]
    if missing:
        detail = (
            "N02 edition lacks the station grouping codes; v2.3 and earlier cannot "
            "be used because one station would be counted once per platform"
            if any(c in GROUPING_COLUMNS for c in missing)
            else "N02 DBF is missing expected columns"
        )
        raise SourceFetchFailed(detail, missing=missing, observed=names, member=contains)
    index = {c: names.index(c) for c in columns}
    return pl.DataFrame(
        [{c: r[index[c]] for c in columns} for r in rows],
        schema={c: pl.Utf8 for c in columns},
    ).rename(rename)


class MlitKsjN02Source(BaseSource):
    name = "mlit_ksj_n02"
    provider = "国土交通省"
    required = False

    def inspect(self, fetched: dict[str, FetchResult]) -> dict[str, SchemaInfo]:
        info: dict[str, SchemaInfo] = {}
        with stage_context(self.name, "inspect"):
            for key, fr in fetched.items():
                names, rows = read_dbf_member(fr.path, encoding=ENCODING, contains=(ENCODING_DIR, "_Station"))
                info[key] = SchemaInfo(
                    columns=names, column_count=len(names), encoding=ENCODING,
                    delimiter="", has_header=True, sheet_names=[], container="zip",
                    members=[], row_count=len(rows),
                )
        return info

    def parse(self, fetched: dict[str, FetchResult]) -> dict[str, pl.DataFrame]:
        with stage_context(self.name, "parse"):
            if len(fetched) != 1:
                raise SourceFetchFailed(
                    "expected exactly one N02 edition; mixing fiscal years mixes "
                    "two licence regimes",
                    found=sorted(fetched),
                )
            fr = next(iter(fetched.values()))
            features = _frame(fr, "_Station", STATION_COLUMNS, STATION_RENAME)
            sections = _frame(fr, "_RailroadSection", SECTION_COLUMNS, SECTION_RENAME)

        if features.is_empty():
            raise SourceFetchFailed("no N02 station features parsed")
        blank = features.filter(pl.col("n02_group_code") == "").height
        if blank:
            # A blank group code cannot be grouped and would become its own
            # station, so this is a data problem worth stopping for rather than
            # a row to drop.
            raise SourceFetchFailed(
                "N02 station features carry a blank 駅グループコード", rows=blank
            )

        stations = (
            # Sorted before grouping so `first()` is a defined choice rather than
            # DBF row order, which keeps a rebuild byte-identical
            # (docs/ARCHITECTURE.md §6).
            features.sort(["n02_group_code", "n02_station_code", "line_name_raw"])
            .group_by("n02_group_code")
            .agg(
                pl.len().alias("feature_count"),
                # NOT a modal or authoritative value. A group can span operators
                # and lines — measured: 399 stations have more than one operator
                # (盛岡 is JR + IGR いわて銀河鉄道) and 816 more than one line — so
                # these are "one of N", and the *_variants counts beside them are
                # what tells a reader that. Nothing is discarded: the ungrouped
                # n02_station_feature table keeps every row.
                pl.col("station_name_raw").first(),
                pl.col("line_name_raw").first(),
                pl.col("operator_name_raw").first(),
                pl.col("railway_class").first(),
                pl.col("operator_class").first(),
                pl.col("n02_station_code").first(),
                pl.col("station_name_raw").n_unique().alias("station_name_variants"),
                pl.col("line_name_raw").n_unique().alias("line_variants"),
                pl.col("operator_name_raw").n_unique().alias("operator_variants"),
            )
            .sort("n02_group_code")
        )
        log.info(
            "parsed N02 railway data",
            features=features.height,
            stations=stations.height,
            inflation_avoided=features.height - stations.height,
            max_features_per_station=int(stations["feature_count"].max()),
            multi_feature_stations=stations.filter(pl.col("feature_count") > 1).height,
            stations_spanning_operators=stations.filter(pl.col("operator_variants") > 1).height,
            distinct_station_names=int(features["station_name_raw"].n_unique()),
            operators=int(features["operator_name_raw"].n_unique()),
            sections=sections.height,
        )
        return {"n02_station": stations, "n02_station_feature": features,
                "n02_railroad_section": sections}

    def build_snapshots(self, discovery, fetched, schemas, row_counts):
        snaps = []
        for res in discovery.resources:
            fr = fetched[res.key]
            snaps.append(
                SourceSnapshot(
                    source_snapshot_id=make_snapshot_id(res.dataset_name, fr.sha256),
                    provider=self.provider, dataset_name=res.dataset_name,
                    source_page_url=discovery.source_page_url, download_url=res.url,
                    license_name=discovery.license_name, license_url=discovery.license_url,
                    license_text_sha256=discovery.license_text_sha256,
                    source_version=res.version, published_at=res.published_at,
                    edition_origin=res.edition_origin,
                    downloaded_at=utcnow(), etag=fr.etag, last_modified=fr.last_modified,
                    sha256=fr.sha256, file_size=fr.size,
                    row_count=row_counts.get(res.key),
                    schema_fingerprint=schemas[res.key].fingerprint(),
                    resolved_via=res.resolved_via,
                )
            )
        self._snapshots = snaps
        return snaps
