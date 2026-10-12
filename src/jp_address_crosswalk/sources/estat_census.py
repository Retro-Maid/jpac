"""国勢調査 令和7年 人口等基本集計 adapter（docs/POPULATION_PREFLIGHT.md）.

**まだ登録していない。** ライセンスの署名（``docs/LICENSE_REVIEW_POPULATION_2026_10.md``
§1）が済むまで ``SOURCE_CLASSES`` にも ``config/sources.yml`` にも入れない。登録後は
``required: false`` で、ペイロードが無ければ ``jpac build`` はこのソースを飛ばし、
表の整合の検査を ``skip`` として品質レポートに記録する。

ペイロードは e-Stat の結果表2本（xlsx）である。

- ``population`` —— 第1-1表（statInfId 000040506423、``b01_01.xlsx``）
- ``age`` —— 第2-7表（statInfId 000040506431、``b02_07.xlsx``）

第2-7表は第1-1表の人口と突き合わせるので、1本でも欠ければ止める。
"""

from __future__ import annotations

import polars as pl

from ..build.census import (
    layout_t01,
    layout_t07,
    parse_t01,
    parse_t07,
    read_xlsx_rows,
    table_notes,
)
from ..errors import SourceFetchFailed
from ..logging_setup import get_logger, stage_context
from ..payload import FetchResult
from ..snapshot import SchemaInfo, SourceSnapshot, make_snapshot_id, utcnow
from .base import BaseSource

log = get_logger(__name__)

RESOURCE_KEYS = ("population", "age")


class EstatCensusSource(BaseSource):
    name = "estat_census_2025"
    provider = "総務省統計局"
    required = False

    def inspect(self, fetched: dict[str, FetchResult]) -> dict[str, SchemaInfo]:
        info: dict[str, SchemaInfo] = {}
        with stage_context(self.name, "inspect"):
            for key, fr in fetched.items():
                rows = read_xlsx_rows(fr.path.read_bytes())
                if key == "population":
                    start, labels = layout_t01(rows)
                else:
                    start, labels, _cols = layout_t07(rows)
                # 注記も列の一部として指紋に入れる。面積の出どころ（注記5）のような
                # 意味の変化を、スキーマの変化として止めるため（preflight §2.1）。
                columns = [*labels, *(f"note:{n}" for n in table_notes(rows))]
                info[key] = SchemaInfo(
                    columns=columns, column_count=len(labels), encoding="utf-8",
                    delimiter="", has_header=True, sheet_names=[], container="xlsx",
                    members=[], row_count=sum(1 for r in rows[start:] if any(r)),
                )
        return info

    def parse(self, fetched: dict[str, FetchResult]) -> dict[str, pl.DataFrame]:
        missing = [k for k in RESOURCE_KEYS if k not in fetched]
        if missing:
            raise SourceFetchFailed(
                "国勢調査は第1-1表と第2-7表が揃って初めて読める", missing=missing
            )
        with stage_context(self.name, "parse"):
            raw = parse_t01(fetched["population"].path.read_bytes())
            age = parse_t07(fetched["age"].path.read_bytes())
            log.info("parsed census tables", regions=raw.height, age_rows=age.height)
        return {"census_population_raw": raw, "_census_age": age}

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
