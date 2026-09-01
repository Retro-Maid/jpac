"""source_snapshot construction, schema fingerprints, and drift detection.

Provenance is mandatory (spec §25): every row in every table cites a snapshot.
"""

from __future__ import annotations

import codecs
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import PARSER_VERSION
from .errors import LicenseReviewRequired, SourceSchemaChanged
from .logging_setup import get_logger

log = get_logger(__name__)


def utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class SourceSnapshot:
    source_snapshot_id: str
    provider: str
    dataset_name: str
    source_page_url: str
    download_url: str
    license_name: str | None = None
    license_url: str | None = None
    license_text_sha256: str | None = None
    source_version: str | None = None
    published_at: str | None = None
    downloaded_at: str = ""
    etag: str | None = None
    last_modified: str | None = None
    sha256: str = ""
    file_size: int = 0
    row_count: int | None = None
    schema_fingerprint: str = ""
    parser_version: str = PARSER_VERSION
    # "discovery" or "recorded_fallback": whether the publisher's own page still
    # advertised this file, or the build fell back to a URL recorded earlier.
    resolved_via: str = "discovery"
    status: str = "ok"

    def as_dict(self) -> dict:
        return asdict(self)


def make_snapshot_id(dataset_name: str, sha256: str) -> str:
    """Deterministic id: same dataset + same bytes always yields the same id.

    Using a content hash rather than a counter keeps a rebuild in a different
    order byte-identical (docs/ARCHITECTURE.md §6).
    """
    token = hashlib.blake2s(
        f"{dataset_name}|{sha256}".encode(), digest_size=8
    ).hexdigest()
    return f"snap_{dataset_name}_{token}"


# --------------------------------------------------------------- fingerprints

_DIGITS = re.compile(r"\d")


def normalize_member_name(name: str) -> str:
    """``01000-19.0b/01_2025.csv`` -> ``NNNNN-NN.Nb/NN_NNNN.csv``.

    A yearly file-name roll should not look like a structural change, while a
    genuine change of members still does.
    """
    return _DIGITS.sub("N", name)


@dataclass
class SchemaInfo:
    columns: list[str] = field(default_factory=list)
    column_count: int = 0
    encoding: str = ""
    delimiter: str = ","
    has_header: bool = True
    sheet_names: list[str] = field(default_factory=list)
    container: str = "none"
    members: list[str] = field(default_factory=list)
    # Counted at inspect time so source_snapshot.row_count is populated for
    # every resource, not only for single-file sources (spec §25).
    row_count: int | None = None

    def fingerprint(self) -> str:
        # row_count is deliberately absent: it changes every month and would
        # make the drift gate fire on ordinary data updates.
        payload = {
            "columns": list(self.columns),
            "count": self.column_count,
            "encoding": self.encoding,
            "delimiter": self.delimiter,
            "has_header": self.has_header,
            "sheet_names": list(self.sheet_names),
            "container": self.container,
            "members": [normalize_member_name(m) for m in self.members],
        }
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict:
        d = asdict(self)
        d["members"] = [normalize_member_name(m) for m in self.members]
        # row_count is data volume, not structure; including it would make every
        # month look like a schema change.
        d.pop("row_count", None)
        d["fingerprint"] = self.fingerprint()
        return d


def check_schema_drift(
    source: str, observed: SchemaInfo, expected_path: Path, allow_create: bool = False
) -> None:
    """Fail closed on any unreviewed schema change (spec §34).

    ``allow_create`` is used **only** by ``jpac baseline``. A build that could
    write its own baseline would let a new, renamed, malformed or truncated
    resource install itself as trusted and reach a release — the exact failure
    this gate exists to prevent.
    """
    import yaml

    if not expected_path.exists():
        if not allow_create:
            raise SourceSchemaChanged(
                "no reviewed expected-schema baseline for this resource; run "
                "`jpac baseline`, review the result, and commit it",
                source=source, expected_path=str(expected_path),
            )
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        expected_path.write_text(
            yaml.safe_dump(observed.as_dict(), allow_unicode=True, sort_keys=True),
            encoding="utf-8",
        )
        log.warning(
            "expected-schema baseline created; review and commit it",
            source=source,
            path=str(expected_path),
        )
        return

    expected = yaml.safe_load(expected_path.read_text(encoding="utf-8")) or {}
    if expected.get("fingerprint") == observed.fingerprint():
        return

    exp_cols = list(expected.get("columns") or [])
    obs_cols = list(observed.columns)
    added = [c for c in obs_cols if c not in exp_cols]
    removed = [c for c in exp_cols if c not in obs_cols]
    reordered = (
        not added and not removed and exp_cols != obs_cols
    )
    raise SourceSchemaChanged(
        "source schema changed; review and update the expected schema before building",
        source=source,
        added=added,
        removed=removed,
        reordered=reordered,
        expected_fingerprint=expected.get("fingerprint"),
        observed_fingerprint=observed.fingerprint(),
    )


