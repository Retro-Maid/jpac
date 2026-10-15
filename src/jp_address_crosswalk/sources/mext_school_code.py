"""文部科学省 学校コード adapter（docs/LICENSE_REVIEW_SCHOOL_2026_10.md）.

ライセンスは 2026-10-15 に人が署名した（同文書 §1）。``required: false`` で、ペイロードが
無ければ ``jpac build`` はこのソースを飛ばし、一覧の整合の検査を ``skip`` として品質
レポートに記録する。

ペイロードは令和7年5月1日時点（確定版）の一覧 CSV 3本（cp932）である。

- ``east`` —— 北海道〜三重県の学校（大学等を除く）
- ``west`` —— 滋賀県〜沖縄県の学校（大学等を除く）
- ``university`` —— 大学・短期大学・高等専門学校

3本で全国の学校の一覧になる。1本でも欠ければ、その地域の学校が「無い」のか「読んで
いない」のか区別できなくなるので、止める。
"""

from __future__ import annotations

import polars as pl

from ..build.school import COLUMNS, ENCODING, build_school_table, build_succession, read_school_csv
from ..errors import SourceFetchFailed
from ..logging_setup import get_logger, stage_context
from ..payload import FetchResult
from ..snapshot import SchemaInfo, SourceSnapshot, make_snapshot_id, utcnow
from .base import BaseSource

log = get_logger(__name__)

RESOURCE_KEYS = ("east", "university", "west")


class MextSchoolCodeSource(BaseSource):
    name = "mext_school_code"
    provider = "文部科学省"
    required = False

    def inspect(self, fetched: dict[str, FetchResult]) -> dict[str, SchemaInfo]:
        info: dict[str, SchemaInfo] = {}
        with stage_context(self.name, "inspect"):
            for key, fr in fetched.items():
                df = read_school_csv(fr.path.read_bytes())
                info[key] = SchemaInfo(
                    columns=list(COLUMNS), column_count=len(COLUMNS), encoding=ENCODING,
                    delimiter=",", has_header=True, sheet_names=[], container="csv",
                    members=[], row_count=df.height,
                )
        return info

    def parse(self, fetched: dict[str, FetchResult]) -> dict[str, pl.DataFrame]:
        missing = [k for k in RESOURCE_KEYS if k not in fetched]
        if missing:
            raise SourceFetchFailed(
                "学校コードは東日本・西日本・大学等の3本が揃って初めて全国の一覧になる",
                missing=missing,
            )
        with stage_context(self.name, "parse"):
            # どの1本から来た行かを持っておき、行ごとにそのファイルの snapshot を引く。
            raw = pl.concat([
                read_school_csv(fetched[k].path.read_bytes()).with_columns(
                    pl.lit(k).alias("_resource")
                )
                for k in RESOURCE_KEYS
            ])
            resource = dict(raw.select("学校コード", "_resource").iter_rows())
            schools = build_school_table(raw.drop("_resource")).with_columns(
                pl.col("school_code").replace_strict(resource).alias("_resource")
            )
            succession = build_succession(schools)
            log.info(
                "parsed MEXT school codes",
                schools=schools.height,
                active=schools.filter(pl.col("is_active")).height,
                check_digit_invalid=schools.filter(~pl.col("check_digit_valid")).height,
                succession_edges=succession.height,
            )
        return {"mext_school": schools, "mext_school_succession": succession}

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
