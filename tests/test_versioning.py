"""Version carry-forward (docs/DATA_MODEL.md §3, spec §43).

The point of these tests: a second build must not erase the first build's
version rows, and an unchanged record must not look like it was re-observed
from scratch every month.
"""

from __future__ import annotations

import polars as pl

from jp_address_crosswalk.build.versioning import build_address_history, carry_forward

KEY = ["lg_code"]
VID = "municipality_version_id"
TABLE = "municipality_version"


def frame(rows) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            VID: pl.Utf8, "lg_code": pl.Utf8, "city": pl.Utf8,
            "observed_from": pl.Utf8, "observed_to": pl.Utf8,
            "is_current": pl.Boolean, "source_snapshot_id": pl.Utf8,
        },
    )


def row(lg, city, obs="2026-01-01", to=None, cur=True, snap="s1"):
    return {
        VID: f"mv_{lg}_{obs}", "lg_code": lg, "city": city,
        "observed_from": obs, "observed_to": to,
        "is_current": cur, "source_snapshot_id": snap,
    }


def write_prev(tmp_path, df):
    d = tmp_path / "parquet"
    d.mkdir(parents=True, exist_ok=True)
    df.write_parquet(d / f"{TABLE}.parquet")
    return d


class TestCarryForward:
    def test_no_previous_release_passes_through(self, tmp_path):
        cur = frame([row("011002", "札幌市")])
        out = carry_forward(cur, None, TABLE, KEY, VID, "2026-02-01")
        assert out.height == 1
        assert "_content_hash" not in out.columns

    def test_first_release_ids_survive_the_second_release(self, tmp_path):
        """A version id must not move because a baseline started existing.

        The id scheme once applied only on the merge path, so release 1 shipped
        construction-time ids and release 2 renamed every one of them without a
        single record having changed. Anyone who had stored an id held a
        dangling reference.
        """
        cur = frame([row("011002", "札幌市", obs="2026-01-01")])
        first = carry_forward(cur, None, TABLE, KEY, VID, "2026-01-01")
        assert first[VID][0].startswith("ver_")

        prev = write_prev(tmp_path, first)
        second = carry_forward(
            frame([row("011002", "札幌市", obs="2026-02-01", snap="s2")]),
            prev, TABLE, KEY, VID, "2026-02-01",
        )
        assert second.height == 1
        assert second[VID][0] == first[VID][0]

    def test_unchanged_row_keeps_its_original_observed_from(self, tmp_path):
        prev = write_prev(tmp_path, frame([row("011002", "札幌市", obs="2026-01-01")]))
        cur = frame([row("011002", "札幌市", obs="2026-02-01", snap="s2")])
        out = carry_forward(cur, prev, TABLE, KEY, VID, "2026-02-01")
        assert out.height == 1, "an unchanged record must not create a second version"
        assert out["observed_from"][0] == "2026-01-01"
        assert out["is_current"][0]

    def test_changed_row_supersedes_rather_than_overwrites(self, tmp_path):
        prev = write_prev(tmp_path, frame([row("011002", "旧市名", obs="2026-01-01")]))
        cur = frame([row("011002", "新市名", obs="2026-02-01", snap="s2")])
        out = carry_forward(cur, prev, TABLE, KEY, VID, "2026-02-01")

        assert out.height == 2, "the previous version must survive"
        old = out.filter(~pl.col("is_current"))
        new = out.filter(pl.col("is_current"))
        assert old.height == 1 and new.height == 1
        assert old["city"][0] == "旧市名"
        assert old["observed_to"][0] == "2026-02-01"
        assert new["city"][0] == "新市名"

    def test_already_closed_rows_are_carried_through(self, tmp_path):
        prev = write_prev(
            tmp_path,
            frame([
                row("011002", "一代目", obs="2025-01-01", to="2026-01-01", cur=False),
                row("011002", "二代目", obs="2026-01-01"),
            ]),
        )
        cur = frame([row("011002", "三代目", obs="2026-02-01", snap="s3")])
        out = carry_forward(cur, prev, TABLE, KEY, VID, "2026-02-01")
        assert out.height == 3
        assert set(out["city"]) == {"一代目", "二代目", "三代目"}
        assert out.filter(pl.col("is_current")).height == 1

    def test_new_entity_is_appended(self, tmp_path):
        prev = write_prev(tmp_path, frame([row("011002", "札幌市")]))
        cur = frame([row("011002", "札幌市"), row("012025", "函館市")])
        out = carry_forward(cur, prev, TABLE, KEY, VID, "2026-02-01")
        assert out.height == 2
        assert set(out["lg_code"]) == {"011002", "012025"}

    def test_disappeared_entity_is_closed_not_deleted(self, tmp_path):
        prev = write_prev(
            tmp_path, frame([row("011002", "札幌市"), row("012025", "函館市")])
        )
        cur = frame([row("011002", "札幌市")])
        out = carry_forward(cur, prev, TABLE, KEY, VID, "2026-02-01")
        gone = out.filter(pl.col("lg_code") == "012025")
        assert gone.height == 1, "a vanished record is retained, not dropped"
        assert not gone["is_current"][0]
        assert gone["observed_to"][0] == "2026-02-01"

    def test_same_day_change_keeps_both_versions(self, tmp_path):
        """Two builds on one date must not collapse into a single row.

        Version ids are content-addressed rather than date-stamped precisely so
        the superseded row and its replacement stay distinguishable.
        """
        prev = write_prev(tmp_path, frame([row("011002", "旧", obs="2026-08-23")]))
        cur = frame([row("011002", "新", obs="2026-08-23", snap="s2")])
        out = carry_forward(cur, prev, TABLE, KEY, VID, "2026-08-23")
        assert out.height == 2
        assert out[VID].n_unique() == 2, "ids must differ even on the same date"
        assert set(out["city"]) == {"旧", "新"}

    def test_repeated_identical_builds_do_not_grow_the_table(self, tmp_path):
        cur = frame([row("011002", "札幌市")])
        prev = write_prev(tmp_path, cur)
        out1 = carry_forward(cur, prev, TABLE, KEY, VID, "2026-02-01")
        out1.write_parquet(prev / f"{TABLE}.parquet")
        out2 = carry_forward(cur, prev, TABLE, KEY, VID, "2026-03-01")
        assert out1.height == out2.height == 1


