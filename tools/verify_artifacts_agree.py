"""The three shipped artifacts must give the same answers.

Users pick one of Parquet, SQLite or CSV.gz. If they disagree, two people doing
the same lookup get different results and nothing else in the test suite would
notice: every check so far validates the normalized tables, not the equivalence
of the files built from them.

Also checks the manifest, and scans for mojibake — CP932 and EUC-JP are decoded
by hand in this pipeline, so a decoding error would surface as U+FFFD or as
plausible-looking wrong kanji rather than as an exception.

And it checks `docs/schema.sql`, which is published as the definition of what
consumers receive. A hand-maintained copy of a schema goes stale silently, so it
is executed into a scratch database and compared object by object with the
artifact it claims to describe.

Run:  jpac verify artifacts   (or: py -3.12 tools/verify_artifacts_agree.py)
"""

from __future__ import annotations

import sys as _sys

# These reports print Japanese and typographic dashes. A Windows console is
# cp932 by default, where an unencodable character raises UnicodeEncodeError
# and kills the run halfway through. Degrade the character, not the report.
if hasattr(_sys.stdout, "reconfigure") and (_sys.stdout.encoding or "").lower() not in (
    "utf-8", "utf8"
):
    _sys.stdout.reconfigure(errors="replace")
    if hasattr(_sys.stderr, "reconfigure"):
        _sys.stderr.reconfigure(errors="replace")


import gzip
import hashlib
import io
import re
import sqlite3
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PQ = DIST / "parquet"

failures: list[str] = []
notes: list[str] = []


def ok(label: str, passed: bool, detail: str = "") -> None:
    """A defect in the artifacts. Fails the run."""
    print(f"  [{'OK ' if passed else 'BAD'}] {label}" + (f"  {detail}" if detail else ""))
    if not passed:
        failures.append(f"{label}: {detail}")


def note(label: str, clean: bool, detail: str = "") -> None:
    """Documentation quality. Reported, never a release gate.

    An example query that matches nothing is worth seeing, but a release must
    not be blocked because `07_ambiguous.sql` legitimately found no ambiguous
    matches this month.
    """
    print(f"  [{'OK ' if clean else 'note'}] {label}" + (f"  {detail}" if detail else ""))
    if not clean:
        notes.append(f"{label}: {detail}")


# ------------------------------------------------------- 1. Parquet vs CSV.gz
print("\n1. Flat view: Parquet vs CSV.gz")
flat_pq = pl.read_parquet(DIST / "jp_address_crosswalk.parquet")
with gzip.open(DIST / "jp_address_crosswalk.csv.gz", "rb") as fh:
    flat_csv = pl.read_csv(io.BytesIO(fh.read()), infer_schema_length=0)

ok("row count", flat_pq.height == flat_csv.height,
   f"parquet {flat_pq.height:,} csv {flat_csv.height:,}")
ok("column set", list(flat_pq.columns) == list(flat_csv.columns),
   f"{len(flat_pq.columns)} vs {len(flat_csv.columns)} columns")

if flat_pq.height == flat_csv.height and list(flat_pq.columns) == list(flat_csv.columns):
    mismatched = []
    for c in flat_pq.columns:
        a = flat_pq[c].cast(pl.Utf8).fill_null("\x00").to_list()
        b = flat_csv[c].cast(pl.Utf8).fill_null("\x00").to_list()
        # CSV has no types, so booleans and floats round-trip as text.
        if flat_pq.schema[c] == pl.Boolean:
            a = ["true" if x == "true" else "false" for x in a]
            b = ["true" if str(x).lower() in ("true", "1") else "false" for x in b]
        if flat_pq.schema[c] in (pl.Float64, pl.Float32):
            a = [f"{float(x):.9g}" if x != "\x00" else x for x in a]
            b = [f"{float(x):.9g}" if x != "\x00" else x for x in b]
        n = sum(1 for x, y in zip(a, b, strict=False) if x != y)
        if n:
            mismatched.append(f"{c}({n})")
    ok("every cell identical", not mismatched, ", ".join(mismatched[:5]) or "all match")

