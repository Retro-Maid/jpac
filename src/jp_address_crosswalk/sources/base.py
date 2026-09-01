"""Common Source adapter interface (docs/ARCHITECTURE.md §3).

``discover()`` is split from ``fetch()`` so the monthly workflow can answer
"did anything change?" without downloading ~200 MB (spec §37).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import polars as pl

from ..payload import FetchResult
from ..snapshot import SchemaInfo, SourceSnapshot


@dataclass
class DiscoveredResource:
    """One downloadable file, resolved from the publisher's landing page."""

    key: str
    url: str
    dataset_name: str
    expect: str | None = None          # magic-byte kind: zip | xls | doc
    member: str | None = None
    encoding: str = "utf-8"
    version: str | None = None
    published_at: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    # How this URL was obtained. A recorded fallback means the publisher's own
    # page no longer advertises the file, which is a signal worth auditing even
    # when the bytes turn out to be correct.
    resolved_via: str = "discovery"


@dataclass
class Discovery:
    """Result of a discovery pass. Cheap: metadata only, no payload."""

    source: str
    source_page_url: str
    resources: list[DiscoveredResource] = field(default_factory=list)
    license_name: str | None = None
    license_url: str | None = None
    license_text_sha256: str | None = None
    version: str | None = None

    def fallback_resources(self) -> list[str]:
        return [r.key for r in self.resources if r.resolved_via != "discovery"]

    def change_signature(self) -> str:
        """Stable string summarising 'what the publisher is currently offering'.

        Compared against the previous run to decide whether a build is needed
        at all.
        """
        parts = [self.source, self.version or ""]
        for r in sorted(self.resources, key=lambda r: r.key):
            parts.append(
                "|".join(
                    [r.key, r.url, r.version or "", r.etag or "", r.last_modified or ""]
                )
            )
        return "\n".join(parts)


class Source(Protocol):
    name: str
    provider: str
    required: bool

    def inspect(self, fetched: dict[str, FetchResult]) -> dict[str, SchemaInfo]: ...
    def parse(self, fetched: dict[str, FetchResult]) -> dict[str, pl.DataFrame]: ...
    def snapshots(self) -> list[SourceSnapshot]: ...


class BaseSource:
    """Shared plumbing. Subclasses implement inspect/parse/build_snapshots.

    Acquisition is not part of this repository (see README), so an adapter here
    only ever reads a payload that is already in ``data/raw``.
    """

    name: str = "base"
    provider: str = ""
    required: bool = True

    def __init__(self, config: dict) -> None:
        self.config = config
        self._snapshots: list[SourceSnapshot] = []

    def snapshots(self) -> list[SourceSnapshot]:
        return list(self._snapshots)
