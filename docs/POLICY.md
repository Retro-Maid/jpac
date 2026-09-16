# POLICY.md — jp-address-crosswalk

The rules this project is built under. They outrank convenience, match rate and
schedule, and the rest of `docs/` is written against them. Section numbers are
stable and are cited from the code and the other documents.

## 1. Project goal

Build a re-generatable, history-aware, provenance-tracked open crosswalk database
between Japan's official address / area code systems:

- Digital Agency **Address Base Registry (ABR)** — 町字マスター (canonical)
- **Japan Post** postal codes (current 7-digit + former 3/5-digit)
- **MLIT** 位置参照情報 大字・町丁目レベル (codes + representative lat/lon)
- **MIC (総務省)** 番号区画 / 市外局番 / 固定電話番号指定状況

Deliverables are data files (Parquet / SQLite / CSV.gz), not a service.

## 2. Canonical source

**ABR 町字マスター (正式版) is the Canonical Address Source.** V1 granularity is
**町字 (machi-aza)** — nothing below it.

The ABR 町字ID (`machiaza_id`) is **NOT** the database primary key. Every canonical
address carries an independent, persistent `address_id`. See
[`IDENTITY_MODEL.md`](IDENTITY_MODEL.md).

## 3. Source restrictions (hard)

Allowed origins: Digital Agency, Japan Post Co., MLIT, MIC, e-Gov — **fetched directly
from the publisher**. Third-party mirrors are not a normal acquisition path.

Explicitly **out of scope for V1**:

- GSI (国土地理院) 住居表示住所 and anything derived from it
- ABR 試験公開版 街区符号 / 住居番号 / 地番
- 大口事業所個別郵便番号 (business-specific postal codes)
- MLIT 街区レベル位置参照情報
- POI, facilities, corporations/法人番号, schools, stations, roads, electoral districts,
  jurisdictions (police/fire/tax), school districts, census areas, population,
  H3 / Geohash / S2 / mesh codes, hazard data, zoning, IP or mobile geolocation
- Any third-party/commercial address database as a data source

Third-party OSS may be studied for **design ideas only**. Never as input data, and
never as ground truth for matching.

## 3.1 V2 scope extension (駅 → 市区町村)

Added 2026-09-05. **§3 above is left exactly as written.** It records what V1's
scope was, and editing that list would destroy the record rather than extend it —
so the `stations` entry there stays, and this section states what V2 adds on top.

### What is added

| | |
|---|---|
| **Subjects** | 鉄道駅・鉄道路線 (国土数値情報 N02)、統計境界 (e-Stat 国勢調査 小地域) |
| **Origins** | **None.** 総務省統計局 is already covered by MIC in §3, and 国土数値情報 by MLIT |
| **Granularity** | 市区町村 only. See below |

### Granularity: 市区町村 is a ceiling, not a starting point

A station is attributed to a municipality and **never to a 町字**. This is not a
gap to be closed later by trying harder. e-Stat's 231,668 町丁・字等 are ~3.1×
coarser than jpac's 726,170 町字 (measured 2026-09-05), so assigning a station
to a 町字 through them would be expanding a municipality-level statement to
町字 level — which §4 below already lists as a defect.

The schema enforces it: `bridge_station_municipality` carries `lg_code` and has
no `address_id` column. A table that cannot express the wrong answer cannot
drift into it.

### New data class: geometry that is read but not distributed

V1 sources are tabular and are redistributed as accepted. The V2 boundary source
is different in kind, so it gets an explicit rule:

- **Read at build time.** Polygons are the instrument that turns a coordinate
  into a `lg_code`.
- **Not shipped.** No release artifact carries geometry. What ships is the
  correspondence and its provenance.
- **Not re-derived into coordinates either.** Centroids, areas and perimeters
  computed from a boundary source are not emitted (see
  `docs/STATION_JOIN_PLAN.md` §3.1 and the `excluded_columns` entry in
  `config/sources.yml`).

This keeps the release the same kind of thing it has always been — a
crosswalk with its evidence — and keeps the project out of the business of
redistributing spatial data, where the licence questions are materially harder
(`docs/N03_BOUNDARY_DESIGN.md` §8 records one that stopped an earlier design).

