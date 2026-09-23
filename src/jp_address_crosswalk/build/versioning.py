"""Slowly-changing-dimension carry-forward for the ``*_version`` tables.

`docs/DATA_MODEL.md` promises append-only version rows: a changed name adds a
version rather than overwriting the previous one. Building each version table
from the current snapshot alone would quietly break that promise on the second
monthly run — the first run's rows would simply be gone.

So each build reads the previous release's version table and merges:

* content unchanged  → keep the **previous** row, preserving its original
  ``observed_from``, so an untouched record does not appear to have been
  re-observed from scratch every month
* content changed    → close the previous row (``observed_to``, ``is_current=0``)
  and append the new one
* already-closed rows → carried through untouched

Nothing is ever deleted, and no history is invented: rows only start existing
when this project first observes them (spec §43).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import polars as pl

from ..errors import ValidationFailed
from ..logging_setup import get_logger

log = get_logger(__name__)

# Bookkeeping columns describe *when we looked*, not what the record says, so
# they are excluded from the content hash.
_METADATA = {
    "observed_from", "observed_to", "is_current", "source_snapshot_id",
    "first_observed_snapshot_id",
    "_content_hash",  # the hash itself is never part of what is hashed
}


def _content_hash_expr(df: pl.DataFrame, version_id_col: str) -> pl.Expr:
    cols = sorted(c for c in df.columns if c not in _METADATA and c != version_id_col)
    return (
        pl.concat_str([pl.col(c).fill_null("\x00") for c in cols], separator="\x1f")
        .map_elements(
            lambda s: hashlib.blake2s(s.encode("utf-8"), digest_size=8).hexdigest(),
            return_dtype=pl.Utf8,
        )
        .alias("_content_hash")
    )


def _with_version_ids(
    df: pl.DataFrame, table: str, key: list[str], version_id_col: str
) -> pl.DataFrame:
    """Content-address a version row over (table, key, content, interval).

    The interval belongs in the id: keying on content alone gave a superseded
    row and its identical-content replacement the same id, and the dedup then
    kept the closed one and dropped the live one — which is how every
    ``mlit_town_version`` row once ended up ``is_current=false``.

    Applied on every path, including the first release. If it were applied only
    when a previous release exists, the first release would publish ids in a
    different namespace and the second would silently replace all of them.
    """
    return df.with_columns(
        pl.concat_str(
            [
                pl.lit(table),
                *[pl.col(k).fill_null("\x00") for k in key],
                pl.col("_content_hash"),
                pl.col("observed_from").fill_null("\x00"),
                pl.col("observed_to").fill_null("open"),
            ],
            separator="|",
        )
        .map_elements(
            lambda s: "ver_"
            + hashlib.blake2s(s.encode("utf-8"), digest_size=12).hexdigest(),
            return_dtype=pl.Utf8,
        )
        .alias(version_id_col)
    )


def carry_forward(
    current: pl.DataFrame,
    previous_dir: Path | None,
    table: str,
    key: list[str],
    version_id_col: str,
    observed_from: str,
) -> pl.DataFrame:
    """Merge ``current`` into the previous release's version table."""
    if current.is_empty():
        return current

    current = current.with_columns(_content_hash_expr(current, version_id_col))

    def _ids(df: pl.DataFrame) -> pl.DataFrame:
        return _with_version_ids(df, table, key, version_id_col)

    prev_path = (previous_dir / f"{table}.parquet") if previous_dir else None
    if prev_path is None or not prev_path.exists():
        return _ids(current).drop("_content_hash")

    previous = pl.read_parquet(prev_path)
    if previous.is_empty() or version_id_col not in previous.columns:
        return _ids(current).drop("_content_hash")

    # Align columns: a schema change between releases must not silently drop data.
    missing_in_prev = [
        c for c in current.columns
        if c not in previous.columns and c != "_content_hash"
    ]
    for c in missing_in_prev:
        previous = previous.with_columns(pl.lit(None).cast(current.schema[c]).alias(c))
    extra_in_prev = [
        c for c in previous.columns if c not in current.columns and c != "_content_hash"
    ]
    if extra_in_prev:
        log.warning(
            "previous version table has columns the current build does not",
            table=table, columns=extra_in_prev,
        )
        previous = previous.drop(extra_in_prev)
    previous = previous.select(
        [c for c in current.columns if c != "_content_hash"]
    ).with_columns(_content_hash_expr(previous, version_id_col))

    previous = _ids(previous)
    current = _ids(current)

    closed = previous.filter(~pl.col("is_current").cast(pl.Boolean))
    live = previous.filter(pl.col("is_current").cast(pl.Boolean))

    key_expr = pl.concat_str([pl.col(k) for k in key], separator="").alias("_k")
    cur = current.with_columns(key_expr)
    live_k = live.with_columns(key_expr)

    # Vectorised: a per-key Python loop here is O(n^2) and takes minutes on the
    # 124k-row postal table.
    unchanged_keys: set[str] = set()
    if live_k.height:
        joined = cur.join(
            live_k.select(["_k", pl.col("_content_hash").alias("_prev_hash")]),
            on="_k", how="inner",
        )
        unchanged_keys = set(
            joined.filter(pl.col("_content_hash") == pl.col("_prev_hash"))["_k"].to_list()
        )

    live_k = live.with_columns(
        pl.concat_str([pl.col(k) for k in key], separator="\x1f").alias("_k")
    )
    # An unchanged row keeps its original observed_from and source_snapshot_id:
    # both record when and from what this version was first established, and
    # re-observing identical content is not a new version. The consequence is
    # that a *defect* in an earlier build persists through unchanged rows, which
    # is why a known-bad baseline is discarded rather than carried forward.
    kept = live_k.filter(pl.col("_k").is_in(list(unchanged_keys)))
    superseded = live_k.filter(~pl.col("_k").is_in(list(unchanged_keys))).with_columns(
        [
            # 版も同じ。区間は開いた日より前に閉じられない
            # （carry_forward_observations に同じ判断がある）。
            pl.max_horizontal(pl.lit(observed_from), pl.col("observed_from"))
            .alias("observed_to"),
            pl.lit(False).alias("is_current"),
        ]
    )
    added = cur.filter(~pl.col("_k").is_in(list(unchanged_keys)))

    # 交代する版は、前の版が閉じた日から始まる。閉じる日を「開いた日より前には
    # しない」と直しただけでは、**行と行のあいだ**に反転が残る（レビューで見つかった）:
    # 前の版が 2026-09-20 に閉じ、後継が 2026-09-17 に開くと、2つの版が
    # 09-17〜09-20 を同時に覆い、as-of の問い合わせが2行返す。
    #
    # 後継の観測日を前の版の閉じた日に合わせる。区間はちょうど接して重ならない
    # （slowly-changing dimension の普通の不変条件）。今回の観測日のほうが後なら
    # 何も起きない。
    if superseded.height and added.height:
        added = added.join(
            superseded.select(["_k", pl.col("observed_to").alias("_closed_at")]),
            on="_k", how="left",
        ).with_columns(
            pl.max_horizontal(
                pl.col("observed_from"), pl.col("_closed_at").fill_null("")
            ).alias("observed_from")
        ).drop("_closed_at")

    # Recomputed in place after observed_to is set, so a row that was just
    # closed cannot collide with its live replacement. with_columns replaces the
    # existing column and preserves position, which concat requires.
    if superseded.height:
        superseded = _ids(superseded)
    if added.height:
        # observed_from が動いた行は id も作り直す（id は区間を含む）。
        added = _ids(added)

    out = pl.concat(
        [
            closed.drop("_content_hash"),
            superseded.drop(["_content_hash", "_k"]),
            kept.drop(["_content_hash", "_k"]),
            added.drop(["_content_hash", "_k"]),
        ],
        how="vertical",
    ).unique(subset=[version_id_col], keep="first").sort([*key, "observed_from"])

    # Exactly one live version per entity, or the flat view silently picks one.
    live_per_key = (
        out.filter(pl.col("is_current"))
        .group_by(key)
        .agg(pl.len().alias("n"))
        .filter(pl.col("n") > 1)
    )
    if live_per_key.height:
        raise ValidationFailed(
            "entities with more than one current version",
            table=table, count=live_per_key.height,
            sample=live_per_key.head(3).to_dicts(),
        )

    log.info(
        "version table carried forward",
        table=table, previous=previous.height, current=current.height,
        unchanged=kept.height, superseded=superseded.height, added=added.height,
        total=out.height,
    )
    return out


