-- 都道府県内の市区町村ごとのバス停の数と、人口1万人あたりの数。
-- 数えるのは「市区町村が1つに決まったバス停」だけです。境界上で決まらないもの（ambiguous）は
-- 候補の両方に1行ずつあるので、別の列に出して足し込みません。
-- 政令市の親の行（area_level = '1'）は除きます。バス停は区で結ばれているためです。
-- 時点がずれている点に注意: バス停は P11 令和4年度版（2022年度）、人口は2025年10月1日です。
.parameter set :pref '香川県'
SELECT m.lg_code, m.city, m.ward,
       COUNT(DISTINCT CASE WHEN b.relation_type = 'contains'  THEN b.p11_stop_id END) AS bus_stops,
       COUNT(DISTINCT CASE WHEN b.relation_type = 'ambiguous' THEN b.p11_stop_id END) AS bus_stops_ambiguous,
       p.population_total,
       ROUND(10000.0 * COUNT(DISTINCT CASE WHEN b.relation_type = 'contains' THEN b.p11_stop_id END)
             / p.population_total, 2) AS stops_per_10k
FROM   municipality_version m
LEFT   JOIN bridge_bus_stop_municipality b ON b.lg_code = m.lg_code
LEFT   JOIN census_population_municipality p
       ON p.lg_code = m.lg_code AND p.census_year = 2025
WHERE  m.pref = :pref AND m.is_current = 1
  AND  (p.area_level IS NULL OR p.area_level <> '1')
GROUP  BY m.lg_code
ORDER  BY stops_per_10k DESC, m.lg_code;
