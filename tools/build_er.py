"""Draw every table in docs/schema.sql as an ER diagram: mermaid pages and one PDF.

``docs/diagrams/04-er.mmd`` is hand-drawn and stopped at v1.0.0's 28 tables. The
release has since grown to 44, and a hand-drawn diagram of 44 tables would go
stale the same way, so this one is generated. Its input is ``docs/schema.sql``,
which is itself checked against the shipped artifact (verify_artifacts_agree.py
3c), so the diagram cannot show a column or a key the release does not have.

What schema.sql cannot say is listed in LOGICAL below: joins the release relies
on without declaring them as FOREIGN KEY (an entity and its ``*_version`` table,
the census tables' ``lg_code``, ...). Those are drawn dashed; declared keys are
solid. Every LOGICAL entry names real columns, and a renamed column fails the
build here instead of leaving a dashed line to nowhere.

The coordinate asset (``jp_address_crosswalk_points.zip``, docs/POLICY.md §3.4)
is not in the SQLite file and so not in schema.sql. Its two tables are read from
the schema constants in build/points.py, the same ones the build writes with, and
drawn on their own page marked as a separate asset.

Outputs, under docs/diagrams/er/:
    NN-<page>.mmd     generated source, one per page (committed, reviewable)
    00-overview.svg   the overview page, embedded in the README
    jpac-er.pdf       every page in one file, with bookmarks, for zooming in
    manifest.json     SHA-256 of each .mmd the SVG and PDF were rendered from

The renders are not compared byte for byte, for the reason build_diagrams.py
gives: mermaid's edge routing is random. The check asks whether the .mmd files
are what schema.sql generates now, and whether the renders were made from them.

Usage:
    py -3.12 tools/build_er.py           # regenerate pages, render, write manifest
    py -3.12 tools/build_er.py --check   # fail if pages or renders are stale
                                         # (same as: jpac verify er)
"""

from __future__ import annotations

import sys as _sys

# Japanese table labels go to a cp932 console on Windows. Degrade the
# character, not the run (same guard as build_diagrams.py).
if hasattr(_sys.stdout, "reconfigure") and (_sys.stdout.encoding or "").lower() not in (
    "utf-8", "utf8"
):
    _sys.stdout.reconfigure(errors="replace")
    if hasattr(_sys.stderr, "reconfigure"):
        _sys.stderr.reconfigure(errors="replace")


import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "docs" / "schema.sql"
OUT = ROOT / "docs" / "diagrams" / "er"
MANIFEST = OUT / "manifest.json"
PDF = OUT / "jpac-er.pdf"
OVERVIEW_SVG = OUT / "00-overview.svg"

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "src"))
from build_diagrams import CONFIG, MERMAID_CLI, _npx, source_hash  # noqa: E402

from jp_address_crosswalk.build.points import POINT_TABLES, POINTS_ZIP_NAME  # noqa: E402

# Tables of the coordinate asset: (key column). Not in schema.sql (see above).
ASSET_KEYS = {"station_point": "n02_group_code", "bus_stop_point": "p11_stop_id"}

