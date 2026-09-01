"""Identity rules (docs/IDENTITY_MODEL.md).

The point of these tests is not that ids get reused — it is that they get reused
*only* on the narrow evidence the model allows, and that everything else is
queued for review instead of guessed.
"""

from __future__ import annotations

import re

import polars as pl
import pytest

from jp_address_crosswalk.identity import (
    ID_LENGTH,
    IdentityLedger,
    LedgerRow,
    mint_address_id,
)

ID_RE = re.compile(r"^jpa1[0-9a-hjkmnp-tv-z]{16}$")
SNAP = "snap_test_0001"


def towns(*rows) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"lg_code": lg, "machiaza_id": mid, "full_name_normalized": name}
            for lg, mid, name in rows
        ],
        schema={"lg_code": pl.Utf8, "machiaza_id": pl.Utf8,
                "full_name_normalized": pl.Utf8},
    ).sort(["lg_code", "machiaza_id"])


def ledger_with(*rows) -> IdentityLedger:
    out = []
    for lg, mid, name in rows:
        aid = mint_address_id(lg, mid)
        out.append(
            LedgerRow(
                address_id=aid, genesis_lg_code=lg, genesis_machiaza_id=mid,
                current_lg_code=lg, current_machiaza_id=mid,
                genesis_normalized_name=name, current_normalized_name=name,
                first_observed_snapshot_id="snap_old",
                last_observed_snapshot_id="snap_old",
            )
        )
    return IdentityLedger(out)


class TestMinting:
    def test_format_and_length(self):
        aid = mint_address_id("011011", "0001001")
        assert ID_RE.match(aid)
        assert len(aid) == ID_LENGTH == 20

    def test_deterministic(self):
        assert mint_address_id("011011", "0001001") == mint_address_id("011011", "0001001")

    def test_distinct_keys_distinct_ids(self):
        assert mint_address_id("011011", "0001001") != mint_address_id("011011", "0001002")

    def test_leading_zeros_matter(self):
        assert mint_address_id("011011", "0001001") != mint_address_id("011011", "1001")

    def test_not_a_concatenation(self):
        """The id must not expose the natural key (docs/POLICY.md §2)."""
        aid = mint_address_id("011011", "0001001")
        assert "011011" not in aid
        assert "0001001" not in aid


class TestGenesis:
    def test_all_new_on_empty_ledger(self):
        r = IdentityLedger().resolve(towns(("011011", "0001001", "旭ケ丘1丁目")), SNAP)
        assert r.minted == 1 and r.reused == 0
        assert all(v == "I5" for v in r.rule_by_address_id.values())

    def test_genesis_build_reproduces_ids(self):
        t = towns(("011011", "0001001", "a"), ("011011", "0001002", "b"))
        a = IdentityLedger().resolve(t, SNAP)
        b = IdentityLedger().resolve(t, SNAP)
        assert sorted(a.rule_by_address_id) == sorted(b.rule_by_address_id)

    def test_row_order_does_not_change_ids(self):
        t1 = towns(("011011", "0001001", "a"), ("011011", "0001002", "b"))
        t2 = t1.reverse().sort(["lg_code", "machiaza_id"])
        assert sorted(IdentityLedger().resolve(t1, SNAP).rule_by_address_id) == sorted(
            IdentityLedger().resolve(t2, SNAP).rule_by_address_id
        )


class TestI1:
    def test_unchanged_key_keeps_id(self):
        led = ledger_with(("011011", "0001001", "旭ケ丘1丁目"))
        expected = led.rows[0].address_id
        r = led.resolve(towns(("011011", "0001001", "旭ケ丘1丁目")), SNAP)
        assert r.reused == 1 and r.minted == 0
        assert expected in r.rule_by_address_id

    def test_retired_key_is_not_revived(self):
        """A reappearing key must not silently inherit a retired identity."""
        led = ledger_with(("011011", "0001001", "旧町名"))
        led.rows[0].entity_status = "retired"
        r = led.resolve(towns(("011011", "0001001", "全然違う町名")), SNAP)
        assert r.minted == 1, "a retired key must not be auto-reused"
        assert any(x["reason"] == "I6_reinstatement_candidate" for x in r.review_required)


