"""MLIT 位置参照情報 大字・町丁目レベル adapter.

The download site is a CGI wizard served as EUC-JP. The adapter walks it only
far enough to discover the current version string (e.g. ``19.0b``) and then
fetches from the stable data path. Hardcoding a version would silently ship
stale data after the annual refresh; hardcoding nothing at all would mean no
drift detection, so the discovered version is checked against the recorded one.

Only the ``b`` (大字・町丁目) level is ever requested; ``a`` is 街区 level and
is out of scope for V1.
"""

from __future__ import annotations

import re

import polars as pl

from ..errors import SourceFetchFailed
from ..logging_setup import get_logger, stage_context
from ..payload import FetchResult, iter_zip_csv_members
from ..snapshot import (
    SchemaInfo,
    SourceSnapshot,
    count_csv_rows,
    make_snapshot_id,
    utcnow,
)
from .base import BaseSource

log = get_logger(__name__)

ISJ_COLUMNS = [
    "都道府県コード", "都道府県名", "市区町村コード", "市区町村名",
    "大字町丁目コード", "大字町丁目名", "緯度", "経度",
    "原典資料コード", "大字・字・丁目区分コード",
]

ISJ_RENAME = {
    "都道府県コード": "pref_code",
    "都道府県名": "pref_name",
    "市区町村コード": "jis_city_code",
    "市区町村名": "city_name",
    "大字町丁目コード": "mlit_code",
    "大字町丁目名": "town_name_raw",
    "緯度": "latitude",
    "経度": "longitude",
    "原典資料コード": "source_material_code",
    "大字・字・丁目区分コード": "aza_class_code",
}

_VERSION_RE = re.compile(r"\b(\d+\.\d+b)\b")
PREFECTURES = [f"{i:02d}" for i in range(1, 48)]


class MlitSource(BaseSource):
    name = "mlit"
    provider = "国土交通省"
    required = True


    def _discover_version(self, disc: dict) -> str:
        """Walk the CGI wizard to read the current 版数."""
        entry = disc["entry"]
        enc = disc.get("page_encoding", "euc_jp")
        base = entry.rsplit("/", 1)[0] + "/"
        try:
            self.client.get_text(entry, encoding=enc)
            html, _ = self.client.post_text(
                base + "_view_cities_wards.cgi", {"sbm": "2", "action": "x"}, encoding=enc
            )
            acs = re.findall(r'value="(\d+)"\s+name="ac"', html)
            if not acs:
                raise SourceFetchFailed("no prefecture entries in ISJ wizard")
            html2, _ = self.client.post_text(
                base + "_choose_files.cgi",
                {"sbm": "2", "srh": "", "oa": "", "pc": "", "ac": acs[0]},
                encoding=enc,
            )
            versions = sorted(set(_VERSION_RE.findall(html2)))
            if not versions:
                raise SourceFetchFailed("no 版数 found in ISJ file chooser")
            # Highest numeric version wins; the 'b' suffix pins 大字・町丁目 level.
            return max(versions, key=lambda v: [int(x) for x in v[:-1].split(".")])
        except SourceFetchFailed:
            raise
        except Exception as exc:  # noqa: BLE001
            recorded = disc.get("observed_version")
            if recorded:
                log.warning(
                    "ISJ version discovery failed; using the recorded version",
                    error=str(exc), version=recorded,
                )
                return recorded
            raise SourceFetchFailed(f"ISJ version discovery failed: {exc}") from exc


    def inspect(self, fetched: dict[str, FetchResult]) -> dict[str, SchemaInfo]:
        import zipfile

        info: dict[str, SchemaInfo] = {}
        with stage_context(self.name, "inspect"):
            for key, fr in fetched.items():
                with zipfile.ZipFile(fr.path) as zf:
                    members = sorted(i.filename for i in zf.infolist())
                    csvs = [m for m in members if m.lower().endswith(".csv")]
                    if not csvs:
                        raise SourceFetchFailed("no CSV member", key=key)
                    with zf.open(csvs[0]) as fh:
                        header = fh.readline().decode("cp932").rstrip("\r\n")
                cols = [c.strip().strip('"') for c in header.split(",")]
                with zipfile.ZipFile(fr.path) as zf, zf.open(csvs[0]) as fh:
                    rows = count_csv_rows(fh.read(), has_header=True)
                info[key] = SchemaInfo(
                    columns=cols, column_count=len(cols), encoding="cp932",
                    delimiter=",", has_header=True, container="zip", members=members,
                    row_count=rows,
                )
        return info

    def parse(self, fetched: dict[str, FetchResult]) -> dict[str, pl.DataFrame]:
        frames: list[pl.DataFrame] = []
        with stage_context(self.name, "parse"):
            for key in sorted(fetched):
                fr = fetched[key]
                for member, raw in iter_zip_csv_members(fr.path):
                    text = raw.decode("cp932", errors="strict")
                    df = pl.read_csv(
                        text.encode("utf-8"), has_header=True, infer_schema_length=0,
                        encoding="utf8", quote_char='"',
                    )
                    missing = [c for c in ISJ_COLUMNS if c not in df.columns]
                    if missing:
                        raise SourceFetchFailed(
                            "unexpected ISJ columns", member=member, missing=missing,
                            observed=df.columns,
                        )
                    version = _version_from_member(member) or ""
                    df = (
                        df.select(ISJ_COLUMNS)
                        .rename(ISJ_RENAME)
                        .with_columns(
                            [
                                # Some ISJ rows pad values with trailing spaces,
                                # which would otherwise yield 13-character
                                # 大字町丁目コード and break every code join.
                                pl.col(c).cast(pl.Utf8).str.strip_chars()
                                for c in ISJ_RENAME.values()
                                if c not in ("latitude", "longitude")
                            ]
                        )
                        .with_columns(
                            [
                                pl.col("latitude").cast(pl.Float64, strict=False),
                                pl.col("longitude").cast(pl.Float64, strict=False),
                                pl.lit(_fiscal_year(member)).alias("fiscal_year"),
                                # Taken from the archive path, not from discovery
                                # metadata: an offline rebuild has no discovery
                                # step, and an empty value there would change every
                                # row's content hash and trigger a mass supersede.
                                pl.lit(version).alias("isj_version"),
                            ]
                        )
                    )
                    frames.append(df)

        if not frames:
            raise SourceFetchFailed("no ISJ data parsed")
        out = pl.concat(frames, how="vertical").sort("mlit_code")
        log.info("parsed MLIT ISJ", rows=out.height, files=len(frames))
        return {"isj": out}

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
                    source_version=res.version, published_at=fr.last_modified,
                    downloaded_at=utcnow(), etag=fr.etag, last_modified=fr.last_modified,
                    sha256=fr.sha256, file_size=fr.size,
                    row_count=row_counts.get(res.key),
                    schema_fingerprint=schemas[res.key].fingerprint(),
                    resolved_via=res.resolved_via,
                )
            )
        self._snapshots = snaps
        return snaps


def _version_from_member(member: str) -> str | None:
    """``01000-19.0b/01_2025.csv`` -> ``19.0b``."""
    m = re.search(r"-(\d+\.\d+[ab])/", member)
    return m.group(1) if m else None


def _fiscal_year(member: str) -> str:
    m = re.search(r"_(\d{4})\.csv$", member)
    return m.group(1) if m else ""
