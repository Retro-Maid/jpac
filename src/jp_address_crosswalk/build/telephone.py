"""Telephone crosswalk (docs/MATCHING_RULES.md §7).

A numbering area is not a municipality — MIC's own text splits 夕張市 across
areas 003 and 004-2. Coverage is therefore retained at the municipality level,
which is the granularity the source states. The address bridge contains one T10
unresolved row per 町字 so no address disappears, but it never turns a
municipality-level statement into a town-level assignment (docs/POLICY.md §4).
"""

from __future__ import annotations

import hashlib

import polars as pl

from ..logging_setup import get_logger, stage_context
from ..normalize import normalize_conservative
from .common import BuildContext, bridge_id, candidate_group_id, finalize_bridge
from .source_aliases import NameAlias

log = get_logger(__name__)


def prepare_telephone(
    area: pl.DataFrame, coverage: pl.DataFrame, snapshot_id: str, observed_from: str
) -> dict[str, pl.DataFrame]:
    with stage_context("mic", "normalize"):
        entity = area.select(
            [
                "numbering_area_code",
                pl.lit(snapshot_id).alias("first_observed_snapshot_id"),
            ]
        ).unique(subset=["numbering_area_code"], keep="first").sort("numbering_area_code")

        version = area.with_columns(
            [
                pl.format("tav_{}_{}", pl.col("numbering_area_code"), pl.lit(observed_from))
                .alias("telephone_area_version_id"),
                pl.lit(observed_from).alias("observed_from"),
                pl.lit(None, dtype=pl.Utf8).alias("observed_to"),
                pl.lit(True).alias("is_current"),
                pl.lit(snapshot_id).alias("source_snapshot_id"),
            ]
        ).select(
            ["telephone_area_version_id", "numbering_area_code", "area_code",
             "area_code_raw", "area_text_raw", "local_digit_pattern", "current_as_of",
             "observed_from", "observed_to", "is_current", "source_snapshot_id"]
        ).sort("numbering_area_code")

        # The id must cover everything that distinguishes one clause row from
        # another. Keying it on (area, clause_raw) alone collided whenever one
        # clause named several municipalities — 「上北郡（東北町、野辺地町、
        # 横浜町及び六ヶ所村に限る。）」 is one clause_raw and four rows — so the
        # dedup below kept whichever row came first and dropped the rest. 140
        # rows disappeared that way and 122 郡部の町村 ended up with no numbering
        # area at all. Silently, because nothing compared the parser's output
        # with what was stored.
        id_fields = [
            "numbering_area_code", "clause_raw", "pref_name", "county_name",
            "municipality_name", "sub_municipal_text", "qualifier",
            "exception_text", "coverage_type", "parse_rule",
        ]
        present = [c for c in id_fields if c in coverage.columns]
        cov = coverage.with_columns(
            [
                pl.concat_str(
                    [pl.col(c).fill_null("\x00") for c in present], separator="\x1f"
                )
                .map_elements(
                    lambda s: "cov_"
                    + hashlib.blake2s(s.encode("utf-8"), digest_size=12).hexdigest(),
                    return_dtype=pl.Utf8,
                )
                .alias("coverage_id"),
                pl.lit(snapshot_id).alias("source_snapshot_id"),
            ]
        )

        # Only byte-identical rows may collapse. Anything else is data loss, and
        # picking a survivor by row order is exactly what docs/POLICY.md §4 forbids.
        deduped = cov.unique(subset=["coverage_id"], keep="first").sort(
            ["numbering_area_code", "clause_raw", "coverage_id"]
        )
        exact_dupes = cov.height - cov.unique(subset=present).height
        if cov.height - deduped.height != exact_dupes:
            raise ValueError(
                "telephone_area_coverage: coverage_id collides across rows that "
                f"differ ({cov.height - deduped.height} dropped, "
                f"{exact_dupes} identical)"
            )
        cov = deduped

        log.info(
            "prepared telephone areas",
            areas=entity.height, coverage=cov.height,
            parsed=coverage.height, identical_rows_collapsed=exact_dupes,
            partial=cov.filter(pl.col("coverage_type") == "partial").height,
        )
        return {
            "telephone_area": entity,
            "telephone_area_version": version,
            "telephone_area_coverage": cov,
        }


