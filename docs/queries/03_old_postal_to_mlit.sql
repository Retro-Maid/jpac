-- 旧郵便番号 -> 現行郵便番号 -> ABR 町字 -> 国土交通省 大字町丁目コード.
.parameter set :old '160'
SELECT p.old_postal_code, x.postal_code, x.town_name,
       x.mlit_code, x.mlit_latitude, x.mlit_longitude,
       x.mlit_relation_type, x.mlit_rule, x.mlit_confidence
FROM   postal_record_version p
JOIN   address_crosswalk x ON x.postal_code = p.postal_code
WHERE  p.old_postal_code = :old AND p.is_current = 1
ORDER  BY x.postal_code, x.town_name;
