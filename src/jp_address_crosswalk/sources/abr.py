"""Digital Agency Address Base Registry adapter.

Discovery uses the catalog's DCAT-US feed and selects datasets by exact title.
If the feed or a title disappears, the adapter fails closed rather than
guessing a URL.
"""

from __future__ import annotations

import polars as pl

from ..errors import SourceFetchFailed
from ..logging_setup import get_logger, stage_context
from ..payload import FetchResult, read_zip_member
from ..snapshot import (
    SchemaInfo,
    SourceSnapshot,
    count_csv_rows,
    make_snapshot_id,
    utcnow,
)
from .base import BaseSource, Discovery

log = get_logger(__name__)

# Every code column is Utf8. Reading these as integers would destroy the
# leading zeros that make them valid (docs/POLICY.md §8).
TOWN_CODE_COLUMNS = [
    "lg_code", "machiaza_id", "machiaza_type", "chome_number",
    "rsdt_addr_flg", "rsdt_addr_mtd_code", "oaza_cho_aka_flg", "koaza_aka_code",
    "oaza_cho_gsi_uncmn", "koaza_gsi_uncmn", "status_flg", "wake_num_flg",
    "src_code", "post_code",
]

TOWN_EXPECTED_COLUMNS = [
    "lg_code", "machiaza_id", "machiaza_type", "pref", "pref_kana", "pref_roma",
    "county", "county_kana", "county_roma", "city", "city_kana", "city_roma",
    "ward", "ward_kana", "ward_roma", "oaza_cho", "oaza_cho_kana", "oaza_cho_roma",
    "chome", "chome_kana", "chome_number", "koaza", "koaza_kana", "koaza_roma",
    "machiaza_dist", "rsdt_addr_flg", "rsdt_addr_mtd_code", "oaza_cho_aka_flg",
    "koaza_aka_code", "oaza_cho_gsi_uncmn", "koaza_gsi_uncmn", "status_flg",
    "wake_num_flg", "efct_date", "ablt_date", "src_code", "post_code", "remarks",
]

POSTAL_CONV_EXPECTED_COLUMNS = [
    "lg_code", "machiaza_id", "pref", "county", "city", "ward", "kyoto_st",
    "oaza_cho", "chome", "koaza", "machiaza_dist", "post_code", "add_date", "dlt_date",
]


