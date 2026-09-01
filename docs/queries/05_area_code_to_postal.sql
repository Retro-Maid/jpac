-- 市外局番 -> 番号区画 -> 市区町村 -> 郵便番号.
-- coverage_type tells you whether MIC asserted the whole municipality.
.parameter set :area_code '0123'
SELECT DISTINCT t.area_code, b.target_id AS numbering_area_code,
       b.lg_code, b.coverage_type, b.matching_rule_id, x.postal_code
FROM   telephone_area_version t
JOIN   bridge_municipality_telephone b ON b.target_id = t.numbering_area_code
JOIN   address a ON a.lg_code = b.lg_code
JOIN   address_crosswalk x ON x.address_id = a.address_id
WHERE  t.area_code = :area_code AND t.is_current = 1
ORDER  BY b.coverage_type, x.postal_code;
