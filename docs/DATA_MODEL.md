# DATA_MODEL.md

Conceptual model. Physical DDL is in `docs/DB_SCHEMA.md`.

## 1. Shape

```
                        source_snapshot
                              │ (every row in every table cites one)
                              ▼
  municipality ◄──── address_entity ────► address_lineage
       │                   │
       │                   ├── address            (current attributes)
       │                   ├── address_code       (code history)
       │                   └── address_history    (attribute history)
       │                   │
       │     ┌─────────────┼──────────────┐
       │     ▼             ▼              ▼
       │  bridge_       bridge_        bridge_
       │  address_      address_       address_
       │  postal        mlit           telephone
       │     │             │              │
       │     ▼             ▼              ▼
       │  postal_code   mlit_town      telephone_area
       │     ▲                            ▲    │
       │     │                            │    ▼
       └─────┴── bridge_municipality_ ────┘  telephone_
                 postal / telephone          number_block
```

Three layers, kept strictly apart:

1. **Canonical** — `address_entity`, `address`, `municipality`. What an address *is*.
2. **Foreign** — `postal_code`, `mlit_town`, `telephone_area`, `telephone_number_block`.
   Each other system's own records, loaded losslessly, never edited to fit.
3. **Bridge** — the relationships, with evidence and uncertainty attached.

A foreign table is never "corrected" to make a join work. Disagreement between
publishers is data, and it lives in the bridge.

## 2. Canonical layer

### `address_entity`

The durable identity. One row per real-world 町字 this project has ever observed.

`address_id` (PK), `genesis_lg_code`, `genesis_machiaza_id`, `entity_status`,
`identity_match_rule`, `first_observed_snapshot_id`, `last_observed_snapshot_id`,
`created_at`, `retired_at`, `retire_reason`.

Governed entirely by `docs/IDENTITY_MODEL.md`. Rows are never deleted.

### `address`

Current attributes of an active entity, from ABR `mt_town`.

Keys: `address_id` (PK, FK), `lg_code`, `machiaza_id`, `jis_city_code`.
Names: `pref`, `county`, `city`, `ward`, `oaza_cho`, `chome`, `koaza`, `machiaza_dist`,
each with `_kana` and `_roma` where ABR supplies them.
Derived: `full_name_raw` (composed from raw parts), `full_name_normalized`
(conservative profile), `normalization_profile`.
ABR attributes: `machiaza_type`, `chome_number`, `rsdt_addr_flg`,
`rsdt_addr_mtd_code`, `oaza_cho_aka_flg`, `koaza_aka_code`, `status_flg`,
`wake_num_flg`, `src_code`, `remarks`.
Time: `valid_from` ← `efct_date`, `valid_to` ← `ablt_date`,
`observed_from`, `observed_to`, `source_snapshot_id`.

`post_code` from `mt_town` is **not** carried here; it was empty in every observed row
and the publisher supersedes it with the conversion table
(ABR の変換表と ken_all の粒度差による).

### `municipality`

From ABR `mt_city`. `lg_code` (PK, 6 digits), `jis_city_code` (5 digits),
`pref`, `county`, `city`, `ward` + kana/roma, `valid_from`, `valid_to`,
`observed_from`, `observed_to`, `source_snapshot_id`.

`jis_city_code` is `lg_code[0:5]`, materialized because it is the join key to Japan Post
and MLIT, which both use the 5-digit form.

### `address_code` — code history

Lets a consumer ask "what codes has this entity ever carried?" without re-reading old
releases.

`address_id`, `code_type` (`abr_machiaza_id` | `abr_lg_code` | `jis_city_code` |
`mlit_code` | `postal_code` | `old_postal_code`), `code_value`,
`valid_from`, `valid_to`, `observed_from`, `observed_to`, `source_snapshot_id`.

Append-only. A superseded code gets `observed_to` set; the row stays.

### `address_history`, `address_lineage`

`address_history` records attribute changes (old value, new value, snapshot observed).
`address_lineage` records entity-level events — split, merge, rename, code correction,
retirement — per `docs/IDENTITY_MODEL.md` §6.

## 3. Foreign layer

### `postal_code_entity` and `postal_record`

