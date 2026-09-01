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
