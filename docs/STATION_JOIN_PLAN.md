# STATION_JOIN_PLAN.md — 駅を jpac に繋ぐ：実装手順案・進め方・データ洗い出し

**この文書は提案であり、決定ではない。** §5 の承認事項に可否をもらってから着手する。
（2026-09-05 更新: §5 に推奨案を追記し、§6 のフェーズ1を推奨に合わせて縮めた）

---

## 方針（一文）

> **駅の位置（N02 2025年版）を、統計境界（e-Stat 令和2年国勢調査 小地域）に一度だけ
> 空間結合して市区町村コードに落とし、そこから先は jpac の既存キーで辿る。
> ジオメトリは配らない。町字には届かないと明言する。断面はソースごとに記録し、
> 揃わないことを前提に、ズレを説明可能な形で残す。**

- 作成日: **2026-09-05**
- 前提となる調査:
  [`GEO_EXPANSION_RESEARCH.md`](GEO_EXPANSION_RESEARCH.md)（全体調査・§10 が N02）/
  [`ESTAT_BOUNDARY_VERIFICATION.md`](ESTAT_BOUNDARY_VERIFICATION.md)（e-Stat の実測検証）/
  [`N03_BOUNDARY_DESIGN.md`](N03_BOUNDARY_DESIGN.md)（N03 案。§0.3 参照）
- 規律: [`POLICY.md`](POLICY.md) / [`LICENSE_POLICY.md`](LICENSE_POLICY.md) / [`../DATA_LICENSE.md`](../DATA_LICENSE.md)

> **2026-09-05: A1〜A7 承認済。実装前調査を完了した →
> [`STATION_JOIN_PREFLIGHT.md`](STATION_JOIN_PREFLIGHT.md)。**
> ゲート **G2 / G3 / G4 は先行通過**、残件 2.4（県境）と 4.2（駅の点）も解決した。
>
> **2026-09-05: フェーズ1（断面管理）を実装した。** 下記 §6 の 1.1 / 1.2 / 1.3 / 1.4 / 1.6 が完了。
> `source_snapshot` に `edition_origin` 列を追加し、版と発行日の出所を
> `payload` / `observed` / `declared` / `mixed` で区別できるようにした。
> オフライン再ビルドを実行して品質ゲート123件が通り、テストは 326 passed / 1 skipped。
>
> **2026-09-05: フェーズ2（e-Stat 取り込み）を実装した。** 2.1〜2.3 と 2.5 の基盤が完了。
> `sources/estat.py`（依存追加なしの DBF リーダ）と `build/estat.py`（突合の5分類）を追加。
> 47県のペイロードから **232,019行** を取り込み、内訳は preflight の実測と全一致
> （町丁字 231,668 / 水面 351 / 市区町村 1,897 / 飛び地 323 / 抜け地 7 / 島 3,787 / 重複境域 17,381）。
>
> **ゲート G2 通過: 一致 1,889、説明できない残余 0 件。**
> 突合は実装1回目に3つの欠陥を露出させ（lineage の片方向読み、分割自治体の誤分類、
> 帰属未確定コードの取りこぼし）、いずれも修正のうえ回帰テストで固定した。
>
> **2026-09-05: フェーズ3（N02 取り込み）を実装した。** 3.2〜3.4 が完了。
> `sources/mlit_ksj_n02.py` を追加し、2025年度版のみを読む。
> **ゲート G3 通過**: `N02_005c` / `N02_005g` が実データに存在し、
> **10,234 features → 9,046 駅**（水増し 1,188 を回避、最大 11 features/駅）。
> DBF リーダは `payload.read_dbf_member` に共通化した（依存追加なし）。
>
> **2026-09-05: フェーズ4（結合）を実装した。依存追加はしていない。**
> shapefile リーダ（`payload.read_shp_shapes`）と空間プリミティブ
> （`build/spatial.py`: even-odd 点in面、一様格子インデックス、線の長さ重心）を自作し、
> `build/station.py` が `bridge_station_municipality` を組み立てる。
>
> **ゲート G4 通過**: 駅 9,046 → bridge 9,047行、**未分類 0**。
> `contains`+`auto` **8,987** / 断面ズレ（浜松旧区）**54** / `ambiguous` 1駅（大阪空港）/
> `unresolved` 4。**preflight で shapely を使って出した実測値を完全再現した。**
> 測地系は両ソースの `.prj` を実際に読んで `GCS_JGD_2011` を確認（仮定していない）。
>
> 残るのは **人によるライセンス逐語読みと commit** のみ
> （`config/sources.yml` の2エントリは書き込み済、commit は未実施）。
> **これが済むまでリリースはしない**（G0）。

