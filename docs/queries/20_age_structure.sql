-- 年齢3区分（0〜14歳・15〜64歳・65歳以上）と年齢不詳、65歳以上の割合。男女別と総数。
-- 割合の分母は年齢が分かっている人だけです（年齢「不詳」を分母から除く）。
-- 平均年齢と年齢中位数は5歳階級から計算せず、公表値（census_population_age_summary）を使います。
-- age_class_raw は「01_0～4歳」の形で、先頭2桁が区分の番号です（22 が年齢「不詳」）。
.parameter set :lg_code '131041'
SELECT a.lg_code, a.area_name_raw, a.sex_raw,
       SUM(CASE WHEN substr(a.age_class_raw, 1, 2) BETWEEN '01' AND '03' THEN a.population END) AS age_0_14,
       SUM(CASE WHEN substr(a.age_class_raw, 1, 2) BETWEEN '04' AND '13' THEN a.population END) AS age_15_64,
       SUM(CASE WHEN substr(a.age_class_raw, 1, 2) BETWEEN '14' AND '21' THEN a.population END) AS age_65_plus,
       SUM(CASE WHEN substr(a.age_class_raw, 1, 2) = '22'                THEN a.population END) AS age_unknown,
       ROUND(100.0 * SUM(CASE WHEN substr(a.age_class_raw, 1, 2) BETWEEN '14' AND '21' THEN a.population END)
             / SUM(CASE WHEN substr(a.age_class_raw, 1, 2) <> '22' THEN a.population END), 1) AS pct_65_plus,
       s.mean_age, s.median_age
FROM   census_population_age a
JOIN   census_population_age_summary s
       ON s.region_key = a.region_key
      AND s.nationality_raw = a.nationality_raw AND s.sex_raw = a.sex_raw
WHERE  a.lg_code = :lg_code AND a.census_year = 2025
  AND  a.nationality_raw = '0_国籍総数'
GROUP  BY a.region_key, a.sex_raw
ORDER  BY a.sex_raw;
