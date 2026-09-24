"""取得日の manifest を書く道具 (tools/write_payload_manifests.py).

固定したいのは1つだけで、それがレビューで見つかった欠陥である:
**mtime が署名された日付と合わないとき、何も書かない。**

書いてしまうと、`jpac build` が署名されていない日付をそのまま使い、`observed_from` にも
リリースタグの `+data-` にも載る。道具は exit 2 を返して「nothing was written」と印字
するので、**報告を読んでも書かれていないと思い込む**という最悪の形になっていた。
"""

from __future__ import annotations

import datetime
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "write_payload_manifests", ROOT / "tools" / "write_payload_manifests.py"
)
tool = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tool
spec.loader.exec_module(tool)


def _payload(raw: Path, source: str, name: str, when: str) -> Path:
    d = raw / source
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_bytes(b"payload")
    ts = datetime.datetime.fromisoformat(when).timestamp()
    os.utime(f, (ts, ts))
    return f


def _signed_date(source: str) -> str:
    return tool.ATTESTED[source][0]


class TestNothingIsWrittenWhenTheMtimeDisagrees:
    def test_a_mismatched_mtime_writes_no_manifest(self, tmp_path, capsys) -> None:
        raw = tmp_path / "raw"
        _payload(raw, "abr", "town_master.zip", "2026-09-30T10:00:00+09:00")
        code = tool.main(check_only=False, raw=raw)
        assert code == 2
        assert not (raw / "abr" / tool.MANIFEST_NAME).exists(), \
            "署名と合わない日付の manifest が書かれている"
        assert "nothing was written" in capsys.readouterr().out

    def test_one_bad_source_stops_the_good_ones_too(self, tmp_path) -> None:
        """1つでも合わなければ、全部書かない。片方だけ書くと、成果物の日時が
        「署名されたもの」と「そうでないもの」の混ざったものになる。"""
        raw = tmp_path / "raw"
        _payload(raw, "abr", "town_master.zip", _signed_date("abr") + "T03:22:24+09:00")
        _payload(raw, "mlit_ksj_p11", "P11-22_SHP.zip", "2026-09-30T10:00:00+09:00")
        assert tool.main(check_only=False, raw=raw) == 2
        assert not (raw / "abr" / tool.MANIFEST_NAME).exists()
        assert not (raw / "mlit_ksj_p11" / tool.MANIFEST_NAME).exists()

    def test_a_matching_mtime_writes_the_signed_stamp(self, tmp_path) -> None:
        raw = tmp_path / "raw"
        when = _signed_date("mlit_ksj_p11") + "T06:53:02+09:00"
        _payload(raw, "mlit_ksj_p11", "P11-22_SHP.zip", when)
        assert tool.main(check_only=False, raw=raw) == 0
        text = (raw / "mlit_ksj_p11" / tool.MANIFEST_NAME).read_text(encoding="utf-8")
        assert "P11-22_SHP:" in text
        assert _signed_date("mlit_ksj_p11") in text
        assert tool.ATTESTED_BY in text

    def test_check_mode_writes_nothing_at_all(self, tmp_path) -> None:
        raw = tmp_path / "raw"
        when = _signed_date("mlit_ksj_p11") + "T06:53:02+09:00"
        _payload(raw, "mlit_ksj_p11", "P11-22_SHP.zip", when)
        assert tool.main(check_only=True, raw=raw) == 2   # manifest が無いので DIFFERS
        assert not (raw / "mlit_ksj_p11" / tool.MANIFEST_NAME).exists()
