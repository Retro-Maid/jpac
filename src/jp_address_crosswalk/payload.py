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

import zipfile
from collections.abc import Iterator
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