### Release independence

Both V2 sources are `required: false` in `config/sources.yml`. A V1 release must
not depend on them and must not be blocked by their absence, their licence
review, or a publisher outage.

## 3.2 Map extension: boundary geometry in `site/` only

Added 2026-09-15, on the maintainer's decision. **§3.1 is left as written and
still governs every release artifact** (`jpac build`, `dist/`): the release ships
no geometry. This section adds one exception, scoped to the static map in
`site/`.

### Why

The map answers "which municipality is this point in". A 3次メッシュ-level
answer cannot settle a point in a cell that two municipalities — or two
prefectures — share; it can only list both. The maintainer requires the clicked
point itself to be decided, and that needs the boundary where it runs.

### What `site/` may ship (built by `tools/build_site_geo.py`)

| Payload | Geometry | Used for |
|---|---|---|
| `exact/` | e-Stat 小地域 dissolved per municipality, **unsimplified**, clipped to every 3次 cell a boundary or the coastline crosses. Coordinates quantised to 1e-6° (~10 cm; the census boundary itself is accurate to metres) | Deciding a click by point-in-polygon |
| `line/`, `coarse/` | The same, simplified (coverage-preserving, per prefecture) | Drawing only. **Never used to decide anything** |

### Conditions

- **Source: e-Stat only.** Its terms permit redistribution with the 出典 and a
  statement that it was processed (`config/sources.yml`
  `estat_boundary.attribution.processed`), both shown on the page. N03 and
  基盤地図情報 remain excluded: the 国土地理院長承認 question in
  `docs/N03_BOUNDARY_DESIGN.md` §8 is unresolved.