# One short Japanese name per table, shown under the table name.
LABELS: dict[str, str] = {
    "address": "町字（ABR）",
    "address_entity": "町字の実体・ID 台帳",
    "address_code": "町字の各種コード",
    "address_history": "町字の属性変更",
    "address_lineage": "町字の分割・統合",
    "address_rsdt_variant": "住居表示の版",
    "address_key_conflict": "鍵が衝突した行（保留）",
    "bridge_address_mlit": "町字 ↔ 国交省",
    "bridge_address_postal": "町字 ↔ 郵便レコード",
    "bridge_address_postal_code": "町字 ↔ 郵便番号",
    "bridge_address_telephone": "町字 ↔ 番号区画",
    "bridge_bus_stop_municipality": "バス停 ↔ 市区町村",
    "bridge_line_municipality": "路線 ↔ 市区町村",
    "bridge_municipality_postal": "市区町村 ↔ 郵便特殊レコード",
    "bridge_municipality_postal_code": "市区町村 ↔ 郵便番号",
    "bridge_municipality_telephone": "市区町村 ↔ 番号区画",
    "bridge_station_line": "駅 ↔ 路線",
    "bridge_station_municipality": "駅 ↔ 市区町村",
    "census_population_age": "年齢別人口",
    "census_population_age_summary": "平均年齢・年齢中位数",
    "census_population_municipality": "市区町村の人口",
    "census_population_numbering_area": "番号区画の人口",
    "census_population_raw": "人口 原表",
    "estat_small_area": "e-Stat 小地域（形状なし）",
    "match_run": "照合の実行",
    "match_run_input": "実行が読んだ元データ",
    "mlit_town": "国交省 大字・町丁目",
    "mlit_town_version": "国交省 大字・町丁目の版（代表点）",
    "municipality": "市区町村",
    "municipality_change_raw": "廃置分合 原表",
    "municipality_lineage_edge": "廃置分合 旧→新",
    "municipality_version": "市区町村の版（名称）",
    "n02_railroad_line": "路線（N02）",
    "n02_station": "駅（N02 駅グループ）",
    "station_point": "駅の代表点（座標アセット）",
    "bus_stop_point": "バス停の点（座標アセット）",
    "p11_bus_stop": "バス停（P11）",
    "postal_code_entity": "郵便番号",
    "postal_record": "郵便レコード",
    "postal_record_version": "郵便レコードの版",
    "snapshot_license_artifact": "規約の照合記録",
    "source_snapshot": "取得した元データ",
    "telephone_area": "番号区画",
    "telephone_area_coverage": "番号区画の収容句",
    "telephone_area_version": "番号区画の版（市外局番）",
    "telephone_number_block": "市内局番",
}

# (slug, title, tables). Page 00 is the overview of everything; each table is
# drawn in full on exactly one of the other pages.
PAGES: list[tuple[str, str, list[str]]] = [
    ("01-address", "住所（ABR 町字）と市区町村", [
        "address", "address_entity", "address_code", "address_history",
        "address_lineage", "address_rsdt_variant", "address_key_conflict",
        "municipality", "municipality_version",
    ]),
    ("02-postal", "郵便番号", [
        "postal_code_entity", "postal_record", "postal_record_version",
        "bridge_address_postal_code", "bridge_address_postal",
        "bridge_municipality_postal_code", "bridge_municipality_postal",
    ]),
    ("03-mlit-telephone", "国土交通省 位置参照・市外局番", [
        "mlit_town", "mlit_town_version", "bridge_address_mlit",
        "telephone_area", "telephone_area_version", "telephone_area_coverage",
        "telephone_number_block", "bridge_address_telephone",
        "bridge_municipality_telephone",
    ]),
    ("04-rail-bus", "駅・路線・バス停", [
        "n02_station", "n02_railroad_line", "bridge_station_line",
        "bridge_station_municipality", "bridge_line_municipality",
        "p11_bus_stop", "bridge_bus_stop_municipality", "estat_small_area",
    ]),
    ("05-census-lineage", "人口（国勢調査）・廃置分合", [
        "census_population_municipality", "census_population_age",
        "census_population_age_summary", "census_population_numbering_area",
        "census_population_raw", "municipality_lineage_edge",
        "municipality_change_raw",
    ]),
    ("06-provenance", "来歴", [
        "source_snapshot", "snapshot_license_artifact", "match_run",
        "match_run_input",
    ]),
    ("07-points", f"座標（別アセット {POINTS_ZIP_NAME}。SQLite には無い）", [
        "station_point", "bus_stop_point",
    ]),
]


@dataclass(frozen=True)
class Edge:
    parent: str
    parent_cols: tuple[str, ...]
    child: str
    child_cols: tuple[str, ...]
    rel: str          # mermaid cardinality, e.g. "||--o{" (solid) or "||..o{" (dashed)
    label: str


