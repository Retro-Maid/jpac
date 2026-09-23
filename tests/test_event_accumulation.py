"""来歴・符号観測がリリースをまたいで積まれること (docs/LIMITATIONS.md 項目7).

版テーブル（``*_version``）は以前から `carry_forward` で積まれていたが、
``address_lineage`` / ``address_code`` / ``address_history`` は毎回作り直されていた。
この3つはどれも「前のリリースと今回を比べて分かったこと」なので、作り直すと
**前のリリースで記録したものが次のリリースで消える**。

3つの表に3つの機構があり、ここで固定するのはその違いである:

* ``address_history`` —— id が既に不変なので、union と重複排除だけ
* ``address_lineage`` —— id が並び順の連番だったので、まず内容アドレスにする
* ``address_code`` —— 観測区間を持つ表。閉じて追記する（`is_current` は無い）
"""

from __future__ import annotations

import polars as pl

from jp_address_crosswalk.build.canonical import _lineage_id
from jp_address_crosswalk.build.versioning import (
    accumulate_events,
    carry_forward_observations,
)

LINEAGE_SCHEMA = {
    "lineage_id": pl.Utf8, "old_address_id": pl.Utf8, "new_address_id": pl.Utf8,
    "relation_type": pl.Utf8, "effective_date": pl.Utf8, "observed_at": pl.Utf8,
    "evidence": pl.Utf8, "evidence_source": pl.Utf8, "source_snapshot_id": pl.Utf8,
}

CODE_SCHEMA = {
    "address_id": pl.Utf8, "code_type": pl.Utf8, "code_value": pl.Utf8,
    "valid_from": pl.Utf8, "valid_to": pl.Utf8,
    "observed_from": pl.Utf8, "observed_to": pl.Utf8, "source_snapshot_id": pl.Utf8,
}


def _lineage(*rows: dict) -> pl.DataFrame:
    base = {
        "old_address_id": None, "new_address_id": None, "relation_type": "renamed",
        "effective_date": None, "observed_at": "2026-01-01", "evidence": None,
        "evidence_source": "abr_town_master", "source_snapshot_id": "snap_1",
    }
    return pl.DataFrame([{**base, **r} for r in rows], schema=LINEAGE_SCHEMA)


def _codes(*rows: dict) -> pl.DataFrame:
    base = {
        "address_id": "a1", "code_type": "abr_machiaza_id", "code_value": "0001000",
        "valid_from": None, "valid_to": None, "observed_from": "2026-02-01",
        "observed_to": None, "source_snapshot_id": "snap_2",
    }
    return pl.DataFrame([{**base, **r} for r in rows], schema=CODE_SCHEMA)


def _promote(df: pl.DataFrame, directory, name: str):
    directory.mkdir(parents=True, exist_ok=True)
    df.write_parquet(directory / f"{name}.parquet")
    return directory


class TestLineageId:
    def test_it_does_not_depend_on_how_many_events_there_are(self) -> None:
        """連番だったときの本質的な欠陥。1件増えると全部ずれ、突き合わせられない。"""
        event = {"old_address_id": "a1", "new_address_id": "a1",
                 "relation_type": "renamed", "evidence": "甲 -> 乙",
                 "evidence_source": "abr_town_master"}
        assert _lineage_id(event) == _lineage_id(event)

    def test_the_direction_of_a_rename_is_part_of_the_id(self) -> None:
        """改名はどれも old == new の renamed なので、向きは evidence にしか無い。"""
        forward = {"old_address_id": "a1", "new_address_id": "a1",
                   "relation_type": "renamed", "evidence": "甲 -> 乙",
                   "evidence_source": "abr_town_master"}
        back = {**forward, "evidence": "乙 -> 甲"}
        assert _lineage_id(forward) != _lineage_id(back)

    def test_observed_at_is_not_part_of_the_id(self) -> None:
        """入れると、台帳無しの genesis ビルドが同じイベントを別の日付で再検出した
        ときに行が二重になる（項目5）。積むことで防ぎたかったことそのもの。"""
        event = {"old_address_id": "a1", "new_address_id": None,
                 "relation_type": "retired", "evidence": "no longer present",
                 "evidence_source": "abr_town_master"}
        assert _lineage_id({**event, "observed_at": "2026-01-01"}) == _lineage_id(
            {**event, "observed_at": "2026-09-21"}
        )

    def test_different_relations_between_the_same_pair_differ(self) -> None:
        pair = {"old_address_id": "a1", "new_address_id": "a1", "evidence": "x -> y",
                "evidence_source": "abr_town_master"}
        assert _lineage_id({**pair, "relation_type": "renamed"}) != _lineage_id(
            {**pair, "relation_type": "code_corrected"}
        )