- **Never called 行政界 / 行政区域.** It is the census survey-district boundary
  as of 2020 (the publisher's own first caveat), and the page says so.
- **§4 holds at point level.** A point inside pieces of two municipalities (the
  prefecture seams e-Stat does not join) keeps both. A point inside none is not
  snapped to the nearest. 旧浜松市北区 — split in 2024, its new ward line absent
  from 2020 data — stays two candidates even at point level.
- **Not a release artifact.** `site/data/geo/` is produced by a separate tool, is
  not listed in a release's `SOURCES.yml` / `NOTICE.md`, and `jpac build` is
  unchanged. The map links to jpac by `lg_code` and nothing else.

### Station points and municipality-level jpac data (added 2026-09-16)

The map also answers "what else does jpac already know about this municipality".
Three payloads join the geometry above, built by `tools/build_site_data.py` into
`site/data/jpac_data.js`:

| Payload | What it is | Key |
|---|---|---|
| `stations` | One **representative point** per N02 station group, with name and operator | `lg_code`, 0 or more |
| `postal` | 郵便番号 per municipality; P3 records (「以下に掲載がない場合」等) resolved to their real 7 digits, with the class kept | `lg_code` |
| `telephone` | 市外局番 per municipality via the numbering area, carrying the publisher's partial-coverage note verbatim | `lg_code` |

- **A station point is N02's own geometry, not a boundary-derived coordinate.**
  §3.1's "not re-derived into coordinates" rule guards the *boundary* sources: a
  centroid of an e-Stat polygon would state a municipality's location as though
  it had been measured. A station's representative point is computed from that
  station's N02 polyline and from nothing else, by the same rule in
  `build/spatial.py` that the station→municipality join already uses — so a pin
  on the map and `bridge_station_municipality` cannot disagree.
- **Representative point, never a 駅舎 or 出入口.** The same restriction the MLIT
  coordinates carry in `DATA_LICENSE.md`. The panel says so where it shows one.
- **市区町村 stays the ceiling.** §3.1's granularity rule is unchanged and §4
  still holds: nothing here descends to 町字, and a station whose point falls on
  a boundary keeps every candidate municipality rather than picking one.
- **Not a release artifact either.** `site/data/jpac_data.js` is produced by a
  separate tool, is absent from `SOURCES.yml` / `NOTICE.md`, and `jpac build` is
  unchanged. It is a projection of tables the release already ships, keyed by
  `lg_code`.
- **Attribution travels with it.** The N02, 日本郵便 and 総務省 attributions are
  in the page in `DATA_LICENSE.md`'s wording, shown with the rest of the 出典.

## 4. Ambiguity policy (hard)

> Leaving data unresolved is acceptable. Inventing a wrong 1:1 mapping is a defect.

NEVER:

- take the first candidate found
- depend on row order (SQL or Python) to pick a match
- confirm a match on string distance alone
- collapse several plausible candidates to the single highest score
- silently drop unmatched rows
- silently promote ambiguous → exact
- treat NULL / special records as ordinary addresses
- expand a municipality-level statement down to 町字 level
- let a language model decide an address mapping

Ambiguous stays ambiguous, with every candidate retained and `candidate_count > 1`,
`is_unique_match = false`.

Fuzzy similarity may **generate candidates**. It may never **confirm** one.

## 5. No data loss

Raw source text is always preserved (`*_raw` columns) alongside normalized values.
Records that fail to match are kept with `relation_type='unresolved'`, never deleted.
Match rate is never improved by removing rows.

## 6. Confidence is not probability

`confidence` ∈ [0,1] is a **deterministic rule-based trust score** defined by
`config/matching_rules.yml`. It is not a statistical probability and must never be
described as one. Rules live in config, are versioned (`matching_rule_version`), and
every bridge row records which rule produced it.

## 7. Time semantics

`valid_from` / `valid_to` (real-world validity, only from the source) are strictly
separate from `observed_from` / `observed_to` (when this project saw it) and from
`source_published_at` / `downloaded_at`.

If the source does not state a real effective date, `valid_from` is **NULL**. Never
substitute a download date for a validity date.

## 8. Types

Every code is a **string**: `postal_code`, `old_postal_code`, `lg_code`,
`jis_city_code`, `machiaza_id`, `mlit_code`, `area_code`, `numbering_area_code`,
`local_code`. Leading zeros must survive every read, join, and write. Read them as
Utf8 at parse time — never cast after inference.

## 9. Licensing

Code is MIT. **Data is not.** Each source keeps its own terms, recorded per
`source_snapshot` and reproduced in `NOTICE.md` / `SOURCES.yml` / `DATA_LICENSE.md`.
Changing a license conclusion requires a documented review — see
[`LICENSE_POLICY.md`](LICENSE_POLICY.md).

License drift or schema drift **stops the release**. Fail closed. See §11.

## 10. Testing requirements

Unit, integration, fixture, data-quality, regression, schema-drift and license-drift
tests are all required. Fixtures are extracted from official sources with recorded
provenance and must cover the known hard cases (23 wards, 政令指定都市, 郡部, 北海道,
Kyoto street names, 丁目 present/absent, postal 1:N and N:1, duplicate town names,
area-code exception regions, post-merger municipalities). See
[`TEST_STRATEGY.md`](TEST_STRATEGY.md).

## 11. Fail closed

No release when any of these is true: source fetch failure, license drift, schema
drift, row-count anomaly, duplicate anomaly, unmatched/ambiguous rate spike, missing
required source, SHA-256 mismatch, validation failure, suspected data loss, or an
unresolved release-blocking review finding.

Stopping the update always beats shipping a questionable one. The thresholds
themselves are in [`QUALITY_POLICY.md`](QUALITY_POLICY.md).

## 12. Independent review

Changes to matching, identity, licensing or gating are reviewed independently of the
person who wrote them. Findings are graded P0/P1/P2/P3; P0 and P1 are resolved before
a release, and severity is never quietly downgraded. A reviewer reports; the
maintainer verifies and applies the fix.

## 13. Build philosophy

Acquisition — discovering what each publisher currently offers, downloading it,
re-hashing the terms, and promoting a payload into `data/raw/` — is managed internally
and is **not part of this repository**. What lives here begins at an accepted payload
and ends at a validated set of artifacts.

`jpac build` must go from `data/raw/` to a validated release candidate without human
file-wrangling, and must never touch the network. Full rebuild from a clean state plus
the accepted payloads must always work; incremental paths are never the only path.
Anything that reaches into the network belongs on the internal side.
