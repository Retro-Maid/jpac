"""MIC 市外局番の一覧 adapter.

Publishes the only official machine-obtainable statement of which areas each
番号区画 covers. The document is Word 97, extracted by
``sources/doc_reader.py``.

The clause parser here is the heart of the telephone model. Official text such
as 「北海道夕張市（富野を除く。）」 says that part of a municipality is in this
numbering area and part is not. V1 records that fact at municipality level with
the clause verbatim, and refuses to guess which 町字 fall on which side —
because the source does not say (docs/MATCHING_RULES.md T3).
"""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl

from ..errors import SourceFetchFailed
from ..logging_setup import get_logger, stage_context
from ..payload import FetchResult
from ..snapshot import SchemaInfo, SourceSnapshot, make_snapshot_id, utcnow
from .base import BaseSource
from .doc_reader import doc_text_to_rows, extract_doc_text

log = get_logger(__name__)

PREFECTURES = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]

# Parentheses nest in this document, so the group is found by depth rather
# than by a regex that stops at the first closing bracket. See _first_paren_span.
_PAREN_OPEN = "（("
_PAREN_CLOSE = "）)"
_MUNI_SUFFIX = re.compile(r"(市|区|町|村)$")
_COUNTY_RE = re.compile(r"^(.+?郡)(.*)$")
_CURRENT_AS_OF = re.compile(r"（?(令和|平成)([０-９0-9元]+)年([０-９0-9]+)月([０-９0-9]+)日現在）?")


def normalize_area_code(raw: str) -> str:
    """Return the conventional 市外局番 form, with its leading zero.

    MIC publishes the same code two ways: 市外局番の一覧 (Word) writes ``11`` /
    ``123`` / ``3``, while 電気通信番号指定状況 (XLS) writes ``011`` / ``0123`` /
    ``03``. Verified nationally: without normalizing, **0 of 387** area codes in
    ``telephone_area`` join ``telephone_number_block`` — the two halves of the
    telephone crosswalk never meet.

    The XLS form is the conventional one users expect, so the Word form is
    normalized to it and the published string is kept as ``area_code_raw``.
    """
    raw = (raw or "").strip()
    if not raw.isdigit():
        return raw
    return raw if raw.startswith("0") else "0" + raw


def normalize_numbering_area_code(raw: str) -> str:
    """``4-2`` -> ``004-2``; ``1`` -> ``001``.

    The Word document writes these unpadded while the XLS zero-pads them, so a
    single normalized form is required for the two to join at all.
    """
    raw = raw.strip()
    if not raw:
        return ""
    if "-" in raw:
        head, tail = raw.split("-", 1)
        return f"{int(head):03d}-{tail}" if head.isdigit() else raw
    return f"{int(raw):03d}" if raw.isdigit() else raw


class MicAreaCodeSource(BaseSource):
    name = "mic_area_code"
    provider = "総務省"
    required = True



    def inspect(self, fetched: dict[str, FetchResult]) -> dict[str, SchemaInfo]:
        with stage_context(self.name, "inspect"):
            rows = self._rows(fetched["shigai_list"].path)
            header = rows[0] if rows else []
            return {
                "shigai_list": SchemaInfo(
                    columns=[c or f"col{i}" for i, c in enumerate(header)],
                    column_count=len(header), encoding="utf-16/cp1252 (Word 97)",
                    delimiter="\\x07", has_header=True, container="none",
                    members=["WordDocument", "1Table"],
                    row_count=max(0, len(rows) - 1),
                )
            }

    @staticmethod
    def _rows(path: Path) -> list[list[str]]:
        text = extract_doc_text(path)
        rows = doc_text_to_rows(text, expected_cells=4)
        if len(rows) < 100:
            raise SourceFetchFailed(
                "implausibly few rows extracted from the MIC Word document; "
                "the publisher may have changed the format",
                rows=len(rows), path=str(path),
            )
        return rows

    def parse(self, fetched: dict[str, FetchResult]) -> dict[str, pl.DataFrame]:
        with stage_context(self.name, "parse"):
            path = fetched["shigai_list"].path
            text = extract_doc_text(path)
            current_as_of = _extract_current_as_of(text)
            rows = doc_text_to_rows(text, expected_cells=4)

            areas: list[dict] = []
            coverage: list[dict] = []
            for cells in rows[1:]:          # row 0 is the header
                code_raw, area_text, area_code, digits = cells
                code = normalize_numbering_area_code(code_raw)
                if not code or not area_code.isdigit():
                    continue
                areas.append(
                    {
                        "numbering_area_code": code,
                        "area_code": normalize_area_code(area_code),
                        "area_code_raw": area_code,
                        "area_text_raw": area_text,
                        "local_digit_pattern": digits,
                        "current_as_of": current_as_of,
                    }
                )
                coverage.extend(parse_area_text(code, area_text))

            area_df = pl.DataFrame(areas, schema={
                "numbering_area_code": pl.Utf8, "area_code": pl.Utf8,
                "area_code_raw": pl.Utf8, "area_text_raw": pl.Utf8,
                "local_digit_pattern": pl.Utf8, "current_as_of": pl.Utf8,
            }).sort("numbering_area_code")

            cov_df = pl.DataFrame(coverage, schema={
                "numbering_area_code": pl.Utf8, "clause_raw": pl.Utf8,
                "pref_name": pl.Utf8, "county_name": pl.Utf8,
                "municipality_name": pl.Utf8, "sub_municipal_text": pl.Utf8,
                "qualifier": pl.Utf8, "coverage_type": pl.Utf8,
                "exception_text": pl.Utf8, "parse_rule": pl.Utf8,
            }).sort(["numbering_area_code", "clause_raw"])

            log.info(
                "parsed MIC area code list",
                areas=area_df.height, coverage_clauses=cov_df.height,
                partial=cov_df.filter(pl.col("coverage_type") == "partial").height,
                unresolved=cov_df.filter(pl.col("coverage_type") == "unresolved").height,
            )
            return {"telephone_area": area_df, "telephone_area_coverage": cov_df}

    def build_snapshots(self, discovery, fetched, schemas, row_counts):
        res = discovery.resources[0]
        fr = fetched[res.key]
        self._snapshots = [
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
        ]
        return self._snapshots