---

## 0. 要約

### 0.1 何を作るか

**駅 → 市区町村（`lg_code`）の対応表。** そこから先は jpac が既に持っている
郵便番号・市外局番・町字・国交省コードへ、キーだけで辿れる。

**町字には届かない。** これは制約ではなく仕様として明示する（§4.4）。

### 0.2 使うデータは3つ

`N02 鉄道`（駅の位置）＋ `e-Stat 小地域境界`（駅を市区町村に落とす面）＋ `jpac 本体`（受け口）。
詳細は §1。

### 0.3 N03 は使わない。ただし文書は残す

`N03_BOUNDARY_DESIGN.md` は **N03 を採らない判断の記録として保持**し、本計画では
実装しない。同文書 §3.1 のスキーマ（`centroid_lat` / `centroid_lon` / `area_sqm`）は
**e-Stat 版として §3 で置き直す**。

> **生きている設計は本文書だけである。** `N03_BOUNDARY_DESIGN.md` の冒頭に
> 「superseded by STATION_JOIN_PLAN.md」を追記する（§6 の作業に含む）。

N03 を採らない理由は測量法の承認要件（同 §8）。**2026年版でも同じで、承認番号が
`R 6JHf 503` から `R 7JHf 351` に変わっただけである**（2026-09-05 確認）。
一過性ではなく構造的。

### 0.4 最重要 — 断面管理を先にやる

いま jpac は「いつ取得したか」しか記録しておらず、「そのデータが何時点を表すか」は
ほぼ空である（§2）。**このまま駅を繋ぐと、静岡県で観測された 551 行のズレが
「説明できない不一致」として出荷される。** 断面を記録できる状態を作るのが先。

---

## 1. 利用する最新データの洗い出し

### 1.1 新規に取り込む2つ

| | **N02 鉄道** | **e-Stat 小地域境界** |
|---|---|---|
| 発行元 | 国土交通省 | 総務省統計局（e-Stat 統計GIS） |
| 採用する版 | **2025年版** | **令和2年国勢調査 小地域（町丁・字等）JGD2011** |
| 版の根拠 | 一覧ページ上の最新が `KsjTmplt-N02-2025.html`。**2026年版は一覧に存在しない**（2026-09-05 確認） | ダウンロード画面が列挙した調査年は **2020 / 2015 / 2010 / 2005 / 2000 のみ**（実際に辿って確認） |
| データ基準日 | **2025-12-31** | **2020-10-01**（国勢調査基準日）。公開（更新）日 2023-01-04 |
| 形式 | Shapefile（線） | Shapefile（面）、世界測地系緯度経度 |
| 測地系 | JGD2011 | **JGD2011**（全47県の `.prj` を実測確認） |
| 配布単位 | 全国 | 都道府県別 47ファイル |
| サイズ | 未計測 | **322,598,028 バイト**（全47県、実測） |
| 利用許諾条件 | 2020年以降：オープンデータ（CC_BY_4.0）／上記以外：商用可 | 政府標準利用規約 第2.0版 / CC BY 4.0 互換 |
| **測量法の条項** | **なし**（「測量法」「承認」「二次利用」いずれも詳細ページに出現せず） | **なし**（規約・注意事項・定義書の5文書で確認） |
| 必要な列 | `N02_003` 路線名 / `N02_004` 運営会社 / `N02_005` 駅名 / **`N02_005c` 駅コード** / **`N02_005g` 駅グループコード** | `PREF` / `CITY` / `KEY_CODE` / `HCODE` / `KIGO_D` / `KIGO_E` / `AREA_MAX_F` |
| §4 ゲート | **未通過** | **未通過** |

> ⚠️ **N02 は駅コードの有無が決定的**。v2.3 には `N02_005c` / `N02_005g` が無い
> （2026-09-05 に v2.3 ページで確認済）。2025年版を使う理由はライセンスではなくこれ。

> ⚠️ **N02 のライセンスは年度で割れる。** 「2020年（令和2年）以降：オープンデータ／
> 上記以外：商用可」。**2025年版のファイルだけを取れば混在しない**（年度ごとに別ファイル）。

