# TEST_STRATEGY.md

## 1. Layers

| Layer | Scope | Network |
|---|---|---|
| Unit | normalization, identity minting, clause parsing, `.doc` extraction, rule scoring | no |
| Adapter | `discover()` / `fetch()` against mocked HTTP (`respx`) | no |
| Fixture | full pipeline on committed miniature sources | no |
| Integration | build → validate → export → reopen SQLite/Parquet and query | no |
| Data quality | thresholds and invariants on the built database | no |
| Regression | golden outputs for the fixture build | no |
| Schema drift | fingerprint computation and mismatch detection | no |
| License drift | terms-hash comparison, and the encoding a terms page is hashed under | no |
| Licensing | attribution wording per publisher, and what a shipped record can evidence | no |
| Live smoke | discovery only, against real sources | yes, `-m live` |

Everything except the live smoke runs offline, so CI is deterministic and the project
stays testable on a machine that cannot reach the publishers.

## 2. Fixtures

Extracted from the real official downloads verified on 2026-08-23, with the source
snapshot recorded in `tests/fixtures/FIXTURE_PROVENANCE.md` (file, SHA-256, extraction
date, selection rule). No hand-written address data — a fabricated fixture would test the
fabricator, not the source (spec §59).

Coverage, chosen to exercise the cases that break naive implementations:

| Case | Fixture content |
|---|---|
| Tokyo 23 wards | 新宿区西新宿一〜八丁目 + Japan Post `西新宿` — the P5 1:N case named in the spec |
| 政令指定都市 | 札幌市中央区 (ward layer, `ward` populated) |
| 郡部 | 空知郡南幌町, 樺戸郡月形町 |
| 北海道 | 夕張市 and 岩見沢市 — municipalities **split across numbering areas** |
| Kyoto | 京都市 with `kyoto_st` (通り名) populated |
| 丁目 present / absent | 旭ケ丘一丁目 vs a flat 大字 |
| Postal 1:N | one town, several postal codes (`flag_multi_code = 1`) |
| Postal N:1 | one postal code, several towns (`flag_multi_town = 1`) |
| Duplicate town names | same normalized name in two municipalities |
| Special postal records | 以下に掲載がない場合 / 岡谷市の次に番地がくる場合 / 境町の次に番地がくる場合 / 利島村一円 |
| Old postal codes | `"060  "` (3-digit, padded) and `"10003"` (5-digit) |
| Area-code exceptions | 夕張市（富野を除く。）, 岩見沢市（宝水町を除く。）, 樺戸郡（浦臼町及び新十津川町に限る。） |
| Post-merger municipality | a town whose `lg_code` changed, for identity rules I3/I5 |
| MLIT name form | `旭ケ丘一丁目` vs ABR `旭ケ丘` + `１丁目` |

## 3. Invariant tests (spec §60)

Structural:

1. `postal_code` matches `^[0-9]{7}$`.
2. `old_postal_code` matches `^[0-9]{3,5}$` and never loses a leading zero;
   `"060  "` → `"060"`, never `"60"` or `60`.
3. `lg_code` `^[0-9]{6}$`; `jis_city_code` `^[0-9]{5}$`;
   `jis_city_code == lg_code[0:5]` everywhere.
4. `machiaza_id` `^[0-9]{7}$`; `mlit_code` `^[0-9]{12}$`;
   `area_code` `^[0-9]{2,5}$`; `numbering_area_code` `^[0-9]{3}(-[0-9])?$`.
5. Every code column has Arrow dtype `Utf8` in every emitted Parquet file — asserted on
   the file, not on the in-memory frame.
6. Latitude ∈ [20, 46], longitude ∈ [122, 154] (Japan), or both NULL.

Referential:

7. Every bridge `address_id` exists in `address_entity`.
8. Every bridge with `relation_type <> 'unresolved'` has a non-NULL target that exists.
9. Every row in every table has a `source_snapshot_id` that exists.
10. Every `address.lg_code` exists in `municipality`.

Semantic:

11. `0.0 <= confidence <= 1.0`.
12. No row with `verification_status='auto'` and `candidate_count > 1`.
13. No row with `verification_status='auto'` and `confidence < 0.98`.
14. No row with `verification_status='auto'` and
    `relation_type NOT IN ('exact','equivalent')`.
15. `relation_type='unresolved'` ⟺ `target_entity_id IS NULL`.
16. `is_unique_match = true` ⟹ `candidate_count = 1`.
17. `candidate_count > 1` ⟹ `candidate_group_id IS NOT NULL`, and every member of that
    group shares it.
18. No postal record with `record_kind <> 'town'` appears in `bridge_address_postal`.
19. Every `bridge_address_telephone` row is T10 with `target_id=NULL`,
    `relation_type='unresolved'`, `candidate_count=0`,
    `coverage_type='municipality_only'`, and `derivation=NULL`.