def _municipality_index(municipality: pl.DataFrame) -> dict[tuple[str, str], list[str]]:
    """(pref, municipality-name) -> lg_codes, using conservative normalization.

    Several spellings of the same municipality are indexed (bare city, city+ward,
    county+city) because MIC's prose uses all of them. A name mapping to several
    lg_codes is ambiguous and never auto-resolved.
    """
    index: dict[tuple[str, str], list[str]] = {}
    for r in municipality.iter_rows(named=True):
        pref = normalize_conservative(r.get("pref") or "")
        city, ward, county = r.get("city"), r.get("ward"), r.get("county")
        names = []
        if city:
            names.append(city)
        if city and ward:
            names.append(city + ward)
        if ward:
            names.append(ward)
        if county and city:
            names.append(county + city)
        for name in names:
            index.setdefault((pref, normalize_conservative(name)), []).append(
                r["lg_code"]
            )
    return {k: sorted(set(v)) for k, v in index.items()}


def _designated_city_index(municipality: pl.DataFrame) -> dict[tuple[str, str], list[str]]:
    """(pref, city) -> lg_codes of every ward, for 政令指定都市 only.

    ABR keys a designated city's towns by **ward** (``011011`` = 札幌市中央区), so a
    bare 「札幌市」 in MIC's prose matches ten lg_codes. Treating that as ambiguous
    is wrong: the source means the whole city, and a city is exactly the union of
    its wards. Without this, every 政令指定都市 — twenty cities and a large share
    of the population — came out with no telephone coverage at all.

    This is a set identity, not an inference: it fires only when every candidate
    is a ward of the same city.
    """
    by_city: dict[tuple[str, str], list[str]] = {}
    has_ward: set[tuple[str, str]] = set()
    for r in municipality.iter_rows(named=True):
        city = r.get("city")
        if not city:
            continue
        key = (
            normalize_conservative(r.get("pref") or ""),
            normalize_conservative(city),
        )
        # The umbrella row (ward = NULL) belongs to the city too. Excluding it
        # made the ward set differ from the candidate set, so the whole check
        # silently never fired.
        by_city.setdefault(key, []).append(r["lg_code"])
        if r.get("ward"):
            has_ward.add(key)
    return {k: sorted(set(v)) for k, v in by_city.items() if k in has_ward}


def _special_wards(municipality: pl.DataFrame) -> list[str]:
    """The 23 特別区, which MIC names collectively as 「東京都23区」.

    That is an official collective term, not an abbreviation to be guessed at:
    it denotes exactly the 特別区 of 東京都, which ABR carries as municipalities
    in their own right. Reading it as a municipality name called 「23区」 matched
    nothing, and every one of Tokyo's 3,172 町字 came out with no numbering area.

    The count is asserted rather than assumed — if ABR ever stops listing 23,
    the term no longer means what this code thinks it means.
    """
    codes = [
        r["lg_code"]
        for r in municipality.iter_rows(named=True)
        if (r.get("pref") or "") == "東京都"
        and not r.get("county")
        and not r.get("ward")
        and (r.get("city") or "").endswith("区")
    ]
    return sorted(set(codes))


def _kana_fold(name: str) -> str:
    """Fold ヶ/ケ/ヵ/カ for **municipality** names only.

    `docs/MATCHING_RULES.md` §8 refuses this fold for 町字, and rightly: two
    towns can differ by exactly that character. A municipality name is a closed
    official list of 1,918, so the fold can be applied and then *checked* for
    uniqueness within the prefecture, which is what makes it a rule rather than
    a guess. MIC writes 袖ヶ浦市 / 鎌ヶ谷市 / 龍ヶ崎市 where ABR writes ケ.
    """
    return name.replace("ヶ", "ケ").replace("ヵ", "カ").replace("ガ", "カ")


def _folded_index(
    index: dict[tuple[str, str], list[str]],
) -> dict[tuple[str, str], list[str]]:
    """Folded name -> lg_codes, keeping only names that stay unique when folded."""
    folded: dict[tuple[str, str], set[str]] = {}
    for (pref, name), codes in index.items():
        folded.setdefault((pref, _kana_fold(name)), set()).update(codes)
    return {k: sorted(v) for k, v in folded.items()}


