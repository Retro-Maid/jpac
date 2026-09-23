"""同じ入力からの2回のビルドが、バイト単位で一致すること (docs/LIMITATIONS.md 項目10).

以前は `observed_from` / `built_at` を時計から取っていたので、同じ payload から作った
2つのリリースが `created_at` や版 id だけ違う、という状態になっていた。id は内容
アドレスで表は総キーで並んでいるのに、**時計由来の列がそれを台無しにしていた**。

観測日時は取得記録（`data/raw/<source>/_payload.yml`、署名は
`docs/ACQUISITION_DATES.md`）から取る。記録が無ければ時計に落ちる —— v1.2.0 までの
挙動なので、その分岐も固定する。
"""

from __future__ import annotations

import pytest

from jp_address_crosswalk.pipeline import FetchOutcome, acquisition_stamp

from .test_two_release import (  # フィクスチャの実パイプラインを使う
    FIXTURES,
    load_abr_town,
    make_outcome,
    repo,
    run_release,
)

pytestmark = pytest.mark.skipif(
    not (FIXTURES / "abr_town.csv").exists(), reason="fixtures not generated"
)


class FakeSnap:
    def __init__(self, name: str, when: str) -> None:
        self.dataset_name = name
        self.downloaded_at = when


def _outcome(*pairs: tuple[str, str]) -> FetchOutcome:
    o = FetchOutcome()
    o.snapshots = [FakeSnap(n, w) for n, w in pairs]
    return o


class TestAcquisitionStamp:
    def test_it_takes_the_latest(self) -> None:
        assert acquisition_stamp(
            _outcome(("a", "2026-08-23T03:22:24+09:00"),
                     ("b", "2026-09-17T06:53:02+09:00"))
        ) == "2026-09-17T06:53:02+09:00"

    def test_mixed_offsets_are_compared_as_instants(self) -> None:
        """文字列順は時刻順ではない。`_payload.yml` は JST、既定の取得側は Z を書く。

        2026-09-17T06:00:00+09:00 は 2026-09-16T21:00:00Z なので、Z 表記のほうが後。
        文字列で比べると "2026-09-17..." が勝ってしまう。
        """
        assert acquisition_stamp(
            _outcome(("jst", "2026-09-17T06:00:00+09:00"),
                     ("utc", "2026-09-17T00:00:00Z"))
        ) == "2026-09-17T00:00:00Z"

    def test_the_attested_string_is_returned_unchanged(self) -> None:
        """署名された値をそのまま返す。整形し直すと、記録と出荷物が食い違う。"""
        assert acquisition_stamp(
            _outcome(("a", "2026-09-17T06:53:02+09:00"))
        ) == "2026-09-17T06:53:02+09:00"

    def test_an_unparsable_stamp_is_skipped_not_guessed(self) -> None:
        assert acquisition_stamp(
            _outcome(("bad", "last tuesday"), ("good", "2026-08-23T00:00:00Z"))
        ) == "2026-08-23T00:00:00Z"

    def test_no_record_means_no_stamp(self) -> None:
        """取得記録が無いときは None。呼び出し側が時計に落ちる（v1.2.0 までの挙動）。"""
        assert acquisition_stamp(_outcome()) is None
        assert acquisition_stamp(_outcome(("a", ""))) is None


class TestTwoIndependentBuilds:
    def test_the_same_inputs_produce_byte_identical_tables(self, tmp_path) -> None:
        """これが項目10 の主張そのもの。

        2つの独立したリポジトリで同じフィクスチャからビルドし、`dist/parquet` の
        全ファイルをバイトで比べる。台帳も `data/previous` も互いに知らない状態から
        始めるので、一致するなら「同じ入力なら同じ出力」が本当に成り立っている。
        """
        town = load_abr_town()
        outputs = []
        for n in ("a", "b"):
            paths = repo(tmp_path / n)
            report = run_release(paths, town, "sha1")
            assert report["passed"], report["thresholds"]
            outputs.append(
                {p.name: p.read_bytes() for p in sorted(paths.parquet.glob("*.parquet"))}
            )

        first, second = outputs
        assert set(first) == set(second)
        differing = [name for name in first if first[name] != second[name]]
        assert not differing, f"byte-identical に失敗: {differing}"

    def test_the_flat_artifacts_match_too(self, tmp_path) -> None:
        """parquet が一致しても、フラット表とレポートが時計を読んでいれば
        `SHA256SUMS` は動く。出荷物が述べているのはデータのことで、実行のことでは
        ない —— 実行時刻はログにある。"""
        town = load_abr_town()
        digests = []
        for n in ("c", "d"):
            paths = repo(tmp_path / n)
            run_release(paths, town, "sha1")
            digests.append(
                {
                    name: (paths.dist / name).read_bytes()
                    for name in ("jp_address_crosswalk.parquet",
                                 "jp_address_crosswalk.csv.gz",
                                 "SOURCES.yml", "quality_report.json")
                    if (paths.dist / name).exists()
                }
            )
        first, second = digests
        differing = [k for k in first if first[k] != second[k]]
        assert not differing, f"byte-identical に失敗: {differing}"


def test_make_outcome_states_an_acquisition_time() -> None:
    """このテスト群の前提。フィクスチャの snapshot が取得時刻を述べていなければ、
    上の2つは「時計が同じ秒を返した」だけの偶然になる。"""
    o = make_outcome(load_abr_town(), "sha1")
    assert acquisition_stamp(o), "フィクスチャの snapshot に downloaded_at が無い"
