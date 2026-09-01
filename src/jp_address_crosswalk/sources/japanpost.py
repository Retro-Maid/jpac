"""Japan Post postal code adapter.

Two details here are correctness-critical rather than stylistic:

* The landing page 301-redirects to a different path prefix, so relative links
  must be resolved against the **final** response URL. Resolving against the
  requested URL produces a 404 whose body is HTML — which is why the download
  is validated by magic bytes.
* ``旧郵便番号`` is space-padded (``"060  "``). It is right-stripped only.
  Stripping zeros or casting to an integer destroys it (docs/POLICY.md §8).
"""

from __future__ import annotations

import re

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
from .base import BaseSource

log = get_logger(__name__)

KEN_ALL_COLUMNS = [
    "jis_city_code", "old_postal_code_raw", "postal_code",
    "pref_kana", "city_kana", "town_kana",
    "pref", "city", "town",
    "flag_multi_code", "flag_koaza_banchi", "flag_has_chome", "flag_multi_town",
    "update_flag", "change_reason",
]

_DELTA_RE = re.compile(r"utf_(add|del)_(\d{4})\.zip$")

# Exact suffix classification. Never fuzzy: a mis-classified special record
# would be joined to towns as if it were an ordinary one (docs/POLICY.md §4).
SPECIAL_SUFFIXES: list[tuple[str, str]] = [
    ("以下に掲載がない場合", "no_listing"),
    ("の次に番地がくる場合", "city_banchi"),
    ("一円", "ichien"),
]


class JapanPostSource(BaseSource):
    name = "japanpost"
    provider = "日本郵便株式会社"
    required = True



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
                        first = fh.readline().decode("utf-8-sig").rstrip("\r\n")
                # No header row, so the fingerprint pins the field count.
                n = len(next(iter(_split_csv_line(first)), []))
                with zipfile.ZipFile(fr.path) as zf, zf.open(csvs[0]) as fh:
                    rows = count_csv_rows(fh.read(), has_header=False)
                info[key] = SchemaInfo(
                    columns=KEN_ALL_COLUMNS if n == 15 else [f"col{i}" for i in range(n)],
                    column_count=n, encoding="utf-8-sig", delimiter=",",
                    has_header=False, container="zip", members=members,
                    row_count=rows,
                )
        return info

    def parse(self, fetched: dict[str, FetchResult]) -> dict[str, pl.DataFrame]:
        with stage_context(self.name, "parse"):
            fr = fetched["ken_all"]
            raw = read_zip_member(fr.path, "utf_ken_all.csv")
            df = pl.read_csv(
                raw, has_header=False, new_columns=KEN_ALL_COLUMNS,
                infer_schema_length=0, encoding="utf8", quote_char='"',
            )
            df = df.with_columns([pl.col(c).cast(pl.Utf8) for c in df.columns])
            if df.width != 15:
                raise SourceFetchFailed(
                    "unexpected field count in ken_all", observed=df.width, expected=15
                )

            df = df.with_columns(
                [
                    # Right-strip only. "060  " -> "060", never "60".
                    pl.col("old_postal_code_raw").str.strip_chars_end(" ")
                    .alias("old_postal_code"),
                    _record_kind_expr().alias("record_kind"),
                ]
            )

            counts = (
                df.group_by("record_kind").len().sort("record_kind").to_dicts()
            )
            log.info("parsed Japan Post ken_all", rows=df.height, record_kinds=counts)
            return {"ken_all": df}

    def build_snapshots(
        self, discovery, fetched, schemas, row_counts
    ) -> list[SourceSnapshot]:
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


def _record_kind_expr() -> pl.Expr:
    """Classify special Japan Post records by exact suffix."""
    expr = pl.when(pl.col("town").is_null()).then(pl.lit("no_listing"))
    for suffix, kind in SPECIAL_SUFFIXES:
        expr = expr.when(pl.col("town").str.ends_with(suffix)).then(pl.lit(kind))
    return expr.otherwise(pl.lit("town"))


def _split_csv_line(line: str):
    import csv
    import io

    yield from csv.reader(io.StringIO(line))
