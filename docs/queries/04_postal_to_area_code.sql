-- 郵便番号 -> 自治体レベルの市外局番候補.
-- MIC does not publish 町字 mappings; this join must not be described as one.
.parameter set :postal_code '0640941'
SELECT DISTINCT x.postal_code, t.area_code,
       b.target_id AS numbering_area_code,
       b.relation_type, b.coverage_type, b.confidence
FROM   address_crosswalk x
JOIN   bridge_municipality_telephone b ON b.lg_code = x.lg_code
JOIN   telephone_area_version t
       ON t.numbering_area_code = b.target_id AND t.is_current = 1
WHERE  x.postal_code = :postal_code
ORDER  BY t.area_code;
