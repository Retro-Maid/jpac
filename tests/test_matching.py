"""Matching rules and the ambiguity contract (docs/MATCHING_RULES.md, docs/POLICY.md §4).

These are the tests that matter most: they assert that the pipeline refuses to
guess. Several of them would pass trivially on a naive implementation that picks
a winner, so each one asserts the *absence* of a forced 1:1 as well.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
import yaml

from jp_address_crosswalk.build import telephone
from jp_address_crosswalk.build.common import (
    BuildContext,
    assert_bridge_invariants,
    bridge_id,
    finalize_bridge,
)
from jp_address_crosswalk.build.postal import (
    build_postal_record_bridge,
    classify_parenthetical,
)
from jp_address_crosswalk.build.quality import compare_with_previous
from jp_address_crosswalk.sources.mic_area_code import (
    normalize_numbering_area_code,
    parse_area_text,
)

CTX = BuildContext(
    match_run_id="run_test", snapshot_id="snap_test", observed_from="2026-08-23",
    built_at="2026-08-23T00:00:00Z", matching_rule_version="1.2.0",
    normalization_profile_version="1.0.0",
)


class TestApprovedRateMigration:
    @staticmethod
    def summaries(delta: float, old="1.1.0", new="1.2.0"):
        previous = {
            "matching_rule_version": old,
            "bridges": {
                "bridge_address_telephone": {
                    "rates": {
                        "unresolved_pct": 7.547,
                        "ambiguous_pct": 0.0,
                        "exact_or_equivalent_pct": 0.0,
                    }
                }
            },
        }
        current = {
            "matching_rule_version": new,
            "bridges": {
                "bridge_address_telephone": {
                    "rates": {
                        "unresolved_pct": 7.547 + delta,
                        "ambiguous_pct": 0.0,
                        "exact_or_equivalent_pct": 0.0,
                    }
                }
            },
        }
        return current, previous

    @staticmethod
    def thresholds():
        return yaml.safe_load(
            (Path(__file__).parents[1] / "config" / "quality_thresholds.yml")
            .read_text(encoding="utf-8")
        )

    def test_exact_reviewed_transition_passes_with_audit_marker(self):
        current, previous = self.summaries(92.453)
        rows = compare_with_previous(current, previous, self.thresholds())
        row = next(
            r for r in rows
            if r["check"] == "unresolved_pct.bridge_address_telephone"
        )
        assert row["status"] == "pass"
        assert row["approved_migration"] is True

    @pytest.mark.parametrize(
        ("delta", "old", "new"),
        [(92.0, "1.1.0", "1.2.0"), (92.453, "1.2.0", "1.2.0")],
    )
    def test_different_delta_or_version_still_fails(self, delta, old, new):
        current, previous = self.summaries(delta, old, new)
        rows = compare_with_previous(current, previous, self.thresholds())
        row = next(
            r for r in rows
            if r["check"] == "unresolved_pct.bridge_address_telephone"
        )
        assert row["status"] == "fail"


# ゲートの検査に使うブリッジ。端点列名はレジストリが決めるので、実在する名前を使う
# （docs/BRIDGE_ENDPOINT_MIGRATION.md A1）。
BRIDGE = "bridge_address_postal"


def make_bridge(**over) -> pl.DataFrame:
    row = {
        "bridge_id": bridge_id("t", "a", "b"), "address_id": "jpa1aaaaaaaaaaaaaaaa",
        "postal_record_id": "pr_t1", "direction": "address_to_postal",
        "relation_type": "exact",
        "match_method": "normalized_name", "matching_rule_id": "P4",
        "confidence": 0.97, "candidate_group_id": None, "candidate_count": 1,
        "mismatch_note": None,
    }
    row.update(over)
    return pl.DataFrame([row], schema={
        "bridge_id": pl.Utf8, "address_id": pl.Utf8, "postal_record_id": pl.Utf8,
        "direction": pl.Utf8, "relation_type": pl.Utf8, "match_method": pl.Utf8,
        "matching_rule_id": pl.Utf8, "confidence": pl.Float64,
        "candidate_group_id": pl.Utf8, "candidate_count": pl.Int64,
        "mismatch_note": pl.Utf8,
    })


class TestAutoAcceptGate:
    def test_clean_exact_match_is_auto(self):
        out = finalize_bridge(
            make_bridge(confidence=0.99), CTX, ["bridge_id"], BRIDGE
        )
        assert out["verification_status"][0] == "auto"

    def test_high_confidence_never_beats_multiple_candidates(self):
        """The gate is a conjunction: a 1.0 score cannot carry an ambiguous row."""
        out = finalize_bridge(
            make_bridge(confidence=1.0, candidate_count=3, candidate_group_id="g1"),
            CTX, ["bridge_id"], BRIDGE,
        )
        assert out["verification_status"][0] == "review_required"
        assert not out["is_unique_match"][0]

    @pytest.mark.parametrize("relation", ["parent", "child", "overlap", "candidate", "ambiguous"])
    def test_non_equivalent_relations_never_auto(self, relation):
        out = finalize_bridge(
            make_bridge(confidence=1.0, relation_type=relation), CTX, ["bridge_id"],
            BRIDGE,
        )
        assert out["verification_status"][0] == "review_required"

    def test_below_threshold_never_auto(self):
        out = finalize_bridge(
            make_bridge(confidence=0.97), CTX, ["bridge_id"], BRIDGE
        )
        assert out["verification_status"][0] == "review_required"

    def test_stale_override_never_auto(self):
        df = make_bridge(confidence=1.0).with_columns(pl.lit(True).alias("override_stale"))
        assert finalize_bridge(df, CTX, ["bridge_id"], BRIDGE)["verification_status"][0] == "review_required"

    def test_incomplete_candidate_set_never_auto(self):
        df = make_bridge(confidence=1.0).with_columns(
            pl.lit(False).alias("candidate_count_is_complete")
        )
        assert finalize_bridge(df, CTX, ["bridge_id"], BRIDGE)["verification_status"][0] == "review_required"


class TestDeterminism:
    def test_bridge_id_is_content_addressed(self):
        assert bridge_id("b", "x", "y") == bridge_id("b", "x", "y")
        assert bridge_id("b", "x", "y") != bridge_id("b", "y", "x")

    def test_null_and_empty_are_distinguishable(self):
        """A missing endpoint is not the same fact as an empty one."""
        assert bridge_id("b", None, "y") != bridge_id("b", "", "y")


class TestParentheticalClassification:
    @pytest.mark.parametrize("town", ["西新宿", "旭ケ丘", None, ""])
    def test_no_parenthetical(self, town):
        assert classify_parenthetical(town)[0] == "none"

    @pytest.mark.parametrize(
        "town",
        ["丸の内（次のビルを除く）", "大手町（１階）"],
    )
    def test_non_geographic_allows_exact(self, town):
        assert classify_parenthetical(town)[0] == "non_geographic"

    @pytest.mark.parametrize(
        "town",
        ["霞が関（１丁目）", "本町（１〜５番地）", "中央（その他）", "港南（無番地）"],
    )
    def test_geographic_blocks_exact(self, town):
        """A partial-coverage record must not be able to claim `exact`."""
        assert classify_parenthetical(town)[0] == "geographic"


class TestPostalRecordBridge:
    """The 西新宿 case and its N:1 mirror."""

    @staticmethod
    def addresses(*names):
        return pl.DataFrame(
            [
                {"address_id": f"jpa1{i:016d}", "jis_city_code": "13104",
                 "full_name_normalized": n, "oaza_cho": n.replace("1丁目", "")
                 .replace("2丁目", "").replace("3丁目", "")}
                for i, n in enumerate(names)
            ],
            schema={"address_id": pl.Utf8, "jis_city_code": pl.Utf8,
                    "full_name_normalized": pl.Utf8, "oaza_cho": pl.Utf8},
        )

    @staticmethod
    def postal(*rows):
        return pl.DataFrame(
            [
                {"postal_record_id": f"pr_{i}", "jis_city_code": "13104",
                 "town_normalized": t, "parenthetical_class": pc,
                 "town_raw": t, "record_kind": "town"}
                for i, (t, pc) in enumerate(rows)
            ],
            schema={"postal_record_id": pl.Utf8, "jis_city_code": pl.Utf8,
                    "town_normalized": pl.Utf8, "parenthetical_class": pl.Utf8,
                    "town_raw": pl.Utf8, "record_kind": pl.Utf8},
        )

    def test_one_to_n_keeps_every_chome(self):
        """Japan Post 西新宿 must not collapse onto one chome (spec §15)."""
        addr = self.addresses("西新宿1丁目", "西新宿2丁目", "西新宿3丁目")
        post = self.postal(("西新宿", "none"))
        out = build_postal_record_bridge(addr, post, set(), CTX)
        p5 = out.filter(pl.col("matching_rule_id") == "P5")
        assert p5.height == 3, "all three chome must be retained"
        assert set(p5["relation_type"]) == {"parent"}
        assert p5["candidate_count"].min() >= 2
        assert p5["candidate_group_id"].n_unique() == 1
        assert (p5["verification_status"] == "auto").sum() == 0

    def test_n_to_one_becomes_p6b_not_unresolved(self):
        """Two postal records naming one town: keep both, pick neither."""
        addr = self.addresses("本町")
        post = self.postal(("本町", "none"), ("本町", "none"))
        out = build_postal_record_bridge(addr, post, set(), CTX)
        p6b = out.filter(pl.col("matching_rule_id") == "P6b")
        assert p6b.height == 2
        assert set(p6b["relation_type"]) == {"ambiguous"}
        assert p6b["candidate_group_id"].n_unique() == 1
        assert (out["matching_rule_id"] == "P4").sum() == 0

    def test_unique_both_ways_is_exact_and_auto(self):
        addr = self.addresses("本町")
        post = self.postal(("本町", "none"))
        out = build_postal_record_bridge(addr, post, set(), CTX)
        p4 = out.filter(pl.col("matching_rule_id") == "P4")
        assert p4.height == 1
        assert p4["relation_type"][0] == "exact"

    def test_geographic_parenthetical_downgrades_to_overlap(self):
        addr = self.addresses("本町")
        post = self.postal(("本町", "geographic"))
        out = build_postal_record_bridge(addr, post, set(), CTX)
        row = out.filter(pl.col("matching_rule_id") == "P4p")
        assert row.height == 1
        assert row["relation_type"][0] == "overlap"
        assert row["verification_status"][0] == "review_required"

    def test_unmatched_postal_record_is_retained(self):
        """No silent deletion to improve a match rate (docs/POLICY.md §5)."""
        addr = self.addresses("本町")
        post = self.postal(("存在しない町", "none"))
        out = build_postal_record_bridge(addr, post, set(), CTX)
        orphan = out.filter(
            (pl.col("relation_type") == "unresolved") & pl.col("address_id").is_null()
        )
        assert orphan.height == 1
        assert orphan["postal_record_id"][0] is not None

    def test_unmatched_address_is_retained(self):
        addr = self.addresses("本町")
        post = self.postal(("別の町", "none"))
        out = build_postal_record_bridge(addr, post, set(), CTX)
        orphan = out.filter(
            (pl.col("relation_type") == "unresolved")
            & pl.col("postal_record_id").is_null()
        )
        assert orphan.height == 1

    def test_invariants_hold(self):
        addr = self.addresses("西新宿1丁目", "西新宿2丁目")
        post = self.postal(("西新宿", "none"), ("孤児町", "none"))
        out = build_postal_record_bridge(addr, post, set(), CTX)
        assert assert_bridge_invariants(out, "bridge_address_postal") == []


class TestNumberingAreaCode:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("1", "001"), ("4-2", "004-2"), ("11", "011"), ("011", "011"),
         ("660", "660"), ("8-2", "008-2")],
    )
    def test_normalizes_to_the_xls_form(self, raw, expected):
        """The Word doc writes 4-2 and the XLS writes 004-2; they must join."""
        assert normalize_numbering_area_code(raw) == expected


class TestAreaTextParsing:
    """Verified against the real MIC wording (docs/DATA_SOURCES.md §4.1)."""

    def test_plain_municipalities_are_full(self):
        out = parse_area_text("001", "北海道江別市、札幌市、北広島市、空知郡南幌町")
        assert len(out) == 4
        assert {c["coverage_type"] for c in out} == {"full"}
        assert out[0]["pref_name"] == "北海道"
        assert out[3]["county_name"] == "空知郡"

    def test_exclusion_clause_is_partial_and_keeps_the_wording(self):
        out = parse_area_text("003", "北海道夕張市（富野を除く。）")
        assert len(out) == 1
        assert out[0]["coverage_type"] == "partial"
        assert out[0]["qualifier"] == "exclude"
        assert "富野を除く" in out[0]["exception_text"]

    def test_sub_municipal_place_is_partial_not_a_municipality(self):
        """夕張市富野 is a place inside a city, not a city."""
        out = parse_area_text("004-2", "北海道夕張市富野、夕張郡")
        first = next(c for c in out if "富野" in c["clause_raw"])
        assert first["coverage_type"] == "partial"
        assert first["municipality_name"] is None

    def test_limit_clause_naming_whole_municipalities_expands(self):
        out = parse_area_text("007", "北海道樺戸郡（浦臼町及び新十津川町に限る。）")
        names = {c["municipality_name"] for c in out}
        assert names == {"浦臼町", "新十津川町"}
        assert {c["coverage_type"] for c in out} == {"full"}
        assert {c["parse_rule"] for c in out} == {"T4"}

    def test_commas_inside_parentheses_do_not_split_the_clause(self):
        out = parse_area_text(
            "008-2", "北海道岩見沢市（宝水町を除く。）、美唄市、石狩郡新篠津村"
        )
        assert len(out) == 3
        assert out[0]["coverage_type"] == "partial"
        assert out[1]["municipality_name"] == "美唄市"

    def test_prefecture_carries_across_clauses(self):
        out = parse_area_text("001", "北海道江別市、札幌市")
        assert all(c["pref_name"] == "北海道" for c in out)

    def test_outer_qualifier_wins_over_a_nested_one(self):
        """「（…を除く。）に限る。」 is a limit. A fixed-width tail saw 除く."""
        out = parse_area_text(
            "385-2", "奈良県高市郡（明日香村及び高取町（越智、車木、寺崎及び丹生谷を除く。）に限る。）"
        )
        by_name = {c["municipality_name"]: c for c in out}
        assert set(by_name) == {"明日香村", "高取町"}
        assert by_name["明日香村"]["coverage_type"] == "full"
        assert by_name["高取町"]["coverage_type"] == "partial"

    def test_dialing_note_is_not_an_exclusion(self):
        """福岡の4条文: the group says how to dial, not what land is covered."""
        out = parse_area_text(
            "577",
            "福岡県糸島市（市外局番を除く電気通信番号による発信については、"
            "番号区画コード578の番号区画を含む。）",
        )
        assert len(out) == 1
        assert out[0]["municipality_name"] == "糸島市"
        assert out[0]["coverage_type"] == "full"
        assert out[0]["qualifier"] == "none"
        assert "番号区画コード578" in out[0]["exception_text"]

    def test_county_exclusion_keeps_the_excluded_names(self):
        out = parse_area_text("250", "千葉県印旛郡（酒々井町を除く。）")
        assert len(out) == 1
        assert out[0]["county_name"] == "印旛郡"
        assert out[0]["municipality_name"] is None
        assert out[0]["qualifier"] == "exclude"


class TestCountyExclusion:
    """「〇〇郡（…を除く。）」 is not a bare 郡 (docs/MATCHING_RULES.md T5).

    Expanding it whole asserted coverage the source explicitly denies — 41
    clauses nationally, 309-2 carrying 大治町 against 「海部郡（大治町を除く。）」.
    """

    MUNI = pl.DataFrame(
        [
            ("122041", "千葉県", None, "船橋市", None),
            ("123226", "千葉県", "印旛郡", "酒々井町", None),
            ("123293", "千葉県", "印旛郡", "栄町", None),
            ("454044", "宮崎県", "児湯郡", "木城町", None),
            ("454052", "宮崎県", "児湯郡", "川南町", None),
            ("393444", "高知県", "長岡郡", "大豊町", None),
            ("393410", "高知県", "長岡郡", "本山町", None),
        ],
        schema=["lg_code", "pref", "county", "city", "ward"],
        orient="row",
    )

    @classmethod
    def bridge(cls, code, text):
        cov = pl.DataFrame(parse_area_text(code, text))
        area = pl.DataFrame(
            [(code, "0", "0", text, "DE", "2026-03-01")],
            schema=["numbering_area_code", "area_code", "area_code_raw",
                    "area_text_raw", "local_digit_pattern", "current_as_of"],
            orient="row",
        )
        tables = telephone.prepare_telephone(area, cov, "snap_test", "2026-08-23")
        addr = pl.DataFrame({"address_id": ["a1"]})
        muni, _ = telephone.build_telephone_bridges(
            addr, cls.MUNI, tables["telephone_area_coverage"], CTX
        )
        return {
            r["lg_code"]: (r["coverage_type"], r["matching_rule_id"])
            for r in muni.iter_rows(named=True)
        }

    def test_named_member_is_left_out(self):
        got = self.bridge("250", "千葉県印旛郡（酒々井町を除く。）")
        assert got == {"123293": ("full", "T5")}

    def test_member_losing_a_place_is_partial_not_dropped(self):
        got = self.bridge("639", "宮崎県児湯郡（木城町中之又を除く。）")
        assert got == {"454044": ("partial", "T3"), "454052": ("full", "T5")}

    def test_member_with_its_own_group_is_partial(self):
        """The common real shape: 長岡郡, 網走郡, 田村郡, 伊都郡, 西伯郡, 玉名郡…"""
        got = self.bridge("558", "高知県長岡郡（大豊町（馬瀬、角茂谷、久寿軒及び戸手野に限る。）を除く。）")
        assert got == {"393444": ("partial", "T3"), "393410": ("full", "T5")}

    def test_limit_the_parser_could_not_split_is_unresolved(self):
        """「…に限る。」 reaching T5 must not expand whole — the same defect inverted."""
        got = self.bridge("250", "千葉県印旛郡（酒々井町中川に限る。）")
        assert got == {None: ("unresolved", "T7")}

    def test_unreadable_exclusion_is_unresolved_not_expanded(self):
        """A name outside the 郡 must not be dropped silently — that widens the area."""
        got = self.bridge("250", "千葉県印旛郡（船橋市を除く。）")
        assert got == {None: ("unresolved", "T7")}

    def test_bare_county_still_expands(self):
        got = self.bridge("250", "千葉県印旛郡")
        assert got == {"123226": ("full", "T5"), "123293": ("full", "T5")}
