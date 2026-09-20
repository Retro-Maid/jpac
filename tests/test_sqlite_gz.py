"""出荷する SQLite は圧縮したほうである (export/writers.py の write_sqlite_gz).

GitHub のリリースアセットは1ファイル 2 GiB 未満で、v1.2.0 の非圧縮 SQLite は
2,082,521,088 バイト —— 残り 62 MiB だった。テーブルが増えるたびに近づくので、
`jp_address_crosswalk.csv.gz` と同じやり方で圧縮して配る。

固定したいのは3つ:

* 解凍したら元のバイト列に戻る（配るのが .gz なら、検査済みのものと同一であること
  そのものが主張である）
* 同じ入力なら同じバイト列になる（gzip ヘッダの mtime は既定で現在時刻が入り、
  中身が同じでもダイジェストが毎回変わる）
* リリースの一覧が **.gz を挙げ、生の .sqlite を挙げない**（両方挙げると
  SHA256SUMS がアップロードされていないファイルを載せ、`sha256sum -c` が落ちる）
"""

from __future__ import annotations

import gzip
import sqlite3

from jp_address_crosswalk.export.writers import write_sqlite_gz
from jp_address_crosswalk.pipeline import RELEASE_ARTIFACT_NAMES


def _a_database(path):
    conn = sqlite3.connect(path)
    try:
        conn.execute('CREATE TABLE "t" ("a" TEXT PRIMARY KEY, "b" TEXT)')
        conn.executemany('INSERT INTO "t" VALUES (?,?)',
                         [(f"k{i:05d}", "漢字とかな" * 20) for i in range(2000)])
        conn.commit()
    finally:
        conn.close()
    return path


class TestWriteSqliteGz:
    def test_it_decompresses_to_the_same_bytes(self, tmp_path) -> None:
        src = _a_database(tmp_path / "a.sqlite")
        out = write_sqlite_gz(src, tmp_path / "a.sqlite.gz")
        with gzip.open(out, "rb") as fh:
            assert fh.read() == src.read_bytes()

    def test_the_decompressed_copy_is_still_a_working_database(self, tmp_path) -> None:
        """バイト一致だけでは「開ける」ことを主張していない。実際に開いて数える。"""
        src = _a_database(tmp_path / "b.sqlite")
        out = write_sqlite_gz(src, tmp_path / "b.sqlite.gz")
        restored = tmp_path / "restored.sqlite"
        with gzip.open(out, "rb") as fh:
            restored.write_bytes(fh.read())
        conn = sqlite3.connect(restored)
        try:
            assert conn.execute('SELECT count(*) FROM "t"').fetchone()[0] == 2000
        finally:
            conn.close()

    def test_it_actually_compresses(self, tmp_path) -> None:
        src = _a_database(tmp_path / "c.sqlite")
        out = write_sqlite_gz(src, tmp_path / "c.sqlite.gz")
        assert out.stat().st_size < src.stat().st_size

    def test_two_runs_produce_identical_bytes(self, tmp_path) -> None:
        """mtime=0 と空のファイル名。中身が同じならダイジェストも同じであること。"""
        src = _a_database(tmp_path / "d.sqlite")
        first = write_sqlite_gz(src, tmp_path / "d1.gz").read_bytes()
        second = write_sqlite_gz(src, tmp_path / "d2.gz").read_bytes()
        assert first == second


class TestTheReleaseShipsTheCompressedCopy:
    def test_the_gz_is_a_release_artifact(self) -> None:
        assert "jp_address_crosswalk.sqlite.gz" in RELEASE_ARTIFACT_NAMES

    def test_the_uncompressed_database_is_not(self) -> None:
        """生の .sqlite は dist/ に残るが、配らない。2 GiB 上限がその理由である。"""
        assert "jp_address_crosswalk.sqlite" not in RELEASE_ARTIFACT_NAMES
