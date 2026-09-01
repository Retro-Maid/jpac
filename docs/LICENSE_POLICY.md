# LICENSE_POLICY.md

## 1. Split

**Code** — MIT (`LICENSE`).

**Data** — not MIT, and never relicensed. Each source keeps its own terms, which flow
through to anything derived from it. `DATA_LICENSE.md` states the combined obligations;
`NOTICE.md` carries the attributions; `SOURCES.yml` carries the machine-readable record.
Applying one blanket licence to the outputs would misrepresent four different
publishers' terms.

## 2. Conclusions as verified on 2026-08-23, re-read on 2026-08-24

The 2026-08-24 pass read all four publishers' pages verbatim to answer a question the
earlier review had not been asked: may the **accepted payloads be redistributed
unmodified**, not merely the data derived from them? The answer is yes for all four, and
the Redistribution column now distinguishes the two cases.

Three of the four publishers adopt the same instrument, **PDL 1.0**. MIC was previously
recorded only as "site terms", which named the page but not the licence.

| Source | Terms | Redistribution (unmodified / derived) | Modification | Attribution |
|---|---|---|---|---|
| ABR (Digital Agency) | 公共データ利用規約（第1.0版）PDL 1.0 — https://www.digital.go.jp/policies/base_registry_address_tos (page updated 2026-05-29) | Yes / Yes | Yes, with a modification notice | 出典 + URL required |
| Japan Post postal codes | Publisher states no copyright claim: 「郵便番号データに限っては日本郵便株式会社は著作権を主張しません。自由に配布していただいて結構です。」 | Yes, freely / Yes, freely | Yes | Not required; given voluntarily |
| MLIT 位置参照情報 | 国土数値情報ダウンロードサイト 利用規約 (施行 2026-03-23), applying PDL 1.0 — https://nlftp.mlit.go.jp/ksj/other/agreement.html | Yes / Yes | Yes, with a modification notice | 出典 + dataset name + URL; must not present processed data as government-produced |
| MIC (総務省) | 公共データ利用規約（第1.0版）PDL 1.0, adopted by https://www.soumu.go.jp/menu_kyotsuu/policy/tyosaku.html | Yes / Yes | Yes, with a modification notice | 出典 + URL |

**Basis for the unmodified column.** PDL 1.0 §1 permits 複製 and 公衆送信 of the content
itself and does not distinguish raw from derived; the 編集・加工 obligations attach only
when content is modified. Japan Post is unrestricted for a different reason — it asserts
no copyright at all. `DATA_LICENSE.md` carries the quoted text and the consequences.

**MLIT, specifically.** The 2026-03-23 terms name 位置参照情報 in scope and apply PDL 1.0
with no redistribution clause of their own. 位置参照情報 is in neither §1.2 (個別法令 —
国土調査 and the Web mapping system, which is where the 測量法 / 国土地理院承認 requirement
lives) nor §1.3 (別ルール — legacy 国土数値情報 datasets). The older 国土情報利用約款 now
sits at `agreement_02.html` and does not reach this dataset, so the previously recorded
name "PDL 1.0 + 位置参照情報 利用約款" cited a document that no longer applies. The
per-download 同意 page (`_view_stipulation.cgi`) rendered no terms body under GET on
2026-08-24, nor in its 2023 Internet Archive capture; the only click-through in the
download flow is a Google Forms アンケート, and the direct data URL answers 200 with no
cookie or referer gate. **The published 利用規約 governs, not a per-download contract.**
The stipulation stays recorded in `config/sources.yml` as evidence, and capturing its text
on the acquisition side is still worth doing — for the provenance record in §4, not
because the conclusion depends on it.

**PDL §1.7 does not collapse the CC BY discrepancy.** §1.7 permits use under CC BY 4.0 in
addition to PDL, which is why a DCAT feed can advertise CC BY without contradicting the
terms page. It does not make PDL 1.0 *be* CC BY: PDL keeps §1.5 準拠法・合意管轄, the §1.2
第三者権利 reservation, and the あたかも国が作成したかのような prohibition. Both values stay
recorded and unreconciled.

**Recorded discrepancy.** The ABR DCAT feed advertises CC BY 4.0 while the Digital Agency
terms page states PDL 1.0. The publisher's terms page governs. Both values are stored in
`source_snapshot`; the difference is surfaced, not resolved by preference
(`config/sources.yml` の該当 license ブロック).

**Per-dataset, not per-ministry.** MIC datasets are not assumed to share a licence.
e-Gov entries and soumu.go.jp pages are checked individually and each snapshot carries
its own licence fields (spec §32).

## 3. Excluded on licence grounds

ABR `地番マスター` and `地番マスター位置参照拡張` fall under a separate
登記所備付地図データ利用規約 distributed via G-Spatial Information Center. Redistribution
under that agreement has not been cleared for this project, so those datasets are not
fetched, parsed, or shipped. They are also below 町字 granularity, so nothing is lost for
V1 (`POLICY.md` §3).

## 4. Drift detection

