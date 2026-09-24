# ブリッジの端点を型付き列にする（計画と実施記録）

`docs/LIMITATIONS.md` 項目8 の未解決側。`docs/DB_SCHEMA.md` §5.1 が設計として書いている
「2つの具体的な、それぞれ NULL 可の外部キー列」に対して、出荷されている6本のブリッジは
いまも多態な `target_id` 1列を持っている。§5.1 の理由づけはそのまま有効なので、ここで
繰り返さない。**この文書が決めるのは、いつ・どの順で・何を壊して移るか**である。

1点だけ §5.1 の形を具体化している。§5.1 は「1つの表が2つの端点列を持つ」と読める形で
書かれているが、実測（§1.1）を経て**各表が端点をちょうど1つ持つ**形にした。結論は同じ
——「端点は型付きで、どの表を指すかが列で分かる」——で、達し方が違う（A2）。

§2 は 2026-09-24 に決定済み（フェーズ0）。A8 の署名を含め9項目すべてに答えがあり、
**フェーズ1〜4 と6の実装は同日に完了し、ゲートはすべて実測で通っている**（§3）。
残っているのは v2.0.0 の公開そのものである。

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
`postal_code_entity` に当たり 1,910行は当たらない（またはその逆）。

「多態な id は外部キーを持てない」だけが問題ではなかった。**どの表を指しているかを列が
言っていない**のが問題である、という実測がこれである。

そして**住所側は、同じ区別を最初から2つの表でやっている**:

| | 端点 | 行数 |
|---|---|---:|
| `bridge_address_postal_code` | 郵便番号 | 676,124 |
| `bridge_address_postal` | `postal_record_id` | 352,499 |

市区町村側だけが、この2種類を1つの表に詰めていた。A2 の答えが「2列に分ける」ではなく
「**2表に分ける**」なのはこれが理由である —— 新しい形を考えるのではなく、住所側が既に
持っている形に揃える。

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

## 2. 決めたこと（2026-09-24 決定、着手前のフェーズ0）

7項目すべてに答えが出ている。以下は決定であって選択肢の一覧ではない —— 変えるなら、
この節を書き換えてから実装する。

### A1. 列名は親テーブルの主キー名に揃える

| 移行後のブリッジ | 端点列 | 親 |
|---|---|---|
| `bridge_address_postal_code` | `postal_code` | `postal_code_entity` |
| `bridge_address_postal` | `postal_record_id` | `postal_record` |
| `bridge_address_mlit` | `mlit_record_id` | `mlit_town` |
| `bridge_address_telephone` | `numbering_area_code` | `telephone_area` |
| `bridge_municipality_postal_code` | `postal_code` | `postal_code_entity` |
| `bridge_municipality_postal` | `postal_record_id` | `postal_record` |
| `bridge_municipality_telephone` | `numbering_area_code` | `telephone_area` |

揃えると `export/writers.py` の `_FK_BY_COLUMN` 規約（列名 → 親テーブル）がそのまま
効き、**外部キーを別途書かなくても付く**。揃えなければ7本それぞれに手書きの対応表が
要る —— 誰かが拡張を忘れる一覧が1つ増える。

### A2. `bridge_municipality_postal` は**2表に分ける**（2列ではない）

`bridge_municipality_postal_code`（P2、郵便番号 8,207行）と
`bridge_municipality_postal`（P3、`postal_record_id` 1,910行）に分ける。

理由は §1.1 のとおり、**住所側が既にその形だから**である。新しい設計を持ち込むのでは
なく、`bridge_address_postal_code` / `bridge_address_postal` と対称にする。結果として:

- 各表の端点がちょうど1つになり、`CHECK` に「2列のうち一方だけが NULL」を書かずに済む
- 外部キーも1本ずつ
- 利用者は**どちらの表を読むかを選ぶだけ**になり、`matching_rule_id` による分岐が消える

