# ライセンス審査: e-Stat 廃置分合等情報 / 総務省 全国地方公共団体コード

`docs/LICENSE_POLICY.md` §4 のゲートを通すのは**人の署名**であって、AI の下読みでは
ない。この文書はその区別を保ったまま、市区町村の廃置分合データの判断を記録する。

結論を先に書く（§1 の署名が済むまでは**案**である）。

| ソース | 規約 | 判断（案） |
|---|---|---|
| e-Stat 廃置分合等情報（CSV） | e-Stat 利用規約（政府標準利用規約 第2.0版 準拠、CC BY 4.0 互換） | 採用可 |
| e-Stat 市区町村を探す（時点一覧） | 同上 | 採用可 |
| 総務省 全国地方公共団体コード（団体コード表・改正一覧表） | 総務省サイトの利用条件（公共データ利用規約 第1.0版 = PDL 1.0） | 採用可 |

---

## 1. 人による確認（未署名）

メンテナが各規約ページを自分で開き、下の §3 の読みが正しいことを確認したら、ここに
日付と確認者を記入する。`config/sources.yml` の `license.reviewed_on` / `reviewed_by` は
この記入を指す。

- e-Stat 利用規約 <https://www.e-stat.go.jp/terms-of-use>
  - 確認者:
  - 確認日:
- 総務省 著作権について <https://www.soumu.go.jp/menu_kyotsuu/policy/tyosaku.html>
  - 確認者:
  - 確認日:

特に確かめてほしい点:

1. e-Stat 利用規約の別紙（適用除外）に、`/municipalities`（廃置分合等情報・市区町村を探す）が
   含まれていないこと
2. 総務省ページの「権利表記の記載がない限り」の条件に、団体コードのページ
   （<https://www.soumu.go.jp/denshijiti/code.html>）が該当すること

---

## 2. 規約上の筋道

- **e-Stat** は jpac の既存ソース `estat_boundary` と同じ規約である
  （`docs/LICENSE_REVIEW_2026_09.md` §1.1）。境界データは統計地理情報システムの機能利用規約を
  経由して e-Stat 利用規約に帰着したが、廃置分合等情報は e-Stat 本体のコンテンツなので、
  直接 e-Stat 利用規約が適用される。
- **総務省** は jpac の既存ソース `mic_area_code`（市外局番一覧）と同じ条件（PDL 1.0）である。

どちらも、出典の記載と、編集・加工した旨の記載を条件に、複製・公衆送信・翻案・商用利用が
できる。政府標準利用規約と PDL は別物として記録する（`docs/GEO_EXPANSION_RESEARCH.md` §7 の6）。

### 2.1 出典表示（案）

```
「廃置分合等情報」（政府統計の総合窓口（e-Stat）、https://www.e-stat.go.jp/）を加工して jp-address-crosswalk 作成
「全国地方公共団体コード」（総務省）https://www.soumu.go.jp/denshijiti/code.html をもとに jp-address-crosswalk 作成
```

---

## 3. ⚠️ AI による下読み（人の判断ではない）

以下は AI が 2026-09-27 に読んだ内容であって、§1 の人の確認を置き換えるものではない。
P11 の審査では AI の下読みが一度誤答している（`docs/LICENSE_REVIEW_P11_2026_09.md` §3）。

**e-Stat 利用規約**（<https://www.e-stat.go.jp/terms-of-use>）:

> 当サイトで公開している情報（以下「コンテンツ」といいます。）は、どなたでも以下の１）～６）に従って、複製、公衆送信、翻訳・変形等の翻案等、自由に利用できます。商用利用も可能です。

> また、数値データ、簡単な表・グラフ等は著作権の対象ではありませんので、これらについては本利用ルールの適用はなく、自由に利用できます。

> イ　コンテンツを編集・加工等して利用する場合は、上記出典とは別に、編集・加工等を行ったことを記載してください。なお、編集・加工した情報を、あたかも国（又は府省等）が作成したかのような態様で公表・利用してはいけません。

> 本利用ルールは、政府標準利用規約（第2.0版）に準拠しています。

下読みでは、別紙の適用除外（ロゴ、API、統計地理情報システムなど）に `/municipalities` は
含まれていなかった。画面のダウンロード機能を使うので、API 利用規約は関係しない。

**総務省**（<https://www.soumu.go.jp/menu_kyotsuu/policy/tyosaku.html>）:

> …特記されていない限り総務省に帰属し、権利表記の記載がない限り「公共データ利用規約（第1.0版）」に準拠した利用条件の下で利用することができます。

---

## 4. 取得範囲（採用する場合）

| | |
|---|---|
| 取得対象 | e-Stat 廃置分合等情報 CSV（境界変更を除く全事由、全都道府県、1970-01-01〜2028-12-31）／ 同 新設・廃止で絞り込んだ CSV 2本 ／ 市区町村を探す（取得日時点）／ 総務省 団体コード表・改正一覧表 |
| 形式 | CSV（UTF-8、BOM なし）、xlsx |
| 記録するもの | 取得したクエリ URL の全文、SHA-256、バイト数、取得日時 |

