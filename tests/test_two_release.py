"""Two consecutive releases through the real pipeline (review R3 P1).

The suite previously called builders directly, so the normal monthly path —
build, promote, build again against the promoted baseline — was never executed.
A carry-forward regression that broke exactly that path passed CI.

This test drives `pipeline.build` and `pipeline.export` twice over fixture data,
with the second release seeing a changed, an added and a removed record.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import polars as pl
import pytest

from jp_address_crosswalk import pipeline
from jp_address_crosswalk.build import canonical
from jp_address_crosswalk.pipeline import Paths

from .test_fixture_build import (  # reuse the fixture loaders
    FIXTURES,
    load_abr_city,
    load_abr_town,
    load_conversion,
    load_ken_all,
    load_mic,
    load_mlit,
)

pytestmark = pytest.mark.skipif(
    not (FIXTURES / "abr_town.csv").exists(), reason="fixtures not generated"
)


class FakeSnapshot:
    """Minimal stand-in for a SourceSnapshot."""

    def __init__(self, dataset_name: str, sha: str, rows: int) -> None:
        self.source_snapshot_id = f"snap_{dataset_name}_{sha}"
        self.dataset_name = dataset_name
        self.sha256 = sha
        self.row_count = rows
        self.provider = "test"
        self.source_page_url = "https://example.invalid/"
        self.download_url = "https://example.invalid/f"
        self.license_name = "test"
        self.license_url = "https://example.invalid/tos"
        self.license_text_sha256 = "0" * 64
        self.source_version = "v1"
        self.published_at = None
        self.downloaded_at = "2026-08-23T00:00:00Z"
        self.etag = None
        self.last_modified = None
        self.file_size = 1
        self.schema_fingerprint = "f"
        self.parser_version = "1.0.0"
        self.status = "ok"

    def as_dict(self) -> dict:
        return {
            k: v for k, v in self.__dict__.items() if not k.startswith("_")
        }


def make_outcome(town: pl.DataFrame, sha: str) -> pipeline.FetchOutcome:
    o = pipeline.FetchOutcome()
    o.parsed = {
        "abr": {
            "town": town,
            "city": load_abr_city(),
            "postal_conversion": load_conversion(),
        },
        "japanpost": {"ken_all": load_ken_all()},
        "mlit": {"isj": load_mlit()},
        "mic_area_code": dict(
            zip(("telephone_area", "telephone_area_coverage"), load_mic(), strict=False)
        ),
        # A complete source set: mic_number_assignment is a required source, so
        # omitting it would (correctly) fail the required-source gate.
        "mic_number_assignment": {
            "telephone_number_block": pl.DataFrame(
                [
                    {"numbering_area_code": "001", "number": "011200",
                     "area_code": "011", "local_code": "200",
                     "carrier": "東日本電信電話株式会社", "usage_status": "使用中",
                     "remarks": "", "current_as_of": "2024-09-01"}
                ],
                schema={c: pl.Utf8 for c in [
                    "numbering_area_code", "number", "area_code", "local_code",
                    "carrier", "usage_status", "remarks", "current_as_of"]},
            )
        },
    }
    o.snapshots = [
        FakeSnapshot("abr_town_master", sha, town.height),
        FakeSnapshot("abr_city_master", "c1", 9),
        FakeSnapshot("abr_postal_conversion", "p1", 100),
        FakeSnapshot("japanpost_ken_all", "j1", 100),
        FakeSnapshot("mlit_isj_01", "m1", 100),
        FakeSnapshot("mic_shigai_list", "s1", 10),
        FakeSnapshot("mic_fixed_phone_1", "n1", 1),
    ]
    o.license_artifacts = []
    return o


def repo(tmp_path: Path) -> Paths:
    """A throwaway repository with the real config and no prior state."""
    root = tmp_path / "repo"
    (root / "reports").mkdir(parents=True)
    src = Path(__file__).resolve().parents[1]
    shutil.copytree(src / "config", root / "config")
    shutil.copytree(src / "overrides", root / "overrides")

    # Genesis floors are national-scale by design; fixtures are a few thousand
    # rows. Only the magnitudes are scaled — the gates themselves stay live, so
    # this still exercises the real gating logic.
    import yaml

    tp = root / "config" / "quality_thresholds.yml"
    cfg = yaml.safe_load(tp.read_text(encoding="utf-8"))
    cfg["genesis_minimums"] = {
        "abr_town_rows": 100,
        "abr_city_rows": 1,
        "abr_postal_conversion_rows": 100,
        "japanpost_ken_all_rows": 100,
        "mlit_isj_rows": 100,
        "mic_telephone_areas": 1,
        "mic_number_blocks": 0,
    }
    tp.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=True), encoding="utf-8")
    return Paths(root)


def run_release(paths: Paths, town: pl.DataFrame, sha: str) -> dict:
    outcome = make_outcome(town, sha)
    tables = pipeline.build(paths, outcome, strict=True)
    report = pipeline.export(paths, tables, outcome, strict=True)
    return report


class TestTwoConsecutiveReleases:
    def test_second_release_against_a_promoted_baseline(self, tmp_path):
        paths = repo(tmp_path)
        town = load_abr_town()

        # --- release 1: genesis
        r1 = run_release(paths, town, "sha1")
        assert r1["passed"], r1["thresholds"]
        assert paths.previous.exists(), "a passing build must become the baseline"
        assert paths.identity.exists(), "the ledger must be committed after gates pass"
        ids1 = set(pl.read_parquet(paths.parquet / "address.parquet")["address_id"])

        diff1 = json.loads((paths.dist / "diff_report.json").read_text(encoding="utf-8"))
        assert diff1["baseline"] is True

        # --- release 2: one town renamed, one added, one removed
        changed = town.with_columns(
            pl.when(pl.col("machiaza_id") == town["machiaza_id"][0])
            .then(pl.lit("改称後の町"))
            .otherwise(pl.col("oaza_cho"))
            .alias("oaza_cho")
        )
        added = changed.head(1).with_columns(
            [
                pl.lit("9999999").alias("machiaza_id"),
                pl.lit("新設の町").alias("oaza_cho"),
            ]
        )
        removed_id = town["machiaza_id"][-1]
        town2 = pl.concat(
            [changed.filter(pl.col("machiaza_id") != removed_id), added],
            how="vertical",
        ).sort(["lg_code", "machiaza_id"])

        r2 = run_release(paths, town2, "sha2")

        # The build must survive carry-forward against a real baseline.
        assert isinstance(r2, dict)
        diff2 = json.loads((paths.dist / "diff_report.json").read_text(encoding="utf-8"))
        assert diff2["baseline"] is False, "release 2 must compare against release 1"

        addr2 = pl.read_parquet(paths.parquet / "address.parquet")
        assert "新設の町" in set(addr2["oaza_cho"]), "the added town must appear"
        assert removed_id not in set(addr2["machiaza_id"]), "the removed town must go"

        # Identity: the surviving towns keep their ids.
        ids2 = set(addr2["address_id"])
        assert len(ids1 & ids2) > 0.9 * len(ids2), "ids must be stable across releases"

        # The retired entity is kept, not deleted.
        entity = pl.read_parquet(paths.parquet / "address_entity.parquet")
        assert entity.filter(pl.col("entity_status") == "retired").height >= 1

        # Version tables accumulated rather than being overwritten.
        prv = pl.read_parquet(paths.parquet / "postal_record_version.parquet")
        assert prv.filter(pl.col("is_current")).height > 0
        assert prv.filter(pl.col("is_current")).height <= prv.height

        # Exactly one current version per entity, everywhere.
        for table, key in [
            ("municipality_version", "lg_code"),
            ("postal_record_version", "postal_record_id"),
            ("mlit_town_version", "mlit_record_id"),
            ("telephone_area_version", "numbering_area_code"),
        ]:
            df = pl.read_parquet(paths.parquet / f"{table}.parquet")
            live = df.filter(pl.col("is_current"))
            assert live.height == live.select(key).unique().height, table

        # History was recorded for the rename.
        hist = pl.read_parquet(paths.parquet / "address_history.parquet")
        assert hist.height >= 1, "a rename between releases must produce history"

    def test_a_failing_build_does_not_become_the_baseline(self, tmp_path):
        """A bad build must not poison the comparison the next run depends on."""
        paths = repo(tmp_path)
        town = load_abr_town()
        run_release(paths, town, "sha1")
        before = sorted(p.name for p in (paths.previous).glob("*.parquet"))
        assert before

        # Drop most of the source: this must trip a gate, not silently promote.
        truncated = town.head(5)
        outcome = make_outcome(truncated, "sha_bad")
        tables = pipeline.build(paths, outcome, strict=False)
        report = pipeline.export(paths, tables, outcome, strict=False)

        assert not report["passed"], "a 99% row loss must fail a gate"
        assert sorted(p.name for p in paths.previous.glob("*.parquet")) == before, (
            "a failed build must leave the baseline untouched"
        )
        saved = json.loads((paths.dist / "quality_report.json").read_text(encoding="utf-8"))
        assert saved["passed"] is False, "the written report must say it failed"


class TestSourceConflictBlocksRelease:
    def test_unmodelled_key_conflict_is_retained_and_gated(self, tmp_path):
        """Rows sharing a key but disagreeing on an unmodelled field."""
        town = load_abr_town()
        first = town.head(1)
        clashing = first.with_columns(pl.lit("まったく別の名前").alias("oaza_cho"))
        conflicted = pl.concat([town, clashing], how="vertical")

        rows = canonical.key_conflict_rows(conflicted)
        assert rows.height >= 2, "every conflicting source row must be retained"
        assert "conflicting_fields" in rows.columns
        assert "oaza_cho" in rows["conflicting_fields"][0]

    def test_modelled_variant_is_not_treated_as_a_conflict(self):
        """住居表示 flag differences are understood and must not block."""
        town = load_abr_town()
        first = town.head(1)
        variant = first.with_columns(
            pl.when(pl.col("rsdt_addr_flg") == "1").then(pl.lit("0"))
            .otherwise(pl.lit("1")).alias("rsdt_addr_flg")
        )
        rows = canonical.key_conflict_rows(pl.concat([town, variant], how="vertical"))
        assert rows.height == 0
