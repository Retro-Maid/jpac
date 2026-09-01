"""An attested alias must lapse the moment its premise stops holding.

The whole justification for resolving MIC's 「篠山市」 to 丹波篠山市 is that MIC
has not updated its text. If MIC updates it, the alias is not merely unnecessary
— continuing to apply it would mean matching on a claim nobody is making any
more. The same goes for the other two premises.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
import yaml

from jp_address_crosswalk.build.source_aliases import load_name_aliases

ROOT = Path(__file__).parents[1]
SHIPPED = ROOT / "overrides" / "source_name_aliases.yml"

MUNI = pl.DataFrame(
    {
        "lg_code": ["282219", "282235"],
        "pref": ["兵庫県", "兵庫県"],
        "city": ["丹波篠山市", "丹波市"],
        "ward": [None, None],
    }
)
NAMES = {"mic_shigai_list": {("兵庫県", "三田市"), ("兵庫県", "篠山市")}}


def _write(tmp_path: Path, doc: dict) -> Path:
    p = tmp_path / "aliases.yml"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return p


@pytest.fixture
def shipped() -> dict:
    return yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))


class TestShippedAlias:
    def test_it_applies_against_the_real_preconditions(self, shipped, tmp_path):
        applied, stale = load_name_aliases(_write(tmp_path, shipped), MUNI, NAMES)
        assert not stale
        assert applied[("兵庫県", "篠山市")].lg_code == "282219"

    def test_every_alias_carries_evidence_and_an_attester(self, shipped):
        for a in shipped["aliases"]:
            assert a.get("evidence_url", "").startswith("http"), a["id"]
            assert "@" in a.get("attested_by", ""), a["id"]
            assert a.get("attested_on"), a["id"]
            assert a.get("preconditions"), a["id"]


class TestPreconditionsLapse:
    def test_lapses_when_the_publisher_corrects_its_text(self, shipped, tmp_path):
        """The premise is that MIC is out of date. If it is not, the alias goes."""
        applied, stale = load_name_aliases(
            _write(tmp_path, shipped),
            MUNI,
            {"mic_shigai_list": {("兵庫県", "三田市"), ("兵庫県", "丹波篠山市")}},
        )
        assert applied == {}
        assert len(stale) == 1
        assert "source_still_contains" in stale[0]["reasons"][0]

    def test_lapses_when_a_municipality_by_the_old_name_exists(self, shipped, tmp_path):
        muni = pl.concat([MUNI, pl.DataFrame({
            "lg_code": ["289999"], "pref": ["兵庫県"], "city": ["篠山市"], "ward": [None],
        })])
        applied, stale = load_name_aliases(_write(tmp_path, shipped), muni, NAMES)
        assert applied == {}
        assert "abr_has_no_municipality_named" in stale[0]["reasons"][0]

    def test_lapses_when_the_target_is_renamed_again(self, shipped, tmp_path):
        muni = MUNI.with_columns(
            pl.when(pl.col("lg_code") == "282219")
            .then(pl.lit("篠山市"))
            .otherwise(pl.col("city"))
            .alias("city")
        )
        applied, stale = load_name_aliases(_write(tmp_path, shipped), muni, NAMES)
        assert applied == {}

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        assert load_name_aliases(tmp_path / "absent.yml", MUNI, NAMES) == ({}, [])
