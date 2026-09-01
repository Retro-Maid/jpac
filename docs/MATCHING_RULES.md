# MATCHING_RULES.md

Every crosswalk edge is produced by exactly one **named, versioned rule**. The rule id is
stored on the bridge row, so any edge can be explained mechanically:
*"this exists because rule `M1` fired under `matching_rule_version` 1.0.0."*

Rules and scores live in `config/matching_rules.yml`. This document explains them.
`POLICY.md` §4 and §6 govern; where this document and `POLICY.md` appear to disagree,
`POLICY.md` wins.

---

## 1. What `confidence` means

`confidence ∈ [0.0, 1.0]` is a **deterministic rule-based trust score**, defined only by
which rule fired. It is not a probability, not a frequency, and not calibrated against
any ground truth. It must never be averaged with, multiplied by, or interpreted as a
statistical quantity.

Rank ordering only:

| Band | Meaning |
|---|---|
| 1.00 | The publisher states the relationship in codes, and an independent field confirms it |
| 0.99 | An official code-level mapping published for this exact purpose |
| 0.97 | Conservative normalized-name identity within one municipality, unique both ways |
| 0.90 | Structural parent/child stated by the source |
| 0.70 | Municipality-level statement only; no sub-municipal detail exists in the source |
| ≤0.50 | Candidate generation only; never sufficient on its own |

## 2. `relation_type`

Read as *source entity → target entity*, where the source is always the address (or
municipality) side.

| Value | Meaning |
|---|---|
| `exact` | Same real-world extent, confirmed by code **and** name |
| `equivalent` | Same extent asserted by official code mapping, name not independently confirmed |
| `parent` | Source contains the target |
| `child` | Source is contained by the target |
| `contains` | Source strictly contains the target and others |
| `overlap` | Extents intersect; neither contains the other |
| `candidate` | A plausible pairing produced by candidate generation, not confirmed |
| `ambiguous` | Several plausible pairings exist and none can be preferred deterministically |
| `unresolved` | No target could be established; target id is NULL |

`unresolved` means exactly one of a bridge row's two endpoints is known. Both endpoints
are concrete nullable foreign keys, so an unmatched *foreign* record (a postal or MLIT row
with no ABR counterpart) is retained just as losslessly as an unmatched address — the
opposite endpoint is simply NULL (`docs/DB_SCHEMA.md` §5.1). Nothing is ever dropped to
make a rate look better.

`ambiguous` rows always have a non-NULL `candidate_group_id`, `candidate_count > 1`, and
`is_unique_match = false`.

## 3. `match_method`

| Value | Meaning |
|---|---|
| `direct_code` | The publisher itself maps the two codes |
| `exact_name` | Byte-identical raw names within the same municipality |
| `normalized_name` | Identical after the conservative normalization profile |
| `parent_child` | Derived from a stated hierarchy (e.g. town → chome) |
| `composite` | Two or more independent signals agreed |
| `official_area_rule` | Derived from official prose (MIC 対象地域 text) |
| `manual_override` | From `overrides/manual_overrides.yml` |
| `unresolved` | Nothing applied |

## 4. Auto-accept gate

```
AUTO_ACCEPT  ⇔  confidence >= 0.98
             AND candidate_count == 1
             AND candidate_count_is_complete
             AND relation_type IN ('exact', 'equivalent')
             AND is_unique_match
             AND NOT override_stale
```

Anything else is `verification_status='review_required'` and lands in
`reports/review_required.csv`. **A high score never overrides `candidate_count > 1`.**
The gate is a conjunction on purpose: no single condition can carry a row through.

The same conjunction is also a `CHECK` constraint on every bridge table
(`docs/DB_SCHEMA.md` §5.1), so a regression in application code cannot export an
over-accepted row: the write fails instead. A gate that lives only in Python is a gate
that a future refactor can quietly remove.

`verification_status ∈ {auto, review_required, manually_verified, manually_rejected}`
is stored independently of `confidence`, because a human decision is a different kind of
fact from a rule score.

---

## 5. Postal rules (`bridge_address_postal`, `bridge_municipality_postal`)

The Digital Agency publishes ABR町字・郵便番号変換表 specifically because this
relationship is many-to-many. V1 therefore leads with the
official mapping and uses names only for what it does not cover.