代償は出荷テーブルが1本増えること（36 → **37**）。どちらの案でもこの表の利用者は
書き換えが要るので、分割が追加の破壊になるわけではない。

### A3. `target_id` は残さない

併記すると「`postal_record_id` と `target_id` のどちらが正か」を利用者が判断すること
になり、**いま `matching_rule_id` で分岐を強いているのと同じ問題が形を変えて残る**。
しかも2つが食い違ったときに気づく仕組みが無い。移行の猶予は A7 で与える。

### A4. `direction` はそのまま

方向は「どちらの側から見た行か」で、端点の**型**とは別のことを言っている。型付き列は
方向を代替しない。`bridge_municipality_postal` が `municipality_to_postal` しか持って
いないのは、方向の概念が要らないからではなく、逆方向の行がまだ無いからである。

### A5. `CHECK` は `DB_SCHEMA.md` §5.1 が本来書いている**強い形**にする

現行は「少なくとも片方の端点がある」だけを言う:

```sql
CHECK (address_id IS NOT NULL OR lg_code IS NOT NULL OR target_id IS NOT NULL)
```

移行後は、**方向ごとに、正しいほうだけが NULL であること**を要求する。たとえば
`bridge_address_postal` なら「住所側から見た行は `address_id` を持つ」「日本郵便側から
見た未一致行は `postal_record_id` を持つ」を縛る。

弱い形（各表の端点列で `OR` を書き直すだけ）でも `POLICY.md` §5 のデータ損失禁止は
守れるが、**設計文書が主張していることを DB に言わせない理由が無い**。実データは現状
すでに強い形を満たしている —— `bridge_address_telephone` の 726,170行はすべて
`address_id` あり・端点 NULL で一貫している（§1.2）。

帰結として、**「両方 NULL」も「両方非 NULL」も DB が止める**。それは狙いであって副作用
ではない。将来どちらかを作りたくなったら、`CHECK` を緩める前に「その行は何を主張して
いるのか」を先に決めることになる。

### A6. 版は v2.0.0

出荷スキーマの列が**消える**ので minor では出さない。タグはコード版とデータ版を併記
する形のまま（`v2.0.0+data-YYYY-MM-DD`）。

### A7. 互換ビューを v2.0.0 に同梱し、**v2.1.0 で落とす**

旧い名前で読めるビューを**6本**置く —— 旧い表が6本だからである。分割した2表は1つのビューに戻す（`UNION ALL`）:

```sql
CREATE VIEW bridge_address_postal_v1 AS
  SELECT bridge_id, address_id, lg_code,
         postal_record_id AS target_id,        -- 旧い名前で見せる
         direction, relation_type, ...
  FROM bridge_address_postal;
```

- **データを二重に持たない**（ビューなので容量ゼロ）。`FLAT_VIEWS` / `DDL_VIEWS` の
  仕組みが既にある
- 旧いクエリは名前に `_v1` を足すだけで動く
- **ビューは外部キーを持てない。** `_v1` を読み続ける利用者は参照整合性の恩恵を受けない
  —— それは「移行してください」という圧力として正しく働く

v2.1.0 で落とす、という期限をリリースノートに書く。期限の無い互換層は落ちない。

### A8. 分割で `candidate_count` の意味が変わる（署名済み）

A2 は「表を分ける」と言っているだけだが、**分けると候補数の数え方が変わる**。実装に入る
前に、これを別の主張として書き出しておく。

いまの `bridge_municipality_postal` は候補数を **P2 と P3 をまとめて** 数えている
（`build/postal.py` の `counts = both.group_by("lg_code")`）。表を分ければ、各表が
自分の中で数えることになる。

| | いま | 分割後 |
|---|---:|---:|
| `candidate_count = 1` の行 | **113** | **3,210**（P2: 1,779 lg_code のうち 1,336 が単独 / P3: 1,892 のうち 1,874） |
| `is_unique_match = true` の行 | 113 | 3,210 |
| `verification_status` | 全 10,117行が `review_required` | **変わらない** |

