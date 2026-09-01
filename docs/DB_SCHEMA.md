# DB_SCHEMA.md

Physical schema. Parquet is the normative form; the SQLite DDL below mirrors it.
Conceptual rationale is in `docs/DATA_MODEL.md`.

Revision 2 (2026-08-23) incorporates the independent design review. The
structural changes are noted inline as
`[R1-Pn]`.

> **This document states intent; [`schema.sql`](schema.sql) states fact.** The DDL here
> is written to explain *why* each table looks the way it does, and in places it
> describes a target the current build has not reached — §5.1 most notably. When the two
> disagree, `schema.sql` wins: it is extracted verbatim from the shipped artifact.

## 0. Where to look

| Question | File |
|---|---|
| What are the actual `CREATE TABLE` statements? | [`schema.sql`](schema.sql) |
| Why is it shaped that way? | this document |
| What do the tables mean? | [`DATA_MODEL.md`](DATA_MODEL.md) |

## 1. Type rules

- Every code column is `TEXT` / Arrow `Utf8`. No exceptions
  (`POLICY.md` §8): `postal_code`, `old_postal_code`, `lg_code`, `jis_city_code`,
  `machiaza_id`, `mlit_code`, `area_code`, `numbering_area_code`, `local_code`,
  `pref_code`, `chome_number`.
- Dates are `TEXT` `YYYY-MM-DD`; timestamps are `TEXT` RFC 3339 UTC.
- Booleans are `INTEGER` 0/1 in SQLite, `Boolean` in Parquet.
- `latitude` / `longitude` are `Float64`.
- SQLite is created with `PRAGMA foreign_keys=ON`; Parquet carries the same
  constraints as validation tests.

## 2. Provenance

```sql
CREATE TABLE source_snapshot (
  source_snapshot_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL, dataset_name TEXT NOT NULL,
  source_page_url TEXT NOT NULL, download_url TEXT NOT NULL,
  license_name TEXT, license_url TEXT, license_text_sha256 TEXT,
  source_version TEXT, published_at TEXT, downloaded_at TEXT NOT NULL,
  etag TEXT, last_modified TEXT,
  sha256 TEXT NOT NULL, file_size INTEGER NOT NULL, row_count INTEGER,
  schema_fingerprint TEXT NOT NULL, parser_version TEXT NOT NULL,
  status TEXT NOT NULL          -- ok | stale_carried_forward | schema_changed
                                -- | license_changed | fetch_failed
);

-- [R1-P0-8] One licence triple could not express the ABR DCAT-vs-terms
-- disagreement, the two sets of terms on the postal conversion table, or MLIT's
-- base terms plus per-download stipulation. Every applicable document is now a
-- row, so an artifact can prove which terms were checked.
CREATE TABLE snapshot_license_artifact (
  artifact_id TEXT PRIMARY KEY,
  source_snapshot_id TEXT NOT NULL REFERENCES source_snapshot,
  role TEXT NOT NULL,               -- primary_terms | policy_page
                                    -- | advertised_license | download_stipulation
  license_name TEXT, license_url TEXT,
  text_sha256 TEXT,                 -- normalized-text hash actually observed
  baseline_sha256 TEXT,             -- reviewed value from config/sources.yml
  reviewed_on TEXT,
  review_decision TEXT NOT NULL,    -- baseline_match | baseline_missing
                                    -- | changed | not_gated | not_observed
  note TEXT
);
```

`review_decision='baseline_missing'` blocks the release. There is no
trust-on-first-use path (`docs/LICENSE_POLICY.md` §4).

`not_observed` is different and does not block: a reviewed baseline exists, but
this build had nothing to compare it against. That is the normal state of an
offline rebuild, which never reads a terms page. The acquisition side can supply
what it observed in `data/raw/<source>/_payload.yml`, and where it does, the
comparison runs and a difference raises `LICENSE_REVIEW_REQUIRED`.

The table was previously left empty by an offline rebuild, which meant a shipped
release could not evidence which terms had been reviewed at all. The committed
baselines are now always emitted, because they are committed and true regardless
of what a given build can observe.

