"""Payload handling: accepted local files and safe archive reading.

This module used to also acquire the payloads. Acquisition — discovery, HTTP,
licence re-hashing, promotion into ``data/raw`` — is managed internally and is
not part of this repository; see README. What remains is everything needed to
*read* a payload that is already on disk:

* ``FetchResult`` — an accepted file with its digest and size, the record the
  parsers and the provenance layer both take as input.
* Archive safety. Zip members are validated before extraction, because a
  publisher's archive is still untrusted input: an absolute path or a ``..``
  member would otherwise write outside the target directory.
* Magic-byte checks. A payload is validated by its leading bytes rather than by
  where it came from, so an HTML error page saved under a ``.zip`` name fails at
  the boundary instead of reaching a parser.
"""

from __future__ import annotations

import io
import struct
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .errors import SourceFetchFailed, UnsafeArchive
from .logging_setup import get_logger

log = get_logger(__name__)

ZIP_MAGIC = b"PK\x03\x04"
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

MAGIC_BY_KIND = {
    "zip": (ZIP_MAGIC,),
    "ole2": (OLE2_MAGIC,),
    # .xls and legacy .doc are both OLE2 compound documents.
    "xls": (OLE2_MAGIC,),
    "doc": (OLE2_MAGIC,),
}


@dataclass(frozen=True)
class ArchiveLimits:
    """Bounds an untrusted archive must respect before anything is extracted.

    A publisher's zip is still untrusted input. Without a ceiling on the
    uncompressed size, the member count and the compression ratio, a malformed
    or hostile archive can exhaust the machine before a parser ever sees a row.
    """

    max_archive_bytes: int = 512 * 1024 * 1024
    max_uncompressed_bytes: int = 2 * 1024 * 1024 * 1024
    max_archive_members: int = 200
    max_compression_ratio: int = 200


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    path: Path
    sha256: str
    size: int
    etag: str | None
    last_modified: str | None
    content_type: str | None


RETRY_STATUSES = frozenset({500, 502, 503, 504})

# Written into ``data/raw/<source>/`` by the internal acquisition side when it
# promotes a payload. Never a payload itself, so the rebuild must not try to
# parse it as one.
MANIFEST_NAME = "_payload.yml"


@dataclass(frozen=True)
class PayloadManifest:
    """Acquisition-side facts a payload cannot carry in its own bytes.

    A build here never touches the network, so it cannot observe when a file was
    downloaded, what URL it came from, or what the terms page said at the time.
    Those facts are real and belong in the provenance record — a redistributed
    release has to be able to evidence which terms text was in force — so the
    side that *did* observe them may state them alongside the payload.

    Absence is the normal case and is never an error: without a manifest the
    build records what it can prove and leaves the rest null, exactly as before.
    """

    license_name: str | None = None
    license_url: str | None = None
    license_text_sha256: str | None = None
    license_observed_at: str | None = None
    # Observed terms-text hashes keyed by the same role names config/sources.yml
    # uses: primary_terms, policy_page, download_stipulation, ...
    license_artifacts: dict[str, dict] = field(default_factory=dict)
    resources: dict[str, dict] = field(default_factory=dict)

    def resource(self, key: str) -> dict:
        value = self.resources.get(key)
        return value if isinstance(value, dict) else {}

    def observed_text_sha256(self, role: str) -> str | None:
        """Observed hash for one terms document, if the acquisition side saw it."""
        entry = self.license_artifacts.get(role)
        if isinstance(entry, dict) and entry.get("text_sha256"):
            return str(entry["text_sha256"])
        if role == "primary_terms":
            return self.license_text_sha256
        return None


def load_payload_manifest(src_dir: Path) -> PayloadManifest:
    """Read ``_payload.yml`` if the acquisition side left one."""
    path = src_dir / MANIFEST_NAME
    if not path.exists():
        return PayloadManifest()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    licence = data.get("license") or {}
    resources = data.get("resources") or {}
    artifacts = licence.get("artifacts") or {}
    manifest = PayloadManifest(
        license_name=licence.get("name"),
        license_url=licence.get("url"),
        license_text_sha256=licence.get("text_sha256"),
        license_observed_at=licence.get("observed_at"),
        license_artifacts=artifacts if isinstance(artifacts, dict) else {},
        resources=resources if isinstance(resources, dict) else {},
    )
    log.info(
        "payload manifest loaded", path=str(path),
        has_license_hash=manifest.license_text_sha256 is not None,
        resources=len(manifest.resources),
    )
    return manifest