def accumulate_events(
    current: pl.DataFrame,
    previous_dir: Path | None,
    table: str,
    id_col: str,
) -> pl.DataFrame:
    """Union an event log with the previous release's copy of itself.

    ``address_lineage`` and ``address_history`` record *events* — a rename, a
    retirement, a changed attribute — each detected by comparing this build with
    the one before it. Detection is therefore one-shot: the event appears in the
    build where it happened and in no later one, because by then the ledger and
    the previous release both already carry the new value. Rebuilding these
    tables from the current run alone was consequently losing every event the
    project had ever recorded, one release at a time
    (``docs/LIMITATIONS.md`` item 7).

    **On a collision the previous row wins.** The id is content-addressed over
    what the event says, not over when it was seen, so an id present in both
    releases is the same event — and the earlier row is the one that records
    when this project first observed it. Taking the current row instead would
    keep moving that date forward, which is the same mistake as re-dating an
    unchanged version row (see ``carry_forward``).

    Note for a future release: these tables now only grow, so
    ``row_count_change.<table>`` measures accumulated history rather than a
    snapshot. A release that detects many events will trip the 5% gate and want
    a signed approval in ``config/quality_thresholds.yml``. That is the gate
    doing its job — a spike in identity events is exactly what a human should
    look at — and not a reason to widen it.
    """
    prev_path = (previous_dir / f"{table}.parquet") if previous_dir else None
    if prev_path is None or not prev_path.exists():
        return current

    previous = pl.read_parquet(prev_path)
    if previous.is_empty() or id_col not in previous.columns:
        return current

    missing = [c for c in current.columns if c not in previous.columns]
    for c in missing:
        previous = previous.with_columns(pl.lit(None).cast(current.schema[c]).alias(c))
    extra = [c for c in previous.columns if c not in current.columns]
    if extra:
        # Same stance as carry_forward: say so rather than drop silently.
        log.warning(
            "previous event log has columns the current build does not",
            table=table, columns=extra,
        )
    previous = previous.select(current.columns)

    out = (
        pl.concat([previous, current], how="vertical")
        .unique(subset=[id_col], keep="first")   # previous is first: it wins
        .sort(id_col)
    )
    log.info(
        "event log accumulated", table=table, previous=previous.height,
        current=current.height, kept_from_previous=previous.height,
        new=out.height - previous.height, total=out.height,
    )
    return out


