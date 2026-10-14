-- 市区町村の概要を1行で: 名称、人口・世帯、駅・路線・バス停・郵便番号・市外局番の数。
-- 駅とバス停は「市区町村が1つに決まったもの（contains）」と「候補が複数で決まらなかった
-- もの（ambiguous）」を分けて数えます。ambiguous は候補の市区町村すべてに1行ずつあるので、
-- 両方を足すと二重に数えます。
-- 政令市の親コード（011002 札幌市など）には駅もバス停も結ばれていません。区で受けて
-- いるためです。政令市全体を見るときはクエリ14の書き方で区に広げてください。
.parameter set :lg_code '131041'
SELECT m.lg_code, m.pref, m.city, m.ward,
       p.population_total, p.households_total, p.population_change_5y, p.value_status,
       (SELECT COUNT(DISTINCT n02_group_code) FROM bridge_station_municipality
         WHERE lg_code = m.lg_code AND relation_type = 'contains')    AS stations,
       (SELECT COUNT(DISTINCT n02_group_code) FROM bridge_station_municipality
         WHERE lg_code = m.lg_code AND relation_type = 'ambiguous')   AS stations_ambiguous,
       (SELECT COUNT(*) FROM bridge_line_municipality
         WHERE lg_code = m.lg_code AND relation_type = 'overlap')     AS lines,
       (SELECT COUNT(DISTINCT p11_stop_id) FROM bridge_bus_stop_municipality
         WHERE lg_code = m.lg_code AND relation_type = 'contains')    AS bus_stops,
       (SELECT COUNT(DISTINCT p11_stop_id) FROM bridge_bus_stop_municipality
         WHERE lg_code = m.lg_code AND relation_type = 'ambiguous')   AS bus_stops_ambiguous,
       (SELECT COUNT(DISTINCT postal_code) FROM bridge_municipality_postal_code
         WHERE lg_code = m.lg_code AND relation_type <> 'unresolved') AS postal_codes,
       (SELECT GROUP_CONCAT(DISTINCT t.area_code)
          FROM bridge_municipality_telephone b
          JOIN telephone_area_version t
            ON t.numbering_area_code = b.numbering_area_code AND t.is_current = 1
         WHERE b.lg_code = m.lg_code AND b.relation_type <> 'unresolved') AS area_codes
FROM   municipality_version m
LEFT   JOIN census_population_municipality p
       ON p.lg_code = m.lg_code AND p.census_year = 2025
WHERE  m.lg_code = :lg_code AND m.is_current = 1;