### 1.2 jpac が既に使っている4ソース

`dist/jp_address_crosswalk.sqlite`（v1.0.0）の `source_snapshot` 66行から。

| 発行元 | データ | jpac が記録している版 | 取得日 |
|---|---|---|---|
| デジタル庁 | ABR 町字マスター / 市区町村 / 都道府県 / 郵便番号変換表 | **記録なし**（`source_version` = NULL） | 2026-08-23 |
| 日本郵便 | ken_all / add / del | **記録なし** | 2026-08-23 |
| 国土交通省 | 位置参照情報 大字・町丁目レベル | `isj_version` = **19.0b** / `fiscal_year` = **2025** | 2026-08-23 |
| 総務省 | 市外局番一覧 / 電気通信番号指定状況 | **記録なし** | 2026-08-23 |

> ⚠️ **位置参照情報 19.0b が上流の最新版かは未確認。** `https://nlftp.mlit.go.jp/isj/`
> を取得したが版数を特定できなかった。§6 のタスクに含める。
> 他の3ソースは `source_version` がそもそも空なので、最新かどうかを本リリースから
> 判定する手段が無い（§2 の問題そのもの）。

### 1.3 断面の一覧 — ここが揃わない

| データ | そのデータが表す時点 |
|---|---|
| ABR 町字マスター（jpac の基準） | 取得 2026-08-23（**基準日は記録なし**） |
| 位置参照情報 | fiscal_year 2025 |
| **N02 鉄道 2025年版** | **2025-12-31** |
| **e-Stat 小地域** | **2020-10-01** |

**e-Stat と N02 の間で 5年3ヶ月**開く。しかも:

- **このズレは当面ひろがる。** 国勢調査は5年周期で、令和7年（2025年）調査の境界データは
  **まだ公開されていない**（§1.1 で実測）。公開されるまで差は毎年開く
- **公開されたら一気に縮み、同時に前提が変わる。** 小地域は調査ごとに切り直されるため、
  `KEY_CODE` も面の形も変わる。`GEO_EXPANSION_RESEARCH.md` §3 が
  「(machiaza_id, key_code, census_year) を鍵にせよ」と書いたのはこのため

> **結論: 断面は「一致させる」のではなく「明示して、ズレを説明可能にする」。**

### 1.4 採らないもの

| | 理由 |
|---|---|
| **N03 行政区域**（2026年版が最新、基準日 2026-01-01） | 測量法に基づく国土地理院長承認（複製）`R 7JHf 351`。ジオメトリ由来の座標を配るなら29条申請。e-Stat で代替可能 |
| **S12 駅別乗降客数**（2024年版が最新） | 詳細ページの HTML に「CC_BY_4.0」と「非商用」が両方あり片方がコメント化。**未決着**。本計画の3データには含めないので §6 の後回しタスク |
| N05 鉄道時系列（2025年版） | 非商用＝再配布不可 |
| 基盤地図情報 / OSM / 駅データ.jp / CODH | 測量法 / ODbL / 非公的 / 第三者 |

---

## 2. 先にやること — 断面管理（実測に基づく）

### 2.1 いま何が空か

| 項目 | 実測 |
|---|---|
| `observed_from`（取得日） | 全テーブル **100%**。値は全部 `2026-08-23` |
| `source_snapshot.downloaded_at` | **66 / 66** |
| `source_snapshot.published_at` | **0 / 66** |
| `source_snapshot.source_version` | **0 / 66** |
| `municipality_version.valid_from` / `valid_to` | **0 / 1,918** |
| `postal_record_version.valid_from` | **0 / 124,513** |
| `bridge_address_mlit.valid_from` | **0 / 733,948** |
| `address.valid_from` | 724,922 / 726,170（`valid_to` は 1 件のみ） |
| `overrides/municipality_lineage.yml` | **`transitions: []`（空）** |

**器は全テーブルに揃っている。値が入っていないだけである。**
これまで問題化しなかったのは、現行4ソースが実質1断面だったから。

### 2.2 なぜ駅より先か

`ESTAT_BOUNDARY_VERIFICATION.md` §3.4 の実測で、静岡県の一致率が **84.85%** に落ちた。
551 行すべてが浜松市で、jpac の新区コード（`22138`/`22139`/`22140`）が e-Stat の
旧区コード（`22131`〜`22137`）のポリゴンに落ちたためである。区再編は 2024-01-01。