```sql
-- [R1-P1-1] A bridge row depends on several snapshots at once (ABR address +
-- ABR conversion table + Japan Post, say). A single source_snapshot_id could
-- not describe that, so bridges cite a match_run and the run cites its inputs.
CREATE TABLE match_run (
  match_run_id TEXT PRIMARY KEY,        -- hash of the ordered input snapshot set
  started_at TEXT NOT NULL,
  matching_rule_version TEXT NOT NULL,
  normalization_profile_version TEXT NOT NULL,
  code_version TEXT NOT NULL
);

CREATE TABLE match_run_input (
  match_run_id TEXT NOT NULL REFERENCES match_run,
  source_snapshot_id TEXT NOT NULL REFERENCES source_snapshot,
  role TEXT NOT NULL,               -- canonical | mapping | corroboration | target
  PRIMARY KEY (match_run_id, source_snapshot_id, role)
);
```

## 3. Canonical layer

```sql
CREATE TABLE address_entity (
  address_id TEXT PRIMARY KEY,
  genesis_lg_code TEXT NOT NULL, genesis_machiaza_id TEXT NOT NULL,
  entity_status TEXT NOT NULL,            -- active | retired
  identity_match_rule TEXT NOT NULL,      -- I1 | I2 | I3 | I4 | I5 | I6
  first_observed_snapshot_id TEXT NOT NULL REFERENCES source_snapshot,
  last_observed_snapshot_id  TEXT NOT NULL REFERENCES source_snapshot,
  created_at TEXT NOT NULL, retired_at TEXT, retire_reason TEXT,
  CHECK (address_id GLOB 'jpa1*')
);

CREATE TABLE address (
  address_id TEXT PRIMARY KEY REFERENCES address_entity,
  lg_code TEXT NOT NULL REFERENCES municipality,
  jis_city_code TEXT NOT NULL, machiaza_id TEXT NOT NULL,
  machiaza_type TEXT,
  pref TEXT NOT NULL, county TEXT, city TEXT NOT NULL, ward TEXT,
  oaza_cho TEXT, chome TEXT, chome_number TEXT, koaza TEXT, machiaza_dist TEXT,
  oaza_cho_kana TEXT, chome_kana TEXT, koaza_kana TEXT,
  oaza_cho_roma TEXT, koaza_roma TEXT,
  full_name_raw TEXT NOT NULL, full_name_normalized TEXT NOT NULL,
  normalization_profile TEXT NOT NULL,
  rsdt_addr_flg TEXT, rsdt_addr_mtd_code TEXT, oaza_cho_aka_flg TEXT,
  koaza_aka_code TEXT, status_flg TEXT, wake_num_flg TEXT,
  src_code TEXT, remarks TEXT,
  valid_from TEXT, valid_to TEXT,                -- efct_date / ablt_date
  observed_from TEXT NOT NULL, observed_to TEXT,
  source_snapshot_id TEXT NOT NULL REFERENCES source_snapshot,
  UNIQUE (lg_code, machiaza_id)
);
```

### 3.1 Versioned entities [R1-P1-3]

`municipality`, `postal_record`, `mlit_town` and `telephone_area` all have
stable natural keys whose *attributes* change. Keying on the natural key alone
would force an overwrite and destroy the previous value, contradicting the
history-aware claim. Each is therefore split into a durable entity and
append-only version rows, with a current view on top.

```sql
CREATE TABLE municipality (              -- durable entity
  lg_code TEXT PRIMARY KEY,
  jis_city_code TEXT NOT NULL,
  first_observed_snapshot_id TEXT NOT NULL REFERENCES source_snapshot
);

CREATE TABLE municipality_version (      -- append-only
  municipality_version_id TEXT PRIMARY KEY,
  lg_code TEXT NOT NULL REFERENCES municipality,
  jis_city_code TEXT NOT NULL,
  pref TEXT NOT NULL, county TEXT, city TEXT NOT NULL, ward TEXT,
  pref_kana TEXT, county_kana TEXT, city_kana TEXT, ward_kana TEXT,
  pref_roma TEXT, county_roma TEXT, city_roma TEXT, ward_roma TEXT,
  valid_from TEXT, valid_to TEXT,
  observed_from TEXT NOT NULL, observed_to TEXT,
  is_current INTEGER NOT NULL,
  source_snapshot_id TEXT NOT NULL REFERENCES source_snapshot,
  UNIQUE (lg_code, observed_from)
);
CREATE VIEW municipality_current AS
  SELECT * FROM municipality_version WHERE is_current = 1;
```