# Joins the release relies on but does not declare. Each was measured on the
# v2.1.0 artifact before it was written down here; the notes say what holds.
LOGICAL: list[Edge] = [
    # address and address_entity are the same set of address_id, one row each.
    Edge("address_entity", ("address_id",), "address", ("address_id",),
         "||..||", "address_id（同じ ID）"),
    Edge("address", ("lg_code", "machiaza_id"), "address_rsdt_variant",
         ("lg_code", "machiaza_id"), "||..|{", "lg_code + machiaza_id"),
    # Entity / version pairs. The current row is is_current = 1.
    Edge("municipality", ("lg_code",), "municipality_version", ("lg_code",),
         "||..|{", "lg_code（現行は is_current = 1）"),
    Edge("postal_record", ("postal_record_id",), "postal_record_version",
         ("postal_record_id",), "||..|{", "postal_record_id（現行は is_current = 1）"),
    Edge("mlit_town", ("mlit_record_id",), "mlit_town_version", ("mlit_record_id",),
         "||..|{", "mlit_record_id（現行は is_current = 1）"),
    Edge("telephone_area", ("numbering_area_code",), "telephone_area_version",
         ("numbering_area_code",), "||..|{", "numbering_area_code（現行は is_current = 1）"),
    # Census. One row per (year, municipality); 政令市 has its own row and its wards'.
    Edge("municipality", ("lg_code",), "census_population_municipality", ("lg_code",),
         "||..o{", "lg_code（census_year ごと）"),
    Edge("telephone_area", ("numbering_area_code",), "census_population_numbering_area",
         ("numbering_area_code",), "||..o{", "numbering_area_code"),
    Edge("census_population_age_summary", ("region_key", "nationality_raw", "sex_raw"),
         "census_population_age", ("region_key", "nationality_raw", "sex_raw"),
         "||..|{", "region_key + 国籍 + 性別"),
    Edge("census_population_age_summary", ("region_key",), "census_population_raw",
         ("region_key",), "|o..o{", "region_key"),
    # Lineage. Old codes are mostly abolished ones, absent from municipality.
    Edge("municipality", ("lg_code",), "municipality_lineage_edge", ("new_lg_code",),
         "|o..o{", "new_lg_code（現行コードのとき）"),
    Edge("municipality_change_raw", ("row_ordinal",), "municipality_lineage_edge",
         ("source_row_ordinals",), "}|..o{", "source_row_ordinals（; 区切り）"),
    # e-Stat small areas carry the 5-digit code of their 2020 boundary.
    Edge("municipality", ("jis_city_code",), "estat_small_area", ("jis_city_code",),
         "|o..o{", "jis_city_code（2020年の境界）"),
    # Provenance.
    Edge("source_snapshot", ("source_snapshot_id",), "match_run_input",
         ("source_snapshot_id",), "||..o{", "source_snapshot_id"),
    Edge("source_snapshot", ("source_snapshot_id",), "snapshot_license_artifact",
         ("source_snapshot_id",), "||..o{", "source_snapshot_id"),
    # The coordinate asset: one row per station / stop, every row kept.
    Edge("n02_station", ("n02_group_code",), "station_point", ("n02_group_code",),
         "||..||", "n02_group_code（1対1）"),
    Edge("p11_bus_stop", ("p11_stop_id",), "bus_stop_point", ("p11_stop_id",),
         "||..||", "p11_stop_id（1対1）"),
    Edge("source_snapshot", ("source_snapshot_id",), "station_point",
         ("source_snapshot_id",), "||..o{", "source_snapshot_id"),
    Edge("source_snapshot", ("source_snapshot_id",), "bus_stop_point",
         ("source_snapshot_id",), "||..o{", "source_snapshot_id"),
]


# Bridge columns that are never NULL, so the parent side is exactly one.
ALWAYS_SET = {"p11_stop_id", "n02_group_code", "line_name_raw", "match_run_id"}