| Rule | Condition | relation | method | conf | Notes |
|---|---|---|---|---|---|
| **P0** | Row in `manual_overrides.yml` | as stated | `manual_override` | as stated | Highest precedence |
| **P1** | `abr_post_code` row with non-empty `machiaza_id`, resolving to a known `address_id`, and `post_code` present in Japan Post `ken_all` | `equivalent` | `direct_code` | 0.99 | → `bridge_address_postal_code`. Official mapping, independently corroborated |
| **P1x** | Same, but `post_code` **absent** from `ken_all` | `candidate` | `direct_code` | 0.70 | Recorded, flagged, never auto-accepted; the two publishers disagree |
| **P2** | `abr_post_code` row with **empty** `machiaza_id` | `parent` | `direct_code` | 0.99 | → `bridge_municipality_postal`, never attached to a town |
| **P3** | Japan Post record is `no_listing` / `city_banchi` / `ichien` | `parent` | `official_area_rule` | 0.99 | → `bridge_municipality_postal` |
| **P4** | Postal `town` record with no parenthetical (or a non-geographic one), whose `jis_city_code` + normalized town name matches exactly one ABR town, and that town matches exactly one postal record | `exact` | `normalized_name` | 0.97 | Unique in **both** directions |
| **P4p** | As P4 but the town field carries a **geographic** parenthetical | `overlap` | `normalized_name` | 0.70 | The record covers part of the town, so it is not `exact` |
| **P5** | Postal town name equals the ABR `oaza_cho` of N ≥ 2 towns that differ only by `chome` | `parent` | `parent_child` | 0.90 | One row **per chome**; the 西新宿 case |
| **P6a** | One postal record matches ≥2 ABR towns, not explained by P5 | `ambiguous` | `normalized_name` | 0.50 | One row per ABR candidate, grouped by `postal_record_id` |
| **P6b** | One ABR town is the sole name match for ≥2 postal records | `ambiguous` | `normalized_name` | 0.50 | One row per postal record, grouped by `address_id` |
| **P7** | Nothing matched | `unresolved` | `unresolved` | 0.00 | Row kept, `address_id` NULL |

**P5 is the rule the specification calls out by name.** Japan Post `東京都新宿区西新宿`
must not collapse onto `西新宿二丁目`. P5 emits eight `parent` rows —
西新宿一丁目 … 西新宿八丁目 — each carrying the same `candidate_group_id` and
`candidate_count = 8`, so no row can pass the auto-accept gate.

P1 and P1x target the **postal code entity**, not a `ken_all` record. The conversion
table names a 7-digit code, and one code appears in many records; pointing the edge at a
single record would manufacture a record-level assertion the Digital Agency never made.
Record-level matching lives in `bridge_address_postal` under P4–P7
(`docs/DB_SCHEMA.md` §4.1).

P4's both-directions test is what prevents an N:1 collapse, and the two P6 variants exist
because the two directions are genuinely different shapes. **P6a** is "this postal record
has several possible towns" — grouped by the record. **P6b** is the N:1 case: each postal
record has exactly *one* candidate, so P6a's condition never fires, yet the town is
contested between records. Without P6b those rows would fall through to `unresolved` and
the real candidates would be lost. Grouping key, emitted rows and candidate count are
specified per variant in `config/matching_rules.yml`.

**Parentheticals decide whether `exact` is even available.** Japan Post town fields carry
notes such as banchi ranges or building exclusions. Stripping them for comparison is
fine; treating the stripped result as the same extent as the whole ABR town is not,
because the record may cover only part of it.
`postal_record_version.parenthetical_class` (`none` / `non_geographic` / `geographic` /
`unknown`) is computed at parse time from an explicit pattern list, and only `none` and
`non_geographic` admit P4. Everything else routes to P4p as `overlap` with
`review_required`.

## 6. MLIT rules (`bridge_address_mlit`)

`mlit_code[0:5]` looks like `jis_city_code` and `mlit_code[5:12]` looks like
`machiaza_id`. **That resemblance alone never produces an edge** (spec §22).

