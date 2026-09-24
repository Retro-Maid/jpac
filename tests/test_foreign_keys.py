"""出荷する SQLite が参照整合性を自分で主張すること (docs/LIMITATIONS.md 項目8).

独立レビュー3 の指摘は「外部キーが1本も宣言されておらず、参照整合性はテストスイートが
主張しているだけ」だった。テストが主張しているものは、テストを消せば消える。

固定したいのは4つ:

* 規約（列名 → 親テーブル）が実際に句になること
* **親テーブル自身に自己参照を吐かないこと**（`municipality.lg_code` は親の側である）
* `docs/schema.sql` に書いた FOREIGN KEY が、同じファイルに存在する表と列を指すこと
* `PRAGMA foreign_key_check` が、書き終えた実物に対して走ること —— 宣言は既定で
  OFF なので、宣言しただけでは何も確かめたことにならない
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import polars as pl
import pytest

from jp_address_crosswalk.errors import ValidationFailed
from jp_address_crosswalk.export import writers

SCHEMA_SQL = Path(__file__).resolve().parents[1] / "docs" / "schema.sql"


class TestTheConvention:
    def test_a_bridge_declares_its_endpoints(self) -> None:
        fks = writers.foreign_keys_for(
            "bridge_address_postal",
            ["bridge_id", "address_id", "lg_code", "target_id",
             "match_run_id", "source_snapshot_id"],
        )
        assert any('"address_id") REFERENCES "address_entity"' in f for f in fks)
        assert any('"lg_code") REFERENCES "municipality"' in f for f in fks)
        assert any('"match_run_id") REFERENCES "match_run"' in f for f in fks)

    def test_the_polymorphic_column_gets_no_foreign_key(self) -> None:
        """`target_id` は郵便番号レコードの id でもあり市外局番でもある。多態な列は
        外部キーを持てない —— 項目8 の未解決側（DB_SCHEMA.md §5.1 の型付き端点）。"""
        fks = writers.foreign_keys_for("bridge_address_postal", ["bridge_id", "target_id"])
        assert not any("target_id" in f for f in fks)

    def test_the_snapshot_columns_get_no_foreign_key(self) -> None:
        """これは通してしまえる誤りである。全国データでは今日たまたま満たされるので、
        宣言してもゲートは緑のまま出荷される。それでも偽である ——
        `source_snapshot` は**このビルドの** payload を載せる表で、
        `first_observed_snapshot_id` や、積まれた行の `source_snapshot_id` は
        「最初に観測したとき」を指し、そのまま引き継がれる。payload が変わった
        最初のリリースで、引き継いだ行の参照先が表から消える。
        """
        fks = writers.foreign_keys_for(
            "address_entity",
            ["address_id", "first_observed_snapshot_id", "last_observed_snapshot_id"],
        )
        assert fks == []
        fks = writers.foreign_keys_for(
            "address_code", ["address_id", "code_value", "source_snapshot_id"]
        )
        assert all("source_snapshot" not in f for f in fks)
        assert any('"address_id") REFERENCES "address_entity"' in f for f in fks)

    def test_a_parent_does_not_reference_itself(self) -> None:
        assert writers.foreign_keys_for("municipality", ["lg_code", "jis_city_code"]) == []
        assert writers.foreign_keys_for(
            "source_snapshot", ["source_snapshot_id", "provider"]
        ) == []
        assert writers.foreign_keys_for("address_entity", ["address_id"]) == []

    def test_a_line_gets_a_composite_foreign_key(self) -> None:
        """路線の鍵は発行元自身の (路線名, 運営会社) なので2列になる。"""
        fks = writers.foreign_keys_for(
            "bridge_line_municipality", ["line_name_raw", "operator_name_raw", "lg_code"]
        )
        assert any(
            '("line_name_raw", "operator_name_raw") REFERENCES "n02_railroad_line"' in f
            for f in fks
        )

    def test_the_order_is_deterministic(self) -> None:
        cols = ["address_id", "lg_code", "source_snapshot_id"]
        assert writers.foreign_keys_for("address_code", cols) == writers.foreign_keys_for(
            "address_code", cols
        )


class TestTheShippedDdl:
    """`docs/schema.sql` は「これが配られるもの」として公開されている定義である。"""

    def test_every_declared_foreign_key_points_at_something_that_exists(self) -> None:
        sql = SCHEMA_SQL.read_text(encoding="utf-8")
        conn = sqlite3.connect(":memory:")
        conn.executescript(sql)
        tables = {
            name: {r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')}
            for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        declared = re.findall(
            r'FOREIGN KEY \(([^)]+)\) REFERENCES "(\w+)"\(([^)]+)\)', sql
        )
        assert declared, "外部キーが1本も宣言されていない"
        for _child_cols, parent, parent_cols in declared:
            assert parent in tables, f"{parent} が schema.sql に無い"
            for c in parent_cols.replace('"', "").split(","):
                assert c.strip() in tables[parent], f"{parent}.{c} が無い"

    def test_it_declares_exactly_what_the_writer_generates(self) -> None:
        """`docs/schema.sql` は成果物から derive されたもので、手で書いた写しではない。

        ずれを捕まえるのは本来 `jpac verify artifacts` だが、あれは全国ビルドを
        必要とする。ここでは公開 DDL の列一覧そのものを入力にして同じ規約を通し、
        FOREIGN KEY 句が1本ずつ一致することを確かめる —— ビルドも `dist/` も要らない。
        """
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        names = [
            n for (n,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        shipped = set(names)
        for table in names:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            expected = writers.foreign_keys_for(table, cols, present=shipped)
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = ?", (table,)
            ).fetchone()[0]
            found = [
                "FOREIGN KEY " + m
                for m in re.findall(r"FOREIGN KEY (\([^)]+\) REFERENCES \S+\([^)]+\))", sql)
            ]

            def canon(items: list[str]) -> list[str]:
                return [re.sub(r"\s+", "", i).replace('"', "") for i in items]

            assert canon(found) == canon(expected), table

    def test_the_polymorphic_endpoint_is_still_undeclared(self) -> None:
        """項目8 の未解決側がまだ未解決であることを、記述と一致させておく。
        型付き端点に移行したらこのテストが落ちる —— そのとき項目8 を閉じる。"""
        sql = SCHEMA_SQL.read_text(encoding="utf-8")
        assert '"target_id"' in sql
        assert "FOREIGN KEY (\"target_id\")" not in sql


class TestTheDatabaseChecksItself:
    @staticmethod
    def _two_tables(parent_ids, child_refs):
        parent = pl.DataFrame(
            [{"address_id": i} for i in parent_ids],
            schema={"address_id": pl.Utf8},
        )
        child = pl.DataFrame(
            [{"lineage_id": f"lin_{n}", "old_address_id": r}
             for n, r in enumerate(child_refs)],
            schema={"lineage_id": pl.Utf8, "old_address_id": pl.Utf8},
        )
        return {"address_entity": parent, "address_lineage": child}

    def test_a_satisfied_foreign_key_writes(self, tmp_path) -> None:
        tables = self._two_tables(["jpa_1"], ["jpa_1"])
        empty = next(iter(tables.values())).head(0)
        writers.write_sqlite(tables, empty, empty, tmp_path / "ok.sqlite")

    def test_a_dangling_reference_fails_the_build(self, tmp_path) -> None:
        """これが項目8 の眼目。宣言は既定で OFF なので、書き終えてから
        `PRAGMA foreign_key_check` を走らせないと、利用者が ON にした瞬間に初めて
        壊れていたことが分かる、という配り方になる。"""
        tables = self._two_tables(["jpa_1"], ["jpa_MISSING"])
        empty = next(iter(tables.values())).head(0)
        with pytest.raises(ValidationFailed):
            writers.write_sqlite(tables, empty, empty, tmp_path / "bad.sqlite")


class TestACarriedForwardTableReferencesNothing:
    """引き継がれる表は、作り直される親を指せない（レビューで見つかった critical）。

    `postal_record_id` は ken_all の行全体のハッシュで、`postal_record` はそのビルドの
    ken_all だけから作られる。日本郵便が1文字直せばその id は `postal_record` から
    消えるが、`carry_forward` が閉じた版の行は**永久に**その id を名乗り続ける ——
    `PRAGMA foreign_key_check` が次のリリースを落とす。

    全国データでは payload が v1.0.0 から変わっていないので、いま測っても緑になる。
    だから測るのではなく、宣言しないことをここで固定する。
    """

    VERSION_TABLES = [
        ("postal_record_version",
         ["postal_record_version_id", "postal_record_id", "postal_code",
          "source_snapshot_id"]),
        ("municipality_version", ["municipality_version_id", "lg_code"]),
        ("mlit_town_version", ["mlit_town_version_id", "mlit_record_id"]),
        ("telephone_area_version",
         ["telephone_area_version_id", "numbering_area_code"]),
    ]

    def test_no_version_table_declares_a_foreign_key(self) -> None:
        for table, cols in self.VERSION_TABLES:
            assert writers.foreign_keys_for(table, cols) == [], table

    def test_the_shipped_ddl_agrees(self) -> None:
        sql = SCHEMA_SQL.read_text(encoding="utf-8")
        conn = sqlite3.connect(":memory:")
        conn.executescript(sql)
        for table, _cols in self.VERSION_TABLES:
            ddl = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = ?", (table,)
            ).fetchone()[0]
            assert "FOREIGN KEY" not in ddl, table

    def test_a_carried_forward_table_may_still_point_at_an_append_only_parent(
        self,
    ) -> None:
        """除外の理由は「引き継がれるから」ではなく「親が作り直されるから」である。

        `address_code` と `address_lineage` も引き継がれるが、指しているのは
        `address_entity` —— 退役しても行が残る表（docs/IDENTITY_MODEL.md §5）なので、
        参照は壊れない。ここを一緒に落とすと、守れる保証を捨てることになる。
        """
        assert any(
            'REFERENCES "address_entity"' in f
            for f in writers.foreign_keys_for(
                "address_code", ["address_id", "code_type", "code_value"]
            )
        )
        assert any(
            'REFERENCES "address_entity"' in f
            for f in writers.foreign_keys_for(
                "address_lineage", ["lineage_id", "old_address_id", "new_address_id"]
            )
        )