class TestAddressHistory:
    @staticmethod
    def addr(rows):
        return pl.DataFrame(
            rows,
            schema={"address_id": pl.Utf8, "lg_code": pl.Utf8, "machiaza_id": pl.Utf8,
                    "full_name_raw": pl.Utf8, "valid_from": pl.Utf8},
        )

    def test_no_previous_means_no_history(self, tmp_path):
        cur = self.addr([{"address_id": "jpa1a", "lg_code": "011011",
                          "machiaza_id": "0001001", "full_name_raw": "旭ケ丘1丁目",
                          "valid_from": None}])
        out = build_address_history(cur, None, "2026-02-01", "s1")
        assert out.height == 0

    def test_rename_is_recorded(self, tmp_path):
        d = tmp_path / "parquet"
        d.mkdir(parents=True)
        self.addr([{"address_id": "jpa1a", "lg_code": "011011",
                    "machiaza_id": "0001001", "full_name_raw": "旧名",
                    "valid_from": None}]).write_parquet(d / "address.parquet")
        cur = self.addr([{"address_id": "jpa1a", "lg_code": "011011",
                          "machiaza_id": "0001001", "full_name_raw": "新名",
                          "valid_from": None}])
        out = build_address_history(cur, d, "2026-02-01", "s2")
        assert out.height == 1
        r = out.to_dicts()[0]
        assert r["field_name"] == "full_name_raw"
        assert r["old_value"] == "旧名" and r["new_value"] == "新名"
        assert r["observed_at"] == "2026-02-01"

    def test_unchanged_address_produces_nothing(self, tmp_path):
        d = tmp_path / "parquet"
        d.mkdir(parents=True)
        rows = [{"address_id": "jpa1a", "lg_code": "011011",
                 "machiaza_id": "0001001", "full_name_raw": "同名",
                 "valid_from": None}]
        self.addr(rows).write_parquet(d / "address.parquet")
        assert build_address_history(self.addr(rows), d, "2026-02-01", "s2").height == 0
