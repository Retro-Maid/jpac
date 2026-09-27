"""e-Stat 廃置分合等情報 adapter（docs/MUNICIPALITY_LINEAGE_PREFLIGHT.md）.

ライセンスは 2026-09-27 に人が署名した（``docs/LICENSE_REVIEW_LINEAGE_2026_09.md`` §1）。
``required: false`` で、ペイロードが無ければ ``jpac build`` はこのソースを飛ばし、署名の
検査を ``skip`` として品質レポートに記録する。

ペイロードは3本の CSV で、どれも同じ検索条件（境界変更を除く全事由・全都道府県・
1970-01-01〜2028-12-31）の結果である。

- ``changes`` —— 本体
- ``changes_created`` —— 同じ条件に「新設」の絞り込みを足したもの
- ``changes_abolished`` —— 同じ条件に「廃止」の絞り込みを足したもの

新設・廃止の2本は飾りではない。「コード追加なし」の新設合併で引き継いだコードと、
分離・編入で旧コードが存続するかどうかは、この2本からしか分からない
（``build/municipality_change.py``）。1本でも欠ければ、推測で埋めずに止める。
"""

from __future__ import annotations

import polars as pl

from ..build.municipality_change import COLUMNS, build_change_tables, read_change_csv
from ..errors import SourceFetchFailed
from ..logging_setup import get_logger, stage_context
from ..payload import FetchResult
from ..snapshot import SchemaInfo, SourceSnapshot, make_snapshot_id, utcnow
from .base import BaseSource

log = get_logger(__name__)

RESOURCE_KEYS = ("changes", "changes_created", "changes_abolished")


class EstatMunicipalityChangeSource(BaseSource):
    name = "estat_municipality_change"
    provider = "総務省統計局"
    required = False

    def inspect(self, fetched: dict[str, FetchResult]) -> dict[str, SchemaInfo]:
        info: dict[str, SchemaInfo] = {}
        with stage_context(self.name, "inspect"):
            for key, fr in fetched.items():
                df = read_change_csv(fr.path.read_bytes())
                info[key] = SchemaInfo(
                    columns=list(COLUMNS), column_count=len(COLUMNS), encoding="utf-8",
                    delimiter=",", has_header=True, sheet_names=[], container="csv",
                    members=[], row_count=df.height,
                )
        return info

    def parse(self, fetched: dict[str, FetchResult]) -> dict[str, pl.DataFrame]:
        missing = [k for k in RESOURCE_KEYS if k not in fetched]
        if missing:
            raise SourceFetchFailed(
                "e-Stat 廃置分合等情報は本体・新設・廃止の3本が揃って初めて読める",
                missing=missing,
            )
        with stage_context(self.name, "parse"):
            frames = {k: read_change_csv(fetched[k].path.read_bytes()) for k in RESOURCE_KEYS}
            tables = build_change_tables(
                frames["changes"], frames["changes_created"], frames["changes_abolished"]
            )
            raw, edges = tables["municipality_change_raw"], tables["municipality_lineage_edge"]
            log.info(
                "parsed e-Stat municipality changes",
                rows=raw.height,
                unresolved_rows=raw.filter(pl.col("parse_status") == "unresolved").height,
                edges=edges.height,
                unique_edges=edges.filter(pl.col("is_unique_match")).height,
            )
        return tables

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