# ------------------------------------------------------------- license drift

_SCRIPT_STYLE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def license_text_hash(html: str) -> str:
    """Hash the readable body of a terms page.

    Scripts, markup and whitespace are stripped so a site redesign does not
    produce a false positive, while a change to the terms body does
    (docs/LICENSE_POLICY.md §4).
    """
    text = _SCRIPT_STYLE.sub(" ", html)
    text = _TAGS.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# A terms page can only be hashed meaningfully once it is decoded correctly.
# Three of the four publishers serve ``text/html`` with no charset parameter, so
# a client that falls back to ISO-8859-1 ends up hashing mojibake. That still
# detects change — the bytes are deterministic — but it is weak evidence of what
# the terms actually say, which is what a redistributed provenance record has to
# carry, and a publisher merely adding a charset header would fire a false
# LICENSE_REVIEW_REQUIRED. Resolution follows the authority of each declaration:
# a BOM outranks the transport header, which outranks the document's own
# declaration (docs/LICENSE_POLICY.md §4).

_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)
_CT_CHARSET = re.compile(r"""charset\s*=\s*["']?([A-Za-z0-9_.:-]+)""", re.I)
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.I)
_XML_ENCODING = re.compile(rb"""<\?xml[^>]+encoding\s*=\s*["']([A-Za-z0-9_.:-]+)["']""", re.I)

# Japanese pages label the same codec several ways, and cp932 is the superset
# actually served under the name "Shift_JIS".
_ENCODING_ALIASES = {
    "shift_jis": "cp932", "shift-jis": "cp932", "sjis": "cp932",
    "x-sjis": "cp932", "windows-31j": "cp932", "ms_kanji": "cp932",
    "euc-jp": "euc_jp", "eucjp": "euc_jp", "x-euc-jp": "euc_jp",
    "utf8": "utf-8",
}

DEFAULT_HTML_ENCODING = "utf-8"


def _canonical_encoding(name: str | None) -> str | None:
    if not name:
        return None
    key = name.strip().strip("\"'").lower()
    return _ENCODING_ALIASES.get(key, key) or None


def resolve_html_encoding(raw: bytes, content_type: str | None = None) -> str:
    """Decide how to decode a terms page, declaration by declaration.

    Deliberately does *not* sniff by trial decoding: a page that happens to
    decode cleanly under two codecs would then hash differently depending on the
    order they were tried, and the drift gate needs one answer per input.
    """
    for bom, enc in _BOMS:
        if raw.startswith(bom):
            return enc
    header = _canonical_encoding(
        m.group(1) if (m := _CT_CHARSET.search(content_type or "")) else None
    )
    if header:
        return header
    head = raw[:4096]
    for pattern in (_XML_ENCODING, _META_CHARSET):
        if m := pattern.search(head):
            declared = _canonical_encoding(m.group(1).decode("ascii", "replace"))
            if declared:
                return declared
    return DEFAULT_HTML_ENCODING


def license_text_hash_bytes(raw: bytes, content_type: str | None = None) -> str:
    """``license_text_hash`` over correctly-decoded bytes.

    Kept separate from ``license_text_hash`` rather than replacing it: the
    committed baselines in ``config/sources.yml`` were recorded against the
    text a mis-decoding client produced, and switching the acquisition side to
    this function invalidates them all at once. That is a coordinated
    re-baseline, not a silent change (docs/LICENSE_POLICY.md §4).
    """
    encoding = resolve_html_encoding(raw, content_type)
    return license_text_hash(raw.decode(encoding, errors="replace"))


def check_license_drift(
    source: str,
    observed_name: str | None,
    observed_url: str | None,
    observed_hash: str | None,
    recorded: dict,
) -> None:
    """Stop the release on any licence change (spec §33)."""
    rec_hash = recorded.get("text_sha256")
    if rec_hash is None:
        log.warning(
            "license baseline recorded for the first time; review and commit it",
            source=source,
            sha256=observed_hash,
        )
        return

    changes = {}
    if recorded.get("name") != observed_name:
        changes["name"] = (recorded.get("name"), observed_name)
    if recorded.get("url") != observed_url:
        changes["url"] = (recorded.get("url"), observed_url)
    if rec_hash != observed_hash:
        changes["text_sha256"] = (rec_hash, observed_hash)

    if changes:
        raise LicenseReviewRequired(
            "terms of use changed; a human must review before any release",
            source=source,
            changes=changes,
        )


_NEWLINE = bytes([10])


def count_csv_rows(raw: bytes, has_header: bool) -> int:
    """Data rows in a CSV payload. Cheap: newline counting, no parsing."""
    n = raw.count(_NEWLINE)
    if raw and not raw.endswith(_NEWLINE):
        n += 1
    return max(0, n - (1 if has_header else 0))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