def _is_unsafe_member(name: str) -> bool:
    if name.startswith(("/", "\\")):
        return True
    if ".." in Path(name.replace("\\", "/")).parts:
        return True
    # Windows drive letters and UNC paths.
    return len(name) > 1 and name[1] == ":"


def open_zip_safely(path: Path, limits: ArchiveLimits) -> zipfile.ZipFile:
    """Bound the archive on disk *before* handing it to zipfile.

    ``zipfile.ZipFile()`` parses the whole central directory on open, so member
    caps applied afterwards are already too late against a hostile directory
    with millions of entries. The on-disk size is therefore checked first.

    This bounds the exposure rather than removing it: a file under the cap can
    still declare many entries. Given the residual risk is a memory spike from a
    government HTTPS endpoint whose bytes are checksum-recorded, bounding is the
    proportionate answer — but it is a bound, not immunity, and saying otherwise
    would be wrong.
    """
    size = path.stat().st_size
    if size > limits.max_archive_bytes:
        raise UnsafeArchive("archive larger than the accepted size cap", size=size)
    return zipfile.ZipFile(path)


def open_zip_bytes_safely(data: bytes, limits: ArchiveLimits) -> zipfile.ZipFile:
    """``open_zip_safely`` for an archive that is itself a member of another.

    P11 ships one archive per prefecture inside a national archive, so the inner
    bytes never exist as a file on disk and the on-disk size check above has
    nothing to look at. The same bound is applied here to the decompressed
    member instead, so nesting does not quietly cost the archive-safety property
    that ``open_zip_safely`` exists to provide. ``safe_zip_members`` then applies
    unchanged to the result.
    """
    if len(data) > limits.max_archive_bytes:
        raise UnsafeArchive(
            "nested archive larger than the accepted size cap", size=len(data)
        )
    return zipfile.ZipFile(io.BytesIO(data))


def safe_zip_members(zf: zipfile.ZipFile, limits: ArchiveLimits) -> list[zipfile.ZipInfo]:
    """Validate an archive before anything is extracted (spec §62)."""
    infos = zf.infolist()
    if len(infos) > limits.max_archive_members:
        raise UnsafeArchive("too many members", count=len(infos))

    total_uncompressed = 0
    for info in infos:
        if _is_unsafe_member(info.filename):
            raise UnsafeArchive("unsafe member name", name=info.filename)
        total_uncompressed += info.file_size
        if info.file_size > limits.max_uncompressed_bytes:
            raise UnsafeArchive(
                "member too large", name=info.filename, size=info.file_size
            )
        if info.compress_size > 0:
            ratio = info.file_size / info.compress_size
            if ratio > limits.max_compression_ratio:
                raise UnsafeArchive(
                    "compression ratio too high", name=info.filename, ratio=round(ratio, 1)
                )
    if total_uncompressed > limits.max_uncompressed_bytes:
        raise UnsafeArchive("archive too large uncompressed", size=total_uncompressed)
    return infos


def read_zip_member(path: Path, member: str, limits: ArchiveLimits | None = None) -> bytes:
    limits = limits or ArchiveLimits()
    with open_zip_safely(path, limits) as zf:
        infos = safe_zip_members(zf, limits)
        names = [i.filename for i in infos]
        if member not in names:
            raise SourceFetchFailed("archive member missing", path=str(path), member=member)
        with zf.open(member) as fh:
            return fh.read()


def iter_zip_csv_members(path: Path, limits: ArchiveLimits | None = None) -> Iterator[tuple[str, bytes]]:
    limits = limits or ArchiveLimits()
    with open_zip_safely(path, limits) as zf:
        for info in safe_zip_members(zf, limits):
            if info.filename.lower().endswith(".csv"):
                with zf.open(info) as fh:
                    yield info.filename, fh.read()


# ------------------------------------------------------------------ shapefile
# Two sources ship their attribute table as a DBF inside a shapefile archive
# (e-Stat 小地域境界, 国土数値情報 N02). Only the .dbf is ever read here: geometry
# is a separate concern with a separate dependency, and neither source's
# attributes need it.
#
# DBF is parsed rather than pulled in as a dependency. The payload is level-5
# DBF with character and numeric fields only, the format has been frozen for
# decades, and the alternative is a new pinned runtime dependency for ~50 lines
# of struct unpacking — the same trade docs/ARCHITECTURE.md §5 records for the
# legacy MIC formats, decided the other way because this format is simpler and
# does not move.

DBF_FIELD_TERMINATOR = 0x0D
DBF_DELETED = b"*"


