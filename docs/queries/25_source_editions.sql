-- このリリースが読んだ元データの一覧: 発行元、データセット、版、取得日、規約。
-- 画面に出典を表示するときや、元データが更新されたかを確かめるときの起点です。
-- 都道府県ごと・番号ごとに分かれたファイル（mlit_isj_01 〜 47、estat_boundary_pref_01 〜 47、
-- mic_fixed_phone_1 〜 9）は、末尾の番号を落として1行にまとめます。
-- 「__aggregate_of_N」の行は分割ファイルを束ねた記録なので、ここでは数えません。
SELECT provider,
       rtrim(rtrim(dataset_name, '0123456789'), '_')  AS dataset,
       COUNT(*)                                       AS files,
       GROUP_CONCAT(DISTINCT source_version)           AS source_versions,
       MIN(downloaded_at)                             AS first_downloaded,
       MAX(downloaded_at)                             AS last_downloaded,
       license_name, source_page_url
FROM   source_snapshot
WHERE  dataset_name NOT LIKE '%\_\_aggregate\_of\_%' ESCAPE '\'
GROUP  BY provider, dataset, license_name, source_page_url
ORDER  BY provider, dataset;
