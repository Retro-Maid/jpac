-- 市区町村の中の、今ある学校の数を学校種ごとに。町字まで決まった数も並べます。
-- 政令市の親コード（例: 011002 札幌市）を渡すと、その区に広げて数えます。学校は区に
-- 結ばれていて、親コードには1校も結ばれていないためです。
-- 町字の候補が複数ある学校（ambiguous）は候補の数だけ行があるので、学校コードで数えます。
.parameter set :lg_code '011002'
WITH target AS (
  SELECT lg_code FROM municipality_version
   WHERE lg_code = :lg_code AND is_current = 1
  UNION
  SELECT w.lg_code
    FROM municipality_version p
    JOIN municipality_version w
      ON w.pref = p.pref AND w.city = p.city
     AND w.ward IS NOT NULL AND w.is_current = 1
   WHERE p.lg_code = :lg_code AND p.is_current = 1 AND p.ward IS NULL
)
SELECT s.school_type_code, s.school_type_name,
       COUNT(DISTINCT s.school_code) AS schools,
       COUNT(DISTINCT CASE WHEN b.relation_type = 'located_in' THEN s.school_code END)
         AS located_in_one_town,
       COUNT(DISTINCT CASE WHEN b.relation_type = 'municipality_only' THEN s.school_code END)
         AS municipality_only
FROM   bridge_school_address b
JOIN   target t ON t.lg_code = b.lg_code
JOIN   mext_school s ON s.school_code = b.school_code
WHERE  s.is_active = 1 AND b.verification_status <> 'manually_rejected'
GROUP  BY s.school_type_code, s.school_type_name
ORDER  BY s.school_type_code;