### 4.1 下読みで使ったクエリ（2026-09-27）

CSV はリクエストのたびに生成されるので、**クエリの URL そのものが来歴になる**。本体の
URL は次のとおり（1行）。

```
https://www.e-stat.go.jp/municipalities/cities/absorption-separation-of-municipalities?pf%5B1%5D=1&pf%5B2%5D=2&pf%5B3%5D=3&pf%5B4%5D=4&pf%5B5%5D=5&pf%5B6%5D=6&pf%5B7%5D=7&pf%5B8%5D=8&pf%5B9%5D=9&pf%5B10%5D=10&pf%5B11%5D=11&pf%5B12%5D=12&pf%5B13%5D=13&pf%5B14%5D=14&pf%5B15%5D=15&pf%5B16%5D=16&pf%5B17%5D=17&pf%5B18%5D=18&pf%5B19%5D=19&pf%5B20%5D=20&pf%5B21%5D=21&pf%5B22%5D=22&pf%5B23%5D=23&pf%5B24%5D=24&pf%5B25%5D=25&pf%5B26%5D=26&pf%5B27%5D=27&pf%5B28%5D=28&pf%5B29%5D=29&pf%5B30%5D=30&pf%5B31%5D=31&pf%5B32%5D=32&pf%5B33%5D=33&pf%5B34%5D=34&pf%5B35%5D=35&pf%5B36%5D=36&pf%5B37%5D=37&pf%5B38%5D=38&pf%5B39%5D=39&pf%5B40%5D=40&pf%5B41%5D=41&pf%5B42%5D=42&pf%5B43%5D=43&pf%5B44%5D=44&pf%5B45%5D=45&pf%5B46%5D=46&pf%5B47%5D=47&city_nm=&reason_kd%5B1%5D=1&reason_kd%5B2%5D=2&reason_kd%5B6%5D=6&reason_kd%5B7%5D=7&reason_kd%5B16%5D=16&reason_kd%5B4%5D=4&reason_kd%5B5%5D=5&reason_kd%5B8%5D=8&reason_kd%5B9%5D=9&reason_kd%5B10%5D=10&reason_kd%5B11%5D=11&reason_kd%5B12%5D=12&reason_kd%5B13%5D=13&reason_kd%5B15%5D=15&form_id=city_merger_form&source=setup&year_from=1970&month_from=1&day_from=1&keyword_kd=code&city_kd%5B2%5D=2&city_kd%5B3%5D=3&city_kd%5B4%5D=4&city_kd%5B5%5D=5&city_kd%5B6%5D=6&city_kd%5B7%5D=7&border_kd=0&year_to=2028&month_to=12&day_to=31&item%5B%5D=jiyuKind&item%5B%5D=sityoNaiGunNm&item%5B%5D=sityoNaiGunKana&item%5B%5D=htCode&item%5B%5D=todoNm&item%5B%5D=parentCityNm&item%5B%5D=parentCityKana&item%5B%5D=selfCityNm&item%5B%5D=selfCityKana&item%5B%5D=htCodeSDate&item%5B%5D=kaiseiJiyu&item%5B%5D=haitibungoKokujiDate&item%5B%5D=haitibungoKokujiNo&sort%5B%5D=htCodeSDate-asc&sort%5B%5D=htCode-asc&file_format=csv&charset=UTF-8&bom=0&op=download
```

新設・廃止の2本は、この URL に引数を1つ足しただけで、他は同一である（3本は同じ時点で
取ること。`build/municipality_change.py` は、絞り込み2本の (施行日, コード) が本体に
無ければ止まる）。

| リソースのキー | 足す引数 | bytes | sha256 |
|---|---|---:|---|
| `changes`（本体） | なし | 978,849 | `8abb622827b01041e2919d8d786c940a29384b9342551c19ad01f2bd73e3da0c` |
| `changes_created`（新設） | `hs_flg%5B1%5D=1` | 218,467 | `ec572a1cdb929b72fa4a494298520b8867c198cfa94d7b0351857583df796431` |
| `changes_abolished`（廃止） | `hs_flg%5B2%5D=2` | 690,789 | `8e8e0568fbab0f88260399bc4a571e5ad1f3b57c1a5ea2a016968eec0aac2987` |

本体は同じ URL を2回取得して SHA-256 が一致した（ファイル名の時刻部分だけが毎回変わる）。
上の値は下読みの観測であって、受け入れ時には取得側が改めて計算する。

`config/sources.yml` の `discovery.observed_sha256` / `observed_bytes` と
`artifacts[].text_sha256` は、**取得側が実ファイル・実ページから計算した値**でなければ
ならない。この文書はそれらを代替しない。

---

## 5. 参照

- `docs/LICENSE_POLICY.md` §4 — ドリフト検知と、自動受理が無いこと
- `docs/LICENSE_REVIEW_2026_09.md` §1.1 — e-Stat 利用規約の先行審査（境界データ）
- `docs/MUNICIPALITY_LINEAGE_PREFLIGHT.md` — 取得元と実測
