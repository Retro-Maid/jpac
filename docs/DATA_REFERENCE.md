# jpac データリファレンス（v2.3.0）

jp-address-crosswalk（jpac）のリリースデータを、作った人ではない人が読み解き、データ基盤（Snowflake など）に
取り込んで使うための資料です。この1冊で、ファイルの中身、型、キー、テーブル同士のつなぎ方が分かるように
書いてあります。リポジトリ内のほかの文書へのリンクは「詳しくは」の案内であり、読まなくても使えます。

- 対象: リリース `v2.3.0+data-2026-10-15`（GitHub Releases で配布）
- 数値は特に断りがない限り、このリリースの SQLite（`jp_address_crosswalk.sqlite`）を実際に数えた値です。
  版が変われば変わります。
- 本書の SQL はすべて、このリリースの SQLite に対して実行して動くことを確かめています。

## 目次

1. [はじめに](#1-はじめに)
2. [配布ファイル](#2-配布ファイル)
3. [データ型の約束](#3-データ型の約束)
4. [識別子（コード）の一覧](#4-識別子コードの一覧)
5. [テーブル構成の全体像](#5-テーブル構成の全体像)
6. [結合のしかた](#6-結合のしかた)
7. [テーブル定義](#7-テーブル定義)
   - [7.1 住所と市区町村](#71-住所と市区町村)
   - [7.2 郵便番号](#72-郵便番号)
   - [7.3 国土交通省 位置参照情報](#73-国土交通省-位置参照情報)
   - [7.4 市外局番](#74-市外局番)
   - [7.5 ブリッジ7本（共通30列）](#75-ブリッジ7本共通30列)
   - [7.6 駅 路線 バス停 e-Stat 小地域](#76-駅-路線-バス停-e-stat-小地域)
   - [7.7 人口（国勢調査 令和7年）](#77-人口国勢調査-令和7年)
   - [7.8 市区町村の廃置分合](#78-市区町村の廃置分合)
   - [7.9 学校（文部科学省 学校コード）](#79-学校文部科学省-学校コード)
   - [7.10 来歴](#710-来歴)
   - [7.11 ビュー](#711-ビュー)
8. [よくある落とし穴](#8-よくある落とし穴)
9. [出典とライセンス](#9-出典とライセンス)
10. [付録 用語集](#10-付録-用語集)

---

## 1. はじめに

### 1.1 これは何か

jpac は、日本の「住所」に関する複数の公的なコード体系を互いに対応づけたデータ群です。国の機関などが自ら
公開しているデータだけから作られています。

| 体系 | 発行元 | jpac での役割 |
|---|---|---|
| 町字（「西新宿一丁目」の単位）と市区町村 | デジタル庁 アドレス・ベース・レジストリ（ABR） | 住所の基準（正準） |
| 郵便番号（現行7桁・旧3/5桁） | 日本郵便 | 対応先 |
| 大字・町丁目コードと代表点の緯度経度 | 国土交通省 位置参照情報 | 対応先 |
| 番号区画・市外局番・市内局番 | 総務省 | 対応先 |
| 駅・路線（N02）、バス停留所（P11） | 国土交通省 国土数値情報 | 市区町村に対応づけ |
| 人口・世帯（令和7年国勢調査）、廃置分合等情報、小地域境界 | 総務省統計局（e-Stat） | 市区町村に対応づけ |
| 学校コード | 文部科学省 | 町字・市区町村に対応づけ |

1枚の表ではなく、**47 テーブル・3 ビュー・36 索引**からなる関係データベースです。中心に ABR の町字があり、
そこから各体系へ「ブリッジ」と呼ぶ対応表が伸びています。対応そのものと一緒に、その根拠（どの規則で、
どれくらい確かか、候補はいくつあったか）も持っています。

### 1.2 版

| 項目 | 値 |
|---|---|
| リリースタグ | `v2.3.0+data-2026-10-15` |
| コード版 | `2.3.0`（`match_run.code_version`） |
| データ版 | `data-2026-10-15`。元データを取得した日のうち最も新しい日で、ビルド日ではありません |
| 照合規則の版 | `2.0.0`（各ブリッジ行の `matching_rule_version`） |
| 正規化プロファイルの版 | `1.0.0`（各ブリッジ行の `normalization_profile_version`） |

利用時は**タグ全体**（`v2.3.0+data-2026-10-15`）を記録してください。同じコードでも元データの版が違えば
中身が変わります。

### 1.3 粒度の上限

| 対象 | どこまで細かいか |
|---|---|
| 住所 ↔ 郵便番号 ／ 国土交通省コード | **町字**（例「西新宿一丁目」）まで。番地・号は持ちません |
| 住所 ↔ 市外局番 | **市区町村**まで。町字単位の対応は総務省が公表していないため、町字単位の行はすべて「未解決」です |
| 駅・路線・バス停 | **市区町村**まで。町字（`address_id`）には結びません |
| 学校 | 所在地の文字列が決める**町字**まで。決まらなければ市区町村まで。座標は座標アセットにだけあります |
| 人口 | **市区町村**まで（番号区画は、市区町村を丸ごと含む区画だけ） |
| 廃置分合 | 市区町村コードの旧→新 |

座標について: 本体（SQLite・Parquet・CSV・テーブル別 zip）に入っている座標は国土交通省の
`mlit_town_version.latitude` / `longitude` だけで、これは **大字・町丁目全体の代表点**です。建物の位置では
ありません。駅・バス停・学校の座標は本体に入れず、別ファイルの**座標アセット**
`jp_address_crosswalk_points.zip` で配ります（[7.6 の末尾](#station_point座標アセット)）。境界の形状はどちらにも
入っていません（意図的な方針です）。

### 1.4 使う人に影響する原則

1. **曖昧さを残す。** 候補が複数あるときに1つに決めません。候補ごとに行があり、同じ候補群の行は
   `candidate_group_id` を共有し、`candidate_count` に候補数が入ります。1件に絞りたいときは「先頭の行」
   ではなく根拠の列（`verification_status = 'auto'` など）で絞ってください。
2. **対応しなかった行を消さない。** 相手が見つからなかった行も `relation_type = 'unresolved'` として残り、
   相手側の列が NULL になります。「探したが無かった」という記録です。
3. **コードは文字列。** `lg_code`、郵便番号などのコードはすべて TEXT です。先頭のゼロ（例 `013100`、`0640941`）
   を保つためで、数値に変換すると別のコードになります。
4. **`confidence` は確率ではない。** どの規則で対応が付いたかを 0〜1 の数値で表した順位づけです。平均したり
   掛け合わせたりしても意味を持ちません。
5. **発行元のデータを書き換えない。** 元の文字列は `*_raw` などの列にそのまま残し、正規化した値は別の列に
   持ちます。発行元どうしの表記の食い違いもそのまま残ります。

---

## 2. 配布ファイル

GitHub Releases の `v2.3.0+data-2026-10-15` に添付されている12ファイルがすべてです（リポジトリを clone
しても入りません）。

| ファイル | 形式 | サイズ（バイト） | 中身 |
|---|---|---:|---|
| `jp_address_crosswalk.parquet` | Parquet（zstd 圧縮） | 35,178,248 | フラット表 727,110 行 × 43 列。SQLite のビュー `address_crosswalk` と同じ内容 |
| `jp_address_crosswalk.csv.gz` | CSV（gzip） | 41,286,940 | 上と同じフラット表 |
| `jp_address_crosswalk.sqlite.gz` | SQLite（gzip） | 432,566,146 | 47 テーブル + 3 ビュー + 36 索引。展開後 2,271,760,384 バイト |
| `jp_address_crosswalk_tables.zip` | zip（無圧縮）に Parquet 47 本 | 180,618,671 | テーブル別 Parquet。`<テーブル名>.parquet` が 47 本。中身は SQLite の各テーブルと同じ行・列 |
| `jp_address_crosswalk_points.zip` | zip（無圧縮）に Parquet 3 本 | 7,275,360 | **座標アセット**（v2.2.0 で追加、学校は v2.3.0 で追加）。`station_point.parquet`（駅の代表点 9,046 行）、`bus_stop_point.parquet`（バス停の点 278,515 行）、`school_point.parquet`（学校の点 56,807 行）。SQLite・テーブル別 zip には入っていない |
| `QUALITY_REPORT.md` / `quality_report.json` | Markdown / JSON | 37,903 / 133,257 | そのビルドの統計と品質チェックの結果 |
| `DIFF_REPORT.md` / `diff_report.json` | Markdown / JSON | 1,661 / 1,946 | 前回リリースからの変化 |
| `SOURCES.yml` | YAML | 143,033 | 使った元データ1ファイルごとの URL・SHA-256・規約・行数 |
| `NOTICE.md` | Markdown | 35,051 | 出典表示（再配布するときに必要。[9章](#9-出典とライセンス)） |
| `SHA256SUMS` | テキスト | 970 | 上の11ファイルの SHA-256。`sha256sum -c SHA256SUMS` で検証できます。`.sqlite.gz` は**圧縮後**のハッシュです |

座標アセットは v2.2.0 から添付しています。v2.1.0 以前のリリースにはありません。`school_point` は v2.3.0 からです。

### 2.1 どれを使うか

| 用途 | 使うファイル |
|---|---|
| 住所ごとに郵便番号・国土交通省コードを並べた1枚の表がほしい | `jp_address_crosswalk.parquet`（または `.csv.gz`） |
| テーブル間の関係を SQL でたどりたい、棄却済みも含む全量（`address_crosswalk_all`）を見たい | `jp_address_crosswalk.sqlite.gz` を展開して使う |
| 人口・駅・路線・バス停・廃置分合など、フラット表に入らないテーブルを SQLite なしで読みたい／DWH に全テーブルを取り込みたい | `jp_address_crosswalk_tables.zip` |
| 駅・バス停を地図に載せたい（経緯度がほしい） | `jp_address_crosswalk_points.zip`（`n02_group_code` / `p11_stop_id` で本体の表に結ぶ） |

- **フラット表（Parquet / CSV）に入るのは、住所・郵便番号・国土交通省・市外局番の4系統だけ**です。駅・路線・
  バス停・小地域・人口・廃置分合のテーブルは入りません。
- フラット表は「現在有効で、人手で棄却されていない対応」だけです（SQLite の `address_crosswalk` と同じ）。
  棄却済みや過去の版も含む `address_crosswalk_all` は SQLite にしかありません。ただし v2.3.0 では棄却も退役も
  まだ発生していないため、両ビューの行数は同じ（727,110 行）です。
- Snowflake に全テーブルを取り込むなら、テーブル別 Parquet が最も扱いやすい形です（型が保たれ、1ファイル =
  1テーブル）。

### 2.2 CSV の書式

- 文字コード UTF-8（BOM なし）、区切りはカンマ、改行は LF、1行目がヘッダ。
- NULL は**空欄**（`,,`）です。空文字列と NULL の区別はありません。Snowflake の `COPY INTO` では
  `EMPTY_FIELD_AS_NULL = TRUE`（既定）のままにしてください。
- 真偽値は `true` / `false` の文字列です。
- **すべての列を文字列として読み込んでから型を付けてください。** 型推論に任せると `0640941` が `640941`
  になります（pandas なら `dtype=str`、polars なら `infer_schema_length=0`）。

---

## 3. データ型の約束

### 3.1 原則

| 種類 | SQLite | Parquet | 例 |
|---|---|---|---|
| コード・ID・名称 | `TEXT` | `large_string` | `lg_code`、`postal_code`、`address_id` |
| 日付 | `TEXT`（`YYYY-MM-DD`） | `large_string` | `valid_from`、`effective_date` |
| 日時 | `TEXT`（RFC 3339、`+09:00` 付き） | `large_string` | `created_at`、`downloaded_at` |
| 件数・人数 | `INTEGER` | `int64` / `int32` / `uint32` | `candidate_count`、`population_total` |
| 真偽値 | `INTEGER`（0/1） | `bool`（一部例外あり、下記） | `is_current`、`is_unique_match` |
| 確からしさ・座標・平均年齢 | `REAL` | `double` | `confidence`、`latitude`、`mean_age` |

**コード列は決して数値に変換しないでください。** `lg_code`、`jis_city_code`、`machiaza_id`、`postal_code`、
`old_postal_code`、`mlit_code`、`numbering_area_code`、`area_code`、`local_code`、`number`、`pref_code`、
`n02_group_code`、`key_code` などは、数字だけでできていても識別子です。先頭のゼロが落ちると別のコードになり、
結合が静かに外れます。数値として比較・ソートする必要もありません。

### 3.2 型の対応表（SQLite → Parquet → Snowflake 推奨）

| SQLite の型 | Parquet（Arrow）の型 | Snowflake 推奨 | 該当する列 |
|---|---|---|---|
| `TEXT` | `large_string` | `VARCHAR` | 下の日付・日時以外のすべての TEXT 列 |
| `TEXT`（`YYYY-MM-DD`） | `large_string` | `DATE` | 3.3 の表の「DATE」の列 |
| `TEXT`（RFC 3339） | `large_string` | `TIMESTAMP_TZ` | `created_at` / `updated_at`（ブリッジ7本）、`match_run.started_at`、`source_snapshot.downloaded_at` |
| `INTEGER`（件数） | `int64` / `int32` / `uint32` | `NUMBER(38,0)` | `candidate_count`、`population*`、`households*`、`census_year`、`feature_count` など |
| `INTEGER`（0/1） | `bool` | `BOOLEAN` | `is_current`、`is_unique_match`、`candidate_count_is_complete`、`override_stale` |
| `INTEGER`（0/1） | `int64` | `BOOLEAN`（取り込み時に変換）または `NUMBER(1,0)` | `bridge_station_municipality.is_unique_match`、`bridge_bus_stop_municipality.is_unique_match` |
| `REAL` | `double` | `FLOAT` | `confidence`、`latitude` / `longitude`、`mean_age` / `median_age` |
| `TEXT`（全行 NULL） | `null` | `VARCHAR`（明示して宣言） | `source_snapshot.license_text_sha256` / `etag` / `last_modified` |

注意点:

- `uint32` の列: `address.rsdt_variant_count`、`postal_code_entity.record_count`、`n02_station.feature_count` /
  `station_name_variants` / `line_variants` / `operator_variants`、`n02_railroad_line.section_count` /
  `railway_class_variants` / `operator_class_variants`、`bridge_station_line.feature_count`、
  `census_population_numbering_area.municipality_count`。`int32` は `census_year`（人口4表）。いずれも
  `NUMBER(38,0)` で受けられます。
- Parquet の `null` 型の3列は、全行が NULL のため型が付いていません。スキーマ推論（`INFER_SCHEMA`）に任せず、
  `VARCHAR` と明示してください。
- 0 行のテーブル（`address_history`、`address_lineage`、`address_key_conflict`）は、Parquet では全列が
  `large_string` です。SQLite の宣言型も全列 `TEXT` です。
- `0`/`1` の**文字列**で持っている列（`address.rsdt_addr_flg`、`postal_record_version.flag_*` など）は、
  発行元のコード値をそのまま持っているものです。真偽値ではなく `VARCHAR` のまま扱ってください。
- **NOT NULL 制約はどの列にも宣言されていません。** NULL がありうるかどうかは、7章の各列の「NULL」欄
  （実測）で判断してください。

### 3.3 日付・日時の列

実測した書式です。日付の列はすべて10文字の `YYYY-MM-DD` で、それ以外の値はありません（NULL を除く）。

| 列 | 書式 | Snowflake 推奨 |
|---|---|---|
| `valid_from` / `valid_to`（全テーブル） | `YYYY-MM-DD` または NULL | `DATE` |
| `observed_from` / `observed_to`（全テーブル） | `YYYY-MM-DD` または NULL | `DATE` |
| `address_entity.created_at` / `retired_at` | `YYYY-MM-DD` または NULL | `DATE` |
| `address_rsdt_variant.efct_date` / `ablt_date` | `YYYY-MM-DD` または NULL | `DATE` |
| `municipality_lineage_edge.effective_date` | `YYYY-MM-DD` | `DATE` |
| `telephone_area_version.current_as_of`、`telephone_number_block.current_as_of` | `YYYY-MM-DD` | `DATE` |
| `snapshot_license_artifact.reviewed_on`、`source_snapshot.published_at` | `YYYY-MM-DD` または NULL | `DATE` |
| `address_history.valid_from` / `observed_at`、`address_lineage.effective_date` / `observed_at`、`address_key_conflict.efct_date` / `ablt_date` | 0 行のため実測なし。設計上は `YYYY-MM-DD` | `DATE`（または `VARCHAR`） |
| ブリッジ7本の `created_at` / `updated_at`、`match_run.started_at`、`source_snapshot.downloaded_at` | `2026-10-12T20:47:44+09:00` の形（RFC 3339、時差付き） | `TIMESTAMP_TZ` |
| `municipality_change_raw.effective_date_raw` / `notice_date_raw` | 発行元の文字列。`notice_date_raw` は32行が空文字列 | `VARCHAR`（`*_raw` は変換しない） |
| `mlit_town_version.fiscal_year` | `2025` のような年度（4文字） | `VARCHAR` |

時間の列は3種類あり、意味が違います。

| 列 | 意味 |
|---|---|
| `valid_from` / `valid_to` | 実世界で有効な期間。**元データに明記されているときだけ**入ります。無ければ NULL（取得日で代用しません） |
| `observed_from` / `observed_to` | jpac がその行を最初に／最後に観測した日。`observed_to` が NULL なら「現在も観測されている」 |
| `downloaded_at` / `published_at` | 元データの取得日時と、発行元が述べた公表日 |

### 3.4 数値の列

- `confidence` は `REAL`。値は規則ごとに決まった少数の値（`1.0`、`0.99`、`0.97`、`0.95`、`0.9`、`0.7`、
  `0.5`、`0.0`）だけをとります。`FLOAT` で受けて等値比較しても問題ありませんが、意味は「規則の順位」です。
- 緯度経度は `REAL`（世界測地系の度）。`mlit_town_version` にだけあり、CHECK で緯度 20〜46・経度 122〜154 の
  範囲に制約されています。
- 人口・世帯数は `INTEGER`。公表値そのもので、按分や推計をした値は1つもありません。

---

## 4. 識別子（コード）の一覧

| 識別子 | 形式・例 | 何を指すか | 発番元 | 安定性 |
|---|---|---|---|---|
| `address_id` | `jpa1` + 16文字（計20文字）。例 `jpa1dy9jn5hy738r9wnb` | 町字1件（jpac の住所の永続 ID） | jpac | 同じ町字である限りリリースをまたいで変わらず、別の町字に使い回されることもありません。初めて観測したときの `lg_code` と `machiaza_id` から作り、台帳で引き継ぎます（町字 ID の訂正や改称では変わらない）。ただし**町字の分割・統合では新しい ID が振られ**、署名済みの承継記録が無い廃置分合で市区町村コードが変わった町字も、古い ID が退役して新しい ID になります。旧 ID から新 ID へは `address_lineage` でたどります（v2.3.0 では 0 行）。値に意味はありません（市区町村を読み取らないでください） |
| `lg_code` | 6桁（5桁 + 検査数字）。例 `131041`（新宿区） | 市区町村（政令市の親・区、特別区を含む） | デジタル庁 ABR（全国地方公共団体コード） | 公的コード。廃置分合で変わることがあります |
| `jis_city_code` | 5桁。例 `13104` | 同上（検査数字なし） | 同上 | `lg_code` の先頭5桁と常に一致します。日本郵便・国土交通省・e-Stat は5桁で持つため、それらとの結合に使います |
| `machiaza_id` | 7桁。例 `0023001` | ABR の町字 ID | デジタル庁 ABR | **全国では一意ではありません**。一意なのは `(lg_code, machiaza_id)` の組です |
| `postal_code` | 7桁。例 `1600023` | 現行の郵便番号 | 日本郵便 | 公的コード |
| `old_postal_code` | 3桁または5桁。例 `160`、`02304` | 1998年以前の旧郵便番号 | 日本郵便 | `old_postal_code_raw` は右側に空白を含む元の5文字（例 `160  `） |
| `postal_record_id` | `pr_` + 24桁の16進。例 `pr_00005b96b903a70b51e3c8af` | 日本郵便 `ken_all` の1行 | jpac | 行の内容から作る ID。日本郵便が行を1文字でも変えると別の ID になります |
| `mlit_code` | 12桁。例 `131040023001` | 国土交通省の大字・町丁目コード | 国土交通省 | 公的コード。先頭5桁は市区町村コード、後ろ7桁は町字 ID に似ていますが、それだけでは対応の根拠になりません |
| `mlit_record_id` | `ml_` + 24桁の16進 | 国土交通省 位置参照情報の1行 | jpac | 行の内容から作る ID。国土交通省が行の内容を変えると別の ID になります |
| `numbering_area_code` | `NNN` または `NNN-N`。例 `001`、`309-2` | 総務省の番号区画（市外局番の区切りの単位） | 総務省 | 公的コード |
| `area_code` | 例 `03`、`011`、`0123` | 市外局番 | 総務省 | 1つの市外局番に複数の番号区画がぶら下がることがあります（582区画に対し市外局番387種） |
| `local_code` / `number` | 例 `200` / `011200` | 市内局番 ／ 市外局番 + 市内局番 | 総務省 | 公的な番号 |
| `n02_group_code` | 6桁。例 `000001` | N02 の駅グループ（jpac の「駅」1件） | 国土交通省 国土数値情報 | 版（2025年版）の中で一意 |
| `(line_name_raw, operator_name_raw)` | 例 `('10号線新宿線', '東京都')` | 路線1本 | 国土交通省 国土数値情報 | 複合キー。路線にはコードが無いため発行元の名称の組で識別します |
| `p11_stop_id` | `p11-22-{都道府県2桁}-{連番6桁}`。例 `p11-22-13-000042` | バス停留所データの**1行** | jpac | **行の代理キーで、実体の同一性ではありません**。発行元の県内の行順から振るため、同じ版（令和4年度版）の中でだけ安定です |
| `key_code` | 例 `011010200` | e-Stat 小地域（町丁・字等）1件 | 総務省統計局 | 2020年国勢調査の境界の版 |
| `region_key` | `{地域識別コード}:{2000年の地域コード}:{2025年の地域コード}`。例 `0:01101:01101`、`a:00000:00000` | 国勢調査の結果表の地域1件 | jpac | 結果表の行を一意にするための合成キー |
| `edge_id` | `mle_` + 24桁の16進 | 廃置分合の旧→新の辺1本 | jpac | 辺を決める列から作る決定的な ID |
| `row_ordinal` | 整数 | 発行元ファイルの行番号（`municipality_change_raw`、`census_population_raw`） | jpac | 同じ取得断面の中でだけ意味を持ちます |
| `bridge_id` | `brg_` + 24桁の16進 | ブリッジの1行 | jpac | 行の意味（キー）のハッシュなので、同じ入力からは同じ値になります |
| `candidate_group_id` | `grp_` + 20桁の16進 | 同じ候補群に属するブリッジ行の束 | jpac | 候補が2件以上のときだけ入ります |
| `*_version_id` | `ver_` + 24桁の16進 | `*_version` テーブルの1版 | jpac | 内容と観測期間から作る ID |
| `coverage_id` / `block_id` | `cov_…` ／ `blk_{市外局番}_{市内局番}` | 番号区画の収容句1件 ／ 市内局番1件 | jpac | `coverage_id` は句の内容から作る ID で、総務省が文を変えると別の ID になります。`block_id` は番号そのものから作ります |
| `rsdt_variant_id` | `rv_{lg_code}_{machiaza_id}_…` | 住居表示の版1件 | jpac | 行を識別する ID |
| `source_snapshot_id` | `snap_…` または `snapset_…` | 取得した元データ1ファイル（`snapset_` は複数ファイルの束） | jpac | 来歴。7.10 を参照 |
| `match_run_id` | `run_` + 20桁の16進 | 照合の実行1回 | jpac | v2.3.0 では1件だけ |

**リリースをまたいで保存してよいキー**は、`address_id`（上の退役の規則つき）、公的なコード（`lg_code`、`jis_city_code`、`(lg_code, machiaza_id)`、`postal_code`、`mlit_code`、`numbering_area_code`、`area_code`）、版の中での `n02_group_code` と `(line_name_raw, operator_name_raw)` です。`bridge_id`・`*_version_id`・`postal_record_id`・`mlit_record_id`・`p11_stop_id` など jpac が行から作る ID は、元データが変われば変わるものとして扱ってください。

**町名は識別子になりません。** 「西新宿」は東京都新宿区のほかにも全国にあります。住所を特定するときは
`address_id` か `(lg_code, machiaza_id)` を使ってください。

---

## 5. テーブル構成の全体像

![jpac v2.3.0 の全体図。47テーブルと座標アセットの3表を領域ごとに並べ、参照の向きを矢印で示す](diagrams/er/00-overview.svg)

全列を載せた ER 図は [`docs/diagrams/er/jpac-er.pdf`](diagrams/er/jpac-er.pdf)（9ページ: 全体図と、領域ごとの
8ページ。8ページ目が座標アセット、9ページ目が学校）です。図の**実線は宣言された `FOREIGN KEY`、点線は宣言のない論理的な結合**です。

| 領域 | テーブル | 行数 | 1行が表すもの |
|---|---|---:|---|
| 住所・市区町村 | [`address`](#address) | 726,170 | 現在有効な町字1件（ABR 町字マスターの1町字） |
| 住所・市区町村 | [`address_entity`](#address_entity) | 726,170 | jpac が一度でも観測した町字1件（ID 台帳）。退役しても行は消えない |
| 住所・市区町村 | [`address_code`](#address_code) | 2,178,510 | 住所1件 × コードの種類 × コードの値 × 観測開始日（コードの履歴） |
| 住所・市区町村 | [`address_rsdt_variant`](#address_rsdt_variant) | 727,418 | 町字1件 × 住居表示の属性の組（ABR 町字マスターで住居表示フラグ・日付だけが違う重複行を、そのまま残したもの） |
| 住所・市区町村 | [`address_history`](#address_history) | 0 | 住所の属性が1つ変わった記録1件 |
| 住所・市区町村 | [`address_lineage`](#address_lineage) | 0 | 町字の分割・統合・改称・コード訂正・退役などの出来事1件（旧 ID → 新 ID） |
| 住所・市区町村 | [`address_key_conflict`](#address_key_conflict) | 0 | ABR 町字マスターでキー `(lg_code, machiaza_id)` が重複し、住居表示以外の列まで食い違っていた元の行1件 |
| 住所・市区町村 | [`municipality`](#municipality) | 1,918 | 市区町村1件（政令市の親〔市全体〕20件と区171件、東京23区を含む 1,918件） |
| 住所・市区町村 | [`municipality_version`](#municipality_version) | 1,918 | 市区町村1件の名前などの版1つ |
| 郵便番号 | [`postal_code_entity`](#postal_code_entity) | 120,682 | 7桁の郵便番号1つ |
| 郵便番号 | [`postal_record`](#postal_record) | 124,513 | 日本郵便 `ken_all`（住所の郵便番号）の1行 |
| 郵便番号 | [`postal_record_version`](#postal_record_version) | 124,513 | 郵便レコード1行の版1つ |
| 郵便番号 | [`bridge_address_postal_code`](#bridge_address_postal_code) | 676,124 | 町字1件 × 郵便番号1つの対応（デジタル庁「ABR 町字・郵便番号変換表」による公式の対応） |
| 郵便番号 | [`bridge_address_postal`](#bridge_address_postal) | 352,499 | 町字1件 × 日本郵便のレコード1行の対応（名前の照合による） |
| 郵便番号 | [`bridge_municipality_postal_code`](#bridge_municipality_postal_code) | 8,207 | 市区町村1件 × 郵便番号1つ（ABR 変換表のうち町字 ID が空の、市区町村単位の行） |
| 郵便番号 | [`bridge_municipality_postal`](#bridge_municipality_postal) | 1,910 | 市区町村1件 × 日本郵便の特殊レコード1行（「以下に掲載がない場合」「〜一円」「〜の次に番地がくる場合」） |
| 国土交通省 | [`mlit_town`](#mlit_town) | 191,106 | 国土交通省 位置参照情報（大字・町丁目レベル）の1行 |
| 国土交通省 | [`mlit_town_version`](#mlit_town_version) | 191,106 | 国交省レコード1行の版1つ |
| 国土交通省 | [`bridge_address_mlit`](#bridge_address_mlit) | 733,948 | 町字1件 × 国交省レコード1行の対応 |
| 市外局番 | [`telephone_area`](#telephone_area) | 582 | 総務省の番号区画1つ |
| 市外局番 | [`telephone_area_version`](#telephone_area_version) | 582 | 番号区画1つの版1つ |
| 市外局番 | [`telephone_area_coverage`](#telephone_area_coverage) | 1,640 | 番号区画の対象地域の文を、句（市区町村や郡ごと）に分けた1件 |
| 市外局番 | [`telephone_number_block`](#telephone_number_block) | 41,987 | 市外局番 + 市内局番の割り当て1件（総務省「電気通信番号指定状況（固定電話等）」） |
| 市外局番 | [`bridge_address_telephone`](#bridge_address_telephone) | 726,170 | 町字1件につき1行（全行が未解決） |
| 市外局番 | [`bridge_municipality_telephone`](#bridge_municipality_telephone) | 2,153 | 番号区画1つ × 市区町村1件（総務省「市外局番の一覧」の対象地域の文から） |
| 駅・路線・バス・小地域 | [`n02_station`](#n02_station) | 9,046 | 駅1つ（国土数値情報 鉄道データ N02 2025年版の「駅グループ」1つ） |
| 駅・路線・バス・小地域 | [`n02_railroad_line`](#n02_railroad_line) | 596 | 路線1本（N02 の `(路線名, 運営会社)` の組） |
| 駅・路線・バス・小地域 | [`bridge_station_line`](#bridge_station_line) | 10,153 | 駅1つ × 路線1本（N02 の駅の地物が自ら持つ路線名の属性による） |
| 駅・路線・バス・小地域 | [`bridge_station_municipality`](#bridge_station_municipality) | 9,061 | 駅1つ × 候補の市区町村1件（候補が複数なら候補ごとに1行） |
| 駅・路線・バス・小地域 | [`bridge_line_municipality`](#bridge_line_municipality) | 3,381 | 路線1本 × 通る市区町村1件 |
| 駅・路線・バス・小地域 | [`p11_bus_stop`](#p11_bus_stop) | 278,515 | バス停留所データ（国土数値情報 P11 令和4年度版）の1行 |
| 駅・路線・バス・小地域 | [`bridge_bus_stop_municipality`](#bridge_bus_stop_municipality) | 278,801 | バス停1行 × 候補の市区町村1件 |
| 駅・路線・バス・小地域 | [`estat_small_area`](#estat_small_area) | 232,019 | e-Stat 令和2年国勢調査 小地域（町丁・字等）境界データの1ポリゴン分の属性（**形状は含まない**） |
| 人口 | [`census_population_municipality`](#census_population_municipality) | 1,918 | 国勢調査の回 × 市区町村1件（令和7年 = 2025年10月1日現在） |
| 人口 | [`census_population_age`](#census_population_age) | 538,824 | 地域1つ × 国籍（総数／日本人）× 男女 × 5歳階級1つ（第2-7表の縦持ち） |
| 人口 | [`census_population_age_summary`](#census_population_age_summary) | 24,492 | 地域1つ × 国籍 × 男女（第2-7表の平均年齢・年齢中位数） |
| 人口 | [`census_population_numbering_area`](#census_population_numbering_area) | 582 | 国勢調査の回 × 番号区画1つ |
| 人口 | [`census_population_raw`](#census_population_raw) | 4,082 | 第1-1表の1行（全国・都道府県・2000年境域の行を含む 4,082行） |
| 廃置分合 | [`municipality_change_raw`](#municipality_change_raw) | 3,739 | e-Stat「廃置分合等情報」CSV（1970-04-20〜2024-01-01 施行分、境界変更を除く）の1行 |
| 廃置分合 | [`municipality_lineage_edge`](#municipality_lineage_edge) | 2,528 | 廃置分合の旧コード → 新コードの辺1本（施行日ごと） |
| 来歴 | [`source_snapshot`](#source_snapshot) | 126 | 取り込んだ元データのファイル1本（または複数ファイルの束） |
| 来歴 | [`snapshot_license_artifact`](#snapshot_license_artifact) | 24 | 元データ1種類に関わる規約文書1つ（規約本文、詳細ページ、ダウンロード時の同意事項など） |
| 来歴 | [`match_run`](#match_run) | 1 | 照合の実行1回 |
| 来歴 | [`match_run_input`](#match_run_input) | 122 | 照合の実行1回 × 読んだ元データ1つ |
| 座標（別アセット） | [`station_point`](#station_point座標アセット) | 9,046 | 駅1つの代表点（`jp_address_crosswalk_points.zip`。SQLite には無い） |
| 座標（別アセット） | [`bus_stop_point`](#bus_stop_point座標アセット) | 278,515 | バス停1行の点（同上。発行元の値のまま） |

テーブルの形には次の決まりがあります。

- **実体と版。** `municipality` / `postal_record` / `mlit_town` / `telephone_area` は ID だけを持つ薄い表で、
  名前などの属性は `*_version` 表にあります。属性が変わったら上書きせず版を足し、古い版は `is_current = 0`
  になります。v2.3.0 ではどの実体もまだ版が1つしかなく、全行が `is_current = 1` です。
- **ブリッジ。** 対応関係そのものと根拠を持つ表です。名前が `bridge_` で始まります。
- **raw 表。** `*_raw` で終わる表・列は、発行元のファイルの行やセルを文字列のまま保持したものです。

---

## 6. 結合のしかた

### 6.1 結合の一覧

「宣言」は SQLite に `FOREIGN KEY` が宣言されているか（○）、宣言のない論理的な結合か（—）です。宣言された外部キーは
ビルド時に `PRAGMA foreign_key_check` で全件検査されています（利用者の接続で強制したいときは
`PRAGMA foreign_keys = ON`。SQLite の既定は OFF です）。多重度は「左 : 右」で、数値は v2.3.0 の実測です。

**住所と市区町村**

| 左（参照する側） | 右（参照される側） | 多重度 | 宣言 | 注意 |
|---|---|---|---|---|
| `address.address_id` | `address_entity.address_id` | 1 : 1 | — | 同じ ID。v2.3.0 では両方 726,170 行。退役した住所は `address_entity` にだけ残る |
| `address.lg_code` | `municipality.lg_code` | N : 1 | ○ | 政令市の住所は区のコード。親（市全体）のコードを持つ住所は 0 件 |
| `address_rsdt_variant.(lg_code, machiaza_id)` | `address.(lg_code, machiaza_id)` | 1〜2 : 1 | — | 1,248 件の住所が2行を持つ（`address.rsdt_variant_count = 2`）。`address_rsdt_variant.lg_code` → `municipality` は宣言 FK |
| `address_code.address_id` | `address_entity.address_id` | N : 1 | ○ | v2.3.0 は1住所あたり3行（`abr_lg_code` / `abr_machiaza_id` / `jis_city_code`） |
| `address_history.address_id` | `address_entity.address_id` | N : 1 | ○ | 0 行 |
| `address_lineage.old_address_id` / `new_address_id` | `address_entity.address_id` | N : 1 | ○ | 0 行 |
| `address_key_conflict.lg_code` | `municipality.lg_code` | N : 1 | ○ | 0 行 |
| `municipality_version.lg_code` | `municipality.lg_code` | N : 1 | — | **`is_current = 1` で絞ると 1 : 1**。市区町村名を引くときは必ずこの条件を付ける |

**郵便番号**

| 左 | 右 | 多重度 | 宣言 | 注意 |
|---|---|---|---|---|
| `postal_record.postal_code` | `postal_code_entity.postal_code` | N : 1 | ○ | 1つの郵便番号を最大 66 行の `ken_all` レコードが持つ |
| `postal_record_version.postal_record_id` | `postal_record.postal_record_id` | N : 1 | — | `is_current = 1` で 1 : 1 |
| `bridge_address_postal_code.address_id` | `address_entity.address_id` | N : 1 | ○ | 住所 ↔ 郵便番号は多対多。1住所に複数の郵便番号、1郵便番号に複数の住所がある |
| `bridge_address_postal_code.postal_code` | `postal_code_entity.postal_code` | N : 1 | ○ | |
| `bridge_address_postal.address_id` | `address_entity.address_id` | N : 1 | ○ | 名前の照合による対応。全行 `review_required` |
| `bridge_address_postal.postal_record_id` | `postal_record.postal_record_id` | N : 1 | ○ | |
| `bridge_municipality_postal_code.lg_code` | `municipality.lg_code` | N : 1 | ○ | 規則 P2（ABR 変換表の市区町村単位の行）。郵便番号を直接指す |
| `bridge_municipality_postal_code.postal_code` | `postal_code_entity.postal_code` | N : 1 | ○ | |
| `bridge_municipality_postal.lg_code` | `municipality.lg_code` | N : 1 | ○ | 規則 P3（日本郵便の「以下に掲載がない場合」など）。郵便**レコード**を指す |
| `bridge_municipality_postal.postal_record_id` | `postal_record.postal_record_id` | N : 1 | ○ | 郵便番号は `postal_record.postal_code` から取る |

**国土交通省・市外局番**

| 左 | 右 | 多重度 | 宣言 | 注意 |
|---|---|---|---|---|
| `mlit_town_version.mlit_record_id` | `mlit_town.mlit_record_id` | N : 1 | — | `is_current = 1` で 1 : 1 |
| `bridge_address_mlit.address_id` | `address_entity.address_id` | N : 1 | ○ | |
| `bridge_address_mlit.mlit_record_id` | `mlit_town.mlit_record_id` | N : 1 | ○ | 名前・代表点は `mlit_town_version`（`is_current = 1`）へさらに結ぶ |
| `telephone_area_version.numbering_area_code` | `telephone_area.numbering_area_code` | N : 1 | — | `is_current = 1` で 1 : 1。市外局番（`area_code`）はここ |
| `telephone_area_coverage.numbering_area_code` | `telephone_area.numbering_area_code` | N : 1 | ○ | |
| `telephone_number_block.numbering_area_code` | `telephone_area.numbering_area_code` | N : 1 | ○ | |
| `bridge_municipality_telephone.lg_code` | `municipality.lg_code` | N : 1 | ○ | **市区町村 ↔ 市外局番の本来の対応**。1,912 市区町村 × 582 区画、2,153 行 |
| `bridge_municipality_telephone.numbering_area_code` | `telephone_area.numbering_area_code` | N : 1 | ○ | |
| `bridge_address_telephone.address_id` | `address_entity.address_id` | N : 1 | ○ | 全行 `unresolved`（町字単位の対応は無い）。使わない |
| `bridge_address_telephone.numbering_area_code` | `telephone_area.numbering_area_code` | N : 1 | ○ | 全行 NULL |

**駅・路線・バス停・小地域**

| 左 | 右 | 多重度 | 宣言 | 注意 |
|---|---|---|---|---|
| `bridge_station_municipality.n02_group_code` | `n02_station.n02_group_code` | N : 1 | ○ | 9,046 駅に 9,061 行（候補が2つの駅は2行） |
| `bridge_station_municipality.lg_code` | `municipality.lg_code` | N : 1 | ○ | 区・市町村のコードだけ。政令市の親コードには1行も無い |
| `bridge_station_line.n02_group_code` | `n02_station.n02_group_code` | N : 1 | ○ | 乗換駅は路線の数だけ行がある |
| `bridge_station_line.(line_name_raw, operator_name_raw)` | `n02_railroad_line.(line_name_raw, operator_name_raw)` | N : 1 | ○（複合） | |
| `bridge_line_municipality.(line_name_raw, operator_name_raw)` | `n02_railroad_line.(…)` | N : 1 | ○（複合） | 596 路線 × 1,446 市区町村、3,381 行 |
| `bridge_line_municipality.lg_code` | `municipality.lg_code` | N : 1 | ○ | |
| `bridge_bus_stop_municipality.p11_stop_id` | `p11_bus_stop.p11_stop_id` | N : 1 | ○ | 278,515 停留所に 278,801 行（候補が2つの停留所は2行） |
| `bridge_bus_stop_municipality.lg_code` | `municipality.lg_code` | N : 1 | ○ | 政令市の親コードには1行も無い |
| `station_point.n02_group_code`（座標アセット） | `n02_station.n02_group_code` | 1 : 1 | — | 別ファイルなので宣言できない。ビルドの `points_match_sources` で行数とキーの一致を検査 |
| `bus_stop_point.p11_stop_id`（座標アセット） | `p11_bus_stop.p11_stop_id` | 1 : 1 | — | 同上。`p11_stop_id` は版をまたいで保存しない（4章） |
| `estat_small_area.jis_city_code` | `municipality.jis_city_code` | N : 1（または 0） | — | **2020年の境界のコード**。231,263 行は一致、756 行は一致しない（`superseded` 686、`split_no_single_successor` 68、`unassigned_area` 2）。`reconcile_status = 'matched'` で絞る |

**人口・廃置分合**

| 左 | 右 | 多重度 | 宣言 | 注意 |
|---|---|---|---|---|
| `census_population_municipality.lg_code` | `municipality.lg_code` | 1 : 1（`census_year` ごと） | — | 1,918 件すべて一致。政令市は親（`area_level = '1'`）と区（`'0'`）の両方の行がある |
| `census_population_age.lg_code` / `census_population_age_summary.lg_code` | `municipality.lg_code` | N : 1（NULL あり） | ○ | 全国・都道府県・2000年境域の行は NULL |
| `census_population_age.(census_year, region_key, nationality_raw, sex_raw)` | `census_population_age_summary.(同じ4列)` | 22 : 1 | — | 年齢階級22区分（不詳を含む） : 平均年齢などの1行 |
| `census_population_raw.region_key` | `census_population_age_summary.region_key` | 1 : 6 | — | 第1-1表の1地域 : 第2-7表の 国籍2 × 男女3。4,082 地域すべて一致 |
| `census_population_numbering_area.numbering_area_code` | `telephone_area.numbering_area_code` | 1 : 1（`census_year` ごと） | — | 582 区画すべて一致 |
| `municipality_lineage_edge.new_lg_code` | `municipality.lg_code` | N : 1（または 0） | — | **新コードが現行の市区町村のときだけ**一致（2,528 本のうち 2,310 本）。それ以外は途中のコードなので、さらに辺をたどる |
| `municipality_lineage_edge.old_lg_code` | `municipality.lg_code` | N : 1（または 0） | — | 旧コードが今も現行なのは 287 本（存続・名称変更など） |
| `municipality_lineage_edge.source_row_ordinals` | `municipality_change_raw.row_ordinal` | N : M | — | `;` 区切りの行番号の並び（全辺で2つ以上）。文字列を分割して結ぶ |

**来歴**

| 左 | 右 | 多重度 | 宣言 | 注意 |
|---|---|---|---|---|
| 全ブリッジ7本の `match_run_id` | `match_run.match_run_id` | N : 1 | ○ | v2.3.0 は1件 |
| `match_run_input.match_run_id` | `match_run.match_run_id` | N : 1 | ○ | |
| `match_run_input.source_snapshot_id` | `source_snapshot.source_snapshot_id` | N : 1 | — | 118 行すべて存在 |
| `snapshot_license_artifact.source_snapshot_id` | `source_snapshot.source_snapshot_id` | N : 1 | — | 20 行すべて存在 |
| 各表の `source_snapshot_id` / `first_observed_snapshot_id` / `last_observed_snapshot_id` | `source_snapshot.source_snapshot_id` | N : 1 | — | 宣言しないのは意図的（過去のビルドから引き継いだ行が、今回の `source_snapshot` に無い元データを指しうるため）。v2.3.0 では全値が存在する |

### 6.2 結合するときの約束

1. **`*_version` 表は `is_current = 1` で絞る。** `municipality_version`、`postal_record_version`、`mlit_town_version`、
   `telephone_area_version` は版を積む表です。v2.3.0 では全行が現行ですが、将来の版で古い版が残ると、絞らない
   結合は行が増えます。
2. **ブリッジは「現行で、棄却されていない」行を使う。** `is_current = 1 AND verification_status <> 'manually_rejected'`。
   フラット表とビュー `address_crosswalk` はこの条件で作られています。さらに自動確定だけにしたいときは
   `verification_status = 'auto'`。
3. **ブリッジには向き（`direction`）がある。** 住所系ブリッジは、住所側から探した行（`address_to_…`）と、相手側から
   探して住所が見つからなかった行（`…_to_address`）が混ざっています。住所から引くときは `address_id` で結べば、
   相手側から来た未一致行（`address_id` が NULL）は自然に外れます。
4. **候補が複数の行は `candidate_group_id` を共有する。** `candidate_count > 1` の行は同じ問いへの複数の答えです。
   1件に決めたいときは先頭の行ではなく、`verification_status = 'auto'` か `is_unique_match = 1` で絞ってください
   （候補が複数の住所は結果に出なくなります。それが正しい答えです）。
5. **住所系ブリッジの市区町村は `address.lg_code` から取る。** 住所系4本の `lg_code` 列は全行 NULL です。
6. **政令市は「親」と「区」の両方のコードがある。** 市区町村マスターには政令市20市の親コード（例 `011002` 札幌市）
   と区のコード（例 `011011` 札幌市中央区）の両方があります。住所・駅・バス停・路線・郵便のブリッジは区のコード
   だけを使い、親コードには1行もありません。人口（`census_population_municipality`）は親と区の両方を持ちます。
   `bridge_municipality_telephone` は、総務省の文が政令市を丸ごと挙げる場合に親と区の両方に行を持ちます
   （21 行が親コード）。親から区を引くには `municipality_version` で同じ都道府県・市名の区を探します（8章）。
7. **市区町村 ↔ 郵便番号は2本に分かれている。** 規則 P2（郵便番号を直接指す）は `bridge_municipality_postal_code`、
   規則 P3（日本郵便の特殊レコードを指す）は `bridge_municipality_postal`。両方を見たいときは2本を
   `UNION ALL` します（8章）。
8. **市外局番は市区町村で引く。** `bridge_address_telephone` は全行が未解決（T10）で、`numbering_area_code` は
   全行 NULL です。住所から市外局番を引くときは `address.lg_code` → `bridge_municipality_telephone` →
   `telephone_area_version` とたどります。結果は「その市区町村に掛かる番号区画の候補」であり、町字ごとの
   正解ではありません（総務省がそこまで公表していないため）。

### 6.3 代表的な結合の例

住所に郵便番号・市区町村名を付ける（公式変換表、自動確定のみ）:

```sql
SELECT a.address_id, mv.pref, mv.city, mv.ward, a.full_name_raw, b.postal_code
FROM   bridge_address_postal_code b
JOIN   address a               ON a.address_id = b.address_id
JOIN   municipality_version mv ON mv.lg_code = a.lg_code AND mv.is_current = 1
WHERE  b.postal_code = '1600023'
  AND  b.is_current = 1
  AND  b.verification_status = 'auto';
```

住所の市区町村に掛かる市外局番の候補:

```sql
SELECT a.full_name_raw, t.area_code, b.numbering_area_code,
       b.relation_type, b.coverage_type, b.mismatch_note
FROM   address a
JOIN   bridge_municipality_telephone b ON b.lg_code = a.lg_code
JOIN   telephone_area_version t
       ON t.numbering_area_code = b.numbering_area_code AND t.is_current = 1
WHERE  a.address_id = 'jpa1dy9jn5hy738r9wnb'
  AND  b.verification_status <> 'manually_rejected';
```

住所に国交省コードと代表点を付ける:

```sql
SELECT a.address_id, a.full_name_raw, v.mlit_code, v.latitude, v.longitude,
       b.relation_type, b.verification_status
FROM   address a
JOIN   bridge_address_mlit b    ON b.address_id = a.address_id
JOIN   mlit_town_version v      ON v.mlit_record_id = b.mlit_record_id AND v.is_current = 1
WHERE  a.lg_code = '131041'
  AND  b.is_current = 1 AND b.verification_status <> 'manually_rejected';
```

---

## 7. テーブル定義

各表について、1行が表すもの・主キー・行数・用途と、全列の定義を載せます。

- 型は「SQLite の宣言型 / Parquet（テーブル別 zip）の Arrow 型」です。Snowflake の型は [3.2](#32-型の対応表sqlite--parquet--snowflake-推奨) を参照。
- 「NULL 行数」と「値」は v2.3.0 の実測です。値の種類が少ない列は全種類を件数付きで（多い順、8種まで）、多い列は例を示します。
- 「発行元のコード値」とある列は、発行元のファイルの値をそのまま持っています。コードの各値の意味が発行元の資料にしか無く、本書の元にした文書に記載が無いものは「未記載」としています。
- 「（説明なし：元データの列をそのまま保持）」は、元データの列を加工せずに持っている列です。

### 7.1 住所と市区町村

#### `address`

- **1行が表すもの:** 現在有効な町字1件（ABR 町字マスターの1町字）
- **主キー:** `address_id`
- **行数:** 726,170
- **用途:** 住所の基準。名前・読み・ローマ字と ABR の属性を持つ。郵便番号などとの対応はブリッジ側にある

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `address_id` | TEXT / large_string | 0 | 住所の永続 ID（20文字、`jpa1` で始まる）。`address_entity.address_id` と同じ値 | 例 `jpa100000t0w77znkc35`、`jpa10004carwwhjq1j26`、`jpa10006jyztjn724hrw` |
| `lg_code` | TEXT / large_string | 0 | 市区町村コード6桁。→ `municipality.lg_code`（宣言 FK）。政令市の住所は区のコードで、親（市全体）のコードは使われない | 例 `011011`、`011029`、`011037` |
| `jis_city_code` | TEXT / large_string | 0 | JIS 市区町村コード5桁。`lg_code` の先頭5桁 | 例 `01101`、`01102`、`01103` |
| `machiaza_id` | TEXT / large_string | 0 | ABR の町字 ID 7桁。一意なのは `(lg_code, machiaza_id)` の組 | 例 `0018500`、`0002155`、`0084104` |
| `machiaza_type` | TEXT / large_string | 0 | ABR 町字マスターの列 `machiaza_type` をそのまま保持（発行元のコード値）。各値の意味は 未記載 | `3` 526,324、`1` 105,978、`2` 93,767、`4` 70、`5` 31 |
| `pref` | TEXT / large_string | 0 | 都道府県名 | 例 `高知県`、`岩手県`、`石川県` |
| `county` | TEXT / large_string | 524,583 | 郡名。郡に属さない市区町村は NULL | 例 `吾川郡`、`石川郡`、`土佐郡` |
| `city` | TEXT / large_string | 0 | 市区町村名（政令市は市名） | 例 `いの町`、`奥州市`、`輪島市` |
| `ward` | TEXT / large_string | 668,264 | 政令市の区名。政令市の区でなければ NULL | 例 `中央区`、`瑞穂区`、`東山区` |
| `oaza_cho` | TEXT / large_string | 2,943 | 大字・町名 | 例 `上八川上分`、`胆沢南都田`、`三井町市ノ坂` |
| `chome` | TEXT / large_string | 632,396 | 丁目名（ABR の表記のまま。「一丁目」「２丁目」が混在する） | 例 `３丁目`、`二十二丁目`、`８丁目` |
| `chome_number` | TEXT / large_string | 632,396 | 丁目の番号（数字の文字列） | 例 `3`、`22`、`8` |
| `koaza` | TEXT / large_string | 199,846 | 小字名 | 例 `大野田`、`字二丁目`、`エ` |
| `machiaza_dist` | TEXT / large_string | 725,831 | ABR 町字マスターの列 `machiaza_dist` をそのまま保持。定義は 未記載（実測では値の例は京都の通り名で、725,831 行が NULL） | 例 `上の下立売通（妙心寺道）`、`御池通`、`大宮通` |
| `oaza_cho_kana` | TEXT / large_string | 15,713 | 大字・町名の読み（カタカナ） | 例 `カミヤカワカミブン`、`イサワナツタ`、`ミイマチイチノサカ` |
| `chome_kana` | TEXT / large_string | 632,396 | 丁目の読み | 例 `３チョウメ`、`２２チョウメ`、`８チョウメ` |
| `koaza_kana` | TEXT / large_string | 238,391 | 小字の読み | 例 `オオノダ`、`アザニチョウメ`、`エ` |
| `oaza_cho_roma` | TEXT / large_string | 154,507 | 大字・町名のローマ字 | 例 `Isawanatsuta`、`Miimachi Ichinosaka`、`Kutsukakecho` |
| `koaza_roma` | TEXT / large_string | 725,189 | 小字のローマ字 | 例 `2-Ku`、`Kamisugazawa`、`Nimurai` |
| `rsdt_addr_flg` | TEXT / large_string | 1,248 | ABR の住居表示／地番の区別を表すフラグ（発行元のコード値。各値の意味は 未記載）。同じ町字が ABR に2行あり値が食い違う場合は NULL（`rsdt_variant_count` = 2 の 1,248 件） | `0` 664,793、`1` 60,129 |
| `rsdt_addr_mtd_code` | TEXT / large_string | 1,248 | ABR 町字マスターの列 `rsdt_addr_mtd_code` をそのまま保持（発行元のコード値。意味は 未記載）。NULL の意味は `rsdt_addr_flg` と同じ | `0` 664,793、`1` 60,098、`2` 31 |
| `oaza_cho_aka_flg` | TEXT / large_string | 0 | ABR 町字マスターの列 `oaza_cho_aka_flg` をそのまま保持（発行元のコード値）。意味は 未記載 | `0` 712,152、`1` 14,018 |
| `koaza_aka_code` | TEXT / large_string | 0 | ABR 町字マスターの列 `koaza_aka_code` をそのまま保持（発行元のコード値）。意味は 未記載 | `0` 701,519、`1` 15,847、`2` 8,804 |
| `status_flg` | TEXT / large_string | 0 | ABR 町字マスターの列 `status_flg` をそのまま保持（発行元のコード値）。各値の意味は 未記載 | `1` 657,909、`0` 46,863、`2` 21,374、`3` 24 |
| `wake_num_flg` | TEXT / large_string | 0 | ABR 町字マスターの列 `wake_num_flg` をそのまま保持（発行元のコード値）。意味は 未記載 | `1` 552,297、`0` 173,873 |
| `src_code` | TEXT / large_string | 0 | ABR 町字マスターの列 `src_code` をそのまま保持（発行元のコード値）。意味は 未記載 | `0` 621,896、`1` 104,274 |
| `remarks` | TEXT / large_string | 726,162 | ABR の備考 | `「蓮」は二点しんにょう` 7、`「辻」は一点しんにょう` 1 |
| `full_name_raw` | TEXT / large_string | 0 | 町字名。ABR の部品を連結したもの（表記はそのまま）。フラット表の `town_name` | 例 `上八川上分大野田`、`胆沢南都田字二丁目`、`三井町市ノ坂エ`（空文字列 70 行） |
| `full_name_normalized` | TEXT / large_string | 0 | 照合に使った正規化後の町字名（全角数字の半角化、丁目の漢数字→数字など）。ヶ/ケ、旧字体などは統一しない | 例 `中島公園`、`伏見1丁目`、`伏見2丁目`（空文字列 70 行） |
| `normalization_profile` | TEXT / large_string | 0 | 正規化の方式名 | 全行 `conservative` |
| `rsdt_variant_count` | INTEGER / uint32 | 0 | この町字が ABR 町字マスターに何行あるか（住居表示フラグ違いの重複）。1 か 2 | `1` 724,922、`2` 1,248 |
| `valid_from` | TEXT / large_string | 1,248 | ABR の施行日（`efct_date`）。元の行どうしで値が食い違う場合は NULL | 例 `1947-04-17`、`1970-01-01`、`2022-01-31` |
| `valid_to` | TEXT / large_string | 726,169 | ABR の廃止日（`ablt_date`）。廃止されていなければ NULL | `2026-09-04` 1 |
| `observed_from` | TEXT / large_string | 0 | jpac がこの行を観測した最初の日 | 全行 `2026-10-15` |
| `observed_to` | TEXT / large_string | 726,170 | jpac がこの行を最後に観測した日。現在も観測中なら NULL | 全行 NULL |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | 全行 `snap_abr_town_master_57d18651b50…` |

#### `address_entity`

- **1行が表すもの:** jpac が一度でも観測した町字1件（ID 台帳）。退役しても行は消えない
- **主キー:** `address_id`
- **行数:** 726,170
- **用途:** `address_id` の発番記録。ブリッジの `address_id` が宣言 FK で参照するのはこの表

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `address_id` | TEXT / large_string | 0 | 住所の永続 ID | 例 `jpa100000t0w77znkc35`、`jpa10004carwwhjq1j26`、`jpa10006jyztjn724hrw` |
| `genesis_lg_code` | TEXT / large_string | 0 | ID を作ったとき（初観測時）の `lg_code` | 例 `393860`、`032158`、`172049` |
| `genesis_machiaza_id` | TEXT / large_string | 0 | ID を作ったときの `machiaza_id` | 例 `0018500`、`0002155`、`0084104` |
| `entity_status` | TEXT / large_string | 0 | `active`（有効）／ `retired`（元データから消えた。行は残る） | 全行 `active` |
| `identity_match_rule` | TEXT / large_string | 0 | 今回のビルドで ID を決めた規則。`I1` キー一致で継続、`I2` 町字 ID の訂正、`I3` 署名済みの市区町村コード変更、`I4` 改称、`I5` 新規発番、`I6` 退役済みキーの再出現（新規発番して要確認） | 全行 `I1` |
| `first_observed_snapshot_id` | TEXT / large_string | 0 | 最初に観測した元データ | 全行 `snap_abr_town_master_57d18651b50…` |
| `last_observed_snapshot_id` | TEXT / large_string | 0 | 最後に観測した元データ | 全行 `snap_abr_town_master_57d18651b50…` |
| `created_at` | TEXT / large_string | 0 | 台帳に載った日 | 全行 `2026-10-15` |
| `retired_at` | TEXT / large_string | 726,170 | 退役日（ABR の廃止日が分かるとき）。有効なら NULL | 全行 NULL |
| `retire_reason` | TEXT / large_string | 726,170 | 退役の理由（`absent_from_source` など）。有効なら NULL | 全行 NULL |

#### `address_code`

- **1行が表すもの:** 住所1件 × コードの種類 × コードの値 × 観測開始日（コードの履歴）
- **主キー:** 宣言なし（論理キーは `(address_id, code_type, code_value, observed_from)`。DB は一意性を強制しない）
- **行数:** 2,178,510
- **用途:** ある住所がこれまでに持ったコードを引く。追記のみで、使われなくなったコードは `observed_to` が入って残る

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `address_id` | TEXT / large_string | 0 | → `address_entity.address_id`（宣言 FK） | 例 `jpa100000t0w77znkc35`、`jpa10004carwwhjq1j26`、`jpa10006jyztjn724hrw` |
| `code_type` | TEXT / large_string | 0 | コードの種類 | `jis_city_code` 726,170、`abr_machiaza_id` 726,170、`abr_lg_code` 726,170 |
| `code_value` | TEXT / large_string | 0 | コードの値（文字列） | 例 `393860`、`0018500`、`39386` |
| `valid_from` | TEXT / large_string | 3,744 | そのコードの施行日（ABR が明記している場合） | 例 `1947-04-17`、`1970-01-01`、`2022-01-31` |
| `valid_to` | TEXT / large_string | 2,178,507 | そのコードの廃止日 | `2026-09-04` 3 |
| `observed_from` | TEXT / large_string | 0 | このコードを観測し始めた日 | 全行 `2026-09-20` |
| `observed_to` | TEXT / large_string | 2,178,510 | 観測しなくなった日。現在も有効なら NULL | 全行 NULL |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | 全行 `snap_abr_town_master_57d18651b50…` |

#### `address_rsdt_variant`

- **1行が表すもの:** 町字1件 × 住居表示の属性の組（ABR 町字マスターで住居表示フラグ・日付だけが違う重複行を、そのまま残したもの）
- **主キー:** `rsdt_variant_id`
- **行数:** 727,418
- **用途:** `address` で NULL になった住居表示フラグ・日付の元の値を引く。`(lg_code, machiaza_id)` で `address` に 1〜2 行対応する

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `rsdt_variant_id` | TEXT / large_string | 0 | 行の ID（`rv_{lg_code}_{machiaza_id}_{住居表示フラグ}_{方式コード}`） | 例 `rv_011011_0001001_1_1`、`rv_011011_0001002_1_1`、`rv_011011_0001003_1_1` |
| `lg_code` | TEXT / large_string | 0 | → `municipality.lg_code`（宣言 FK）。`address` とは `(lg_code, machiaza_id)` で結ぶ（宣言なし） | 例 `011011`、`011029`、`011037` |
| `machiaza_id` | TEXT / large_string | 0 | 町字 ID | 例 `0001001`、`0001002`、`0001003` |
| `rsdt_addr_flg` | TEXT / large_string | 0 | ABR の列 `rsdt_addr_flg`（発行元のコード値。各値の意味は 未記載） | `0` 666,041、`1` 61,377 |
| `rsdt_addr_mtd_code` | TEXT / large_string | 0 | ABR の列 `rsdt_addr_mtd_code`（発行元のコード値。意味は 未記載） | `0` 666,041、`1` 61,346、`2` 31 |
| `efct_date` | TEXT / large_string | 0 | ABR の施行日 | 例 `1947-04-17`、`2025-04-07`、`2022-11-20` |
| `ablt_date` | TEXT / large_string | 727,417 | ABR の廃止日。廃止されていなければ NULL | `2026-09-04` 1 |

#### `address_history`

- **1行が表すもの:** 住所の属性が1つ変わった記録1件
- **主キー:** `history_id`
- **行数:** 0
- **用途:** 町字名などの変化を追う。元データがまだ一度も更新されていないため v2.3.0 では 0 行（欠損ではない）

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `history_id` | TEXT / large_string | 0 | 記録の ID（`hst_…`） | （0行） |
| `address_id` | TEXT / large_string | 0 | → `address_entity.address_id`（宣言 FK） | （0行） |
| `field_name` | TEXT / large_string | 0 | 変わった列の名前 | （0行） |
| `old_value` | TEXT / large_string | 0 | 変更前の値 | （0行） |
| `new_value` | TEXT / large_string | 0 | 変更後の値 | （0行） |
| `valid_from` | TEXT / large_string | 0 | 変更の施行日（元データが明記している場合のみ） | （0行） |
| `observed_at` | TEXT / large_string | 0 | 変化を検出したビルドの日 | （0行） |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | （0行） |

#### `address_lineage`

- **1行が表すもの:** 町字の分割・統合・改称・コード訂正・退役などの出来事1件（旧 ID → 新 ID）
- **主キー:** `lineage_id`
- **行数:** 0
- **用途:** 古い `address_id` が今どれになったかを引く。v2.3.0 では 0 行（元データが未更新のため）

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `lineage_id` | TEXT / large_string | 0 | 記録の ID（`lin_…`） | （0行） |
| `old_address_id` | TEXT / large_string | 0 | 旧 ID → `address_entity.address_id`（宣言 FK） | （0行） |
| `new_address_id` | TEXT / large_string | 0 | 新 ID → `address_entity.address_id`（宣言 FK） | （0行） |
| `relation_type` | TEXT / large_string | 0 | 出来事の種類。設計上は `split` / `merge` / `renamed` / `code_corrected` / `municipality_recoded` / `retired` / `reinstated` | （0行） |
| `effective_date` | TEXT / large_string | 0 | 出来事の施行日。元データが明記している場合のみ、無ければ NULL | （0行） |
| `observed_at` | TEXT / large_string | 0 | jpac が検出した日 | （0行） |
| `evidence` | TEXT / large_string | 0 | 根拠の説明 | （0行） |
| `evidence_source` | TEXT / large_string | 0 | 根拠の出どころ（`abr_efct_date` / `abr_ablt_date` / `manual_override`） | （0行） |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | （0行） |

#### `address_key_conflict`

- **1行が表すもの:** ABR 町字マスターでキー `(lg_code, machiaza_id)` が重複し、住居表示以外の列まで食い違っていた元の行1件
- **主キー:** 宣言なし
- **行数:** 0
- **用途:** 保留中の衝突の記録。行があるとリリースは止まる仕組みで、v2.3.0 では 0 行。列は ABR 町字マスターの列をそのまま持つ

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `lg_code` | TEXT / large_string | 0 | → `municipality.lg_code`（宣言 FK） | （0行） |
| `machiaza_id` | TEXT / large_string | 0 | 町字 ID | （0行） |
| `machiaza_type` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `pref` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `pref_kana` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `pref_roma` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `county` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `county_kana` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `county_roma` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `city` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `city_kana` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `city_roma` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `ward` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `ward_kana` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `ward_roma` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `oaza_cho` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `oaza_cho_kana` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `oaza_cho_roma` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `chome` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `chome_kana` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `chome_number` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `koaza` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `koaza_kana` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `koaza_roma` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `machiaza_dist` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `rsdt_addr_flg` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `rsdt_addr_mtd_code` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `oaza_cho_aka_flg` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `koaza_aka_code` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `oaza_cho_gsi_uncmn` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `koaza_gsi_uncmn` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `status_flg` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `wake_num_flg` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `efct_date` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `ablt_date` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `src_code` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `post_code` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `remarks` | TEXT / large_string | 0 | （説明なし：元データの列をそのまま保持） | （0行） |
| `jis_city_code` | TEXT / large_string | 0 | JIS 市区町村コード5桁（jpac が付与） | （0行） |
| `full_name_raw` | TEXT / large_string | 0 | 連結した町字名（jpac が付与） | （0行） |
| `full_name_normalized` | TEXT / large_string | 0 | 正規化した町字名（jpac が付与） | （0行） |

#### `municipality`

- **1行が表すもの:** 市区町村1件（政令市の親〔市全体〕20件と区171件、東京23区を含む 1,918件）
- **主キー:** `lg_code`
- **行数:** 1,918
- **用途:** 市区町村の実体。名前は `municipality_version`（`is_current = 1`）にある。多くの表の `lg_code` が宣言 FK でここを参照する

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `lg_code` | TEXT / large_string | 0 | 市区町村コード6桁（検査数字付き） | 例 `011002`、`011011`、`011029` |
| `jis_city_code` | TEXT / large_string | 0 | JIS 市区町村コード5桁（`lg_code` の先頭5桁）。全件で一意 | 例 `01100`、`01101`、`01102` |
| `first_observed_snapshot_id` | TEXT / large_string | 0 | 最初に観測した元データ | 全行 `snap_abr_city_master_419f6bc19a4…` |

#### `municipality_version`

- **1行が表すもの:** 市区町村1件の名前などの版1つ
- **主キー:** `municipality_version_id`
- **行数:** 1,918
- **用途:** 市区町村名・読み・ローマ字を引く。必ず `is_current = 1` で絞る（v2.3.0 は全行 1）

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `municipality_version_id` | TEXT / large_string | 0 | 版の ID（`ver_…`） | 例 `ver_0011c6bd200557d15b45e937`、`ver_001a9ebeb5c2faccf6960b5e`、`ver_0033025f932300a8013c4d24` |
| `lg_code` | TEXT / large_string | 0 | 市区町村コード → `municipality.lg_code`（宣言なしの論理結合） | 例 `205885`、`313700`、`141534` |
| `jis_city_code` | TEXT / large_string | 0 | JIS 市区町村コード5桁 | 例 `20588`、`31370`、`14153` |
| `pref` | TEXT / large_string | 0 | 都道府県名 | 例 `長野県`、`鳥取県`、`神奈川県` |
| `county` | TEXT / large_string | 995 | 郡名。郡に属さなければ NULL | 例 `上水内郡`、`東伯郡`、`東茨城郡` |
| `city` | TEXT / large_string | 0 | 市区町村名。政令市の区の行では市名（区名は `ward`） | 例 `小川村`、`湯梨浜町`、`相模原市` |
| `ward` | TEXT / large_string | 1,747 | 政令市の区名。区でなければ NULL（政令市の親の行も NULL） | 例 `南区`、`八幡東区`、`熱田区` |
| `pref_kana` | TEXT / large_string | 0 | 都道府県名の読み | 例 `ナガノケン`、`トットリケン`、`カナガワケン` |
| `county_kana` | TEXT / large_string | 995 | 郡名の読み | 例 `カミミノチグン`、`トウハクグン`、`ヒガシイバラキグン` |
| `city_kana` | TEXT / large_string | 0 | 市区町村名の読み | 例 `オガワムラ`、`ユリハマチョウ`、`サガミハラシ` |
| `ward_kana` | TEXT / large_string | 1,747 | 区名の読み | 例 `ミナミク`、`ヤハタヒガシク`、`アツタク` |
| `pref_roma` | TEXT / large_string | 0 | 都道府県名のローマ字 | 例 `Nagano`、`Tottori`、`Kanagawa` |
| `county_roma` | TEXT / large_string | 995 | 郡名のローマ字 | 例 `Kamiminochi-gun`、`Tohaku-gun`、`Higashiibaraki-gun` |
| `city_roma` | TEXT / large_string | 0 | 市区町村名のローマ字 | 例 `Ogawa-mura`、`Yurihama-cho`、`Sagamihara-shi` |
| `ward_roma` | TEXT / large_string | 1,747 | 区名のローマ字 | 例 `Minami-ku`、`Yahatahigashi-ku`、`Atsuta-ku` |
| `valid_from` | TEXT / large_string | 1,918 | 有効期間の開始（元データに無いため NULL） | 全行 NULL |
| `valid_to` | TEXT / large_string | 1,918 | 有効期間の終了（同上） | 全行 NULL |
| `observed_from` | TEXT / large_string | 0 | この版を観測し始めた日 | 全行 `2026-08-23` |
| `observed_to` | TEXT / large_string | 1,918 | この版を観測しなくなった日。現行なら NULL | 全行 NULL |
| `is_current` | INTEGER / bool | 0 | 現行の版なら 1 | 全行 `1` |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | 全行 `snap_abr_city_master_419f6bc19a4…` |

### 7.2 郵便番号

#### `postal_code_entity`

- **1行が表すもの:** 7桁の郵便番号1つ
- **主キー:** `postal_code`
- **行数:** 120,682
- **用途:** 郵便番号そのもの。ABR の公式変換表による対応（`bridge_address_postal_code`）はこれを指す

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `postal_code` | TEXT / large_string | 0 | 郵便番号7桁（CHECK で長さ7） | 例 `0010000`、`0010010`、`0010011` |
| `record_count` | INTEGER / uint32 | 0 | この郵便番号を持つ日本郵便 `ken_all` の行数 | `1` 119,341、`2` 845、`3` 176、`4` 78、`5` 48、`6` 41、`7` 27、`8` 24 ほか 28 種 |
| `observed_from` | TEXT / large_string | 0 | 観測し始めた日 | 全行 `2026-10-15` |
| `observed_to` | TEXT / large_string | 120,682 | 観測しなくなった日。現行なら NULL | 全行 NULL |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | 全行 `snap_japanpost_ken_all_3fd8f44bf…` |

#### `postal_record`

- **1行が表すもの:** 日本郵便 `ken_all`（住所の郵便番号）の1行
- **主キー:** `postal_record_id`
- **行数:** 124,513
- **用途:** 郵便レコードの実体。中身は `postal_record_version`（`is_current = 1`）にある

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `postal_record_id` | TEXT / large_string | 0 | 行の ID（`pr_…`、行の全列から作るハッシュ） | 例 `pr_00005b96b903a70b51e3c8af`、`pr_0000d24a333cc21f6072472e`、`pr_00013ad614d3fb52a12c22f1` |
| `postal_code` | TEXT / large_string | 0 | 郵便番号 → `postal_code_entity.postal_code`（宣言 FK） | 例 `5097513`、`9500892`、`3220303` |
| `jis_city_code` | TEXT / large_string | 0 | JIS 市区町村コード5桁（日本郵便の値） | 例 `21210`、`15102`、`09205` |
| `first_observed_snapshot_id` | TEXT / large_string | 0 | 最初に観測した元データ | 全行 `snap_japanpost_ken_all_3fd8f44bf…` |

#### `postal_record_version`

- **1行が表すもの:** 郵便レコード1行の版1つ
- **主キー:** `postal_record_version_id`
- **行数:** 124,513
- **用途:** 郵便番号の町域名・読み・旧郵便番号・日本郵便のフラグを引く。`is_current = 1` で絞る

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `postal_record_version_id` | TEXT / large_string | 0 | 版の ID（`ver_…`） | 例 `ver_000117cf457e4838e6eb2178`、`ver_00013d05d391df0a660ee197`、`ver_0001d7d53b1ad2ccf5b08261` |
| `postal_record_id` | TEXT / large_string | 0 | → `postal_record.postal_record_id`（宣言なしの論理結合） | 例 `pr_c918d22a4197a655a861a4c7`、`pr_85658be5fff09018bffd42de`、`pr_b4191ea0e8a9208e40b43b38` |
| `postal_code` | TEXT / large_string | 0 | 郵便番号7桁 | 例 `0010000`、`0010010`、`0010011` |
| `jis_city_code` | TEXT / large_string | 0 | JIS 市区町村コード5桁 | 例 `01101`、`01102`、`01103` |
| `old_postal_code_raw` | TEXT / large_string | 0 | 旧郵便番号（元の5文字。3桁の場合は右に空白2つ） | 例 `581  `、`816  `、`63922` |
| `old_postal_code` | TEXT / large_string | 0 | 旧郵便番号（右の空白を除いたもの。3桁または5桁） | 例 `001`、`002`、`003` |
| `pref_kana` | TEXT / large_string | 0 | 都道府県名の読み（半角カナを含む元の表記） | 例 `オオサカフ`、`フクオカケン`、`ナラケン` |
| `city_kana` | TEXT / large_string | 0 | 市区町村名の読み | 例 `ヤオシ`、`フクオカシハカタク`、`ゴセシ` |
| `town_kana` | TEXT / large_string | 0 | 町域名の読み | 例 `ヒガシオイハラ`、`ヒガシナカ`、`ミムロ` |
| `pref` | TEXT / large_string | 0 | 都道府県名 | 例 `大阪府`、`福岡県`、`奈良県` |
| `city` | TEXT / large_string | 0 | 市区町村名 | 例 `八尾市`、`福岡市博多区`、`御所市` |
| `town` | TEXT / large_string | 0 | 町域名 | 例 `東老原`、`東那珂`、`三室` |
| `town_raw` | TEXT / large_string | 0 | 町域名（元の表記） | 例 `東老原`、`東那珂`、`三室` |
| `town_normalized` | TEXT / large_string | 0 | 照合用に正規化した町域名 | 例 `中島公園`、`以下に掲載がない場合`、`伏見` |
| `parenthetical_raw` | TEXT / large_string | 118,009 | 町域名の末尾の「（…）」の中身。無ければ NULL | 例 `（早稲田町）`、`（その他）`、`（９階）` |
| `parenthetical_class` | TEXT / large_string | 0 | 「（…）」の意味の分類。`none` 無し ／ `non_geographic` 範囲を限定しない注記 ／ `geographic` 番地の範囲などで町の一部だけを指す ／ `unknown` 判別できない。`geographic` / `unknown` の行は町全体との `exact` 対応にはならない | `none` 118,009、`non_geographic` 3,424、`unknown` 1,979、`geographic` 1,101 |
| `flag_multi_code` | TEXT / large_string | 0 | 日本郵便のフラグ（`0`/`1` の文字列。1つの町域が複数の郵便番号を持つ） | `0` 121,860、`1` 2,653 |
| `flag_koaza_banchi` | TEXT / large_string | 0 | 日本郵便のフラグ（`0`/`1`）。意味の詳細は 未記載 | `0` 115,650、`1` 8,863 |
| `flag_has_chome` | TEXT / large_string | 0 | 日本郵便のフラグ（`0`/`1`）。意味の詳細は 未記載 | `0` 102,650、`1` 21,863 |
| `flag_multi_town` | TEXT / large_string | 0 | 日本郵便のフラグ（`0`/`1`）。1つの郵便番号が複数の町域を表す | `0` 119,341、`1` 5,172 |
| `update_flag` | TEXT / large_string | 0 | 日本郵便の更新の表示（コード値）。意味の詳細は 未記載 | `0` 124,507、`1` 6 |
| `change_reason` | TEXT / large_string | 0 | 日本郵便の変更理由（コード値）。意味の詳細は 未記載 | `0` 124,507、`5` 6 |
| `record_kind` | TEXT / large_string | 0 | 行の種類。`town` 通常の町域 ／ `no_listing`「以下に掲載がない場合」 ／ `ichien`「〜一円」 ／ `city_banchi`「〜の次に番地がくる場合」。`town` 以外は町ではなく市区町村単位の記述で、`bridge_municipality_postal`（規則 P3）に回る | `town` 122,603、`no_listing` 1,870、`ichien` 23、`city_banchi` 17 |
| `valid_from` | TEXT / large_string | 124,513 | 日本郵便は施行日を示さないため常に NULL | 全行 NULL |
| `valid_to` | TEXT / large_string | 124,513 | 同上 | 全行 NULL |
| `observed_from` | TEXT / large_string | 0 | 観測し始めた日 | 全行 `2026-08-23` |
| `observed_to` | TEXT / large_string | 124,513 | 観測しなくなった日。現行なら NULL | 全行 NULL |
| `is_current` | INTEGER / bool | 0 | 現行の版なら 1 | 全行 `1` |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | 全行 `snap_japanpost_ken_all_3fd8f44bf…` |

### 7.3 国土交通省 位置参照情報

#### `mlit_town`

- **1行が表すもの:** 国土交通省 位置参照情報（大字・町丁目レベル）の1行
- **主キー:** `mlit_record_id`
- **行数:** 191,106
- **用途:** 国交省レコードの実体。名前と代表点は `mlit_town_version`（`is_current = 1`）にある

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `mlit_record_id` | TEXT / large_string | 0 | 行の ID（`ml_…`） | 例 `ml_00008518e048b3363cfe0fa2`、`ml_0000ddba5d72b2ed99f8a4f4`、`ml_000226f315c304dba23e37be` |
| `mlit_code` | TEXT / large_string | 0 | 大字・町丁目コード12桁 | 例 `262050053000`、`122200039005`、`222260014000` |
| `jis_city_code` | TEXT / large_string | 0 | JIS 市区町村コード5桁 | 例 `26205`、`12220`、`22226` |
| `first_observed_snapshot_id` | TEXT / large_string | 0 | 最初に観測した元データ | 全行 `snapset_3eb691427e420770840f` |

#### `mlit_town_version`

- **1行が表すもの:** 国交省レコード1行の版1つ
- **主キー:** `mlit_town_version_id`
- **行数:** 191,106
- **用途:** 大字・町丁目名と代表点の緯度経度を引く。`is_current = 1` で絞る

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `mlit_town_version_id` | TEXT / large_string | 0 | 版の ID（`ver_…`） | 例 `ver_00005920d7d484a315bf520f`、`ver_0000fbd38810618c82579b19`、`ver_00015cacc6c40d4a0ed95b86` |
| `mlit_record_id` | TEXT / large_string | 0 | → `mlit_town.mlit_record_id`（宣言なしの論理結合） | 例 `ml_a9b497e4c5d4b3d1925648e4`、`ml_6b146b4b1a334301a7f8c4c8`、`ml_3f58f61d079cb0465b7dcfc5` |
| `mlit_code` | TEXT / large_string | 0 | 大字・町丁目コード12桁 | 例 `011010001001`、`011010001002`、`011010001003` |
| `jis_city_code` | TEXT / large_string | 0 | JIS 市区町村コード5桁 | 例 `01101`、`01102`、`01103` |
| `pref_code` | TEXT / large_string | 0 | 都道府県コード2桁 | 例 `21`、`01`、`30` |
| `pref_name` | TEXT / large_string | 0 | 都道府県名 | 例 `岐阜県`、`北海道`、`和歌山県` |
| `city_name` | TEXT / large_string | 0 | 市区町村名（郡名を含む国交省の表記） | 例 `飛騨市`、`網走郡大空町`、`東牟婁郡那智勝浦町` |
| `town_name_raw` | TEXT / large_string | 0 | 大字・町丁目名（国交省の表記のまま） | 例 `宮川町西忍`、`女満別西一条四丁目`、`大字築地一丁目` |
| `town_name_normalized` | TEXT / large_string | 0 | 照合用に正規化した名前 | 例 `中島公園`、`伏見1丁目`、`伏見2丁目`（空文字列 1 行） |
| `latitude` | REAL / double | 0 | 代表点の緯度（度）。**大字・町丁目全体の代表点で、建物の位置ではない** | 例 `36.341356`、`43.91315`、`33.628552` |
| `longitude` | REAL / double | 0 | 代表点の経度（度）。同上 | 例 `137.141824`、`144.173609`、`135.943966` |
| `source_material_code` | TEXT / large_string | 0 | 国交省の原典資料コード（発行元のコード値）。各値の意味は 未記載 | `0` 188,713、`1` 1,990、`3` 403 |
| `aza_class_code` | TEXT / large_string | 0 | 国交省の大字・字・丁目区分コード（発行元のコード値）。各値の意味は 未記載 | `1` 97,314、`3` 91,171、`0` 1,638、`2` 983 |
| `fiscal_year` | TEXT / large_string | 0 | データの年度（調査年。有効期間ではない） | 全行 `2025` |
| `isj_version` | TEXT / large_string | 0 | 位置参照情報の版。取れないときは空文字列（8,650行が空の理由は 未記載） | `19.0b` 182,456、空文字列 8,650 |
| `observed_from` | TEXT / large_string | 0 | 観測し始めた日 | 全行 `2026-08-23` |
| `observed_to` | TEXT / large_string | 191,106 | 観測しなくなった日。現行なら NULL | 全行 NULL |
| `is_current` | INTEGER / bool | 0 | 現行の版なら 1 | 全行 `1` |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない（47都道府県のファイルの束 `snapset_…`） | 全行 `snapset_3eb691427e420770840f` |

### 7.4 市外局番

#### `telephone_area`

- **1行が表すもの:** 総務省の番号区画1つ
- **主キー:** `numbering_area_code`
- **行数:** 582
- **用途:** 番号区画の実体。市外局番と対象地域の文は `telephone_area_version`（`is_current = 1`）にある

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `numbering_area_code` | TEXT / large_string | 0 | 番号区画コード（`NNN` または `NNN-N`） | 例 `001`、`002`、`003` |
| `first_observed_snapshot_id` | TEXT / large_string | 0 | 最初に観測した元データ | 全行 `snap_mic_shigai_list_7e020a71d76…` |

#### `telephone_area_version`

- **1行が表すもの:** 番号区画1つの版1つ
- **主キー:** `telephone_area_version_id`
- **行数:** 582
- **用途:** 番号区画の市外局番と、総務省の「対象地域」の原文を引く。`is_current = 1` で絞る

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `telephone_area_version_id` | TEXT / large_string | 0 | 版の ID（`ver_…`） | 例 `ver_0008ce58452519467969f78c`、`ver_0083c17aaa0592f39417b3e2`、`ver_010e259e3d449406dafb6bd3` |
| `numbering_area_code` | TEXT / large_string | 0 | → `telephone_area.numbering_area_code`（宣言なしの論理結合） | 例 `112`、`262`、`193` |
| `area_code` | TEXT / large_string | 0 | 市外局番（先頭の0を含む）。複数の番号区画が同じ市外局番を持つことがある | 例 `011`、`0123`、`0124` |
| `area_code_raw` | TEXT / large_string | 0 | 総務省の表の元の値。定義は 未記載 | 例 `194`、`495`、`279` |
| `area_text_raw` | TEXT / large_string | 0 | 対象地域の原文（例「岩手県久慈市、九戸郡（野田村及び洋野町に限る。）」）。フラット表の `numbering_area_name` | 例 `岩手県久慈市、九戸郡（野田村及び洋野町に限る。）`、`埼玉県本庄市、児玉郡（神川町（上阿久原、下阿久原、矢納及…`、`群馬県吾妻郡（高山村、中之条町（赤岩、入山、太子、小雨、…` |
| `local_digit_pattern` | TEXT / large_string | 0 | 総務省「市外局番の一覧」の表の4列目の値をそのまま保持。定義は 未記載 | `DE` 450、`CDE` 87、`E` 40、`BCDE` 5 |
| `current_as_of` | TEXT / large_string | 0 | 総務省の文書の「…現在」の日付 | 全行 `2026-03-01` |
| `observed_from` | TEXT / large_string | 0 | 観測し始めた日 | 全行 `2026-08-23` |
| `observed_to` | TEXT / large_string | 582 | 観測しなくなった日。現行なら NULL | 全行 NULL |
| `is_current` | INTEGER / bool | 0 | 現行の版なら 1 | 全行 `1` |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | 全行 `snap_mic_shigai_list_7e020a71d76…` |

#### `telephone_area_coverage`

- **1行が表すもの:** 番号区画の対象地域の文を、句（市区町村や郡ごと）に分けた1件
- **主キー:** `coverage_id`
- **行数:** 1,640
- **用途:** 「夕張市（富野を除く。）」のような除外・限定の原文を構造化して引く

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `numbering_area_code` | TEXT / large_string | 0 | → `telephone_area.numbering_area_code`（宣言 FK） | 例 `611`、`309-2`、`543` |
| `clause_raw` | TEXT / large_string | 0 | 句の原文。1つの句から複数行が出ることがある（郡の中の複数町村を挙げる句など） | 例 `熊本県水俣市`、`海部郡（大治町を除く。）`、`香川県さぬき市（小田、鴨部、鴨庄、志度及び末を除く。）` |
| `pref_name` | TEXT / large_string | 0 | 都道府県名 | 例 `熊本県`、`愛知県`、`香川県` |
| `county_name` | TEXT / large_string | 981 | 郡名。郡を含まない句は NULL | 例 `海部郡`、`阿蘇郡`、`吉野郡` |
| `municipality_name` | TEXT / large_string | 308 | 市区町村名。郡全体を指す句などは NULL | 例 `水俣市`、`さぬき市`、`産山村` |
| `sub_municipal_text` | TEXT / large_string | 1,257 | 「〜を除く。」「〜に限る。」の中身 | 例 `大治町を除く。`、`小田、鴨部、鴨庄、志度及び末を除く。`、`安居、泉、泉市野谷入会地、市野谷、押辺、上郷、下郷、土師…` |
| `qualifier` | TEXT / large_string | 0 | `none` 限定なし ／ `exclude`「〜を除く。」 ／ `limit`「〜に限る。」 | `none` 1,075、`limit` 357、`exclude` 208 |
| `coverage_type` | TEXT / large_string | 0 | `full` 市区町村全体 ／ `partial` 一部だけ | `full` 1,257、`partial` 383 |
| `exception_text` | TEXT / large_string | 1,071 | 除外・限定の原文（括弧付き） | 例 `（大治町を除く。）`、`（小田、鴨部、鴨庄、志度及び末を除く。）`、`（産山村、小国町及び南小国町に限る。）` |
| `parse_rule` | TEXT / large_string | 0 | 句を読んだ規則（T1 市区町村全体、T3 一部、T4 郡内の町村を限定、T5 郡全体） | `T1` 802、`T3` 383、`T5` 244、`T4` 211 |
| `coverage_id` | TEXT / large_string | 0 | 行の ID（`cov_…`） | 例 `cov_002a2cf2a5c18f17a03378ff`、`cov_005db532e44d8d7c63c94585`、`cov_005ff4fb7421f635bb63a484` |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | 全行 `snap_mic_shigai_list_7e020a71d76…` |

#### `telephone_number_block`

- **1行が表すもの:** 市外局番 + 市内局番の割り当て1件（総務省「電気通信番号指定状況（固定電話等）」）
- **主キー:** `block_id`
- **行数:** 41,987
- **用途:** 市内局番の使用状況と事業者を引く。照合は無く、番号区画コードで結ぶだけの事実表

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `numbering_area_code` | TEXT / large_string | 0 | → `telephone_area.numbering_area_code`（宣言 FK） | 例 `001`、`002`、`003` |
| `number` | TEXT / large_string | 0 | 市外局番 + 市内局番（例 `011200` = `011` + `200`）。総務省の表の「番号」 | 例 `011200`、`011201`、`011202` |
| `area_code` | TEXT / large_string | 0 | 市外局番（先頭の0付き） | 例 `011`、`0123`、`0124` |
| `local_code` | TEXT / large_string | 0 | 市内局番 | 例 `200`、`201`、`202` |
| `carrier` | TEXT / large_string | 0 | 指定を受けた事業者名。未使用の番号は空文字列 | 空文字列 18,882、`西日本電信電話株式会社` 9,775、`東日本電信電話株式会社` 9,225、`ソフトバンク株式会社` 1,445、`ＫＤＤＩ株式会社` 1,374、`エヌ・ティ・ティ・コミュニケーションズ株式会社` 556、`楽天モバイル株式会社` 139、`株式会社オプテージ` 130 ほか 12 種 |
| `usage_status` | TEXT / large_string | 0 | 使用状況（総務省の表の値） | `使用中` 23,105、`未使用` 18,882 |
| `remarks` | TEXT / large_string | 0 | 備考（総務省の表の値） | 全行 空文字列 |
| `current_as_of` | TEXT / large_string | 0 | 総務省の文書の「…現在」の日付 | 全行 `2024-09-01` |
| `block_id` | TEXT / large_string | 0 | 行の ID（`blk_{市外局番}_{市内局番}`） | 例 `blk_011_200`、`blk_011_201`、`blk_011_202` |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない（9ファイルの束 `snapset_…`） | 全行 `snapset_2d570bcc44f99d88a0b0` |

### 7.5 ブリッジ7本（共通30列）

住所または市区町村と、郵便番号・国交省・番号区画を結ぶ7本のブリッジは、**同じ30列**を同じ順で持ちます（駅・路線・バス停のブリッジは形が違い、[7.6](#76-駅-路線-バス停-e-stat-小地域) にあります）。
違うのは4列目の**端点列**の名前（指す先の表の主キー名）だけです。主キーはどれも `bridge_id` です。

| ブリッジ | 主語（探す側） | 端点列 | 端点が指す表 | 行数 | 規則 |
|---|---|---|---|---:|---|
| `bridge_address_postal_code` | `address_id` | `postal_code` | `postal_code_entity`（7桁の郵便番号） | 676,124 | P1、P7 |
| `bridge_address_postal` | `address_id` | `postal_record_id` | `postal_record`（日本郵便 `ken_all` の1行） | 352,499 | P4、P4p、P5、P6a、P6b、P7 |
| `bridge_address_mlit` | `address_id` | `mlit_record_id` | `mlit_town`（国交省 位置参照情報の1行） | 733,948 | M1、M2、M3、M4、M5 |
| `bridge_address_telephone` | `address_id` | `numbering_area_code` | `telephone_area`（番号区画） | 726,170 | T10 |
| `bridge_municipality_postal_code` | `lg_code` | `postal_code` | `postal_code_entity`（7桁の郵便番号） | 8,207 | P2 |
| `bridge_municipality_postal` | `lg_code` | `postal_record_id` | `postal_record`（日本郵便の特殊レコード） | 1,910 | P3 |
| `bridge_municipality_telephone` | `lg_code` | `numbering_area_code` | `telephone_area`（番号区画） | 2,153 | T1、T1b、T1d、T3、T4、T5、T6 |

共通30列の意味（型は7本とも同じ。SQLite / Parquet）:

| 列名 | 型（SQLite / Parquet） | 説明 |
|---|---|---|
| `bridge_id` | TEXT / large_string | 行の ID（`brg_…`）。行の意味から作るハッシュで、同じ入力なら同じ値 |
| `address_id` | TEXT / large_string | 住所側の端点 → `address_entity.address_id`（宣言 FK）。住所系4本で使う。住所系では、相手側から探して住所が見つからなかった行だけ NULL。**市区町村系3本では常に NULL** |
| `lg_code` | TEXT / large_string | 市区町村側の端点 → `municipality.lg_code`（宣言 FK）。市区町村系3本で使う。**住所系4本では常に NULL**（住所の市区町村は `address.lg_code` から取る） |
| （端点列） | TEXT / large_string | 相手側の端点。表ごとに名前が違う（`postal_code` / `postal_record_id` / `mlit_record_id` / `numbering_area_code`）。それぞれの実体表への宣言 FK。`relation_type = 'unresolved'` の行で、相手が見つからなかった側が NULL |
| `direction` | TEXT / large_string | どちら側から照合した行か（`address_to_mlit`、`postal_to_address` など）。前半が探した側で、探した側は常に NULL でない |
| `relation_type` | TEXT / large_string | 対応の付き方。`exact` 同じ範囲（コードと名前で確認）／ `equivalent` 公式のコード対応が同じと述べる ／ `parent` 主語側が相手を含む ／ `child` 主語側が相手に含まれる ／ `overlap` 重なる ／ `ambiguous` 候補が複数で決められない ／ `unresolved` 相手が見つからない。CHECK は `contains` / `candidate` も許すが v2.3.0 では 0 行 |
| `match_method` | TEXT / large_string | 対応の見つけ方。`direct_code` 発行元のコード対応 ／ `composite` コードと名前の両方が一致 ／ `normalized_name` 正規化した名前の一致 ／ `parent_child` 発行元が示す親子関係 ／ `official_area_rule` 公式の文（対象地域の文など）から導出 ／ `manual_override` 人手の上書き ／ `unresolved` 該当なし |
| `matching_rule_id` | TEXT / large_string | 適用された照合規則（P1、M1、T1 など。各表の規則一覧を参照） |
| `confidence` | REAL / double | 規則ごとに決まる確からしさ（0〜1）。**確率ではない**。1.00 コード明示 + 別項目で裏付け ／ 0.99 公式のコード対応 ／ 0.97 名前が双方向で一意に一致 ／ 0.95 市区町村名の ヶ/ケ を畳んで一意 ／ 0.90 構造的な親子関係・根拠つきの別名 ／ 0.70 一部だけの重なり等 ／ 0.50 候補どまり ／ 0.0 未解決 |
| `candidate_group_id` | TEXT / large_string | 同じ候補群に属する行に共通の ID（`grp_…`）。`candidate_count` が2以上のときだけ入り、1以下なら NULL（CHECK で強制） |
| `candidate_count` | INTEGER / int64 | 候補の数。候補群の全件から数えた値（表示用に切り詰めた数ではない）。未解決は 0 |
| `candidate_count_is_complete` | INTEGER / bool | `candidate_count` が全候補から数えた値なら 1（v2.3.0 は全行 1） |
| `is_unique_match` | INTEGER / bool | 候補が1件に定まったら 1 |
| `verification_status` | TEXT / large_string | `auto` 自動確定 ／ `review_required` 要確認 ／ `manually_verified` 人手で確認済み ／ `manually_rejected` 人手で棄却（v2.3.0 は `auto` と `review_required` だけ）。`auto` は「候補1件・一意・全件数え済み・confidence 0.98以上・上書きが古くない・relation_type が exact か equivalent」をすべて満たす行だけで、CHECK 制約で強制される |
| `override_stale` | INTEGER / bool | 人手の上書きが、元データの更新で前提を失っていれば 1（v2.3.0 は全行 0） |
| `derivation` | TEXT / large_string | 導出の経路。`bridge_municipality_telephone` でのみ値がある（`expanded_from_county` 郡から町村に展開 ／ `designated_city_wards` 政令市を区にも展開 ／ `attested_source_name_alias` 署名済みの別名で解決）。他は NULL |
| `coverage_type` | TEXT / large_string | 市外局番の収容範囲。`full` 市区町村全体 ／ `partial` 一部 ／ `municipality_only` 町字単位では公表されていない（`bridge_address_telephone` の全行）。郵便・国交省のブリッジでは NULL |
| `normalization_profile` | TEXT / large_string | 名前の照合に使った正規化の方式（`conservative`） |
| `mismatch_note` | TEXT / large_string | 食い違いや未解決の理由の説明文（英語または日本語）。無ければ NULL |
| `valid_from` | TEXT / large_string | 対応の有効期間の開始。ABR 変換表が日付を持つ郵便番号の2本だけ値がある |
| `valid_to` | TEXT / large_string | 対応の有効期間の終了。終了していなければ NULL |
| `observed_from` | TEXT / large_string | この行を観測し始めた日 |
| `observed_to` | TEXT / large_string | 観測しなくなった日。現行なら NULL |
| `is_current` | INTEGER / bool | 現行の行なら 1（v2.3.0 は全行 1）。フラット表とビュー `address_crosswalk` は 1 の行だけを使う |
| `match_run_id` | TEXT / large_string | この行を作った照合の実行 → `match_run.match_run_id`（宣言 FK） |
| `source_snapshot_id` | TEXT / large_string | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない。複数の元データが関わる行の全入力は `match_run_input` で引く |
| `matching_rule_version` | TEXT / large_string | 照合規則の版（`2.0.0`） |
| `normalization_profile_version` | TEXT / large_string | 正規化プロファイルの版（`1.0.0`） |
| `created_at` | TEXT / large_string | 行を作った日時（RFC 3339、`+09:00` 付き） |
| `updated_at` | TEXT / large_string | 行を更新した日時（同上） |

CHECK 制約（7本共通）: `confidence` は 0〜1、`candidate_count >= 0`、探した側（`direction` の前半）は NULL でない、相手側が NULL であることと `relation_type = 'unresolved'` であることは同値、`candidate_count` が2以上のときだけ `candidate_group_id` が入る、`is_unique_match = 1` なら候補は1以下、`verification_status = 'auto'` は自動確定の6条件をすべて満たす行だけ。`relation_type` / `match_method` / `verification_status` は列挙された値だけを取ります。

#### `bridge_address_postal_code`

- **1行が表すもの:** 町字1件 × 郵便番号1つの対応（デジタル庁「ABR 町字・郵便番号変換表」による公式の対応）
- **主キー:** `bridge_id`
- **行数:** 676,124
- **主語 → 端点:** `address_id` → `postal_code`。指す先は `postal_code_entity`（7桁の郵便番号）
- **用途:** 住所 → 郵便番号の主たる対応。フラット表の `postal_*` 列はこの表から作る
- **規則:** P1 は ABR 変換表の行で、郵便番号が日本郵便のデータにもあるもの。P7（27行）は変換表が ABR 町字マスターに無い町字を指しているため住所に結べなかった行で、`address_id` が NULL、`postal_code` は入っている。`valid_from` / `valid_to` は変換表の日付。

規則ごとの内訳（実測）:

| direction | matching_rule_id | relation_type | match_method | confidence | verification_status | 行数 | `address_id` が NULL | `postal_code` が NULL |
|---|---|---|---|---:|---|---:|---:|---:|
| `address_to_postal_code` | `P1` | `equivalent` | `direct_code` | 0.99 | `auto` | 674,574 | 0 | 0 |
| `address_to_postal_code` | `P1` | `equivalent` | `direct_code` | 0.99 | `review_required` | 1,523 | 0 | 0 |
| `postal_code_to_address` | `P7` | `unresolved` | `unresolved` | 0.0 | `review_required` | 27 | 27 | 0 |

#### `bridge_address_postal`

- **1行が表すもの:** 町字1件 × 日本郵便のレコード1行の対応（名前の照合による）
- **主キー:** `bridge_id`
- **行数:** 352,499
- **主語 → 端点:** `address_id` → `postal_record_id`。指す先は `postal_record`（日本郵便 `ken_all` の1行）
- **用途:** 公式変換表とは別に、日本郵便のファイルと名前で突き合わせた結果。信頼度が違うためフラット表には入らない。全行 `review_required`
- **規則:** P4 は名前が双方向で一意に一致、P4p は町域名に範囲を限る「（…）」があるため `overlap`、P5 は郵便の町域が丁目に分かれる町字をまとめて指す（西新宿 → 一〜八丁目の8行）、P6a / P6b は候補が複数、P7 は未一致。`direction = 'address_to_postal'` の未一致行は `postal_record_id` が NULL、`postal_to_address` の未一致行は `address_id` が NULL。

規則ごとの内訳（実測）:

| direction | matching_rule_id | relation_type | match_method | confidence | verification_status | 行数 | `address_id` が NULL | `postal_record_id` が NULL |
|---|---|---|---|---:|---|---:|---:|---:|
| `postal_to_address` | `P4` | `exact` | `normalized_name` | 0.97 | `review_required` | 74,688 | 0 | 0 |
| `postal_to_address` | `P4p` | `overlap` | `normalized_name` | 0.7 | `review_required` | 63 | 0 | 0 |
| `postal_to_address` | `P5` | `parent` | `parent_child` | 0.9 | `review_required` | 200,816 | 0 | 0 |
| `postal_to_address` | `P6a` | `ambiguous` | `normalized_name` | 0.5 | `review_required` | 856 | 0 | 0 |
| `postal_to_address` | `P6b` | `ambiguous` | `normalized_name` | 0.5 | `review_required` | 1,556 | 0 | 0 |
| `address_to_postal` | `P7` | `unresolved` | `unresolved` | 0.0 | `review_required` | 48,873 | 0 | 48,873 |
| `postal_to_address` | `P7` | `unresolved` | `unresolved` | 0.0 | `review_required` | 25,647 | 25,647 | 0 |

#### `bridge_address_mlit`

- **1行が表すもの:** 町字1件 × 国交省レコード1行の対応
- **主キー:** `bridge_id`
- **行数:** 733,948
- **主語 → 端点:** `address_id` → `mlit_record_id`。指す先は `mlit_town`（国交省 位置参照情報の1行）
- **用途:** 住所 → 国交省 大字・町丁目コード・代表点。フラット表の `mlit_*` 列はこの表から作る
- **規則:** M1 はコードと名前の両方が一致、M2 はコードは一致するが名前が違う（要確認、差分は `mismatch_note`）、M3 はコードは一致しないが名前が双方向で一意に一致、M4 は候補が複数、M5 は未一致。国交省側から見て住所が見つからない行（`direction = 'mlit_to_address'`）は `address_id` が NULL。

規則ごとの内訳（実測）:

| direction | matching_rule_id | relation_type | match_method | confidence | verification_status | 行数 | `address_id` が NULL | `mlit_record_id` が NULL |
|---|---|---|---|---:|---|---:|---:|---:|
| `address_to_mlit` | `M1` | `exact` | `composite` | 1.0 | `auto` | 162,057 | 0 | 0 |
| `address_to_mlit` | `M2` | `equivalent` | `direct_code` | 0.9 | `review_required` | 15,712 | 0 | 0 |
| `address_to_mlit` | `M3` | `exact` | `normalized_name` | 0.97 | `review_required` | 5,507 | 0 | 0 |
| `address_to_mlit` | `M4` | `ambiguous` | `normalized_name` | 0.5 | `review_required` | 106 | 0 | 0 |
| `address_to_mlit` | `M5` | `unresolved` | `unresolved` | 0.0 | `review_required` | 542,792 | 0 | 542,792 |
| `mlit_to_address` | `M5` | `unresolved` | `unresolved` | 0.0 | `review_required` | 7,774 | 7,774 | 0 |

#### `bridge_address_telephone`

- **1行が表すもの:** 町字1件につき1行（全行が未解決）
- **主キー:** `bridge_id`
- **行数:** 726,170
- **主語 → 端点:** `address_id` → `numbering_area_code`。指す先は `telephone_area`（番号区画）
- **用途:** **中身のある対応は入っていない。** 総務省は町字単位の番号区画を公表していないため、全町字が規則 T10 の `unresolved`（`numbering_area_code` は全行 NULL、`coverage_type = 'municipality_only'`）。市外局番は `bridge_municipality_telephone` を `address.lg_code` で引く
- **規則:** T10 は「市区町村単位の根拠しかないので町字には展開しない」という規則そのもの。

規則ごとの内訳（実測）:

| direction | matching_rule_id | relation_type | match_method | confidence | verification_status | 行数 | `address_id` が NULL | `numbering_area_code` が NULL |
|---|---|---|---|---:|---|---:|---:|---:|
| `address_to_telephone` | `T10` | `unresolved` | `unresolved` | 0.0 | `review_required` | 726,170 | 0 | 726,170 |

#### `bridge_municipality_postal_code`

- **1行が表すもの:** 市区町村1件 × 郵便番号1つ（ABR 変換表のうち町字 ID が空の、市区町村単位の行）
- **主キー:** `bridge_id`
- **行数:** 8,207
- **主語 → 端点:** `lg_code` → `postal_code`。指す先は `postal_code_entity`（7桁の郵便番号）
- **用途:** 町字に結べない、市区町村単位で公式に示された郵便番号。全行 `parent`・`review_required`
- **規則:** P2 のみ。同じ市区町村が複数の郵便番号を持つため候補群になる行が多い（`candidate_count` は市区町村ごとの郵便番号の数）。

規則ごとの内訳（実測）:

| direction | matching_rule_id | relation_type | match_method | confidence | verification_status | 行数 | `lg_code` が NULL | `postal_code` が NULL |
|---|---|---|---|---:|---|---:|---:|---:|
| `municipality_to_postal` | `P2` | `parent` | `direct_code` | 0.99 | `review_required` | 8,207 | 0 | 0 |

#### `bridge_municipality_postal`

- **1行が表すもの:** 市区町村1件 × 日本郵便の特殊レコード1行（「以下に掲載がない場合」「〜一円」「〜の次に番地がくる場合」）
- **主キー:** `bridge_id`
- **行数:** 1,910
- **主語 → 端点:** `lg_code` → `postal_record_id`。指す先は `postal_record`（日本郵便の特殊レコード）
- **用途:** 日本郵便の市区町村単位の行を市区町村に結んだもの。郵便番号は `postal_record` / `postal_record_version` から取る
- **規則:** P3 のみ。v1.3.0 まで P2 と同じ表・同じ列に混在していたが、v2.0.0 で2表に分けた（P2 は郵便番号、P3 は郵便レコードを指す）。

規則ごとの内訳（実測）:

| direction | matching_rule_id | relation_type | match_method | confidence | verification_status | 行数 | `lg_code` が NULL | `postal_record_id` が NULL |
|---|---|---|---|---:|---|---:|---:|---:|
| `municipality_to_postal` | `P3` | `parent` | `official_area_rule` | 0.99 | `review_required` | 1,910 | 0 | 0 |

#### `bridge_municipality_telephone`

- **1行が表すもの:** 番号区画1つ × 市区町村1件（総務省「市外局番の一覧」の対象地域の文から）
- **主キー:** `bridge_id`
- **行数:** 2,153
- **主語 → 端点:** `lg_code` → `numbering_area_code`。指す先は `telephone_area`（番号区画）
- **用途:** 市区町村 ↔ 市外局番の**本来の**対応。1つの市区町村が複数の番号区画にまたがることがある
- **規則:** T1 句が市区町村を限定なしで挙げる、T1b ヶ/ケ を畳んで一意に解決、T1d 総務省の表記が古い市名を署名済みの別名で解決（丹波篠山市）、T3 「〜を除く。」「〜に限る。」で一部だけ（`coverage_type = 'partial'`、除外の原文は `mismatch_note` と `telephone_area_coverage`）、T4 郡内の町村を限定して挙げる、T5 郡全体を町村に展開、T6 市区町村名が一意に決まらない（候補を全部残す）。v2.3.0 の T6 は「東京都23区」の句（番号区画 `219`、市外局番 `03`）の23行だけで、23区それぞれが候補（`candidate_count = 23`、`ambiguous`）になっている（規則 T1c の行は 0 行）。政令市が丸ごと挙がる場合は親のコードと区のコードの両方に行がある（`derivation = 'designated_city_wards'`）。

規則ごとの内訳（実測）:

| direction | matching_rule_id | relation_type | match_method | confidence | verification_status | 行数 | `lg_code` が NULL | `numbering_area_code` が NULL |
|---|---|---|---|---:|---|---:|---:|---:|
| `telephone_to_municipality` | `T1` | `child` | `official_area_rule` | 0.99 | `review_required` | 859 | 0 | 0 |
| `telephone_to_municipality` | `T1b` | `child` | `official_area_rule` | 0.95 | `review_required` | 2 | 0 | 0 |
| `telephone_to_municipality` | `T1d` | `child` | `official_area_rule` | 0.9 | `review_required` | 1 | 0 | 0 |
| `telephone_to_municipality` | `T3` | `overlap` | `official_area_rule` | 0.7 | `review_required` | 497 | 0 | 0 |
| `telephone_to_municipality` | `T4` | `child` | `official_area_rule` | 0.99 | `review_required` | 211 | 0 | 0 |
| `telephone_to_municipality` | `T5` | `child` | `official_area_rule` | 0.95 | `review_required` | 560 | 0 | 0 |
| `telephone_to_municipality` | `T6` | `ambiguous` | `official_area_rule` | 0.5 | `review_required` | 23 | 0 | 0 |

### 7.6 駅 路線 バス停 e-Stat 小地域

#### `n02_station`

- **1行が表すもの:** 駅1つ（国土数値情報 鉄道データ N02 2025年版の「駅グループ」1つ）
- **主キー:** `n02_group_code`
- **行数:** 9,046
- **用途:** 駅の名前と代表の路線・事業者。**座標は持たない**（座標は別アセットの [`station_point`](#station_point座標アセット)）。市区町村は `bridge_station_municipality`、通る路線は `bridge_station_line` で引く

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `n02_group_code` | TEXT / large_string | 0 | N02 の駅グループコード（`N02_005g`）。乗換駅は事業者が違っても同じグループなら1駅 | 例 `000001`、`000002`、`000004` |
| `feature_count` | INTEGER / uint32 | 0 | グループに含まれる N02 の地物（ホーム・線路の線分）の数 | `1` 8,205、`2` 633、`3` 149、`4` 25、`5` 14、`6` 9、`7` 4、`8` 3 ほか 2 種 |
| `station_name_raw` | TEXT / large_string | 0 | 駅名（N02 の表記） | 例 `稚内`、`南稚内`、`勇知` |
| `line_name_raw` | TEXT / large_string | 0 | 代表の路線名。グループに複数の路線があるときは「そのうちの1つ」（駅コード・路線名の順で最小の行）で、主たる路線という意味はない | 例 `宗谷線`、`石北線`、`釧網線` |
| `operator_name_raw` | TEXT / large_string | 0 | 代表の事業者名（同上） | 例 `北海道旅客鉄道`、`札幌市`、`一般社団法人札幌市交通事業振興公社` |
| `railway_class` | TEXT / large_string | 0 | N02 の鉄道区分コード（`N02_001`、発行元のコード値）。コードの意味は 未記載（`12` 普通鉄道、`21` 軌道のみ記載あり） | `11` 4,216、`12` 3,887、`21` 626、`24` 82、`16` 76、`23` 63、`13` 51、`22` 16 ほか 3 種 |
| `operator_class` | TEXT / large_string | 0 | N02 の事業者種別コード（`N02_002`、発行元のコード値）。コードの意味は 未記載 | `2` 4,159、`4` 3,098、`5` 1,238、`3` 494、`1` 57 |
| `n02_station_code` | TEXT / large_string | 0 | N02 の駅コード（`N02_005c`）。代表の行の値 | 例 `000001`、`000002`、`000004` |
| `station_name_variants` | INTEGER / uint32 | 0 | グループ内の駅名の種類数 | 全行 `1` |
| `line_variants` | INTEGER / uint32 | 0 | グループ内の路線名の種類数（2以上なら乗換駅） | `1` 8,230、`2` 635、`3` 129、`4` 27、`5` 13、`6` 7、`7` 5 |
| `operator_variants` | INTEGER / uint32 | 0 | グループ内の事業者名の種類数 | `1` 8,647、`2` 345、`3` 42、`4` 8、`5` 3、`6` 1 |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | 全行 `snap_mlit_ksj_n02_n02_2025_1276a…` |

#### `n02_railroad_line`

- **1行が表すもの:** 路線1本（N02 の `(路線名, 運営会社)` の組）
- **主キー:** `(line_name_raw, operator_name_raw)`
- **行数:** 596
- **用途:** 路線の一覧。路線にはコードが無いので名前の組がキー。**形状は持たない**

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `line_name_raw` | TEXT / large_string | 0 | 路線名（N02 の表記） | 例 `10号線新宿線`、`11号線半蔵門線`、`12号線大江戸線` |
| `operator_name_raw` | TEXT / large_string | 0 | 運営会社名（N02 の表記） | 例 `東京都`、`東京地下鉄`、`千葉都市モノレール` |
| `section_count` | INTEGER / uint32 | 0 | 路線を構成する N02 の区間（地物）の数 | 例 `45`、`28`、`89` |
| `railway_class` | TEXT / large_string | 0 | 鉄道区分コード（N02 の値）。区間によって違う場合は「そのうちの1つ」（最小値） | `12` 308、`11` 179、`21` 63、`13` 21、`16` 10、`23` 5、`24` 4、`22` 2 ほか 3 種 |
| `operator_class` | TEXT / large_string | 0 | 事業者種別コード（N02 の値）。同上 | `4` 277、`2` 170、`5` 96、`3` 44、`1` 9 |
| `railway_class_variants` | INTEGER / uint32 | 0 | 路線内の鉄道区分の種類数（10路線が2） | `1` 586、`2` 10 |
| `operator_class_variants` | INTEGER / uint32 | 0 | 路線内の事業者種別の種類数 | 全行 `1` |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | 全行 `snap_mlit_ksj_n02_n02_2025_1276a…` |

#### `bridge_station_line`

- **1行が表すもの:** 駅1つ × 路線1本（N02 の駅の地物が自ら持つ路線名の属性による）
- **主キー:** 宣言なし（`(n02_group_code, line_name_raw, operator_name_raw)` で一意に並べて出力）
- **行数:** 10,153
- **用途:** 駅を通る路線、路線の駅を引く。乗換駅は路線の数だけ行がある（9,046駅に 10,153行）

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `n02_group_code` | TEXT / large_string | 0 | → `n02_station.n02_group_code`（宣言 FK） | 例 `000001`、`000002`、`000004` |
| `station_name_raw` | TEXT / large_string | 0 | 駅名（この路線での表記） | 例 `稚内`、`南稚内`、`勇知` |
| `line_name_raw` | TEXT / large_string | 0 | 路線名。`(line_name_raw, operator_name_raw)` → `n02_railroad_line`（宣言 FK、複合） | 例 `10号線新宿線`、`11号線半蔵門線`、`12号線大江戸線` |
| `operator_name_raw` | TEXT / large_string | 0 | 運営会社名（同上） | 例 `東京都`、`東京地下鉄`、`千葉都市モノレール` |
| `feature_count` | INTEGER / uint32 | 0 | その駅・路線の組の地物の数 | `1` 10,087、`2` 56、`3` 6、`4` 3、`5` 1 |
| `match_method` | TEXT / large_string | 0 | 常に `n02_attribute`（属性による。空間判定ではない） | 全行 `n02_attribute` |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | 全行 `snap_mlit_ksj_n02_n02_2025_1276a…` |

#### `bridge_station_municipality`

- **1行が表すもの:** 駅1つ × 候補の市区町村1件（候補が複数なら候補ごとに1行）
- **主キー:** 宣言なし（`(n02_group_code, boundary_jis_city_code)` で並べて出力）
- **行数:** 9,061
- **用途:** 駅 → 市区町村。駅の代表点を e-Stat 小地域境界（2020年）に当てて判定。9,046駅に 9,061行

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `n02_group_code` | TEXT / large_string | 0 | → `n02_station.n02_group_code`（宣言 FK） | 例 `000001`、`000002`、`000004` |
| `station_name_raw` | TEXT / large_string | 0 | 駅名 | 例 `稚内`、`南稚内`、`勇知` |
| `operator_name_raw` | TEXT / large_string | 0 | 代表の事業者名 | 例 `北海道旅客鉄道`、`札幌市`、`一般社団法人札幌市交通事業振興公社` |
| `lg_code` | TEXT / large_string | 4 | 市区町村 → `municipality.lg_code`（宣言 FK）。**常に区・市町村のコードで、政令市の親コードは入らない**。`unresolved` の行と、境界データのコードが現行の市区町村にも承継記録にも無い行は NULL | 例 `011011`、`011029`、`011037` |
| `boundary_jis_city_code` | TEXT / large_string | 4 | 点が入った e-Stat 小地域（2020年）が持つ5桁の市区町村コード。浜松市の旧区コード（`22131`〜`22137`）のように現行と違うことがある。`unresolved` なら NULL | 例 `01214`、`01516`、`01520` |
| `relation_type` | TEXT / large_string | 0 | `contains` 市区町村に含まれる（1件に決まった）／ `ambiguous` 候補が2つ（境界の重なり、旧浜松市北区の分割）／ `unresolved` どの境界にも入らない（最寄りには寄せない） | `contains` 9,027、`ambiguous` 30、`unresolved` 4 |
| `match_method` | TEXT / large_string | 0 | `spatial_containment` 点が境界に入った ／ `spatial_containment_via_lineage` 境界の旧コードを承継記録で現行コードに読み替えた ／ `unresolved` | `spatial_containment` 8,989、`spatial_containment_via_line…` 68、`unresolved` 4 |
| `confidence` | REAL / double | 0 | `contains` / `ambiguous` は 1.0、`unresolved` は 0.0 | `1.0` 9,057、`0.0` 4 |
| `candidate_count` | INTEGER / int64 | 0 | 候補の市区町村の数（承継で読み替えた後の数）。未解決は 0 | `1` 9,027、`2` 30、`0` 4 |
| `is_unique_match` | INTEGER / int64 | 0 | 候補が1つなら 1。**Parquet では bool ではなく int64** | `1` 9,027、`0` 34 |
| `verification_status` | TEXT / large_string | 0 | `auto` 1件に決まった ／ `review_required` 候補が複数か未解決 | `auto` 9,027、`review_required` 34 |
| `placement_point` | TEXT / large_string | 0 | どの点で判定したか。`centroid` 駅の線分の重心 ／ `point_on_line` 重心が境界に入らないとき線上の点（v2.3.0 では 0 行）／ `none` どの点も入らない（未解決） | `centroid` 9,057、`none` 4 |
| `mismatch_note` | TEXT / large_string | 9,027 | 未解決・候補複数・承継の分割などの理由（日本語）。1件に決まった行は NULL | `境界データ側のコード 22135 は jpac の現行市…` 14、`境界データ側のコード 22135 は jpac の現行市…` 14、`どのポリゴンにも含まれない。最寄りへの割り当てはしない（…` 4、`複数の市区町村に含まれる。候補を残す（POLICY.md…` 2 |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | 全行 `snap_mlit_ksj_n02_n02_2025_1276a…` |

#### `bridge_line_municipality`

- **1行が表すもの:** 路線1本 × 通る市区町村1件
- **主キー:** 宣言なし（`(line_name_raw, operator_name_raw, boundary_jis_city_code)` で並べて出力）
- **行数:** 3,381
- **用途:** 路線 → 通る市区町村。線上の点（25m 間隔）を境界に当てて判定。40の市区町村を通る路線は40行の正しい答えで、曖昧さではない

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `line_name_raw` | TEXT / large_string | 0 | 路線名。`(line_name_raw, operator_name_raw)` → `n02_railroad_line`（宣言 FK、複合） | 例 `10号線新宿線`、`11号線半蔵門線`、`12号線大江戸線` |
| `operator_name_raw` | TEXT / large_string | 0 | 運営会社名 | 例 `東京都`、`東京地下鉄`、`千葉都市モノレール` |
| `lg_code` | TEXT / large_string | 0 | 通る市区町村 → `municipality.lg_code`（宣言 FK）。政令市は区のコード | 例 `011011`、`011029`、`011037` |
| `boundary_jis_city_code` | TEXT / large_string | 0 | 当たった境界の5桁コード（2020年） | 例 `12203`、`13101`、`13102` |
| `relation_type` | TEXT / large_string | 0 | `overlap`（路線が市区町村と重なる）／ `unresolved`（v2.3.0 では 0 行） | 全行 `overlap` |
| `match_method` | TEXT / large_string | 0 | `spatial_sampling` ／ `spatial_sampling_via_lineage`（旧コードを承継記録で読み替え）／ `unresolved` | `spatial_sampling` 3,364、`spatial_sampling_via_lineage` 17 |
| `confidence` | REAL / double | 0 | 1.0（未解決なら 0.0） | 全行 `1.0` |
| `sample_hits` | INTEGER / int64 | 0 | その市区町村に入った標本点の数（根拠の量。長さや割合ではない） | 例 `88`、`240`、`60` |
| `municipality_count` | INTEGER / int64 | 0 | その路線が通る市区町村の数（路線ごとに同じ値が繰り返される） | `3` 255、`2` 226、`4` 224、`7` 203、`5` 200、`8` 168、`6` 168、`9` 162 ほか 29 種 |
| `verification_status` | TEXT / large_string | 0 | `auto`（v2.3.0 は全行） | 全行 `auto` |
| `mismatch_note` | TEXT / large_string | 3,381 | 未解決などの理由。v2.3.0 は全行 NULL | 全行 NULL |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | 全行 `snap_mlit_ksj_n02_n02_2025_1276a…` |

#### `p11_bus_stop`

- **1行が表すもの:** バス停留所データ（国土数値情報 P11 令和4年度版）の1行
- **主キー:** `p11_stop_id`
- **行数:** 278,515
- **用途:** バス停の名前・事業者・都道府県。**座標は持たない**（座標は別アセットの [`bus_stop_point`](#bus_stop_point座標アセット)）。同名・同事業者の別のバス停が多数あるため、名前で集約してはいけない

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `p11_stop_id` | TEXT / large_string | 0 | 行の代理キー `p11-22-{都道府県2桁}-{連番6桁}`。連番は発行元ファイルの県内の行順。**版をまたいで同じバス停を指す保証はない** | 例 `p11-22-01-000000`、`p11-22-01-000001`、`p11-22-01-000002` |
| `pref_code` | TEXT / large_string | 0 | 都道府県コード2桁（発行元のファイル名から） | 例 `01`、`02`、`03` |
| `stop_name_raw` | TEXT / large_string | 0 | 停留所名 | 例 `宗谷岬`、`宗谷岬漁港前`、`珊内` |
| `operator_name_raw` | TEXT / large_string | 0 | 事業者名 | 例 `宗谷バス（株）`、`沿岸バス（株）`、`名士バス（株）` |
| `note_raw` | TEXT / large_string | 0 | P11 の備考（`P11_005`）。無ければ空文字列 | 空文字列 278,167、`自治体と民間での共同運行` 337、`運休中` 2、`迂回運行時` 2、`冬期間迂回運行時` 2、`冬季運休` 2、`臨時運行時` 1、`特急期間通過` 1 ほか 1 種 |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | 全行 `snap_mlit_ksj_p11_P11-22_SHP_b4e…` |

#### `bridge_bus_stop_municipality`

- **1行が表すもの:** バス停1行 × 候補の市区町村1件
- **主キー:** 宣言なし（`(p11_stop_id, boundary_jis_city_code)` で並べて出力）
- **行数:** 278,801
- **用途:** バス停 → 市区町村。発行元の点を e-Stat 小地域境界（2020年）に当てて判定。278,515停留所に 278,801行

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `p11_stop_id` | TEXT / large_string | 0 | → `p11_bus_stop.p11_stop_id`（宣言 FK） | 例 `p11-22-01-000000`、`p11-22-01-000001`、`p11-22-01-000002` |
| `stop_name_raw` | TEXT / large_string | 0 | 停留所名 | 例 `宗谷岬`、`宗谷岬漁港前`、`珊内` |
| `operator_name_raw` | TEXT / large_string | 0 | 事業者名 | 例 `宗谷バス（株）`、`沿岸バス（株）`、`名士バス（株）` |
| `lg_code` | TEXT / large_string | 115 | 市区町村 → `municipality.lg_code`（宣言 FK）。**常に区・市町村のコードで、政令市の親コードは入らない**。`unresolved` の行と、境界データのコードが現行の市区町村にも承継記録にも無い行は NULL | 例 `011011`、`011029`、`011037` |
| `boundary_jis_city_code` | TEXT / large_string | 115 | 点が入った e-Stat 小地域（2020年）が持つ5桁の市区町村コード。浜松市の旧区コード（`22131`〜`22137`）のように現行と違うことがある。`unresolved` なら NULL | 例 `01214`、`01517`、`01511` |
| `relation_type` | TEXT / large_string | 0 | `contains` 市区町村に含まれる（1件に決まった）／ `ambiguous` 候補が2つ（境界の重なり、旧浜松市北区の分割）／ `unresolved` どの境界にも入らない（最寄りには寄せない） | `contains` 278,114、`ambiguous` 572、`unresolved` 115 |
| `match_method` | TEXT / large_string | 0 | `spatial_containment` 点が境界に入った ／ `spatial_containment_via_lineage` 境界の旧コードを承継記録で現行コードに読み替えた ／ `unresolved` | `spatial_containment` 277,192、`spatial_containment_via_line…` 1,494、`unresolved` 115 |
| `confidence` | REAL / double | 0 | `contains` / `ambiguous` は 1.0、`unresolved` は 0.0 | `1.0` 278,686、`0.0` 115 |
| `candidate_count` | INTEGER / int64 | 0 | 候補の市区町村の数（承継で読み替えた後の数）。未解決は 0 | `1` 278,114、`2` 572、`0` 115 |
| `is_unique_match` | INTEGER / int64 | 0 | 候補が1つなら 1。**Parquet では bool ではなく int64** | `1` 278,114、`0` 687 |
| `verification_status` | TEXT / large_string | 0 | `auto` 1件に決まった ／ `review_required` 候補が複数か未解決 | `auto` 278,114、`review_required` 687 |
| `mismatch_note` | TEXT / large_string | 278,114 | 未解決・候補複数・承継の分割などの理由（日本語）。1件に決まった行は NULL | `境界データ側のコード 22135 は jpac の現行市…` 276、`境界データ側のコード 22135 は jpac の現行市…` 276、`どのポリゴンにも含まれない。最寄りへの割り当てはしない（…` 115、`複数の市区町村に含まれる。候補を残す（POLICY.md…` 20 |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | 全行 `snap_mlit_ksj_p11_P11-22_SHP_b4e…` |

#### `estat_small_area`

- **1行が表すもの:** e-Stat 令和2年国勢調査 小地域（町丁・字等）境界データの1ポリゴン分の属性（**形状は含まない**）
- **主キー:** 宣言なし（`key_code` で並べて出力。1つの町丁・字が複数ポリゴンに分かれると同じ `key_code` が複数行になりうる）
- **行数:** 232,019
- **用途:** 駅・バス停の判定に使った境界の属性と、境界の市区町村コードが現行のどれに当たるかの分類。町字（`address_id`）とは結ばない

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `key_code` | TEXT / large_string | 0 | 小地域の識別子（`KEY_CODE` = 都道府県 + 市区町村 + 町丁・字等番号） | 例 `011010200`、`011010300`、`011010400` |
| `pref_code` | TEXT / large_string | 0 | 都道府県コード2桁 | 例 `01`、`02`、`03` |
| `city_code` | TEXT / large_string | 0 | 市区町村コード3桁 | 例 `101`、`102`、`103` |
| `s_area` | TEXT / large_string | 0 | 町丁・字等番号 | 例 `020000`、`030000`、`040000` |
| `pref_name_raw` | TEXT / large_string | 0 | 都道府県名 | 例 `北海道`、`青森県`、`岩手県` |
| `city_name_raw` | TEXT / large_string | 0 | 市区町村名（政令市は区まで）。空文字列なら所属未定地 | 例 `札幌市中央区`、`札幌市北区`、`札幌市東区`（空文字列 2 行） |
| `s_name_raw` | TEXT / large_string | 0 | 町丁・字等の名前 | 例 `宮ケ丘（番地）`、`円山`、`円山西町（番地）`（空文字列 388 行） |
| `hcode` | TEXT / large_string | 0 | `8101` 町丁・字等 ／ `8154` 水面調査区（港湾・漁港の水域。判定には使わない） | `8101` 231,668、`8154` 351 |
| `kigo_e` | TEXT / large_string | 0 | 重複境域フラグ。1つの町丁・字が複数のポリゴンに分かれるとき `E1`…。数字の意味は 未記載。無ければ空文字列 | 例 `E1`、`E2`、`E3`（空文字列 214,638 行） |
| `area_max_f` | TEXT / large_string | 0 | 分かれたポリゴンのうち最大のものに `M`。それ以外は空文字列 | `M` 220,852、空文字列 11,167 |
| `kigo_d` | TEXT / large_string | 0 | `D` 飛び地 ／ `D1` 抜け地（他の市区町村に属する穴）／ 空文字列 | 空文字列 231,689、`D` 323、`D1` 7 |
| `n_ken` | TEXT / large_string | 0 | 抜け地の本来の都道府県コード。無ければ空文字列 | 空文字列 231,690、`27` 56、`12` 46、`06` 29、`15` 28、`11` 28、`08` 20、`34` 17 ほか 23 種 |
| `n_city` | TEXT / large_string | 0 | 抜け地の本来の市区町村コード3桁。無ければ空文字列 | 例 `363`、`381`、`365`（空文字列 231,690 行） |
| `kigo_i` | TEXT / large_string | 0 | `I` 島 ／ 空文字列 | 空文字列 228,232、`I` 3,787 |
| `jis_city_code` | TEXT / large_string | 0 | `pref_code` + `city_code`（5桁、2020年時点のコード）。`municipality.jis_city_code` と結ぶ（宣言なし） | 例 `01101`、`01102`、`01103` |
| `reconcile_status` | TEXT / large_string | 0 | 2020年のコードが現行のどれに当たるか。`matched` 現行の市区町村にある ／ `superseded` 承継記録で1つの現行コードに移った旧コード（浜松市の旧区）／ `split_no_single_successor` 承継先が1つに決まらない旧コード（旧浜松市北区 `22135`）／ `unassigned_area` 所属未定地（市区町村名が空）／ `estat_only` 説明できない（v2.3.0 では 0 行） | `matched` 231,263、`superseded` 686、`split_no_single_successor` 68、`unassigned_area` 2 |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない（47都道府県の束 `snapset_…`） | 全行 `snapset_7a1f11819bea85c8f25d` |

#### `station_point`（座標アセット）

- **入っているファイル:** `jp_address_crosswalk_points.zip` の `station_point.parquet`。**SQLite・テーブル別 zip には無い**
- **1行が表すもの:** 駅1つの代表点（`n02_station` の1行に1行）
- **主キー:** `n02_group_code`
- **行数:** 9,046（座標が NULL の行 0）
- **用途:** 駅を地図に載せる。N02 は駅を線（ホームの線分）で公開していて点を持たないので、その線から計算した点。
  `bridge_station_municipality` が市区町村の判定に使った点と同じ（静的マップのピンとも同じ）。**駅舎・出入口の位置ではない**
- **測地系:** JGD2011 の経緯度（EPSG:6668）。範囲は経度 127.652285〜145.58304、緯度 26.193265〜45.416338（実測）

| 列名 | 型（Parquet / Snowflake 推奨） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `n02_group_code` | large_string / `VARCHAR` | 0 | → `n02_station.n02_group_code`（1対1） | 例 `000001`、`000002`、`000004` |
| `station_name_raw` | large_string / `VARCHAR` | 0 | 駅名（`n02_station` と同じ） | 例 `稚内`、`南稚内`、`勇知` |
| `operator_name_raw` | large_string / `VARCHAR` | 0 | 事業者名（`n02_station` と同じ） | 例 `北海道旅客鉄道` |
| `longitude` | double / `FLOAT` | 0 | 経度。小数6桁（約 0.1 m）に丸めた値 | 例 `141.67731` |
| `latitude` | double / `FLOAT` | 0 | 緯度。小数6桁に丸めた値 | 例 `45.416338` |
| `placement_point` | large_string / `VARCHAR` | 0 | 点の取り方。`centroid` 線の長さで重み付けした重心 ／ `point_on_line` 線の長さの中点（重心がどの境界にも入らなかった駅に使う）。座標が NULL なら NULL | `centroid` 9,046 |
| `source_snapshot_id` | large_string / `VARCHAR` | 0 | 元データ（`source_snapshot.source_snapshot_id`） | 全行 `snap_mlit_ksj_n02_n02_2025_1276a…` |

#### `bus_stop_point`（座標アセット）

- **入っているファイル:** `jp_address_crosswalk_points.zip` の `bus_stop_point.parquet`。**SQLite・テーブル別 zip には無い**
- **1行が表すもの:** バス停1行の点（`p11_bus_stop` の1行に1行）
- **主キー:** `p11_stop_id`
- **行数:** 278,515（座標が NULL の行 0。異なる点は 242,984 個 —— 同じ場所に事業者違いの停留所が重なるため）
- **用途:** バス停を地図に載せる。P11 が公表している点を**計算も丸めもせずに**そのまま出している。**停留所標識の位置とは限らない**
- **測地系:** JGD2011 の経緯度（EPSG:6668）。範囲は経度 123.747…〜145.811…、緯度 24.259…〜45.522…（実測）

| 列名 | 型（Parquet / Snowflake 推奨） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `p11_stop_id` | large_string / `VARCHAR` | 0 | → `p11_bus_stop.p11_stop_id`（1対1）。版をまたいで同じ停留所を指す保証はない | 例 `p11-22-01-000000` |
| `stop_name_raw` | large_string / `VARCHAR` | 0 | 停留所名（`p11_bus_stop` と同じ） | 例 `宗谷岬`、`宗谷岬漁港前`、`珊内` |
| `operator_name_raw` | large_string / `VARCHAR` | 0 | 事業者名（`p11_bus_stop` と同じ） | 例 `宗谷バス（株）` |
| `longitude` | double / `FLOAT` | 0 | 経度（発行元の値のまま） | 例 `141.93716703828878` |
| `latitude` | double / `FLOAT` | 0 | 緯度（発行元の値のまま） | 例 `45.522239250308765` |
| `source_snapshot_id` | large_string / `VARCHAR` | 0 | 元データ（`source_snapshot.source_snapshot_id`） | 全行 `snap_mlit_ksj_p11_P11-22_SHP_b4e…` |

座標アセットの表は市区町村の列を持ちません。市区町村はブリッジから引きます（境界上の駅・バス停は候補が2つ）。

```sql
-- 例（DuckDB）: 市区町村が1つに決まった駅の点
SELECT p.n02_group_code, p.station_name_raw, b.lg_code, p.longitude, p.latitude
FROM 'points/station_point.parquet' p
JOIN 'tables/bridge_station_municipality.parquet' b USING (n02_group_code)
WHERE b.verification_status = 'auto';
```

### 7.7 人口（国勢調査 令和7年）

#### `census_population_municipality`

- **1行が表すもの:** 国勢調査の回 × 市区町村1件（令和7年 = 2025年10月1日現在）
- **主キー:** `(census_year, lg_code)`
- **行数:** 1,918
- **用途:** **市区町村の人口はまずこれを使う。** 第1-1表の人口・世帯の公表値。政令市は親と区の両方の行があるので、合計するときは `area_level = '1'` を除く

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `census_year` | INTEGER / int32 | 0 | 国勢調査の年（2025）。次の回は行を足す | 全行 `2025` |
| `lg_code` | TEXT / large_string | 0 | → `municipality.lg_code`（宣言なしの論理結合。1,918件すべて一致） | 例 `011002`、`011011`、`011029` |
| `area_level` | TEXT / large_string | 6 | 地域識別コード。`1` 政令市（市全体、20件）／ `0` 東京23区・政令市の区 ／ `2` 政令市以外の市 ／ `3` 町村 ／ NULL 北方領土の6村（結果表に無い） | `3` 926、`2` 772、`0` 194、`1` 20 |
| `population_total` | INTEGER / int64 | 6 | 人口 総数。`value_status = 'published'` のとき必ず値がある | 例 `1961996`、`255523`、`285318` |
| `population_male` | INTEGER / int64 | 6 | 人口 男 | 例 `914908`、`116520`、`135368` |
| `population_female` | INTEGER / int64 | 6 | 人口 女 | 例 `1047088`、`139003`、`149950` |
| `population_2020_rebased` | INTEGER / int64 | 6 | 2020年の人口（組替）。統計局が2025年の境域に組み替えた公式の値 | 例 `1973395`、`248680`、`289323` |
| `population_change_5y` | INTEGER / int64 | 6 | 5年間の人口増減数（負の値あり） | 例 `-11399`、`6843`、`-4005` |
| `households_total` | INTEGER / int64 | 6 | 世帯数 総数 | 例 `998412`、`150081`、`142773` |
| `households_general` | INTEGER / int64 | 6 | 世帯数 一般世帯 | 例 `996291`、`149797`、`142537` |
| `households_institutional` | INTEGER / int64 | 6 | 世帯数 施設等の世帯（結果表の「-」は 0） | 例 `2121`、`284`、`236` |
| `households_2020_rebased` | INTEGER / int64 | 6 | 2020年の世帯数（組替） | 例 `969161`、`141429`、`139675` |
| `households_change_5y` | INTEGER / int64 | 6 | 5年間の世帯増減数 | 例 `29251`、`8652`、`3098` |
| `household_members_total` | INTEGER / int64 | 6 | 世帯人員 総数 | 例 `1961996`、`255523`、`285318` |
| `household_members_general` | INTEGER / int64 | 6 | 世帯人員 一般世帯 | 例 `1900917`、`247131`、`278067` |
| `household_members_institutional` | INTEGER / int64 | 6 | 世帯人員 施設等の世帯（「-」は 0） | 例 `61079`、`8392`、`7251` |
| `value_status` | TEXT / large_string | 0 | `published` 公表値あり ／ `not_enumerated` 国勢調査の対象外（北方領土の6村。値は NULL）／ `missing_from_table` 結果表に無い（あればビルドが止まる。v2.3.0 では 0 行） | `published` 1,912、`not_enumerated` 6 |
| `valid_from` | TEXT / large_string | 0 | 調査の期日（2025-10-01） | 全行 `2025-10-01` |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | 全行 `snap_estat_census_2025_populatio…` |

#### `census_population_age`

- **1行が表すもの:** 地域1つ × 国籍（総数／日本人）× 男女 × 5歳階級1つ（第2-7表の縦持ち）
- **主キー:** `(census_year, region_key, nationality_raw, sex_raw, age_class_raw)`
- **行数:** 538,824
- **用途:** 年齢別人口。全国・都道府県・2000年境域の市区町村の行も含むので、市区町村だけなら `lg_code IS NOT NULL` で絞る。外国人は表に無く、引き算で作らない

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `census_year` | INTEGER / int32 | 0 | 国勢調査の年（2025） | 全行 `2025` |
| `region_key` | TEXT / large_string | 0 | 地域のキー `{area_level_raw}:{2000年の地域コード}:{2025年の地域コード}` | 例 `0:01101:01101`、`0:01102:01102`、`0:01103:01103` |
| `lg_code` | TEXT / large_string | 286,440 | 現行の市区町村の行だけ値がある → `municipality.lg_code`（宣言 FK）。全国・都道府県・特別区部・2000年境域（`area_level_raw` が `a` / `9`）の行は NULL。政令市の親の行も値を持つ | 例 `011011`、`011029`、`011037` |
| `area_level_raw` | TEXT / large_string | 0 | 地域識別コード（結果表の値）。`a` 全国・都道府県 ／ `1` 特別区部・政令市 ／ `0` 東京23区・政令市の区 ／ `2` 政令市以外の市 ／ `3` 町村 ／ `9` 2000年時点の市区町村 | `9` 279,972、`3` 122,232、`2` 101,904、`0` 25,608、`a` 6,336、`1` 2,772 |
| `pref_2000_raw` | TEXT / large_string | 0 | 結果表の「2000年_都道府県」（例 `01_北海道`） | 例 `01_北海道`、`04_宮城県`、`11_埼玉県` |
| `area_code_2000_raw` | TEXT / large_string | 0 | 結果表の「2000年_地域コード」（5桁） | 例 `01101`、`01102`、`01103` |
| `year_2000_raw` | TEXT / large_string | 86,064 | 結果表の「2000年地域」の値。意味の詳細は 未記載 | `2000` 452,760 |
| `pref_2025_raw` | TEXT / large_string | 0 | 結果表の「2025年_都道府県」 | 例 `01_北海道`、`04_宮城県`、`11_埼玉県` |
| `area_code_2025_raw` | TEXT / large_string | 0 | 結果表の「2025年_地域コード」（5桁） | 例 `01101`、`01102`、`01103` |
| `area_name_raw` | TEXT / large_string | 0 | 結果表の地域名（`連番_名前` の形。例 `0004_札幌市中央区`） | 例 `0004_札幌市中央区`、`0005_札幌市北区`、`0006_札幌市東区` |
| `nationality_raw` | TEXT / large_string | 0 | 国籍（結果表の値） | `1_うち日本人` 269,412、`0_国籍総数` 269,412 |
| `sex_raw` | TEXT / large_string | 0 | 男女（結果表の値） | `2_女` 179,608、`1_男` 179,608、`0_総数` 179,608 |
| `age_class_raw` | TEXT / large_string | 0 | 年齢5歳階級（結果表の値）。先頭2桁で並ぶ。`22_年齢「不詳」` を含めて足すと総数になる | `22_年齢「不詳」` 24,492、`21_100歳以上` 24,492、`20_95～99歳` 24,492、`19_90～94歳` 24,492、`18_85～89歳` 24,492、`17_80～84歳` 24,492、`16_75～79歳` 24,492、`15_70～74歳` 24,492 ほか 14 種 |
| `population` | INTEGER / int64 | 0 | 人口（整数。「-」は 0） | 例 `6041`、`7482`、`8057` |
| `population_raw` | TEXT / large_string | 0 | 結果表のセルの文字列そのまま | 例 `6041`、`7482`、`8057` |
| `valid_from` | TEXT / large_string | 0 | 調査の期日（2025-10-01） | 全行 `2025-10-01` |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | 全行 `snap_estat_census_2025_age_259d7…` |

#### `census_population_age_summary`

- **1行が表すもの:** 地域1つ × 国籍 × 男女（第2-7表の平均年齢・年齢中位数）
- **主キー:** `(census_year, region_key, nationality_raw, sex_raw)`
- **行数:** 24,492
- **用途:** 平均年齢・年齢中位数の公表値（5歳階級からは再計算できないので別表）

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `census_year` | INTEGER / int32 | 0 | 国勢調査の年（2025） | 全行 `2025` |
| `region_key` | TEXT / large_string | 0 | 地域のキー `{area_level_raw}:{2000年の地域コード}:{2025年の地域コード}` | 例 `0:01101:01101`、`0:01102:01102`、`0:01103:01103` |
| `lg_code` | TEXT / large_string | 13,020 | `census_population_age.lg_code` と同じ規則 → `municipality.lg_code`（宣言 FK） | 例 `011011`、`011029`、`011037` |
| `area_level_raw` | TEXT / large_string | 0 | 地域識別コード（結果表の値）。`a` 全国・都道府県 ／ `1` 特別区部・政令市 ／ `0` 東京23区・政令市の区 ／ `2` 政令市以外の市 ／ `3` 町村 ／ `9` 2000年時点の市区町村 | `9` 12,726、`3` 5,556、`2` 4,632、`0` 1,164、`a` 288、`1` 126 |
| `pref_2000_raw` | TEXT / large_string | 0 | 結果表の「2000年_都道府県」（例 `01_北海道`） | 例 `01_北海道`、`04_宮城県`、`11_埼玉県` |
| `area_code_2000_raw` | TEXT / large_string | 0 | 結果表の「2000年_地域コード」（5桁） | 例 `01101`、`01102`、`01103` |
| `year_2000_raw` | TEXT / large_string | 3,912 | 結果表の「2000年地域」の値。意味の詳細は 未記載 | `2000` 20,580 |
| `pref_2025_raw` | TEXT / large_string | 0 | 結果表の「2025年_都道府県」 | 例 `01_北海道`、`04_宮城県`、`11_埼玉県` |
| `area_code_2025_raw` | TEXT / large_string | 0 | 結果表の「2025年_地域コード」（5桁） | 例 `01101`、`01102`、`01103` |
| `area_name_raw` | TEXT / large_string | 0 | 結果表の地域名（`連番_名前` の形。例 `0004_札幌市中央区`） | 例 `0004_札幌市中央区`、`0005_札幌市北区`、`0006_札幌市東区` |
| `nationality_raw` | TEXT / large_string | 0 | 国籍（結果表の値） | `1_うち日本人` 12,246、`0_国籍総数` 12,246 |
| `sex_raw` | TEXT / large_string | 0 | 男女（結果表の値） | `2_女` 8,164、`1_男` 8,164、`0_総数` 8,164 |
| `mean_age` | REAL / double | 0 | 平均年齢（歳） | 例 `48.31553`、`46.52963`、`49.78478` |
| `mean_age_raw` | TEXT / large_string | 0 | 結果表のセルの文字列そのまま（浮動小数の表記を含む） | 例 `48.315530000000003`、`46.529629999999997`、`49.784779999999998` |
| `median_age` | REAL / double | 0 | 年齢中位数（歳） | 例 `49.45948`、`47.75643`、`50.86401` |
| `median_age_raw` | TEXT / large_string | 0 | 結果表のセルの文字列そのまま | 例 `49.459479999999999`、`47.756430000000002`、`50.86401` |
| `valid_from` | TEXT / large_string | 0 | 調査の期日（2025-10-01） | 全行 `2025-10-01` |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | 全行 `snap_estat_census_2025_age_259d7…` |

#### `census_population_numbering_area`

- **1行が表すもの:** 国勢調査の回 × 番号区画1つ
- **主キー:** `(census_year, numbering_area_code)`
- **行数:** 582
- **用途:** 市外局番の番号区画ごとの人口。属する市区町村が**すべて丸ごと**含まれる区画だけ、その和を出す。按分はしない

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `census_year` | INTEGER / int32 | 0 | 国勢調査の年（2025） | 全行 `2025` |
| `numbering_area_code` | TEXT / large_string | 0 | → `telephone_area.numbering_area_code`（宣言なしの論理結合。582件すべて一致） | 例 `001`、`002`、`003` |
| `municipality_count` | INTEGER / uint32 | 0 | 区画に属する市区町村の行数（`bridge_municipality_telephone` の行。政令市の親が丸ごと含まれる区画ではその区の行を除いて数える） | `2` 157、`1` 119、`3` 100、`4` 67、`5` 48、`7` 24、`6` 24、`8` 15 ほか 11 種 |
| `population_total` | INTEGER / int64 | 281 | 人口 総数。`value_status = 'complete'` のときだけ値がある | 例 `2145106`、`168951`、`10581` |
| `population_male` | INTEGER / int64 | 281 | 人口 男 | 例 `1002008`、`84519`、`4904` |
| `population_female` | INTEGER / int64 | 281 | 人口 女 | 例 `1143098`、`84432`、`5677` |
| `households_total` | INTEGER / int64 | 281 | 世帯数 総数 | 例 `1081032`、`80341`、`5443` |
| `value_status` | TEXT / large_string | 0 | `complete` 区画 = 市区町村の和 ／ `partial_municipality` 一部だけ含む市区町村がある（NULL）／ `unresolved_member` 市区町村に解決できない句がある（NULL）／ `member_not_enumerated` 対象外の市区町村を含む ／ `no_municipality` 市区町村の行が無い（後2つは v2.3.0 で 0 行） | `complete` 301、`partial_municipality` 280、`unresolved_member` 1 |
| `valid_from` | TEXT / large_string | 0 | 調査の期日（2025-10-01） | 全行 `2025-10-01` |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | 全行 `snap_estat_census_2025_populatio…` |

#### `census_population_raw`

- **1行が表すもの:** 第1-1表の1行（全国・都道府県・2000年境域の行を含む 4,082行）
- **主キー:** `(table_id, row_ordinal)`
- **行数:** 4,082
- **用途:** 結果表のセルを文字列のまま確認する。面積（参考）と人口密度の列は持たない

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `table_id` | TEXT / large_string | 0 | 結果表の ID（`b01_01` = 第1-1表） | 全行 `b01_01` |
| `row_ordinal` | INTEGER / int64 | 0 | Excel の行番号（1始まり） | 例 `16`、`17`、`18` |
| `area_level_raw` | TEXT / large_string | 0 | 地域識別コード（結果表の値）。`a` 全国・都道府県 ／ `1` 特別区部・政令市 ／ `0` 東京23区・政令市の区 ／ `2` 政令市以外の市 ／ `3` 町村 ／ `9` 2000年時点の市区町村 | `9` 2,121、`3` 926、`2` 772、`0` 194、`a` 48、`1` 21 |
| `pref_2000_raw` | TEXT / large_string | 0 | 結果表の「2000年_都道府県」（例 `01_北海道`） | 例 `00_全国`、`01_北海道`、`02_青森県` |
| `area_code_2000_raw` | TEXT / large_string | 0 | 結果表の「2000年_地域コード」（5桁） | 例 `00000`、`01000`、`01100` |
| `year_2000_raw` | TEXT / large_string | 652 | 結果表の「2000年地域」の値。意味の詳細は 未記載 | `2000` 3,430 |
| `pref_2025_raw` | TEXT / large_string | 0 | 結果表の「2025年_都道府県」 | 例 `00_全国`、`01_北海道`、`02_青森県` |
| `area_code_2025_raw` | TEXT / large_string | 0 | 結果表の「2025年_地域コード」（5桁） | 例 `00000`、`01000`、`01100` |
| `area_name_raw` | TEXT / large_string | 0 | 結果表の地域名（`連番_名前` の形。例 `0004_札幌市中央区`） | 例 `0001_全国`、`0002_北海道`、`0003_札幌市` |
| `population_total_raw` | TEXT / large_string | 0 | 結果表のセルの文字列そのまま。人口 総数 | 例 `122972528`、`4980272`、`1961996` |
| `population_male_raw` | TEXT / large_string | 0 | 結果表のセルの文字列そのまま。人口 男 | 例 `59772092`、`2355051`、`914908` |
| `population_female_raw` | TEXT / large_string | 0 | 結果表のセルの文字列そのまま。人口 女 | 例 `63200436`、`2625221`、`1047088` |
| `population_2020_rebased_raw` | TEXT / large_string | 0 | 結果表のセルの文字列そのまま。2020年の人口（組替） | 例 `126146099`、`5224614`、`1973395` |
| `population_change_5y_raw` | TEXT / large_string | 0 | 結果表のセルの文字列そのまま。5年間の人口増減数 | 例 `-3173571`、`-244342`、`-11399` |
| `population_change_rate_5y_raw` | TEXT / large_string | 0 | 結果表のセルの文字列そのまま。5年間の人口増減率（%） | 例 `-2.51579`、`-4.6767500000000002`、`-0.57762999999999998` |
| `sex_ratio_raw` | TEXT / large_string | 0 | 結果表のセルの文字列そのまま。人口性比 | 例 `94.57544`、`89.708680000000001`、`87.376419999999996` |
| `households_total_raw` | TEXT / large_string | 0 | 結果表のセルの文字列そのまま。世帯数 総数 | 例 `57324619`、`2472790`、`998412` |
| `households_general_raw` | TEXT / large_string | 0 | 結果表のセルの文字列そのまま。世帯数 一般世帯 | 例 `57181619`、`2463524`、`996291` |
| `households_institutional_raw` | TEXT / large_string | 0 | 結果表のセルの文字列そのまま。世帯数 施設等の世帯 | 例 `143000`、`9266`、`2121` |
| `households_2020_rebased_raw` | TEXT / large_string | 0 | 結果表のセルの文字列そのまま。2020年の世帯数（組替） | 例 `55830154`、`2476846`、`969161` |
| `households_change_5y_raw` | TEXT / large_string | 0 | 結果表のセルの文字列そのまま。5年間の世帯増減数 | 例 `1494465`、`-4056`、`29251` |
| `households_change_rate_5y_raw` | TEXT / large_string | 0 | 結果表のセルの文字列そのまま。5年間の世帯増減率（%） | 例 `2.6768100000000001`、`-0.16375999999999999`、`3.0181800000000001` |
| `household_members_total_raw` | TEXT / large_string | 0 | 結果表のセルの文字列そのまま。世帯人員 総数 | 例 `122972528`、`4980272`、`1961996` |
| `household_members_general_raw` | TEXT / large_string | 0 | 結果表のセルの文字列そのまま。世帯人員 一般世帯 | 例 `119837159`、`4788838`、`1900917` |
| `household_members_institutional_raw` | TEXT / large_string | 0 | 結果表のセルの文字列そのまま。世帯人員 施設等の世帯 | 例 `3135369`、`191434`、`61079` |
| `region_key` | TEXT / large_string | 0 | 地域のキー（`census_population_age` / `_age_summary` と同じ形） | 例 `a:00000:00000`、`a:01000:01000`、`1:01100:01100` |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない | 全行 `snap_estat_census_2025_populatio…` |

### 7.8 市区町村の廃置分合

#### `municipality_change_raw`

- **1行が表すもの:** e-Stat「廃置分合等情報」CSV（1970-04-20〜2024-01-01 施行分、境界変更を除く）の1行
- **主キー:** `row_ordinal`
- **行数:** 3,739
- **用途:** 発行元の行を原文のまま確認する。旧→新の辺は `municipality_lineage_edge`

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `row_ordinal` | INTEGER / int64 | 0 | CSV の行番号（1始まり）。`municipality_lineage_edge.source_row_ordinals` から参照される | 例 `1`、`2`、`3` |
| `event_pattern_raw` | TEXT / large_string | 0 | 事由パターン（原文） | `新設合併（コード追加あり）` 1,312、`編入合併` 704、`新設合併（コード追加なし）` 646、`町制施行` 262、`市制施行` 210、`郡の廃止` 199、`都市種別変更` 120、`名称変更` 92 ほか 7 種 |
| `subprefecture_county_raw` | TEXT / large_string | 0 | 支庁・振興局等内郡（原文） | 例 `幌別郡`、`茅部郡`、`幌泉郡`（空文字列 3,647 行） |
| `subprefecture_county_kana_raw` | TEXT / large_string | 0 | 同ふりがな（原文） | 例 `ほろべつぐん`、`かやべぐん`、`ほろいずみぐん`（空文字列 3,647 行） |
| `area_code_raw` | TEXT / large_string | 0 | 標準地域コード5桁（原文） | 例 `21486`、`12222`、`12304` |
| `pref_raw` | TEXT / large_string | 0 | 都道府県（原文） | 例 `岐阜県`、`千葉県`、`東京都` |
| `parent_raw` | TEXT / large_string | 0 | 政令市・郡・支庁・振興局等（原文） | 例 `郡上郡`、`東葛飾郡`、`西多摩郡`（空文字列 879 行） |
| `parent_kana_raw` | TEXT / large_string | 0 | 同ふりがな（原文） | 例 `ぐじょうぐん`、`ひがしかつしかぐん`、`にしたまぐん`（空文字列 879 行） |
| `municipality_raw` | TEXT / large_string | 0 | 市区町村（原文） | 例 `明方村`、`奥明方村`、`我孫子市`（空文字列 249 行） |
| `municipality_kana_raw` | TEXT / large_string | 0 | 同ふりがな（原文） | 例 `みょうがたむら`、`おくみょうがたむら`、`あびこし`（空文字列 249 行） |
| `effective_date_raw` | TEXT / large_string | 0 | 廃置分合等施行年月日（原文、`YYYY-MM-DD`） | 例 `1970-04-20`、`1970-07-01`、`1970-08-01` |
| `reason_raw` | TEXT / large_string | 0 | 改正事由（原文）。旧→新のコードは本文中の「名称(5桁コード)」から読む | 例 `奥明方村(21486)が明方村(21486)に名称変更`、`我孫子町(12304)が我孫子市(12222)に市制施行`、`福生町(13301)が福生市(13218)に市制施行` |
| `notice_date_raw` | TEXT / large_string | 0 | 告示年月日（原文。32行が空文字列） | 例 `1970-04-11`、`1970-06-25`、`1970-05-09`（空文字列 32 行） |
| `notice_number_raw` | TEXT / large_string | 0 | 告示番号（原文） | 例 `80`、`112`、`113`（空文字列 32 行） |
| `row_sha256` | TEXT / large_string | 0 | 13列の原文から作ったハッシュ（全列が同じ行が2組あるため主キーではない） | 例 `0eb13804a57b710728cab8a5f699…`、`3481e0d33276c6e852d513d0130c…`、`b92b47c28df65771a7922c110fe8…` |
| `hs_flag` | TEXT / large_string | 398 | 同じ行が e-Stat の新設で絞った CSV にあれば `S`、廃止で絞った CSV にあれば `H`、両方なら `SH`、どちらにも無ければ NULL | `H` 2,432、`S` 905、`SH` 4 |
| `parse_status` | TEXT / large_string | 0 | `parsed` 規則で読めた ／ `unresolved` 推測しないと読めない節がある（その行の他の節からは辺が出ることがある） | `parsed` 3,729、`unresolved` 10 |
| `unresolved_reason` | TEXT / large_string | 3,729 | 読めなかった理由（日本語）。`parsed` なら NULL | `郡の区域変更は旧と新を位置でしか対にできない: 鳳至郡穴…` 6、`旧側を「名称(コード)」の並びとして読めない: 中道町(…` 2、`旧側を「名称(コード)」の並びとして読めない: 上九一色…` 2 |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない（3ファイルの束 `snapset_…`） | 全行 `snapset_b8daddd9f2265b26effb` |

#### `municipality_lineage_edge`

- **1行が表すもの:** 廃置分合の旧コード → 新コードの辺1本（施行日ごと）
- **主キー:** `edge_id`
- **行数:** 2,528
- **用途:** 古いデータに残る市区町村コードを現行のコードに読み替える。1段ずつの辺なので、何度も変わったコードは施行日の順につなぐ

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `effective_date` | TEXT / large_string | 0 | 施行日 | 例 `2006-02-20`、`2005-09-01`、`2005-10-01` |
| `old_jis_code` | TEXT / large_string | 0 | 旧コード5桁 | 例 `10208`、`17384`、`11403` |
| `new_jis_code` | TEXT / large_string | 0 | 新コード5桁 | 例 `10208`、`17384`、`11202` |
| `event_type` | TEXT / large_string | 0 | 辺の種類。`merge` 新設合併 ／ `merge_reuse_code` 新設合併で旧コードを引き継ぐ ／ `absorb` 編入 ／ `status_change` 市制・町制施行 ／ `designated_city` 政令市への移行 ／ `designated_city_ward` 政令市化で区ができる（旧市→各区）／ `separation` 分離 ／ `split` 分割 ／ `rename` 名称変更 ／ `county_change` 郡の区域変更 ／ `ward_reorg` 区の再編 ／ `continues` 分離の後も旧コードが存続する（旧→旧） | `merge` 1,013、`merge_reuse_code` 646、`absorb` 444、`status_change` 246、`designated_city_ward` 81、`separation` 23、`rename` 21、`continues` 19 ほか 4 種 |
| `area_qualifier_raw` | TEXT / large_string | 2,417 | 区域の注記。発行元の文（「三方原地区以外」など）か、jpac が付けた定型句（`区域の一部`）。無ければ NULL | `区域の一部` 108、`区名、区域の変更なし` 1、`三方原地区以外` 1、`三方原地区/初生町、三方原町、東三方町、豊岡町、三幸町、…` 1 |
| `parse_rule_id` | TEXT / large_string | 0 | 改正事由を読んだ規則の ID（`C_…`） | `C_merge` 1,659、`C_absorb` 444、`C_status` 224、`C_designated_wards` 81、`C_separate` 23、`C_rename` 21、`C_continues` 19、`C_designated` 14 ほか 5 種 |
| `source_row_ordinals` | TEXT / large_string | 0 | この辺の出どころの `municipality_change_raw.row_ordinal` を `;` 区切りで並べたもの（例 `2420;2421`） | 例 `3021;3022;3023;3024;3025;302…`、`2420;2421`、`2511;2520;2521` |
| `candidate_count` | INTEGER / int64 | 0 | 同じ施行日・同じ旧コードから出る行き先の数（`continues` を含む） | `1` 2,383、`2` 38、`8` 32、`6` 24、`3` 12、`10` 10、`9` 9、`4` 8 ほか 2 種 |
| `old_lg_code` | TEXT / large_string | 0 | 旧コードに検査数字を付けた6桁。**現行の市区町村とは限らない**（現行は 287 本）。外部キーは宣言されていない | 例 `102083`、`173843`、`114031` |
| `new_lg_code` | TEXT / large_string | 0 | 新コードの6桁。現行の市区町村なら `municipality.lg_code` に一致する（2,310 本）。外部キーは宣言されていない | 例 `102083`、`173843`、`112020` |
| `is_unique_match` | INTEGER / bool | 0 | 行き先が1つなら 1（`candidate_count = 1`） | `1` 2,383、`0` 145 |
| `edge_id` | TEXT / large_string | 0 | 辺の ID（`mle_…`、施行日・旧・新・種類・注記から作る） | 例 `mle_0025a9aa0dfb01ccfe155071`、`mle_0052ef61cdba39b6dbbfaa5a`、`mle_009099d204bd1ca910a050a8` |
| `source_snapshot_id` | TEXT / large_string | 0 | この行の出どころとなった元データ（`source_snapshot.source_snapshot_id`）。外部キーは宣言されていない（3ファイルの束 `snapset_…`） | 全行 `snapset_b8daddd9f2265b26effb` |

### 7.9 学校（文部科学省 学校コード）

**v2.3.0 で追加した3表です。** 数値は令和7年5月1日時点（確定版）の一覧を照合した `v2.3.0` の実測です。

学校の点は座標アセットの [`school_point`](#school_point座標アセット)（国土数値情報 P29 2023年度版）にあります。

学校コードは文部科学省が全国の学校に1つずつ振った13桁のコードで、学校種（2桁）・都道府県番号（2桁）・
設置区分（1桁）・学校番号（7桁）・検査数字（1桁）からなります。一覧は**座標を持たず**、所在地を文字列で
持っています。jpac はその文字列を町字に照合して結びます（位置は使いません）。

#### `mext_school`

- **1行が表すもの:** 学校コード一覧の1校（廃止した学校を含む）
- **主キー:** `school_code`
- **行数:** 59,738（今ある学校 55,862、廃止 3,876）
- **用途:** 学校の種別・設置者・名前・所在地。町字・市区町村は `bridge_school_address` から引く

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `school_code` | TEXT / large_string | 0 | 学校コード13桁。一度振られたら変わらず、廃止しても他校に流用されない | 例 `A101110000012`、`B111210000013` |
| `school_type_code` | TEXT / large_string | 0 | 学校種のコード（コードの1〜2桁目と同じ）。`A1` 幼稚園（幼稚園型認定こども園を含む）／ `A2` 幼保連携型認定こども園 ／ `B1` 小学校 ／ `C1` 中学校 ／ `C2` 義務教育学校 ／ `D1` 高等学校 ／ `D2` 中等教育学校 ／ `E1` 特別支援学校 ／ `F1` 大学 ／ `F2` 短期大学 ／ `G1` 高等専門学校 ／ `H1` 専修学校 ／ `H2` 各種学校 | `B1` 19,725、`C1` 10,271、`A1` 9,741、`A2` 7,771、`D1` 5,093、`H1` 3,244、`E1` 1,212、`H2` 1,138 ほか 5 種 |
| `school_type_name` | TEXT / large_string | 0 | 学校種の名前（発行元の略称のまま） | `小学校` 19,725、`中学校` 10,271、`幼稚園` 9,741、`こども` 7,771、`高校` 5,093、`専修` 3,244、`特支` 1,212、`各種` 1,138 ほか 5 種 |
| `pref_code` | TEXT / large_string | 0 | 都道府県番号2桁（コードの3〜4桁目と同じ） | `13` 4,507、`27` 3,551、`01` 2,998 ほか 44 種 |
| `founder_code` | TEXT / large_string | 0 | 設置区分（コードの5桁目と同じ）。`1` 国立 ／ `2` 公立 ／ `3` 私立 | `2` 38,573、`3` 20,760、`1` 405 |
| `founder_name` | TEXT / large_string | 0 | 設置区分の名前（発行元の略称） | `公` 38,573、`私` 20,760、`国` 405 |
| `branch_code` | TEXT / large_string | 0 | 本分校。`1` 本校 ／ `2` 分校 ／ `9` 廃校 | `1` 55,248、`9` 3,846、`2` 644 |
| `branch_name` | TEXT / large_string | 0 | 本分校の名前 | `本` 55,248、`廃` 3,846、`分` 644 |
| `school_name_raw` | TEXT / large_string | 0 | 学校名（原文） | 例 `北海道教育大学附属函館幼稚園`、`札幌市立手稲中央幼稚園` |
| `address_raw` | TEXT / large_string | 0 | 所在地（原文。全角数字のまま）。都道府県名が無い行が3行、所在地が2つ書かれた行が1行ある | 例 `北海道函館市美原３丁目４８番６号` |
| `postal_code_raw` | TEXT / large_string | 0 | 郵便番号（原文）。**7桁でない値や、郵便番号の表に無い番号（大学などの事業所個別番号）がある**ので、`postal_code_entity` への外部キーにしていない | 例 `0410806`、`0700875` |
| `attribute_set_date` | TEXT / large_string | 0 | 属性情報設定年月日（`YYYY-MM-DD` に直した値） | `2020-12-22` 55,752、`2021-01-20` 1,183 ほか |
| `attribute_abolished_date` | TEXT / large_string | 55,862 | 属性情報廃止年月日（`YYYY-MM-DD`）。今ある学校は NULL | `2025-05-01` 823、`2022-05-01` 788、`2021-05-27` 776 ほか |
| `old_survey_number_raw` | TEXT / large_string | 2,186 | 旧学校調査番号（原文） | 例 `016501`、`016502` |
| `successor_school_codes_raw` | TEXT / large_string | 57,949 | 移行後の学校コード（原文。複数のときは `、` `,` 改行などで区切られている）。分けたものが `mext_school_succession` | 例 `B103220900301` |
| `is_active` | INTEGER / bool | 0 | 今ある学校なら 1（本分校が `9` でなく、廃止日が無い） | `1` 55,862、`0` 3,876 |
| `check_digit_valid` | INTEGER / bool | 0 | 末尾の検査数字が、発行元の計算方法と合えば 1。**学校種などが変わった学校は元の種別の検査数字を持ち続けていて 0 になる**（幼稚園 → 認定こども園など）。発行元のコードなので直していない | `1` 59,575、`0` 163 |
| `source_snapshot_id` | TEXT / large_string | 0 | この行が載っていた CSV（3本のどれか）の `source_snapshot_id`。外部キーは宣言されていない | `snap_mext_school_code_east_…` 34,163、`snap_mext_school_code_west_…` 24,355、`snap_mext_school_code_university_…` 1,220 |

#### `mext_school_succession`

- **1行が表すもの:** 廃止した学校 → 移行後の学校の1組
- **主キー:** `(school_code, successor_school_code)`
- **行数:** 1,797（移行元 1,789校）
- **用途:** 統合・分離・種別の変更で学校コードが変わった学校をたどる。1校が複数の学校に移ることがある

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `school_code` | TEXT / large_string | 0 | 移行元（廃止した学校）→ `mext_school.school_code`（外部キーは宣言していない） | 例 `B101222900050` |
| `successor_school_code` | TEXT / large_string | 0 | 移行先の学校コード | 例 `B101222900014` |
| `successor_listed` | INTEGER / bool | 0 | 移行先が一覧にあれば 1。**無いことがある**ので外部キーにしていない | `1` 1,793、`0` 4 |
| `source_snapshot_id` | TEXT / large_string | 0 | 移行元の行が載っていた CSV | |

#### `bridge_school_address`

- **1行が表すもの:** 学校1校 × 町字の候補1件。町字まで決まらない学校も1行持つ
- **主キー:** 宣言なし（`(school_code, address_id)` で並べて出力）
- **行数:** 59,952（59,738校）
- **用途:** 学校 → 町字・市区町村。所在地の文字列を、町字マスターの名前に照合して結ぶ

照合の規則（`src/jp_address_crosswalk/build/school.py`）:

1. 市区町村は、学校の都道府県の中で、所在地の先頭に最も長く一致する市区町村名で決める（郡名は省いてもよい）。
   同じ名前の候補が複数なら決めない。政令市の市名で止まっていれば、その区の町字をまとめて探す。
2. 町字は、残りの先頭に最も長く一致する町字名で決める。**ただし町字名の直後が番地（数字・番地・字など）で
   なければ結ばない。**「小野田２２番地」を名前の短い別の町字「小野」に結ばないためです。`4-6-27` の先頭の
   数字は、その町字に「4丁目」があれば丁目として読む。先頭の「大字」「字」は省いてもよい。
3. 表記の正規化は住所の照合と同じ保守的なもので、ヶ／ケの違い・「字」の省略・京都の通り名などは結ばずに
   市区町村どまりにする。
4. 郵便番号は照合に使わない。結んだ後で、学校の郵便番号がその町字の郵便番号（`bridge_address_postal_code`）に
   含まれるかを `postal_check` に記録する。

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `school_code` | TEXT / large_string | 0 | → `mext_school.school_code`（宣言 FK） | 例 `A101110000012` |
| `address_id` | TEXT / large_string | 2,914 | 町字 → `address_entity.address_id`（宣言 FK）。`located_in` と `ambiguous` の行だけ値がある | 例 `jpa16tgz98s6dm2q7sxe` |
| `lg_code` | TEXT / large_string | 24 | 市区町村 → `municipality.lg_code`（宣言 FK）。**常に区・市町村のコードで、政令市の親コードは入らない**。`unresolved` なら NULL | 例 `221384`、`462012` |
| `relation_type` | TEXT / large_string | 0 | `located_in` 町字が1つに決まった ／ `ambiguous` 同じ名前の町字が複数（候補ごとに1行）／ `municipality_only` 市区町村までしか決まらない ／ `unresolved` 市区町村も決まらない | `located_in` 56,610、`municipality_only` 2,890、`ambiguous` 428、`unresolved` 24 |
| `match_method` | TEXT / large_string | 0 | `address_text_prefix` 町字名で結んだ ／ `municipality_text_prefix` 市区町村名まで ／ `unresolved` | `address_text_prefix` 57,038、`municipality_text_prefix` 2,890、`unresolved` 24 |
| `matched_town_name` | TEXT / large_string | 2,914 | 一致した町字名（正規化後。先頭の「大字」「字」を省いた形のことがある） | 例 `美原3丁目`、`春光5条2丁目` |
| `confidence` | REAL / double | 0 | 規則で決まる値（確率ではない）。町字が1つで郵便番号も合う 0.98 ／ 町字が1つで郵便番号で確かめられない 0.9 ／ 市区町村まで 0.9 ／ 町字の候補が複数 0.5 ／ 未解決 0.0 | `0.98` 54,154、`0.9` 5,346、`0.5` 428、`0.0` 24 |
| `candidate_count` | INTEGER / int64 | 0 | 町字の候補の数。町字まで決まらなければ 0 | `1` 56,610、`0` 2,914、`2` 428 |
| `is_unique_match` | INTEGER / int64 | 0 | 町字の候補が1つなら 1 | `1` 56,610、`0` 3,342 |
| `postal_check` | TEXT / large_string | 2,914 | `agrees` 学校の郵便番号が町字の郵便番号の1つ ／ `differs` 違う（大学などの事業所個別番号のことが多い）／ `town_has_no_postal_code` 町字に郵便番号の対応が無い ／ NULL 町字が無いか、学校の郵便番号が7桁でない | `agrees` 54,552、`differs` 2,145、`town_has_no_postal_code` 341 |
| `verification_status` | TEXT / large_string | 0 | `auto` 町字が1つに決まり、郵便番号も合う ／ `review_required` それ以外 | `auto` 54,153、`review_required` 5,799 |
| `mismatch_note` | TEXT / large_string | 54,153 | `auto` でない理由（日本語）。`auto` なら NULL | `町字まで決まらない（表記の違い・町字の無い地域など）` 2,890、`学校の郵便番号が町字の郵便番号と違う（…）` 2,133、`同じ名前の町字が複数ある。候補を残す（…）` 428、`町字に郵便番号の対応が無く、…` 323、`所在地から市区町村が決まらない` 24、`所在地が複数書かれている。…` 1 |
| `source_snapshot_id` | TEXT / large_string | 0 | 学校の行が載っていた CSV | |

今ある学校 55,862 校で数えると、町字に1つに決まった学校が 94.85%、町字の候補が複数 0.34%、市区町村どまり
4.77%、市区町村も決まらない 0.04% です。

#### `school_point`（座標アセット）

- **入っているファイル:** `jp_address_crosswalk_points.zip` の `school_point.parquet`（v2.3.0 で追加）。**SQLite・テーブル別 zip には無い**
- **1行が表すもの:** 国土数値情報 学校データ（P29 2023年度版）の1点 = 学校1校 × キャンパス1つ
- **主キー:** `(school_code, campus_code)`
- **行数:** 56,807（56,318校。キャンパスが複数ある学校は 258校）。座標が NULL の行 0
- **用途:** 学校を地図に載せる。P29 が公表している点を**計算も丸めもせずに**出している。町字（`bridge_school_address`）は所在地の文字列から決めたもので、この点からは決めていない
- **測地系:** JGD2011 の経緯度（EPSG:6668）。範囲は経度 122.943…〜145.750…、緯度 24.061…〜45.513…（実測）
- **注意:** P29 は令和5年（2023年）5月1日時点の一覧から作られている。今ある学校（令和7年の一覧）55,862校のうち点があるのは 54,776校（98.1%）で、無いのは2023年以降にできた学校が中心（認定こども園 729校など）。P29 の原典資料には地理院地図（測量法に基づく国土地理院長承認（使用）R 3JHs 917）が含まれる

| 列名 | 型（Parquet / Snowflake 推奨） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `school_code` | large_string / `VARCHAR` | 0 | 学校コード → `mext_school.school_code`（56,807 点の学校コードはすべて令和7年の一覧にある。廃止になった学校もある） | 例 `A101110000012` |
| `campus_code` | large_string / `VARCHAR` | 0 | キャンパスコード。`00` キャンパスの区別なし ／ `01` 本校 ／ `02` 以降 それ以外のキャンパス | `00` 56,021、`01` 297、`02` 258、`03` 105、`04` 50 ほか |
| `school_name_raw` | large_string / `VARCHAR` | 0 | P29 の名称（`mext_school.school_name_raw` と違うことがある） | 例 `北海道教育大学附属函館幼稚園` |
| `campus_note_raw` | large_string / `VARCHAR` | 56,109 | 学校名備考（キャンパスの名称等）。無ければ NULL | 例 `原峠分室`、`女子部` |
| `p29_admin_code` | large_string / `VARCHAR` | 0 | P29 作成時点の行政区域コード5桁。jpac の `lg_code`（6桁）ではない | 例 `01202` |
| `closed_code_raw` | large_string / `VARCHAR` | 0 | 休校区分。`0` 調査なし ／ `1` 開校中 ／ `2` 休校中。`9` はコード表に無いが発行元の値のまま | `1` 28,932、`0` 27,673、`9` 202 |
| `longitude` | double / `FLOAT` | 0 | 経度（発行元の値のまま） | 例 `140.7481043400378` |
| `latitude` | double / `FLOAT` | 0 | 緯度（発行元の値のまま） | 例 `41.82050309831556` |
| `source_snapshot_id` | large_string / `VARCHAR` | 0 | 元データ（P29 の全国ファイル） | 全行 `snap_mlit_ksj_p29_P29-23_GML_…` |

```sql
-- 例（DuckDB）: 今ある小学校の点と、所在地から決めた町字
SELECT s.school_code, s.school_name_raw, p.campus_code, p.longitude, p.latitude, b.address_id
FROM 'tables/mext_school.parquet' s
JOIN 'points/school_point.parquet' p USING (school_code)
LEFT JOIN 'tables/bridge_school_address.parquet' b
       ON b.school_code = s.school_code AND b.relation_type = 'located_in'
WHERE s.is_active AND s.school_type_code = 'B1';
```

### 7.10 来歴

#### `source_snapshot`

- **1行が表すもの:** 取り込んだ元データのファイル1本（または複数ファイルの束）
- **主キー:** `source_snapshot_id`
- **行数:** 126
- **用途:** どの表の行がどのファイルから来たかを引く。全表の `source_snapshot_id` がここを指す（宣言 FK は無いが、v2.3.0 では全値が存在する）

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `source_snapshot_id` | TEXT / large_string | 0 | ID。`snap_{データセット名}_{ハッシュ}`、束は `snapset_…` | 例 `snap_abr_city_master_419f6bc…`、`snap_abr_postal_conversion_c…`、`snap_abr_pref_master_92bbbae…` |
| `provider` | TEXT / large_string | 0 | 発行元 | `総務省統計局` 54、`国土交通省` 50、`総務省` 11、`デジタル庁` 4、`日本郵便株式会社` 3、`文部科学省` 3、`国土交通省 国土政策局 国土情報課` 1 |
| `dataset_name` | TEXT / large_string | 0 | データセット名（jpac の内部名。例 `abr_town_master`、`japanpost_ken_all`）。束は `…__aggregate_of_N` | 例 `abr_city_master`、`abr_postal_conversion`、`abr_pref_master` |
| `source_page_url` | TEXT / large_string | 0 | 発行元のページの URL | `https://www.e-stat.go.jp/gis…` 48、`https://nlftp.mlit.go.jp/isj…` 48、`https://www.soumu.go.jp/main…` 10、`https://www.e-stat.go.jp/mun…` 4、`https://www.digital.go.jp/po…` 4、`https://www.post.japanpost.j…` 3、`https://www.mext.go.jp/b_men…` 3、`https://www.e-stat.go.jp/sta…` 2 ほか 4 種 |
| `download_url` | TEXT / large_string | 0 | 取得元。リリースでは `local-payload:raw/…`（受け入れ済みのファイルの場所） | 例 `local-payload:raw/abr/city_m…`、`local-payload:raw/abr/postal…`、`local-payload:raw/abr/pref_m…` |
| `license_name` | TEXT / large_string | 0 | 適用される規約の名前 | `公共データ利用規約（第1.0版） (PDL 1.0)` 66、`政府統計の総合窓口(e-Stat)利用規約（政府標準利用…` 54、`著作権主張なし / 自由配布可 (publisher s…` 3、`文部科学省ウェブサイト利用規約（公共データ利用規約 第1…` 3 |
| `license_url` | TEXT / large_string | 0 | 規約の URL | `https://www.e-stat.go.jp/ter…` 54、`https://nlftp.mlit.go.jp/ksj…` 51、`https://www.soumu.go.jp/menu…` 11、`https://www.digital.go.jp/po…` 4、`https://www.post.japanpost.j…` 3、`https://www.mext.go.jp/b_men…` 3 |
| `license_text_sha256` | TEXT / null | 126 | このビルドで観測した規約本文のハッシュ。v2.3.0 は全行 NULL（オフラインのビルドで規約ページを読まないため） | 全行 NULL |
| `source_version` | TEXT / large_string | 72 | 発行元が示す版 | `A002005212020 / JGD2011` 48、`令和7年5月1日時点（確定版）` 3、`2025` 1、`2023` 1、`2022` 1 |
| `published_at` | TEXT / large_string | 64 | 発行元が示す公表日・基準日 | `2020-10-01` 48、`2024-09-01` 10、`2025-12-26` 3、`2025-12-31` 1 |
| `edition_origin` | TEXT / large_string | 62 | 版の情報の出どころ（`declared` 設定で宣言 ／ `payload` ファイル自身が述べる ／ NULL） | `declared` 54、`payload` 10 |
| `downloaded_at` | TEXT / large_string | 0 | 取得日時（RFC 3339） | 例 `2026-08-23T03:22:24+09:00`、`2026-08-23T03:22:26+09:00`、`2026-09-05T12:32:04+09:00` |
| `etag` | TEXT / null | 126 | HTTP ETag。v2.3.0 は全行 NULL | 全行 NULL |
| `last_modified` | TEXT / null | 126 | HTTP Last-Modified。v2.3.0 は全行 NULL | 全行 NULL |
| `sha256` | TEXT / large_string | 0 | ファイルの SHA-256 | 例 `c719e7394489907c6192f58837d7…`、`febff4aabc2cd99b35ff395855b8…`、`abbd87846872ce6688c569d9aa33…` |
| `file_size` | INTEGER / int64 | 0 | ファイルサイズ（バイト） | 例 `49179`、`6374347`、`1238` |
| `row_count` | INTEGER / int64 | 0 | ファイルの行数 | 例 `1918`、`685105`、`47` |
| `schema_fingerprint` | TEXT / large_string | 0 | 列構成のハッシュ（変わればビルドが止まる） | `7fa522e5f155846be56b4f46f0c1…` 48、`be3e33c3d50dd3324971431bc5d6…` 28、`b60c89dc0754d84e5a472a039271…` 19、`a2c6620218583c5b8e74845dfc56…` 10、`f69e7f12fb1a8f65d5270156b24f…` 4、`148c2a7e1b2656eb5ca0d211f18e…` 3、`f16367f7fd1b5d50124213af6f89…` 1、`bd5d0fcaf642625a1f365a010445…` 1 ほか 12 種 |
| `parser_version` | TEXT / large_string | 0 | 読み込み処理の版 | 全行 `1.0.0` |
| `resolved_via` | TEXT / large_string | 0 | ファイルの特定方法（`discovery`） | 全行 `discovery` |
| `status` | TEXT / large_string | 0 | `ok` ／ `aggregate`（複数ファイルの束） | `ok` 122、`aggregate` 4 |

#### `snapshot_license_artifact`

- **1行が表すもの:** 元データ1種類に関わる規約文書1つ（規約本文、詳細ページ、ダウンロード時の同意事項など）
- **主キー:** 宣言なし（`artifact_id` が全行で一意）
- **行数:** 24
- **用途:** どの規約文書を、いつ人が確認したかの記録

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `artifact_id` | TEXT / large_string | 0 | 記録の ID（`lic_…`） | `lic_feb38f9b8e66494e` 1、`lic_f034322f793f5b43` 1、`lic_e6a0bb0515d832d4` 1、`lic_dbdee6a6c95e16a6` 1、`lic_d8ee45e90d8cb81c` 1、`lic_cecf549961d035ba` 1、`lic_c7f19d2d7cf30c89` 1、`lic_c2e6f57e2d0178be` 1 ほか 16 種 |
| `source` | TEXT / large_string | 0 | 元データの種類（jpac の内部名） | `estat_boundary` 5、`mlit` 3、`abr` 3、`mlit_ksj_p29` 2、`mlit_ksj_p11` 2、`mlit_ksj_n02` 2、`mext_school_code` 2、`mic_number_assignment` 1 ほか 4 種 |
| `role` | TEXT / large_string | 0 | その文書の役割（`primary_terms` 主たる規約 など） | `primary_terms` 12、`detail_page` 3、`superseded_terms` 1、`policy_page` 1、`gis_terms` 1、`download_stipulation` 1、`definition_pdf` 1、`dataset_page` 1 ほか 3 種 |
| `license_name` | TEXT / large_string | 0 | 文書の名前 | `国土数値情報ダウンロードサイト 利用規約 (2026-0…` 4、`政府統計の総合窓口(e-Stat) 利用規約` 3、`総務省ホームページ 著作権について（PDL 1.0 採用…` 2、`郵便番号データの説明 (readme)` 1、`統計地理情報システム機能利用規約（第6条でコンテンツの利…` 1、`文部科学省ウェブサイト利用規約` 1、`文部科学省 学校コード（掲載ページ）` 1、`境界データ 注意事項（国勢調査）` 1 ほか 10 種 |
| `license_url` | TEXT / large_string | 0 | 文書の URL | `https://nlftp.mlit.go.jp/ksj…` 4、`https://www.e-stat.go.jp/ter…` 3、`https://www.soumu.go.jp/menu…` 2、`https://www.post.japanpost.j…` 1、`https://www.mext.go.jp/b_men…` 1、`https://www.mext.go.jp/b_men…` 1、`https://www.geospatial.jp/ck…` 1、`https://www.e-stat.go.jp/pdf…` 1 ほか 10 種 |
| `text_sha256` | TEXT / large_string | 24 | このビルドで観測した本文のハッシュ（v2.3.0 は全行 NULL） | 全行 NULL |
| `baseline_sha256` | TEXT / large_string | 5 | 人が確認した時点の本文のハッシュ | `93c44dc2fc4de2f33789c71ca80b…` 3、`4c44671c4183abdad0fa8f117b98…` 3、`e885876a039a4d93a54c852bef99…` 2、`e980fada707098bae32b48a6c170…` 1、`e6dda47382bb34da7e78ad3aec25…` 1、`d069b147966c9b7d32084b5b8b3d…` 1、`b3e62d5b3ff0047bba2478aa5758…` 1、`acd5205988866f65b29d0720c5bc…` 1 ほか 6 種 |
| `reviewed_on` | TEXT / large_string | 0 | 人が確認した日 | `2026-08-24` 9、`2026-09-14` 7、`2026-10-15` 4、`2026-09-17` 2、`2026-10-12` 1、`2026-09-27` 1 |
| `review_decision` | TEXT / large_string | 0 | `not_observed` 確認済みの基準があり今回は観測していない ／ `not_gated` 基準のハッシュを持たない（証拠として記録） | `not_observed` 19、`not_gated` 5 |
| `note` | TEXT / large_string | 6 | 補足 | `版ごとの利用許諾条件はこのページにあり、agreemen…` 1、`版ごとの利用許諾条件と、原典資料（地理院地図と承認番号 …` 1、`ゲートにしない記録。ページは版ごとに書き換わる。規約の文…` 1、`mlit（位置参照情報）および mlit_ksj_n02…` 1、`mlit・mlit_ksj_n02・mlit_ksj_p…` 1、`estat_boundary と同じページ・同じハッシュ…` 1、`estat_boundary と同じページ・同じハッシュ…` 1、`The per-layer 利用許諾条件 lives h…` 1 ほか 10 種 |
| `source_snapshot_id` | TEXT / large_string | 0 | → `source_snapshot.source_snapshot_id`（宣言なしの論理結合。24行すべて存在） | `snap_estat_boundary_pref_01_…` 5、`snap_mlit_isj_01_c1214a2a31e…` 3、`snap_abr_city_master_419f6bc…` 3、`snap_mlit_ksj_p29_P29-23_GML…` 2、`snap_mlit_ksj_p11_P11-22_SHP…` 2、`snap_mlit_ksj_n02_n02_2025_1…` 2、`snap_mext_school_code_east_7…` 2、`snap_mic_shigai_list_7e020a7…` 1 ほか 4 種 |

#### `match_run`

- **1行が表すもの:** 照合の実行1回
- **主キー:** `match_run_id`
- **行数:** 1
- **用途:** ブリッジ行を作った実行の版情報

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `match_run_id` | TEXT / large_string | 0 | 実行の ID（`run_…`、入力と版から作る） | 全行 `run_f03e7d9008d287dfc16b` |
| `started_at` | TEXT / large_string | 0 | 実行の日時（RFC 3339） | 全行 `2026-10-15T22:21:32+09:00` |
| `matching_rule_version` | TEXT / large_string | 0 | 照合規則の版 | 全行 `2.0.0` |
| `normalization_profile_version` | TEXT / large_string | 0 | 正規化プロファイルの版 | 全行 `1.0.0` |
| `code_version` | TEXT / large_string | 0 | jpac のコードの版 | 全行 `2.3.0` |

#### `match_run_input`

- **1行が表すもの:** 照合の実行1回 × 読んだ元データ1つ
- **主キー:** 宣言なし（論理キーは `(match_run_id, source_snapshot_id, role)`）
- **行数:** 122
- **用途:** ブリッジ行が依存するすべての元データを引く

| 列名 | 型（SQLite / Parquet） | NULL 行数 | 説明 | 値の例・取りうる値（実測） |
|---|---|---:|---|---|
| `match_run_id` | TEXT / large_string | 0 | → `match_run.match_run_id`（宣言 FK） | 全行 `run_f03e7d9008d287dfc16b` |
| `source_snapshot_id` | TEXT / large_string | 0 | → `source_snapshot.source_snapshot_id`（宣言なしの論理結合。118行すべて存在） | 例 `snap_abr_city_master_419f6bc…`、`snap_abr_postal_conversion_c…`、`snap_abr_pref_master_92bbbae…` |
| `role` | TEXT / large_string | 0 | その元データの役割。`canonical` 基準（ABR 町字・市区町村）／ `mapping` 対応表（ABR 変換表）／ `corroboration` 裏付け（日本郵便）／ `target` 対応先 | `target` 116、`corroboration` 3、`canonical` 2、`mapping` 1 |

### 7.11 ビュー

SQLite にだけあるビューです（Parquet / CSV のフラット表は `address_crosswalk` と同じ内容を実体化したもの）。

| ビュー | 中身 | 行数（v2.3.0） |
|---|---|---:|
| `address_crosswalk` | フラット表。各ブリッジを `is_current = 1 AND verification_status <> 'manually_rejected'` で絞ってから結合 | 727,110 |
| `address_crosswalk_all` | 同じ列で、ブリッジを絞らずに結合（棄却済み・過去の版も含む） | 727,110 |
| `unmatched_records` | 対応が付かなかったレコードの一覧 | 576,213 |

#### `address_crosswalk` と `address_crosswalk_all`

- **1行 = 住所1件 × 対応する郵便番号（`bridge_address_postal_code`）× 対応する国交省レコード（`bridge_address_mlit`）
  × 番号区画（`bridge_address_telephone`）の組み合わせ。** 対応が複数ある住所はその数だけ行に展開されます
  （726,170 住所に 727,110 行）。対応の無い住所も必ず1行出て、相手側の列が NULL になります。
- **一意キーはありません。** `address_id` は重複します。住所の件数は `COUNT(DISTINCT address_id)` で数えます。
- 絞り込み（棄却の除外）はブリッジを結合する**前**に行うので、ブリッジ行が棄却されても住所の行は消えず、
  その系統の列が NULL になります。
- 列は `address`（9列）、郵便番号の系統（`bridge_address_postal_code` から10列）、国交省の系統
  （`bridge_address_mlit` と `mlit_town_version` から11列）、市外局番の系統（`bridge_address_telephone` と
  `telephone_area_version` から13列）の計43列です。

| 列 | 出どころ | 型（SQLite の値 / Parquet） | 備考 |
|---|---|---|---|
| `address_id` | `address.address_id` | TEXT / large_string | |
| `lg_code` | `address.lg_code` | TEXT / large_string | |
| `jis_city_code` | `address.jis_city_code` | TEXT / large_string | |
| `pref_name` | `address.pref` | TEXT / large_string | |
| `city_name` | `address.city` | TEXT / large_string | |
| `ward_name` | `address.ward` | TEXT / large_string | 政令市の区でなければ NULL |
| `town_name` | `address.full_name_raw` | TEXT / large_string | ABR の表記のまま |
| `town_name_normalized` | `address.full_name_normalized` | TEXT / large_string | |
| `machiaza_id` | `address.machiaza_id` | TEXT / large_string | |
| `postal_code` | `bridge_address_postal_code.postal_code` | TEXT / large_string | 対応が無ければ NULL |
| `postal_relation_type` | 同 `relation_type` | TEXT / large_string | v2.3.0 は `equivalent` か NULL |
| `postal_match_method` | 同 `match_method` | TEXT / large_string | |
| `postal_rule` | 同 `matching_rule_id` | TEXT / large_string | |
| `postal_confidence` | 同 `confidence` | REAL / double | |
| `postal_candidate_count` | 同 `candidate_count` | INTEGER / int64 | |
| `postal_candidate_group` | 同 `candidate_group_id` | TEXT / large_string | |
| `postal_is_unique` | 同 `is_unique_match` | INTEGER / bool | |
| `postal_status` | 同 `verification_status` | TEXT / large_string | `auto` なら自動確定 |
| `mlit_relation_type` | `bridge_address_mlit.relation_type` | TEXT / large_string | |
| `mlit_match_method` | 同 `match_method` | TEXT / large_string | |
| `mlit_rule` | 同 `matching_rule_id` | TEXT / large_string | |
| `mlit_confidence` | 同 `confidence` | REAL / double | |
| `mlit_candidate_count` | 同 `candidate_count` | INTEGER / int64 | |
| `mlit_candidate_group` | 同 `candidate_group_id` | TEXT / large_string | |
| `mlit_is_unique` | 同 `is_unique_match` | INTEGER / bool | |
| `mlit_status` | 同 `verification_status` | TEXT / large_string | |
| `numbering_area_code` | `bridge_address_telephone.numbering_area_code` | TEXT / large_string | v2.3.0 は全行 NULL |
| `telephone_relation_type` | 同 `relation_type` | TEXT / large_string | 全行 `unresolved` |
| `telephone_match_method` | 同 `match_method` | TEXT / large_string | 全行 `unresolved` |
| `telephone_rule` | 同 `matching_rule_id` | TEXT / large_string | 全行 `T10` |
| `telephone_confidence` | 同 `confidence` | REAL / double | 全行 0.0 |
| `telephone_candidate_count` | 同 `candidate_count` | INTEGER / int64 | 全行 0 |
| `telephone_candidate_group` | 同 `candidate_group_id` | TEXT / large_string | 全行 NULL |
| `telephone_is_unique` | 同 `is_unique_match` | INTEGER / bool | 全行 0（false） |
| `telephone_status` | 同 `verification_status` | TEXT / large_string | 全行 `review_required` |
| `telephone_coverage_type` | 同 `coverage_type` | TEXT / large_string | 全行 `municipality_only` |
| `telephone_derivation` | 同 `derivation` | TEXT / large_string | 全行 NULL |
| `mlit_code` | `mlit_town_version.mlit_code`（`is_current = 1`） | TEXT / large_string | |
| `mlit_latitude` | 同 `latitude` | REAL / double | 代表点 |
| `mlit_longitude` | 同 `longitude` | REAL / double | 代表点 |
| `area_code` | `telephone_area_version.area_code`（`is_current = 1`） | TEXT / large_string | v2.3.0 は全行 NULL |
| `numbering_area_name` | 同 `area_text_raw` | TEXT / large_string | v2.3.0 は全行 NULL |
| `old_postal_code` | `postal_record_version.old_postal_code`（`is_current = 1`）を郵便番号ごとに集約 | TEXT / large_string | 旧郵便番号が複数あるときは `;` 区切り（例 `93021;93923`） |

市外局番の13列が空なのは、町字単位の対応が公表されていないためです。市区町村単位の市外局番は
`bridge_municipality_telephone` を引きます（[6.2](#62-結合するときの約束) の 8）。

#### `unmatched_records`

| 列 | 型 | 内容 |
|---|---|---|
| `kind` | TEXT | `postal_record`（住所に結べなかった日本郵便レコード）／ `mlit_town`（住所に結べなかった国交省レコード）／ `address`（国交省レコードに結べなかった住所） |
| `record_id` | TEXT | `kind` に応じて `postal_record_id` / `mlit_record_id` / `address_id` |
| `matching_rule_id` | TEXT | 未一致を記録した規則（`P7` / `M5`） |

`bridge_address_postal`（名前の照合）と `bridge_address_mlit` の未解決行から作られます。公式変換表
（`bridge_address_postal_code`）や市外局番の未解決行は含みません。

---

## 8. よくある落とし穴

どの SQL も v2.3.0 の SQLite で実行して確かめています。

### 8.1 コードを数値として扱う

`lg_code = 11002` のような数値の比較は一致しません（`011002` の先頭のゼロが落ちる）。コードは常に文字列で
比べ、CSV・Excel・DWH への取り込みでも文字列の型にしてください。

```sql
SELECT lg_code, jis_city_code FROM municipality WHERE lg_code = '011002';
```

### 8.2 政令市の人口を二重に数える

`census_population_municipality` には政令市の親（`area_level = '1'`、20市）と区（`'0'`）の両方があります。
合計は親を除いて取ります（全国 122,972,528 人）。

```sql
SELECT sum(population_total)
FROM   census_population_municipality
WHERE  area_level <> '1';
```

行数を数えるときは注意が必要です。`area_level <> '1'` は `area_level` が NULL の北方領土6村の行も落とします
（合計には影響しませんが件数は変わります）。親だけを除いて数えるなら `IS NOT` を使います（1,898 行）。

```sql
SELECT count(*)
FROM   census_population_municipality
WHERE  area_level IS NOT '1';
```

`census_population_age` は全国・都道府県・2000年境域の地域も含み、国籍（総数／日本人）と男女（総数／男／女）の
組ごとに行があります。市区町村の値を出すときは `lg_code IS NOT NULL` と国籍・男女の条件を必ず付けます。

```sql
-- 新宿区の 65歳以上（国籍総数・男女総数）
SELECT sum(population)
FROM   census_population_age
WHERE  lg_code = '131041'
  AND  nationality_raw = '0_国籍総数' AND sex_raw = '0_総数'
  AND  age_class_raw >= '14' AND age_class_raw < '22';
```

### 8.3 住所系ブリッジの `lg_code` で市区町村を結ぶ

`bridge_address_postal_code` / `bridge_address_postal` / `bridge_address_mlit` / `bridge_address_telephone` の
`lg_code` は**全行 NULL** です。これを `municipality` に結ぶと、エラーにならずに0行になります。

```sql
-- 誤り: 0 行になる
SELECT count(*)
FROM   bridge_address_postal_code b
JOIN   municipality m ON m.lg_code = b.lg_code
WHERE  b.postal_code = '1600023';
```

```sql
-- 正しい: 住所の lg_code を使う
SELECT DISTINCT a.lg_code
FROM   bridge_address_postal_code b
JOIN   address a ON a.address_id = b.address_id
WHERE  b.postal_code = '1600023';
```

逆に市区町村系3本（`bridge_municipality_*`）の `address_id` は全行 NULL です。

### 8.4 政令市の親コードで駅・バス停を探す

駅・バス停・路線は区のコードに結ばれていて、政令市の親コード（例 `011002` 札幌市）には1行もありません。
親コードから引くときは、同じ都道府県・市名で `ward` を持つ行（区）に広げます。

```sql
WITH target AS (
  SELECT w.lg_code
  FROM   municipality_version p
  JOIN   municipality_version w
         ON w.pref = p.pref AND w.city = p.city
        AND w.ward IS NOT NULL AND w.is_current = 1
  WHERE  p.lg_code = '011002' AND p.is_current = 1 AND p.ward IS NULL
)
SELECT s.lg_code, count(*) AS stations
FROM   bridge_station_municipality s
JOIN   target t ON t.lg_code = s.lg_code
GROUP  BY s.lg_code;
```

### 8.5 `match_method = 'spatial_containment'` で絞る

駅・バス停のブリッジでは、境界データの旧コード（浜松市の2024年再編前の区）を承継記録で読み替えた行が
`spatial_containment_via_lineage` になっています（駅 68 行、バス停 1,494 行）。`= 'spatial_containment'` で絞ると
これらが静かに落ちます。確定した行がほしいなら `verification_status` で絞ります。

```sql
SELECT match_method, verification_status, count(*)
FROM   bridge_station_municipality
WHERE  match_method LIKE 'spatial_containment%'
GROUP  BY 1, 2;
```

### 8.6 `LIMIT 1` や先頭の行で1件に絞る

1つの郵便番号が複数の住所を覆うのは正しい状態です（`1600023` は西新宿一〜八丁目の8件）。`LIMIT 1` は正しい
答えの中から任意の1件を選ぶことになります。1件に絞るなら根拠の列で絞ります。候補が複数の住所は結果に
出なくなりますが、それが正しい答えです。

```sql
SELECT b.address_id, b.postal_code
FROM   bridge_address_postal_code b
WHERE  b.address_id = 'jpa1dy9jn5hy738r9wnb'
  AND  b.is_current = 1
  AND  b.verification_status = 'auto';
```

### 8.7 フラット表の行数を住所の件数と思う

フラット表（`address_crosswalk`、Parquet、CSV）は 727,110 行ですが、住所は 726,170 件です。住所の件数は
`address_id` の異なり数で数えます。

```sql
SELECT count(*) FROM address;
```

### 8.8 住所から市外局番を `bridge_address_telephone` で引く

この表は全行が未解決（T10）で、番号区画は入っていません。市区町村単位の `bridge_municipality_telephone` を
使います（[6.3](#63-代表的な結合の例) の2つ目の例）。1つの市区町村が複数の番号区画にまたがることがあり、
`coverage_type = 'partial'` の行は市区町村の一部だけです（除外の原文は `mismatch_note`）。

### 8.9 市区町村の郵便番号を片方の表だけで引く

市区町村単位の郵便番号は2本に分かれています。両方を合わせて引きます。

```sql
SELECT b.lg_code, b.postal_code, b.matching_rule_id
FROM   bridge_municipality_postal_code b
WHERE  b.lg_code = '011011'
UNION ALL
SELECT b.lg_code, r.postal_code, b.matching_rule_id
FROM   bridge_municipality_postal b
JOIN   postal_record r ON r.postal_record_id = b.postal_record_id
WHERE  b.lg_code = '011011';
```

町字単位の郵便番号（`bridge_address_postal_code`）を市区町村で集めるには `address.lg_code` を経由します（8.3）。

### 8.10 廃置分合の新コードをそのまま現行コードと思う

`municipality_lineage_edge.new_lg_code` は、その時点の新コードです。その後また合併していれば現行の市区町村では
ありません（現行に一致するのは 2,528 本のうち 2,310 本）。現行までたどるには施行日の順に辺をつなぎます。
`continues`（旧→旧）の辺はたどらず、行き先が複数（`is_unique_match = 0`）の辺は分かれたまま扱います。

```sql
WITH RECURSIVE walk(lg_code, effective_date, depth, all_unique) AS (
  SELECT '343463', '0000-00-00', 0, 1
  UNION ALL
  SELECT e.new_lg_code, e.effective_date, w.depth + 1, w.all_unique * e.is_unique_match
  FROM   walk w
  JOIN   municipality_lineage_edge e ON e.old_lg_code = w.lg_code
  WHERE  e.effective_date > w.effective_date
    AND  e.new_lg_code <> w.lg_code
)
SELECT w.depth, w.lg_code, w.all_unique, (m.lg_code IS NOT NULL) AS is_current_code
FROM   walk w
LEFT   JOIN municipality m ON m.lg_code = w.lg_code
ORDER  BY w.depth, w.lg_code;
```

### 8.11 `*_version` 表を `is_current` なしで結ぶ

v2.3.0 ではどの実体も版が1つなので結果は変わりませんが、次の版以降で古い版が残ると行が倍になります。
最初から `is_current = 1` を付けてください。

```sql
SELECT m.lg_code, v.pref, v.city, v.ward
FROM   municipality m
JOIN   municipality_version v ON v.lg_code = m.lg_code AND v.is_current = 1
WHERE  m.lg_code = '131041';
```

### 8.12 市区町村名や町名で結ぶ

名前は一意ではありません。「西新宿」は複数の都道府県にあり、市区町村でも「泊村」が北海道に2つあります
（`014036` と北方領土の `016969`）。結合は必ずコード（`address_id`、`(lg_code, machiaza_id)`、`lg_code`）で行います。

```sql
SELECT lg_code, pref, city
FROM   municipality_version
WHERE  city = '泊村' AND is_current = 1;
```

### 8.13 e-Stat 小地域の市区町村コードを現行コードとして結ぶ

`estat_small_area.jis_city_code` は2020年の境界のコードです。756 行は現行の市区町村に無いコード（浜松市の
旧区など）なので、`reconcile_status = 'matched'` で絞るか、`superseded` / `split_no_single_successor` を別に
扱います。また、2025年の人口を2020年の小地域に結びつけないでください（調査の回が違います）。

```sql
SELECT reconcile_status, count(*)
FROM   estat_small_area
GROUP  BY reconcile_status;
```

### 8.14 旧郵便番号で直接フラット表を引く

フラット表の `old_postal_code` は、旧郵便番号が2つある郵便番号では `;` 区切り（例 `93021;93923`）です。
等値比較では取りこぼすので、旧郵便番号から引くときは `postal_record_version` を使います。

```sql
SELECT DISTINCT postal_code
FROM   postal_record_version
WHERE  old_postal_code = '160' AND is_current = 1;
```

### 8.15 `confidence` を確率として計算に使う

`confidence` は規則の順位を表す値で、平均や積に意味はありません。条件には `verification_status` や
`relation_type` を使ってください。

### 8.16 ビューに重い処理を重ねる

`address_crosswalk` を `postal_code` などで絞って読むのは数秒で済みますが、このビューの上に `DISTINCT` と
別の表との結合を重ねると非常に遅くなることがあります（駅の表との結合で数分かかった例があります）。
フラット表を介さず、ブリッジ表と `address` を直接結んでください（ブリッジ表には `address_id` や端点列の索引が
あります）。

### 8.17 Snowflake の型推論に任せる

- Parquet の `null` 型の列（`source_snapshot.license_text_sha256` / `etag` / `last_modified`）は推論できません。
  `VARCHAR` を明示します。
- `bridge_station_municipality.is_unique_match` と `bridge_bus_stop_municipality.is_unique_match` は Parquet で
  `int64` です（他の表の同名列は `bool`）。
- CSV を読むときは全列を文字列で受けてから型を付けます（2.2）。

---

## 9. 出典とライセンス

**コード（リポジトリ）は MIT ライセンスですが、データはそうではありません。** 元データの発行元ごとの利用条件が、
そこから作ったデータにもそのまま及びます。正式な記載は同梱の `NOTICE.md` と、リポジトリの
[`DATA_LICENSE.md`](../DATA_LICENSE.md) です。以下は `DATA_LICENSE.md` の要約で、出典表示の文言は同文書から
そのまま引用しています（リリースのファイルは加工済みのデータなので「加工」の形の表示です）。

| 発行元・データ | 規約 | 出典表示 |
|---|---|---|
| デジタル庁 アドレス・ベース・レジストリ | 公共データ利用規約（第1.0版）(PDL 1.0) | 必要（加工した旨を含む） |
| 日本郵便 郵便番号データ | 著作権を主張しない（自由配布可） | 不要（jpac は任意で表示） |
| 国土交通省 位置参照情報（大字・町丁目レベル） | 国土数値情報ダウンロードサイト 利用規約（PDL 1.0 を適用） | 必要。加工データを国が作成したかのように示してはならない |
| 総務省 市外局番の一覧 ／ 電気通信番号指定状況 | 公共データ利用規約（第1.0版）(PDL 1.0) | 必要 |
| 国土交通省 国土数値情報 鉄道データ（N02）2025年版 | 国土数値情報ダウンロードサイト 利用規約（PDL 1.0 を適用） | 必要 |
| 国土交通省 国土数値情報 バス停留所（P11）令和4年度版 | 同上（令和4年度版のみ使用） | 必要 |
| 総務省統計局 令和2年国勢調査 小地域境界データ | 政府統計の総合窓口(e-Stat)利用規約 | 必要（加工した旨を含む） |
| 総務省統計局 廃置分合等情報 | 政府統計の総合窓口(e-Stat)利用規約 | 必要 |
| 総務省統計局 令和7年国勢調査 人口等基本集計 | 政府統計の総合窓口(e-Stat)利用規約 | 必要 |

出典表示の文言（`DATA_LICENSE.md` から引用。`NOTICE.md` の「加工済み成果物」の節と同じ）:

```
出典：「アドレス・ベース・レジストリ（町字マスター、町字・郵便番号変換表）」（デジタル庁）
      https://www.digital.go.jp/policies/base_registry_address を加工して作成
```

```
郵便番号データ：日本郵便株式会社
      https://www.post.japanpost.jp/zipcode/dl/utf-zip.html
```

> 郵便番号データに限っては日本郵便株式会社は著作権を主張しません。
> 自由に配布していただいて結構です。

```
「位置参照情報ダウンロードサービス」（国土交通省）
      https://nlftp.mlit.go.jp/isj/ をもとに jp-address-crosswalk 作成
```

```
出典：「市外局番の一覧」（総務省）
      https://www.soumu.go.jp/main_sosiki/joho_tsusin/top/tel_number/shigai_list.html を加工して作成

出典：「電気通信番号指定状況（固定電話等の電話番号）」（総務省）
      https://www.soumu.go.jp/main_sosiki/joho_tsusin/top/tel_number/number_shitei.html を加工して作成
```

```
「国土数値情報（鉄道データ）」（国土交通省）
      https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N02-2025.html をもとに jp-address-crosswalk 作成
```

```
「国土数値情報（バス停留所データ）」（国土交通省）
      https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-P11-v3_0.html をもとに jp-address-crosswalk 作成
```

```
「令和2年国勢調査 小地域（町丁・字等別）境界データ」（総務省統計局）
      https://www.e-stat.go.jp/gis を加工して作成
```

```
「廃置分合等情報」（政府統計の総合窓口（e-Stat）、https://www.e-stat.go.jp/）を加工して作成
```

```
「令和7年国勢調査 人口等基本集計」（総務省統計局、政府統計の総合窓口（e-Stat）、https://www.e-stat.go.jp/）を加工して作成
```

### 9.1 再配布するときの義務

社内のデータ基盤に載せて配る、加工したデータを他部署や社外に渡すなど、データを再配布する場合は
`DATA_LICENSE.md` の「Combined obligation for redistributors」に従います。原文を引用します。

> If you redistribute the built database, or anything derived from it, you must:
>
> 1. Retain `dist/NOTICE.md` unmodified, or reproduce every attribution it contains.
> 2. State that the data was **processed** and is not the publishers' own product.
> 3. Not present processed data as if produced by デジタル庁, 国土交通省 or 総務省.
> 4. Not describe MLIT coordinates as precise or building-level locations.
> 5. Keep this file, or an equivalent statement of the per-source terms, alongside the data.

実務上は、取り込んだテーブルやダッシュボードのそばに `NOTICE.md`（または上の出典表示すべて）を置き、
「加工したデータである」ことを明記し、国交省の緯度経度を建物の位置として説明しないことです。

### 9.2 保証

発行元も jpac も、データの正確さを保証しません。特にデジタル庁は「町字・郵便番号変換表」を参考情報とし、
完全性や正確性を保証していません。

---

## 10. 付録 用語集

| 用語 | 意味 |
|---|---|
| 町字（まちあざ） | デジタル庁 ABR が定める住所の単位。「西新宿一丁目」「大字○○字△△」のように、市区町村の下の町・大字・丁目・小字までを指す。jpac の住所1件 = 町字1件 |
| 大字・町 ／ 丁目 ／ 小字 | 町字を構成する部分。`address.oaza_cho` / `chome` / `koaza` |
| ABR（アドレス・ベース・レジストリ） | デジタル庁が整備する住所の基準データ。jpac は町字マスター・市区町村マスター・町字・郵便番号変換表を使う |
| 正準（canonical） | jpac が基準とする住所の体系。ABR の町字。ほかの体系はこれに対応づける |
| ブリッジ | 2つの体系の対応を、根拠と確からしさ付きで持つ表（`bridge_…`） |
| 全国地方公共団体コード（`lg_code`） | 市区町村の6桁のコード。5桁の JIS コードに検査数字1桁を付けたもの |
| 検査数字 | `lg_code` の6桁目。5桁の各桁に 6,5,4,3,2 を掛けて足し、11 で割った余り r から (11 − r) mod 10 で求める |
| 政令市（政令指定都市） | 区を置く大都市（20市）。市全体の「親」コードと各区のコードがある |
| 特別区 | 東京23区。市と同格の市区町村として1件ずつコードを持つ（親は無い）。人口の結果表では「特別区部」という集計行もあるが jpac の市区町村には無い |
| 番号区画 | 総務省が定める固定電話番号の地域の単位（`numbering_area_code`）。市外局番は番号区画に割り当てられ、区切りは市区町村の境界と一致しない |
| 市外局番 ／ 市内局番 | 固定電話番号の先頭部分（`area_code`）と、その次の部分（`local_code`） |
| 対象地域の文 | 総務省「市外局番の一覧」で各番号区画がどこを含むかを書いた文（「夕張市（富野を除く。）」など）。`telephone_area_version.area_text_raw` |
| 旧郵便番号 | 1998年の7桁化より前の3桁・5桁の郵便番号 |
| `ken_all` | 日本郵便の「住所の郵便番号」データファイル。1行が `postal_record` 1件 |
| 大字・町丁目コード | 国土交通省 位置参照情報の12桁のコード（`mlit_code`） |
| 代表点 | 国土交通省が大字・町丁目ごとに1点だけ示す緯度経度。範囲全体を代表する点で、建物の位置ではない |
| 小地域 | 国勢調査の集計単位（町丁・字等）。境界は国勢調査の調査区の境界で、行政区域そのものではない |
| 駅グループ | 国土数値情報 N02 で、同じ駅とみなされる地物（ホーム・線路）の集まり。jpac の駅1件 |
| 廃置分合 | 市区町村の合併・編入・分割・分離・市制施行・名称変更など、市区町村の区域や名称・コードが変わる出来事 |
| 承継記録 | 浜松市の2024年の区再編について、旧区コードがどの新区に移ったかを人が一次資料から確認して署名した記録（リポジトリの `overrides/municipality_lineage.yml`）。駅・バス停・路線の判定で旧コードの読み替えに使う |
| 組替 | 統計局が2020年の人口・世帯数を2025年の市区町村の境域に組み替えて公表した値（`*_2020_rebased`） |
| 不詳 | 国勢調査で年齢などが分からなかった人の区分。jpac は「不詳」を独立の区分として持ち、推計で補完した値は使わない |
| 按分 | 面積などの比率で数値を割り振ること。jpac は一切しない |
| 北方領土の6村 | 色丹村・泊村・留夜別村・留別村・紗那村・蘂取村。市区町村コードはあるが国勢調査の対象外 |
| `unresolved` | 対応先が見つからなかったことを表す `relation_type`。行は消さずに残る |
| `auto` / `review_required` | 自動確定した対応 ／ 人の確認が要る対応（候補が複数、名前だけの照合など） |