@dataclass
class Table:
    name: str
    columns: list[tuple[str, str]]        # (name, type)
    pk: list[str]


def load_schema() -> tuple[dict[str, Table], list[Edge], str]:
    sql = SCHEMA.read_text(encoding="utf-8")
    m = re.search(r"抽出元ビルド:\s*([^\s。]+)", sql)
    build = m.group(1) if m else "unknown"
    db = sqlite3.connect(":memory:")
    db.executescript(sql)
    names = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    tables: dict[str, Table] = {}
    edges: list[Edge] = []
    for n in names:
        info = db.execute(f'PRAGMA table_info("{n}")').fetchall()
        cols = [(r[1], r[2] or "TEXT") for r in info]
        pk = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
        tables[n] = Table(n, cols, pk)
        fks: dict[int, list[tuple]] = {}
        for r in db.execute(f'PRAGMA foreign_key_list("{n}")').fetchall():
            fks.setdefault(r[0], []).append(r)
        for _, rows in sorted(fks.items()):
            rows.sort(key=lambda r: r[1])
            parent = rows[0][2]
            child_cols = tuple(r[3] for r in rows)
            parent_cols = tuple(r[4] for r in rows)
            # A bridge's endpoint is NULL on an unresolved row, so the parent
            # side is zero-or-one there. The side a bridge starts from (the
            # stop, the station, the line) and its match run are always set.
            nullable = n.startswith("bridge_") and not set(child_cols) & ALWAYS_SET
            rel = "|o--o{" if nullable else "||--o{"
            label = " + ".join(child_cols)
            if child_cols != parent_cols:
                label = f"{label} → {' + '.join(parent_cols)}"
            edges.append(Edge(parent, parent_cols, n, child_cols, rel, label))
    types = {"String": "TEXT", "Float64": "REAL", "Int64": "INTEGER"}
    for n, schema in POINT_TABLES.items():
        tables[n] = Table(
            n, [(c, types[str(t)]) for c, t in schema.items()], [ASSET_KEYS[n]]
        )
    return tables, edges, build


def validate(tables: dict[str, Table], edges: list[Edge]) -> list[str]:
    problems = []
    placed = [t for _, _, ts in PAGES for t in ts]
    for t in sorted(set(tables) - set(placed)):
        problems.append(f"table {t} is in schema.sql but on no page of PAGES")
    for t in sorted(set(placed) - set(tables)):
        problems.append(f"PAGES lists {t}, which schema.sql does not have")
    for t in sorted({t for t in placed if placed.count(t) > 1}):
        problems.append(f"table {t} is on more than one page")
    for t in sorted(set(tables) - set(LABELS)):
        problems.append(f"table {t} has no entry in LABELS")
    for e in LOGICAL:
        for tab, cols in ((e.parent, e.parent_cols), (e.child, e.child_cols)):
            if tab not in tables:
                problems.append(f"LOGICAL names table {tab}, which does not exist")
                continue
            have = {c for c, _ in tables[tab].columns}
            for c in cols:
                if c not in have:
                    problems.append(f"LOGICAL names {tab}.{c}, which does not exist")
    return problems


def _entity_header(name: str, note: str = "") -> str:
    label = f"{name}　{LABELS.get(name, '')}{note}"
    return f'  {name}["{label}"]'


def _attr(table: Table, col: str, typ: str, fk_targets: dict[str, list[str]]) -> str:
    keys = []
    if col in table.pk:
        keys.append("PK")
    if any(not t.startswith("…") for t in fk_targets.get(col, [])):
        keys.append("FK")
    targets = [t.lstrip("…") for t in fk_targets.get(col, [])]
    comment = f' "→ {", ".join(sorted(set(targets)))}"' if targets else ""
    return f"    {typ.replace(' ', '_')} {col} {', '.join(keys)}{comment}".rstrip()


LEGEND = "実線 = 宣言された FOREIGN KEY ／ 点線 = 宣言のない結合"