`postal_record_version`, `mlit_town_version` and `telephone_area_version`
follow the identical pattern (`*_current` views alongside).

```sql
CREATE TABLE address_code (
  address_id TEXT NOT NULL REFERENCES address_entity,
  code_type TEXT NOT NULL, code_value TEXT NOT NULL,
  valid_from TEXT, valid_to TEXT,
  observed_from TEXT NOT NULL, observed_to TEXT,
  source_snapshot_id TEXT NOT NULL REFERENCES source_snapshot,
  PRIMARY KEY (address_id, code_type, code_value, observed_from)
);

CREATE TABLE address_lineage (
  lineage_id TEXT PRIMARY KEY,
  old_address_id TEXT REFERENCES address_entity,
  new_address_id TEXT REFERENCES address_entity,
  relation_type TEXT NOT NULL,      -- split|merge|renamed|code_corrected
                                    -- |municipality_recoded|retired|reinstated
  effective_date TEXT,              -- source-stated only
  observed_at TEXT NOT NULL,
  evidence TEXT, evidence_source TEXT,
  source_snapshot_id TEXT NOT NULL REFERENCES source_snapshot,
  CHECK (old_address_id IS NOT NULL OR new_address_id IS NOT NULL)
);

CREATE TABLE address_history (
  history_id TEXT PRIMARY KEY,
  address_id TEXT NOT NULL REFERENCES address_entity,
  field_name TEXT NOT NULL, old_value TEXT, new_value TEXT,
  valid_from TEXT, observed_at TEXT NOT NULL,
  source_snapshot_id TEXT NOT NULL REFERENCES source_snapshot
);
```

## 4. Foreign layer

### 4.1 Postal: code entity vs record [R1-P0-2]

The ABR conversion table maps a 町字 to a **7-digit postal code**. A ken_all
*record* is a different thing: one code can appear in many records. Targeting a
record from a code-level assertion would fabricate a record-level claim the
publisher never made, so the two are separate entities.

```sql
CREATE TABLE postal_code_entity (
  postal_code TEXT PRIMARY KEY,          -- the 7-digit code itself
  record_count INTEGER NOT NULL,         -- how many ken_all records carry it
  observed_from TEXT NOT NULL, observed_to TEXT,
  source_snapshot_id TEXT NOT NULL REFERENCES source_snapshot,
  CHECK (length(postal_code) = 7)
);

CREATE TABLE postal_record (             -- durable entity, one per ken_all row
  postal_record_id TEXT PRIMARY KEY,
  postal_code TEXT NOT NULL REFERENCES postal_code_entity,
  jis_city_code TEXT NOT NULL,
  first_observed_snapshot_id TEXT NOT NULL REFERENCES source_snapshot
);

CREATE TABLE postal_record_version (
  postal_record_version_id TEXT PRIMARY KEY,
  postal_record_id TEXT NOT NULL REFERENCES postal_record,
  postal_code TEXT NOT NULL, jis_city_code TEXT NOT NULL,
  old_postal_code_raw TEXT, old_postal_code TEXT,
  pref_kana TEXT, city_kana TEXT, town_kana TEXT,
  pref TEXT NOT NULL, city TEXT NOT NULL, town TEXT,
  town_raw TEXT, town_normalized TEXT,
  parenthetical_raw TEXT,
  -- [R1-P1-9] Blindly stripping "(...)" let a partial-coverage record look like
  -- a whole town. The class decides whether P4 (exact) is even admissible.
  parenthetical_class TEXT NOT NULL,   -- none | non_geographic | geographic | unknown
  flag_multi_code INTEGER, flag_koaza_banchi INTEGER,
  flag_has_chome INTEGER, flag_multi_town INTEGER,
  update_flag TEXT, change_reason TEXT,
  record_kind TEXT NOT NULL,           -- town|no_listing|city_banchi|ichien
  valid_from TEXT, valid_to TEXT,      -- always NULL: source states none
  observed_from TEXT NOT NULL, observed_to TEXT,
  is_current INTEGER NOT NULL,
  source_snapshot_id TEXT NOT NULL REFERENCES source_snapshot,
  CHECK (valid_from IS NULL AND valid_to IS NULL)
);
```