Every run records `license_name`, `license_url`, and `license_text_sha256` — the SHA-256
of the normalized text of the terms page. Any change in any of the three raises
`LICENSE_REVIEW_REQUIRED` and stops the release. That check runs on the internal
acquisition side, which is where the terms pages are read.

### What the shipped record can evidence

A build here never reads a terms page, so `license_text_sha256` is null unless the
acquisition side states what it observed in `data/raw/<source>/_payload.yml` (README,
"受理済みペイロードのマニフェスト"). That is acceptable for a build; it is not acceptable
for a **redistributed** release, which has to be able to show which terms were in force.

So the committed baselines travel with every release regardless. `snapshot_license_artifact`
carries one row per terms document — reviewed baseline, observed hash where one exists, and
the resulting `review_decision` — and `dist/SOURCES.yml` repeats the primary baseline and
its review date per snapshot. Where a manifest does supply an observed hash, it is compared
against the baseline and a difference stops the release, so the §11 licence-drift gate now
has an in-repository path rather than existing only on the acquisition side.

Before this, an offline rebuild emitted an empty `snapshot_license_artifact`, and the
shipped record carried no licence evidence at all.

Text hashing rather than URL comparison is the point: terms are routinely revised in
place. Normalization strips scripts, styles, navigation chrome and whitespace so that an
unrelated site redesign does not produce a false positive, while a change to the terms
body does.

Clearing the gate requires a human to read the new terms, update this file and
`config/sources.yml`, and commit the new hash with a rationale. No automatic acceptance
path exists (spec §33).

### The committed baselines hash mis-decoded text

All five baselines were re-computed against the live pages on 2026-08-24 and every one
reproduced exactly, so **there is no drift** — but reproducing them required decoding
three of the pages the way a client that ignores the declared charset would:

| Artifact | Decoded as | Actually |
|---|---|---|
| `abr.primary_terms`, `abr.policy_page` | UTF-8 | UTF-8 — correct |
| `japanpost.primary_terms` | ISO-8859-1 | UTF-8 |
| `mic.primary_terms` | ISO-8859-1 | Shift_JIS, declared in the XML prolog |
| `mlit.primary_terms` | EUC-JP | UTF-8 with a BOM (the EUC-JP pin belongs to the download CGI, not this page) |

Three of the four publishers serve `text/html` with no charset parameter, and the
acquisition client fell back to ISO-8859-1. The gate still works — the bytes are
deterministic, so a change in the terms body still changes the hash. Two things are
nevertheless wrong with it. A publisher merely *adding* a charset header would change the
decoded text and fire a false `LICENSE_REVIEW_REQUIRED`. And more importantly for a
redistributed record, a hash over mojibake is weak evidence of what the terms said, which
is precisely what shipping the payloads asks that record to carry.

`snapshot.resolve_html_encoding()` and `snapshot.license_text_hash_bytes()` implement the
correct resolution — BOM, then transport header, then the document's own declaration — and
each artifact in `config/sources.yml` now records the correctly-decoded value alongside its
baseline as `text_sha256_decoded`.

**The baselines are deliberately left at their existing values.** Flipping them would halt
every build on the acquisition side until that code adopts the new function, and this
repository cannot change it. Promoting `text_sha256_decoded` to `text_sha256` is a
two-sided release: land the function on the acquisition side first, then re-baseline here
in the same change.

## 5. Attribution actually emitted

`NOTICE.md` is generated at build time — the snapshot table from what was really used, the
attribution wording from `config/sources.yml` — so neither can drift from what was
reviewed. Each entry carries provider, dataset name, source page URL, version/snapshot
date, licence name and URL, and an explicit statement that the data was processed.

### Two forms, because a release carries two different things

The built artifacts are processed data. The payloads in `raw-sources.tar.zst` are the
publishers' own files, untouched. **They do not take the same 出典.** Describing an
unmodified publisher file as 加工して作成 misstates what it is, so `NOTICE.md` emits both
blocks, and `config/sources.yml` carries both forms per source under `license.attribution`.

PDL 1.0 §1.1 states that a publisher's own 重要情報 examples replace the PDL default, so
each form is the publisher's wording rather than a house style. This matters most for
MLIT, whose examples differ from the other three on two points: the dataset is named
位置参照情報ダウンロードサービス, and the processed form is 「…をもとに○○作成」, not
「…を加工して作成」.

```
# unmodified payloads
出典：位置参照情報ダウンロードサービス（国土交通省）
      https://nlftp.mlit.go.jp/isj/

# processed artifacts
「位置参照情報ダウンロードサービス」（国土交通省）
      https://nlftp.mlit.go.jp/isj/ をもとに jp-address-crosswalk 作成
```

MLIT's requirement that processed data not be presented as government-produced is met by
stating processing on every artifact and in the README. The converse also has to hold: the
raw payloads must not be described as processed.

## 6. When terms are unclear

Exclude conservatively and document the reason (spec §78). A source whose redistribution
terms cannot be established is not shipped, and the exclusion is recorded here rather
than left as an undocumented gap.
