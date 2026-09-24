# LIMITATIONS.md — what V1 does not do

Read this before building anything on the data. It is the honest list: what is
deliberately out of scope, what the data cannot answer, and where V1 stops short of
what `DB_SCHEMA.md` and `DATA_MODEL.md` describe.

## Out of scope, deliberately

`POLICY.md` §3 excludes these as data sources, and nothing derived from them appears in
the artifacts:

- GSI (国土地理院) 住居表示住所 and anything derived from it
- ABR 試験公開版 街区符号 / 住居番号 / 地番
- MLIT 街区レベル位置参照情報
- 大口事業所個別郵便番号
- POI, corporations, facilities, jurisdictions, statistics, mesh / geohash systems
- any commercial or community-processed address database

ABR 街区符号 / 住居番号 / 地番 move in scope **only** when they reach 正式版 and their
licensing permits redistribution; 地番 additionally needs 登記所備付地図データ利用規約
clearance (`LICENSE_POLICY.md` §3). Trial-status availability is explicitly not a reason
to adopt early.

V2 (駅 in v1.1.0, 路線 and バス停 in v1.2.0) adds three recorded exceptions to this list,
each scoped in `POLICY.md` rather than by editing the list above:

- **§3.1** — 鉄道駅 (国土数値情報 N02) and 統計境界 (e-Stat 国勢調査 小地域), at
  municipality granularity only. Boundary geometry is read at build time and never
  shipped in a release.
- **§2's 2026-09-17 addition** — バス停留所 (国土数値情報 P11, 令和4年度版 only), also at
  municipality granularity only. A release subject, not a map-only one: `p11_bus_stop`
  and `bridge_bus_stop_municipality` are emitted by `jpac build`.
- **§3.2** — the static map in `site/`, which answers 緯度経度 → 市区町村 through 3次/6次
  standard-area mesh codes and ships simplified and clipped e-Stat geometry. It is a
  separate product from the release.

## Never

Web API or hosted service; commercial or community-processed address data as input;
any inference of address structure by a language model; any promise that a
representative coordinate is a building location. (The static map in `site/` is a
client-only page — no server, no API, no user data — and not a way to query the
release.)

## Known limitations

1. **Telephone coverage stops at the municipality where the source does.** Numbering-area
   text with sub-municipal exclusions (`夕張市（富野を除く。）`) yields municipality-level
   partial coverage and verbatim `exception_text`, not 町字-level assignment.
2. **No pre-observation history.** History begins when this project first observed a
   source. Officially published past dates (ABR `efct_date`) are carried; nothing else is
   reconstructed (spec §43). Version rows accumulate from the first build onward: each
   run reads the previous release's `*_version` tables, keeps unchanged rows with their
   original `observed_from`, closes superseded ones, and appends the new ones. A first
   release therefore has exactly one version per record, which is correct rather than
   incomplete.
3. **Postal ↔ town is many-to-many and stays that way.** No single "the" postal code for a
   town, and no single "the" town for a postal code.
4. **MLIT coordinates are representative points** for a whole 大字・町丁目.
5. **Identity continuity depends on the committed ledger.** A genesis build without it
   reproduces ids only for entities whose natural key never changed
   (`IDENTITY_MODEL.md` §3).
6. **MIC 固定電話 assignment data is annual**, so `telephone_number_block` can lag the
   area-code list.
7. **History accumulates, and what remains is the cost of that.** Until v1.3.0,
   `address_lineage`, `address_code` and `address_history` were rebuilt from the
   current run alone, so an event detected in an earlier release was gone from the
   next one — the P0 independent review 2 recorded as partially resolved. (The
   entry used to name only the first two; `address_history` had the same defect and
   was fixed with them.) All three now carry forward: lineage and history are
   unioned on content-addressed ids, and `address_code` closes an observation with
   `observed_to` instead of dropping it, which is what its documented key
   `(address_id, code_type, code_value, observed_from)` always implied. Three
   consequences remain, none of them a defect to fix:
   - **These tables only grow**, so `row_count_change.<table>` now measures
     accumulated history. A release that detects many identity events will trip the
     5% gate and need a signed approval in `config/quality_thresholds.yml`. That is
     the gate doing its job.
   - **History still starts when this project first looked** (item 2). Accumulation
     carries forward what was observed; it does not reconstruct what happened before.
   - **A genesis build without the ledger** re-detects events at a new date (item 5).
     The ids are content-addressed over what the event says and not over when it was
     seen, so those events merge with the existing rows rather than doubling them —
     but the first-observation dates are then the genesis build's, not the original's.
   - **A repeating `address_lineage` event is recorded once.** A town renamed A→B,
     back to A, then to B again yields the same `lineage_id` the first rename did, so
     the third is not recorded and the surviving row's `observed_at` still names the
     first. Excluding `observed_at` from the id is what prevents the doubling above,
     and the trade is deliberate. `address_history` does include the old and new
     values, so it is the table that can answer "how many times did this flip".