`verification_status` が動かないのは、両規則の `relation_type` が `parent` で、auto の
条件（`relation_type IN ('exact','equivalent')`）を満たしえないからである。**つまり
「要確認だった行が自動確定に変わる」ことは起きない。**

それでも署名が要る理由: `candidate_count` はこのプロジェクトで単なる集計ではなく、
`docs/MATCHING_RULES.md` §4 の auto-accept 連言の一部である。意味を「この市区町村の
行数」から「**この表の中での**この市区町村の行数」に変えるのは、行数の遷移に署名を
求めてきたのと同じ種類の行為である。

**どちらが正しいか。** 分割後の数え方のほうが正しい。「この市区町村の郵便番号は何通り
あるか」と「この市区町村に日本郵便の特殊レコードが何件あるか」は別の問いで、混ぜて
数えた値はどちらの問いにも答えていなかった。

実測（2026-09-24 の全国ビルドで確認）:

| | 分割前 | 分割後 |
|---|---:|---:|
| `bridge_municipality_postal_code` | — | 8,207行（うち `candidate_count = 1` が 1,336） |
| `bridge_municipality_postal` | 10,117行（うち 113） | 1,910行（うち 1,874） |
| 合計 | 10,117 | **10,117** |
| `verification_status` | 全行 `review_required` | **全行 `review_required`** |

- **attested_by**: retro.maid.itworker@gmail.com
- **attested_on**: 2026-09-24

### A9. 常に NULL の主語列は**触らない**（v2.0.0 のスコープ外と決める）

実測すると、主語の側にも使われていない列がある:

| 表 | 常に NULL の列 | 行数 |
|---|---|---:|
| `bridge_address_postal_code` / `_postal` / `_mlit` / `_telephone` | `lg_code` | 全行 |
| `bridge_municipality_postal` / `_telephone` | `address_id` | 全行 |

「この表では使わない列」が残るのは、いま解いている問題と同じ種類の分かりにくさである。
それでも **v2.0.0 では落とさない**。出荷されている列を消すのはメジャー版が引き受ける
仕事で、v2.0.0 は既に端点の改名でそれをやっている。ここを別に落とせば**破壊的
リリースが3回続く**（v2.0.0 で端点、v2.1.0 で互換ビュー廃止、さらにもう1回で主語列）。

A5 は主語と端点の**両方が存在すること**を前提に「方向ごとに正しいほうだけが NULL」を
言っているので、この決定と矛盾しない。落とすなら、次のメジャー版で改めて計画を書く。

## 3. フェーズとゲート

> **ゲートを飛ばさない。** 各フェーズは前のゲートが通ってから着手する。

| フェーズ | 内容 | ゲート |
|---|---|---|
| **0** | §2 を人が決める | ✅ **2026-09-24 完了**（A1–A7 に決定が書かれている） |
| **1** | ✅ `build/common.py` に端点レジストリ（`BRIDGE_ENDPOINTS`）を置き、7本のビルダーを直す。`bridge_municipality_postal` を分割 | ✅ 下記 G1 の2条件を実測で通過 |
| **2** | ✅ `CHECK`（A5 の強い形）をレジストリから生成し、`PRIMARY_KEYS` / `SORT_KEYS` / 品質ゲート / 索引に新しい表を登録、`docs/schema.sql` を出荷物から作り直す | ✅ `foreign_keys_checked: true`（全国568万行）、`jpac verify artifacts` が**79オブジェクト一致**、0 problem |
| **3** | ✅ `build_flat_view` の読み出し元を端点列に変える | ✅ **フラット表は43列のまま、`target_id` を含まない** |
| **4** | ✅ 互換ビュー6本（A7）を生成する | ✅ `bridge_municipality_postal_v1` が旧い列名で 10,117行を返す |
| **5** | `docs/queries/` 5本 ✅ ・README ・`DB_SCHEMA.md` §5.1 ・`LIMITATIONS.md` 項目8 を更新 | ✅ 12本すべて実行でき、空を返さない |
| **6** | ✅ 全国ビルド（`build: PASS 1.3.0+data-2026-09-17`、177ゲート 0 failed、37テーブル）。版を 2.0.0 に上げた再ビルドとリリースノートは公開時に行う | ✅ 品質ゲート全通 |