**`municipality_lineage.yml` が空である限り、この551行を「説明済み」として記録する場所が無い。**
先に断面を整えないと、駅の結合はこれを未説明のノイズとして再生産する。

---

## 3. スキーマ案

`N03_BOUNDARY_DESIGN.md` §3.1 を e-Stat 用に置き直したもの。
**`centroid_*` は持たない**（N03 では測量法対策だったが、e-Stat では単に不要）。

### 3.1 `estat_small_area` — 小地域ポリゴンの受け皿

```sql
CREATE TABLE "estat_small_area" (
  "small_area_id"      TEXT PRIMARY KEY,
  "key_code"           TEXT,      -- KEY_CODE (PREF+KEYCODE2)
  "jis_city_code"      TEXT,      -- PREF+CITY。jpac への結合キー
  "lg_code"            TEXT,      -- 結合後に持ち替える
  "s_area"             TEXT,      -- S_AREA 町丁・字等番号
  "census_year"        TEXT,      -- '2020'
  "reference_date"     TEXT,      -- '2020-10-01'
  "published_at"       TEXT,      -- '2023-01-04'
  "datum"              TEXT,      -- 'JGD2011'
  "hcode"              INTEGER,   -- 8101 町丁字 / 8154 水面調査区
  "kigo_d"             TEXT,      -- '' / 'D' 飛び地 / 'D1' 抜け地
  "kigo_i"             TEXT,      -- 'I' 島
  "kigo_e"             TEXT,      -- 重複境域フラグ E1..En
  "area_max_f"         TEXT,      -- 'M' 最大面積
  "n_ken"              TEXT,      -- 抜け地の元都道府県
  "n_city"             TEXT,      -- 抜け地の元市区町村
  "pref_name_raw"      TEXT,
  "city_name_raw"      TEXT,
  "s_name_raw"         TEXT,
  "reconcile_status"   TEXT,
  "source_snapshot_id" TEXT,
  "created_at"         TEXT,
  "updated_at"         TEXT,
  CHECK (hcode IN (8101, 8154)),
  CHECK (reconcile_status IN ('matched','designated_city_parent','jpac_only',
                              'estat_only','unassigned_area','not_surveyed'))
);
```

- **ジオメトリは持たない。** 面は結合の道具であって成果物ではない。リリースサイズを
  変えない（`ESTAT_BOUNDARY_VERIFICATION.md` §0.3 の方針）
- `AREA` / `PERIMETER` は取り込まない。発行元が「国土地理院等の公式な面積と一致しません」
  と明記しているものを配る意味が無い
- `not_surveyed` は北方領土6件のための分類（§4.3）

### 3.2 `station` — 駅

```sql
CREATE TABLE "station" (
  "station_id"          TEXT PRIMARY KEY,   -- jpac 側の恒久ID
  "n02_group_code"      TEXT,               -- N02_005g 駅グループコード
  "n02_station_code"    TEXT,               -- N02_005c 駅コード
  "station_name_raw"    TEXT,               -- N02_005
  "line_name_raw"       TEXT,               -- N02_003
  "operator_name_raw"   TEXT,               -- N02_004
  "railway_class"       TEXT,               -- N02_001
  "operator_class"      TEXT,               -- N02_002
  "feature_count"       INTEGER,            -- 束ねた線分の数
  "n02_edition"         TEXT,               -- '2025'
  "n02_reference_date"  TEXT,               -- '2025-12-31'
  "datum"               TEXT,               -- 'JGD2011'
  "source_snapshot_id"  TEXT,
  "created_at"          TEXT,
  "updated_at"          TEXT
);
```

**`N02_005g` で束ねてから1行にする。** 束ねないとホーム・線路ごとに行が水増しされる
（`GEO_EXPANSION_RESEARCH.md` §10.4）。

### 3.3 `bridge_station_municipality` — 本体

既存の `bridge_municipality_*` と同じ列構成に揃える。