# ------------------------------------------------------ 2. Parquet vs SQLite
print("\n2. Normalized tables: Parquet vs SQLite")
conn = sqlite3.connect(DIST / "jp_address_crosswalk.sqlite")
try:
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    pq_tables = {p.stem for p in PQ.glob("*.parquet")}
    ok("same set of tables", pq_tables <= tables,
       f"missing in sqlite: {sorted(pq_tables - tables) or 'none'}")

    bad_counts, bad_cells = [], []
    for name in sorted(pq_tables & tables):
        df = pl.read_parquet(PQ / f"{name}.parquet")
        n = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        if n != df.height:
            bad_counts.append(f"{name}({df.height:,} vs {n:,})")
            continue
        if not df.height:
            continue
        # Compare the whole table as a sorted multiset of rows.
        cols = [c for c in df.columns]
        quoted = ", ".join(f'"{c}"' for c in cols)
        rows_db = conn.execute(f'SELECT {quoted} FROM "{name}"').fetchall()
        bool_ix = {i for i, c in enumerate(cols) if df.schema[c] == pl.Boolean}

        def to_txt(v, i: int, _bool_ix=bool_ix) -> str:
            # SQLite stores Boolean as INTEGER 0/1 by design
            # (docs/DB_SCHEMA.md 1), so both sides are normalised to 0/1
            # rather than one side being rendered as "true"/"false".
            if v is None:
                return chr(0)
            if i in _bool_ix:
                return "1" if v in (True, 1, "1") else "0"
            return str(v)

        a = sorted(
            chr(31).join(to_txt(v, i) for i, v in enumerate(r))
            for r in df.iter_rows()
        )
        b = sorted(
            chr(31).join(to_txt(v, i) for i, v in enumerate(r)) for r in rows_db
        )
        diff = sum(1 for x, y in zip(a, b, strict=False) if x != y)
        if diff:
            bad_cells.append(f"{name}({diff})")
    ok("row counts match in every table", not bad_counts,
       ", ".join(bad_counts) or f"{len(pq_tables & tables)} tables")
    ok("every row identical in every table", not bad_cells,
       ", ".join(bad_cells[:5]) or "all match")

    # 3. The views users are pointed at must actually work and agree.
    print("\n3. SQLite views")
    n_view = conn.execute("SELECT COUNT(*) FROM address_crosswalk").fetchone()[0]
    n_all = conn.execute("SELECT COUNT(*) FROM address_crosswalk_all").fetchone()[0]
    ok("address_crosswalk returns rows", n_view > 0, f"{n_view:,}")
    ok("accepted view <= all-evidence view", n_view <= n_all,
       f"{n_view:,} <= {n_all:,}")
    ok("SQL view row count == Parquet flat view", n_view == flat_pq.height,
       f"sqlite {n_view:,} parquet {flat_pq.height:,}")
    ok("unmatched_records view works",
       conn.execute("SELECT COUNT(*) FROM unmatched_records").fetchone()[0] > 0)

    # 3b. Every query README points users at must still run. Removing a column
    # from a view is invisible in the tables and breaks these silently, so they
    # are executed rather than eyeballed. They are written for the sqlite3 CLI,
    # so the dot-commands are translated: `.parameter set :x 'v'` becomes the
    # binding for :x.
    print("\n3b. Shipped example queries (docs/queries/*.sql)")
    broken: list[str] = []
    empty: list[str] = []
    ran = 0
    for q in sorted((ROOT / "docs" / "queries").glob("*.sql")):
        params: dict[str, str] = {}
        body = []
        for line in q.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith(".parameter set"):
                _, _, rest = s.partition(".parameter set")
                name, _, value = rest.strip().partition(" ")
                params[name.lstrip(":")] = value.strip().strip("'\"")
            elif s.startswith("."):
                continue
            else:
                body.append(line)
        # One file may hold several statements, and a broken second statement
        # is just as broken, so each is executed rather than only the first.
        # Splitting on ";" alone is wrong — a semicolon inside a comment ends a
        # statement that has not ended — so SQLite is asked where the boundaries
        # are instead of guessing.
        stmts, buf = [], ""
        for line in body:
            buf += line + "\n"
            if sqlite3.complete_statement(buf):
                stmts.append(buf.strip())
                buf = ""
        if buf.strip():
            stmts.append(buf.strip())
        if not stmts:
            continue
        try:
            got = 0
            for s in stmts:
                got += len(conn.execute(s, params).fetchmany(5))
            ran += 1
            # Executing is not the same as demonstrating. A worked example whose
            # parameter matches nothing teaches the reader nothing and hides a
            # column rename behind an empty result.
            if not got:
                empty.append(q.name)
        except Exception as exc:  # noqa: BLE001 - the message is the finding
            broken.append(f"{q.name}: {exc}")
    note("no documented example returns an empty result", not empty,
         ", ".join(empty))
    ok("every documented example query executes", not broken,
       "; ".join(broken[:3]) or f"{ran} queries")

    # 3c. docs/schema.sql is published as "this is what you get". It is derived
    # from the artifact rather than written by hand, but derived-once is not the
    # same as still-true: a column added to an export would leave the published
    # DDL describing a database nobody ships. So it is replayed into a scratch
    # database and compared object by object.
    #
    # The comparison ignores whitespace and identifier quoting only. Those are
    # the two things the pretty-printing in schema.sql is allowed to change;
    # anything else — a column, a type, a CHECK, an index — is a real drift.
    print("\n3c. docs/schema.sql vs the shipped schema")
    schema_sql = ROOT / "docs" / "schema.sql"

    def _canon(sql: str) -> str:
        sql = re.sub(r"\s+", " ", sql).replace('"', "").strip().rstrip(";")
        return re.sub(r"\s*([(),])\s*", r"\1", sql)

    def _objects(cur: sqlite3.Cursor) -> dict[str, str]:
        return {
            name: _canon(sql)
            for name, sql in cur.execute(
                "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"
            )
        }

    if not schema_sql.exists():
        ok("docs/schema.sql exists", False, "file is missing")
    else:
        scratch = sqlite3.connect(":memory:")
        try:
            scratch.executescript(schema_sql.read_text(encoding="utf-8"))
        except sqlite3.Error as exc:  # noqa: BLE001 - the message is the finding
            ok("docs/schema.sql is executable SQL", False, str(exc))
        else:
            shipped = _objects(conn.cursor())
            published = _objects(scratch.cursor())
            missing = sorted(set(shipped) - set(published))
            extra = sorted(set(published) - set(shipped))
            differs = sorted(
                n for n in shipped.keys() & published.keys()
                if shipped[n] != published[n]
            )
            ok("docs/schema.sql documents every shipped object", not missing,
               ", ".join(missing[:5]))
            ok("docs/schema.sql documents nothing that is not shipped", not extra,
               ", ".join(extra[:5]))
            ok("every published definition matches the shipped one", not differs,
               ", ".join(differs[:5]) or f"{len(shipped)} objects")
        finally:
            scratch.close()
