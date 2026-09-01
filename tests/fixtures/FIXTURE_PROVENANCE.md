# Fixture provenance

Every fixture below was extracted from the genuine official payload named in
`extracted_from`, whose SHA-256 is recorded. Nothing here is hand-written: a
fabricated fixture would test the fabricator, not the source
(docs/TEST_STRATEGY.md §2).

Extraction date: **2026-08-23**.

## Municipalities selected, and why

| JIS code | Reason |
|---|---|
| `01101` | Hokkaido, 政令指定都市 ward (札幌市中央区): ward layer + 丁目 |
| `01209` | 夕張市: split across numbering areas 003 / 004-2 (を除く clause) |
| `01210` | 岩見沢市: split across 008-2 / 010 (宝水町を除く) |
| `01430` | 空知郡南幌町: 郡部 |
| `13104` | 東京都新宿区: 西新宿 1:N postal case named in the spec |
| `20204` | 長野県岡谷市: 市の次に番地がくる場合 |
| `08546` | 茨城県猿島郡境町: 町村の次に番地がくる場合 |
| `13362` | 東京都利島村: 一円 + 5-digit old postal code |
| `26104` | 京都市中京区: 京都通り名 (kyoto_st) + ward layer |

## Files

```json
{
  "abr_city.csv": {
    "extracted_from": "data/raw/abr/city_master.zip",
    "extraction_date": "2026-08-23",
    "rows": 9,
    "selection": "lg_code[0:5] in ['01101', '01209', '01210', '01430', '13104', '20204', '08546', '13362', '26104']",
    "source_sha256": "c719e7394489907c6192f58837d7f61274c05feb72ebec2543995d0d6cb08b5e"
  },
  "abr_postal_conversion.csv": {
    "extracted_from": "data/raw/abr/postal_conversion.zip",
    "extraction_date": "2026-08-23",
    "rows": 5121,
    "selection": "lg_code[0:5] in ['01101', '01209', '01210', '01430', '13104', '20204', '08546', '13362', '26104']; includes municipality-level rows (empty machiaza_id)",
    "source_sha256": "febff4aabc2cd99b35ff395855b89bd6a0457407fa387471c3966b8319dfeeb2"
  },
  "abr_town.csv": {
    "extracted_from": "data/raw/abr/town_master.zip",
    "extraction_date": "2026-08-23",
    "rows": 4697,
    "selection": "lg_code[0:5] in ['01101', '01209', '01210', '01430', '13104', '20204', '08546', '13362', '26104'] + 3 duplicate-key towns",
    "source_sha256": "ea1841164a27e69994cef6d4da5fa46c582eb3eb1d67cf3bb6409408f1cf7463"
  },
  "japanpost_ken_all.csv": {
    "extracted_from": "data/raw/japanpost/ken_all.zip",
    "extraction_date": "2026-08-23",
    "rows": 1623,
    "selection": "jis_city_code in ['01101', '01209', '01210', '01430', '13104', '20204', '08546', '13362', '26104']; includes 以下に掲載がない場合 / 次に番地 / 一円 records",
    "source_sha256": "0b52620fb659846e1893416b333e23154243060325ff1d3d3747b221b915a19d"
  },
  "mic_shigai_list.tsv": {
    "extracted_from": "data/raw/mic_area_code/shigai_list.doc",
    "extraction_date": "2026-08-23",
    "rows": 12,
    "selection": "clauses covering the fixture municipalities, incl. を除く / に限る cases",
    "source_sha256": "39cd71ef05ecd6461ef2b58115a1c19ec47784643d62521f3c484d0cf26ba4fa"
  },
  "mlit_isj.csv": {
    "extracted_from": [
      "data/raw/mlit/isj_01.zip",
      "data/raw/mlit/isj_13.zip",
      "data/raw/mlit/isj_20.zip",
      "data/raw/mlit/isj_08.zip",
      "data/raw/mlit/isj_26.zip"
    ],
    "extraction_date": "2026-08-23",
    "rows": 2717,
    "selection": "市区町村コード in ['01101', '01209', '01210', '01430', '13104', '20204', '08546', '13362', '26104']",
    "source_sha256": [
      "da269f15dbc0350f31ead4772c1cd4e01b4d8056152c96fb7c8f4231fdae060c",
      "37b1dbc7a059d8517e1def20a243a5aa8e371fda5ece56767a9f858caed9c2ed",
      "7ba65d8d6276cf35a15d66047c09c29e1c02ea1ba1abc5ebd484c63ff5cf56b3",
      "366597c1f35722087e54c4a402b22745dbe15778b692d14dbe93e1e522d35e96",
      "9a6e7301e7d379cbe5baff85c0eff8b2c4ab540f9ffcb86bf4baf1f694688d47"
    ]
  }
}
```
