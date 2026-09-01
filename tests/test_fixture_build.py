"""End-to-end build on committed fixtures, with no network access.

This is the test that proves the pipeline works: it runs canonical → postal →
MLIT → telephone → export against real (but small) official data and asserts the
specific cases the spec names. It also runs the build twice in shuffled input
order and requires byte-identical output, which is what catches the
non-determinism a Polars join can otherwise introduce
(docs/ARCHITECTURE.md §6, docs/TEST_STRATEGY.md §5).
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from jp_address_crosswalk.build import canonical, mlit_bridge, postal, telephone
from jp_address_crosswalk.build.common import BuildContext, assert_bridge_invariants
from jp_address_crosswalk.export import writers
from jp_address_crosswalk.identity import IdentityLedger
from jp_address_crosswalk.sources.abr import TOWN_EXPECTED_COLUMNS
from jp_address_crosswalk.sources.japanpost import KEN_ALL_COLUMNS, SPECIAL_SUFFIXES
from jp_address_crosswalk.sources.mic_area_code import (
    normalize_area_code,
    normalize_numbering_area_code,
    parse_area_text,
)
from jp_address_crosswalk.sources.mlit import ISJ_COLUMNS, ISJ_RENAME

FIXTURES = Path(__file__).parent / "fixtures"
SNAP = "snap_fixture"
OBS = "2026-08-23"

pytestmark = pytest.mark.skipif(
    not (FIXTURES / "abr_town.csv").exists(),
    reason="fixtures not generated",
)


def _utf8(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns([pl.col(c).cast(pl.Utf8) for c in df.columns])


def load_abr_town() -> pl.DataFrame:
    df = _utf8(pl.read_csv(FIXTURES / "abr_town.csv", infer_schema_length=0))
    missing = [c for c in TOWN_EXPECTED_COLUMNS if c not in df.columns]
    assert not missing, f"fixture is missing ABR columns: {missing}"
    return df.with_columns(pl.col("lg_code").str.slice(0, 5).alias("jis_city_code"))


def load_abr_city() -> pl.DataFrame:
    df = _utf8(pl.read_csv(FIXTURES / "abr_city.csv", infer_schema_length=0))
    return df.with_columns(pl.col("lg_code").str.slice(0, 5).alias("jis_city_code"))


def load_conversion() -> pl.DataFrame:
    df = _utf8(pl.read_csv(FIXTURES / "abr_postal_conversion.csv", infer_schema_length=0))
    return df.with_columns(
        pl.when(
            pl.col("machiaza_id").is_null() | (pl.col("machiaza_id").str.len_chars() == 0)
        )
        .then(None)
        .otherwise(pl.col("machiaza_id"))
        .alias("machiaza_id")
    )


def load_ken_all() -> pl.DataFrame:
    df = _utf8(
        pl.read_csv(
            FIXTURES / "japanpost_ken_all.csv", has_header=False,
            new_columns=KEN_ALL_COLUMNS, infer_schema_length=0, quote_char='"',
        )
    )
    kind = pl.when(pl.col("town").is_null()).then(pl.lit("no_listing"))
    for suffix, k in SPECIAL_SUFFIXES:
        kind = kind.when(pl.col("town").str.ends_with(suffix)).then(pl.lit(k))
    return df.with_columns(
        [
            pl.col("old_postal_code_raw").str.strip_chars_end(" ").alias("old_postal_code"),
            kind.otherwise(pl.lit("town")).alias("record_kind"),
        ]
    )


def load_mlit() -> pl.DataFrame:
    df = _utf8(pl.read_csv(FIXTURES / "mlit_isj.csv", infer_schema_length=0))
    df = df.select(ISJ_COLUMNS).rename(ISJ_RENAME)
    return df.with_columns(
        [
            pl.col("latitude").cast(pl.Float64, strict=False),
            pl.col("longitude").cast(pl.Float64, strict=False),
            pl.lit("2025").alias("fiscal_year"),
        ]
    )


def load_mic() -> tuple[pl.DataFrame, pl.DataFrame]:
    rows = [
        line.split("\t")
        for line in (FIXTURES / "mic_shigai_list.tsv").read_text(encoding="utf-8").splitlines()
    ]
    areas, coverage = [], []
    for code_raw, text, area_code, digits in rows[1:]:
        code = normalize_numbering_area_code(code_raw)
        if not code or not area_code.isdigit():
            continue
        areas.append({
            "numbering_area_code": code,
            "area_code": normalize_area_code(area_code),
            "area_code_raw": area_code,
            "area_text_raw": text, "local_digit_pattern": digits,
            "current_as_of": "2026-03-01",
        })
        coverage.extend(parse_area_text(code, text))
    area_df = pl.DataFrame(areas, schema={
        "numbering_area_code": pl.Utf8, "area_code": pl.Utf8,
        "area_code_raw": pl.Utf8, "area_text_raw": pl.Utf8,
        "local_digit_pattern": pl.Utf8, "current_as_of": pl.Utf8})
    cov_df = pl.DataFrame(coverage, schema={
        "numbering_area_code": pl.Utf8, "clause_raw": pl.Utf8, "pref_name": pl.Utf8,
        "county_name": pl.Utf8, "municipality_name": pl.Utf8,
        "sub_municipal_text": pl.Utf8, "qualifier": pl.Utf8, "coverage_type": pl.Utf8,
        "exception_text": pl.Utf8, "parse_rule": pl.Utf8})
    return area_df, cov_df


def build_all(shuffle_seed: int | None = None) -> dict[str, pl.DataFrame]:
    ctx = BuildContext(
        match_run_id="run_fixture", snapshot_id=SNAP, observed_from=OBS,
        built_at="2026-08-23T00:00:00Z", matching_rule_version="1.2.0",
        normalization_profile_version="1.0.0",
    )
    town = load_abr_town()
    if shuffle_seed is not None:
        town = town.sample(fraction=1.0, shuffle=True, seed=shuffle_seed)

    town = canonical.prepare_towns(town)
    town, rsdt, _conflicts = canonical.split_rsdt_variants(town)
    canon = canonical.build_canonical(town, load_abr_city(), IdentityLedger(), SNAP, OBS)
    canon.pop("_identity_review", None)

    tables = dict(canon)
    tables["address_rsdt_variant"] = rsdt
    address = tables["address"]

    ptab = postal.prepare_postal(load_ken_all(), SNAP, OBS)
    tables |= ptab
    tables["bridge_address_postal_code"] = postal.build_postal_code_bridge(
        address, load_conversion(), ptab["postal_code_entity"], ctx
    )
    tables["bridge_municipality_postal"] = postal.build_municipality_postal_bridge(
        load_conversion(), ptab["postal_record_version"],
        tables["municipality_version"], ctx,
    )
    covered = set(
        tables["bridge_address_postal_code"]
        .filter(pl.col("address_id").is_not_null())["address_id"].to_list()
    )
    tables["bridge_address_postal"] = postal.build_postal_record_bridge(
        address, ptab["postal_record_version"], covered, ctx
    )

    mtab = mlit_bridge.prepare_mlit(load_mlit(), SNAP, OBS, "19.0b")
    tables |= mtab
    tables["bridge_address_mlit"] = mlit_bridge.build_mlit_bridge(
        address, mtab["mlit_town_version"], ctx
    )

    area, cov = load_mic()
    ttab = telephone.prepare_telephone(area, cov, SNAP, OBS)
    tables |= ttab
    mt, at = telephone.build_telephone_bridges(
        address, tables["municipality_version"], ttab["telephone_area_coverage"], ctx
    )
    tables["bridge_municipality_telephone"] = mt
    tables["bridge_address_telephone"] = at
    return tables


@pytest.fixture(scope="module")
def built() -> dict[str, pl.DataFrame]:
    return build_all()


class TestPipelineRuns:
    def test_produces_every_table(self, built):
        for name in [
            "address", "address_entity", "municipality_version",
            "postal_code_entity", "postal_record_version", "mlit_town_version",
            "telephone_area_version", "bridge_address_postal_code",
            "bridge_address_postal", "bridge_address_mlit",
            "bridge_address_telephone", "bridge_municipality_postal",
            "bridge_municipality_telephone",
        ]:
            assert name in built, f"missing {name}"
            assert built[name].height > 0, f"{name} is empty"

    def test_all_bridge_invariants_hold(self, built):
        problems = []
        for name, df in built.items():
            if name.startswith("bridge_"):
                problems += assert_bridge_invariants(df, name)
        assert problems == []

    def test_code_columns_are_strings(self, built):
        assert writers.assert_code_columns_are_strings(built) == []


class TestNamedSpecCases:
    def test_nishishinjuku_is_not_collapsed(self, built):
        """Spec §15: 西新宿 must map to every chome, not to one."""
        addr = built["address"]
        chome = addr.filter(pl.col("oaza_cho") == "西新宿")
        assert chome.height >= 5, "fixture should contain the 西新宿 chome"
        bridge = built["bridge_address_postal"]
        p5 = bridge.filter(
            (pl.col("matching_rule_id") == "P5")
            & pl.col("address_id").is_in(chome["address_id"].to_list())
        )
        if p5.height:
            assert (p5["candidate_count"] > 1).all()
            assert (p5["verification_status"] != "auto").all()

    def test_special_postal_records_are_present_and_excluded(self, built):
        prv = built["postal_record_version"]
        kinds = set(prv["record_kind"].to_list())
        assert {"no_listing", "city_banchi", "ichien"} <= kinds, kinds
        specials = set(prv.filter(pl.col("record_kind") != "town")["postal_record_id"])
        used = set(
            built["bridge_address_postal"]
            .filter(pl.col("target_id").is_not_null())["target_id"].to_list()
        )
        assert not (specials & used)

    def test_toshima_mura_five_digit_old_code(self, built):
        prv = built["postal_record_version"]
        toshima = prv.filter(pl.col("city").str.contains("利島"))
        assert toshima.height > 0
        assert toshima["old_postal_code"][0] == "10003"

    def test_leading_zero_old_code_survives(self, built):
        prv = built["postal_record_version"]
        zeros = prv.filter(pl.col("old_postal_code").str.starts_with("0"))
        assert zeros.height > 0
        assert prv.schema["old_postal_code"] == pl.Utf8

    def test_yubari_is_split_across_numbering_areas(self, built):
        """夕張市 is in areas 003 and 004-2 — it must not look like one."""
        cov = built["telephone_area_coverage"]
        yubari = cov.filter(pl.col("clause_raw").str.contains("夕張市"))
        assert yubari.height >= 2
        assert yubari["numbering_area_code"].n_unique() >= 2

    def test_every_parsed_coverage_clause_survives_into_the_table(self, built):
        """One clause can name several municipalities; all of them must be kept.

        `coverage_id` was keyed on (area, clause_raw), which is the *same* string
        for every municipality named by 「上北郡（東北町、野辺地町、横浜町及び
        六ヶ所村に限る。）」. The dedup then kept one row per clause and dropped
        the rest — 140 rows nationally, leaving 122 郡部の町村 with no numbering
        area. Nothing compared the parser's output with what was stored, so it
        was invisible.
        """
        from jp_address_crosswalk.sources.mic_area_code import parse_area_text

        version = built["telephone_area_version"]
        parsed = sum(
            len(parse_area_text(r["numbering_area_code"], r["area_text_raw"]))
            for r in version.iter_rows(named=True)
        )
        cov = built["telephone_area_coverage"]
        assert cov.height == parsed, (
            f"parser produced {parsed} clause rows, table holds {cov.height}"
        )
        assert cov["coverage_id"].n_unique() == cov.height

    def test_a_clause_naming_several_municipalities_keeps_all_of_them(self, built):
        cov = built["telephone_area_coverage"]
        multi = (
            cov.group_by(["numbering_area_code", "clause_raw"])
            .agg(pl.col("municipality_name").n_unique().alias("n"))
            .filter(pl.col("n") > 1)
        )
        assert multi.height > 0, "the fixture should contain a multi-municipality clause"

    def test_exclusion_wording_is_preserved_verbatim(self, built):
        cov = built["telephone_area_coverage"]
        partial = cov.filter(pl.col("coverage_type") == "partial")
        assert partial.height > 0
        assert partial["exception_text"].drop_nulls().str.contains("除く").any()

    def test_no_municipality_statement_is_expanded_to_towns(self, built):
        """The address bridge is lossless but deliberately unresolved."""
        bat = built["bridge_address_telephone"]
        assert bat.height == built["address"].height
        assert bat["address_id"].n_unique() == bat.height
        assert bat["target_id"].null_count() == bat.height
        assert bat["derivation"].null_count() == bat.height
        assert bat["relation_type"].unique().to_list() == ["unresolved"]
        assert bat["matching_rule_id"].unique().to_list() == ["T10"]
        assert bat["coverage_type"].unique().to_list() == ["municipality_only"]

    def test_yubari_evidence_remains_at_municipality_level(self, built):
        muni = built["municipality_version"].filter(pl.col("city") == "夕張市")
        assert muni.height > 0
        bridge = built["bridge_municipality_telephone"].filter(
            pl.col("lg_code").is_in(muni["lg_code"].to_list())
        )
        assert bridge["target_id"].drop_nulls().n_unique() >= 2

    def test_ward_layer_present_for_designated_city(self, built):
        addr = built["address"]
        assert addr.filter(pl.col("ward").is_not_null()).height > 0

    def test_kyoto_and_county_municipalities_present(self, built):
        addr = built["address"]
        assert addr.filter(pl.col("city").str.contains("京都")).height > 0
        assert addr.filter(pl.col("county").is_not_null()).height > 0

    def test_abr_duplicate_key_towns_collapse_with_variants_kept(self, built):
        addr, rsdt = built["address"], built["address_rsdt_variant"]
        assert addr.select(["lg_code", "machiaza_id"]).unique().height == addr.height
        multi = addr.filter(pl.col("rsdt_variant_count") > 1)
        if multi.height:
            keys = multi.select(["lg_code", "machiaza_id"])
            kept = rsdt.join(keys, on=["lg_code", "machiaza_id"], how="inner")
            assert kept.height > keys.height, "both published variants must survive"

    def test_mlit_and_abr_chome_forms_match(self, built):
        """ABR 旭ケ丘 + １丁目 must reconcile with MLIT 旭ケ丘一丁目."""
        bam = built["bridge_address_mlit"]
        exact = bam.filter(pl.col("matching_rule_id") == "M1")
        assert exact.height > 0, "code+name agreement should occur in the fixture"


class TestExport:
    def test_rejected_and_superseded_edges_blank_columns_but_keep_the_address(
        self, built, tmp_path
    ):
        """The property the two flat-view implementations once disagreed on.

        A build with no rejected and no superseded bridge rows cannot tell a
        correct implementation from one that filters after the LEFT JOIN, since
        the filter never fires. So rows are marked here on purpose: one
        rejected, one superseded. A wrong implementation deletes those
        addresses; a right one blanks their columns and keeps them.
        """
        import sqlite3

        public = {k: v for k, v in built.items() if not k.startswith("_") and v.width}
        bridge = public["bridge_address_postal_code"]
        # .unique(): the bridge fans out, so the first two rows can be the same
        # address — and then only one of the two cases would be exercised.
        victims = bridge["address_id"].unique().sort().head(2).to_list()
        assert len(victims) == 2, "fixture needs two distinct bridged addresses"
        public["bridge_address_postal_code"] = bridge.with_columns(
            [
                pl.when(pl.col("address_id") == victims[0])
                .then(pl.lit("manually_rejected"))
                .otherwise(pl.col("verification_status"))
                .alias("verification_status"),
                pl.when(pl.col("address_id") == victims[1])
                .then(pl.lit(False))
                .otherwise(pl.col("is_current"))
                .alias("is_current"),
            ]
        )

        flat = writers.build_flat_view(public, accepted_only=True)
        flat_all = writers.build_flat_view(public, accepted_only=False)
        for v in victims:
            row = flat.filter(pl.col("address_id") == v)
            assert row.height > 0, f"{v} was dropped from the Parquet flat view"
            assert row["postal_code"].null_count() == row.height

        db = tmp_path / "excluded.sqlite"
        writers.write_sqlite(public, flat, flat_all, db)
        conn = sqlite3.connect(db)
        try:
            for v in victims:
                n = conn.execute(
                    "SELECT COUNT(*) FROM address_crosswalk WHERE address_id = ?", (v,)
                ).fetchone()[0]
                assert n > 0, f"{v} was dropped from the SQL view"
                codes = conn.execute(
                    "SELECT postal_code FROM address_crosswalk WHERE address_id = ?",
                    (v,),
                ).fetchall()
                assert all(c[0] is None for c in codes), codes
            # And the two implementations still agree row for row.
            cur = conn.execute("SELECT * FROM address_crosswalk")
            assert [d[0] for d in cur.description] == list(flat.columns)

            def canon(x: object) -> str:
                if x is None:
                    return chr(0)
                if isinstance(x, bool):
                    return "1" if x else "0"
                if isinstance(x, float):
                    return f"{x:.9g}"
                return str(x)

            sql_rows = sorted(chr(31).join(canon(c) for c in r) for r in cur)
            pq_rows = sorted(
                chr(31).join(canon(c) for c in r) for r in flat.iter_rows()
            )
            assert sql_rows == pq_rows
        finally:
            conn.close()

    def test_flat_view_and_artifacts(self, built, tmp_path):
        public = {k: v for k, v in built.items() if not k.startswith("_") and v.width}
        writers.write_parquet(public, tmp_path / "parquet")
        flat = writers.build_flat_view(public, accepted_only=True)
        flat_all = writers.build_flat_view(public, accepted_only=False)
        assert flat.height > 0
        assert flat_all.height >= flat.height

        for col in ["postal_relation_type", "postal_confidence", "mlit_confidence",
                    "telephone_relation_type"]:
            assert col in flat.columns

        db = tmp_path / "t.sqlite"
        writers.write_sqlite(public, flat, flat_all, db)
        assert db.exists()

        import sqlite3

        conn = sqlite3.connect(db)
        try:
            n = conn.execute("SELECT COUNT(*) FROM address_crosswalk").fetchone()[0]
            assert n > 0
            cols = [r[1] for r in conn.execute("PRAGMA table_info(address_crosswalk)")]
            assert "postal_confidence" in cols
            # The SQL view and build_flat_view are two implementations of one
            # definition. They shipped with different column lists once
            # (old_postal_code missing from SQL, *_is_current only in SQL), so
            # the identity is asserted rather than assumed.
            assert cols == list(flat.columns), (
                f"only in SQL {sorted(set(cols) - set(flat.columns))}, "
                f"only in Parquet {sorted(set(flat.columns) - set(cols))}"
            )
            assert [
                r[1] for r in conn.execute("PRAGMA table_info(address_crosswalk_all)")
            ] == list(flat_all.columns)
            assert conn.execute(
                "SELECT COUNT(*) FROM address_crosswalk_all"
            ).fetchone()[0] >= n
            # Rejecting or superseding a match may blank a column; it may never
            # remove the address. Filtering after the LEFT JOIN would.
            assert conn.execute(
                "SELECT COUNT(DISTINCT address_id) FROM address_crosswalk"
            ).fetchone()[0] == conn.execute(
                "SELECT COUNT(*) FROM address"
            ).fetchone()[0]
            # The gate must hold in the shipped database, not only in Python.
            over = conn.execute(
                "SELECT COUNT(*) FROM bridge_address_postal "
                "WHERE verification_status='auto' AND candidate_count>1"
            ).fetchone()[0]
            assert over == 0
        finally:
            conn.close()

        csv = tmp_path / "t.csv.gz"
        writers.write_csv_gz(flat, csv)
        assert csv.stat().st_size > 0


class TestDeterminism:
    def test_shuffled_input_yields_identical_output(self, tmp_path):
        """Polars joins are not order-stable; the canonical sort must absorb that."""
        a = build_all(shuffle_seed=1)
        b = build_all(shuffle_seed=99)

        da, db = tmp_path / "a", tmp_path / "b"
        writers.write_parquet({k: v for k, v in a.items() if v.width}, da)
        writers.write_parquet({k: v for k, v in b.items() if v.width}, db)

        names = sorted(p.name for p in da.glob("*.parquet"))
        assert names == sorted(p.name for p in db.glob("*.parquet"))
        for name in names:
            fa = pl.read_parquet(da / name)
            fb = pl.read_parquet(db / name)
            assert fa.equals(fb), f"{name} differs between shuffled runs"

    def test_address_ids_are_stable_across_runs(self):
        a, b = build_all(shuffle_seed=7), build_all(shuffle_seed=8)
        assert set(a["address"]["address_id"]) == set(b["address"]["address_id"])
