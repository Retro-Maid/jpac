-- 大字町丁目コード -> ABR 町字 -> 郵便番号.
.parameter set :mlit_code '011010001001'
SELECT mlit_code, town_name, postal_code,
       postal_relation_type, postal_rule, postal_confidence
FROM   address_crosswalk
WHERE  mlit_code = :mlit_code
ORDER  BY postal_code;