class TestAccumulateEvents:
    def test_an_event_from_the_previous_release_survives(self, tmp_path) -> None:
        """これが項目7の本体。今回検出されなくても残ること。"""
        prev = _lineage({"lineage_id": "lin_old", "old_address_id": "a1",
                         "observed_at": "2026-01-01"})
        d = _promote(prev, tmp_path / "previous", "address_lineage")
        now = _lineage({"lineage_id": "lin_new", "old_address_id": "a2",
                        "observed_at": "2026-09-21"})
        out = accumulate_events(now, d, "address_lineage", "lineage_id")
        assert set(out["lineage_id"]) == {"lin_old", "lin_new"}

    def test_the_first_observation_date_is_the_one_that_is_kept(self, tmp_path) -> None:
        """同じイベントが再検出されても、初めて見た日付を前に進めない。"""
        prev = _lineage({"lineage_id": "lin_1", "observed_at": "2026-01-01"})
        d = _promote(prev, tmp_path / "previous", "address_lineage")
        again = _lineage({"lineage_id": "lin_1", "observed_at": "2026-09-21"})
        out = accumulate_events(again, d, "address_lineage", "lineage_id")
        assert out.height == 1
        assert out["observed_at"][0] == "2026-01-01"

    def test_without_a_previous_release_it_passes_through(self, tmp_path) -> None:
        now = _lineage({"lineage_id": "lin_1"})
        assert accumulate_events(now, None, "address_lineage", "lineage_id").height == 1
        assert accumulate_events(
            now, tmp_path / "nothing_here", "address_lineage", "lineage_id"
        ).height == 1

    def test_an_empty_current_run_still_keeps_the_past(self, tmp_path) -> None:
        """イベントが1件も起きなかったリリースで履歴が消えないこと。"""
        prev = _lineage({"lineage_id": "lin_1"})
        d = _promote(prev, tmp_path / "previous", "address_lineage")
        empty = pl.DataFrame([], schema=LINEAGE_SCHEMA)
        out = accumulate_events(empty, d, "address_lineage", "lineage_id")
        assert out["lineage_id"].to_list() == ["lin_1"]


class TestCarryForwardObservations:
    def test_an_unchanged_code_keeps_its_original_observed_from(self, tmp_path) -> None:
        prev = _codes({"observed_from": "2026-02-01"})
        d = _promote(prev, tmp_path / "previous", "address_code")
        now = _codes({"observed_from": "2026-09-21"})
        out = carry_forward_observations(
            now, d, "address_code",
            ["address_id", "code_type", "code_value"], "2026-09-21",
        )
        assert out.height == 1
        assert out["observed_from"][0] == "2026-02-01"
        assert out["observed_to"][0] is None

    def test_a_changed_code_closes_the_old_row_and_adds_the_new(self, tmp_path) -> None:
        prev = _codes({"code_value": "0001000", "observed_from": "2026-02-01"})
        d = _promote(prev, tmp_path / "previous", "address_code")
        now = _codes({"code_value": "0002000", "observed_from": "2026-09-21"})
        out = carry_forward_observations(
            now, d, "address_code",
            ["address_id", "code_type", "code_value"], "2026-09-21",
        ).sort("code_value")
        assert out["code_value"].to_list() == ["0001000", "0002000"]
        assert out["observed_to"].to_list() == ["2026-09-21", None]
        # 古い観測の「いつから」は動かさない。動かすと「2026-02-01 から見えていた」
        # という事実そのものが消える。
        assert out["observed_from"].to_list() == ["2026-02-01", "2026-09-21"]

    def test_a_retired_address_keeps_its_codes_closed_rather_than_losing_them(
        self, tmp_path
    ) -> None:
        """住所が退役しても符号の観測は残る。退役は「もう見えない」であって
        「見えたことが無い」ではない。"""
        prev = _codes({"address_id": "gone", "observed_from": "2026-02-01"})
        d = _promote(prev, tmp_path / "previous", "address_code")
        now = _codes({"address_id": "still_here"})
        out = carry_forward_observations(
            now, d, "address_code",
            ["address_id", "code_type", "code_value"], "2026-09-21",
        ).sort("address_id")
        assert out["address_id"].to_list() == ["gone", "still_here"]
        assert out.filter(pl.col("address_id") == "gone")["observed_to"][0] == "2026-09-21"

    def test_an_already_closed_row_is_left_alone(self, tmp_path) -> None:
        prev = _codes(
            {"code_value": "0001000", "observed_from": "2026-01-01",
             "observed_to": "2026-02-01"},
            {"code_value": "0002000", "observed_from": "2026-02-01"},
        )
        d = _promote(prev, tmp_path / "previous", "address_code")
        now = _codes({"code_value": "0002000"})
        out = carry_forward_observations(
            now, d, "address_code",
            ["address_id", "code_type", "code_value"], "2026-09-21",
        ).sort("code_value")
        assert out["observed_to"].to_list() == ["2026-02-01", None]

    def test_a_code_observed_again_after_closing_gets_a_second_row(
        self, tmp_path
    ) -> None:
        """「ここからここまで、そしてまたここから」を2行で言う。区間が鍵に入って
        いる（DB_SCHEMA.md の PRIMARY KEY）ので、これが正しい表現である。"""
        prev = _codes({"code_value": "0001000", "observed_from": "2026-01-01",
                       "observed_to": "2026-02-01"})
        d = _promote(prev, tmp_path / "previous", "address_code")
        now = _codes({"code_value": "0001000", "observed_from": "2026-09-21"})
        out = carry_forward_observations(
            now, d, "address_code",
            ["address_id", "code_type", "code_value"], "2026-09-21",
        ).sort("observed_from")
        assert out.height == 2
        assert out["observed_from"].to_list() == ["2026-01-01", "2026-09-21"]
        assert out["observed_to"].to_list() == ["2026-02-01", None]

    def test_without_a_previous_release_it_passes_through(self) -> None:
        now = _codes({})
        assert carry_forward_observations(
            now, None, "address_code",
            ["address_id", "code_type", "code_value"], "2026-09-21",
        ).height == 1


