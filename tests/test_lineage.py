"""境界コードの読み替え (src/jp_address_crosswalk/build/lineage.py).

固定したいのは4つの場合分けと、**分割を1つに決めないこと**である。旧浜松市北区は
中央区と浜名区に分かれており、どちらかを選ぶのは POLICY.md §4 が欠陥と呼ぶ
「誤った 1:1 の捏造」にあたる。
"""

from __future__ import annotations

from jp_address_crosswalk.build.lineage import (
    lineage_note,
    resolve_candidates,
    via_lineage_method,
)

# 実データと同じ形。22131〜22134/22136/22137 は 1:1、22135（旧北区）だけが分割。
LG_BY_JIS = {"13101": "131016", "22100": "221007"}
SUCCESSORS = {
    "22131": ["221384"],              # 旧中区 → 中央区
    "22137": ["221406"],              # 旧天竜区 → 天竜区（コードのみ改定）
    "22135": ["221384", "221392"],    # 旧北区 → 中央区・浜名区（分割）
}


class TestResolveCandidates:
    def test_a_current_code_is_returned_as_is(self) -> None:
        assert resolve_candidates("13101", LG_BY_JIS, SUCCESSORS) == [("131016", False)]

    def test_a_current_code_is_never_overridden_by_lineage(self) -> None:
        """現行にあるコードは、系譜に載っていても読み替えない。"""
        lg_by_jis = {**LG_BY_JIS, "22131": "221317"}
        assert resolve_candidates("22131", lg_by_jis, SUCCESSORS) == [("221317", False)]

    def test_a_one_to_one_transition_resolves(self) -> None:
        assert resolve_candidates("22137", LG_BY_JIS, SUCCESSORS) == [("221406", True)]

    def test_a_split_keeps_every_successor(self) -> None:
        """これが本体。旧北区は2候補のまま。"""
        got = resolve_candidates("22135", LG_BY_JIS, SUCCESSORS)
        assert got == [("221384", True), ("221392", True)]

    def test_an_unknown_code_keeps_a_null_candidate(self) -> None:
        """行を消さない。「コードが無い」と「対応が無い」は別の主張。"""
        assert resolve_candidates("99999", LG_BY_JIS, SUCCESSORS) == [(None, False)]

    def test_works_without_a_lineage_at_all(self) -> None:
        assert resolve_candidates("22135", LG_BY_JIS) == [(None, False)]
        assert resolve_candidates("13101", LG_BY_JIS, None) == [("131016", False)]

    def test_the_order_is_deterministic(self) -> None:
        """バイト再現性がこれに依存する。入力の順序に影響されない。"""
        shuffled = {"22135": ["221392", "221384"]}
        assert resolve_candidates("22135", LG_BY_JIS, shuffled) == [
            ("221384", True), ("221392", True),
        ]

    def test_duplicate_successors_collapse(self) -> None:
        assert resolve_candidates("22135", LG_BY_JIS, {"22135": ["221384", "221384"]}) == [
            ("221384", True)
        ]


class TestLineageNote:
    """注記は**分割のときだけ**付ける。

    1:1 の承継に注記を付けると、出荷テーブルの CHECK
    「auto なら mismatch_note は NULL」に抵触する（export/writers.py の
    STATION_BRIDGE_CHECKS）。1:1 の経路は match_method が持つ。
    """

    def test_it_names_both_codes_so_the_path_is_traceable(self) -> None:
        note = lineage_note("22135", "221384")
        assert "22135" in note and "221384" in note
        assert "municipality_lineage.yml" in note

    def test_it_says_it_is_only_one_candidate(self) -> None:
        note = lineage_note("22135", "221384")
        assert "候補の1つ" in note
        assert "POLICY.md" in note


class TestViaLineageMethod:
    def test_it_extends_the_method_rather_than_replacing_it(self) -> None:
        """語彙を増やしているのであって、別物に置き換えてはいない。"""
        assert via_lineage_method("spatial_containment") == "spatial_containment_via_lineage"
        assert via_lineage_method("spatial_sampling") == "spatial_sampling_via_lineage"

    def test_the_spelling_matches_the_shipped_ddl(self) -> None:
        """docs/schema.sql と export/writers.py の CHECK に載っている綴りであること。

        ここがずれると、ビルドは parquet まで通って SQLite の書き込みで落ちる。
        実際に一度そうなった。
        """
        from pathlib import Path

        ddl = Path("docs/schema.sql").read_text(encoding="utf-8")
        for base in ("spatial_containment", "spatial_sampling"):
            assert via_lineage_method(base) in ddl

    def test_the_spelling_matches_the_checks_the_build_actually_writes(self) -> None:
        """``docs/schema.sql`` ではなく、出荷時に効く方の CHECK も見ておく。

        落ちるのは SQLite の書き込みであり、その CHECK は ``export/writers.py`` が
        組み立てている。DDL ドキュメントだけを見ていると、両者がずれたことに
        気づけない —— バス停のブリッジが ``STATION_BRIDGE_CHECKS`` を共有している
        ことも、ここで一緒に固定される。
        """
        from jp_address_crosswalk.export.writers import (
            LINE_BRIDGE_CHECKS,
            STATION_BRIDGE_CHECKS,
        )

        assert via_lineage_method("spatial_containment") in " ".join(STATION_BRIDGE_CHECKS)
        assert via_lineage_method("spatial_sampling") in " ".join(LINE_BRIDGE_CHECKS)
