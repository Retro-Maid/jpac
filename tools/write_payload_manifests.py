"""`data/raw/<source>/_payload.yml` を、署名された取得日から書き直す.

このビルドはネットワークを触らないので、**いつ取得したか**を自分では観測できない。
記録が無いと `downloaded_at` がビルド時刻に落ち、リリースタグの `+data-YYYY-MM-DD`
がビルド日を指す（docs/LIMITATIONS.md 項目10、v1.1.0 / v1.2.0 のリリースノート）。

`data/raw/` は `.gitignore` 済みなので、そこに置いた `_payload.yml` はこのマシンにしか
無い。**署名そのものはリポジトリに残る必要がある** —— 誰が何を根拠にこの日付を主張した
のかが payload と一緒に消えては、来歴の記録として意味がない。だから署名は
`docs/ACQUISITION_DATES.md` とこのファイルの `ATTESTED` にあり、`_payload.yml` は
そこから生成される派生物にしてある。

**mtime が署名された日付と合わなければ書かない。** 別の payload に差し替わっている
可能性のほうが、記録が古いままである可能性より重い。取得し直したら、まず
docs/ACQUISITION_DATES.md に人が署名を足す。

Run:  py -3.12 tools/write_payload_manifests.py [--check]
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
MANIFEST_NAME = "_payload.yml"

ATTESTED_BY = "retro.maid.itworker@gmail.com"
ATTESTED_ON = "2026-09-23"

# 取得日（JST の暦日）と、その日付を mtime とは独立に記録している文書。
# docs/ACQUISITION_DATES.md が同じ表を人の言葉で持っている。
ATTESTED: dict[str, tuple[str, str]] = {
    "abr": ("2026-08-23", "README.md の出典表と dist/SOURCES.yml v1.0.0"),
    "japanpost": ("2026-08-23", "README.md の出典表と dist/SOURCES.yml v1.0.0"),
    "mlit": ("2026-08-23", "README.md の出典表と dist/SOURCES.yml v1.0.0"),
    "mic_area_code": ("2026-08-23", "README.md の出典表と dist/SOURCES.yml v1.0.0"),
    "mic_number_assignment": (
        "2026-08-23", "README.md の出典表と dist/SOURCES.yml v1.0.0",
    ),
    "mlit_ksj_n02": (
        "2026-09-05", "docs/STATION_JOIN_PREFLIGHT.md（同日に取得して実測）",
    ),
    "estat_boundary": (
        "2026-09-05", "docs/STATION_JOIN_PREFLIGHT.md（同日に全47県を取得して実測）",
    ),
    "mlit_ksj_p11": (
        "2026-09-17", "docs/LICENSE_REVIEW_P11_2026_09.md（同日の逐語読みと取得）",
    ),
}

HEADER = """\
# Acquisition-side facts for data/raw/{source}/ (src/jp_address_crosswalk/payload.py).
#
# 生成物である。手で編集しない —— tools/write_payload_manifests.py が
# docs/ACQUISITION_DATES.md の署名から書く。
#
# このビルドはネットワークを触らないので、いつ取得したかを自分では観測できない。
# 記録が無いと downloaded_at がビルド時刻に落ち、リリースタグの +data-YYYY-MM-DD が
# ビルド日を指す（docs/LIMITATIONS.md 項目10）。
#
# 根拠は2つ:
#   1. ファイルシステムの mtime —— payload がこのディスクに書かれた時刻。下の
#      downloaded_at は各ファイルの mtime そのままで、時刻部分もそこから来る（JST）。
#      日付だけに丸めると、観測していない時刻を主張することになる
#   2. {evidence}
#
# mtime は書き換えられる値で、コピーで失われることもある。だから mtime 単独ではなく、
# 上の文書が同じ日付を独立に記録していることをもって署名している。生成時に全ファイルの
# mtime が署名された日付と一致することを確かめており、一致しなければ何も書かない。
#
# ライセンスの節は置かない。terms のハッシュは config/sources.yml の committed
# baseline が持っており、ここに観測値を書くと「このビルドが terms ページを見た」という
# 主張になる —— 見ていない。
#
# attested_date: {date}
# attested_by: {by}
# attested_on: {on}
resources:
"""


def payloads(src_dir: Path) -> list[Path]:
    return sorted(
        p for p in src_dir.iterdir() if p.is_file() and p.name != MANIFEST_NAME
    )


def main(check_only: bool) -> int:
    problems: list[str] = []
    written = 0
    for source, (date, evidence) in sorted(ATTESTED.items()):
        src_dir = RAW / source
        if not src_dir.is_dir():
            print(f"  [skip] {source}: no payload directory")
            continue
        files = payloads(src_dir)
        if not files:
            print(f"  [skip] {source}: directory is empty")
            continue

        stamps = {}
        for f in files:
            ts = datetime.datetime.fromtimestamp(f.stat().st_mtime).astimezone()
            if ts.date().isoformat() != date:
                problems.append(
                    f"{source}/{f.name}: mtime {ts.date().isoformat()} "
                    f"but the signed acquisition date is {date}"
                )
            stamps[f.stem] = ts.isoformat(timespec="seconds")

        body = HEADER.format(
            source=source, evidence=evidence, date=date, by=ATTESTED_BY, on=ATTESTED_ON
        ) + "".join(
            f"  {stem}:\n    downloaded_at: \"{ts}\"\n"
            for stem, ts in sorted(stamps.items())
        )
        target = src_dir / MANIFEST_NAME
        if check_only:
            state = "up to date" if (
                target.exists() and target.read_text(encoding="utf-8") == body
            ) else "DIFFERS"
            print(f"  [{state}] {source}: {len(files)} payload(s), {date}")
            if state == "DIFFERS":
                problems.append(f"{source}: {MANIFEST_NAME} differs from the signature")
            continue
        target.write_text(body, encoding="utf-8", newline="\n")
        written += 1
        print(f"  [ok] {source}: {len(files)} payload(s), {date}")

    print()
    if problems:
        print(f"{len(problems)} problem(s) — nothing was trusted:")
        for p in problems[:10]:
            print("  !", p)
        print("\n取得し直したなら、まず docs/ACQUISITION_DATES.md に署名を足す。")
        return 2
    print("wrote" if not check_only else "checked", written or len(ATTESTED), "manifest(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv[1:]))
