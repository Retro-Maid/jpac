"""Measure what the ABR 町字マスター位置参照拡張 actually carries.

The position extension is described by the publisher as an ID対応表 — the table
that links a 町字ID to MLIT 位置参照情報 and to the Statistics Bureau's 国勢調査
小地域境界データ. Its published schema has the columns to do exactly that:

    pos_oaza_cho_chome_code   MLIT 大字町丁目コード
    cns_bnd_s_area_kcode      国勢調査 境界 小地域（町丁・字等別）KEY_CODE
    cns_bnd_year              国勢調査 境界 データ整備年度
    plygn_fname / plygn_kcode / plygn_fmt / plygn_srid / plygn_scale
                              町字ポリゴンの外部ファイル参照

Whether those columns are *populated* is a different question from whether they
exist, and it decides whether an official ABR ↔ e-Stat crosswalk is something
this project can consume or something it has to build. This probe answers it by
counting, per prefecture, how many rows carry a non-empty value in each column.

It also joins the extension back to 町字マスター on (lg_code, machiaza_id) — the
extension is only useful to jpac to the extent that it lands on addresses jpac
already has.

Nothing here is part of the build. It is a one-shot measurement, kept so the
numbers quoted in docs/GEO_EXPANSION_RESEARCH.md §3.4 can be re-taken rather
than trusted.

Run:  py -3.12 tools/probe_abr_town_pos.py [--cache DIR]
"""

from __future__ import annotations

import sys as _sys

# Same reason as the other tools: a cp932 console must not kill the report.
if hasattr(_sys.stdout, "reconfigure") and (_sys.stdout.encoding or "").lower() not in (
    "utf-8",
    "utf8",
):
    _sys.stdout.reconfigure(errors="replace")
    if hasattr(_sys.stderr, "reconfigure"):
        _sys.stderr.reconfigure(errors="replace")

import csv
import io
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

POS_URL = "https://data.address-br.digital.go.jp/mt_town_pos/pref/mt_town_pos_pref{:02d}.csv.zip"
TOWN_URL = "https://data.address-br.digital.go.jp/mt_town/mt_town_all.csv.zip"

# Counted columns. The MLIT one is the control: if it is populated and the
# census one is not, the finding is "the e-Stat slot was left empty", not
# "the whole linkage block is empty".
COLUMNS = [
    "pos_oaza_cho_chome_code",
    "pos_data_mnt_year",
    "cns_bnd_s_area_kcode",
    "cns_bnd_year",
    "plygn_fname",
    "plygn_kcode",
    "rep_lon",
]

PREF_NAMES = ["北海道", "青森", "岩手", "宮城", "秋田", "山形", "福島", "茨城", "栃木", "群馬", "埼玉", "千葉", "東京", "神奈川", "新潟", "富山", "石川", "福井", "山梨", "長野", "岐阜", "静岡", "愛知", "三重", "滋賀", "京都", "大阪", "兵庫", "奈良", "和歌山", "鳥取", "島根", "岡山", "広島", "山口", "徳島", "香川", "愛媛", "高知", "福岡", "佐賀", "長崎", "熊本", "大分", "宮崎", "鹿児島", "沖縄"]


def fetch(url: str, cache: Path) -> bytes:
    """Download once, then reuse. The publisher does not need 47 requests twice."""
    target = cache / url.rsplit("/", 1)[-1]
    if target.exists():
        return target.read_bytes()
    with urllib.request.urlopen(url, timeout=120) as response:
        payload = response.read()
    cache.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return payload


def read_csv_from_zip(payload: bytes) -> list[dict[str, str]]:
    """Every value stays a string. machiaza_id and lg_code have leading zeros."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".csv"))
        text = archive.read(name).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def filled(row: dict[str, str], column: str) -> bool:
    """Non-empty after strip. A column present but blank is not a value."""
    return (row.get(column) or "").strip() != ""


def main(argv: list[str]) -> int:
    cache = Path(argv[argv.index("--cache") + 1]) if "--cache" in argv else Path(".abr_probe")

    town_keys: set[tuple[str, str]] = set()
    for row in read_csv_from_zip(fetch(TOWN_URL, cache)):
        town_keys.add((row["lg_code"], row["machiaza_id"]))
    by_lg: dict[str, set[str]] = defaultdict(set)
    for lg_code, machiaza_id in town_keys:
        by_lg[lg_code].add(machiaza_id)
    print(f"町字マスター: {len(town_keys):,} distinct (lg_code, machiaza_id)\n")

    header = f"{'pref':<10}{'rows':>9}" + "".join(f"{c.split('_')[0][:8]:>10}" for c in COLUMNS)
    print(header)
    print("-" * len(header))

    totals = dict.fromkeys(COLUMNS, 0)
    rows_total = 0
    matched_total = 0
    pos_keys: set[tuple[str, str]] = set()
    schema: list[str] | None = None

    for index, name in enumerate(PREF_NAMES, start=1):
        rows = read_csv_from_zip(fetch(POS_URL.format(index), cache))
        if schema is None:
            schema = list(rows[0].keys())
        elif list(rows[0].keys()) != schema:
            print(f"  ! {name}: schema differs from prefecture 01", file=_sys.stderr)

        counts = {c: sum(1 for r in rows if filled(r, c)) for c in COLUMNS}
        matched = sum(1 for r in rows if r["machiaza_id"] in by_lg.get(r["lg_code"], ()))
        pos_keys.update((r["lg_code"], r["machiaza_id"]) for r in rows)
        for column in COLUMNS:
            totals[column] += counts[column]
        rows_total += len(rows)
        matched_total += matched
        print(f"{name:<10}{len(rows):>9}" + "".join(f"{counts[c]:>10}" for c in COLUMNS))

    print("-" * len(header))
    print(f"\nschema: {schema}\n")
    print(f"rows: {rows_total:,}")
    for column in COLUMNS:
        share = totals[column] / rows_total * 100 if rows_total else 0.0
        print(f"  {column:<26}{totals[column]:>9,}  ({share:5.2f}%)")
    share = matched_total / rows_total * 100 if rows_total else 0.0
    print(f"\nextension rows that join to 町字マスター: {matched_total:,} / {rows_total:,} ({share:.1f}%)")
    covered = len(town_keys & pos_keys)
    share = covered / len(town_keys) * 100 if town_keys else 0.0
    print(f"町字 that have an extension record:      {covered:,} / {len(town_keys):,} ({share:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(_sys.argv[1:]))
