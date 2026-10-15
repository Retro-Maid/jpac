# そのまま実行できる SQL

すべて SQLite 成果物に対して実行できます。

```bash
sqlite3 jp_address_crosswalk.sqlite < docs/queries/01_postal_to_address.sql
```

各ファイルは冒頭で `.parameter set` により引数を与えています。別の値で引くときは、その行を
書き換えてください。

### 郵便番号・住所・国交省コード・市外局番

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

### 市区町村・駅・路線・バス停・人口・廃置分合

| ファイル | 引けるもの |
|---|---|
| [`13_municipality_profile.sql`](13_municipality_profile.sql) | 市区町村の概要を1行で。人口・世帯、駅・路線・バス停・郵便番号の数、市外局番 |
| [`14_stations_in_municipality.sql`](14_stations_in_municipality.sql) | 市区町村の駅と路線。政令市の親コードを渡すと区に広げて引く |
| [`15_municipalities_on_line.sql`](15_municipalities_on_line.sql) | 路線が通る市区町村と、その人口・その路線の駅の数 |
| [`16_line_stations.sql`](16_line_stations.sql) | 路線の駅と各駅の市区町村（N02 は駅の並び順を持たない） |
| [`17_bus_stops_per_capita.sql`](17_bus_stops_per_capita.sql) | 都道府県内の市区町村ごとのバス停の数と、人口1万人あたりの数 |
| [`18_postal_to_stations.sql`](18_postal_to_stations.sql) | 郵便番号 → 市区町村 → その市区町村の駅（最寄り駅ではない） |
| [`19_lineage_to_current.sql`](19_lineage_to_current.sql) | 古い市区町村コードを廃置分合で現行まで再帰的にたどる。行き先が分かれれば全部 |
| [`20_age_structure.sql`](20_age_structure.sql) | 年齢3区分・年齢不詳・65歳以上の割合、公表の平均年齢と中位数 |
| [`21_aging_by_prefecture.sql`](21_aging_by_prefecture.sql) | 都道府県内の市区町村を65歳以上の割合の順に |
| [`22_unresolved_stations_and_stops.sql`](22_unresolved_stations_and_stops.sql) | 市区町村が1つに決まらなかった駅とバス停。候補を並べて1行に |
| [`23_municipality_postal_codes.sql`](23_municipality_postal_codes.sql) | 市区町村の郵便番号すべて。P2（7桁）と P3（特殊レコード）の2つのブリッジを合わせる |
| [`24_area_code_population.sql`](24_area_code_population.sql) | 市外局番の番号区画ごとの人口と、値が無い理由（`value_status`） |
| [`25_source_editions.sql`](25_source_editions.sql) | このリリースが読んだ元データの一覧（発行元・版・取得日・規約） |

### 学校

| ファイル | 引けるもの |
|---|---|
| [`26_schools_by_postal_code.sql`](26_schools_by_postal_code.sql) | 郵便番号 → 町字 → その町字にある学校。郵便番号が合うかも並べる |
| [`27_schools_in_municipality.sql`](27_schools_in_municipality.sql) | 市区町村の学校を学校種ごとに数える。町字まで決まった数と、市区町村どまりの数 |

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

`bridge_address_telephone` の行はすべて T10 の `unresolved` で、端点の `numbering_area_code` は NULL です。
総務省が「この市区町村を丸ごと含む」と述べている場合でも同じです。公式の全部収容・一部収容
の記述は `bridge_municipality_telephone` にあり、`夕張市（富野を除く。）` のような除外規定は
`telephone_area_coverage` に原文のまま入っています。

市区町村レベルの候補が必要なら `address.lg_code` で結合してください（クエリ04・05）。ただし
その結合結果を「発行元が述べた町字単位の対応」として提示しないでください。総務省はそこまで
公表していません。

## 市区町村の表を結ぶときの注意（クエリ13〜25）

- **政令市は親と区の両方の行があります。** 人口（`census_population_municipality`）は
  市全体の行（`area_level = '1'`）と区の行の両方を持つので、合計するときは `area_level <> '1'`
  で絞ります。駅・路線・バス停は区にだけ結ばれていて、親コードには1件も結ばれていません。
  政令市全体で引くときはクエリ14の書き方で区に広げてください。
- **決まらなかった駅・バス停は候補ごとに行があります。** `relation_type = 'ambiguous'` の行は
  候補の市区町村それぞれに1行あるので、件数は `COUNT(DISTINCT n02_group_code)` /
  `COUNT(DISTINCT p11_stop_id)` で数え、`contains` と分けて出してください（クエリ13・17）。
- **町字側のブリッジ（`bridge_address_*`）の `lg_code` は NULL です。** 町字の市区町村は
  `address.lg_code` から `address_id` で引きます（クエリ18）。
- **`address_crosswalk` ビューに `DISTINCT` や結合を重ねると遅くなります。** ビューは旧郵便番号の
  集約を中で作っているためで、郵便番号での単純な絞り込み（クエリ01）は数秒ですが、駅の表と
  結合して `DISTINCT` をかけた形は数分かかりました。市区町村を引くだけならブリッジと `address`
  を直接結ぶほうが速いです。
- **時点がそろっていません。** 境界は2020年、駅は N02 2025年版、バス停は P11 令和4年度版
  （2022年度）、人口は2025年10月1日です。比率を出すときはこのずれを前提にしてください。

## 対応が付かなかったレコードの行き先

削除していません。

```sql
SELECT kind, COUNT(*) FROM unmatched_records GROUP BY kind;
```

対応率は、行を消すことによっては決して改善されません。

---

これらの `.sql` は `tools/verify_artifacts_agree.py` が毎ビルド実際に実行して検証しています。
ビューの列が変わればここが失敗するので、記載が黙って腐ることはありません。