```sql
CREATE TABLE "bridge_station_municipality" (
  "bridge_id"                     TEXT PRIMARY KEY,
  "station_id"                    TEXT,
  "lg_code"                       TEXT,
  "relation_type"                 TEXT,
  "match_method"                  TEXT,
  "matching_rule_id"              TEXT,
  "confidence"                    REAL,
  "candidate_group_id"            TEXT,
  "candidate_count"               INTEGER,
  "candidate_count_is_complete"   INTEGER,
  "is_unique_match"               INTEGER,
  "verification_status"           TEXT,
  "derivation"                    TEXT,          -- どの断面の組で作ったか
  "n02_reference_date"            TEXT,
  "estat_census_year"             TEXT,
  "mismatch_note"                 TEXT,
  "observed_from"                 TEXT,
  "observed_to"                   TEXT,
  "is_current"                    INTEGER,
  "match_run_id"                  TEXT,
  "source_snapshot_id"            TEXT,
  "matching_rule_version"         TEXT,
  "created_at"                    TEXT,
  "updated_at"                    TEXT,
  CHECK (confidence >= 0.0 AND confidence <= 1.0),
  CHECK (candidate_count >= 0),
  CHECK (NOT (is_unique_match = 1 AND candidate_count > 1)),
  CHECK (candidate_count <= 1 OR candidate_group_id IS NOT NULL),
  CHECK (relation_type IN ('contains','candidate','ambiguous','unresolved')),
  CHECK (match_method IN ('spatial_containment','unresolved')),
  CHECK (verification_status IN ('auto','review_required','manually_verified','manually_rejected'))
);
```

**`relation_type` に `exact` / `equivalent` は与えない。** 駅と市区町村は同一物ではなく
包含関係なので、`contains` が正しい。既存の bridge が `exact` を持てるのは ID 対応だから。

---

## 4. 結合ロジック

### 4.1 手順

```
1. N02 駅を N02_005g で束ねる            → station 1行
2. 束ねた線分の代表点を決める             → §4.2
3. 代表点を e-Stat 小地域に落とす（HCODE=8101 のみ）
4. 落ちた面の PREF+CITY → jis_city_code → lg_code
5. §4.3 の分類で全件を classify
```

### 4.2 駅は「線」である — 点の決め方

N02 の駅は線分（ホーム・線路）。点in面には点が要る。

**既定: グループの全線分を合わせた図形の重心。** ただし重心が線分の外に出る形状が
ありうるので、**重心が小地域に落ちなかった場合は線分上の点でも判定し、
両者が一致しない駅は `candidate_count > 1` として曖昧のまま出す。**
片方を黙って採用しない（`POLICY.md` §4）。

### 4.3 分類 — 全件をいずれかに入れる

| `reconcile_status` / 結果 | 条件 | 扱い |
|---|---|---|
| `matched` | 面に落ち、コードが jpac にある | 正常 |
| `designated_city_parent` | 政令市の親20件。面が無いのが正しい | 駅は区で受ける |
| `not_surveyed` | 北方領土6件。国勢調査未実施 | 面が無いのが正しい |
| `unassigned_area` | `13199`（市区町村名が空） | 保持。`lg_code` は NULL |
| `estat_only` / `jpac_only` | 浜松の区再編など | `municipality_lineage.yml` で説明 |
| 面に落ちない駅 | 埋立地・港湾・島 | **`unresolved` として残す。最寄りに寄せない** |
| 複数の面に落ちる駅 | 県境など | `candidate_count > 1`、`is_unique_match = 0` |

### 4.4 やらないこと

| | 理由 |
|---|---|
| 駅を町字に割り当てる | e-Stat 231,668 面 vs jpac 726,170 町字。約3.1倍の粒度差（実測）。`POLICY.md` §4「市区町村レベルの言明を町字に展開しない」 |
| 面に落ちない駅を最寄りに寄せる | 近さで確定させない（同 §4） |
| 駅名と町字名の文字列一致で確定 | 候補生成にしか使わない |
| ポリゴンを配る | 面は結合の道具。`estat_small_area` にジオメトリを持たない |
| 小地域を市区町村に融合する | 加工になる。融合しなくても `PREF`+`CITY` で足りる |

---

## 5. 承認をお願いしたい事項

**以下に可否をいただいてから着手する。**

### A1 — `POLICY.md` §3 の改定

**推奨: 承認。ただし §3 の V1 記述は書き換えず、V2 の節を新設する。**

現行の除外リストは「V1 のスコープはこうだった」という記録である。そこから `stations`
を消すと V1 が何だったかの記録が壊れる。

```
§3   Source restrictions (hard)      ← V1 の記述として凍結。触らない
§3.1 V2 scope extension（新設）
     - 追加する主題: 鉄道駅（N02）、統計境界（e-Stat 小地域）
     - 追加する取得元: なし（総務省統計局は §3 の MIC に含まれる）
     - 新データクラス:「幾何を読むが配布しない」
```