def _county_index(municipality: pl.DataFrame) -> dict[tuple[str, str], list[str]]:
    """(pref, 郡名) -> lg_codes of every municipality in that 郡.

    MIC writes bare 郡 names (「仁多郡」「浅口郡」) meaning the whole 郡. Rule T5
    resolves those to their member municipalities; without this they would all
    fall through to T7 and the coverage would look unknown when the source is
    perfectly clear (docs/MATCHING_RULES.md T5).
    """
    index: dict[tuple[str, str], list[str]] = {}
    for r in municipality.iter_rows(named=True):
        county = r.get("county")
        if not county:
            continue
        key = (
            normalize_conservative(r.get("pref") or ""),
            normalize_conservative(county),
        )
        index.setdefault(key, []).append(r["lg_code"])
    return {k: sorted(set(v)) for k, v in index.items()}


def _resolve_by_longest_prefix(
    text: str, pref: str, index: dict[tuple[str, str], list[str]]
) -> list[str]:
    """Longest official municipality name that ``text`` starts with.

    MIC names sub-municipal places as 「夕張市富野」 or 「たつの市御津町」 — a
    municipality name followed by a place inside it. Recovering the municipality
    is deterministic (longest exact prefix against official names) and keeps the
    statement at municipality level, which is the only level the source supports.
    Nothing here descends to 町字.
    """
    return _longest_prefix(text, pref, index)[0]


def _longest_prefix(
    text: str, pref: str, index: dict[tuple[str, str], list[str]]
) -> tuple[list[str], str]:
    """As above, but also return the unconsumed sub-municipal wording."""
    normalized = normalize_conservative(text)
    best: list[str] = []
    best_len = 0
    for (p, name), codes in index.items():
        if p != pref or len(name) <= best_len:
            continue
        if normalized.startswith(name):
            best, best_len = codes, len(name)
    return best, normalized[best_len:]


