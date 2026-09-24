"""v1 互換ビュー (docs/BRIDGE_ENDPOINT_MIGRATION.md A7).

v2.0.0 で端点列の名前が変わり、`bridge_municipality_postal` は2つに分かれた。旧い名前で
読めるビューを置くので、既存のクエリは表名に `_v1` を足すだけで動く。

固定したいのは4つ:

* 旧い列名（`target_id`）で読めること
* 分割した2表が**1つのビュー**に戻ること —— 旧い表は6本なので、ビューも6本である
* 列の**並びと数**が旧い表と同じであること（`SELECT *` で読んでいる利用者がいる）
* 存在しない表のビューを作らないこと（V2 の元データが無いビルドでも通る）
"""

from __future__ import annotations

import sqlite3

import polars as pl

from jp_address_crosswalk.build.common import BRIDGE_COLUMNS
from jp_address_crosswalk.export import writers


def _frame(bridge: str, **over) -> pl.DataFrame:
    endpoint = writers.BRIDGE_ENDPOINTS[bridge]
    subject = writers.BRIDGE_SUBJECTS[bridge]
    row = {
        "bridge_id": "brg_1",
        "address_id": "jpa_1" if subject == "address_id" else None,
        "lg_code": "131016" if subject == "lg_code" else None,
        endpoint: "x1",
        "direction": ("address_to_x" if subject == "address_id"
                      else "municipality_to_x"),
        "relation_type": "parent", "match_method": "direct_code",
        "matching_rule_id": "P2", "confidence": 0.99, "candidate_group_id": None,
        "candidate_count": 1, "candidate_count_is_complete": True,
        "is_unique_match": True, "verification_status": "review_required",
        "override_stale": False, "derivation": None, "coverage_type": None,
        "normalization_profile": "conservative", "mismatch_note": None,
        "valid_from": None, "valid_to": None, "observed_from": "2026-09-17",
        "observed_to": None, "is_current": True, "match_run_id": "run_1",
        "source_snapshot_id": "snap_1", "matching_rule_version": "1.2.0",
        "normalization_profile_version": "1.0.0",
        "created_at": "2026-09-17T00:00:00Z", "updated_at": "2026-09-17T00:00:00Z",
    }
    row.update(over)
    schema = {
        (endpoint if k == "target_id" else k): v for k, v in BRIDGE_COLUMNS.items()
    }
    return pl.DataFrame([row], schema=schema)


def _db(tmp_path, tables):
    empty = next(iter(tables.values())).head(0)
    path = tmp_path / "v1.sqlite"
    writers.write_sqlite(tables, empty, empty, path)
    return sqlite3.connect(path)


class TestTheOldNamesStillRead:
    def test_the_endpoint_is_visible_as_target_id(self, tmp_path) -> None:
        conn = _db(tmp_path, {
            "bridge_address_mlit": _frame("bridge_address_mlit", relation_type="exact"),
        })
        row = conn.execute(
            'SELECT target_id FROM bridge_address_mlit_v1'
        ).fetchone()
        assert row == ("x1",)

    def test_the_split_tables_come_back_as_one_view(self, tmp_path) -> None:
        """旧い表は6本。分割した2表は1つのビューに戻る（UNION ALL）。"""
        conn = _db(tmp_path, {
            "bridge_municipality_postal_code": _frame(
                "bridge_municipality_postal_code", bridge_id="brg_p2"
            ),
            "bridge_municipality_postal": _frame(
                "bridge_municipality_postal", bridge_id="brg_p3", matching_rule_id="P3"
            ),
        })
        rows = conn.execute(
            "SELECT bridge_id, target_id, matching_rule_id "
            "FROM bridge_municipality_postal_v1 ORDER BY bridge_id"
        ).fetchall()
        assert rows == [("brg_p2", "x1", "P2"), ("brg_p3", "x1", "P3")]
        # 分割前と同じ名前のビューがあり、分割後の表とは別物であること
        names = {
            n for (n,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view'"
            )
        }
        assert "bridge_municipality_postal_v1" in names
        assert "bridge_municipality_postal_code_v1" not in names

    def test_the_column_order_and_count_match_the_old_table(self, tmp_path) -> None:
        """`SELECT *` で読んでいる利用者のために、並びも数も変えない。"""
        conn = _db(tmp_path, {
            "bridge_address_postal": _frame("bridge_address_postal"),
        })
        cols = [
            r[1] for r in conn.execute('PRAGMA table_info("bridge_address_postal_v1")')
        ]
        assert cols == list(BRIDGE_COLUMNS)

    def test_no_view_is_made_for_a_table_that_is_not_there(self, tmp_path) -> None:
        conn = _db(tmp_path, {
            "bridge_address_mlit": _frame("bridge_address_mlit", relation_type="exact"),
        })
        names = {
            n for (n,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view'"
            )
        }
        assert "bridge_address_mlit_v1" in names
        assert "bridge_municipality_postal_v1" not in names
        assert "bridge_address_telephone_v1" not in names
