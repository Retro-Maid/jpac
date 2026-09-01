# DATA_LICENSE

**The code in this repository is MIT licensed. The data it produces is not.**

Applying one blanket licence to the outputs would misrepresent four different
publishers' terms, so each source's conditions flow through to anything derived from it.
This file states the combined obligations; `dist/NOTICE.md` carries the
attributions actually emitted by a build, and `SOURCES.yml` carries the machine-readable
record of exactly which snapshots were used.

Analysis and drift handling: [`docs/LICENSE_POLICY.md`](docs/LICENSE_POLICY.md).
Terms verified **2026-08-23**, re-read verbatim against the publishers' own pages on
**2026-08-24** to settle whether the accepted payloads may be redistributed unmodified.

Three of the four publishers now adopt the same instrument — **公共データ利用規約（第1.0版）
/ PDL 1.0**. Only Japan Post stands apart, by asserting no copyright at all.

## Per-source terms

### デジタル庁 — アドレス・ベース・レジストリ

- **Terms:** 公共データ利用規約（第1.0版） (PDL 1.0)
  <https://www.digital.go.jp/policies/base_registry_address_tos> (page updated 2026-05-29)
- Redistribution: permitted. Modification: permitted, with a statement that the content
  was modified.
- Attribution required, including a note that the data was processed:

```
出典：「アドレス・ベース・レジストリ（町字マスター、町字・郵便番号変換表）」（デジタル庁）
      https://www.digital.go.jp/policies/base_registry_address を加工して作成
```

- **Recorded discrepancy:** the catalog's DCAT feed advertises CC BY 4.0 for the master
  datasets while the terms page states PDL 1.0. The publisher's terms page governs. Both
  values are stored per snapshot rather than silently reconciled.
- The 郵便番号変換表 additionally carries Japan Post's readme terms for its postal side,
  and the publisher labels it 参考情報 with no completeness guarantee.

### 日本郵便株式会社 — 郵便番号データ

- **Terms:** the publisher asserts no copyright:

  > 郵便番号データに限っては日本郵便株式会社は著作権を主張しません。
  > 自由に配布していただいて結構です。

  <https://www.post.japanpost.jp/service/search/zipcode/download/utf-readme.html>
- Redistribution and modification: freely permitted.
- Attribution is **not required**. It is given voluntarily:

```
郵便番号データ：日本郵便株式会社
      https://www.post.japanpost.jp/zipcode/dl/utf-zip.html
```

### 国土交通省 — 位置参照情報（大字・町丁目レベル）

- **Terms:** 国土数値情報ダウンロードサイト 利用規約 (施行 2026-03-23), which applies PDL 1.0
  <https://nlftp.mlit.go.jp/ksj/other/agreement.html>
- The terms name 位置参照情報 in scope explicitly. 位置参照情報 appears in neither §1.2
  (個別法令: 国土調査 and the Web mapping system, which is where the 測量法 / 国土地理院
  承認 requirement lives) nor §1.3 (別ルール: legacy 国土数値情報 datasets). The older
  国土情報利用約款 now sits at `agreement_02.html` and does not govern this dataset.
- Redistribution: permitted, unmodified as well as modified. Modification: permitted,
  with disclosure.
- Attribution required, and **processed data must not be presented as government-produced**:

```
「位置参照情報ダウンロードサービス」（国土交通省）
      https://nlftp.mlit.go.jp/isj/ をもとに jp-address-crosswalk 作成
```

  This is the publisher's own wording, and it differs from the other three on two
  points: the dataset is named 位置参照情報ダウンロードサービス, and the processed form
  is 「…をもとに○○作成」 rather than 「…を加工して作成」. PDL 1.0 §1.1 states that a
  publisher's own examples replace the PDL default, so this project follows each
  publisher's rather than imposing one house style.

- Coordinates must be described as **representative points for the whole 大字・町丁目**,
  never as building locations.
- MLIT place names must not be presented as the national standard place names.

### 総務省 — 市外局番の一覧 / 電気通信番号指定状況

- **Terms:** 公共データ利用規約（第1.0版） (PDL 1.0), adopted by the 総務省 site terms page
  <https://www.soumu.go.jp/menu_kyotsuu/policy/tyosaku.html>

  > 当ホームページで公開している情報…の著作権は、特記されていない限り総務省に帰属し、
  > 権利表記の記載がない限り「公共データ利用規約（第1.0版）」に準拠した利用条件の下で
  > 利用することができます。

  Not 政府標準利用規約: the page was re-read verbatim on 2026-08-24. §1.3 (個別法令) covers
  政党助成法 reports only and §1.4 covers symbols and logos, so neither reaches these
  datasets.