def _extract_current_as_of(text: str) -> str:
    m = _CURRENT_AS_OF.search(text)
    if not m:
        return ""
    era, y, mo, d = m.groups()
    trans = str.maketrans("０１２３４５６７８９", "0123456789")
    y = 1 if y == "元" else int(y.translate(trans))
    mo = int(mo.translate(trans))
    d = int(d.translate(trans))
    base = 2018 if era == "令和" else 1988
    return f"{base + y:04d}-{mo:02d}-{d:02d}"


def _first_paren_span(text: str) -> tuple[int, int] | None:
    """Span of the first parenthesis group, honouring nesting.

    「上北郡（東北町（旭北、…に限る。）、七戸町及び六戸町に限る。）」 puts a group
    inside a group. A regex that stops at the first 「）」 cut the clause in half
    and the tail — 「、七戸町及び六戸町に限る。」 — was stored as a municipality
    name. Twenty-two clauses were mangled that way, including the ones covering
    京都市 and 喜多方市.
    """
    start = -1
    depth = 0
    for i, ch in enumerate(text):
        if ch in _PAREN_OPEN:
            if depth == 0:
                start = i
            depth += 1
        elif ch in _PAREN_CLOSE and depth:
            depth -= 1
            if depth == 0:
                return start, i + 1
    return None


def _split_county(rest_wo: str) -> tuple[str | None, str]:
    """Separate 郡 from municipality, without inventing counties.

    A 郡 contains only 町 and 村, never 市. Taking everything up to the first
    「郡」 turned 「蒲郡市」「小郡市」「大和郡山市」 into county 蒲郡 / 小郡 /
    大和郡 plus a municipality called 「市」「山市」.
    """
    m = _COUNTY_RE.match(rest_wo)
    if not m:
        return None, rest_wo
    county, remainder = m.group(1), m.group(2)
    if not remainder:
        return county, ""
    if remainder.endswith(("町", "村")):
        return county, remainder
    return None, rest_wo