**取得元の追加は不要。** e-Stat は総務省であり §3 の許可リストに既にある。

### A2 — 断面管理の先行

**推奨: 承認。ただしフェーズ1を縮める。**

計画の 1.5（`municipality_version.valid_from`/`valid_to` を 1,918 行埋める）は
**G1 には不要**。G1 が求めるのは「静岡の551行を説明済みとして記録できること」で、
1.1 / 1.4 / 1.6 で足りる。1.5 は既存データモデル全体に触る独立課題であり、
駅に抱き合わせると両方遅れる。**後回しにする。**

### A3 — 使う3データの確定

**推奨: 承認。ただし e-Stat は「町丁・字等」を採り、「基本単位区」は採らない。**

市区町村への割り当てはどちらでも同結果（`PREF`+`CITY` は両方が持つ）。
**実測・逐語確認を行ったのは町丁・字等の方だけ**であり、測った方を使う。

### A4 — N03 の不採用

**推奨: 配布物としては不採用（承認）。ただし「検証専用の利用」は A6 で別途判断。**

N03 2026年版は基準日 2026-01-01 で e-Stat より6年新しく、統計用ではなく行政区域そのもの。
**§7.4 に残る未実測2項目（県境・調査区境界と行政界のズレ）を直接測れる唯一の道具**である。

国土地理院の免除規定では「組織内でビルド時に読むだけ」「座標を持たない成果品」は申請不要。
出力が一致率などの件数だけなら免除の側に立つ。**ただしこれを免除と断定するのは
本計画の判断であってはならない。** A6 のゲートで人が判断する項目に含める。

### A5 — 粒度は市区町村まで

**推奨: 承認。文書だけでなく機械可読な場所にも書く。**

1. `README.md` に明記
2. **スキーマで表現する** — `bridge_station_municipality` は `lg_code` を持つが
   `address_id` は**持たない**。持てないものを持たない構造にする
3. `dist/STATION_JOIN_REPORT.md` に粒度差を数字で書く

**「対応していない」ではなく「3.1倍の粒度差があるので原理的に対応できない」と書く。**
後から誰かが埋めようとする事故を防ぐため。将来の道（e-Stat `KEY_CODE` ↔
`machiaza_id` が解けたとき＝v3）も併記する。

### A6 — ライセンスゲートの実施主体

**推奨: 逐語読みと commit は人。それ以外は自動化する。最初に通す。**

| 自動化できる | 人がやる |
|---|---|
| 対象ページの取得・正規化・`text_sha256` / `text_sha256_decoded` の算出 | **本文を逐語で読む** |
| `config/sources.yml` エントリ草案（rationale 込み） | 判断して commit |
| 判断が要る論点の列挙 | |

読む対象は5点（N02 詳細 / e-Stat 機能利用規約 / e-Stat 利用規約 / e-Stat 注意事項 /
国土数値情報 利用規約）。A4 の検証利用を検討するなら国土地理院パンフレットが6点目。

**ゲートが通らないとフェーズ2以降が全部止まるので、最初に通す。**

### A7 — 成果物の置き場所

**推奨: 同一リポジトリ・別リリースアセット。別リポジトリにはしない。**

別リポジトリにすると `LICENSE_POLICY.md`・`sources.yml` の規律・`source_snapshot` 機構・
品質レポートを複製することになる。**このプロジェクトの主張の信頼性はその仕組みに
乗っており、複製すれば必ず片方が腐る。**

一方で本体リリース（3ファイル・約2GB）に混ぜるのも避ける。粒度も断面も違うため、
本体の品質指標に混ざると「jpac の欠陥」に見える。

```
同一リポジトリ
├─ config/sources.yml   ← mlit_ksj_n02 / estat_boundary を required: false で追加
├─ 既存の §4 ゲート・snapshot 機構をそのまま使う
└─ dist/
   ├─ jp_address_crosswalk.{parquet,csv.gz,sqlite}   ← 変更なし
   └─ jp_station_municipality.{parquet,csv.gz}       ← 新規・別アセット
      + STATION_JOIN_REPORT.md
```

`sources.yml` に `required` フラグが既にあるため、**N02/e-Stat のゲートが未通過でも
本体リリースはブロックされない。** この構造が既に存在するのが決め手。

### 承認事項の一覧