def render_overview(build: str, tables: dict[str, Table], edges: list[Edge]) -> str:
    """Every table as a box, grouped by page, arrows from referrer to referenced.

    An erDiagram of 44 entities is unreadable: municipality alone is referenced
    by 17 tables, and the layout puts every referrer in one row 12,000 px wide.
    A flowchart with one subgraph per page keeps each area together, and the
    columns are left to the detail pages.
    """
    lines = [
        "---",
        f"title: jpac {build}　全体図（SQLite {len(tables) - len(POINT_TABLES)} テーブル"
        f" + 座標アセット {len(POINT_TABLES)}）　矢印は「参照する側 → される側」"
        f"　{LEGEND}　列は各ページ（ブリッジ7本 → match_run の線は省略）",
        "---",
        "flowchart LR",
    ]
    for i, (slug, title, ts) in enumerate(PAGES):
        lines.append(f'  subgraph {slug.replace("-", "_")}["p.{i + 2} {title}"]')
        for t in ts:
            lines.append(f'    {t}["{t}<br/>{LABELS[t]}"]')
        lines.append("  end")
    seen = set()
    for e in edges:
        # Seven bridges point at match_run; drawn here they run the full
        # height of the page and bury everything else. p.7 shows them.
        if e.parent == "match_run" and e.child.startswith("bridge_"):
            continue
        key = (e.child, e.parent, ".." in e.rel)
        if key in seen:  # address_lineage has two keys into address_entity
            continue
        seen.add(key)
        arrow = "-.->" if ".." in e.rel else "-->"
        lines.append(f"  {e.child} {arrow} {e.parent}")
    return "\n".join(lines) + "\n"


def render_page(title: str, build: str, tables: dict[str, Table], edges: list[Edge],
                full: list[str], page_of: dict[str, tuple[int, str]]) -> str:
    """Mermaid erDiagram for one detail page.

    A page draws the edges its own tables make: to each other, and out to the
    tables they reference. The reverse (other pages' tables referencing this
    one) is drawn on those pages; pulling every referrer of municipality onto
    its page would put 17 neighbours on it.
    """
    page_edges = [e for e in edges if e.child in full]
    shown = list(full) + sorted({e.parent for e in page_edges} - set(full))
    lines = [
        "---",
        f"title: jpac {build}　{title}　（{LEGEND}）",
        "---",
        "erDiagram",
    ]
    for e in page_edges:
        lines.append(f'  {e.parent} {e.rel} {e.child} : "{e.label}"')
    for name in shown:
        t = tables[name]
        # fk_targets: column -> tables it points at. Declared FKs are plain,
        # logical joins are marked with "…" so _attr does not call them FK.
        fk_targets: dict[str, list[str]] = {}
        join_cols: set[str] = set()
        for e in page_edges:
            dashed = ".." in e.rel
            if e.child == name:
                for c in e.child_cols:
                    fk_targets.setdefault(c, []).append(("…" if dashed else "") + e.parent)
                    join_cols.add(c)
            if e.parent == name:
                join_cols.update(e.parent_cols)
        if name in full:
            cols = t.columns
            lines.append(_entity_header(name) + " {")
        else:
            # A neighbour from another page: only the columns this page joins on.
            keep = set(t.pk) | join_cols
            cols = [(c, ty) for c, ty in t.columns if c in keep]
            n, ptitle = page_of[name]
            lines.append(_entity_header(name, f"（→ p.{n} {ptitle}）") + " {")
        for c, ty in cols:
            lines.append(_attr(t, c, ty, fk_targets))
        lines.append("  }")
    return "\n".join(lines) + "\n"


