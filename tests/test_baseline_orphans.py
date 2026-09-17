"""`jpac baseline` がスキーマゲートを黙って落とさないこと。

`baseline` は expected_schema を一度空にしてから、`data/raw` にあるペイロードだけを
読んで作り直す。ペイロードがこのマシンに無いだけのソースは再生成されないので、
素直に書くと**成功したビルドがゲートを消す**。失敗時の復元処理は走らないため、
消えたことに誰も気づかない。実際に japanpost の delta 2件がこれで消えた。

ここで固定するのは「再生成できなかったものは残す」という一点である。
"""

from __future__ import annotations

from pathlib import Path

from jp_address_crosswalk.cli import restore_orphaned_baselines


def test_a_baseline_whose_payload_is_absent_is_kept(tmp_path: Path) -> None:
    """これが本体。ペイロードが無いソースのゲートは生き残る。"""
    kept = {"present.yml": b"before", "payload_gone.yml": b"gate"}
    # 再生成できたのは片方だけ、という状態を作る。
    (tmp_path / "present.yml").write_bytes(b"regenerated")

    orphaned = restore_orphaned_baselines(tmp_path, kept)

    assert orphaned == ["payload_gone.yml"]
    assert (tmp_path / "payload_gone.yml").read_bytes() == b"gate"


def test_regenerated_files_are_not_overwritten(tmp_path: Path) -> None:
    """埋めるのは穴だけ。再生成された内容を古い版で上書きしない。"""
    kept = {"present.yml": b"before"}
    (tmp_path / "present.yml").write_bytes(b"regenerated")

    assert restore_orphaned_baselines(tmp_path, kept) == []
    assert (tmp_path / "present.yml").read_bytes() == b"regenerated"


def test_a_newly_created_baseline_is_left_alone(tmp_path: Path) -> None:
    """新しいソースを足した直後は kept に無い。触らない。"""
    (tmp_path / "brand_new.yml").write_bytes(b"new")

    assert restore_orphaned_baselines(tmp_path, {}) == []
    assert (tmp_path / "brand_new.yml").read_bytes() == b"new"


def test_the_restored_bytes_are_byte_identical(tmp_path: Path) -> None:
    """ゲートの中身は日本語を含む YAML。復元は再エンコードではなくバイト単位で行う。"""
    blob = "columns:\n- 名称\n- 事業者名\n".encode()
    kept = {"utf8.yml": blob}

    assert restore_orphaned_baselines(tmp_path, kept) == ["utf8.yml"]
    assert (tmp_path / "utf8.yml").read_bytes() == blob


def test_several_orphans_come_back_sorted(tmp_path: Path) -> None:
    kept = {"b.yml": b"2", "a.yml": b"1", "c.yml": b"3"}
    (tmp_path / "b.yml").write_bytes(b"regenerated")

    assert restore_orphaned_baselines(tmp_path, kept) == ["a.yml", "c.yml"]
    assert (tmp_path / "a.yml").read_bytes() == b"1"
    assert (tmp_path / "c.yml").read_bytes() == b"3"
