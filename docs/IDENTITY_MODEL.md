# IDENTITY_MODEL.md

How `address_id` is minted, kept stable, and retired.

## 1. Why not `lg_code + machiaza_id`

ABR's natural key `(lg_code, machiaza_id)` is the correct *lookup* key, but it is not a
durable identity:

- ABR issues corrections to `machiaza_id` and to `lg_code`.
- Municipal mergers (廃置分合) change `lg_code` for every town in the absorbed
  municipality while the towns themselves continue to exist.
- Designation as a 政令指定都市 introduces `ward` and re-codes towns.

If `address_id` were the concatenation, all of the above would silently break every
downstream user's stored references. `POLICY.md` §2 therefore requires an independent
persistent id.

## 2. The id itself

```
address_id = "jpa1" + Crockford-base32( blake2s(genesis_key, digest_size=10) )
```

- `genesis_key` = `f"abr:town:{lg_code}:{machiaza_id}"` **as first observed** for that
  entity — not as currently observed.
- The full id is **20 characters**: the 4-character prefix `jpa1` plus a
  16-character base32 payload, e.g. `jpa1k3m8q2z7ry04ph9t`.
- `jpa1` is a scheme prefix. A future incompatible identity scheme becomes `jpa2`;
  existing `jpa1` ids are never re-minted.
- BLAKE2s with a 10-byte digest is used for a short, stable, collision-safe token.
  Collisions are checked explicitly at build time; a collision is a hard build failure,
  not a warning.

Two properties follow. It is **deterministic** — a genesis build with no prior state
reproduces every id exactly (`POLICY.md` §13, spec §46). And it is **opaque** — the
value carries no meaning, so consumers cannot parse a municipality out of it and be
wrong later.

## 3. The identity ledger

Determinism alone is not stability: once `lg_code` changes, the hash of the *current*
key would change too. Continuity is therefore carried by a small committed artifact:

```
identity/address_id_ledger.csv.gz
```

| Column | Meaning |
|---|---|
| `address_id` | the minted id |
| `genesis_lg_code`, `genesis_machiaza_id` | the key the id was minted from |
| `current_lg_code`, `current_machiaza_id` | the key it resolves to now |
| `genesis_normalized_name` | composed normalized name at minting |
| `current_normalized_name` | composed normalized name now |
| `first_observed_snapshot_id`, `last_observed_snapshot_id` | observation window |
| `entity_status` | `active` / `retired` |
| `retired_at`, `retire_reason` | set only when retired |

The ledger is **committed to git**. It is the project's memory and it is
human-reviewable in diffs.

Measured on the national build: **726,170 rows, 26 MB gzipped**. That is not
small, and committing a new copy every month grows the repository by roughly
300 MB a year even though most rows never change. Two consequences worth stating
rather than discovering later:

- The monthly workflow commits the ledger **only when it actually changed**, and
  only after the release has been published.
- If repository size becomes a problem, the answer is to store the ledger as a
  release asset with a committed checksum, not to stop keeping it. Losing the
  ledger means losing the identity of every address ever published.

Reproducibility is therefore stated precisely:

> Same source snapshots + same code version + same config + **same ledger**
> ⇒ byte-identical logical output.
> Same source snapshots + same code + **no ledger** ⇒ a valid genesis build in which
> every entity whose natural key never changed carries its original id.

## 4. Resolution order on every build

For each ABR town record, in this fixed order. First rule that fires wins; the rule
that fired is recorded in `address_entity.identity_match_rule`.

| # | Rule | Condition | Result |
|---|---|---|---|
| **I1** | key match | `(lg_code, machiaza_id)` equals the `current_*` pair of an **active** ledger row | reuse id |
| **I2** | code correction | same `lg_code`; `machiaza_id` differs; `current_normalized_name` equals the new normalized name; the candidate's previous key is **absent from this build**; exactly one ledger row qualifies | reuse id; lineage `code_corrected` |
| **I3** | municipality re-code | `machiaza_id` and normalized name both unchanged; `lg_code` changed; the old→new `lg_code` transition is **attested in `overrides/municipality_lineage.yml`**; the candidate's previous key is absent from this build; exactly one row qualifies | reuse id; lineage `municipality_recoded` |
| **I4** | rename | `(lg_code, machiaza_id)` unchanged, name changed | reuse id (I1 already fired); lineage `renamed` from the attribute diff |
| **I6** | reinstatement | key matches a **retired** ledger row | **never automatic** — review queue; mint a new id meanwhile |
| **I5** | new | nothing matched | mint a new id |

### Why these are narrower than they look

**I1 only ever considers active rows.** If ABR reuses a natural key after a town was
abolished, treating the reappearance as continuity would silently hand an unrelated new
town the old `address_id` — precisely the corruption an independent id exists to
prevent. A retired key that reappears goes to I6: reported, not resolved. Reinstatement
requires a human confirming compatible names and non-conflicting validity dates, after
which the ledger row is reactivated with a `reinstated` lineage row.

