# ブリッジの端点を型付き列にする（計画）

`docs/LIMITATIONS.md` 項目8 の未解決側。`docs/DB_SCHEMA.md` §5.1 が設計として書いている
「2つの具体的な、それぞれ NULL 可の外部キー列」に対して、出荷されている6本のブリッジは
いまも多態な `target_id` 1列を持っている。§5.1 の理由づけはそのまま有効なので、ここで
繰り返さない。**この文書が決めるのは、いつ・どの順で・何を壊して移るか**である。

着手前の文書であり、実装の記録ではない。フェーズとゲートを飛ばさない。

## 1. 実測（2026-09-24、v1.3.0 相当のビルド）

6本のブリッジはすべて同じ30列を持つ。`target_id` に何が入っているかは表ごとに違う。

| ブリッジ | `target_id` の中身 | 親テーブル | 行数 |
|---|---|---|---:|
| `bridge_address_postal_code` | 郵便番号（7桁） | `postal_code_entity` | 676,124 |
| `bridge_address_postal` | `postal_record_id` | `postal_record` | 352,499 |
| `bridge_address_mlit` | `mlit_record_id` | `mlit_town` | 733,948 |
| `bridge_address_telephone` | （**全 NULL**） | `telephone_area` | 726,170 |
| `bridge_municipality_postal` | **2種類が混在**（下記） | 両方 | 10,117 |
| `bridge_municipality_telephone` | `numbering_area_code` | `telephone_area` | 2,189 |

### 1.1 `bridge_municipality_postal` は表の内側で多態だった

| 照合規則 | `target_id` の中身 | 行数 |
|---|---|---:|
| `P2` | 郵便番号（7桁） | 8,207 |
| `P3` | `postal_record_id` | 1,910 |

**同じ列の同じ表に、2種類の識別子が入っている。** 利用者はこの列を join する前に
`matching_rule_id` で分岐しなければならない —— そうと知らずに join すれば、8,207行は
`postal_code_entity` に当たり 1,910行は当たらない（またはその逆）。型付き列に分けると、
この分岐は**スキーマが答える**ことになる。

「多態な id は外部キーを持てない」だけが問題ではなかった。**どの表を指しているかを列が
言っていない**のが問題である、という実測がこれである。

### 1.2 `bridge_address_telephone` は全行 `unresolved`

726,170行すべてが `relation_type = 'unresolved'`、`target_id` は全 NULL。市外局番の
区画に町字レベルの対応を作らないという判断（`POLICY.md`、承認済みの rate change）の
結果であり、移行後の `numbering_area_code` 列は**今日の時点では全 NULL になる**。
列を作る意味が無いのではなく、**空であることが答えである**行が 726,170行ある、という
ことをスキーマが言えるようになる。

### 1.3 壊れる範囲は、以前の記述より狭い

`docs/LIMITATIONS.md` 項目8 は「6本のブリッジと**フラット表**の列名が変わる」と書いて
いたが、**フラット表は変わらない**。フラット表は既に型付きの名前を出している:

```
$ 出荷されている jp_address_crosswalk.parquet の43列に target_id は無い
  postal_code / numbering_area_code / mlit_code / …
```

`build_flat_view` が `target_id` を読んで `postal_code` などに alias しているだけなので、
**移行で変わるのは内部の読み出し元**であって、出荷される列名ではない。

したがって影響を受けるのは:

- **SQLite の正規化ブリッジ6本を直接読んでいる利用者**（フラット表・フラット CSV・
  フラット parquet を使っている利用者は影響を受けない）
- `docs/queries/` の12本のうち5本（`04` `05` `07` `08` `11`）
- README の関係の多重度と ER の説明

`dist/parquet/` の正規化テーブルはリリースアセットではない（出荷されるのはフラット
parquet 1枚）ので、parquet 利用者にも影響しない。

## 2. 決めること（実装前に決め、ここに書き足す）