class TestAnIntervalCannotCloseBeforeItOpened:
    """観測日を時計ではなく取得記録から取ると（docs/LIMITATIONS.md 項目10）、
    ビルド日で開いた行を、それより前の取得日で閉じることになりうる。

    実データで起きる形がある: v1.2.0 の `address_code` 2,178,510行は
    `observed_from = 2026-09-20`（ビルド日）で、payload の取得日は最新でも
    2026-09-17 である。素直に代入すると「2026-09-20 に開き 2026-09-17 に閉じた」
    行が出る。どちらの値も嘘ではないので、直すのは区間の側である。
    """

    def test_a_closing_date_never_precedes_the_opening_date(self, tmp_path) -> None:
        prev = _codes({"address_id": "gone", "observed_from": "2026-09-20"})
        d = _promote(prev, tmp_path / "previous", "address_code")
        out = carry_forward_observations(
            _codes({"address_id": "still_here"}), d, "address_code",
            ["address_id", "code_type", "code_value"], "2026-09-17",
        )
        closed = out.filter(pl.col("address_id") == "gone")
        assert closed["observed_to"][0] == "2026-09-20"
        assert closed["observed_to"][0] >= closed["observed_from"][0]

    def test_a_later_stamp_still_closes_at_the_stamp(self, tmp_path) -> None:
        """ふつうの場合は何も変わらないこと。"""
        prev = _codes({"address_id": "gone", "observed_from": "2026-02-01"})
        d = _promote(prev, tmp_path / "previous", "address_code")
        out = carry_forward_observations(
            _codes({"address_id": "still_here"}), d, "address_code",
            ["address_id", "code_type", "code_value"], "2026-09-21",
        )
        assert out.filter(pl.col("address_id") == "gone")["observed_to"][0] == "2026-09-21"


class TestFindingsFromReview:
    """レビューで見つかった、いずれも「黙って失われる」種類の欠陥。"""

    def test_an_empty_current_closes_the_past_instead_of_deleting_it(
        self, tmp_path
    ) -> None:
        """今回どの符号も観測されなかったとき、前の観測は**閉じて残る**。

        `if current.is_empty(): return current` という早期 return があり、
        docstring が約束していることの逆をしていた —— 2.18M 行がまるごと消える。
        """
        prev = _codes({"address_id": "a1", "observed_from": "2026-02-01"})
        d = _promote(prev, tmp_path / "previous", "address_code")
        empty = pl.DataFrame([], schema=CODE_SCHEMA)
        out = carry_forward_observations(
            empty, d, "address_code",
            ["address_id", "code_type", "code_value"], "2026-09-21",
        )
        assert out.height == 1
        assert out["observed_to"][0] == "2026-09-21"

    def test_two_different_changes_on_one_date_both_survive(self, tmp_path) -> None:
        """`history_id` が (address_id|field|observed_at) だけだと、同じ観測日で同じ列が
        2度違う値に変わったときに id が衝突し、重複排除が**後の変更を捨てる**。
        観測日が時計由来だった頃は日付が毎回違うので起きなかったが、取得記録から取る
        ようになった今は同じ日付の2リリースがありうる。
        """
        from jp_address_crosswalk.build.versioning import build_address_history

        schema = {"address_id": pl.Utf8, "lg_code": pl.Utf8}
        prev_dir = tmp_path / "previous"
        prev_dir.mkdir()

        def addr(lg: str) -> pl.DataFrame:
            return pl.DataFrame([{"address_id": "a1", "lg_code": lg}], schema=schema)

        # release 1: 011011 -> 011012 を検出
        addr("011011").write_parquet(prev_dir / "address.parquet")
        h1 = build_address_history(addr("011012"), prev_dir, "2026-09-17", "snap_1")
        assert h1.height == 1
        h1.write_parquet(prev_dir / "address_history.parquet")

        # release 2: 同じ観測日のまま 011012 -> 011013
        addr("011012").write_parquet(prev_dir / "address.parquet")
        h2 = build_address_history(addr("011013"), prev_dir, "2026-09-17", "snap_2")
        assert h2.height == 2, "2つ目の変更が捨てられている"
        assert set(h2["new_value"]) == {"011012", "011013"}