- Redistribution and modification: permitted with a modification notice.
- Attribution required:

```
出典：「市外局番の一覧」（総務省）
      https://www.soumu.go.jp/main_sosiki/joho_tsusin/top/tel_number/shigai_list.html を加工して作成

出典：「電気通信番号指定状況（固定電話等の電話番号）」（総務省）
      https://www.soumu.go.jp/main_sosiki/joho_tsusin/top/tel_number/number_shitei.html を加工して作成
```

- MIC datasets are **not** assumed to share one licence; each is checked individually and
  each snapshot stores its own licence fields.

## Redistribution of the unmodified payloads

A release carries two different things, and they do not take the same terms.

`raw-sources.tar.zst` holds each publisher's own file exactly as accepted — no parsing,
no normalization, byte-for-byte what was downloaded. **Redistributing those files
unmodified is permitted by all four publishers, and needs no further permission.** The
governing text is PDL 1.0 §1:

> 当ウェブサイトで公開している情報…は、別の利用ルールが適用されるコンテンツを除き、
> どなたでも…**複製、公衆送信**、翻訳・変形等の翻案等、自由に利用できます。商用利用も
> 可能です。

複製 and 公衆送信 are the acts redistribution consists of, and the clause does not
distinguish raw from derived. The 編集・加工 obligations attach only when content is
modified. Japan Post is unrestricted for a different reason: it asserts no copyright.

Two consequences follow, and both are obligations rather than options:

- **Unmodified payloads take the plain 出典 form, not the 加工 form.** Describing an
  untouched publisher file as 加工して作成 misstates what it is. `NOTICE.md` therefore
  emits two attribution blocks, and both are generated from `config/sources.yml` so they
  cannot drift from the reviewed wording.
- **The 出典 obligation still applies.** PDL 1.0 also says 数値データ、簡単な表・グラフ等
  are not copyrightable and fall outside the rules entirely. This project does not rely on
  that: a 727,418-row master with names, readings and coordinates is the paradigm case for
  a データベースの著作物 (著作権法 §12-2), and leaning on the exception would discard the
  attribution this project intends to give regardless.

PDL 1.0 §1.7 additionally permits use under CC BY 4.0. That is an **added** permission,
not an equivalence: PDL keeps its own 準拠法・合意管轄 (§1.5), its 第三者権利 reservation
(§1.2), and the あたかも国が作成したかのような prohibition, none of which CC BY carries in
that form. This project does not relicense, and the recorded ABR CC-BY discrepancy stays
recorded (see `docs/LICENSE_POLICY.md` §2).

## Combined obligation for redistributors

If you redistribute the built database, or anything derived from it, you must:

1. Retain `dist/NOTICE.md` unmodified, or reproduce every attribution it contains.
2. State that the data was **processed** and is not the publishers' own product.
3. Not present processed data as if produced by デジタル庁, 国土交通省 or 総務省.
4. Not describe MLIT coordinates as precise or building-level locations.
5. Keep this file, or an equivalent statement of the per-source terms, alongside the data.

Requirements 2–4 are the strictest of the four sources applied uniformly, which is the
safe reading when a derived work mixes all of them.

If you redistribute the **unmodified payloads** — on their own, or bundled as this
project does — requirements 1, 4 and 5 still apply, and 2 and 3 are replaced by their
opposite: do not describe those files as processed, because they are not. Use the plain
出典 block from `NOTICE.md`.

## Excluded on licence grounds

ABR `地番マスター` and `地番マスター位置参照拡張` are distributed under a separate
登記所備付地図データ利用規約 via G-Spatial Information Center. Redistribution under that
agreement has not been cleared for this project, so those datasets are not fetched,
parsed, or shipped. They are also below 町字 granularity, so nothing is lost for V1.

## Warranty

None, from anyone. The publishers disclaim warranties on their data; this project
disclaims warranties on the crosswalk. The Digital Agency explicitly labels the
町字・郵便番号変換表 参考情報 with no guarantee of completeness, accuracy, currency or
continuity — which is why this project scores it 0.99 rather than 1.00 and cross-checks
it against Japan Post.

## Drift

Every terms document is re-hashed on each run and compared with a committed,
human-reviewed baseline. Any change halts the build with `LICENSE_REVIEW_REQUIRED` and no
release is produced until a person reads the new terms and commits an updated baseline.
There is no automatic acceptance path.
