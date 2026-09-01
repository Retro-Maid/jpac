"""Tests that the safety mechanisms actually reject bad input.

Review R2 P1 was that the existing tests only checked that *current* data happens
to be clean — they would all have passed with the enforcement missing. These
tests deliberately feed invalid input and require it to be refused.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import polars as pl
import pytest
import yaml

from jp_address_crosswalk.build.common import (
    BuildContext,
    bridge_id,
    finalize_bridge,
)
from jp_address_crosswalk.build.overrides import (
    Override,
    apply_overrides,
    check_staleness,
    load_overrides,
)
from jp_address_crosswalk.errors import ValidationFailed
from jp_address_crosswalk.export import writers
from jp_address_crosswalk.identity import IdentityLedger, LedgerRow, mint_address_id
from jp_address_crosswalk.pipeline import _validate

CTX = BuildContext(
    match_run_id="run_t", snapshot_id="snap_t", observed_from="2026-08-23",
    built_at="2026-08-23T00:00:00Z", matching_rule_version="1.2.0",
    normalization_profile_version="1.0.0",
)
RULES = yaml.safe_load(
    (Path(__file__).parents[1] / "config" / "matching_rules.yml")
    .read_text(encoding="utf-8")
)


def bridge_frame(**over) -> pl.DataFrame:
    row = {
        "bridge_id": bridge_id("t", "a"), "address_id": "jpa1aaaaaaaaaaaaaaaa",
        "target_id": "t1", "direction": "x", "relation_type": "exact",
        "match_method": "normalized_name", "matching_rule_id": "P4",
        "confidence": 0.99, "candidate_group_id": None, "candidate_count": 1,
        "mismatch_note": None,
    }
    row.update(over)
    return finalize_bridge(
        pl.DataFrame([row], schema={
            "bridge_id": pl.Utf8, "address_id": pl.Utf8, "target_id": pl.Utf8,
            "direction": pl.Utf8, "relation_type": pl.Utf8, "match_method": pl.Utf8,
            "matching_rule_id": pl.Utf8, "confidence": pl.Float64,
            "candidate_group_id": pl.Utf8, "candidate_count": pl.Int64,
            "mismatch_note": pl.Utf8,
        }),
        CTX, ["bridge_id"],
    )


class TestSqliteRejectsInvalidRows:
    """The database must refuse what the documentation forbids."""

    @staticmethod
    def db(tmp_path):
        good = bridge_frame()
        path = tmp_path / "t.sqlite"
        writers.write_sqlite(
            {"bridge_address_postal": good}, good.head(0), good.head(0), path
        )
        return path

    def insert(self, conn, df):
        cols = df.columns
        conn.execute(
            f'INSERT INTO bridge_address_postal VALUES ({",".join("?" * len(cols))})',
            list(df.iter_rows())[0],
        )

    def test_valid_row_inserts(self, tmp_path):
        conn = sqlite3.connect(self.db(tmp_path))
        try:
            row = bridge_frame(bridge_id="brg_new")
            self.insert(conn, row)
        finally:
            conn.close()

    def test_auto_with_multiple_candidates_is_rejected(self, tmp_path):
        conn = sqlite3.connect(self.db(tmp_path))
        try:
            bad = bridge_frame(bridge_id="brg_bad").with_columns(
                [
                    pl.lit("auto").alias("verification_status"),
                    pl.lit(3).cast(pl.Int64).alias("candidate_count"),
                    pl.lit("g1").alias("candidate_group_id"),
                ]
            )
            with pytest.raises(sqlite3.IntegrityError):
                self.insert(conn, bad)
        finally:
            conn.close()

    def test_auto_with_low_confidence_is_rejected(self, tmp_path):
        conn = sqlite3.connect(self.db(tmp_path))
        try:
            bad = bridge_frame(bridge_id="brg_low").with_columns(
                [pl.lit("auto").alias("verification_status"),
                 pl.lit(0.5).alias("confidence")]
            )
            with pytest.raises(sqlite3.IntegrityError):
                self.insert(conn, bad)
        finally:
            conn.close()

    def test_auto_with_ambiguous_relation_is_rejected(self, tmp_path):
        conn = sqlite3.connect(self.db(tmp_path))
        try:
            bad = bridge_frame(bridge_id="brg_amb").with_columns(
                [pl.lit("auto").alias("verification_status"),
                 pl.lit("ambiguous").alias("relation_type")]
            )
            with pytest.raises(sqlite3.IntegrityError):
                self.insert(conn, bad)
        finally:
            conn.close()

    def test_out_of_range_confidence_is_rejected(self, tmp_path):
        conn = sqlite3.connect(self.db(tmp_path))
        try:
            bad = bridge_frame(bridge_id="brg_rng").with_columns(
                pl.lit(1.5).alias("confidence")
            )
            with pytest.raises(sqlite3.IntegrityError):
                self.insert(conn, bad)
        finally:
            conn.close()

    def test_unknown_relation_type_is_rejected(self, tmp_path):
        conn = sqlite3.connect(self.db(tmp_path))
        try:
            bad = bridge_frame(bridge_id="brg_enum").with_columns(
                pl.lit("probably").alias("relation_type")
            )
            with pytest.raises(sqlite3.IntegrityError):
                self.insert(conn, bad)
        finally:
            conn.close()

    def test_duplicate_bridge_id_is_rejected(self, tmp_path):
        conn = sqlite3.connect(self.db(tmp_path))
        try:
            with pytest.raises(sqlite3.IntegrityError):
                self.insert(conn, bridge_frame())
        finally:
            conn.close()

    def test_row_with_no_endpoint_is_rejected(self, tmp_path):
        conn = sqlite3.connect(self.db(tmp_path))
        try:
            bad = bridge_frame(bridge_id="brg_void").with_columns(
                [
                    pl.lit(None, dtype=pl.Utf8).alias("address_id"),
                    pl.lit(None, dtype=pl.Utf8).alias("lg_code"),
                    pl.lit(None, dtype=pl.Utf8).alias("target_id"),
                ]
            )
            with pytest.raises(sqlite3.IntegrityError):
                self.insert(conn, bad)
        finally:
            conn.close()


class TestPipelineSemanticValidation:
    def test_rule_id_missing_from_config_is_rejected(self):
        bad = bridge_frame(matching_rule_id="P_DOES_NOT_EXIST")
        problems = _validate(
            {"bridge_address_postal": bad}, strict=False, matching_rules=RULES
        )
        assert any("absent from config" in p for p in problems)

    def test_stale_matching_rule_version_is_rejected(self):
        bad = bridge_frame().with_columns(
            pl.lit("1.1.0").alias("matching_rule_version")
        )
        problems = _validate(
            {"bridge_address_postal": bad}, strict=False, matching_rules=RULES
        )
        assert any("matching_rule_version differs" in p for p in problems)

    def test_town_level_telephone_target_is_rejected(self):
        bad = bridge_frame(
            matching_rule_id="T10", relation_type="child"
        ).with_columns(
            [
                pl.lit("full").alias("coverage_type"),
                pl.lit("expanded_from_municipality").alias("derivation"),
            ]
        )
        problems = _validate(
            {"bridge_address_telephone": bad},
            strict=False,
            matching_rules=RULES,
        )
        assert any("misrepresented at town level" in p for p in problems)


class TestRetiredIdIsNeverReused:
    """Review R2 P0: minting is deterministic, so a reused key got the old id."""

    def test_reused_key_gets_a_different_id(self):
        original = mint_address_id("011011", "0001001")
        ledger = IdentityLedger([
            LedgerRow(
                address_id=original, genesis_lg_code="011011",
                genesis_machiaza_id="0001001", current_lg_code="011011",
                current_machiaza_id="0001001", genesis_normalized_name="旧町",
                current_normalized_name="旧町",
                first_observed_snapshot_id="s0", last_observed_snapshot_id="s0",
                entity_status="retired", retire_reason="absent_from_source",
            )
        ])
        towns = pl.DataFrame(
            [{"lg_code": "011011", "machiaza_id": "0001001",
              "full_name_normalized": "まったく別の町"}],
            schema={"lg_code": pl.Utf8, "machiaza_id": pl.Utf8,
                    "full_name_normalized": pl.Utf8},
        )
        result = ledger.resolve(towns, "s1")

        assert result.minted == 1
        new_ids = set(result.rule_by_address_id)
        assert original not in new_ids, (
            "a reinstated key must not inherit the retired entity's address_id"
        )

    def test_ids_stay_unique_across_the_whole_ledger(self):
        original = mint_address_id("011011", "0001001")
        ledger = IdentityLedger([
            LedgerRow(
                address_id=original, genesis_lg_code="011011",
                genesis_machiaza_id="0001001", current_lg_code="011011",
                current_machiaza_id="0001001", genesis_normalized_name="旧",
                current_normalized_name="旧",
                first_observed_snapshot_id="s0", last_observed_snapshot_id="s0",
                entity_status="retired",
            )
        ])
        towns = pl.DataFrame(
            [{"lg_code": "011011", "machiaza_id": "0001001",
              "full_name_normalized": "新"}],
            schema={"lg_code": pl.Utf8, "machiaza_id": pl.Utf8,
                    "full_name_normalized": pl.Utf8},
        )
        ledger.resolve(towns, "s1")
        ids = [r.address_id for r in ledger.rows]
        assert len(ids) == len(set(ids)), "ledger must never contain a duplicate id"

    def test_generation_ids_are_deterministic(self):
        a = mint_address_id("011011", "0001001", 1)
        b = mint_address_id("011011", "0001001", 1)
        assert a == b
        assert a != mint_address_id("011011", "0001001", 0)


class TestManualOverrides:
    BRIDGE = "bridge_address_mlit"

    @staticmethod
    def write(tmp_path, entries):
        import yaml

        p = tmp_path / "manual_overrides.yml"
        p.write_text(
            yaml.safe_dump({"version": "1.0.0", "overrides": entries},
                           allow_unicode=True),
            encoding="utf-8",
        )
        return p

    def entry(self, **over):
        e = {
            "id": "OVR-0001", "bridge": self.BRIDGE,
            "source": {"address_id": "jpa1aaaaaaaaaaaaaaaa"},
            "target": {"target_id": "t1"},
            "set": {"relation_type": "exact", "confidence": 1.0,
                    "verification_status": "manually_verified"},
            "reason": "checked against the municipal gazette",
            "observed_source_state": {"mlit_snapshot_sha256": "abc123"},
            "created_at": "2026-08-23",
        }
        e.update(over)
        return e

    def test_missing_file_is_not_an_error(self, tmp_path):
        assert load_overrides(tmp_path / "absent.yml") == []

    def test_valid_file_loads(self, tmp_path):
        assert len(load_overrides(self.write(tmp_path, [self.entry()]))) == 1

    def test_unknown_bridge_is_rejected(self, tmp_path):
        p = self.write(tmp_path, [self.entry(bridge="bridge_nonsense")])
        with pytest.raises(ValidationFailed):
            load_overrides(p)

    def test_overriding_a_forbidden_field_is_rejected(self, tmp_path):
        p = self.write(tmp_path, [self.entry(set={"address_id": "jpa1x"})])
        with pytest.raises(ValidationFailed):
            load_overrides(p)

    def test_out_of_range_confidence_is_rejected(self, tmp_path):
        p = self.write(tmp_path, [self.entry(set={"confidence": 2.0})])
        with pytest.raises(ValidationFailed):
            load_overrides(p)

    def test_duplicate_id_is_rejected(self, tmp_path):
        p = self.write(tmp_path, [self.entry(), self.entry()])
        with pytest.raises(ValidationFailed):
            load_overrides(p)

    def test_missing_required_key_is_rejected(self, tmp_path):
        e = self.entry()
        del e["reason"]
        with pytest.raises(ValidationFailed):
            load_overrides(self.write(tmp_path, [e]))

    def test_fresh_override_is_applied(self):
        o = Override(**self.entry())
        stale = check_staleness([o], {"mlit_isj": "abc123"})
        assert stale["OVR-0001"] is False
        tables = {self.BRIDGE: bridge_frame(relation_type="ambiguous",
                                            confidence=0.5)}
        outcome = apply_overrides(tables, [o], stale)
        assert outcome.applied == 1
        row = tables[self.BRIDGE].to_dicts()[0]
        assert row["relation_type"] == "exact"
        assert row["verification_status"] == "manually_verified"

    def test_stale_override_is_flagged_and_not_applied(self):
        """Spec §71: a decision made against data that has since moved must not
        keep applying itself."""
        o = Override(**self.entry())
        stale = check_staleness([o], {"mlit_isj": "DIFFERENT"})
        assert stale["OVR-0001"] is True
        tables = {self.BRIDGE: bridge_frame(relation_type="ambiguous",
                                            confidence=0.5)}
        outcome = apply_overrides(tables, [o], stale)
        assert outcome.applied == 0
        assert outcome.stale
        row = tables[self.BRIDGE].to_dicts()[0]
        assert row["override_stale"] is True
        assert row["relation_type"] == "ambiguous", "the override must not apply"
        assert row["verification_status"] == "review_required"

    def test_override_with_no_recorded_state_is_treated_as_stale(self):
        o = Override(**self.entry(observed_source_state={}))
        assert check_staleness([o], {"mlit_isj": "abc123"})["OVR-0001"] is True

    def test_override_targeting_nothing_is_reported(self):
        o = Override(**self.entry(target={"target_id": "does_not_exist"}))
        tables = {self.BRIDGE: bridge_frame()}
        outcome = apply_overrides(tables, [o], {"OVR-0001": False})
        assert outcome.applied == 0
        assert outcome.unmatched


class TestBridgeIdDistinguishesNullFromEmpty:
    """A NULL endpoint and an empty-string endpoint are different states."""

    def test_none_and_empty_string_differ(self):
        assert bridge_id("b", None, "y") != bridge_id("b", "", "y")

    def test_still_deterministic(self):
        assert bridge_id("b", None, "y") == bridge_id("b", None, "y")


class TestApprovedRowCountMigration:
    """A row-count approval must be a scalpel, not a widened gate."""

    @staticmethod
    def _cmp(prev_rows, cur_rows, thresholds, table="telephone_area_coverage"):
        from jp_address_crosswalk.build import quality

        return quality.compare_with_previous(
            {"tables": {table: {"row_count": cur_rows}}},
            {"tables": {table: {"row_count": prev_rows}}},
            thresholds,
        )[0]

    def test_the_shipped_approval_passes_only_its_own_transition(self):
        import yaml

        cfg = yaml.safe_load(
            (Path(__file__).parents[1] / "config" / "quality_thresholds.yml")
            .read_text(encoding="utf-8")
        )
        assert cfg.get("approved_row_count_changes"), "the approval should be present"

        approved = self._cmp(1489, 1629, cfg)
        assert approved["status"] == "pass"
        assert approved["approved_migration"] is True
        assert approved["attested_by"]

        # One row either side and it is a different change, so it blocks again.
        assert self._cmp(1489, 1630, cfg)["status"] == "fail"
        assert self._cmp(1488, 1629, cfg)["status"] == "fail"

    def test_the_approval_is_inert_once_it_has_been_applied(self):
        """Next release compares 1629 against 1629's successor, never 1489."""
        import yaml

        cfg = yaml.safe_load(
            (Path(__file__).parents[1] / "config" / "quality_thresholds.yml")
            .read_text(encoding="utf-8")
        )
        # A later regression of the same size must not be waved through.
        assert self._cmp(1629, 1489, cfg)["status"] == "fail"
        # And an unrelated table is untouched by the approval.
        assert self._cmp(1489, 1629, cfg, table="postal_record")["status"] == "fail"