def read_dbf(data: bytes, encoding: str = "cp932") -> tuple[list[str], list[list[str]]]:
    """Field names and rows of a DBF table, every value as stripped text.

    Numeric fields are returned as text too. The codes in these payloads are
    zero-padded identifiers (``PREF`` ``01``, ``CITY`` ``101``, ``N02_005g``
    ``010112``) and reading them as numbers is exactly the ``013100`` →
    ``13100`` corruption README.md warns about; there is no caller here that
    wants arithmetic.
    """
    if len(data) < 32:
        raise SourceFetchFailed("DBF shorter than its own header", bytes=len(data))
    n_records = int.from_bytes(data[4:8], "little")
    header_len = int.from_bytes(data[8:10], "little")
    record_len = int.from_bytes(data[10:12], "little")
    if not header_len or not record_len:
        raise SourceFetchFailed(
            "DBF header declares no length", header_len=header_len, record_len=record_len
        )

    names: list[str] = []
    widths: list[int] = []
    pos = 32
    while pos + 32 <= header_len and data[pos] != DBF_FIELD_TERMINATOR:
        descriptor = data[pos : pos + 32]
        names.append(descriptor[:11].split(b"\x00")[0].decode("ascii", "replace").strip())
        widths.append(descriptor[16])
        pos += 32
    if not names:
        raise SourceFetchFailed("DBF declares no fields")
    # The header is self-describing, so disagreement means the file is not what
    # it claims and every offset below would be wrong. Fail rather than emit
    # plausible-looking garbage.
    if sum(widths) + 1 != record_len:
        raise SourceFetchFailed(
            "DBF field widths do not add up to the declared record length",
            declared=record_len, from_fields=sum(widths) + 1, fields=len(names),
        )

    rows: list[list[str]] = []
    offset = header_len
    for _ in range(n_records):
        record = data[offset : offset + record_len]
        offset += record_len
        if len(record) < record_len:
            break
        if record[0:1] == DBF_DELETED:
            continue
        values: list[str] = []
        cursor = 1
        for width in widths:
            values.append(record[cursor : cursor + width].decode(encoding, "replace").strip())
            cursor += width
        rows.append(values)
    return names, rows


def read_dbf_member(
    path: Path, suffix: str = ".dbf", limits: ArchiveLimits | None = None,
    encoding: str = "cp932", contains: str | Sequence[str] | None = None,
) -> tuple[list[str], list[list[str]]]:
    """Read the one DBF in a shapefile archive.

    ``contains`` narrows to a single member and may be several substrings, all
    of which must match. Both kinds of ambiguity are real in these payloads: N02
    ships ``_Station`` and ``_RailroadSection`` side by side, *and* ships a
    Shift-JIS and a UTF-8 copy of each — so the caller has to name the encoding
    directory as well as the table, or it can decode Shift-JIS bytes as UTF-8
    and get mojibake that still parses.

    Matching more than one member is an error rather than "take the first":
    picking silently is how one dataset's rows end up attributed to another.
    """
    limits = limits or ArchiveLimits()
    needles = (
        [] if contains is None
        else [contains] if isinstance(contains, str)
        else list(contains)
    )
    with open_zip_safely(path, limits) as zf:
        members = [
            m for m in safe_zip_members(zf, limits)
            if m.filename.lower().endswith(suffix)
            and all(n in m.filename for n in needles)
        ]
        if len(members) != 1:
            raise SourceFetchFailed(
                "expected exactly one matching DBF in the archive",
                path=str(path), contains=needles,
                found=[m.filename for m in members],
            )
        with zf.open(members[0]) as fh:
            return read_dbf(fh.read(), encoding)


# Shapefile geometry. Only the shape types these sources use are handled:
# Polygon (5) for the e-Stat 小地域 boundaries, PolyLine (3) for N02 stations and
# railway sections, and Point (1) for P11 bus stops.
#
# Parsed here for the same reason as the DBF above. The format is frozen, the
# subset needed is small, and the alternative is a pinned spatial dependency for
# the two record layouts below. The point-in-polygon logic that consumes this
# lives in build/spatial.py and is validated against an independent
# implementation (see tests/test_spatial.py).
SHP_POINT = 1
SHP_POLYLINE = 3
SHP_POLYGON = 5
_SHP_HEADER_BYTES = 100
_SHP_RECORD_HEADER_BYTES = 8