def carry_forward_observations(
    current: pl.DataFrame,
    previous_dir: Path | None,
    table: str,
    key: list[str],
    observed_from: str,
) -> pl.DataFrame:
    """Accumulate an observation log whose natural key already spans time.

    ``address_code`` is one row per (address, code type, code value) *while that
    value was observed*. ``docs/DB_SCHEMA.md`` documents its key as
    ``(address_id, code_type, code_value, observed_from)`` — a key that only
    means something if old observations survive. They did not: every build
    rewrote all 2.18M rows with today's ``observed_from`` and a NULL
    ``observed_to``, so the table claimed each code had been first observed on
    the day of the latest build and no superseded code was ever recorded
    (``docs/LIMITATIONS.md`` item 7).

    No ``is_current`` column and no surrogate id, so this is not
    ``carry_forward``: the documented key carries the interval itself, and
    adding columns the published schema does not have would put
    ``docs/schema.sql`` out of step with ``DB_SCHEMA.md``.

    Four cases:

    * already closed (``observed_to`` set) → untouched
    * open and still observed → **kept**, with its original ``observed_from``
    * open and no longer observed → closed at ``observed_from`` of this build.
      This is the case that covers a retired address: its codes stop being
      observed, and the rows stay, closed, rather than vanishing
    * observed and not open already → appended

    A key observed again after being closed gets a *new* row, because its
    ``observed_from`` differs — which is the correct reading of "observed from
    here to there, then again from here".
    """
    # 空の current で早期に返してはいけない。「今回どの符号も観測されなかった」は
    # 「前の観測を捨てる」ではなく「前の観測を全部閉じる」である —— この関数の
    # docstring が約束しているのは後者で、早期 return は逆のことをしていた
    # （レビューで見つかった）。以降の処理は current が空でも正しく動く。
    prev_path = (previous_dir / f"{table}.parquet") if previous_dir else None
    if prev_path is None or not prev_path.exists():
        return current

    previous = pl.read_parquet(prev_path)
    if previous.is_empty() or not set(key) <= set(previous.columns):
        return current
    missing = [c for c in current.columns if c not in previous.columns]
    for c in missing:
        previous = previous.with_columns(pl.lit(None).cast(current.schema[c]).alias(c))
    previous = previous.select(current.columns)

    k = pl.concat_str([pl.col(c).fill_null("\x00") for c in key], separator="\x1f")
    previous = previous.with_columns(k.alias("_k"))
    cur = current.with_columns(k.alias("_k"))

    closed = previous.filter(pl.col("observed_to").is_not_null())
    open_ = previous.filter(pl.col("observed_to").is_null())
    observed_now = set(cur["_k"].to_list())
    open_keys = set(open_["_k"].to_list())

    kept = open_.filter(pl.col("_k").is_in(list(observed_now)))
    # 区間は、開いた日より前に閉じられない。ふつうは `observed_from` が前の行より
    # 後なので何も起きないが、観測日を時計ではなく取得記録から取る（項目10）と、
    # ビルド日で開いた行を取得日で閉じることになりうる。そのとき素直に代入すると
    # 「2026-09-20 に開き 2026-09-17 に閉じた」行が出る。
    ended = open_.filter(~pl.col("_k").is_in(list(observed_now))).with_columns(
        pl.max_horizontal(pl.lit(observed_from), pl.col("observed_from"))
        .alias("observed_to")
    )
    added = cur.filter(~pl.col("_k").is_in(list(open_keys)))

    out = (
        pl.concat(
            [closed.drop("_k"), ended.drop("_k"), kept.drop("_k"), added.drop("_k")],
            how="vertical",
        )
        .unique(subset=[*key, "observed_from"], keep="first")
        .sort([*key, "observed_from"])
    )
    log.info(
        "observation log carried forward", table=table, previous=previous.height,
        current=current.height, unchanged=kept.height, closed=ended.height,
        added=added.height, total=out.height,
    )
    return out