| | 問い | 既定の答え |
|---|---|---|
| **A1** | 列名は親テーブルの主キー名に揃えるか | 揃える（`postal_code` / `postal_record_id` / `mlit_record_id` / `numbering_area_code`）。`_FK_BY_COLUMN` の規約がそのまま効く |
| **A2** | `bridge_municipality_postal` は**2列**持つのか | 持つ。P2 は `postal_code`、P3 は `postal_record_id`。§5.1 の「それぞれ NULL 可」が意味を持つのはここである |
| **A3** | `target_id` を残して併記するか | **残さない。** 併記すると「どちらが正か」を利用者が判断することになり、いま分岐を強いているのと同じ問題が形を変えて残る |
| **A4** | `direction` はどうなるか | そのまま。方向は「どちらの側から見た行か」で、端点の型とは別のことを言っている |
| **A5** | `CHECK` をどう書き換えるか | 現行の `CHECK (address_id IS NOT NULL OR lg_code IS NOT NULL OR target_id IS NOT NULL)` を、各ブリッジの端点列を数える形にする。**片側が NULL でも行が残る**性質（`POLICY.md` §5 のデータ損失禁止）を落とさない |
| **A6** | 版はどうするか | **v2.0.0。** 出荷スキーマの列が消えるので minor では出さない |
| **A7** | 移行期間を置くか | 判断が必要。v1.x の最終リリースで「次は列名が変わる」と告知する案と、v2.0.0 のノートだけで足りるとする案がある |

## 3. フェーズとゲート

> **ゲートを飛ばさない。** 各フェーズは前のゲートが通ってから着手する。

| フェーズ | 内容 | ゲート |
|---|---|---|
| **0** | この文書の §2 を人が決める | A1–A7 に答えが書かれている |
| **1** | `build/common.py` の共通スキーマを端点列に置き換え、6本のビルダーを直す | フィクスチャビルドが通り、`finalize_bridge` の重複排除キーが端点列で成立する |
| **2** | `CHECK` と `FOREIGN KEY` を書き換え、`docs/schema.sql` を更新 | `PRAGMA foreign_key_check` が全国データで違反0、`jpac verify artifacts` がオブジェクト一致 |
| **3** | `build_flat_view` の読み出し元を端点列に変える | **フラット表の43列が1列も変わらない**こと（バイト比較） |
| **4** | `docs/queries/` 5本・README・`DB_SCHEMA.md` §5.1 の「What is actually shipped」注記を更新 | 12本すべて実行でき、空を返さない |
| **5** | 全国ビルド + 2回ビルドのバイト一致 + v2.0.0 のリリースノート | 品質ゲート全通、`verify idempotent` 0 problem |

**G1（フェーズ1のゲート）で確かめるべき実測**: `bridge_municipality_postal` の
8,207 + 1,910 が、2つの列に分かれて**合計が変わらない**こと。ここで行が増えたり減ったり
するなら、移行が「多態を解いた」のではなく「データを作り変えた」ことになる。

## 4. やらないこと

- **`bridge_id` の作り方は変えない。** 内容アドレスの材料に端点が入るので値は変わるが、
  作り方（content-addressed）は同じである。値が変わること自体は v2.0.0 の破壊的変更に
  含まれる
- **`target_id` 時代のデータを移行しない。** 出荷物は毎回 payload から作り直されるので、
  変換すべき既存データは無い（`address_code` のような累積表とは違う）
- **フラット表の列名は変えない。** §1.3 のとおり、変える必要が無い

## 5. なぜ今やらないのか

v1.3.0 の時点で `target_id` は**外部キーを持てない唯一の列**になった（`docs/LIMITATIONS.md`
項目8）。参照整合性の穴としては最後の1つだが、出荷スキーマの列を消す変更なので、
**利用者が追随する準備ができるタイミングでしか出せない**。v1.3.0 は配布ファイル名の変更
（`.sqlite` → `.sqlite.gz`）を含んでおり、同じリリースに2つの破壊的変更を入れると、
利用者はどちらで壊れたのかを切り分けられない。
