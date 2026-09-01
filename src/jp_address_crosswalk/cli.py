"""jpac CLI (spec §35, §36).

Every command answers one question and says what to run next. Machine-readable
output is opt-in (``--json``); the default is a short human summary, because the
report a build produces is 123 threshold rows long and nobody reads that in a
terminal.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import typer

from . import __version__
from .errors import JpacError
from .logging_setup import configure, get_logger
from .pipeline import Paths, rebuild_offline, release_artifacts
from .pipeline import build as build_tables
from .pipeline import export as export_tables

app = typer.Typer(
    add_completion=False,
    help="Crosswalk between Japan's official address and area code systems.",
    no_args_is_help=True,
)
log = get_logger(__name__)

ROOT_OPT = typer.Option(
    None, "--root", metavar="PATH",
    help="Repository root to work in (default: current directory)",
)
VERBOSE_OPT = typer.Option(False, "--verbose", "-v", help="Debug-level logging")


def _paths(root: str | None) -> Paths:
    return Paths(Path(root).resolve() if root else Path.cwd())


def _setup(verbose: bool) -> None:
    # force=True: importing the pipeline configures logging at module import,
    # so without it every command ran at INFO with JSON output no matter what
    # the flags said.
    configure(
        level="DEBUG" if verbose else "INFO",
        json_output=not sys.stderr.isatty(),
        force=True,
    )


def _fail(exc: JpacError) -> None:
    """Named error code to stderr, non-zero exit: CI reads this."""
    typer.echo(str(exc), err=True)
    raise typer.Exit(code=2)


def _abort(message: str, hint: str = "", code: int = 1) -> None:
    typer.echo(message, err=True)
    if hint:
        typer.echo(f"  -> {hint}", err=True)
    raise typer.Exit(code=code)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", help="Print the code version and exit", is_eager=True
    ),
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@app.command()
def version() -> None:
    """Print the code version."""
    typer.echo(__version__)


def _build_summary(report: dict, paths: Paths) -> str:
    thresholds = report.get("thresholds", [])
    failed = [t for t in thresholds if t.get("status") == "fail"]
    tables = report.get("tables", {})
    rows = sum(t.get("row_count", 0) for t in tables.values())
    lines = [
        "build: {} {}+{}".format(
            "PASS" if report.get("passed") else "FAIL",
            report.get("code_version", "?"),
            report.get("data_version", "?"),
        ),
        f"  tables    {len(tables)} measured ({rows:,} rows)",
        f"  bridges   {len(report.get('bridges', {}))}",
        f"  gates     {len(thresholds)} checks, {len(failed)} failed",
        f"  artifacts {paths.dist}  (SHA256SUMS で検証できます)",
        f"  reports   {paths.dist / 'QUALITY_REPORT.md'}",
        f"            {paths.dist / 'DIFF_REPORT.md'}",
    ]
    for t in failed[:10]:
        lines.append(
            "  FAIL      {}: observed={} limit={}".format(
                t.get("check"), t.get("observed"), t.get("limit")
            )
        )
    if len(failed) > 10:
        lines.append(f"  ... and {len(failed) - 10} more (see QUALITY_REPORT.md)")
    return "\n".join(lines)


@app.command()
def build(
    root: str = ROOT_OPT,
    lenient: bool = typer.Option(
        False, "--lenient",
        help="Report gate failures without exiting. Never release the result",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Print the full quality report as JSON instead"
    ),
    verbose: bool = VERBOSE_OPT,
) -> None:
    """Rebuild every table and artifact from the payloads in `data/raw/`.

    The build never touches the network. Given `data/raw/`, the committed
    config and the identity ledger, the whole database is reconstructed.
    Acquiring those payloads is managed internally and is not part of this
    repository; see README.
    """
    _setup(verbose)
    p = _paths(root)
    try:
        outcome = rebuild_offline(p, strict=not lenient)
        tables = build_tables(p, outcome, strict=not lenient)
        report = export_tables(p, tables, outcome, strict=not lenient)
    except JpacError as exc:
        _fail(exc)
    if as_json:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo(_build_summary(report, p))
    if not report.get("passed"):
        # --lenient got us here; the artifacts exist but are not releasable.
        typer.echo(
            "\ngates failed: this build must not be released "
            "(baseline and identity ledger were left untouched)",
            err=True,
        )
        raise typer.Exit(code=2)


@app.command()
def validate(
    root: str = ROOT_OPT,
    verbose: bool = VERBOSE_OPT,
) -> None:
    """Re-check invariants against the exported Parquet tables."""
    _setup(verbose)
    import polars as pl

    from .build.common import assert_bridge_invariants
    from .build.quality import BRIDGES

    p = _paths(root)
    problems: list[str] = []
    checked = 0
    for name in BRIDGES:
        path = p.parquet / f"{name}.parquet"
        if path.exists():
            checked += 1
            problems += assert_bridge_invariants(pl.read_parquet(path), name)
    if not checked:
        # Reporting OK for an empty directory is worse than reporting nothing:
        # it reads as "the data is fine" when there is no data.
        _abort(
            f"validate: no bridge tables in {p.parquet}",
            "run `jpac build` first",
        )
    if problems:
        for msg in problems:
            typer.echo(msg, err=True)
        typer.echo(f"validate: {len(problems)} problems in {checked} bridges", err=True)
        raise typer.Exit(code=2)
    typer.echo(f"validate: OK ({checked}/{len(BRIDGES)} bridges checked)")


@app.command()
def diff(
    root: str = ROOT_OPT,
    as_json: bool = typer.Option(False, "--json", help="Print diff_report.json instead"),
) -> None:
    """Show the diff report produced by the last build."""
    p = _paths(root)
    path = p.dist / ("diff_report.json" if as_json else "DIFF_REPORT.md")
    if not path.exists():
        _abort(f"diff: {path.name} not found", "run `jpac build` first")
    typer.echo(path.read_text(encoding="utf-8"))


@app.command()
def export(
    root: str = ROOT_OPT,
    verbose: bool = VERBOSE_OPT,
) -> None:
    """Re-export the three artifacts from the Parquet tables already on disk."""
    _setup(verbose)
    import polars as pl

    from .export import writers

    p = _paths(root)
    tables = {
        f.stem: pl.read_parquet(f) for f in sorted(p.parquet.glob("*.parquet"))
    }
    if not tables:
        _abort(f"export: no parquet tables in {p.parquet}", "run `jpac build` first")
    flat = writers.build_flat_view(tables, accepted_only=True)
    flat_all = writers.build_flat_view(tables, accepted_only=False)
    flat.write_parquet(p.dist / "jp_address_crosswalk.parquet", compression="zstd")
    writers.write_sqlite(tables, flat, flat_all, p.dist / "jp_address_crosswalk.sqlite")
    writers.write_csv_gz(flat, p.dist / "jp_address_crosswalk.csv.gz")
    # Rewriting the artifacts without rewriting their digests would leave a
    # SHA256SUMS that describes the previous export, which is exactly the file
    # consumers are told to trust.
    writers.write_sha256sums(release_artifacts(p), p.dist / "SHA256SUMS")
    typer.echo(f"export: OK ({flat.height:,} flat rows, SHA256SUMS rewritten)")


@app.command()
def baseline(
    root: str = ROOT_OPT,
    verbose: bool = VERBOSE_OPT,
) -> None:
    """Write expected-schema baselines. Never produces a release.

    Kept separate from `build` on purpose: if a release build could write its
    own baseline, a truncated payload would install itself as the reference.
    """
    _setup(verbose)
    p = _paths(root)
    p.expected_schema.mkdir(parents=True, exist_ok=True)
    kept = {f.name: f.read_bytes() for f in p.expected_schema.glob("*.yml")}
    for f in p.expected_schema.glob("*.yml"):
        f.unlink()
    done = False
    try:
        rebuild_offline(p, strict=True, allow_new_baselines=True)
        done = True
    except JpacError as exc:
        _fail(exc)
    finally:
        # Anything that stops the regeneration — a named error, a parser raising
        # ValueError, Ctrl-C — used to leave the repository with no baselines at
        # all, which turns a bad payload into a lost gate.
        if not done:
            for name, blob in kept.items():
                (p.expected_schema / name).write_bytes(blob)
            typer.echo(
                f"baseline: failed, restored {len(kept)} previous schema files",
                err=True,
            )
    written = sorted(f.name for f in p.expected_schema.glob("*.yml"))
    typer.echo(f"baseline: wrote {len(written)} schema files to {p.expected_schema}")


# ------------------------------------------------------------------- verify

# Each entry: the script, one line of what it answers, and whether it needs a
# second build to compare against.
VERIFY_CHECKS: dict[str, tuple[str, str]] = {
    "sources": ("reconcile_sources.py", "全入力行が DB に届いたか（行落ち検出）"),
    "fields": ("compare_all_fields.py", "全行・全列を元ファイルと突き合わせ"),
    "cross-source": ("verify_cross_source.py", "出典同士の食い違いと地理的な妥当性"),
    "distribution": ("verify_distribution.py", "分布とフラット表の再計算一致"),
    "artifacts": ("verify_artifacts_agree.py", "3成果物の一致と schema.sql の一致"),
    "diagrams": ("build_diagrams.py", "README の図が .mmd と同期しているか"),
    "idempotent": ("verify_idempotent.py", "2回のビルドが論理的に同一か（--against 必須）"),
}
DEFAULT_CHECKS = [c for c in VERIFY_CHECKS if c != "idempotent"]


def _tools_dir() -> Path:
    root = Path(__file__).resolve().parents[2]
    tools = root / "tools"
    if not tools.is_dir():
        _abort(
            "verify: tools/ not found",
            "検証ツールはリポジトリのチェックアウトからのみ実行できます",
            code=2,
        )
    return tools


@app.command()
def verify(
    check: str = typer.Argument(
        None,
        metavar="[CHECK]",
        help="実行する検査。省略すると idempotent 以外をすべて実行します",
    ),
    against: str = typer.Option(
        None, "--against", metavar="PATH",
        help="idempotent 用: 1回目のビルドの dist/parquet を控えたディレクトリ",
    ),
    list_checks: bool = typer.Option(False, "--list", help="検査の一覧を表示して終了"),
) -> None:
    """Check the built data against the original payloads and against itself.

    These read the raw payloads again, independently of the pipeline, so they
    need both `data/raw/` and a completed build. They are not run by CI, which
    has neither.
    """
    if list_checks:
        for name, (script, what) in VERIFY_CHECKS.items():
            typer.echo(f"{name:<14} {what}  (tools/{script})")
        return

    if check and check not in VERIFY_CHECKS:
        _abort(
            f"verify: unknown check {check!r}",
            "`jpac verify --list` で一覧を表示できます",
            code=2,
        )
    if check == "idempotent" and not against:
        _abort(
            "verify idempotent: --against が必要です",
            "jpac build → cp -r dist/parquet <dir> → jpac build → "
            "jpac verify idempotent --against <dir>",
            code=2,
        )
    if against and check != "idempotent":
        _abort("verify: --against は idempotent 専用です", code=2)

    tools = _tools_dir()
    # The scripts resolve their own repository root from their location, so
    # they always verify the checkout they ship with. Say which one that is.
    typer.echo(f"verify: {tools.parent}")

    selected = [check] if check else DEFAULT_CHECKS
    results: list[tuple[str, int]] = []
    for name in selected:
        script, what = VERIFY_CHECKS[name]
        argv = [sys.executable, str(tools / script)]
        if name == "idempotent":
            argv.append(str(Path(against).resolve()))
        if name == "diagrams":
            argv.append("--check")
        typer.echo(f"\n=== {name}: {what}")
        results.append((name, subprocess.run(argv, cwd=tools.parent).returncode))

    failed = [n for n, rc in results if rc != 0]
    typer.echo("\n" + "-" * 60)
    for name, rc in results:
        typer.echo(f"  {'OK ' if rc == 0 else 'BAD'}  {name}")
    if failed:
        typer.echo(f"verify: {len(failed)} failed ({', '.join(failed)})", err=True)
        raise typer.Exit(code=2)
    typer.echo(f"verify: OK ({len(results)} checks)")


if __name__ == "__main__":
    app()
