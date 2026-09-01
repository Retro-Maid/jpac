"""Human-attested name resolution for a publisher whose prose is out of date.

MIC's 市外局番の一覧 still writes 「篠山市」 seven years after the municipality
renamed itself 丹波篠山市. MIC is not wrong about the substance — area 441 really
does cover that municipality — only about the string naming it. So what is needed
is not an override of the source's statement but a resolution of one name, and
the two are worth keeping apart.

Each alias states its own `preconditions`, which are measured against the current
build rather than assumed. When the publisher corrects its text the alias stops
matching its precondition and lapses, reported rather than silently reapplied
(`overrides/manual_overrides.yml` sets that expectation for the whole project).
An alias that lapses costs coverage; it can never produce a wrong mapping.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl
import yaml

from ..logging_setup import get_logger
from ..normalize import normalize_conservative

log = get_logger(__name__)


@dataclass(frozen=True)
class NameAlias:
    id: str
    source: str
    pref: str
    published_name: str
    lg_code: str
    attested_by: str
    evidence_url: str


def load_name_aliases(
    path: Path,
    municipality: pl.DataFrame,
    source_names: dict[str, set[tuple[str, str]]],
) -> tuple[dict[tuple[str, str], NameAlias], list[dict]]:
    """Return ``{(pref, normalized published name): alias}`` and the stale ones.

    ``source_names`` maps a dataset name to the ``(pref, municipality name)``
    pairs its parser produced from the publisher's current text, so
    ``source_still_contains`` is checked against what the source says today.

    It must be the parsed names and not the raw text: 「篠山市」 is a substring of
    「丹波篠山市」, so a containment test on the document would still pass after
    MIC corrected it, and the alias could never lapse.
    """
    if not path.exists():
        return {}, []
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    names_by_lg = {
        r["lg_code"]: (r.get("city") or "") + (r.get("ward") or "")
        for r in municipality.iter_rows(named=True)
    }
    all_names = set(names_by_lg.values())

    applied: dict[tuple[str, str], NameAlias] = {}
    stale: list[dict] = []

    for a in doc.get("aliases") or []:
        pre = a.get("preconditions") or {}
        failures = []

        lg = str(a.get("lg_code") or "")
        want_name = pre.get("abr_lg_code_current_name")
        if want_name is not None and names_by_lg.get(lg) != want_name:
            failures.append(
                f"abr_lg_code_current_name: {lg} is "
                f"{names_by_lg.get(lg)!r}, expected {want_name!r}"
            )

        forbidden = pre.get("abr_has_no_municipality_named")
        if forbidden is not None and forbidden in all_names:
            failures.append(
                f"abr_has_no_municipality_named: {forbidden!r} now exists"
            )

        needle = pre.get("source_still_contains")
        if needle is not None:
            names = source_names.get(str(a.get("source")), set())
            if (str(a.get("pref") or ""), str(needle)) not in names:
                failures.append(
                    f"source_still_contains: {a.get('source')} no longer names "
                    f"{needle!r} in {a.get('pref')} — the publisher has "
                    "corrected it"
                )

        if failures:
            stale.append({"id": a.get("id"), "reasons": failures})
            continue

        key = (
            normalize_conservative(str(a.get("pref") or "")),
            normalize_conservative(str(a.get("published_name") or "")),
        )
        applied[key] = NameAlias(
            id=str(a.get("id")),
            source=str(a.get("source")),
            pref=str(a.get("pref")),
            published_name=str(a.get("published_name")),
            lg_code=lg,
            attested_by=str(a.get("attested_by") or ""),
            evidence_url=str(a.get("evidence_url") or ""),
        )

    if stale:
        log.warning(
            "attested source-name aliases lapsed and were NOT applied",
            count=len(stale), detail=stale,
        )
    log.info("loaded source-name aliases", applied=len(applied), stale=len(stale))
    return applied, stale