**I2 compares the current name, not the genesis name.** Using the genesis name would
fragment an entity that was legitimately renamed earlier and then had its code
corrected. It also requires the candidate's previous key to have *disappeared* in this
same build: a code correction is a disappearance/appearance pair, whereas a newly
created same-named town leaves the original key in place — and must therefore not
inherit anything.

**I3 does not infer from a municipality merger.** A municipality-level lineage statement
is not town-level evidence, and `POLICY.md` §4 forbids expanding one into the other. I3
fires only where a human has attested the specific `lg_code` transition in
`overrides/municipality_lineage.yml`. Without attestation the towns get new ids and the
old ones retire — more churn, but no invented continuity.

**Ambiguity never resolves itself.** If more than one ledger row qualifies under I2 or
I3, no id is reused: the record goes to `reports/review_required.csv` with every
candidate and is treated as new. A wrong identity carry-over is worse than a new id.

V1 ships `overrides/municipality_lineage.yml` empty; e-Stat 廃置分合 history is a V1.x
item (`LIMITATIONS.md`).

## 5. Retirement

A town that disappears from ABR, or whose `ablt_date` has passed, is **not deleted**.
`entity_status` becomes `retired`, `retired_at` is set to the source-stated `ablt_date`
when present (otherwise NULL), and `observed_to` is set to the last snapshot in which it
appeared. The ledger row remains forever.

A retired key that later reappears does **not** automatically recover its id: I1 ignores
retired rows, so the reappearance falls to I6 and is queued for review. Recovery is a
human decision recorded in `overrides/manual_overrides.yml`, because "the same code came
back" is not evidence that it denotes the same place.

## 6. Split and merge

Splits and merges do **not** reuse an id. Forcing one id across a 1:N or N:1 change would
assert an equivalence the source does not support. Instead new ids are minted and the
relationship is recorded:

```
address_lineage(
  lineage_id, old_address_id, new_address_id,
  relation_type,        -- split | merge | code_corrected | municipality_recoded
                        -- | renamed | retired | reinstated
  effective_date,       -- source-stated only; NULL if the source is silent
  observed_at,          -- snapshot in which this project detected it
  evidence,             -- what justified it
  evidence_source,      -- 'abr_efct_date' | 'abr_ablt_date' | 'manual_override'
  source_snapshot_id
)
```

`split` produces one row per (old, new) pair; `merge` likewise. Both directions are
queryable, so "what is this old id now?" and "where did this new id come from?" are both
answerable.

V1 detects split/merge only where ABR's own `efct_date` / `ablt_date` and code changes
make it unambiguous. Anything else is reported for review rather than guessed — a
suspected split with no source evidence is a `review_required` row, not a lineage row.

## 7. Worked examples

| Event | Ledger effect | Lineage row | Consumer impact |
|---|---|---|---|
| Town renamed, codes unchanged | `current_normalized_name` updated | `renamed` | none — id stable |
| ABR fixes a typo'd `machiaza_id` | `current_machiaza_id` updated (I2) | `code_corrected` | none — id stable |
| Municipality merges, `lg_code` changes, towns intact | `current_lg_code` updated (I3, if attested) | `municipality_recoded` | none — id stable |
| Municipality merges, unattested | no reuse (I5) | none; both ids exist | old id retired, new id minted, both visible |
| 大字 split into 一〜三丁目 | 3 new rows; old row retired | 3 × `split` | old id resolves to 3 successors |
| Two towns merged into one | 1 new row; 2 retired | 2 × `merge` | both old ids resolve to 1 successor |
| Town disappears from ABR | `entity_status='retired'` | `retired` | id kept, never reused |

## 8. Invariants (asserted in tests)

1. `address_id` matches `^jpa1[0-9a-hjkmnp-tv-z]{16}$` and is exactly 20 characters.
2. `address_id` is unique across the ledger, and no two distinct genesis keys hash to
   the same id.
3. Every `address.address_id` exists in the ledger.
4. A given `(current_lg_code, current_machiaza_id)` maps to at most one **active** id.
5. An id, once minted, never changes its `genesis_*` values.
6. Ids are never reused after retirement, and no retired row is reactivated without an
   `overrides/manual_overrides.yml` entry plus a `reinstated` lineage row.
7. Every `address_lineage.old_address_id` / `new_address_id` exists in the ledger.
8. `split` implies ≥2 distinct `new_address_id` for one `old_address_id`;
   `merge` implies ≥2 distinct `old_address_id` for one `new_address_id`.
9. No lineage row carries a non-NULL `effective_date` unless
   `evidence_source` names a source field that supplied it.