class AbrSource(BaseSource):
    name = "abr"
    provider = "デジタル庁"
    required = True


    @staticmethod
    def _pick_download_url(key: str, spec: dict, entry: dict) -> tuple[str, str]:
        """Prefer the catalog's own distribution URL over the recorded one."""
        if key == "postal_conversion":
            # The conversion table is an ArcGIS "CSV Collection" item; its
            # payload is served from the item data endpoint, not from a DCAT
            # distribution.
            item_id = spec["arcgis_item_id"]
            return (
                f"https://www.arcgis.com/sharing/rest/content/items/{item_id}/data",
                "discovery",
            )
        for dist in entry.get("distribution", []):
            url = dist.get("accessURL", "")
            if url.endswith(".csv.zip"):
                return url, "discovery"
        observed = spec.get("observed_url")
        if observed:
            log.warning(
                "no .csv.zip distribution in the feed; falling back to the recorded URL",
                key=key, url=observed,
            )
            return observed, "recorded_fallback"
        raise SourceFetchFailed("no usable distribution URL", key=key)


    def inspect(self, fetched: dict[str, FetchResult]) -> dict[str, SchemaInfo]:
        import zipfile

        info: dict[str, SchemaInfo] = {}
        with stage_context(self.name, "inspect"):
            for key, fr in fetched.items():
                with zipfile.ZipFile(fr.path) as zf:
                    members = sorted(i.filename for i in zf.infolist())
                    csv_member = self._csv_member(key, members)
                    with zf.open(csv_member) as fh:
                        header = fh.readline().decode("utf-8-sig").rstrip("\r\n")
                cols = [c.strip() for c in header.split(",")]
                with zipfile.ZipFile(fr.path) as zf, zf.open(csv_member) as fh:
                    rows = count_csv_rows(fh.read(), has_header=True)
                info[key] = SchemaInfo(
                    columns=cols, column_count=len(cols), encoding="utf-8",
                    delimiter=",", has_header=True, container="zip", members=members,
                    row_count=rows,
                )
        return info

    @staticmethod
    def _csv_member(key: str, members: list[str]) -> str:
        csvs = [m for m in members if m.lower().endswith(".csv")]
        if not csvs:
            raise SourceFetchFailed("no CSV member in archive", key=key, members=members)
        return csvs[0]

    def parse(self, fetched: dict[str, FetchResult]) -> dict[str, pl.DataFrame]:
        out: dict[str, pl.DataFrame] = {}
        with stage_context(self.name, "parse"):
            if "town_master" in fetched:
                out["town"] = self._parse_town(fetched["town_master"])
            if "city_master" in fetched:
                out["city"] = self._parse_city(fetched["city_master"])
            if "postal_conversion" in fetched:
                out["postal_conversion"] = self._parse_postal_conversion(
                    fetched["postal_conversion"]
                )
        return out

    def _read_zip_csv(self, fr: FetchResult, expected: list[str]) -> pl.DataFrame:
        import zipfile

        with zipfile.ZipFile(fr.path) as zf:
            members = sorted(i.filename for i in zf.infolist())
        member = self._csv_member("", members)
        raw = read_zip_member(fr.path, member)
        # Every column is read as Utf8 and narrowed later. Letting Polars infer
        # would turn 011011 into 11011 (docs/POLICY.md §8).
        df = pl.read_csv(
            raw,
            has_header=True,
            infer_schema_length=0,
            schema_overrides=None,
            encoding="utf8",
            truncate_ragged_lines=False,
        )
        df = df.with_columns([pl.col(c).cast(pl.Utf8) for c in df.columns])
        missing = [c for c in expected if c not in df.columns]
        if missing:
            raise SourceFetchFailed(
                "expected columns missing from ABR CSV", missing=missing,
                observed=df.columns,
            )
        return df

    def _parse_town(self, fr: FetchResult) -> pl.DataFrame:
        df = self._read_zip_csv(fr, TOWN_EXPECTED_COLUMNS)
        df = df.with_columns(
            pl.col("lg_code").str.slice(0, 5).alias("jis_city_code")
        )
        log.info("parsed ABR town master", rows=df.height)
        return df

    def _parse_city(self, fr: FetchResult) -> pl.DataFrame:
        df = self._read_zip_csv(fr, ["lg_code", "pref", "city"])
        df = df.with_columns(pl.col("lg_code").str.slice(0, 5).alias("jis_city_code"))
        log.info("parsed ABR city master", rows=df.height)
        return df

    def _parse_postal_conversion(self, fr: FetchResult) -> pl.DataFrame:
        df = self._read_zip_csv(fr, POSTAL_CONV_EXPECTED_COLUMNS)
        # machiaza_id is empty for municipality-level postal codes; that
        # emptiness is meaningful and must survive as an explicit null.
        df = df.with_columns(
            [
                pl.col("lg_code").str.slice(0, 5).alias("jis_city_code"),
                pl.when(pl.col("machiaza_id").str.len_chars() == 0)
                .then(None)
                .otherwise(pl.col("machiaza_id"))
                .alias("machiaza_id"),
            ]
        )
        n_muni = df.filter(pl.col("machiaza_id").is_null()).height
        log.info(
            "parsed ABR postal conversion table",
            rows=df.height, municipality_level_rows=n_muni,
        )
        return df

    def build_snapshots(
        self,
        discovery: Discovery,
        fetched: dict[str, FetchResult],
        schemas: dict[str, SchemaInfo],
        row_counts: dict[str, int],
    ) -> list[SourceSnapshot]:
        snaps = []
        for res in discovery.resources:
            fr = fetched[res.key]
            snaps.append(
                SourceSnapshot(
                    source_snapshot_id=make_snapshot_id(res.dataset_name, fr.sha256),
                    provider=self.provider,
                    dataset_name=res.dataset_name,
                    source_page_url=discovery.source_page_url,
                    download_url=res.url,
                    license_name=discovery.license_name,
                    license_url=discovery.license_url,
                    license_text_sha256=discovery.license_text_sha256,
                    source_version=res.version,
                    published_at=res.published_at,
                    downloaded_at=utcnow(),
                    etag=fr.etag,
                    last_modified=fr.last_modified,
                    sha256=fr.sha256,
                    file_size=fr.size,
                    row_count=row_counts.get(res.key),
                    schema_fingerprint=schemas[res.key].fingerprint(),
                    resolved_via=res.resolved_via,
                )
            )
        self._snapshots = snaps
        return snaps
