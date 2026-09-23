"""版の区間が重ならないこと (build/versioning.py の carry_forward).

観測日を時計から取得記録に変えた副作用（レビューで見つかった）。取得日は前のリリースの
ビルド日より**前**になりうるので:

* 前の版を今回の観測日で閉じると、開いた日より前に閉じることがある（項目10 の下ごしらえ
  で直した1つ目）
* それを直しても、**行と行のあいだ**に重なりが残る —— 前の版が 2026-09-20 に閉じ、
  後継が 2026-09-17 に開くと、2つの版が 09-17〜09-20 を同時に覆い、as-of の問い合わせが
  2行返す

slowly-changing dimension の普通の不変条件は「区間はちょうど接して重ならない」なので、
後継の観測日を前の版が閉じた日に合わせる。
"""

from __future__ import annotations

import polars as pl

from jp_address_crosswalk.build.versioning import carry_forward

SCHEMA = {
    "k": pl.Utf8, "payload": pl.Utf8, "ver_id": pl.Utf8,
    "observed_from": pl.Utf8, "observed_to": pl.Utf8, "is_current": pl.Boolean,
    "source_snapshot_id": pl.Utf8,
}


def _frame(payload: str, observed_from: str) -> pl.DataFrame:
    return pl.DataFrame(
        [{"k": "k1", "payload": payload, "ver_id": None,
          "observed_from": observed_from, "observed_to": None, "is_current": True,
          "source_snapshot_id": "snap"}],
        schema=SCHEMA,
    )


def _release(frame: pl.DataFrame, previous_dir, stamp: str) -> pl.DataFrame:
    return carry_forward(frame, previous_dir, "t", ["k"], "ver_id", stamp)


class TestTheSuccessorOpensWhereThePredecessorClosed:
    def test_an_earlier_stamp_does_not_open_a_version_in_the_past(self, tmp_path) -> None:
        """前の版がビルド日（2026-09-20）で開いていて、今回の取得日が 2026-09-17。"""
        prev = tmp_path / "previous"
        prev.mkdir()
        _release(_frame("old", "2026-09-20"), None, "2026-09-20").write_parquet(
            prev / "t.parquet"
        )
        out = _release(_frame("new", "2026-09-17"), prev, "2026-09-17")

        closed = out.filter(~pl.col("is_current"))
        live = out.filter(pl.col("is_current"))
        assert closed.height == 1 and live.height == 1
        # 閉じる日は開いた日より前にならない
        assert closed["observed_to"][0] == "2026-09-20"
        # そして後継はそこから始まる —— 重ならない
        assert live["observed_from"][0] == "2026-09-20"
        assert closed["observed_to"][0] == live["observed_from"][0]

    def test_the_ordinary_case_is_untouched(self, tmp_path) -> None:
        """取得日が前の版より後なら、何も起きない。"""
        prev = tmp_path / "previous"
        prev.mkdir()
        _release(_frame("old", "2026-02-01"), None, "2026-02-01").write_parquet(
            prev / "t.parquet"
        )
        out = _release(_frame("new", "2026-09-21"), prev, "2026-09-21")
        assert out.filter(~pl.col("is_current"))["observed_to"][0] == "2026-09-21"
        assert out.filter(pl.col("is_current"))["observed_from"][0] == "2026-09-21"

    def test_exactly_one_version_stays_live(self, tmp_path) -> None:
        """区間を動かしても、生きている版は1つのままであること。"""
        prev = tmp_path / "previous"
        prev.mkdir()
        _release(_frame("old", "2026-09-20"), None, "2026-09-20").write_parquet(
            prev / "t.parquet"
        )
        out = _release(_frame("new", "2026-09-17"), prev, "2026-09-17")
        assert out.filter(pl.col("is_current")).height == 1
        assert out["ver_id"].n_unique() == out.height