class TestI4Rename:
    def test_rename_keeps_id_and_records_lineage(self):
        led = ledger_with(("011011", "0001001", "旧名"))
        expected = led.rows[0].address_id
        r = led.resolve(towns(("011011", "0001001", "新名")), SNAP)
        assert r.rule_by_address_id[expected] == "I4"
        assert any(x["relation_type"] == "renamed" for x in r.lineage)


class TestI2CodeCorrection:
    def test_correction_reuses_id(self):
        led = ledger_with(("011011", "0001001", "旭ケ丘1丁目"))
        expected = led.rows[0].address_id
        r = led.resolve(towns(("011011", "0009001", "旭ケ丘1丁目")), SNAP)
        assert r.rule_by_address_id.get(expected) == "I2"
        assert any(x["relation_type"] == "code_corrected" for x in r.lineage)

    def test_new_same_named_town_does_not_inherit(self):
        """The original key is still present, so this is an addition, not a correction."""
        led = ledger_with(("011011", "0001001", "中央"))
        r = led.resolve(
            towns(("011011", "0001001", "中央"), ("011011", "0009001", "中央")), SNAP
        )
        assert r.minted == 1, "a second same-named town must get a new id"
        assert r.reused == 1

    def test_ambiguous_correction_is_queued_not_guessed(self):
        led = ledger_with(("011011", "0001001", "中央"), ("011011", "0002001", "中央"))
        r = led.resolve(towns(("011011", "0009001", "中央")), SNAP)
        assert r.minted == 1
        assert any(x["reason"] == "I2_ambiguous" for x in r.review_required)


class TestI3MunicipalityRecode:
    OLD, NEW = "012025", "012033"

    def test_unattested_recode_does_not_carry_the_id(self):
        led = ledger_with((self.OLD, "0001001", "中央"))
        r = led.resolve(towns((self.NEW, "0001001", "中央")), SNAP)
        assert r.minted == 1, "a municipality merger alone is not town-level evidence"
        assert any(x["reason"] == "I3_unattested" for x in r.review_required)

    def test_attested_recode_carries_the_id(self):
        led = ledger_with((self.OLD, "0001001", "中央"))
        expected = led.rows[0].address_id
        r = led.resolve(
            towns((self.NEW, "0001001", "中央")), SNAP, {self.OLD: self.NEW}
        )
        assert r.rule_by_address_id.get(expected) == "I3"
        assert any(x["relation_type"] == "municipality_recoded" for x in r.lineage)


class TestRetirement:
    def test_absent_town_retires_and_is_not_deleted(self):
        led = ledger_with(("011011", "0001001", "a"), ("011011", "0001002", "b"))
        r = led.resolve(towns(("011011", "0001001", "a")), SNAP)
        assert r.retired == 1
        assert len(led.rows) == 2, "retired rows are kept forever"
        assert any(x["relation_type"] == "retired" for x in r.lineage)


class TestLedgerRoundTrip:
    def test_save_load_preserves_rows(self, tmp_path):
        led = ledger_with(("011011", "0001001", "旭ケ丘1丁目"))
        path = tmp_path / "ledger.csv.gz"
        led.save(path)
        again = IdentityLedger.load(path)
        assert [r.address_id for r in again.rows] == [r.address_id for r in led.rows]
        assert again.rows[0].genesis_machiaza_id == "0001001"

    def test_leading_zeros_survive_round_trip(self, tmp_path):
        led = ledger_with(("011011", "0001001", "x"))
        path = tmp_path / "l.csv.gz"
        led.save(path)
        assert IdentityLedger.load(path).rows[0].genesis_machiaza_id == "0001001"


@pytest.mark.parametrize("n", [1, 5, 50])
def test_no_collisions_at_scale(n):
    t = towns(*[("011011", f"{i:07d}", f"t{i}") for i in range(n)])
    r = IdentityLedger().resolve(t, SNAP)
    assert len(r.rule_by_address_id) == n