def _split_top_level(text: str) -> list[str]:
    """Split on 、 but not inside parentheses.

    「樺戸郡（浦臼町及び新十津川町に限る。）」 is one clause, not three.
    """
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in text:
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth = max(0, depth - 1)
        if ch == "、" and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def parse_area_text(code: str, area_text: str) -> list[dict]:
    """Parse the official 対象地域 prose into coverage clauses.

    Conservative by construction: anything not confidently understood becomes
    ``coverage_type='unresolved'`` with the text preserved, rather than a guess
    (docs/POLICY.md §4).
    """
    out: list[dict] = []
    current_pref: str | None = None

    for clause in _split_top_level(area_text):
        rest = clause
        for pref in PREFECTURES:
            if rest.startswith(pref):
                current_pref = pref
                rest = rest[len(pref):]
                break

        span = _first_paren_span(rest)
        qualifier = "none"
        exception_text: str | None = None
        inner: str | None = None
        if span:
            inner = rest[span[0] + 1 : span[1] - 1]
            exception_text = rest[span[0] : span[1]]
            # The qualifier is the one that closes the *outer* group; a nested
            # 「…に限る。」 inside an exclusion describes what is excluded.
            tail = inner[max(0, len(inner) - 12):]
            if "除く" in tail:
                qualifier = "exclude"
            elif "限る" in tail:
                qualifier = "limit"
            elif "除く" in inner:
                qualifier = "exclude"
            elif "限る" in inner:
                qualifier = "limit"
            rest_wo = (rest[: span[0]] + rest[span[1] :]).strip()
        else:
            rest_wo = rest.strip()

        county, muni = _split_county(rest_wo)

        base = {
            "numbering_area_code": code,
            "clause_raw": clause,
            "pref_name": current_pref,
            "county_name": county,
            "municipality_name": muni or None,
            "sub_municipal_text": None,
            "qualifier": qualifier,
            "exception_text": exception_text,
        }

        if qualifier == "none":
            if muni and _MUNI_SUFFIX.search(muni):
                out.append({**base, "coverage_type": "full", "parse_rule": "T1"})
            elif county and not muni:
                # A whole 郡: expanded to its municipalities downstream.
                out.append({**base, "coverage_type": "full", "parse_rule": "T5"})
            elif muni:
                # Text like 「夕張市富野」 — a place *inside* a municipality.
                out.append(
                    {
                        **base,
                        "municipality_name": None,
                        "sub_municipal_text": rest_wo,
                        "coverage_type": "partial",
                        "parse_rule": "T3",
                    }
                )
            else:
                out.append({**base, "coverage_type": "unresolved", "parse_rule": "T7"})
            continue

        if qualifier == "limit" and inner and county and not muni:
            # 「樺戸郡（浦臼町及び新十津川町に限る。）」 — named whole municipalities.
            named = _split_named_municipalities(inner)
            if named:
                for name in named:
                    out.append(
                        {
                            **base,
                            "municipality_name": name,
                            "coverage_type": "full",
                            "parse_rule": "T4",
                        }
                    )
                continue

            # 「沙流郡（平取町及び日高町（栄町西、…に限る。）に限る。）」 — a mix:
            # some members whole, one qualified. Reading only the whole ones and
            # dropping the rest lost 平取町 entirely, so each member is emitted
            # with its own scope.
            members = _split_county_members(inner)
            if members:
                for name, member_inner in members:
                    if member_inner is None:
                        out.append(
                            {
                                **base,
                                "municipality_name": name,
                                "coverage_type": "full",
                                "parse_rule": "T4",
                            }
                        )
                    else:
                        out.append(
                            {
                                **base,
                                "municipality_name": name,
                                "sub_municipal_text": member_inner,
                                "qualifier": (
                                    "exclude" if "除く" in member_inner else "limit"
                                ),
                                "exception_text": f"（{member_inner}）",
                                "coverage_type": "partial",
                                "parse_rule": "T3",
                            }
                        )
                continue

        # Anything else qualified names sub-municipal places. Municipality-level
        # partial coverage only; never expanded to 町字 (docs/MATCHING_RULES.md T3).
        out.append(
            {
                **base,
                "sub_municipal_text": inner,
                "coverage_type": "partial",
                "parse_rule": "T3",
            }
        )

    return out


def _split_named_municipalities(inner: str) -> list[str]:
    body = inner.replace("に限る。", "").replace("に限る", "")
    names = re.split(r"及び|、", body)
    cleaned = [n.strip() for n in names if n.strip()]
    return cleaned if all(_MUNI_SUFFIX.search(n) for n in cleaned) and cleaned else []


def _split_county_members(inner: str) -> list[tuple[str, str | None]]:
    """Members of a 郡 named inside 「…に限る。」, each with its own qualifier.

    Returns ``(municipality_name, inner_text_or_None)``. Every item must be a
    municipality name, optionally followed by its own parenthesised qualifier;
    otherwise the clause is not understood and nothing is returned.
    """
    body = re.sub(r"に限る。?\s*$", "", inner.strip())
    out: list[tuple[str, str | None]] = []
    for chunk in _split_top_level_connectives(body):
        span = _first_paren_span(chunk)
        if span is None:
            name, member_inner = chunk.strip(), None
        else:
            name = chunk[: span[0]].strip()
            member_inner = chunk[span[0] + 1 : span[1] - 1]
        if not name or not _MUNI_SUFFIX.search(name):
            return []
        out.append((name, member_inner))
    return out


def _split_top_level_connectives(text: str) -> list[str]:
    """Split on 、 and 及び at depth zero."""
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in _PAREN_OPEN:
            depth += 1
        elif ch in _PAREN_CLOSE:
            depth = max(0, depth - 1)
        if depth == 0:
            if ch == "、":
                parts.append("".join(buf))
                buf = []
                i += 1
                continue
            if text.startswith("及び", i):
                parts.append("".join(buf))
                buf = []
                i += 2
                continue
        buf.append(ch)
        i += 1
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]
