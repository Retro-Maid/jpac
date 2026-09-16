"""国土数値情報 P11 バス停留所 adapter.

Only the **令和4年度 (2022) edition** is ever read. The licence splits by edition
and the two halves are incompatible: 平成22年度 is 「非商用」, which the old
国土情報利用約款 defines as 「非商用目的のみでの利用（ただし複製物の再配布を除く）」
— redistribution excluded outright — while 令和4年度 states 「『国土数値情報
ダウンロードサイトコンテンツ利用規約』に基づく（オープンデータ）」 and falls under
the current terms, where PDL 1.0 applies. A project that redistributes derived
data cannot mix those. See ``docs/LICENSE_REVIEW_P11_2026_09.md``; the human
review is §1 there, not this docstring.

Three things about this payload differ from every other source here.

**It is an archive of archives.** The national file contains one zip per
prefecture, so the inner bytes never exist on disk. ``open_zip_bytes_safely``
applies the same size bound to the decompressed member that
``open_zip_safely`` applies to a file, and ``safe_zip_members`` then runs
unchanged — nesting does not buy an exemption from archive safety.

**Its DBF is 99% padding, which trips a security limit for a legitimate
reason.** Every one of the 73 fields is ``C 254``, so a record is 18,543 bytes
whether or not a stop has 35 bus routes; nationwide the tables decompress to
4.81 GiB and 38 of the 47 prefectures exceed the default compression-ratio cap.
That cap is not wrong and is not lowered globally — ``ArchiveLimits`` is a
per-call argument precisely so one source can state its own bound, and
``P11_LIMITS`` below does that with the measurement attached.

**A bus stop has no publisher identifier.** N02 gives a station ``N02_005g`` and
a line a unique ``(路線名, 運営会社)``; P11 gives neither. Measured on this
edition: 278,515 stops, but only 276,169 distinct ``(バス停名, 事業者名)``, and
the 2,035 colliding groups are **not** 上り/下り pairs — the median maximum
distance inside a group is 16.4 km, the 90th percentile 55 km, and 129 groups
cross a prefecture boundary. Aggregating on the name would therefore fuse stops
16 km apart into one row, which ``docs/POLICY.md`` §4 forbids. Even
``(name, operator, coordinates)`` still collides 12 times, so there is no natural
key to be found by trying harder.

``p11_stop_id`` is therefore a **surrogate row identifier**, not an entity
identity: ``docs/IDENTITY_MODEL.md`` governs ``address_id``, while the schema
already mints synthetic ids for rows that have no natural key (``bridge_id``,
``block_id``, ``coverage_id``). It is the publisher's own record order within a
prefecture, which is deterministic for a pinned edition and, unlike an ordering
by position, needs no coordinates — so this adapter reads no geometry at all,
exactly like the e-Stat and N02 adapters. The .shp is paired with these rows by
index at build time.

The 35 route-name and 35 route-class columns are not ingested. They are what
makes the file enormous, they are route attributes rather than anything this
project crosswalks, and the granularity ceiling here is the municipality
(``POLICY.md`` §3.1).
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from ..errors import SourceFetchFailed
from ..logging_setup import get_logger, stage_context
from ..payload import (
    ArchiveLimits,
    FetchResult,
    open_zip_bytes_safely,
    open_zip_safely,
    read_dbf,
    safe_zip_members,
)
from ..snapshot import SchemaInfo, SourceSnapshot, make_snapshot_id, utcnow
from .base import BaseSource

log = get_logger(__name__)

# The .cpg says SJIS and there is no UTF-8 copy, unlike N02.
ENCODING = "cp932"
EXPECTED_CPG = "SJIS"

# Measured on the 令和4年度 edition: every field is C 254, so a record is 18,543
# bytes regardless of how many routes a stop has, and the largest prefecture
# decompresses to 268 MB at a ratio of 269. The default cap of 200 is right for
# an unknown archive and is left alone; this is a per-source statement that the
# ratio here is fixed-width padding rather than a bomb, and the uncompressed-size
# cap still bounds the real exposure.
P11_LIMITS = ArchiveLimits(max_compression_ratio=400)

COLUMNS = [
    "P11_001",   # バス停名
    "P11_002",   # バス事業者名
    "P11_005",   # 備考
]
RENAME = {
    "P11_001": "stop_name_raw",
    "P11_002": "operator_name_raw",
    "P11_005": "note_raw",
}
# Present in the edition this adapter accepts. Their absence identifies a
# different edition rather than a missing column, so it is reported that way.
ROUTE_COLUMN_PREFIXES = ("P11_003_", "P11_004_")


def stop_id(pref_code: str, seq: int) -> str:
    """The surrogate row id, in one place.

    The build side has to reproduce these exactly to pair a stop with its point,
    so the format lives here rather than being spelled twice: a drift between
    the two spellings would attach every stop to the wrong geometry, and nothing
    downstream would report it.
    """
    return f"p11-22-{pref_code}-{seq:06d}"


def _prefecture(member: str) -> str:
    """``P11-22_13_GML.zip`` → ``13``."""
    stem = member.rsplit("/", 1)[-1]
    parts = stem.split("_")
    if len(parts) < 2 or not parts[1].isdigit():
        raise SourceFetchFailed(
            "cannot read a prefecture code from the nested archive name", member=member
        )
    return parts[1]


def inner_archives(path: Path) -> list[tuple[str, str, bytes]]:
    """``(prefecture code, member name, bytes)`` for each per-prefecture archive.

    Public because the build side reads the stops' geometry out of these same
    archives (``POLICY.md`` §3.1: read at build time, never emitted). Spelling
    the nesting and its limits a second time over there would be a second place
    to get them wrong.
    """
    with open_zip_safely(path, P11_LIMITS) as outer:
        members = [
            m for m in safe_zip_members(outer, P11_LIMITS)
            if m.filename.lower().endswith(".zip")
        ]
        if not members:
            raise SourceFetchFailed(
                "P11 payload contains no per-prefecture archives", path=str(path)
            )
        return [
            (_prefecture(m.filename), m.filename, outer.read(m))
            for m in sorted(members, key=lambda m: m.filename)
        ]


def _read_prefecture(member: str, data: bytes) -> tuple[list[str], list[list[str]]]:
    with open_zip_bytes_safely(data, P11_LIMITS) as iz:
        inner = safe_zip_members(iz, P11_LIMITS)
        dbf = [m for m in inner if m.filename.lower().endswith(".dbf")]
        cpg = [m for m in inner if m.filename.lower().endswith(".cpg")]
        if len(dbf) != 1:
            raise SourceFetchFailed(
                "expected exactly one DBF in the prefecture archive",
                member=member, found=[m.filename for m in dbf],
            )
        if cpg:
            declared = iz.read(cpg[0]).decode("ascii", "replace").strip()
            if declared.upper() != EXPECTED_CPG:
                # Decoding SJIS bytes as something else yields mojibake that still
                # parses, so every stop name would be silently wrong.
                raise SourceFetchFailed(
                    "P11 declares an unexpected character set",
                    member=member, declared=declared, expected=EXPECTED_CPG,
                )
        return read_dbf(iz.read(dbf[0]), ENCODING)


class MlitKsjP11Source(BaseSource):
    name = "mlit_ksj_p11"
    provider = "国土交通省"
    required = False

    def inspect(self, fetched: dict[str, FetchResult]) -> dict[str, SchemaInfo]:
        info: dict[str, SchemaInfo] = {}
        with stage_context(self.name, "inspect"):
            for key, fr in fetched.items():
                archives = inner_archives(fr.path)
                _, member, data = archives[0]
                names, rows = _read_prefecture(member, data)
                info[key] = SchemaInfo(
                    columns=names, column_count=len(names), encoding=ENCODING,
                    delimiter="", has_header=True, sheet_names=[], container="zip",
                    members=[m for _, m, _ in archives], row_count=len(rows),
                )
        return info

    def parse(self, fetched: dict[str, FetchResult]) -> dict[str, pl.DataFrame]:
        with stage_context(self.name, "parse"):
            if len(fetched) != 1:
                raise SourceFetchFailed(
                    "expected exactly one P11 edition; mixing editions mixes a "
                    "redistributable licence with a non-commercial one",
                    found=sorted(fetched),
                )
            fr = next(iter(fetched.values()))
            records: list[dict] = []
            for pref, member, data in inner_archives(fr.path):
                names, rows = _read_prefecture(member, data)
                missing = [c for c in COLUMNS if c not in names]
                if missing:
                    routes = any(
                        n.startswith(ROUTE_COLUMN_PREFIXES) for n in names
                    )
                    raise SourceFetchFailed(
                        "P11 DBF is missing expected columns; this is most likely a "
                        "different edition than 令和4年度"
                        if not routes else "P11 DBF is missing expected columns",
                        member=member, missing=missing, observed=names,
                    )
                index = {c: names.index(c) for c in COLUMNS}
                for seq, row in enumerate(rows):
                    records.append(
                        {
                            # Publisher record order within the prefecture. A
                            # surrogate, and deliberately not an entity identity:
                            # see the module docstring.
                            "p11_stop_id": stop_id(pref, seq),
                            "pref_code": pref,
                            **{c: row[index[c]] for c in COLUMNS},
                        }
                    )

        if not records:
            raise SourceFetchFailed("no P11 bus stops parsed")
        stops = pl.DataFrame(
            records,
            schema={
                "p11_stop_id": pl.Utf8, "pref_code": pl.Utf8,
                **{c: pl.Utf8 for c in COLUMNS},
            },
        ).rename(RENAME).sort("p11_stop_id")

        blank = stops.filter(pl.col("stop_name_raw") == "").height
        if blank:
            raise SourceFetchFailed("P11 bus stops carry a blank 名称", rows=blank)
        log.info(
            "parsed P11 bus stops",
            stops=stops.height,
            prefectures=stops["pref_code"].n_unique(),
            operators=int(stops["operator_name_raw"].n_unique()),
            distinct_names=int(stops["stop_name_raw"].n_unique()),
            # Recorded because it is the reason this table has a surrogate key.
            name_operator_pairs=stops.select(
                "stop_name_raw", "operator_name_raw"
            ).unique().height,
        )
        return {"p11_bus_stop": stops}

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