| # | 事項 | 推奨 |
|---|---|---|
| **A1** | `POLICY.md` §3 の改定 | ⭕ 承認（§3 は凍結、§3.1 新設） |
| **A2** | 断面管理の先行 | ⭕ 承認（**1.5 を後回し**） |
| **A3** | 使う3データの確定 | ⭕ 承認（**町丁・字等**を採用） |
| **A4** | N03 の不採用 | ⭕ 承認（**検証専用の利用は A6 で別途判断**） |
| **A5** | 粒度は市区町村まで | ⭕ 承認（スキーマと README にも書く） |
| **A6** | ライセンスゲートの実施主体 | 逐語読み5点は人。**最初に通す** |
| **A7** | 成果物の置き場所 | **同一リポジトリ・別アセット** |

---

## 6. 実装手順案

**フェーズ間にゲートがある。ゲートを飛ばさない。**

### フェーズ 0 — 承認とライセンスゲート（着手前）

| # | 作業 | 完了条件 |
|---|---|---|
| 0.1 | §5 の A1〜A7 に可否をもらう | 全項目に回答がある |
| 0.2 | `POLICY.md` §3.1 の新設（§3 は凍結） | commit 済 |
| 0.3 | **ライセンスゲートの下ごしらえ** — 5ページの取得・正規化・ハッシュ算出・`sources.yml` 草案 | 人が読むだけの状態になっている |
| 0.4 | **人による逐語読みと commit**（A6） | `text_sha256` が rationale 付きで commit されている |

> **ゲート G0: A1 が否なら終了。0.4 が通らなければフェーズ2以降は着手しない。**
> A6 を最初に置くのは、ここが通らないと後続が全部止まるため。

### フェーズ 1 — 断面管理（駅より前）

A2 の推奨により、**1.5 は本フェーズから外し独立課題とする。**

| # | 作業 | 完了条件 |
|---|---|---|
| 1.1 | `source_snapshot.published_at` / `source_version` を埋める設計 | 4ソース分の取得元と値が決まっている |
| 1.2 | 位置参照情報の**上流最新版を確認**（19.0b が最新か） | 版数が確定 |
| 1.3 | ABR / 日本郵便 / 総務省の版の表し方を決める | 各ソースの「版」の定義が文書化されている |
| 1.4 | `municipality_lineage.yml` に**浜松市（2024-01-01、旧7区→新3区）**を登録 | `transitions` が非空。`evidence_url` 付き |
| 1.6 | `dist/SOURCES.yml` に断面の一覧を出す | 各ソースの基準日が読める |
| ~~1.5~~ | ~~`municipality_version.valid_from`/`valid_to` を埋める~~ | **後回し（独立課題）。** 既存データモデル全体に触るため、駅と抱き合わせない |

> **ゲート G1: 静岡県の551行が「説明済みの版ズレ」として表現できること。**
> ここが通らないうちは駅に進まない。

### フェーズ 2 — e-Stat の取り込み

| # | 作業 | 完了条件 |
|---|---|---|
| 2.1 | （ライセンスゲートは 0.4 で済んでいる） | `config/sources.yml` に `estat_boundary` が `required: false` で入っている |
| 2.2 | 47県の取得と `estat_small_area` への格納 | 232,019 行。`HCODE` 8154 が 351 件 |
| 2.3 | 市区町村コードの突合 | **一致 1,889 / 未説明 0**（既に実測済の再現） |
| 2.4 | **47県を結合した状態での県境検証** | 隙間・重なりの件数が出る（**未実測の残件**） |
| 2.5 | `dist/ESTAT_RECONCILE.md` 出力 | — |

> **ゲート G2: 2.3 が 1,889 / 0 を再現すること。** 外したら取り込みが壊れている。

### フェーズ 3 — N02 の取り込み

| # | 作業 | 完了条件 |
|---|---|---|
| 3.1 | （ライセンスゲートは 0.4 で済んでいる） | `config/sources.yml` に `mlit_ksj_n02` が `required: false` で入っている |
| 3.2 | 2025年版のみ取得（年度混在させない） | 取得ファイルが1年度分 |
| 3.3 | `N02_005g` で束ねて `station` を作る | 駅数と `feature_count` の分布が出る |
| 3.4 | 束ねる前後の行数を記録 | 水増し率が分かる |

> **ゲート G3: `N02_005c` / `N02_005g` が実データに存在すること。**
> 無ければ版の選択が誤っている。