def generate() -> dict[str, str]:
    tables, declared, build = load_schema()
    problems = validate(tables, declared)
    if problems:
        raise SystemExit("ER definition is out of step with schema.sql:\n  ! "
                         + "\n  ! ".join(problems))
    edges = declared + LOGICAL
    # p.1 is the overview, so the detail pages are p.2 onward.
    page_of = {t: (i + 2, title) for i, (_, title, ts) in enumerate(PAGES) for t in ts}
    out = {"00-overview.mmd": render_overview(build, tables, edges)}
    for slug, title, ts in PAGES:
        out[f"{slug}.mmd"] = render_page(title, build, tables, edges, ts, page_of)
    return out


def _mmdc(npx: str, src: Path, out: Path, *extra: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "mermaid.json"
        # The overview has 44 entities; the default 50 KB text limit and edge
        # limit are for hand-written diagrams, not this one.
        cfg.write_text(json.dumps({**CONFIG, "maxTextSize": 500_000, "maxEdges": 2000}),
                       encoding="utf-8")
        result = subprocess.run(
            [npx, "-y", "-p", MERMAID_CLI, "mmdc", "-i", str(src), "-o", str(out),
             "-c", str(cfg), "-b", "white", *extra],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    if result.returncode != 0 or not out.exists():
        raise SystemExit(f"mermaid failed for {src.name}:\n{result.stdout}\n{result.stderr}")


def build() -> int:
    from pypdf import PdfReader, PdfWriter

    npx = _npx()
    if npx is None:
        print("ERROR: npx not found; mermaid-cli needs Node.js")
        return 1
    pages = generate()
    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob("*.mmd"):
        if stale.name not in pages:
            stale.unlink()
    for name, text in pages.items():
        (OUT / name).write_text(text, encoding="utf-8", newline="\n")

    titles = ["全体図"] + [title for _, title, _ in PAGES]
    writer = PdfWriter()
    with tempfile.TemporaryDirectory() as tmp:
        for i, name in enumerate(sorted(pages)):
            src = OUT / name
            pdf = Path(tmp) / f"{src.stem}.pdf"
            # mmdc lays the chart out at the viewport width, 800 px by default,
            # which shrinks a 3,000 px page to unreadable 6 pt text at 100 %.
            _mmdc(npx, src, pdf, "--pdfFit", "-w", "3000")
            start = len(writer.pages)
            writer.append(PdfReader(pdf))
            writer.add_outline_item(f"p.{i + 1} {titles[i]}", start)
            print(f"  {name} -> page {i + 1}")
        _mmdc(npx, OUT / "00-overview.mmd", OVERVIEW_SVG)
    writer.add_metadata({"/Title": "jp-address-crosswalk ER 図（全テーブル）"})
    writer.page_mode = "/UseOutlines"
    with PDF.open("wb") as f:
        writer.write(f)

    MANIFEST.write_text(json.dumps(
        {n: source_hash(OUT / n) for n in sorted(pages)}, indent=2, sort_keys=True
    ) + "\n", encoding="utf-8")
    print(f"wrote {PDF.relative_to(ROOT)} ({len(pages)} pages) and "
          f"{OVERVIEW_SVG.relative_to(ROOT)}")
    return 0


def check() -> int:
    problems = []
    try:
        pages = generate()
    except SystemExit as exc:
        print(exc)
        return 1
    recorded = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    for name, text in pages.items():
        path = OUT / name
        if not path.exists():
            problems.append(f"{name} is missing")
        elif path.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
            problems.append(f"{name} is not what schema.sql generates now")
        elif recorded.get(name) != source_hash(path):
            problems.append(f"the PDF/SVG were rendered from an older {name}")
    for name in sorted(set(recorded) - set(pages)):
        problems.append(f"manifest.json still lists {name}, which is no longer generated")
    for out in (PDF, OVERVIEW_SVG):
        if not out.exists():
            problems.append(f"{out.name} is missing")
    if problems:
        print("ER diagram is out of date. Run: py -3.12 tools/build_er.py")
        for p in problems:
            print("  !", p)
        return 1
    print(f"ER diagram is up to date ({len(pages)} pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(check() if "--check" in sys.argv else build())
