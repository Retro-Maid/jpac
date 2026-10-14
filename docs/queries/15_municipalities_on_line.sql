-- 路線が通る市区町村と、その人口・その路線の駅の数。
-- 路線は (路線名, 運営会社) の組で識別します。「山手線」は東日本旅客鉄道と神戸市の2つが
-- あるので、運営会社まで指定してください。
-- 路線と市区町村は「通っている」（overlap）関係です。8つの区を通る路線は8行が正しい答えで、
-- 曖昧さではありません。
-- 人口は区の行です。路線は政令市の親コードに結ばれないので、ここで足しても二重になりません。
-- 駅の数が0の市区町村は、線路が通過するだけで、その路線の駅が無いところです。
.parameter set :line_name '山手線'
.parameter set :operator '東日本旅客鉄道'
SELECT b.lg_code, m.pref, m.city, m.ward,
       b.sample_hits, b.municipality_count,
       (SELECT COUNT(DISTINCT sm.n02_group_code)
          FROM bridge_station_line sl
          JOIN bridge_station_municipality sm
            ON sm.n02_group_code = sl.n02_group_code
           AND sm.lg_code = b.lg_code AND sm.relation_type = 'contains'
         WHERE sl.line_name_raw = b.line_name_raw
           AND sl.operator_name_raw = b.operator_name_raw) AS stations_on_this_line,
       p.population_total
FROM   bridge_line_municipality b
JOIN   municipality_version m ON m.lg_code = b.lg_code AND m.is_current = 1
LEFT   JOIN census_population_municipality p
       ON p.lg_code = b.lg_code AND p.census_year = 2025
WHERE  b.line_name_raw = :line_name AND b.operator_name_raw = :operator
  AND  b.relation_type = 'overlap'
ORDER  BY b.lg_code;