### フェーズ 4 — 結合

| # | 作業 | 完了条件 |
|---|---|---|
| 4.1 | 神奈川県だけで点in面を試す | 政令市3市を含むので §4.3 の分類が効く |
| 4.2 | **駅の点での挙動を実測**（埋立地・港湾で落ちない駅の件数） | 件数が出る。**代表点での 99.93% がそのまま適用できるかの検証** |
| 4.3 | 全国で結合 | 全駅がいずれかの分類に入る |
| 4.4 | `bridge_station_municipality` を書き出す | — |
| 4.5 | `dist/STATION_JOIN_REPORT.md` 出力 | 分類別件数と、落ちなかった駅の一覧 |

> **ゲート G4: 未分類の駅が 0 件であること。** 分類できない駅が残るなら
> §4.3 の分類が足りていない。

### フェーズ 5 — 出荷

| # | 作業 | 完了条件 |
|---|---|---|
| 5.1 | `NOTICE.md` に N02・e-Stat の出典を追加 | 発行元の記載例に沿った文言 |
| 5.2 | `README.md` に「市区町村までで町字には届かない」を明記 | — |
| 5.3 | 断面の組を成果物に刻む | `derivation` 列と `SOURCES.yml` の両方 |

---

## 7. 進め方についての提案

### 7.1 小さく確認してから広げる

各フェーズが「1県で試す → 全国」の順になっている。これは e-Stat の検証で実際に
機能した（神奈川で 3件の差を確認 → 全国で 37件、全部説明できた）。

### 7.2 実測値をゲートにする

G2 の「1,889 / 0」、G3 の「駅コードの存在」、G4 の「未分類 0」は、
**既に測ってある値か、定義上0になるべき値**である。実装が外したら実装が誤っている。

### 7.3 「説明できない不一致」をゼロに保つ

`POLICY.md` §4・§5 の実運用として、**不一致を消すのではなく分類する**。
分類できないものが残ったら、それは分類の設計不足として扱う。

### 7.4 残っている未実測項目（正直に）

| # | 内容 | どのフェーズで潰すか |
|---|---|---|
| 1 | 47県結合時の県境（発行元が「接合処理を行っていない」と明記） | 2.4 |
| 2 | 駅の点での点in面（これまでの 99.93% は町字代表点での測定） | 4.2 |
| 3 | 位置参照情報 19.0b が上流最新か | 1.2 |
| 4 | e-Stat の不一致9件が e-Stat 側か代表点側か | 未定（優先度低） |
| 5 | S12 の CC_BY_4.0 / 非商用 の決着 | フェーズ外。後回し |
| 6 | e-Stat JGD2011 版定義書の属性測地系（`.prj` は JGD2011 確定） | 2.1 |

### 7.5 スケジュール感

フェーズ1（断面管理）が最も地味で、最も効く。ここを飛ばすと後で全部やり直しになる。
フェーズ2〜4 は実測済みの手順の実行なので、想定外が出るとすれば
**2.4（県境）と 4.2（駅の点）** の2箇所である。

---

## 8. 参照

| 内容 | URL | 確認日 |
|---|---|---|
| 国土数値情報 データ一覧（最新版の確認） | https://nlftp.mlit.go.jp/ksj/index.html | 2026-09-05 |
| N02 鉄道 2025年版 | https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N02-2025.html | 2026-09-05 |
| N03 行政区域 2026年版（承認番号 R 7JHf 351） | https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03-2026.html | 2026-09-05 |
| e-Stat 境界データダウンロード | https://www.e-stat.go.jp/gis/statmap-search?type=2 | 2026-09-05 |
| 統計地理情報システム機能利用規約 | https://www.e-stat.go.jp/gis-terms | 2026-09-05 |
| e-Stat 利用規約 | https://www.e-stat.go.jp/terms-of-use | 2026-09-05 |
| e-Stat 境界データ 注意事項 | https://www.e-stat.go.jp/pdf/gis/notes/00200521.pdf | 2026-09-05 |
| 国土地理院 地図の利用手続パンフレット | https://www.gsi.go.jp/common/000223838.pdf | 2026-09-05 |

jpac 側の数値はすべて `dist/jp_address_crosswalk.sqlite`（v1.0.0）を 2026-09-05 に集計。
e-Stat の数値は同日に取得した全47県の実ファイルを集計。