20. Every emitted `matching_rule_id` exists in `config/matching_rules.yml`, and every
    emitted `matching_rule_version` equals the configured version.
21. `valid_from` is non-NULL only for sources that publish a validity date
    (ABR `mt_town`, ABR `abr_post_code`); it is NULL for Japan Post and MLIT.
22. `valid_from` never equals `downloaded_at`.

Identity (`docs/IDENTITY_MODEL.md` §8):

23. `address_id` matches `^jpa1[0-9a-hjkmnp-tv-z]{16}$` and is unique.
24. No hash collision between distinct genesis keys.
25. At most one **active** entity per `(lg_code, machiaza_id)`.
26. Ids are never reused after retirement.
27. `split` ⟹ ≥2 successors; `merge` ⟹ ≥2 predecessors.

## 4. Property tests

`hypothesis` over the normalizer:

- normalization is idempotent: `n(n(x)) == n(x)`
- normalization never returns empty for non-empty input
- the conservative profile **never** unifies ヶ/ケ/が, ノ/之/の, or 旧字体/新字体 —
  generated pairs differing only in those characters must stay distinct
  (`docs/MATCHING_RULES.md` §8)
- `bridge_id` and `address_id` are stable under input row permutation

## 5. Determinism tests

Build the fixture set twice, in two shuffled input orders, and assert the two Parquet
outputs are byte-identical after the canonical sort. This is the test that catches the
non-determinism Polars joins can introduce (`docs/ARCHITECTURE.md` §6).

## 6. Regression

Golden `dist/` outputs for the fixture build are committed. A diff is a test failure
unless the change is intentional and the golden files are updated in the same commit
with a rationale — which makes every behavioural change visible in review.

## 7. Negative tests

The failure paths are tested as first-class behaviour, because they are the safety
mechanism:

- HTML body served where a zip was expected → `SOURCE_FETCH_FAILED`, not a parse attempt
- relative link resolved against the pre-redirect URL → caught by an adapter test
  asserting `urljoin` uses `response.url`
- altered terms text → `LICENSE_REVIEW_REQUIRED`
- added/removed/reordered column → `SOURCE_SCHEMA_CHANGED`
- zip bomb (high ratio, oversized member, `..` in a member name) → refused
- integer-typed code column anywhere → test failure
- a manual override whose recorded source state no longer holds → `override_stale`,
  not applied
- two equally good name candidates → `ambiguous` with both retained, never a pick

## 8. Source reconciliation

Inspecting the normalized tables does not guarantee the path **from the publisher to the
shipped artifacts**. `tools/` holds an independent verification per layer:

| Tool | Layer it verifies |
|---|---|
| `compare_all_fields.py` | `data/raw` → DB (all 26,360,566 values, no sampling) |
| `reconcile_sources.py` | row counts, key sets, special-record classification |
| `verify_artifacts_agree.py` | DB → Parquet/SQLite/CSV.gz, manifests, mojibake, the 12 example queries, `docs/schema.sql` |
| `verify_distribution.py` | distributions against the raw data; the two flat-view implementations agree |
| `verify_cross_source.py` | the three publishers against each other; geographic sanity |
| `verify_idempotent.py` | two builds are logically identical |

`tools/reconcile_sources.py` re-reads every raw payload **independently of the
pipeline** and accounts for each input row against the built tables: key sets,
row counts, field values on a sample, leading zeros, special-record
classification, and whether the two MIC datasets actually join.

This is a different kind of check from everything above, and it earns its place:
both row-loss defects this project has had were found by it and by nothing else.
One Japan Post record and five MLIT records were being discarded by an id that
hashed part of a row followed by `unique(keep="first")` — every internal
invariant passed the whole time, because the surviving data was perfectly
self-consistent. Only counting against the original files reveals that class of
bug.

`tools/compare_all_fields.py` goes further: it compares **every field of every
row**, with no sampling — 26,360,566 values across the six sources on the
national build. Fields the pipeline deliberately transforms are checked against
that transformation's own contract (empty string → NULL, `"060  "` → `"060"`,
trailing-space stripping, collapsed duplicate keys) rather than being skipped.

It also compares **whole rows**, not just columns. Comparing columns
independently cannot see a value moving between rows: field A from row 1 paired
with field B from row 2 leaves every per-column multiset identical. Row-level
multiset comparison closes that hole.

### The gap both of those still leave

Both compare the database against `data/raw/`. That proves the pipeline
propagated its inputs faithfully — it says nothing about whether those were the
right bytes. A stale cache, a truncated download, a fallback URL or the wrong
dataset would sail through every check above, because the wrong data would be
propagated perfectly consistently.

Closing it means going back to the publisher, and acquisition lives on the
internal side (`POLICY.md` §13). It is checked there, when a payload is accepted:
the bytes are re-fetched, hashed against the accepted copy and against
`source_snapshot.sha256`, every archive member is CRC-checked, and the URL is
required to have come from the publisher's own page rather than from a recorded
fallback. Adapters report `resolved_via` and it is stored in `source_snapshot`,
so the shipped provenance says how each URL was obtained even though this
repository never fetches anything.

