# そのまま実行できる SQL

すべて SQLite 成果物に対して実行できます。

```bash
sqlite3 jp_address_crosswalk.sqlite < docs/queries/01_postal_to_address.sql
```

各ファイルは冒頭で `.parameter set` により引数を与えています。別の値で引くときは、その行を
書き換えてください。

| ファイル | 引けるもの |
|---|---|
| [`01_postal_to_address.sql`](01_postal_to_address.sql) | 郵便番号 → ABR 町字。覆うすべての町字を証跡つきで返す |
| [`02_old_postal_to_current.sql`](02_old_postal_to_current.sql) | 旧郵便番号 → 現行郵便番号。1つの旧番号が多数の現行番号に対応するのが普通 |
| [`03_old_postal_to_mlit.sql`](03_old_postal_to_mlit.sql) | 旧郵便番号 → 現行郵便番号 → ABR 町字 → 国土交通省 大字町丁目コード |
| [`04_postal_to_area_code.sql`](04_postal_to_area_code.sql) | 郵便番号 → 市区町村レベルの市外局番候補（町字の辺ではない） |
| [`05_area_code_to_postal.sql`](05_area_code_to_postal.sql) | 市外局番 → 番号区画 → 市区町村 → 郵便番号 |
| [`06_mlit_to_postal.sql`](06_mlit_to_postal.sql) | 大字町丁目コード → ABR 町字 → 郵便番号 |
| [`07_ambiguous.sql`](07_ambiguous.sql) | パイプラインが確定を拒んだ行を、出典が残した形のまま一覧 |
| [`08_split_municipalities.sql`](08_split_municipalities.sql) | 複数の番号区画にまたがる市区町村（夕張市、岩見沢市 …） |
| [`09_partial_coverage.sql`](09_partial_coverage.sql) | 部分的な収容を述べた公式テキストを、除外規定つきで逐語表示 |
| [`10_unmatched.sql`](10_unmatched.sql) | 何にも対応しなかったレコード。削除せず保持している |
| [`11_postal_multi_town.sql`](11_postal_multi_town.sql) | 複数の町字を覆う郵便番号（日本郵便自身のフラグ13が示すもの） |
| [`12_provenance.sql`](12_provenance.sql) | ある住所の郵便番号の辺の来歴。どのスナップショットが生んだか |

## 読むときの注意

これらのクエリは**曖昧性を隠すための `LIMIT 1` を使いません**。1つの郵便番号が8つの町字を
覆うなら8行返します。それが問いに対する正しい答えであり、出典が名指ししていない1つを選ぶ
ことはしないためです。

1住所につき1行が必要な場合は、`LIMIT` ではなく証跡列で絞ってください。

```sql
WHERE postal_status = 'auto' AND postal_relation_type IN ('exact','equivalent')
```

これはパイプラインが自動確定を認めた対応だけを返し、データが曖昧なところでは黙って何も
返しません。「単一の対応を出せ」に対して単一の対応が存在しないとき、それが正直な答えです。

証跡列の一覧と意味は README の
[フラット表の列](../../README.md#フラット表の列)。

## 市外局番は町字の辺ではありません

`bridge_address_telephone` の行はすべて T10 の `unresolved` で、`target_id` は NULL です。
総務省が「この市区町村を丸ごと含む」と述べている場合でも同じです。公式の全部収容・一部収容
の記述は `bridge_municipality_telephone` にあり、`夕張市（富野を除く。）` のような除外規定は
`telephone_area_coverage` に原文のまま入っています。

市区町村レベルの候補が必要なら `address.lg_code` で結合してください（クエリ04・05）。ただし
その結合結果を「発行元が述べた町字単位の対応」として提示しないでください。総務省はそこまで
公表していません。

## 対応が付かなかったレコードの行き先

削除していません。

```sql
SELECT kind, COUNT(*) FROM unmatched_records GROUP BY kind;
```

対応率は、行を消すことによっては決して改善されません。

---

これらの `.sql` は `tools/verify_artifacts_agree.py` が毎ビルド実際に実行して検証しています。
ビューの列が変わればここが失敗するので、記載が黙って腐ることはありません。