### 4.2 MLIT and telephone

```sql
CREATE TABLE mlit_town (
  mlit_record_id TEXT PRIMARY KEY,
  mlit_code TEXT NOT NULL, jis_city_code TEXT NOT NULL,
  first_observed_snapshot_id TEXT NOT NULL REFERENCES source_snapshot
);

CREATE TABLE mlit_town_version (
  mlit_town_version_id TEXT PRIMARY KEY,
  mlit_record_id TEXT NOT NULL REFERENCES mlit_town,
  mlit_code TEXT NOT NULL, jis_city_code TEXT NOT NULL,
  pref_code TEXT NOT NULL, pref_name TEXT NOT NULL, city_name TEXT NOT NULL,
  town_name_raw TEXT NOT NULL, town_name_normalized TEXT NOT NULL,
  latitude REAL, longitude REAL,
  source_material_code TEXT, aza_class_code TEXT,
  fiscal_year TEXT, isj_version TEXT,
  observed_from TEXT NOT NULL, observed_to TEXT, is_current INTEGER NOT NULL,
  source_snapshot_id TEXT NOT NULL REFERENCES source_snapshot,
  CHECK (latitude IS NULL OR (latitude BETWEEN 20 AND 46)),
  CHECK (longitude IS NULL OR (longitude BETWEEN 122 AND 154))
);

CREATE TABLE telephone_area (
  numbering_area_code TEXT PRIMARY KEY,
  first_observed_snapshot_id TEXT NOT NULL REFERENCES source_snapshot,
  CHECK (numbering_area_code GLOB '[0-9][0-9][0-9]*')
);

CREATE TABLE telephone_area_version (
  telephone_area_version_id TEXT PRIMARY KEY,
  numbering_area_code TEXT NOT NULL REFERENCES telephone_area,
  area_code TEXT NOT NULL, area_text_raw TEXT NOT NULL,
  local_digit_pattern TEXT, current_as_of TEXT,
  observed_from TEXT NOT NULL, observed_to TEXT, is_current INTEGER NOT NULL,
  source_snapshot_id TEXT NOT NULL REFERENCES source_snapshot
);

CREATE TABLE telephone_area_coverage (
  coverage_id TEXT PRIMARY KEY,
  numbering_area_code TEXT NOT NULL REFERENCES telephone_area,
  clause_raw TEXT NOT NULL,
  pref_name TEXT, county_name TEXT, municipality_name TEXT,
  sub_municipal_text TEXT,
  qualifier TEXT NOT NULL,          -- none|exclude|limit
  coverage_type TEXT NOT NULL,      -- full|partial|municipality_only|unresolved
  exception_text TEXT, parse_rule TEXT NOT NULL,
  source_snapshot_id TEXT NOT NULL REFERENCES source_snapshot
);

CREATE TABLE telephone_number_block (
  block_id TEXT PRIMARY KEY,
  numbering_area_code TEXT NOT NULL,
  number TEXT NOT NULL, area_code TEXT NOT NULL, local_code TEXT NOT NULL,
  carrier TEXT, usage_status TEXT, remarks TEXT, current_as_of TEXT,
  source_snapshot_id TEXT NOT NULL REFERENCES source_snapshot
);
```

## 5. Bridges

### 5.1 Concrete endpoints, both nullable [R1-P0-1, R1-P1-2]

> **What V1 actually ships.** The typed columns below (`postal_record_id`,
> `mlit_record_id`, …) and their `REFERENCES` / `NOT NULL` clauses are the intended
> design; they are **not** what the current artifacts contain. All six shipped bridges
> carry the same 30 columns — `address_id`, `lg_code` and a polymorphic `target_id` —
> and the SQLite export declares **no foreign keys at all**. The guarantee this section
> argues for is enforced by a constraint rather than by typed columns:
> `CHECK (address_id IS NOT NULL OR lg_code IS NOT NULL OR target_id IS NOT NULL)`,
> so an unmatched record from either side is still retained. Referential integrity is
> checked by the invariant tests (`docs/TEST_STRATEGY.md` §3), not by the database.
> The definitions as shipped are in [`schema.sql`](schema.sql). The reasoning below
> stands and the migration remains open.

