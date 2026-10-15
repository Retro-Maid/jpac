"""国土数値情報 P29 学校（2023年度版）adapter（docs/POLICY.md §3.5、docs/LICENSE_REVIEW_SCHOOL_2026_10.md §6）.

**座標アセットのためだけに読む。** 学校の一覧そのものは文部科学省 学校コード
（``mext_school_code``）が持ち、P29 からは学校コード・キャンパスごとの点だけを取る。
P29 の属性 ``P29_002`` は同じ学校コードなので、1対1ではなくキャンパスごとに結べる。

2026-09-28 にいったん中止したレイヤである。詳細ページは原典資料に地理院地図を挙げ、
「測量法に基づく国土地理院長承認（使用）R 3JHs 917」と書いている。2026-10-15、それを
伝えたうえでメンテナが座標アセットとしての採用を選んだ。利用許諾条件は「オープン
データ（CC_BY_4.0）」で、国土数値情報の規約 §1.3 の適用除外（「一部制限」「商用可」
「非商用」「記載なし」）に当たらず、§1.2 が承認を求めるのは国土調査の成果である。

読むのは全国1ファイル ``P29-23_GML.zip`` の中のシェープファイル（.cpg が UTF-8、
.prj が JGD2011）。点はビルド時に ``read_points`` で読み、属性の行と添字で対にする。
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from ..errors import SourceFetchFailed
from ..logging_setup import get_logger, stage_context
from ..payload import (
    ArchiveLimits,
    FetchResult,
    open_zip_safely,
    read_dbf_member,
    read_shp_member,
    safe_zip_members,
)
from ..snapshot import SchemaInfo, SourceSnapshot, make_snapshot_id, utcnow
from .base import BaseSource

log = get_logger(__name__)

ENCODING = "utf-8"
EXPECTED_CPG = "UTF-8"
# 全国1ファイルの DBF は 45 MB で、固定長の空白が大半を占める（圧縮比は約 260）。
# P11 と同じく、上限は全体では緩めずこのソースにだけ与える。
P29_LIMITS = ArchiveLimits(max_compression_ratio=400)
MEMBER = "P29-23"

COLUMNS = [f"P29_{i:03d}" for i in range(1, 10)]
RENAME = {
    "P29_001": "p29_admin_code",      # 行政区域コード5桁（P29 作成時点）
    "P29_002": "school_code",         # 学校コード（文部科学省）
    "P29_003": "p29_school_class",    # 学校分類コード
    "P29_004": "school_name_raw",     # 名称
    "P29_005": "address_raw",         # 所在地
    "P29_006": "p29_manager_code",    # 管理者コード
    "P29_007": "closed_code_raw",     # 休校区分（0 調査なし／1 開校中／2 休校中。9 はコード表に無い）
    "P29_008": "campus_code",         # キャンパスコード（00 なし、01 本校、02〜 それ以降）
    "P29_009": "campus_note_raw",     # 学校名備考（キャンパスの名称等）
}


def _check_container(path: Path) -> None:
    with open_zip_safely(path, P29_LIMITS) as zf:
        members = safe_zip_members(zf, P29_LIMITS)
        cpg = [m for m in members if m.filename.lower().endswith(".cpg") and MEMBER in m.filename]
        prj = [m for m in members if m.filename.lower().endswith(".prj") and MEMBER in m.filename]
        if len(cpg) != 1 or len(prj) != 1:
            raise SourceFetchFailed(
                "P29 archive must hold exactly one .cpg and one .prj",
                cpg=[m.filename for m in cpg], prj=[m.filename for m in prj],
            )
        declared = zf.read(cpg[0]).decode("ascii", "replace").strip()
        if declared.upper() != EXPECTED_CPG:
            raise SourceFetchFailed(
                "P29 declares an unexpected character set", declared=declared,
                expected=EXPECTED_CPG,
            )
        datum = zf.read(prj[0]).decode("ascii", "replace")
        if "JGD_2011" not in datum:
            raise SourceFetchFailed("P29 is not in JGD2011", prj=datum[:120])


def read_attributes(path: Path) -> tuple[list[str], list[list[str]]]:
    _check_container(path)
    return read_dbf_member(path, limits=P29_LIMITS, encoding=ENCODING, contains=MEMBER)


def read_points(path: Path) -> list[tuple[float, float] | None]:
    """属性の行と同じ順の点。null の図形は None のまま残す（添字をずらさない）。"""
    _check_container(path)
    shapes = read_shp_member(path, contains=MEMBER, expect=1, limits=P29_LIMITS)
    return [None if s is None else s[1][0][0] for s in shapes]


class MlitKsjP29Source(BaseSource):
    name = "mlit_ksj_p29"
    provider = "国土交通省 国土政策局 国土情報課"
    required = False

    def inspect(self, fetched: dict[str, FetchResult]) -> dict[str, SchemaInfo]:
        info: dict[str, SchemaInfo] = {}
        with stage_context(self.name, "inspect"):
            for key, fr in fetched.items():
                names, rows = read_attributes(fr.path)
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
                    "expected exactly one P29 edition (2023年度)", found=sorted(fetched)
                )
            fr = next(iter(fetched.values()))
            names, rows = read_attributes(fr.path)
            missing = [c for c in COLUMNS if c not in names]
            if missing:
                raise SourceFetchFailed(
                    "P29 DBF is missing expected columns", missing=missing, observed=names
                )
            index = [names.index(c) for c in COLUMNS]
            schools = pl.DataFrame(
                [[r[i] for i in index] for r in rows], schema=COLUMNS, orient="row"
            ).rename(RENAME).with_row_index("p29_row")
            dup = schools.height - schools.select("school_code", "campus_code").unique().height
            if dup:
                raise SourceFetchFailed(
                    "P29 has duplicate (学校コード, キャンパスコード)", duplicates=dup
                )
            log.info(
                "parsed P29 schools",
                rows=schools.height,
                schools=schools["school_code"].n_unique(),
                campuses=schools.filter(pl.col("campus_code") != "00").height,
            )
        return {"p29_school": schools}

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
