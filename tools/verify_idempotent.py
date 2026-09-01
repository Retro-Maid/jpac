"""Two builds from the same inputs must produce the same logical data.

`docs/ARCHITECTURE.md` §6 claims logical — not byte — reproducibility, and is
explicit about why: `observed_from`, `created_at` and `updated_at` come from the
clock. This checks the claim that is actually made, and reports separately on
the fields that keep it from being a byte claim, so the gap stays visible
instead of being quietly absorbed.

Usage:
    jpac build                          # build 1
    cp -r dist/parquet <run_a>
    jpac build                          # build 2
    jpac verify idempotent --against <run_a>
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


import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
PQ = ROOT / "dist" / "parquet"

# Populated from the clock at build time, so they differ between runs by design.
WALL_CLOCK = {"observed_from", "observed_to", "created_at", "updated_at",
              "started_at", "downloaded_at", "built_at", "observed_at"}

failures: list[str] = []


def ok(label: str, passed: bool, detail: str = "") -> None:
    print(f"  [{'OK ' if passed else 'BAD'}] {label}" + (f"  {detail}" if detail else ""))
    if not passed:
        failures.append(f"{label}: {detail}")


if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(2)

RUN_A = Path(sys.argv[1])
a_files = {p.stem for p in RUN_A.glob("*.parquet")}
b_files = {p.stem for p in PQ.glob("*.parquet")}

print(f"\nrun A: {RUN_A}  ({len(a_files)} tables)")
print(f"run B: {PQ}  ({len(b_files)} tables)")
ok("same set of tables", a_files == b_files,
   f"only in A: {sorted(a_files - b_files)}, only in B: {sorted(b_files - a_files)}")

clock_only = []
for name in sorted(a_files & b_files):
    a = pl.read_parquet(RUN_A / f"{name}.parquet")
    b = pl.read_parquet(PQ / f"{name}.parquet")

    if a.height != b.height:
        ok(f"{name}: row count", False, f"{a.height:,} vs {b.height:,}")
        continue
    if list(a.columns) != list(b.columns):
        ok(f"{name}: columns", False, "column set differs")
        continue
    if not a.height:
        continue

    stable = [c for c in a.columns if c not in WALL_CLOCK]
    key = lambda df, cols: sorted(  # noqa: E731
        df.select(
            pl.concat_str(
                [pl.col(c).cast(pl.Utf8).fill_null(chr(0)) for c in cols],
                separator=chr(31),
            ).alias("_k")
        )["_k"].to_list()
    )
    diff = sum(
        1 for x, y in zip(key(a, stable), key(b, stable), strict=False) if x != y
    )
    ok(f"{name}: identical ignoring clock fields", diff == 0, f"{diff:,} rows differ")

    moving = [c for c in a.columns if c in WALL_CLOCK
              and a[c].cast(pl.Utf8).to_list() != b[c].cast(pl.Utf8).to_list()]
    if moving:
        clock_only.append(f"{name}({','.join(moving)})")

print("\nFields that differ purely because they come from the clock:")
if clock_only:
    for c in clock_only:
        print("  -", c)
    print("\n  This is why ARCHITECTURE.md claims logical rather than byte")
    print("  reproducibility. Deriving these from a persisted acquisition record")
    print("  instead would close the gap; see docs/LIMITATIONS.md item 10.")
else:
    print("  none — the two runs are byte-comparable")

# The identity ledger must be stable: ids are the project's contract with users.
led = ROOT / "identity" / "address_id_ledger.csv.gz"
if led.exists():
    import gzip

    with gzip.open(led, "rb") as fh:
        ids = pl.read_csv(fh.read(), schema_overrides={"address_id": pl.Utf8})
    addr_a = pl.read_parquet(RUN_A / "address.parquet")
    addr_b = pl.read_parquet(PQ / "address.parquet")
    ok("address_id set unchanged between runs",
       set(addr_a["address_id"]) == set(addr_b["address_id"]),
       f"A {addr_a.height:,} B {addr_b.height:,}")
    ok("every address is in the ledger",
       set(addr_b["address_id"]) <= set(ids["address_id"]))

print("\n" + "=" * 78)
print(f"RESULT: {len(failures)} problem(s)")
for f in failures:
    print("  !", f)
sys.exit(1 if failures else 0)
