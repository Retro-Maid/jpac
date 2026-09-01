-- 旧郵便番号 -> 現行郵便番号. One old code commonly maps to many current codes.
.parameter set :old '160'
SELECT DISTINCT old_postal_code, postal_code, pref, city, town, record_kind
FROM   postal_record_version
WHERE  old_postal_code = :old AND is_current = 1
ORDER  BY postal_code;