**G1（フェーズ1のゲート）で確かめるべき実測**:

1. `bridge_municipality_postal` の 8,207 + 1,910 が、**2つの表に分かれて合計が
   変わらない**こと（= 10,117）。ここで行が増えたり減ったりするなら、移行が「多態を
   解いた」のではなく「データを作り変えた」ことになる
2. **両方の表の全行が `review_required` のままである**こと（A8）。これが「行が移った」
   ことと「分類が変わった」ことを分ける。`candidate_count` は意図して変わる（113 →
   3,210）が、`verification_status` は動いてはいけない

フィクスチャでこのゲートは意味を持つ（確認済み）: 変換表の市区町村レベル行が 644件
あり P2 を作る。ken_all の非 town レコードが11件（`city_banchi` 2 / `ichien` 1 /
`no_listing` 8）あり P3 を作る。

**G2 の予測は外れた（実測で判明）。** 「`bridge_municipality_postal` が 10,117 → 1,910 の
-81.1% になるので行数変化の承認が要る」と書いたが、**そのゲートは存在しなかった**。
`row_count_change.*` が見るのは品質レポートの `tables` 節で、**ブリッジはそこに載って
いない**（`bridges` 節に一致率などが載る）。全国ビルドは承認なしで PASS した。

つまり **ブリッジが行を8割失っても、行数のゲートは鳴らない。** 今回は分割による移動
なので実害はないが、これは移行とは独立した**ゲートの穴**である。`docs/LIMITATIONS.md`
に記録し、ブリッジの行数ゲートを足すかどうかは別途判断する（足せば、この移行そのものが
最初の承認対象になる）。

## 4. やらないこと

- **`bridge_id` の作り方は変えない。** 内容アドレスの材料に端点が入るので値は変わるが、
  作り方（content-addressed）は同じである。値が変わること自体は v2.0.0 の破壊的変更に
  含まれる
- **`target_id` 時代のデータを移行しない。** 出荷物は毎回 payload から作り直されるので、
  変換すべき既存データは無い（`address_code` のような累積表とは違う）
- **フラット表の列名は変えない。** §1.3 のとおり、変える必要が無い
- **互換ビューに外部キーを持たせようとしない。** ビューは持てない（A7）。旧い名前で
  読み続けることの代償がそれである
- **`bridge_municipality_postal_code` を住所側と統合しない。** 端点が同じ
  `postal_code_entity` でも、主語が市区町村と住所で違う。統合すれば主語の混ざった表が
  できるだけで、いま解こうとしている問題の別形になる

## 5. 着手の条件

§2 は決まった（フェーズ0 完了）。着手を止めているのは設計ではなく**タイミング**である。

v1.3.0 の時点で `target_id` は**外部キーを持てない唯一の列**になった
（`docs/LIMITATIONS.md` 項目8）。参照整合性の穴としては最後の1つだが、出荷スキーマの
列を消す変更なので、**利用者が追随する準備ができるタイミングでしか出せない**。v1.3.0
は配布ファイル名の変更（`.sqlite` → `.sqlite.gz`）を含んでおり、同じ時期に2つの破壊的
変更を重ねると、利用者はどちらで壊れたのかを切り分けられない。

フェーズ1 に入る前に確かめること:

- v1.3.0 の配布形式の変更が利用者に行き渡ったか（少なくとも1リリース分の間隔）
- 互換ビュー（A7）を v2.1.0 で落とす期限に、こちらが付き合えるか —— 期限を書いて
  落とさない互換層は、無いほうがましである