8. **Bridge endpoints are still a polymorphic `target_id`.** Primary keys, the full
   auto-accept conjunction, enumerated vocabularies and range checks are real `CHECK`
   constraints and are proven by negative-insertion tests. Foreign keys **are** now
   declared — every `address_id`, `lg_code`, `match_run_id`, `postal_record_id`,
   `postal_code`, `mlit_record_id`, `numbering_area_code`, `n02_group_code` and
   `p11_stop_id`, plus the composite `(路線名, 運営会社)` reference — and the build
   runs `PRAGMA foreign_key_check` over the finished database, so a dangling
   reference fails the release rather than waiting for a consumer to turn foreign
   keys on. Two columns are deliberately left undeclared (independent review 3):
   - **`target_id`**, because it is polymorphic: a postal record id in one bridge, an
     area code in another — and in `bridge_municipality_postal` both, in the same
     column, split by `matching_rule_id` (8,207 postal codes under P2, 1,910 record ids
     under P3). A polymorphic column cannot carry a foreign key, and it also does not
     say which table it points at. The fix is the typed endpoint columns `DB_SCHEMA.md`
     §5.1 describes; it renames a column in all six bridges, so it waits for a major
     version. The **flat view is not affected** — it already publishes typed names
     (`postal_code`, `numbering_area_code`, …) and reads `target_id` only internally.
     Planned in `BRIDGE_ENDPOINT_MIGRATION.md`, whose design decisions were settled
     2026-09-24: one typed endpoint per bridge, `bridge_municipality_postal` split in
     two so each table has exactly one, and `_v1` compatibility views shipped with
     v2.0.0 and dropped in v2.1.0.
   - **the snapshot columns** (`source_snapshot_id`, `first_observed_snapshot_id`,
     `last_observed_snapshot_id`). These satisfy the constraint today and declaring
     it would ship green, but it would be false: `source_snapshot` carries *this
     build's* payloads while those columns cite when a row was first observed and are
     carried forward untouched. The first release whose payloads change would leave
     carried-forward rows pointing at a snapshot the shipped table no longer
     contains. Making them real means making `source_snapshot` append-only, which is
     a change to what provenance means rather than a detail of the DDL.
9. **Ingestion is eager, not streaming.** A national build peaks around 4–6 GB
   (`ARCHITECTURE.md` §8).
10. **Byte reproducibility depends on the acquisition record, which is not in the
   repository.** Ids are content-addressed, every table is sorted on a total key, and
   since v1.3.0 every date an artifact carries is derived from
   `data/raw/<source>/_payload.yml` rather than from the clock, so two rebuilds of the
   same payloads are byte-identical (independent review 2's P1, now resolved for the
   normal case). What remains:
   - **`data/raw/` is gitignored**, so the manifests live only on the machine that
     acquired the payloads. The *signature* is committed
     (`ACQUISITION_DATES.md`, `tools/write_payload_manifests.py`) and regenerates them,
     but a clean checkout on another machine has neither payloads nor manifests, and a
     build there would fall back to the clock — the pre-v1.3.0 behaviour.
   - **The time of day is the payload file's mtime.** The signed fact is the calendar
     date, corroborated by the documents named in `ACQUISITION_DATES.md`; mtime
     supplies the rest and is a mutable value that copying can lose.
   - **Ids still come from the committed ledger** (item 5). Byte identity is a property
     of rebuilding the same payloads with the same ledger, not of minting an entity's
     id from nothing.
11. **Municipality mergers do not carry `address_id` forward.** Identity rule I3 needs an
   attested `lg_code` transition in `overrides/municipality_lineage.yml`. V1 shipped
   that file empty; V2 records one reorganisation (浜松市, 2024-01-01: six 1:1
   transitions and one split with two successors), which the boundary stage, the mesh
   table and the three spatial bridges use. It fires no I3 today because ABR already
   carried the new codes when jpac first observed them. Any other 廃置分合 still
   retires the town and mints a new id
   (`IDENTITY_MODEL.md` §4).

## V2: stations, lines, bus stops, mesh and the map

12. **Stations and map answers stop at the municipality.** e-Stat's 小地域 are ~3.1×
   coarser than jpac's 町字, so neither a station nor a map point is ever tied to an
   `address_id` (`POLICY.md` §3.1, §4).
13. **The boundary is the 2020 census survey-district boundary**, not the
   administrative boundary, and the publisher does not join prefecture seams. A point
   inside a few-metre overlap at a seam keeps both municipalities; a point in a seam gap
   gets none, never the nearest.
14. **旧浜松市北区 cannot be split.** It became part of 中央区 and part of 浜名区 in
   2024, and that new ward line is absent from 2020 data, so points there keep two
   candidates (`bridge_station_municipality`, the mesh table and the map alike).
15. **A superseded boundary code resolves only through the lineage file, and the path is
   in a column rather than a note.** The 54 stations in the former 浜松 wards
   (`STATION_JOIN_PREFLIGHT.md` §4.3) and the bus-stop rows carrying the same codes
   (`BUS_STOP_PLAN.md`) are read through `overrides/municipality_lineage.yml` by all
   three bridges, as the mesh table and the map already were. A 1:1 succession stays
   `contains`+`auto` — the old ward's area lies inside the new one — and records the
   path as `match_method = 'spatial_containment_via_lineage'`; 旧北区 keeps both
   successors instead (item 14). `boundary_jis_city_code` is kept either way, so the
   code the publisher stated is never overwritten. Two things remain: a code in neither
   the current municipalities nor the lineage file still keeps `lg_code` NULL, and a
   reader who filters on `match_method = 'spatial_containment'` alone will silently
   exclude the lineage-resolved rows.
16. **The map's mesh colouring is an approximation for display** (each 6次 cell sampled
   at its centre and corners) and its outlines are simplified; neither decides anything.
   The clicked point is decided against the unsimplified boundary.