Two different things, deliberately separated. `postal_code_entity` is the **7-digit code
itself**; `postal_record` is one **`ken_all` row**. The ABR conversion table maps a 町字
to a code, and one code appears in many records, so an edge from that table must land on
the code — attaching it to a single record would assert something the publisher never
said (`docs/DB_SCHEMA.md` §4.1).

`postal_record` is loaded losslessly, one row per `ken_all` record.

`postal_record_id` (surrogate), `jis_city_code`, `old_postal_code_raw` (`"060  "`),
`old_postal_code` (right-stripped: `"060"`), `postal_code`,
`pref_kana`/`city_kana`/`town_kana`, `pref`/`city`/`town`,
`town_raw`, `town_normalized`,
`flag_multi_code`, `flag_koaza_banchi`, `flag_has_chome`, `flag_multi_town`,
`update_flag`, `change_reason`,
`record_kind ∈ {town, no_listing, city_banchi, ichien}`,
`observed_from`, `observed_to`, `source_snapshot_id`.

`valid_from` / `valid_to` are **NULL**: Japan Post states no effective date in this file.
Substituting the download date is forbidden (`POLICY.md` §7).

`record_kind` is assigned by exact suffix match on the 漢字 town field
(`以下に掲載がない場合`, `〜の次に番地がくる場合`, `〜一円`), never by fuzzy matching,
and drives routing to the municipality-level bridge.

`parenthetical_class` (`none` / `non_geographic` / `geographic` / `unknown`) records what
a trailing `（…）` in the town field means. It gates whether an `exact` postal match is
admissible at all: a parenthetical describing banchi ranges or exclusions means the record
covers only part of the town (`docs/MATCHING_RULES.md` §5).

Municipality, postal, MLIT and telephone records are all stored as a durable entity plus
append-only `*_version` rows with `is_current`, so a changed name or coverage text adds a
version instead of overwriting the previous one. A schema that can only overwrite cannot
honestly claim to be history-aware.

Each `*_version_id` is content-addressed over `(table, entity key, content,
observation interval)`, and the interval belongs in the key: without it a superseded row
and its identical-content replacement collide. The same function runs on the **first**
release as on every later one. It once ran only when a previous release existed, so
release 1 published ids in one form and release 2 renamed all 318,119 of them without a
single record having changed — a version id that moves for no reason is worse than
useless to anyone who stored one. Only `address_id` is a promised persistent identifier
(`docs/IDENTITY_MODEL.md`), but a version id must still not move while its version does
not.

### `mlit_town`

`mlit_code` (12 digits, PK with snapshot), `jis_city_code`, `pref_code`, `pref_name`,
`city_name`, `town_name_raw`, `town_name_normalized`, `latitude`, `longitude`,
`source_material_code`, `aza_class_code`, `fiscal_year`, `isj_version`,
`observed_from`, `observed_to`, `source_snapshot_id`.

`valid_from` is NULL; `fiscal_year` is the survey year, not a validity date.
Coordinates are documented at every layer as **representative points for the whole
大字・町丁目**.

### `telephone_area` and `telephone_area_coverage`

`telephone_area`: `numbering_area_code` (normalized `NNN` or `NNN-N`), `area_code`,
`area_text_raw` (the full official 対象地域 string), `local_digit_pattern`
(`CDE`/`DE`/`E`), `current_as_of`, `observed_from`, `observed_to`, `source_snapshot_id`.

`telephone_area_coverage`: one row per parsed clause —
`coverage_id`, `numbering_area_code`, `clause_raw`, `pref_name`, `county_name`,
`municipality_name`, `sub_municipal_text`, `qualifier ∈ {none, exclude, limit}`,
`coverage_type ∈ {full, partial, municipality_only, unresolved}`,
`exception_text`, `parse_rule`.

Splitting the prose into its own table is what makes 「（富野を除く。）」 first-class
data instead of a comment.

`bridge_address_telephone` deliberately contains one unresolved T10 row per canonical
町字. Its `target_id` is always NULL. All official telephone coverage evidence remains in
`bridge_municipality_telephone`; no municipality statement is expanded to 町字.

### `telephone_number_block`

From the MIC XLS: `numbering_area_code`, `number`, `area_code`, `local_code`, `carrier`,
`usage_status`, `remarks`, `current_as_of`, `source_snapshot_id`. Pure facts, joined by
code, no matching involved.

## 4. Bridge layer