| Rule | Condition | relation | method | conf | Notes |
|---|---|---|---|---|---|
| **M0** | Manual override | as stated | `manual_override` | as stated | |
| **M1** | `mlit_code[0:5] == jis_city_code` **and** `mlit_code[5:12] == machiaza_id` **and** normalized names equal | `exact` | `composite` | 1.00 | Code and name agree independently |
| **M2** | Same code match, names differ | `equivalent` | `direct_code` | 0.90 | `review_required`; name diff stored in `mismatch_note` |
| **M3** | No code match; `jis_city_code` equal and normalized name matches exactly one ABR town **and** one MLIT row | `exact` | `normalized_name` | 0.97 | Unique both directions |
| **M4** | ≥2 candidates on either side | `ambiguous` | `normalized_name` | 0.50 | All kept |
| **M5** | Nothing matched | `unresolved` | `unresolved` | 0.00 | Kept, id NULL |

M2 exists because a name difference is real information: it usually means one of the two
publishers has updated and the other has not. Downgrading to 0.90 and demanding review is
the honest handling; silently trusting the code would manufacture `exact` edges across a
disagreement.

Representative coordinates ride along on the MLIT row. They are **never** used as
matching evidence — proximity is not identity.

## 7. Telephone rules

Two tables, because the source operates at two different granularities.

`telephone_area` holds the numbering areas themselves. `telephone_area_coverage` holds
one row per parsed clause of the official 対象地域 text, with `coverage_type` and the
verbatim `exception_text`.

| Rule | Condition | relation | method | conf | Target |
|---|---|---|---|---|---|
| **T0** | Manual override | as stated | `manual_override` | as stated | — |
| **T1** | Clause names a municipality with **no** qualifier, and that municipality resolves to exactly one `lg_code` | `child` | `official_area_rule` | 0.99 | `bridge_municipality_telephone`, `coverage_type='full'` |
| **T3** | Clause carries 「〜を除く。」 or 「〜に限る。」 naming **sub-municipal** places | `overlap` | `official_area_rule` | 0.70 | `bridge_municipality_telephone`, `coverage_type='partial'`, `exception_text` verbatim. Never expanded to 町字. |
| **T4** | Clause carries 「〜に限る。」 naming **whole municipalities** inside a 郡 | `child` | `official_area_rule` | 0.99 | Treated as T1 for each named municipality |
| **T5** | Clause names a 郡 with no qualifier | `child` | `official_area_rule` | 0.95 | Expanded only to the municipalities of that 郡 |
| **T6** | Municipality name resolves ambiguously | `ambiguous` | `official_area_rule` | 0.50 | All candidates kept |
| **T7** | Clause unparseable | `unresolved` | `unresolved` | 0.00 | `exception_text` kept verbatim for review |
| **T1b** | Municipality name matches only after folding ヶ/ケ, **and** the folded name is unique in that prefecture | `child` | `official_area_rule` | 0.95 | MIC writes 袖ヶ浦市 / 鎌ヶ谷市 / 龍ヶ崎市 where ABR writes ケ |
| **T1c** | Clause says 「東京都23区」 and ABR carries exactly 23 特別区 | `child` | `official_area_rule` | 0.99 | An official collective term, expanded as a set identity; if the count is not 23 the term is not honoured |
| **T10** | MIC evidence exists only at municipality level | `unresolved` | `unresolved` | 0.00 | One `bridge_address_telephone` row per 町字, `target_id=NULL`, `coverage_type='municipality_only'` |

**T3 is the rule that keeps the model honest.** `北海道夕張市（富野を除く。）` is real
official text. V1 knows that 夕張市 is split between numbering areas `003` and `004-2`
and says exactly that, at municipality level, with the clause attached. It does not
decide which 町字 of 夕張市 belongs to which area, because the source never says.

**No municipality-to-town expansion.** T10 is deliberately repetitive: every canonical
町字 remains represented in `bridge_address_telephone`, but its target is NULL. Full,
partial, 郡-level and collective municipality statements all stop in
`bridge_municipality_telephone`. This enforces the repository rule that a
municipality-level statement must not be expanded to 町字 merely because set arithmetic
would make such an expansion tempting. Consumers can query the official municipality
evidence without mistaking it for a publisher-stated town assignment.

### The municipality-name fold (T1b)

§8 refuses to fold ヶ/ケ for 町字, and must: two towns can differ by exactly that character.
A municipality name is different in kind — a closed official list of 1,918 — so the fold
can be applied and then **checked**: it is accepted only when the folded name resolves to
exactly one `lg_code` in that prefecture. That check is what makes it a rule rather than a
guess, and it is why the fold is confined to municipality names.