The first design had one polymorphic `target_entity_id` and an address-only
source side. Two things broke:

* An unmatched **postal or MLIT record** had nowhere to live. `unresolved`
  nulls the target, and the source side accepted only addresses, so the
  foreign record could only be retained by inventing an endpoint or dropping
  the row — the exact data loss `POLICY.md` §5 forbids.
* Polymorphic ids carry no foreign key, so a bridge could point at a
  nonexistent or wrong-type record while every declared constraint passed.

Both are fixed by giving each bridge **two concrete, individually nullable FK
columns** and requiring exactly the right one to be null for each direction.
Unresolved is now expressible from either side without losing the record.

```sql
CREATE TABLE bridge_address_postal (
  bridge_id TEXT PRIMARY KEY,
  address_id       TEXT REFERENCES address_entity,   -- NULL => unmatched postal record
  postal_record_id TEXT REFERENCES postal_record,    -- NULL => unmatched address
  direction TEXT NOT NULL,          -- address_to_postal | postal_to_address
  relation_type TEXT NOT NULL, match_method TEXT NOT NULL,
  matching_rule_id TEXT NOT NULL, confidence REAL NOT NULL,
  candidate_group_id TEXT, candidate_count INTEGER NOT NULL,
  candidate_count_is_complete INTEGER NOT NULL DEFAULT 1,   -- [R1-P1-7]
  is_unique_match INTEGER NOT NULL,
  verification_status TEXT NOT NULL,
  override_stale INTEGER NOT NULL DEFAULT 0,                -- [R1-P0-7]
  derivation TEXT,                                          -- [R1-P0-3]
  normalization_profile TEXT, mismatch_note TEXT,
  valid_from TEXT, valid_to TEXT,
  observed_from TEXT NOT NULL, observed_to TEXT,
  is_current INTEGER NOT NULL,
  match_run_id TEXT NOT NULL REFERENCES match_run,          -- [R1-P1-1]
  source_snapshot_id TEXT NOT NULL REFERENCES source_snapshot,
  matching_rule_version TEXT NOT NULL,
  normalization_profile_version TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,

  CHECK (confidence >= 0.0 AND confidence <= 1.0),
  CHECK (candidate_count >= 0),
  -- At least one endpoint always exists, so nothing is ever dropped.
  CHECK (address_id IS NOT NULL OR postal_record_id IS NOT NULL),
  -- unresolved means exactly one endpoint is known.
  CHECK ((relation_type = 'unresolved')
         = ((address_id IS NULL) <> (postal_record_id IS NULL))),
  CHECK (NOT (is_unique_match = 1 AND candidate_count > 1)),
  CHECK (candidate_count > 1 OR candidate_group_id IS NULL
         OR candidate_count = 1),
  CHECK (candidate_count <= 1 OR candidate_group_id IS NOT NULL),
  -- The full documented auto-accept conjunction, enforced by the database so a
  -- code regression cannot export an over-accepted row [R1-P0-7].
  CHECK (verification_status <> 'auto' OR (
           candidate_count = 1
       AND is_unique_match = 1
       AND candidate_count_is_complete = 1
       AND confidence >= 0.98
       AND override_stale = 0
       AND relation_type IN ('exact','equivalent')
  )),
  CHECK (relation_type IN ('exact','equivalent','parent','child','contains',
                           'overlap','candidate','ambiguous','unresolved')),
  CHECK (match_method IN ('direct_code','exact_name','normalized_name','parent_child',
                          'composite','official_area_rule','manual_override','unresolved')),
  CHECK (verification_status IN ('auto','review_required',
                                 'manually_verified','manually_rejected'))
);
```

The same column set and constraints apply to:

| Table | Canonical endpoint | Foreign endpoint |
|---|---|---|
| `bridge_address_postal_code` [R1-P0-2] | `address_id` | `postal_code` → `postal_code_entity` |
| `bridge_address_postal` | `address_id` | `postal_record_id` |
| `bridge_address_mlit` | `address_id` | `mlit_record_id` |
| `bridge_address_telephone` | `address_id` | always NULL in V1; municipality evidence is not expanded to 町字 |
| `bridge_municipality_postal` | `lg_code` | `postal_record_id` |
| `bridge_municipality_telephone` | `lg_code` | `numbering_area_code` (+ `coverage_type`) |

`bridge_address_postal_code` carries the official ABR conversion-table edges
(rules P1/P1x); `bridge_address_postal` carries record-level name matching
(P4–P7). Keeping them apart is what stops an official code-level statement from
being reported as a record-level one.

## 6. Views

### 6.1 Two views, deliberately [R1-P0-6]

A single flat view would have published `manually_rejected` and superseded edges
next to accepted ones with no field to tell them apart. So there are two, and
the plain name is the safe one:

```sql
-- Accepted, current evidence only. This is what casual consumers should use.
-- It repeats the whole SELECT of address_crosswalk_all with each bridge
-- filtered in a subquery, rather than wrapping the unfiltered view in a WHERE.
CREATE VIEW address_crosswalk AS
SELECT ... FROM address a
LEFT JOIN (SELECT * FROM bridge_address_postal_code
            WHERE is_current = 1
              AND verification_status <> 'manually_rejected') bp
       ON bp.address_id = a.address_id
-- ... and likewise for bridge_address_mlit and bridge_address_telephone
```

The subquery placement is the whole point, and an earlier revision got it
wrong. Filtering after the `LEFT JOIN` removes the entire address row when its
only bridge is rejected, so an address would disappear from the SQLite artifact
while remaining in the Parquet one. Filtering before the join blanks the
columns and keeps the address, which is what `POLICY.md` §4 requires and what
`export.writers.build_flat_view` does. Both views are generated from one
template string in `export/writers.py` so they cannot drift apart, and
`tools/verify_distribution.py` compares the SQL view against the Parquet flat
file row for row — two independent implementations of one definition, checked
against each other rather than trusted.

```sql
-- Every edge, every status, including rejected and superseded.
CREATE VIEW address_crosswalk_all AS
SELECT
  a.address_id, a.lg_code, a.jis_city_code,
  a.pref AS pref_name, a.city AS city_name, a.ward AS ward_name,
  a.full_name_raw AS town_name, a.full_name_normalized AS town_name_normalized,
  a.machiaza_id,

  bm.mlit_code, mv.latitude AS mlit_latitude, mv.longitude AS mlit_longitude,
  bp.postal_code,
  -- semicolon-joined when a postal code has more than one former code
  op.old_postal_code,
  bt.numbering_area_code, tv.area_code, tv.area_text_raw AS numbering_area_name,

  bp.relation_type AS postal_relation_type, bp.match_method AS postal_match_method,
  bp.matching_rule_id AS postal_rule, bp.confidence AS postal_confidence,
  bp.candidate_count AS postal_candidate_count,
  bp.candidate_group_id AS postal_candidate_group,
  bp.is_unique_match AS postal_is_unique,
  bp.verification_status AS postal_status,

  bm.relation_type AS mlit_relation_type, bm.match_method AS mlit_match_method,
  bm.matching_rule_id AS mlit_rule, bm.confidence AS mlit_confidence,
  bm.candidate_count AS mlit_candidate_count,
  bm.candidate_group_id AS mlit_candidate_group,
  bm.is_unique_match AS mlit_is_unique,
  bm.verification_status AS mlit_status,

  bt.relation_type AS telephone_relation_type,
  bt.match_method AS telephone_match_method,
  bt.matching_rule_id AS telephone_rule, bt.confidence AS telephone_confidence,
  bt.candidate_count AS telephone_candidate_count,
  bt.candidate_group_id AS telephone_candidate_group,
  bt.is_unique_match AS telephone_is_unique,
  bt.verification_status AS telephone_status,
  bt.coverage_type AS telephone_coverage_type,
  bt.derivation AS telephone_derivation,

  -- No is_current column: it would appear in the SQLite artifact and not in
  -- the Parquet one, and the two must expose the same fields. The filtered
  -- view above applies the condition instead of publishing it.
FROM address a
LEFT JOIN bridge_address_postal_code bp ON bp.address_id = a.address_id
LEFT JOIN (SELECT postal_code, group_concat(old_postal_code, ';') AS old_postal_code
             FROM (SELECT DISTINCT postal_code, old_postal_code
                     FROM postal_record_version
                    WHERE is_current = 1 AND old_postal_code IS NOT NULL
                    ORDER BY postal_code, old_postal_code)
            GROUP BY postal_code) op ON op.postal_code = bp.postal_code
LEFT JOIN bridge_address_mlit bm ON bm.address_id = a.address_id
LEFT JOIN mlit_town_version mv
       ON mv.mlit_record_id = bm.mlit_record_id AND mv.is_current = 1
LEFT JOIN bridge_address_telephone bt ON bt.address_id = a.address_id
LEFT JOIN telephone_area_version tv
       ON tv.numbering_area_code = bt.numbering_area_code AND tv.is_current = 1;
```

