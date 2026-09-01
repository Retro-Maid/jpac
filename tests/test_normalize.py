"""Normalization must be conservative (docs/MATCHING_RULES.md §8)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from jp_address_crosswalk.normalize import (
    kanji_to_int,
    normalize_conservative,
    normalize_mlit_relaxed,
    normalize_postal_town,
)


class TestChomeNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("旭ケ丘一丁目", "旭ケ丘1丁目"),
            ("旭ケ丘１丁目", "旭ケ丘1丁目"),
            ("西新宿八丁目", "西新宿8丁目"),
            ("十二丁目", "12丁目"),
            ("二十丁目", "20丁目"),
        ],
    )
    def test_kanji_chome_becomes_arabic(self, raw, expected):
        assert normalize_conservative(raw) == expected

    @pytest.mark.parametrize("name", ["三田", "四谷", "六本木", "九段下", "八丁堀"])
    def test_kanji_numerals_outside_chome_are_untouched(self, name):
        """三田 must not become 3田: the rewrite is bounded to a 丁目 suffix."""
        assert normalize_conservative(name) == name

    def test_abr_and_mlit_chome_forms_converge(self):
        """ABR splits the name; MLIT concatenates it. Both must normalize alike."""
        abr = normalize_conservative("旭ケ丘" + "１丁目")
        mlit = normalize_conservative("旭ケ丘一丁目")
        assert abr == mlit == "旭ケ丘1丁目"


class TestDistinctionsPreserved:
    """These pairs are different real place names and must NOT be merged."""

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("青ヶ島", "青ケ島"),
            ("霞ヶ関", "霞が関"),
            ("井之頭", "井の頭"),
            ("一ノ関", "一の関"),
            ("澤田", "沢田"),
            ("齋藤町", "斎藤町"),
            ("渡邊町", "渡辺町"),
            ("髙田", "高田"),
            ("宮崎", "宮﨑"),
            ("さくら町", "サクラ町"),
        ],
    )
    def test_not_unified(self, a, b):
        assert normalize_conservative(a) != normalize_conservative(b)


class TestWhitespaceAndWidth:
    def test_all_whitespace_removed(self):
        assert normalize_conservative(" 西 新宿　1丁目 ") == "西新宿1丁目"

    def test_fullwidth_digits_folded(self):
        assert normalize_conservative("１２３番町") == "123番町"

    def test_none_and_empty(self):
        assert normalize_conservative(None) == ""
        assert normalize_conservative("") == ""


class TestProfiles:
    def test_mlit_relaxed_strips_oaza_prefix(self):
        assert normalize_mlit_relaxed("大字千代田") == "千代田"
        assert normalize_mlit_relaxed("字中央") == "中央"

    def test_conservative_keeps_oaza_prefix(self):
        assert normalize_conservative("大字千代田") == "大字千代田"

    def test_postal_town_drops_parenthetical(self):
        assert normalize_postal_town("西新宿（次のビルを除く）") == "西新宿"


class TestKanjiToInt:
    @pytest.mark.parametrize(
        ("text", "value"),
        [("一", 1), ("十", 10), ("十五", 15), ("二十", 20),
         ("二十三", 23), ("百", 100), ("百二十三", 123)],
    )
    def test_parses(self, text, value):
        assert kanji_to_int(text) == value

    def test_rejects_non_numeral(self):
        assert kanji_to_int("丁目") is None


class TestProperties:
    @given(st.text(min_size=1, max_size=40))
    def test_idempotent(self, s):
        once = normalize_conservative(s)
        assert normalize_conservative(once) == once

    @given(st.text(alphabet="東西南北中央町丁目一二三四五六七八九十", min_size=1, max_size=12))
    def test_non_empty_input_stays_non_empty(self, s):
        assert normalize_conservative(s) != ""