What this repository can check without the network is that the publishers agree
with each other and that the result is geographically plausible — `jpac verify
cross-source`.

### The three shipped files must also agree with each other

`tools/verify_artifacts_agree.py` checks that Parquet, SQLite and CSV.gz give the
same answers. Nothing else does: every check above validates the normalized
tables, not the files built from them, so two users could reach different
conclusions from the same release and no test would notice. It also verifies the
`SHA256SUMS` manifest and scans every text column for mojibake, since CP932 and
EUC-JP are decoded by hand here and a decoding error surfaces as wrong characters
rather than as an exception.

It found a real gap on first run: `snapshot_license_artifact` was absent from the
shipped SQLite, because an empty frame carries no columns and the writer skipped
it — and an offline rebuild, which performs no licence check, always produces
that. The table now carries its schema whether or not it has rows.

The deeper half of that gap took longer to see: carrying the schema fixed the
*shape*, but the table was still empty on every offline build, so a shipped
release could not evidence which terms had been reviewed. It now always emits the
committed baselines, and `tests/test_licensing.py` holds it to that — along with
the rule that unmodified payloads and processed artifacts do not take the same
出典, which only became a correctness question once the payloads started shipping
alongside the database.

### Shape, and the flat view against a second implementation

`tools/verify_distribution.py` covers what referential invariants cannot: a
parser can lose an entire prefecture, or attach every town to one municipality,
without breaking a single key constraint.

The town counts are skewed enough to look like a fault — Fukushima carries 76x
Yamanashi's — so they are not judged against a threshold someone invented but
reconciled against the raw ABR file prefecture by prefecture. Two identities
must hold, so a compensating error cannot hide: every raw row survives into
`address_rsdt_variant`, and `address` holds exactly its distinct keys. The skew
is the publisher's.

It then rebuilds the flat view and compares it with the shipped file — but
recomputing with `build_flat_view` only proves the file was not damaged after
being written. The check that matters compares the Parquet flat file against the
SQLite `address_crosswalk` view, which is a **second, independently written
implementation** of the same definition: hand-written SQL rather than Polars
joins. That found two defects the first time it ran:

* `old_postal_code` was missing from the SQL view, so SQLite users could not see
  a column Parquet and CSV users had;
* the filtered view was `SELECT * FROM address_crosswalk_all WHERE …`, which
  removes the whole address row when its only bridge is rejected. No release had
  a rejected or superseded bridge yet, so nothing had ever exercised it — the
  first human rejection would have deleted addresses from one artifact and not
  the others. `POLICY.md` §4 forbids exactly that.

The second one is the more useful lesson: a test written against data that
cannot trigger the bug passes on the broken code. The regression test
(`test_rejected_and_superseded_edges_blank_columns_but_keep_the_address`) marks
one bridge rejected and one superseded on purpose, and was confirmed to fail
against the pre-fix definition before being kept.

Run them all after any build that will be released:

```bash
jpac verify                  # every check below, in order
jpac verify sources          # row accounting, key sets, classification
jpac verify fields           # every field of every row vs data/raw
jpac verify cross-source     # publishers against each other, geographic sanity
jpac verify artifacts        # Parquet vs SQLite vs CSV.gz, manifest, encoding
jpac verify distribution     # distribution vs raw, flat view vs the SQL view
jpac verify diagrams         # README figures vs their .mmd sources
```

Each one is a script under `tools/` and can also be run directly with
`py -3.12 tools/<name>.py`.

`verify_artifacts_agree.py` also executes every query in `docs/queries/`. Those
are what the README points users at, and removing a column from a view breaks
them without touching a single table. It found one that had never worked:
`11_postal_multi_town.sql` selected `postal_code` from `bridge_address_postal_code`,
which stores the far end of the edge in `target_id`. A query nobody runs is a
query nobody notices is broken.

Executing is not the same as demonstrating, so a query that returns nothing is
also a finding: `12_provenance.sql` carried `'jpa1...'` as its parameter, so it
parsed, returned zero rows, and would have kept doing so after any rename. It
now uses a real `address_id`.

A note on all four: each of them has had a bug that produced a false result —
a URL check that could not distinguish discovery from fallback, a column-wise
comparison blind to cross-row scrambling, and a type comparison that read
SQLite's INTEGER booleans as differences. Before believing a verification
result, check that the verification measures what it claims to.

The row-count half of it also runs as an invariant test
(`test_every_source_row_survives_to_its_table`) whenever `data/raw/` is present.

## 9. CI

`ci.yml` runs ruff, mypy, the full offline suite, the deterministic fixture build, and
the invariant suite against the fixture database on every push and PR. The live smoke
job is scheduled and manual only, never a PR gate — a publisher's outage must not fail
someone's pull request.