def build_telephone_bridges(
    address: pl.DataFrame,
    municipality: pl.DataFrame,
    coverage: pl.DataFrame,
    ctx: BuildContext,
    name_aliases: dict[tuple[str, str], NameAlias] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Return ``(municipality_bridge, address_bridge)``."""
    with stage_context("mic", "bridge"):
        aliases = name_aliases or {}
        index = _municipality_index(municipality)
        counties = _county_index(municipality)
        designated = _designated_city_index(municipality)
        folded = _folded_index(index)
        # The collective term is only honoured when ABR really does carry the 23
        # 特別区. A fixture build holds a handful of them, and there the clause
        # must fall through to T7 rather than expand to whatever is present.
        special_wards = _special_wards(municipality)
        if len(special_wards) != 23:
            log.warning(
                "「東京都23区」 not expanded: ABR does not carry exactly 23 特別区",
                found=len(special_wards),
            )
            special_wards = []

        muni_rows: list[dict] = []

        for r in coverage.iter_rows(named=True):
            code = r["numbering_area_code"]
            pref = normalize_conservative(r.get("pref_name") or "")
            name = r.get("municipality_name")
            county = r.get("county_name")
            ctype = r["coverage_type"]
            rule = r["parse_rule"]

            candidates: list[str] = []
            name_rule: str | None = None
            alias_used: NameAlias | None = None
            if name:
                candidates = index.get((pref, normalize_conservative(name)), [])
                if not candidates and county:
                    candidates = index.get(
                        (pref, normalize_conservative(county + name)), []
                    )
                if not candidates and pref == "東京都" and name.strip() == "23区":
                    candidates = list(special_wards)
                    name_rule = "T1c"
                if not candidates:
                    # ヶ/ケ, checked for uniqueness after folding.
                    hit = folded.get((pref, _kana_fold(normalize_conservative(name))), [])
                    if len(hit) == 1:
                        candidates = hit
                        name_rule = "T1b"
                if not candidates:
                    # A name the publisher has not updated. Attested by a human
                    # with evidence, and only after its preconditions were
                    # measured against this build
                    # (overrides/source_name_aliases.yml).
                    al = aliases.get((pref, normalize_conservative(name)))
                    if al:
                        candidates = [al.lg_code]
                        name_rule = "T1d"
                        alias_used = al

            # T5: a bare 郡 means every municipality in it.
            if not candidates and county and not name:
                members = counties.get((pref, normalize_conservative(county)), [])
                if members:
                    for lg in members:
                        muni_rows.append(
                            {
                                "bridge_id": bridge_id(
                                    "bridge_municipality_telephone", lg, code, "T5"
                                ),
                                "lg_code": lg, "target_id": code,
                                "direction": "telephone_to_municipality",
                                "relation_type": "child",
                                "match_method": "official_area_rule",
                                "matching_rule_id": "T5", "confidence": 0.95,
                                "candidate_group_id": None, "candidate_count": 1,
                                "coverage_type": "full",
                                "derivation": "expanded_from_county",
                                "mismatch_note": r.get("clause_raw"),
                            }
                        )
                    continue

            # T3: a sub-municipal place. Recover the municipality it sits in and
            # record partial coverage there.
            if not candidates and r.get("sub_municipal_text"):
                candidates, _tail = _longest_prefix(
                    r["sub_municipal_text"], pref, index
                )
                if candidates:
                    ctype = "partial"

            # 「鹿屋市輝北町」「久遠郡せたな町大成区」 — the clause carries no
            # qualifier, so the parser read the whole string as a municipality
            # name. It is a municipality followed by a place inside it, which
            # only the official municipality list can reveal. Thirty-five
            # clauses looked like whole municipalities and covered nothing.
            if not candidates and name:
                candidates, tail = _longest_prefix(name, pref, index)
                if candidates and tail:
                    ctype = "partial"

            if not candidates:
                # T7 — the clause could not be resolved to a municipality. The
                # official wording is kept so a human can act on it.
                muni_rows.append(
                    {
                        "bridge_id": bridge_id(
                            "bridge_municipality_telephone", None, code, r["coverage_id"]
                        ),
                        "lg_code": None, "target_id": code,
                        "direction": "telephone_to_municipality",
                        "relation_type": "unresolved", "match_method": "unresolved",
                        "matching_rule_id": "T7", "confidence": 0.0,
                        "candidate_group_id": None, "candidate_count": 0,
                        "coverage_type": "unresolved", "derivation": None,
                        "mismatch_note": r.get("clause_raw"),
                    }
                )
                continue

            # A bare designated-city name resolves to all of its wards. That is
            # the city, not a set of competing candidates.
            if len(candidates) > 1 and name:
                wards = designated.get((pref, normalize_conservative(name)))
                # Fires only when every candidate is part of that one city, so a
                # genuinely ambiguous name still falls through to T6.
                if wards and set(candidates) <= set(wards):
                    for lg in wards:
                        muni_rows.append(
                            {
                                "bridge_id": bridge_id(
                                    "bridge_municipality_telephone", lg, code, "T1"
                                ),
                                "lg_code": lg, "target_id": code,
                                "direction": "telephone_to_municipality",
                                "relation_type": "child" if ctype == "full" else "overlap",
                                "match_method": "official_area_rule",
                                "matching_rule_id": "T1" if ctype == "full" else "T3",
                                "confidence": 0.99 if ctype == "full" else 0.70,
                                "candidate_group_id": None, "candidate_count": 1,
                                "coverage_type": ctype,
                                "derivation": "designated_city_wards",
                                "mismatch_note": r.get("exception_text"),
                            }
                        )
                    continue

            if len(candidates) > 1:
                # T6 — the name is genuinely ambiguous; keep every candidate.
                group = candidate_group_id("bridge_municipality_telephone", code, name)
                for lg in candidates:
                    muni_rows.append(
                        {
                            "bridge_id": bridge_id(
                                "bridge_municipality_telephone", lg, code, "T6"
                            ),
                            "lg_code": lg, "target_id": code,
                            "direction": "telephone_to_municipality",
                            "relation_type": "ambiguous",
                            "match_method": "official_area_rule",
                            "matching_rule_id": "T6", "confidence": 0.50,
                            "candidate_group_id": group,
                            "candidate_count": len(candidates),
                            "coverage_type": ctype, "derivation": None,
                            "mismatch_note": r.get("clause_raw"),
                        }
                    )
                continue

            lg = candidates[0]
            if ctype == "full":
                rid = name_rule or ("T4" if rule == "T4" else "T1")
                # Neither a folded name nor an attested alias is what the source
                # literally says, so neither gets T1's score.
                rel, conf = "child", {"T1b": 0.95, "T1d": 0.90}.get(rid, 0.99)
            else:
                # T3 — partial. Municipality level only, exception text preserved.
                rel, conf, rid = "overlap", 0.70, "T3"
            muni_rows.append(
                {
                    "bridge_id": bridge_id("bridge_municipality_telephone", lg, code, rid),
                    "lg_code": lg, "target_id": code,
                    "direction": "telephone_to_municipality",
                    "relation_type": rel, "match_method": "official_area_rule",
                    "matching_rule_id": rid, "confidence": conf,
                    "candidate_group_id": None, "candidate_count": 1,
                    "coverage_type": ctype,
                    "derivation": (
                        "attested_source_name_alias" if alias_used else None
                    ),
                    "mismatch_note": (
                        f"{alias_used.id}: 「{alias_used.published_name}」 -> "
                        f"{alias_used.lg_code} (attested {alias_used.attested_by}, "
                        f"{alias_used.evidence_url})"
                        if alias_used
                        else r.get("exception_text")
                    ),
                }
            )

        schema = {
            "bridge_id": pl.Utf8, "lg_code": pl.Utf8, "target_id": pl.Utf8,
            "direction": pl.Utf8, "relation_type": pl.Utf8, "match_method": pl.Utf8,
            "matching_rule_id": pl.Utf8, "confidence": pl.Float64,
            "candidate_group_id": pl.Utf8, "candidate_count": pl.Int64,
            "coverage_type": pl.Utf8, "derivation": pl.Utf8, "mismatch_note": pl.Utf8,
        }
        muni_df = pl.DataFrame(muni_rows, schema=schema)

        # A municipality in several numbering areas is a real fact (夕張市). It
        # must never look unique, so candidate_count reflects the true fan-out.
        if muni_df.height:
            counts = (
                muni_df.filter(pl.col("lg_code").is_not_null())
                .group_by("lg_code")
                .agg(pl.len().alias("n_areas"))
            )
            muni_df = muni_df.join(counts, on="lg_code", how="left").with_columns(
                [
                    pl.max_horizontal(
                        pl.col("candidate_count"), pl.col("n_areas").fill_null(1)
                    ).alias("candidate_count"),
                    pl.when(pl.col("n_areas") > 1)
                    .then(
                        pl.col("lg_code").map_elements(
                            lambda a: candidate_group_id(
                                "bridge_municipality_telephone", "lg", a
                            ),
                            return_dtype=pl.Utf8,
                        )
                    )
                    .otherwise(pl.col("candidate_group_id"))
                    .alias("candidate_group_id"),
                ]
            ).drop("n_areas")

        muni_bridge = finalize_bridge(muni_df, ctx, ["lg_code", "target_id"])

        # T10: MIC's document is a municipality/area statement. Keep one row per
        # address so unmatched rows remain visible, but never attach a numbering
        # area to a 町字. Municipality evidence remains queryable in
        # bridge_municipality_telephone.
        addr_rows = [
            {
                "bridge_id": bridge_id(
                    "bridge_address_telephone", aid, None, "T10"
                ),
                "address_id": aid, "target_id": None,
                "direction": "address_to_telephone",
                "relation_type": "unresolved", "match_method": "unresolved",
                "matching_rule_id": "T10", "confidence": 0.0,
                "candidate_group_id": None, "candidate_count": 0,
                "coverage_type": "municipality_only", "derivation": None,
                "mismatch_note": "official MIC evidence is retained only at "
                                 "municipality level; no town-level assignment",
            }
            for aid in sorted(address["address_id"].to_list())
        ]

        addr_schema = dict(schema)
        addr_schema.pop("lg_code")
        addr_schema["address_id"] = pl.Utf8
        addr_df = pl.DataFrame(addr_rows, schema=addr_schema)
        addr_bridge = finalize_bridge(addr_df, ctx, ["address_id", "target_id"])

        log.info(
            "built telephone bridges",
            municipality_rows=muni_bridge.height,
            address_rows=addr_bridge.height,
            town_assignments=0,
        )
        return muni_bridge, addr_bridge
