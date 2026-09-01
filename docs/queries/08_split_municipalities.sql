-- Municipalities served by more than one numbering area (夕張市, 岩見沢市, …).
-- A 1:1 市外局番 = 市区町村 model would misrepresent every row here.
SELECT b.lg_code, m.pref, m.city, m.ward,
       COUNT(DISTINCT b.target_id) AS numbering_areas,
       GROUP_CONCAT(DISTINCT b.coverage_type) AS coverage_types
FROM   bridge_municipality_telephone b
JOIN   municipality_version m ON m.lg_code = b.lg_code AND m.is_current = 1
WHERE  b.relation_type <> 'unresolved'
GROUP  BY b.lg_code
HAVING numbering_areas > 1
ORDER  BY numbering_areas DESC, b.lg_code;
