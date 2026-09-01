"""MIC 電気通信番号指定状況 (固定電話等) adapter.

Nine BIFF8 workbooks, one per leading digit of the area code. Together they give
``numbering_area_code -> area_code -> local_code`` plus carrier and usage status.

They carry no address field and are never used to infer geography — only the
市外局番の一覧 does that.
"""

from __future__ import annotations

import re

import polars as pl
import xlrd

from ..errors import SourceFetchFailed
from ..logging_setup import get_logger, stage_context
from ..payload import FetchResult
from ..snapshot import SchemaInfo, SourceSnapshot, make_snapshot_id, utcnow
from .base import BaseSource
from .mic_area_code import normalize_area_code, normalize_numbering_area_code

log = get_logger(__name__)

EXPECTED_HEADER = ["番号区画コード", "番号", "市外局番", "市内局番", "事業者", "使用状況", "備考"]
COLUMNS = ["numbering_area_code", "number", "area_code", "local_code",
           "carrier", "usage_status", "remarks"]

_CURRENT_AS_OF = re.compile(r"[（(]?(令和|平成)([０-９0-9元]+)年([０-９0-9]+)月([０-９0-9]+)日現在[）)]?")
_FIXED_PHONE_RE = re.compile(r"固定電話.*?(\d)\s*から始まる市外局番")


class MicNumberAssignmentSource(BaseSource):
    name = "mic_number_assignment"
    provider = "総務省"
    required = True


    def _discover_urls(self, cfg: dict, disc: dict) -> tuple[list[str], str]:
        """Resolve the nine 固定電話 workbooks from the e-Gov dataset page."""
        egov = cfg.get("egov_dataset")
        if egov:
            try:
                html, final_url = self.client.get_text(egov)
                found = sorted(
                    set(re.findall(r"https://www\.soumu\.go\.jp/main_content/\d+\.xls\b", html))
                )
                if len(found) == 9:
                    return found, "discovery"
                # Taking found[:9] would silently pick whichever nine sorted
                # first, which after an e-Gov page change could be last year's.
                raise SourceFetchFailed(
                    "e-Gov listed an unexpected number of 固定電話 workbooks; "
                    "the resource set must be reviewed before it is trusted",
                    found=len(found), expected=9, final_url=final_url,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("e-Gov discovery failed", error=str(exc))

        observed = disc.get("observed_urls") or []
        if not observed:
            raise SourceFetchFailed("no MIC number-assignment URLs discoverable")
        log.warning("falling back to recorded MIC number-assignment URLs", count=len(observed))
        return list(observed), "recorded_fallback"


    def inspect(self, fetched: dict[str, FetchResult]) -> dict[str, SchemaInfo]:
        info: dict[str, SchemaInfo] = {}
        with stage_context(self.name, "inspect"):
            for key, fr in fetched.items():
                book = xlrd.open_workbook(str(fr.path))
                sheet = book.sheet_by_index(0)
                header = _find_header(sheet)
                info[key] = SchemaInfo(
                    columns=header, column_count=len(header), encoding="biff8",
                    delimiter="", has_header=True,
                    sheet_names=list(book.sheet_names()), container="none",
                    members=[],
                    row_count=max(0, sheet.nrows - _header_row_index(sheet) - 1),
                )
        return info

    def parse(self, fetched: dict[str, FetchResult]) -> dict[str, pl.DataFrame]:
        rows: list[dict] = []
        current_as_of = ""
        with stage_context(self.name, "parse"):
            for key in sorted(fetched):
                book = xlrd.open_workbook(str(fetched[key].path))
                sheet = book.sheet_by_index(0)
                current_as_of = current_as_of or _extract_current_as_of(sheet)
                header_row = _header_row_index(sheet)
                header = [str(c.value).strip() for c in sheet.row(header_row)]
                if header[: len(EXPECTED_HEADER)] != EXPECTED_HEADER:
                    raise SourceFetchFailed(
                        "unexpected MIC number-assignment header",
                        key=key, observed=header, expected=EXPECTED_HEADER,
                    )
                for r in range(header_row + 1, sheet.nrows):
                    values = [_cell_str(c) for c in sheet.row(r)]
                    values += [""] * (len(COLUMNS) - len(values))
                    code = normalize_numbering_area_code(values[0])
                    if not code:
                        continue
                    rows.append(
                        dict(zip(COLUMNS, values[: len(COLUMNS)], strict=False))
                        | {
                            "numbering_area_code": code,
                            # Same normalizer as the Word source, so the two MIC
                            # datasets always express a code identically.
                            "area_code": normalize_area_code(values[2]),
                            "current_as_of": current_as_of,
                        }
                    )

        if not rows:
            raise SourceFetchFailed("no MIC number-assignment rows parsed")
        df = pl.DataFrame(
            rows,
            schema={c: pl.Utf8 for c in [*COLUMNS, "current_as_of"]},
        ).unique(subset=["numbering_area_code", "number"], keep="first").sort(
            ["area_code", "local_code", "numbering_area_code"]
        )
        log.info(
            "parsed MIC number assignment",
            rows=df.height,
            areas=df["numbering_area_code"].n_unique(),
            current_as_of=current_as_of,
        )
        return {"telephone_number_block": df}

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


def _cell_str(cell) -> str:
    """Render a cell as text without ever letting a code become a number.

    xlrd yields floats for numeric cells, so a naive ``str()`` turns
    ``011`` into ``11.0``. Codes are text (docs/POLICY.md §8).
    """
    value = cell.value
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    return str(value).strip()


def _header_row_index(sheet) -> int:
    for r in range(min(10, sheet.nrows)):
        if str(sheet.cell_value(r, 0)).strip() == EXPECTED_HEADER[0]:
            return r
    raise SourceFetchFailed("MIC number-assignment header row not found")


def _find_header(sheet) -> list[str]:
    return [str(c.value).strip() for c in sheet.row(_header_row_index(sheet))]


def _extract_current_as_of(sheet) -> str:
    for r in range(min(5, sheet.nrows)):
        for c in sheet.row(r):
            m = _CURRENT_AS_OF.search(str(c.value))
            if m:
                era, y, mo, d = m.groups()
                trans = str.maketrans("０１２３４５６７８９", "0123456789")
                y = 1 if y == "元" else int(y.translate(trans))
                mo = int(mo.translate(trans))
                d = int(d.translate(trans))
                base = 2018 if era == "令和" else 1988
                return f"{base + y:04d}-{mo:02d}-{d:02d}"
    return ""