All bridges share this core (spec §13). Both endpoints are **concrete, individually
nullable foreign keys** rather than polymorphic ids, so referential integrity is
enforceable and an unmatched record from either side survives:

```
bridge_id                 deterministic hash of the semantic key
<canonical>_id            address_id or lg_code; NULL => unmatched foreign record
<foreign>_id              postal_record_id / postal_code / mlit_record_id /
                          numbering_area_code; NULL => unmatched address
direction                 which side the match was attempted from
relation_type
match_method
matching_rule_id          e.g. 'P5'
confidence
candidate_group_id        NULL iff candidate_count = 1
candidate_count           computed over the COMPLETE candidate set
candidate_count_is_complete
is_unique_match
override_stale
derivation                NULL for telephone-town rows; municipality evidence is not expanded
verification_status
normalization_profile
mismatch_note
valid_from / valid_to
observed_from / observed_to
is_current
match_run_id              resolves to every contributing snapshot and its role
source_snapshot_id
matching_rule_version
normalization_profile_version
created_at / updated_at
```

`bridge_id` is a hash of the semantic key rather than a counter, so a rebuild in a
different execution order produces identical ids (`POLICY.md` §13, spec §46).

Six bridges: `bridge_address_postal_code`, `bridge_address_postal`,
`bridge_address_mlit`, `bridge_address_telephone`, `bridge_municipality_postal`,
`bridge_municipality_telephone`, plus `address_lineage` as the internal one.

A bridge row usually depends on **several** snapshots — a postal edge on the ABR town
master, the ABR conversion table and Japan Post at once. One `source_snapshot_id` cannot
express that, so each row cites a `match_run_id` and `match_run_input` records every
contributing snapshot with its role (`canonical`, `mapping`, `corroboration`, `target`).

**Why the municipality-level bridges exist.** Japan Post's 以下に掲載がない場合 and
MIC's partial-coverage clauses are statements about a *municipality*, not a 町字. Forcing
them into `bridge_address_postal` would require either inventing a town target or a NULL
target that breaks referential integrity. Giving them their own tables keeps the
invariant "a non-`unresolved` bridge row has a real target" true everywhere
(`docs/MATCHING_RULES.md` P2/P3, T1/T3).

## 5. Provenance

`source_snapshot` is mandatory and every row in every table carries
`source_snapshot_id` (spec §25).

`source_snapshot_id`, `provider`, `dataset_name`, `source_page_url`, `download_url`,
`license_name`, `license_url`, `license_text_sha256`, `source_version`,
`published_at`, `downloaded_at`, `etag`, `last_modified`, `sha256`, `file_size`,
`row_count`, `schema_fingerprint`, `parser_version`, `status`.

`license_text_sha256` is what makes license-drift detection real rather than nominal: a
terms page can be rewritten without changing its URL or its license name.

## 6. Time semantics

| Column | Source |
|---|---|
| `valid_from` / `valid_to` | **Only** from an explicit source field |
| `observed_from` / `observed_to` | Snapshot in which this project first/last saw the row |
| `source_published_at` | Publisher's stated publication date |
| `downloaded_at` | Wall-clock fetch time |

Where each is actually available:

| Source | Real validity dates? |
|---|---|
| ABR `mt_town` | **Yes** — `efct_date` / `ablt_date` |
| ABR `abr_post_code` | **Yes** — `add_date` / `dlt_date` |
| Japan Post `ken_all` | No → NULL |
| MLIT ISJ | No → NULL |
| MIC | Only a document-level 現在日 → `current_as_of`, not `valid_from` |

## 7. Flat view

There are **two** flat views, and the plain name is the safe one:

- `address_crosswalk` — current, non-rejected evidence only.
- `address_crosswalk_all` — every edge, including `manually_rejected` and superseded
  rows, with the temporal and status columns needed to tell them apart.

A single view would have published a rejected edge next to an accepted one with no field
distinguishing them, and the fan-out would then have multiplied it across the other
systems.

Both **fan out** on 1:N and N:M — that is correct behaviour, not a defect — and both
carry `relation_type`, `match_method`, `matching_rule_id`, `confidence`,
`candidate_count`, `candidate_group_id`, `is_unique_match` and `verification_status` per
bridged system, so a consumer can never read a row without being able to see how
trustworthy it is.

`docs/DB_SCHEMA.md` §6 gives the definitions.