def build_address_history(
    address: pl.DataFrame,
    previous_dir: Path | None,
    observed_at: str,
    snapshot_id: str,
) -> pl.DataFrame:
    """One row per changed attribute on a surviving address (spec §43)."""
    schema = {
        "history_id": pl.Utf8, "address_id": pl.Utf8, "field_name": pl.Utf8,
        "old_value": pl.Utf8, "new_value": pl.Utf8, "valid_from": pl.Utf8,
        "observed_at": pl.Utf8, "source_snapshot_id": pl.Utf8,
    }
    prev_path = (previous_dir / "address.parquet") if previous_dir else None
    if prev_path is None or not prev_path.exists():
        return pl.DataFrame(schema=schema)

    previous = pl.read_parquet(prev_path)
    tracked = [
        c for c in ["lg_code", "machiaza_id", "jis_city_code", "pref", "county",
                    "city", "ward", "oaza_cho", "chome", "koaza", "machiaza_dist",
                    "full_name_raw", "valid_from", "valid_to", "status_flg"]
        if c in previous.columns and c in address.columns
    ]
    joined = previous.select(["address_id", *tracked]).join(
        address.select(
            ["address_id", *[pl.col(c).alias(f"{c}__new") for c in tracked]]
        ),
        on="address_id", how="inner",
    )

    rows = []
    for field in tracked:
        changed = joined.filter(
            pl.col(field).fill_null("\x00") != pl.col(f"{field}__new").fill_null("\x00")
        )
        for r in changed.iter_rows(named=True):
            rows.append(
                {
                    # 値も材料に入れる。(address_id|field|observed_at) だけだと、
                    # 同じ観測日で同じ列が2度違う値に変わったときに id が衝突し、
                    # `accumulate_events` の重複排除が**後の変更を捨てる**
                    # （レビューで見つかった）。観測日が時計由来だった頃は日付が
                    # 毎回違うので起きなかったが、取得記録から取るようになった今は
                    # 同じ日付の2リリースがありうる。
                    "history_id": "hst_"
                    + hashlib.blake2s(
                        "|".join([
                            r["address_id"], field, observed_at,
                            r[field] or "\x00", r[f"{field}__new"] or "\x00",
                        ]).encode(),
                        digest_size=10,
                    ).hexdigest(),
                    "address_id": r["address_id"],
                    "field_name": field,
                    "old_value": r[field],
                    "new_value": r[f"{field}__new"],
                    # Only ABR states a real effective date; nothing is invented.
                    "valid_from": r.get("valid_from__new") if field != "valid_from" else None,
                    "observed_at": observed_at,
                    "source_snapshot_id": snapshot_id,
                }
            )

    out = pl.DataFrame(rows, schema=schema).sort("history_id")
    log.info("address history built", changes=out.height)
    # 検出は1回きり（この関数は「前リリースと今回の差」しか返さない）。積まないと、
    # 前のリリースで記録した変更が次のリリースで消える。history_id は
    # history_id は (address_id|field|observed_at|旧値|新値) の内容アドレスなので、
    # union と重複排除で足りる。
    return accumulate_events(out, previous_dir, "address_history", "history_id")


def promote_to_previous(dist_parquet: Path, previous_dir: Path, extra: list[Path]) -> None:
    """Snapshot a passing build so the next run has something to compare against.

    Without this, `compare_with_previous` returns ``skip`` on every run and the
    row-count, unmatched-rate, ambiguous-rate and data-loss gates can never fire.
    """
    import shutil

    if not dist_parquet.exists():
        return
    previous_dir.mkdir(parents=True, exist_ok=True)
    for f in previous_dir.glob("*.parquet"):
        f.unlink()
    for f in sorted(dist_parquet.glob("*.parquet")):
        shutil.copy2(f, previous_dir / f.name)
    for f in extra:
        if f.exists():
            shutil.copy2(f, previous_dir.parent / f.name)
    log.info("promoted build to data/previous", dir=str(previous_dir))
