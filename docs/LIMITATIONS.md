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

V2 (unreleased) adds two recorded exceptions to this list, each scoped in `POLICY.md`
rather than by editing the list above:

- **§3.1** — 鉄道駅 (国土数値情報 N02) and 統計境界 (e-Stat 国勢調査 小地域), at
  municipality granularity only. Boundary geometry is read at build time and never
  shipped in a release.
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
7. **`address_lineage` and `address_code` do not yet accumulate across releases.**
   The `*_version` tables carry forward and `address_history` records attribute
   changes, but lineage events and code observations are rebuilt from the current
   run only, so an event detected in an earlier release is not retained in a later
   one. Identified by independent review 2 as a partially-resolved P0; the
   remaining work is to union the
   previous release's rows before appending the current run's, which requires the
   same immutable-id treatment the version tables already have.
8. **SQLite enforces most, not all, of the documented schema.** Primary keys, the
   full auto-accept conjunction, enumerated vocabularies and range checks are real
   `CHECK` constraints and are proven by negative-insertion tests. Foreign keys are
   **not** declared, and bridge endpoints are exported as a generic `target_id` rather
   than the concrete per-bridge columns `DB_SCHEMA.md` describes. Referential
   integrity is therefore asserted by the test suite rather than by the database
   (independent review 3).
9. **Ingestion is eager, not streaming.** A national build peaks around 4–6 GB
   (`ARCHITECTURE.md` §8).
10. **Rebuilds are logically reproducible, not byte-reproducible.** Ids are
   content-addressed and every table is sorted on a total key, so the same inputs
   always produce the same *logical* data. But `observed_from`, `created_at` and
   `updated_at` come from wall-clock time, so two rebuilds of identical snapshots
   differ byte-for-byte. Making them byte-identical means deriving observation
   metadata from a persisted acquisition record rather than from the clock
   (independent review 2, partially-resolved P1).
11. **Municipality mergers do not carry `address_id` forward.** Identity rule I3 needs an
   attested `lg_code` transition in `overrides/municipality_lineage.yml`. V1 shipped
   that file empty; V2 records one reorganisation (浜松市, 2024-01-01: six 1:1
   transitions and one split with two successors), which the boundary stage, the mesh
   table and the three spatial bridges use. It fires no I3 today because ABR already carried the new codes when jpac first
   observed them. Any other 廃置分合 still retires the town and mints a new id
   (`IDENTITY_MODEL.md` §4).

## V2 (unreleased): stations, mesh and the map

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