An address with 3 postal codes and 2 numbering areas produces 6 rows. That is the
correct answer, and every row shows its own `relation_type`, `confidence`,
`candidate_count` and `verification_status`, so the fan-out is interpretable
rather than misleading (`POLICY.md` §4).

### 6.2 Unmatched view

```sql
CREATE VIEW unmatched_records AS
  SELECT 'postal_record' AS kind, postal_record_id AS record_id, matching_rule_id
    FROM bridge_address_postal
   WHERE relation_type = 'unresolved' AND address_id IS NULL
  UNION ALL
  SELECT 'mlit_town', mlit_record_id, matching_rule_id
    FROM bridge_address_mlit
   WHERE relation_type = 'unresolved' AND address_id IS NULL
  UNION ALL
  SELECT 'address', address_id, matching_rule_id
    FROM bridge_address_mlit
   WHERE relation_type = 'unresolved' AND mlit_record_id IS NULL;
```

Unmatched records are queryable, which is the point of keeping them.

## 7. Indexes

```sql
CREATE INDEX idx_addr_lg           ON address(lg_code);
CREATE INDEX idx_addr_jis_norm     ON address(jis_city_code, full_name_normalized);
CREATE INDEX idx_pcv_code          ON postal_record_version(postal_code);
CREATE INDEX idx_pcv_old           ON postal_record_version(old_postal_code);
CREATE INDEX idx_pcv_jis_norm      ON postal_record_version(jis_city_code, town_normalized);
CREATE INDEX idx_mlv_code          ON mlit_town_version(mlit_code);
CREATE INDEX idx_mlv_jis_norm      ON mlit_town_version(jis_city_code, town_name_normalized);
CREATE INDEX idx_tav_area_code     ON telephone_area_version(area_code);
CREATE INDEX idx_tnb_area          ON telephone_number_block(area_code, local_code);
CREATE INDEX idx_bapc_addr         ON bridge_address_postal_code(address_id);
CREATE INDEX idx_bapc_code         ON bridge_address_postal_code(postal_code);
CREATE INDEX idx_bap_addr          ON bridge_address_postal(address_id);
CREATE INDEX idx_bap_rec           ON bridge_address_postal(postal_record_id);
CREATE INDEX idx_bap_rel           ON bridge_address_postal(relation_type);
CREATE INDEX idx_bam_addr          ON bridge_address_mlit(address_id);
CREATE INDEX idx_bam_rec           ON bridge_address_mlit(mlit_record_id);
CREATE INDEX idx_bat_addr          ON bridge_address_telephone(address_id);
CREATE INDEX idx_bat_area          ON bridge_address_telephone(numbering_area_code);
CREATE INDEX idx_bmt_area          ON bridge_municipality_telephone(numbering_area_code);
CREATE INDEX idx_bmp_lg            ON bridge_municipality_postal(lg_code);
CREATE INDEX idx_lineage_old       ON address_lineage(old_address_id);
CREATE INDEX idx_lineage_new       ON address_lineage(new_address_id);
```

These cover the searches in spec §47 in both directions. None reduces a
many-to-many result to one row.
