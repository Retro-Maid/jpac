# QUALITY_POLICY.md

## 1. The goal is not 100%

> Match Rate 100% is not a target (spec §79).

The target is: **produce few wrong crosswalks, and let users see the evidence and the
uncertainty.** A database that is 95% exact/equivalent, 3% ambiguous and 2% unresolved is
better than one forced to 100% without justification, and the reports are written so that
the second kind cannot be mistaken for the first.

A rising `exact` rate is therefore not automatically good news. If `exact` jumps while
`ambiguous` collapses, that is a suspected regression in matching strictness, and the
release stops until it is explained.

## 2. Per-source metrics

`row_count`, `duplicate_count`, `null_count` per column, `unique_key_count`,
plus encoding, schema fingerprint and parse-error count. Compared with the previous
release as absolute and percentage change.

## 3. Per-bridge metrics

`exact_count`, `equivalent_count`, `parent_count`, `child_count`, `overlap_count`,
`candidate_count`, `ambiguous_count`, `unresolved_count`, `auto_accept_count`,
`review_required_count`, `manually_verified_count`, `manually_rejected_count`,
plus a confidence histogram and a breakdown by `matching_rule_id`.

The per-rule breakdown is what makes a change explainable: "ambiguous rose 4 points"
is not actionable, but "rule P4 fired 3,000 fewer times and P6 3,000 more" is.

## 4. Thresholds (`config/quality_thresholds.yml`)

Release-blocking:

| Metric | Condition |
|---|---|
| Source row count | changed by more than ±5% vs previous |
| ABR town count | changed by more than ±2% |
| Duplicate natural keys | any |
| Required source | missing |
| Unresolved rate, any bridge | risen by more than 3 percentage points |
| Ambiguous rate, any bridge | risen by more than 3 points |
| Exact rate, any bridge | **fallen** by more than 3 points |
| Exact rate, any bridge | **risen** by more than 5 points without a matching-rule version bump |
| Addresses removed without a lineage row or a source-level removal | **any** (zero tolerance) |
| Addresses retired *with* evidence | more than 2% of the previous total |
| Invariant failures | any |

Warnings (recorded, not blocking): ±1–5% source row change, ±1–3 point rate moves,
new unresolved clusters in one prefecture, override count changes.

Thresholds are configuration and may be tuned against observed data, but a loosening is
a reviewed commit with a rationale, never an inline tweak to make a build pass
(`POLICY.md` §11).

An intentional matching-policy correction can move a rate beyond a normal threshold.
Such a migration is allowed only through `approved_rate_changes`, naming the exact old
and new matching-rule versions, bridge, metric, expected delta and a narrow tolerance.
The entry is one-shot because it cannot match again after the new version becomes the
comparison baseline. Rule 1.1.0 → 1.2.0 uses this mechanism for the reviewed removal of
municipality-to-町字 telephone expansion; the underlying invariant remains zero-tolerance.

## 5. Reports

`quality_report.json` (machine) and `QUALITY_REPORT.md` (human), both regenerated every
build:

- code version, data version, every contributing `source_snapshot_id`
- per-source row counts and deltas
- per-bridge relation/method/confidence distributions
- match rates with explicit numerator and denominator
- threshold evaluations with pass/warn/fail
- counts of `review_required` and `override_stale`

## 6. Review queue

`reports/review_required.csv` — one row per record needing human attention:

```
queue_id, bridge, source_record_id, source_description,
candidate_1_id, candidate_1_name, candidate_1_score,
candidate_2_id, candidate_2_name, candidate_2_score, ...,
match_method, matching_rule_id, confidence, candidate_count, reason
```

Sorted most-impactful first (largest candidate groups first), so partial human effort
still buys the most correctness.

**Only actionable rows.** `unresolved` rows and rows this project *derived* from a
municipality-level assertion are excluded: there is no decision a reviewer can make about
"MIC does not state town-level assignment" or "no MLIT counterpart exists". Including
them produced a 1.66-million-row file that nobody could work through. Those records are
still fully queryable — `unmatched_records` in SQLite, and the `unresolved` counts in
`quality_report.json` — they are simply not presented as pending human work.

What is left is what a person can settle: name disagreements between two publishers
(M2), genuine ambiguity (P6a/P6b/M4/T6), and partial-coverage overlaps (P4p/T3).

Resolutions are written to `overrides/manual_overrides.yml` with evidence, never patched
into code (spec §19).

`reports/override_stale.csv` lists overrides whose recorded source state no longer holds;
those are **not applied** and return to the queue (spec §71).

## 7. Diff report

`diff_report.json` / `DIFF_REPORT.md` versus the previous release (spec §42):

ABR towns added / removed / code-changed / renamed; postal codes added / removed;
old-postal correspondence changes; MLIT code and coordinate changes; area-code and
numbering-area changes; bridge relation changes (added, removed, `relation_type` changed,
`confidence` changed, `verification_status` changed); identity events (mint, retire,
split, merge, code correction).

Every removal must be explained by a lineage row or a source-level removal. A single
unexplained removal is `DATA_LOSS_SUSPECTED` and blocks the release — there is no
percentage allowance, because an allowance of "under 1%" would have let thousands of
canonical addresses disappear quietly. Properly evidenced retirements have their own,
separate percentage threshold.

## 8. Honest reporting

Match rates are always published with the denominator, split by `relation_type`, and
never as a single headline number. `ambiguous` and `unresolved` are reported as normal
outcomes rather than as failures, because they are the mechanism by which this database
avoids being confidently wrong.

## Approved migrations

Two gates accept a narrowly-scoped, reviewed exception: `approved_rate_changes`
and `approved_row_count_changes` in `config/quality_thresholds.yml`. Both name a
single transition by its exact measured values — rule versions and delta for the
first, table and exact before/after counts for the second — so an entry matches
the one change it was written for and is inert afterwards, because the new value
becomes the baseline.

This is deliberately not a threshold waiver. Raising
`source_row_count_change_pct` from 5% to 10% to admit one recovered-row migration
would widen the gate for every future release, and the gate exists precisely to
catch the unexplained volume change that a wider limit would let through. An
approval also carries `attested_by`, `attested_on` and a rationale, so the
release record says who decided and why.

Entries so far:

- `bridge_address_telephone` unresolved rate +92.453 points (rules 1.1.0 → 1.2.0):
  town-level telephone targets replaced by lossless T10 unresolved rows, because
  `POLICY.md` forbids expanding a municipality-level statement to 町字.
- `telephone_area_coverage` 1,489 → 1,629 rows: 140 clause rows recovered after
  `coverage_id` was found to collide whenever one clause named several
  municipalities, dropping the rest by row order.
