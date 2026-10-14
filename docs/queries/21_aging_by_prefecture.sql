-- 都道府県内の市区町村を、65歳以上の割合が高い順に並べる。
-- 割合で比べるので、政令市は市全体の行と区の行が両方出ます（足し算ではないので二重計上には
-- なりません）。区だけで比べたいときは AND a.area_level_raw <> '1' を足してください。
-- lg_code が NULL の行（全国・都道府県・特別区部・2000年の境域の市区町村）は除いています。
-- 割合の分母は年齢が分かっている人だけです。
.parameter set :pref_code '13'
SELECT a.lg_code, a.area_name_raw, a.area_level_raw,
       SUM(CASE WHEN substr(a.age_class_raw, 1, 2) <> '22' THEN a.population END) AS known_age_total,
       ROUND(100.0 * SUM(CASE WHEN substr(a.age_class_raw, 1, 2) BETWEEN '14' AND '21' THEN a.population END)
             / SUM(CASE WHEN substr(a.age_class_raw, 1, 2) <> '22' THEN a.population END), 1) AS pct_65_plus
FROM   census_population_age a
WHERE  a.census_year = 2025 AND a.lg_code IS NOT NULL
  AND  substr(a.lg_code, 1, 2) = :pref_code
  AND  a.nationality_raw = '0_国籍総数' AND a.sex_raw = '0_総数'
GROUP  BY a.region_key
ORDER  BY pct_65_plus DESC, a.lg_code;
