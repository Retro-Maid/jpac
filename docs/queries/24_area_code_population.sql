-- 市外局番の番号区画ごとの人口。
-- 値があるのは、区画に属する市区町村がすべて丸ごと入っている区画だけです。市区町村の一部
-- だけを含む区画は按分せずに NULL とし、理由を value_status に残しています。
--   complete              区画 = 市区町村の和。人口あり
--   partial_municipality  一部だけ含む市区町村がある。NULL（例: 0465）
--   unresolved_member     市区町村に解決できない収容句がある。NULL（例: 03）
.parameter set :area_code '011'
SELECT t.area_code, t.area_text_raw, c.numbering_area_code,
       c.municipality_count, c.population_total, c.households_total, c.value_status
FROM   telephone_area_version t
JOIN   census_population_numbering_area c
       ON c.numbering_area_code = t.numbering_area_code AND c.census_year = 2025
WHERE  t.area_code = :area_code AND t.is_current = 1
ORDER  BY c.numbering_area_code;
