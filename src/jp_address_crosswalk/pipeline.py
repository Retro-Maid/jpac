"""End-to-end orchestration: read → build → validate → quality → diff → export.

Starts from payloads that have already been accepted into ``data/raw/``.
Acquiring them — discovery, download, licence re-hashing, promotion — is managed
internally and is not part of this repository, so nothing here touches the
network.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path

import polars as pl
import yaml

from . import PARSER_VERSION, __version__
from .build import (
    canonical,
    diffing,
    mlit_bridge,
    postal,
    quality,
    source_aliases,
    telephone,
    versioning,
)
from .build import (
    overrides as overrides_mod,
)
from .build.common import BuildContext, assert_bridge_invariants
from .errors import (
    RequiredSourceMissing,
    ValidationFailed,
)
from .export import writers
from .identity import IdentityLedger
from .logging_setup import get_logger, stage_context
from .normalize import NORMALIZATION_PROFILE_VERSION
from .payload import (
    MANIFEST_NAME,
    FetchResult,
    PayloadManifest,
    load_payload_manifest,
)
from .snapshot import (
    SourceSnapshot,
    check_license_drift,
    check_schema_drift,
    sha256_file,
    utcnow,
)
from .sources.abr import AbrSource
from .sources.base import DiscoveredResource, Discovery
from .sources.japanpost import JapanPostSource
from .sources.mic_area_code import MicAreaCodeSource
from .sources.mic_number_assignment import MicNumberAssignmentSource
from .sources.mlit import MlitSource

log = get_logger(__name__)

SOURCE_CLASSES = {
    "abr": AbrSource,
    "japanpost": JapanPostSource,
    "mlit": MlitSource,
    "mic_area_code": MicAreaCodeSource,
    "mic_number_assignment": MicNumberAssignmentSource,
}


@dataclass
class Paths:
    root: Path

    @property
    def config(self) -> Path: return self.root / "config"
    @property
    def cache(self) -> Path: return self.root / "data" / "cache"
    @property
    def raw(self) -> Path: return self.root / "data" / "raw"
    @property
    def dist(self) -> Path: return self.root / "dist"
    @property
    def parquet(self) -> Path: return self.dist / "parquet"
    @property
    def reports(self) -> Path: return self.root / "reports"
    @property
    def identity(self) -> Path: return self.root / "identity" / "address_id_ledger.csv.gz"
    @property
    def overrides(self) -> Path: return self.root / "overrides"
    @property
    def expected_schema(self) -> Path: return self.config / "expected_schema"
    @property
    def previous(self) -> Path: return self.root / "data" / "previous" / "parquet"


@dataclass
class Config:
    sources: dict
    matching_rules: dict
    thresholds: dict

    @classmethod
    def load(cls, paths: Paths) -> Config:
        s = yaml.safe_load((paths.config / "sources.yml").read_text(encoding="utf-8"))
        m = yaml.safe_load((paths.config / "matching_rules.yml").read_text(encoding="utf-8"))
        t = yaml.safe_load((paths.config / "quality_thresholds.yml").read_text(encoding="utf-8"))
        return cls(sources=s["sources"],
                   matching_rules=m, thresholds=t)


@dataclass
class FetchOutcome:
    snapshots: list = field(default_factory=list)
    parsed: dict = field(default_factory=dict)
    license_artifacts: list = field(default_factory=list)
    discoveries: dict = field(default_factory=dict)
    override_outcome: object | None = None
    # Attested source-name aliases whose preconditions no longer hold. Kept so a
    # lapsed alias is reported rather than quietly forgotten.
    stale_name_aliases: list = field(default_factory=list)




# ----------------------------------------------------------------- discovery





# --------------------------------------------------------------------- fetch

# A payload's resource key is its filename without the extension, so a renamed
# file is not a different file — it is a missing resource. These are the keys
# each adapter looks up by name; the rest of the sources take whatever files are
# present. Checking them here turns a silent half-build into a named error.
REQUIRED_PAYLOAD_KEYS = {
    "abr": ("town_master", "city_master", "postal_conversion"),
    "japanpost": ("ken_all",),
    "mic_area_code": ("shigai_list",),
}




def rebuild_offline(
    paths: Paths, strict: bool = True, allow_new_baselines: bool = False
) -> FetchOutcome:
    """Parse everything from ``data/raw/`` with no network access at all.

    This is the full-rebuild guarantee of spec §45: a clean checkout plus the
    already-acquired payloads, the committed config and the identity ledger must
    reproduce the whole database. It also makes the pipeline testable on a
    machine that can never reach the publishers.

    Licence and schema gates were applied when the payloads were accepted into
    ``data/raw/``; this path deliberately re-runs neither, and therefore never
    produces a release on its own.
    """
    cfg = Config.load(paths)
    outcome = FetchOutcome()

    for name, spec in cfg.sources.items():
        src_dir = paths.raw / name
        required_keys = REQUIRED_PAYLOAD_KEYS.get(name, ())
        if not src_dir.exists():
            if spec.get("required", True) and strict:
                raise RequiredSourceMissing(
                    "no accepted payload in data/raw for a required source. "
                    "Acquisition is managed internally and is not part of this "
                    "repository; see README.",
                    source=name,
                    expected_dir=str(src_dir),
                    expected_files=", ".join(f"{k}.*" for k in required_keys) or "any",
                )
            log.warning("skipping source with no local payload", source=name)
            continue

        src = SOURCE_CLASSES[name](spec)
        manifest = load_payload_manifest(src_dir)
        fetched = _accepted_payloads(src_dir, name, manifest)
        if not fetched:
            if spec.get("required", True) and strict:
                raise RequiredSourceMissing(
                    "the source directory exists but holds no payload file.",
                    source=name,
                    expected_dir=str(src_dir),
                    expected_files=", ".join(f"{k}.*" for k in required_keys) or "any",
                )
            continue

        for key, fr in sorted(fetched.items()):
            log.debug(
                "accepted payload", source=name, key=key, file=fr.path.name,
                bytes=fr.size, sha256=fr.sha256[:12],
            )

        missing = [k for k in required_keys if k not in fetched]
        if missing and strict:
            # Renaming a payload used to drop its table without a word.
            raise RequiredSourceMissing(
                "a payload this source parses by name is missing. The resource "
                "key is the filename without its extension, so a renamed file "
                "reads as an absent one.",
                source=name,
                expected_dir=str(src_dir),
                missing=", ".join(f"{k}.*" for k in missing),
                found=", ".join(sorted(fetched)) or "(none)",
            )
        if missing:
            log.warning("payload missing", source=name, missing=missing)

        schemas = src.inspect(fetched)
        for key, info in schemas.items():
            check_schema_drift(
                f"{name}.{key}", info, paths.expected_schema / f"{name}.{key}.yml",
                allow_create=allow_new_baselines,
            )
        parsed = src.parse(fetched)
        outcome.parsed[name] = parsed

        discovery = Discovery(
            source=name,
            source_page_url=spec.get("landing_page", ""),
            resources=[
                DiscoveredResource(
                    key=k, url=fr.url,
                    # Must match what the online adapter emits: snapshot lookup
                    # is by dataset-name prefix, and a mismatch would silently
                    # attribute MIC rows to the ABR town snapshot.
                    dataset_name=_offline_dataset_name(name, k),
                    version=manifest.resource(k).get("source_version"),
                    published_at=manifest.resource(k).get("published_at"),
                )
                for k, fr in sorted(fetched.items())
            ],
            license_name=manifest.license_name or spec.get("license", {}).get("name"),
            license_url=manifest.license_url or spec.get("license", {}).get("url"),
            license_text_sha256=manifest.observed_text_sha256("primary_terms"),
        )
        outcome.discoveries[name] = discovery
        row_counts = _resource_row_counts(fetched, schemas, parsed)
        built = src.build_snapshots(discovery, fetched, schemas, row_counts)
        resource_key = {r.dataset_name: r.key for r in discovery.resources}
        for snap in built:
            # ``downloaded_at`` defaults to now, which for an offline rebuild is
            # the build time, not the acquisition time. When the side that did
            # acquire the payload says otherwise, it is right.
            observed = manifest.resource(resource_key.get(snap.dataset_name, ""))
            if observed.get("downloaded_at"):
                snap.downloaded_at = str(observed["downloaded_at"])
        outcome.snapshots.extend(built)
        outcome.license_artifacts.extend(
            _license_artifacts(name, spec, manifest, built)
        )
        log.info("source rebuilt from data/raw", source=name,
                 tables={k: v.height for k, v in parsed.items()})

    return outcome




# Everything a release ships. One list, because `jpac export` rewrites the
# artifacts and must rewrite their digests from the same definition.
RELEASE_ARTIFACT_NAMES = (
    "jp_address_crosswalk.parquet",
    "jp_address_crosswalk.sqlite",
    "jp_address_crosswalk.csv.gz",
    "quality_report.json",
    "diff_report.json",
    "QUALITY_REPORT.md",
    "DIFF_REPORT.md",
    "SOURCES.yml",
    "NOTICE.md",
)


def release_artifacts(paths: Paths) -> list[Path]:
    """The release files that currently exist, in a stable order."""
    return [p for p in (paths.dist / n for n in RELEASE_ARTIFACT_NAMES) if p.exists()]


# --------------------------------------------------------------------- build

def build(paths: Paths, outcome: FetchOutcome, strict: bool = True) -> dict[str, pl.DataFrame]:
    cfg = Config.load(paths)
    observed_from = utcnow()[:10]
    built_at = utcnow()

    snapshots = outcome.snapshots
    snapshot_ids = sorted(s.source_snapshot_id for s in snapshots)
    match_run_id = "run_" + hashlib.blake2s(
        "|".join([*snapshot_ids, cfg.matching_rules["version"],
                  NORMALIZATION_PROFILE_VERSION, __version__]).encode(),
        digest_size=10,
    ).hexdigest()
    primary = next((s for s in snapshots if s.dataset_name == "abr_town_master"), None)
    snapshot_id = primary.source_snapshot_id if primary else (
        snapshot_ids[0] if snapshot_ids else "snap_unknown"
    )

    # Each source's own snapshot. Passing the ABR town snapshot everywhere made
    # every postal, MLIT and MIC row claim it came from the ABR town master —
    # provenance corruption that silently invalidates any audit of those rows.
    aggregate_snapshots: list[SourceSnapshot] = []

    def snapshot_for(*dataset_prefixes: str) -> str:
        for prefix in dataset_prefixes:
            matching = sorted(
                s.source_snapshot_id for s in snapshots
                if s.dataset_name.startswith(prefix)
            )
            if matching:
                # MLIT ships 47 prefectural files; one id cannot represent them
                # all, so the aggregate table cites a deterministic digest of the
                # full set and match_run_input keeps every individual snapshot.
                if len(matching) == 1:
                    return matching[0]
                agg_id = "snapset_" + hashlib.blake2s(
                    "|".join(matching).encode(), digest_size=10
                ).hexdigest()
                # A synthetic id must still be a real source_snapshot row, or
                # every table citing it dangles. The aggregate records what it
                # covers; the individual snapshots remain in match_run_input.
                if not any(s.source_snapshot_id == agg_id for s in aggregate_snapshots):
                    members = [
                        s for s in snapshots if s.source_snapshot_id in set(matching)
                    ]
                    first = members[0]
                    aggregate_snapshots.append(
                        SourceSnapshot(
                            source_snapshot_id=agg_id,
                            provider=first.provider,
                            dataset_name=f"{prefix}__aggregate_of_{len(members)}",
                            source_page_url=first.source_page_url,
                            download_url=f"{len(members)} files; see match_run_input",
                            license_name=first.license_name,
                            license_url=first.license_url,
                            license_text_sha256=first.license_text_sha256,
                            source_version=first.source_version,
                            published_at=first.published_at,
                            downloaded_at=first.downloaded_at,
                            sha256=hashlib.sha256(
                                "|".join(sorted(m.sha256 for m in members)).encode()
                            ).hexdigest(),
                            file_size=sum(m.file_size for m in members),
                            row_count=sum(
                                m.row_count for m in members if m.row_count is not None
                            ) or None,
                            schema_fingerprint=first.schema_fingerprint,
                            status="aggregate",
                        )
                    )
                return agg_id
        return snapshot_id

    snap_abr_town = snapshot_for("abr_town_master")
    snap_abr_city = snapshot_for("abr_city_master")
    snap_abr_conv = snapshot_for("abr_postal_conversion")
    snap_postal = snapshot_for("japanpost_ken_all")
    snap_mlit = snapshot_for("mlit_isj")
    snap_mic_area = snapshot_for("mic_shigai_list")
    snap_mic_block = snapshot_for("mic_fixed_phone")

    ctx = BuildContext(
        match_run_id=match_run_id, snapshot_id=snapshot_id,
        observed_from=observed_from, built_at=built_at,
        matching_rule_version=cfg.matching_rules["version"],
        normalization_profile_version=NORMALIZATION_PROFILE_VERSION,
    )

    abr = outcome.parsed["abr"]
    towns = canonical.prepare_towns(abr["town"])
    # ABR publishes some 町字 twice, differing only in the 住居表示 flags. Collapse
    # to one canonical row and keep every published variant (canonical.py).
    key_conflicts = canonical.key_conflict_rows(
        towns,
        canonical.load_reviewed_conflicts(paths.overrides / "source_conflicts.yml"),
    )
    towns, rsdt_variants, rsdt_conflicts = canonical.split_rsdt_variants(towns)
    ledger = IdentityLedger.load(paths.identity)
    muni_lineage = _load_municipality_lineage(paths)
    canon = canonical.build_canonical(
        towns, abr["city"], ledger, snap_abr_town, observed_from, muni_lineage,
        city_snapshot_id=snap_abr_city,
    )
    identity_review = canon.pop("_identity_review", []) + rsdt_conflicts
    # Staged, not committed. The ledger is the permanent record of every
    # address_id ever minted; a build that later fails validation must not be
    # able to retire entities or mint ids in it. commit_identity_ledger()
    # promotes the staged file only after every gate has passed.
    ledger.save(_staged_ledger(paths))

    tables: dict[str, pl.DataFrame] = dict(canon)
    tables["address_rsdt_variant"] = rsdt_variants
    # Lossless record of any natural-key conflict this project does not model.
    # Non-empty means a release is blocked (quality.check_source_conflicts).
    tables["address_key_conflict"] = key_conflicts
    address = tables["address"]
    municipality = tables["municipality_version"]

    # --- Postal
    postal_tables = postal.prepare_postal(
        outcome.parsed["japanpost"]["ken_all"], snap_postal, observed_from
    )
    tables |= postal_tables
    # The conversion-table bridge is produced from the conversion table, so it
    # cites that snapshot rather than the town master.
    tables["bridge_address_postal_code"] = postal.build_postal_code_bridge(
        address, abr["postal_conversion"], postal_tables["postal_code_entity"],
        replace(ctx, snapshot_id=snap_abr_conv),
    )
    tables["bridge_municipality_postal"] = postal.build_municipality_postal_bridge(
        abr["postal_conversion"], postal_tables["postal_record_version"],
        municipality, replace(ctx, snapshot_id=snap_abr_conv),
    )
    covered = set(
        tables["bridge_address_postal_code"]
        .filter(pl.col("address_id").is_not_null())["address_id"]
        .to_list()
    )
    tables["bridge_address_postal"] = postal.build_postal_record_bridge(
        address, postal_tables["postal_record_version"], covered,
        replace(ctx, snapshot_id=snap_postal),
    )

    # --- MLIT
    isj_version = next(
        (s.source_version for s in snapshots if s.dataset_name.startswith("mlit_isj")),
        "",
    ) or ""
    mlit_tables = mlit_bridge.prepare_mlit(
        outcome.parsed["mlit"]["isj"], snap_mlit, observed_from, isj_version
    )
    tables |= mlit_tables
    tables["bridge_address_mlit"] = mlit_bridge.build_mlit_bridge(
        address, mlit_tables["mlit_town_version"], replace(ctx, snapshot_id=snap_mlit)
    )

    # --- Telephone
    mic = outcome.parsed["mic_area_code"]
    tel_tables = telephone.prepare_telephone(
        mic["telephone_area"], mic["telephone_area_coverage"], snap_mic_area,
        observed_from,
    )
    tables |= tel_tables
    name_aliases, stale_aliases = source_aliases.load_name_aliases(
        paths.overrides / "source_name_aliases.yml",
        municipality,
        {
            "mic_shigai_list": {
                (r["pref_name"], r["municipality_name"])
                for r in tel_tables["telephone_area_coverage"].iter_rows(named=True)
                if r.get("pref_name") and r.get("municipality_name")
            }
        },
    )
    outcome.stale_name_aliases = stale_aliases
    muni_tel, addr_tel = telephone.build_telephone_bridges(
        address, municipality, tel_tables["telephone_area_coverage"],
        replace(ctx, snapshot_id=snap_mic_area),
        name_aliases=name_aliases,
    )
    tables["bridge_municipality_telephone"] = muni_tel
    tables["bridge_address_telephone"] = addr_tel

    if "mic_number_assignment" in outcome.parsed:
        blocks = outcome.parsed["mic_number_assignment"]["telephone_number_block"]
        tables["telephone_number_block"] = blocks.with_columns(
            [
                pl.format("blk_{}_{}", pl.col("area_code"), pl.col("local_code"))
                .alias("block_id"),
                pl.lit(snap_mic_block).alias("source_snapshot_id"),
            ]
        ).unique(subset=["block_id"], keep="first").sort("block_id")

    # --- Provenance
    tables["source_snapshot"] = pl.DataFrame(
        [s.as_dict() for s in [*snapshots, *aggregate_snapshots]]
    ).unique(subset=["source_snapshot_id"], keep="first").sort("source_snapshot_id") \
        if snapshots else pl.DataFrame()
    # Always carries its schema, even with no rows. An empty frame with no
    # columns is skipped by the SQLite writer, so a documented table would
    # simply not exist in the shipped database — and an offline rebuild, which
    # performs no licence check, produces exactly that.
    tables["snapshot_license_artifact"] = pl.DataFrame(
        outcome.license_artifacts,
        schema={
            "artifact_id": pl.Utf8, "source": pl.Utf8, "role": pl.Utf8,
            "license_name": pl.Utf8, "license_url": pl.Utf8,
            "text_sha256": pl.Utf8, "baseline_sha256": pl.Utf8,
            "reviewed_on": pl.Utf8, "review_decision": pl.Utf8,
            "note": pl.Utf8, "source_snapshot_id": pl.Utf8,
        },
    )
    tables["match_run"] = pl.DataFrame(
        [{
            "match_run_id": match_run_id, "started_at": built_at,
            "matching_rule_version": ctx.matching_rule_version,
            "normalization_profile_version": ctx.normalization_profile_version,
            "code_version": __version__,
        }]
    )
    tables["match_run_input"] = pl.DataFrame(
        [
            {"match_run_id": match_run_id, "source_snapshot_id": s.source_snapshot_id,
             "role": _snapshot_role(s.dataset_name)}
            for s in snapshots
        ]
    ).unique().sort(["match_run_id", "source_snapshot_id", "role"]) if snapshots else pl.DataFrame()

    tables["_identity_review"] = pl.DataFrame(
        [{k: json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v
          for k, v in r.items()} for r in identity_review]
    ) if identity_review else pl.DataFrame()

    # Manual overrides: a human decision beats a rule, but only while the source
    # state it was made against still holds (docs/MATCHING_RULES.md §10).
    ovr = overrides_mod.load_overrides(paths.overrides / "manual_overrides.yml")
    if ovr:
        sha_by_dataset = {s.dataset_name: s.sha256 for s in snapshots}
        stale = overrides_mod.check_staleness(ovr, sha_by_dataset)
        outcome.override_outcome = overrides_mod.apply_overrides(tables, ovr, stale)
    overrides_mod.write_stale_report(
        outcome.override_outcome or overrides_mod.OverrideOutcome(),
        paths.reports / "override_stale.csv",
    )

    # Version tables accumulate rather than overwrite (docs/DATA_MODEL.md §3).
    prev = paths.previous if paths.previous.exists() else None
    for table, key, vid in [
        ("municipality_version", ["lg_code"], "municipality_version_id"),
        ("postal_record_version", ["postal_record_id"], "postal_record_version_id"),
        ("mlit_town_version", ["mlit_record_id"], "mlit_town_version_id"),
        ("telephone_area_version", ["numbering_area_code"], "telephone_area_version_id"),
    ]:
        if table in tables:
            tables[table] = versioning.carry_forward(
                tables[table], prev, table, key, vid, observed_from
            )

    tables["address_history"] = versioning.build_address_history(
        tables["address"], prev, observed_from, snap_abr_town
    )

    validation_problems = _validate(tables, strict, cfg.matching_rules)
    tables["_validation_problems"] = pl.DataFrame(
        {"problem": validation_problems}, schema={"problem": pl.Utf8}
    )
    return tables


# Online adapters name their datasets themselves; an offline rebuild has no
# discovery step, so the same names are reconstructed here.
_OFFLINE_DATASET_NAMES = {
    ("abr", "town_master"): "abr_town_master",
    ("abr", "city_master"): "abr_city_master",
    ("abr", "pref_master"): "abr_pref_master",
    ("abr", "postal_conversion"): "abr_postal_conversion",
    ("japanpost", "ken_all"): "japanpost_ken_all",
    ("mic_area_code", "shigai_list"): "mic_shigai_list",
}


def _resource_row_counts(fetched: dict, schemas: dict, parsed: dict) -> dict:
    """Row count per resource, preferring parsed output then the inspect count.

    Leaving this null for multi-file sources meant 60 of 66 snapshots reported no
    row count at all, which is a documented provenance field (spec §25).
    """
    total = sum(df.height for df in parsed.values())
    out: dict[str, int | None] = {}
    for key in fetched:
        if key in parsed:
            out[key] = parsed[key].height
        elif schemas.get(key) is not None and schemas[key].row_count is not None:
            out[key] = schemas[key].row_count
        elif len(fetched) == 1:
            out[key] = total
        else:
            out[key] = None
    return out


def _accepted_payloads(
    src_dir: Path, source: str, manifest: PayloadManifest
) -> dict[str, FetchResult]:
    """Every file in ``data/raw/<source>/`` that is a payload.

    The manifest lives in the same directory and is not one: parsing it as a
    payload would fail a magic-byte check at best, and at worst invent a
    snapshot for a file the publisher never sent.
    """
    fetched: dict[str, FetchResult] = {}
    for path in sorted(src_dir.iterdir()):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        overrides = manifest.resource(path.stem)
        # A local absolute path is not provenance, and shipping one in a
        # redistributed SOURCES.yml leaks the builder's filesystem while telling
        # the reader nothing. Say what is actually known: this came from an
        # accepted payload, and name it.
        url = overrides.get("download_url") or f"local-payload:raw/{source}/{path.name}"
        fetched[path.stem] = FetchResult(
            url=url, final_url=url, path=path,
            sha256=sha256_file(path), size=path.stat().st_size,
            etag=overrides.get("etag"),
            last_modified=overrides.get("last_modified"),
            content_type=None,
        )
    return fetched


def _license_artifacts(
    source: str,
    spec: dict,
    manifest: PayloadManifest,
    snapshots: list[SourceSnapshot],
) -> list[dict]:
    """One row per terms document that applies to this source.

    ``snapshot_license_artifact`` exists because a single licence triple could
    not express the ABR DCAT-vs-terms disagreement or MLIT's base terms beside
    its per-download stipulation (docs/DB_SCHEMA.md, R1-P0-8). An offline
    rebuild left the table empty, so a shipped release could not evidence which
    terms were reviewed at all — the one thing a redistributed payload most
    needs to carry (docs/LICENSE_POLICY.md §4).

    The committed baselines are always emitted: they are committed, human
    reviewed, and true regardless of what this build can observe. The observed
    hash is emitted only when the acquisition side stated one in the payload
    manifest, and where both exist they are compared — a difference is licence
    drift and stops the release (docs/POLICY.md §11).

    Only the hash is gated here. ``license_name`` and ``license_url`` are not:
    this build never reads the terms page, so it has no independent observation
    of either, and comparing a configured value against itself would be a check
    that cannot fail.
    """
    licence = spec.get("license", {}) or {}
    # Adapters emit resources in sorted key order, so the first snapshot is a
    # stable choice and a rebuild stays byte-identical.
    snapshot_id = snapshots[0].source_snapshot_id if snapshots else None
    rows: list[dict] = []
    for artifact in licence.get("artifacts", []) or []:
        role = str(artifact.get("role", "unknown"))
        baseline = artifact.get("text_sha256")
        observed = manifest.observed_text_sha256(role)
        if baseline is None:
            # Recorded as evidence rather than as a gate — the advertised CC BY
            # entry and MLIT's download stipulation, for example.
            decision = "not_gated"
        elif observed is None:
            # The baseline stands; this build simply had nothing to compare it
            # against. Distinct from `baseline_missing`, which means no reviewed
            # value exists and blocks the release.
            decision = "not_observed"
        else:
            check_license_drift(
                f"{source}.{role}",
                artifact.get("name"),
                artifact.get("url"),
                observed,
                {
                    "name": artifact.get("name"),
                    "url": artifact.get("url"),
                    "text_sha256": baseline,
                },
            )
            decision = "baseline_match"
        rows.append(
            {
                "artifact_id": "lic_" + hashlib.blake2s(
                    f"{source}|{role}|{baseline or ''}|{observed or ''}".encode(),
                    digest_size=8,
                ).hexdigest(),
                "source": source,
                "role": role,
                "license_name": artifact.get("name"),
                "license_url": artifact.get("url"),
                "text_sha256": observed,
                "baseline_sha256": baseline,
                "reviewed_on": licence.get("reviewed_on"),
                "review_decision": decision,
                "note": artifact.get("note"),
                "source_snapshot_id": snapshot_id,
            }
        )
    if not any(r["review_decision"] == "baseline_match" for r in rows):
        log.warning(
            "no terms text was observed for this build; the shipped record "
            "carries the reviewed baselines but cannot evidence what the "
            "publisher served at acquisition time",
            source=source,
        )
    return rows


def _offline_dataset_name(source: str, key: str) -> str:
    named = _OFFLINE_DATASET_NAMES.get((source, key))
    if named:
        return named
    if source == "mlit" and key.startswith("isj_"):
        return f"mlit_{key}"
    if source == "mic_number_assignment" and key.startswith("fixed_"):
        return f"mic_fixed_phone_{key.removeprefix('fixed_')}"
    if source == "japanpost" and key.startswith("delta_"):
        return f"japanpost_{key.removeprefix('delta_')}_offline"
    return f"{source}_{key}"


def _staged_ledger(paths: Paths) -> Path:
    return paths.identity.with_suffix(paths.identity.suffix + ".staged")


def commit_identity_ledger(paths: Paths) -> None:
    """Promote the staged ledger. Called only after every gate has passed."""
    staged = _staged_ledger(paths)
    if staged.exists():
        staged.replace(paths.identity)
        log.info("identity ledger committed", path=str(paths.identity))


def _source_of(dataset_name: str) -> str:
    """Map a dataset name back to its data/raw/<source>/ directory."""
    for prefix, source in [
        ("abr_", "abr"), ("japanpost_", "japanpost"), ("mlit_", "mlit"),
        ("mic_shigai", "mic_area_code"), ("mic_fixed", "mic_number_assignment"),
    ]:
        if dataset_name.startswith(prefix):
            return source
    return dataset_name


def _snapshot_role(dataset_name: str) -> str:
    if dataset_name.startswith("abr_town") or dataset_name.startswith("abr_city"):
        return "canonical"
    if "postal_conversion" in dataset_name:
        return "mapping"
    if dataset_name.startswith("japanpost"):
        return "corroboration"
    return "target"


def _load_municipality_lineage(paths: Paths) -> dict[str, str]:
    path = paths.overrides / "municipality_lineage.yml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        str(e["old_lg_code"]): str(e["new_lg_code"])
        for e in (data.get("transitions") or [])
    }


def _validate(
    tables: dict[str, pl.DataFrame], strict: bool, matching_rules: dict
) -> list[str]:
    with stage_context("build", "validate"):
        problems: list[str] = []
        bridge_families = {
            "bridge_address_postal_code": "postal",
            "bridge_address_postal": "postal",
            "bridge_municipality_postal": "postal",
            "bridge_address_mlit": "mlit",
            "bridge_address_telephone": "telephone",
            "bridge_municipality_telephone": "telephone",
        }
        configured_version = str(matching_rules.get("version", ""))
        for name in quality.BRIDGES:
            df = tables.get(name)
            if df is not None:
                problems += assert_bridge_invariants(df, name)
                family = bridge_families[name]
                configured_ids = {
                    str(rule.get("id"))
                    for rule in matching_rules.get(family, [])
                    if rule.get("id") is not None
                }
                emitted_ids = set(df["matching_rule_id"].drop_nulls().to_list())
                unknown_ids = sorted(emitted_ids - configured_ids)
                if unknown_ids:
                    problems.append(
                        f"{name}: matching_rule_id absent from config ({unknown_ids})"
                    )
                bad_versions = df.filter(
                    pl.col("matching_rule_version") != configured_version
                )
                if bad_versions.height:
                    problems.append(
                        f"{name}: matching_rule_version differs from config "
                        f"({bad_versions.height} rows)"
                    )

        town_tel = tables.get("bridge_address_telephone")
        if town_tel is not None and town_tel.height:
            bad = town_tel.filter(
                pl.col("target_id").is_not_null()
                | (pl.col("relation_type") != "unresolved")
                | (pl.col("matching_rule_id") != "T10")
                | (pl.col("candidate_count") != 0)
                | (pl.col("coverage_type") != "municipality_only")
                | pl.col("derivation").is_not_null()
            )
            if bad.height:
                problems.append(
                    "bridge_address_telephone: municipality evidence expanded or "
                    f"misrepresented at town level ({bad.height} rows)"
                )
        problems += writers.assert_code_columns_are_strings(
            {k: v for k, v in tables.items() if not k.startswith("_")}
        )

        addr = tables.get("address")
        if addr is not None and addr.height:
            bad = addr.filter(~pl.col("address_id").str.contains(r"^jpa1[0-9a-hjkmnp-tv-z]{16}$"))
            if bad.height:
                problems.append(f"address: malformed address_id ({bad.height} rows)")
            dupes = addr.height - addr["address_id"].n_unique()
            if dupes:
                problems.append(f"address: duplicate address_id ({dupes} rows)")

        prv = tables.get("postal_record_version")
        if prv is not None and prv.height:
            bad = prv.filter(
                pl.col("valid_from").is_not_null() | pl.col("valid_to").is_not_null()
            )
            if bad.height:
                problems.append(
                    f"postal_record_version: valid_from/to must be NULL ({bad.height} rows)"
                )
            bad = prv.filter(~pl.col("postal_code").str.contains(r"^\d{7}$"))
            if bad.height:
                problems.append(f"postal_record_version: bad postal_code ({bad.height} rows)")
            bad = prv.filter(
                pl.col("old_postal_code").is_not_null()
                & ~pl.col("old_postal_code").str.contains(r"^\d{3,5}$")
            )
            if bad.height:
                problems.append(
                    f"postal_record_version: bad old_postal_code ({bad.height} rows)"
                )

        mlv = tables.get("mlit_town_version")
        if mlv is not None and mlv.height:
            bad = mlv.filter(
                pl.col("latitude").is_not_null()
                & ((pl.col("latitude") < 20) | (pl.col("latitude") > 46))
            )
            if bad.height:
                problems.append(f"mlit_town_version: latitude out of range ({bad.height})")
            bad = mlv.filter(
                pl.col("longitude").is_not_null()
                & ((pl.col("longitude") < 122) | (pl.col("longitude") > 154))
            )
            if bad.height:
                problems.append(f"mlit_town_version: longitude out of range ({bad.height})")

        if problems:
            log.error("validation failed", count=len(problems), problems=problems[:20])
            if strict:
                raise ValidationFailed("invariant violations", problems=problems[:20])
        else:
            log.info("validation passed")
        return problems


# -------------------------------------------------------------------- export

def export(
    paths: Paths, tables: dict[str, pl.DataFrame], outcome: FetchOutcome,
    strict: bool = True,
) -> dict:
    public = {k: v for k, v in tables.items() if not k.startswith("_") and v.width}

    writers.write_parquet(public, paths.parquet)
    flat_all = writers.build_flat_view(public, accepted_only=False)
    flat = writers.build_flat_view(public, accepted_only=True)

    flat.write_parquet(paths.dist / "jp_address_crosswalk.parquet", compression="zstd")
    writers.write_sqlite(public, flat, flat_all, paths.dist / "jp_address_crosswalk.sqlite")
    writers.write_csv_gz(flat, paths.dist / "jp_address_crosswalk.csv.gz")

    cfg = Config.load(paths)
    data_version = _data_version(outcome)
    report = quality.QualityReport(
        code_version=__version__, data_version=data_version, built_at=utcnow(),
        matching_rule_version=str(cfg.matching_rules["version"]),
    )
    report.sources = {
        s.dataset_name: {
            "source_version": s.source_version, "row_count": s.row_count,
            "sha256": s.sha256, "license_name": s.license_name,
            "license_url": s.license_url, "downloaded_at": s.downloaded_at,
            "schema_fingerprint": s.schema_fingerprint,
            "source_page_url": s.source_page_url, "download_url": s.download_url,
        }
        for s in outcome.snapshots
    }
    keys = {
        "address": ["address_id"], "municipality": ["lg_code"],
        "postal_record": ["postal_record_id"], "postal_code_entity": ["postal_code"],
        "mlit_town": ["mlit_record_id"], "telephone_area": ["numbering_area_code"],
        "telephone_number_block": ["block_id"],
    }
    report.tables = {
        n: quality.table_metrics(n, df, keys.get(n))
        for n, df in sorted(public.items()) if not n.startswith("bridge_")
    }
    report.bridges = {
        n: quality.bridge_metrics(public[n]) for n in quality.BRIDGES if n in public
    }
    report.identity = {
        "review_required": tables.get("_identity_review", pl.DataFrame()).height,
    }
    report.thresholds = quality.check_genesis_minimums(public, cfg.thresholds)
    report.thresholds += quality.check_required_and_duplicates(
        public, cfg.thresholds, keys
    )
    invariant_failures = tables.get(
        "_validation_problems", pl.DataFrame(schema={"problem": pl.Utf8})
    ).height
    invariant_limit = int(
        cfg.thresholds.get("blocking", {}).get("invariant_failures_max", 0)
    )
    report.thresholds.append(
        {
            "check": "invariant_failures",
            "observed": invariant_failures,
            "limit": invariant_limit,
            "status": "pass" if invariant_failures <= invariant_limit else "fail",
        }
    )
    report.thresholds.append(
        {
            "check": "unmodelled_source_key_conflicts",
            "observed": public.get("address_key_conflict", pl.DataFrame()).height,
            "limit": 0,
            "status": "pass"
            if not public.get("address_key_conflict", pl.DataFrame()).height
            else "fail",
        }
    )

    previous_report = None
    prev_json = paths.previous.parent / "quality_report.json"
    if prev_json.exists():
        previous_report = json.loads(prev_json.read_text(encoding="utf-8"))
        # Reports before quality schema 1.1 did not expose this value even
        # though every bridge row carried it. Recover it from the comparison
        # artifact so a one-time rule migration can name both endpoints.
        if not previous_report.get("matching_rule_version"):
            for bridge in quality.BRIDGES:
                prior_bridge = paths.previous / f"{bridge}.parquet"
                if not prior_bridge.exists():
                    continue
                versions = (
                    pl.read_parquet(prior_bridge, columns=["matching_rule_version"])
                    ["matching_rule_version"]
                    .drop_nulls()
                    .unique()
                    .to_list()
                )
                if len(versions) == 1:
                    previous_report["matching_rule_version"] = versions[0]
                    break
    current_summary = {
        "tables": report.tables,
        "bridges": report.bridges,
        # Source snapshots carry their own row counts; excluding them meant the
        # configured source-volume gate never actually ran on a source.
        "sources": report.sources,
        "retired_with_lineage": (
            tables.get("address_lineage", pl.DataFrame())
            .filter(pl.col("relation_type") == "retired").height
            if "address_lineage" in tables
            and "relation_type" in tables["address_lineage"].columns
            else 0
        ),
        "matching_rule_version": cfg.matching_rules["version"],
    }
    report.thresholds += quality.compare_with_previous(
        current_summary, previous_report, cfg.thresholds
    )

    # Diff first: unexplained removals become a threshold row, so `passed`
    # accounts for them in lenient mode too.
    diff = diffing.build_diff(
        public, paths.previous if paths.previous.exists() else None, strict
    )
    report.thresholds += diffing.data_loss_threshold(diff)

    # Evaluated BEFORE serialization. Writing first left `passed` at its default
    # True, so a failing build published a report that said PASS.
    passed = quality.evaluate(report, strict=False)
    quality.write_reports(
        report, paths.dist / "quality_report.json", paths.dist / "QUALITY_REPORT.md"
    )
    quality.write_review_queue(
        {n: public[n] for n in quality.BRIDGES if n in public},
        paths.reports / "review_required.csv",
    )
    diffing.write_diff(diff, paths.dist / "diff_report.json", paths.dist / "DIFF_REPORT.md")

    _write_sources_and_notice(paths, outcome)

    writers.write_sha256sums(release_artifacts(paths), paths.dist / "SHA256SUMS")

    # A failed build never becomes the baseline, regardless of --lenient. A bad
    # baseline would hide the very regressions the comparison gates exist for.
    if passed:
        versioning.promote_to_previous(
            paths.parquet, paths.previous,
            [paths.dist / "quality_report.json"],
        )
        commit_identity_ledger(paths)
    else:
        log.error(
            "build did not pass its gates; baseline and identity ledger left "
            "untouched",
            failures=[t for t in report.thresholds if t.get("status") == "fail"][:5],
        )

    quality.evaluate(report, strict)
    return report.as_dict()


def _data_version(outcome: FetchOutcome) -> str:
    dates = [s.downloaded_at[:10] for s in outcome.snapshots if s.downloaded_at]
    return f"data-{max(dates)}" if dates else "data-unknown"


def _attribution_section(
    cfg: Config, heading: str, *intro_and_form: str
) -> list[str]:
    """One attribution block, in each publisher's own wording.

    PDL 1.0 §1.1 says a publisher's own 重要情報 examples replace the PDL default,
    and the unmodified and processed forms genuinely differ — MLIT's processed
    example is 「…をもとに○○作成」, not 「…を加工して作成」. Both forms therefore
    live in ``config/sources.yml`` and are emitted from it, so NOTICE.md cannot
    drift from the reviewed wording.
    """
    *intro, form = intro_and_form
    blocks: list[str] = []
    for spec in cfg.sources.values():
        text = (spec.get("license", {}).get("attribution", {}) or {}).get(form)
        if text and text.strip() not in blocks:
            blocks.append(text.strip())
    if not blocks:
        return []
    body: list[str] = []
    for block in blocks:
        if body:
            body.append("")
        body.extend(block.splitlines())
    return [heading, "", *intro, "", "```", *body, "```", ""]


def _write_sources_and_notice(paths: Paths, outcome: FetchOutcome) -> None:
    """NOTICE.md is generated from what was actually used, so it cannot drift."""
    cfg = Config.load(paths)
    # A redistributed release has to be able to evidence which terms text was in
    # force. `license_text_sha256` is what this build observed, which is null
    # unless the acquisition side supplied a payload manifest; the reviewed
    # baseline is committed here and is always available, so both travel with
    # the record rather than only the one that may be missing.
    reviewed = {
        name: next(
            (
                a for a in (spec.get("license", {}).get("artifacts") or [])
                if a.get("role") == "primary_terms"
            ),
            {},
        )
        for name, spec in cfg.sources.items()
    }
    decisions = {
        (r["source"], r["role"]): r["review_decision"]
        for r in outcome.license_artifacts
    }
    entries = []
    for s in outcome.snapshots:
        source = _source_of(s.dataset_name)
        baseline = reviewed.get(source, {})
        entries.append(
            {
                "provider": s.provider, "dataset_name": s.dataset_name,
                "source_page_url": s.source_page_url, "download_url": s.download_url,
                "license_name": s.license_name, "license_url": s.license_url,
                "license_text_sha256": s.license_text_sha256,
                "license_text_reviewed_sha256": baseline.get("text_sha256"),
                "license_reviewed_on": cfg.sources.get(source, {})
                    .get("license", {}).get("reviewed_on"),
                "license_review_decision": decisions.get(
                    (source, "primary_terms"), "not_observed"
                ),
                "source_version": s.source_version, "published_at": s.published_at,
                "downloaded_at": s.downloaded_at, "sha256": s.sha256,
                "file_size": s.file_size, "row_count": s.row_count,
                "schema_fingerprint": s.schema_fingerprint,
                "parser_version": s.parser_version,
                # Where the exact bytes are kept, so an older release stays
                # rebuildable after a publisher overwrites a "current file" URL.
                "archive_asset": "raw-sources.tar.zst",
                "archive_member": f"raw/{source}/",
            }
        )
    paths.dist.mkdir(parents=True, exist_ok=True)
    (paths.dist / "SOURCES.yml").write_text(
        yaml.safe_dump(
            {"generated_at": utcnow(), "parser_version": PARSER_VERSION,
             "sources": entries},
            allow_unicode=True, sort_keys=True,
        ),
        encoding="utf-8", newline="\n",
    )

    lines = [
        "# NOTICE",
        "",
        "This distribution is derived from official Japanese public data.",
        "The code is MIT licensed; **the data is not**. Each source keeps its own",
        "terms, reproduced below. See `DATA_LICENSE.md` and `docs/LICENSE_POLICY.md`.",
        "",
        "A release carries two different things, and they do not take the same",
        "attribution. The built artifacts are **processed** data and must not be",
        "presented as if produced by the publishing authority. The accepted payloads",
        "in `raw-sources.tar.zst` are the publishers' own files, **unmodified**, and",
        "take the plain 出典 form — calling them 加工 would misdescribe them.",
        "",
        *_attribution_section(
            cfg,
            "## 出典 / Attribution — 加工済み成果物 (processed artifacts)",
            "Applies to every file this build produces: the Parquet, SQLite and CSV.gz",
            "artifacts and the reports beside them.",
            "processed",
        ),
        *_attribution_section(
            cfg,
            "## 出典 / Attribution — 同梱された原データ (unmodified payloads)",
            "Applies to `raw-sources.tar.zst`, which contains each publisher's file",
            "exactly as accepted. Redistribution of these files unmodified is permitted",
            "by each publisher's terms; see `DATA_LICENSE.md`.",
            "unmodified",
        ),
        "日本郵便株式会社は郵便番号データについて著作権を主張していません",
        "（「郵便番号データに限っては日本郵便株式会社は著作権を主張しません。",
        "自由に配布していただいて結構です。」）。出典表示は本プロジェクトの自主的な記載です。",
        "",
        "## Snapshots used in this build",
        "",
        "| Provider | Dataset | Version | Downloaded | SHA-256 | License |",
        "|---|---|---|---|---|---|",
    ]
    for e in sorted(entries, key=lambda x: x["dataset_name"]):
        lines.append(
            f"| {e['provider']} | {e['dataset_name']} | {e['source_version'] or '—'} "
            f"| {e['downloaded_at']} | `{(e['sha256'] or '')[:16]}…` "
            f"| [{e['license_name'] or '—'}]({e['license_url'] or ''}) |"
        )
    lines += [
        "",
        "## Excluded on licence grounds",
        "",
        "ABR `地番マスター` and `地番マスター位置参照拡張` are distributed under a separate",
        "登記所備付地図データ利用規約 via G-Spatial Information Center, which this project",
        "has not cleared for redistribution. They are not fetched, parsed, or shipped.",
        "",
    ]
    for name, spec in cfg.sources.items():
        lic = spec.get("license", {})
        if lic.get("statement"):
            lines.append(f"- **{name}**: {lic['statement']}")
    (paths.dist / "NOTICE.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
    )
