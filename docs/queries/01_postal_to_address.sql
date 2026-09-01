-- 郵便番号 -> ABR 町字 (spec §72). Returns every covered town, with evidence.
.parameter set :postal_code '1600023'
SELECT town_name, machiaza_id,
       postal_relation_type, postal_rule, postal_confidence,
       postal_candidate_count, postal_status
FROM   address_crosswalk
WHERE  postal_code = :postal_code
ORDER  BY town_name;
