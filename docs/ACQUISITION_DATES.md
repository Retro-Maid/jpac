# 取得日の記録（署名つき）

このプロジェクトのビルドは**ネットワークを一切触らない**（`jpac build` の docstring、
spec §45）。だから「この payload をいつ取得したか」をビルドは自分では観測できない。
記録が無いと `downloaded_at` がビルド時刻に落ち、リリースタグの `+data-YYYY-MM-DD` が
**取得日ではなくビルド日**を指す。v1.1.0 と v1.2.0 のリリースノートはその状態を明示して
出している。

取得日は**人が署名して記録する主張**である。ライセンスの逐語読み
（`LICENSE_REVIEW_*.md`）や市区町村の承継（`overrides/municipality_lineage.yml`）と同じ
扱いで、署名の無い記録は作らない。

## 署名

| ソース | 取得日（JST） | mtime 以外の根拠 |
|---|---|---|
| `abr`（デジタル庁 ABR 町字/市区町村/都道府県/郵便番号変換表） | **2026-08-23** | `README.md` の出典表、`dist/SOURCES.yml` v1.0.0 |
| `japanpost`（ken_all / add / del） | **2026-08-23** | 同上 |
| `mlit`（位置参照情報 大字・町丁目レベル 47ファイル） | **2026-08-23** | 同上 |
| `mic_area_code`（市外局番一覧） | **2026-08-23** | 同上 |
| `mic_number_assignment`（固定電話 番号指定状況 9ファイル） | **2026-08-23** | 同上 |
| `mlit_ksj_n02`（国土数値情報 鉄道 N02 2025年版） | **2026-09-05** | `STATION_JOIN_PREFLIGHT.md`（同日に取得して実測） |
| `estat_boundary`（令和2年国勢調査 小地域境界 47ファイル） | **2026-09-05** | `STATION_JOIN_PREFLIGHT.md`（同日に全47県を取得して実測） |
| `mlit_ksj_p11`（国土数値情報 バス停留所 P11 令和4年度版） | **2026-09-17** | `LICENSE_REVIEW_P11_2026_09.md`（同日の逐語読みと取得） |

- **attested_by**: `retro.maid.itworker@gmail.com`
- **attested_on**: 2026-09-23

## 根拠を2つ置いている理由

**1. ファイルシステムの mtime。** payload が `data/raw/` に書かれた時刻そのもの。
生成される `_payload.yml` の `downloaded_at` は各ファイルの mtime をそのまま書く
（JST、`+09:00`）—— 日付だけに丸めると、観測していない時刻（00:00:00）を主張すること
になる。

**2. 上の表の文書。** mtime は書き換えられる値であり、コピーやアーカイブ展開で失われる
こともある。だから mtime 単独では署名の根拠にしない。**上の文書が同じ日付を独立に記録
している**ことをもって署名している。

## どこに書かれ、どこから読まれるか

```
docs/ACQUISITION_DATES.md          この署名（リポジトリに残る）
tools/write_payload_manifests.py   同じ表を持ち、manifest を生成する
  ↓
data/raw/<source>/_payload.yml     生成物。.gitignore 済みなのでこのマシンにしか無い
  ↓
src/jp_address_crosswalk/payload.py    load_payload_manifest が読む
  ↓
source_snapshot.downloaded_at → SOURCES.yml、リリースタグの +data-YYYY-MM-DD
```

`data/raw/` は `.gitignore` 済みなので、`_payload.yml` はリポジトリに入らない。**署名
そのものはリポジトリに残る必要がある** —— 誰が何を根拠にこの日付を主張したのかが
payload と一緒に消えては、来歴の記録として意味がない。だから署名はこのファイルと
`tools/write_payload_manifests.py` にあり、`_payload.yml` はそこからの派生物にしてある。

## 書き直すとき

```bash
py -3.12 tools/write_payload_manifests.py           # 生成する
py -3.12 tools/write_payload_manifests.py --check   # 署名と一致しているか見るだけ
```

**mtime が署名された日付と合わなければ、何も書かない。** 別の payload に差し替わって
いる可能性のほうが、記録が古いままである可能性より重い。取得し直したときの手順は:

1. このファイルの表に新しい取得日を人が書き、署名する
2. `tools/write_payload_manifests.py` の `ATTESTED` を同じ値にする
3. 生成し直す

## 記録しないこと

**ライセンスの節（`license:`）は `_payload.yml` に置かない。** terms のハッシュは
`config/sources.yml` の committed baseline が持っており、manifest に観測値を書くと
「このビルドが terms ページを見た」という主張になる —— 見ていない。取得側が実際に
terms を観測したときに初めて書く節である（`payload.py` の `PayloadManifest`）。

**download_url も書かない。** 取得時の URL は `config/sources.yml` の landing page と
`SOURCES.yml` が記録している。manifest に書けるのは「取得側が観測した URL」であって、
それはこのマシンには残っていない。