### One clause, several municipalities

「上北郡（東北町、野辺地町、横浜町及び六ヶ所村に限る。）」 is a single `clause_raw` and
four coverage rows. `coverage_id` was keyed on `(numbering_area_code, clause_raw)`, so the
four collided and the dedup kept whichever came first — a match chosen by row order, which
`POLICY.md` §4 forbids by name. 140 rows were dropped nationally and 122 郡部の町村 ended up
with no numbering area.

The id is now content-addressed over every field that distinguishes a clause row, and the
dedup is checked: only byte-identical rows may collapse, and anything else raises. The
build could not see the loss before because nothing compared the parser's output with what
was stored, so `test_every_parsed_coverage_clause_survives_into_the_table` now does exactly
that.

`telephone_number_block` (from the XLS) is a plain fact table keyed by
`numbering_area_code`; it is joined by code and involves no matching.

## 8. Normalization used by name rules

Defined in `config/address_normalization.yml`, versioned as
`normalization_profile_version`, and recorded per bridge row in
`normalization_profile` so any match can be replayed.

**Applied** (`conservative` profile):

- Unicode NFKC
- full-width ASCII digits → half-width (`１丁目` → `1丁目`)
- kanji numerals in a 丁目 context → arabic (`一丁目` → `1丁目`) — bounded to the
  `丁目` suffix, never applied to general place names
- all whitespace removed
- hyphen-like characters (`-` `‐` `‑` `–` `—` `−` `ー` in numeric contexts) unified
- 大字 / 字 prefixes stripped only for the `mlit_relaxed` profile, never for `conservative`

**Deliberately not applied**, because these distinctions can be meaningful in Japanese
place names (`POLICY.md` §4):

- ヶ / ケ / が / ガ
- ノ / 之 / の
- 旧字体 ↔ 新字体 (e.g. 澤/沢, 齋/斎, 邊/辺)
- 高 / 髙, 崎 / 﨑
- katakana ↔ hiragana

Two towns that differ only in these characters therefore do **not** match, and if a match
is genuinely intended it must be added to `overrides/manual_overrides.yml` with evidence.
That is the intended cost: an explicit, reviewed exception beats a silent global rule.

## 9. Fuzzy similarity

Permitted **only** to generate candidates for the review report, and only inside an
already-narrowed block (same `jis_city_code`). Never to confirm, rank-and-pick, or set
`confidence`. Any edge whose only support is a similarity score is `candidate` or
`ambiguous`, never `exact` or `equivalent`.

Blocking by municipality keeps this out of O(N²): the largest block is a few thousand
rows, not 200k.

**Candidate counts are computed before any capping.** The review report renders at most
ten candidates per record, but `candidate_count` and `is_unique_match` are always derived
from the complete set, and `candidate_count_is_complete` records whether the rendered
list was truncated. A capped display must never be mistakable for the full ambiguity
set.

An LLM is never in this path (`POLICY.md` §4).

## 10. Manual overrides

```yaml
- id: OVR-0001
  bridge: bridge_address_mlit
  source: {address_id: jpa1...}
  target: {mlit_code: "011010001001"}
  set: {relation_type: exact, confidence: 1.0,
        verification_status: manually_verified}
  reason: "…"
  evidence: "…"                 # what was checked
  evidence_url: "https://…"     # official page consulted
  observed_source_state:        # guards against stale reuse
    abr_snapshot_sha256: "…"
    mlit_snapshot_sha256: "…"
  created_at: 2026-08-23
  created_by: "…"
```

On every build each override is re-checked against current source state. If the recorded
`observed_source_state` no longer holds, the override is marked `override_stale`, is
**not applied**, and is reported (spec §71). A stale override silently continuing to
apply would be exactly the kind of invisible wrong answer this project exists to avoid.

## 11. Rule versioning

`matching_rule_version` is semver on `config/matching_rules.yml`.

- **patch** — wording, comments; no edge changes
- **minor** — new rule added, existing rules unaffected
- **major** — an existing rule's condition or score changes

Any minor or major bump requires a diff report against the previous release showing
exactly which edges changed (`docs/QUALITY_POLICY.md`). Every bridge row stores the
version that produced it, so a mixed-version table is still fully explainable.