finally:
    conn.close()

# --------------------------------------------------------------- 4. Manifest
print("\n4. SHA256SUMS manifest")
manifest = (DIST / "SHA256SUMS").read_text(encoding="utf-8").strip().splitlines()
bad_sum = []
for line in manifest:
    digest, name = line.split("  ", 1)
    f = DIST / name
    if not f.exists():
        bad_sum.append(f"{name} missing")
        continue
    h = hashlib.sha256()
    with f.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != digest:
        bad_sum.append(f"{name} digest differs")
ok("every listed file matches its digest", not bad_sum,
   ", ".join(bad_sum) or f"{len(manifest)} files")

# --------------------------------------------------------------- 5. Mojibake
print("\n5. Encoding integrity")
scanned = 0
mojibake = []
for p in sorted(PQ.glob("*.parquet")):
    df = pl.read_parquet(p)
    for c in df.columns:
        if df.schema[c] != pl.Utf8 or not df.height:
            continue
        scanned += 1
        bad = df.filter(
            pl.col(c).is_not_null()
            & (pl.col(c).str.contains("�") | pl.col(c).str.contains("縺|繧|繝|譁|�"))
        )
        if bad.height:
            mojibake.append(f"{p.stem}.{c}({bad.height})")
ok("no replacement characters or CP932-as-UTF8 mojibake", not mojibake,
   ", ".join(mojibake[:5]) or f"{scanned} text columns scanned")

print("\n" + "=" * 78)
print(f"RESULT: {len(failures)} problem(s)")
for f in failures:
    print("  !", f)
if notes:
    print(f"NOTES (not release gates): {len(notes)}")
    for n in notes:
        print("  -", n)
sys.exit(1 if failures else 0)