def read_shp_shapes(
    data: bytes, expect: int | None = None
) -> list[tuple[tuple[float, float, float, float], list[list[tuple[float, float]]]] | None]:
    """Bounding box and rings/parts of every record, in file order.

    Returns ``None`` for a null shape so the result stays index-aligned with the
    DBF: dropping them would silently shift every attribute onto the wrong
    geometry.

    Polygon and PolyLine share a record layout — bbox, part offsets, then a flat
    point array — so one reader covers both. Ring orientation is not normalised;
    the even-odd rule used downstream does not need it, and rewriting the
    publisher's rings would be an undeclared modification.

    Point is a *different* layout — just the two doubles, with no bbox and no
    part table — so it gets its own branch. It is returned in the same shape as
    the others, as a degenerate bbox and a single one-point part, so that every
    caller and the index-alignment rule above stay uniform rather than each
    consumer learning a second return type.
    """
    shapes: list[tuple[tuple[float, float, float, float], list[list[tuple[float, float]]]] | None] = []
    offset = _SHP_HEADER_BYTES
    total = len(data)
    while offset + _SHP_RECORD_HEADER_BYTES <= total:
        content_words = int.from_bytes(data[offset + 4 : offset + 8], "big")
        body = offset + _SHP_RECORD_HEADER_BYTES
        end = body + content_words * 2
        if end > total:
            raise SourceFetchFailed(
                "shapefile record runs past the end of the file",
                offset=offset, declared_end=end, size=total,
            )
        shape_type = int.from_bytes(data[body : body + 4], "little")
        if shape_type == 0:                      # null shape
            shapes.append(None)
            offset = end
            continue
        if expect is not None and shape_type != expect:
            raise SourceFetchFailed(
                "unexpected shapefile shape type", expected=expect, observed=shape_type
            )
        if shape_type == SHP_POINT:
            x, y = struct.unpack("<2d", data[body + 4 : body + 20])
            shapes.append(((x, y, x, y), [[(x, y)]]))
            offset = end
            continue
        box = struct.unpack("<4d", data[body + 4 : body + 36])
        n_parts = int.from_bytes(data[body + 36 : body + 40], "little")
        n_points = int.from_bytes(data[body + 40 : body + 44], "little")
        parts_at = body + 44
        points_at = parts_at + n_parts * 4
        starts = [
            int.from_bytes(data[parts_at + i * 4 : parts_at + i * 4 + 4], "little")
            for i in range(n_parts)
        ]
        coords = struct.unpack(f"<{n_points * 2}d", data[points_at : points_at + n_points * 16])
        rings: list[list[tuple[float, float]]] = []
        for i, start in enumerate(starts):
            stop = starts[i + 1] if i + 1 < n_parts else n_points
            rings.append(
                [(coords[j * 2], coords[j * 2 + 1]) for j in range(start, stop)]
            )
        shapes.append(((box[0], box[1], box[2], box[3]), rings))
        offset = end
    return shapes


def read_shp_member(
    path: Path, contains: str | Sequence[str] | None = None,
    expect: int | None = None, limits: ArchiveLimits | None = None,
) -> list[tuple[tuple[float, float, float, float], list[list[tuple[float, float]]]] | None]:
    """``read_shp_shapes`` for the one .shp in an archive. See ``read_dbf_member``."""
    limits = limits or ArchiveLimits()
    needles = (
        [] if contains is None
        else [contains] if isinstance(contains, str)
        else list(contains)
    )
    with open_zip_safely(path, limits) as zf:
        members = [
            m for m in safe_zip_members(zf, limits)
            if m.filename.lower().endswith(".shp")
            and all(n in m.filename for n in needles)
        ]
        if len(members) != 1:
            raise SourceFetchFailed(
                "expected exactly one matching .shp in the archive",
                path=str(path), contains=needles,
                found=[m.filename for m in members],
            )
        with zf.open(members[0]) as fh:
            return read_shp_shapes(fh.read(), expect)


def read_prj_member(
    path: Path, contains: str | Sequence[str] | None = None,
    limits: ArchiveLimits | None = None,
) -> str:
    """The .prj text, so a datum can be asserted rather than assumed.

    N02 and e-Stat are both JGD2011, and a join between two sources on different
    datums is off by metres in a way nothing downstream would report.
    """
    limits = limits or ArchiveLimits()
    needles = (
        [] if contains is None
        else [contains] if isinstance(contains, str)
        else list(contains)
    )
    with open_zip_safely(path, limits) as zf:
        members = [
            m for m in safe_zip_members(zf, limits)
            if m.filename.lower().endswith(".prj")
            and all(n in m.filename for n in needles)
        ]
        if len(members) != 1:
            raise SourceFetchFailed(
                "expected exactly one matching .prj in the archive",
                path=str(path), contains=needles,
                found=[m.filename for m in members],
            )
        with zf.open(members[0]) as fh:
            return fh.read().decode("ascii", "replace")
