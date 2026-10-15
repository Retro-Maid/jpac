-- 郵便番号 → 町字 → その町字にある学校（文部科学省 学校コード）。
-- 学校は所在地の文字列で町字に結んでいます（座標は持ちません）。結べたのは町字が1つに
-- 決まった学校（located_in）と、同じ名前の町字が複数ある学校（ambiguous、候補ごとに1行）
-- だけです。町字まで決まらなかった学校（municipality_only）はここには出ません。
-- postal_check は、学校の郵便番号がその町字の郵便番号と合うか（differs は大学などの
-- 事業所個別番号のことが多い）。廃止した学校も is_active = 0 で出ます。
.parameter set :postal_code '1600023'
SELECT p.postal_code, a.full_name_raw AS town_name,
       s.school_code, s.school_type_name, s.founder_name, s.school_name_raw,
       s.address_raw, s.is_active,
       b.relation_type, b.postal_check, b.verification_status
FROM   bridge_address_postal_code p
JOIN   address a ON a.address_id = p.address_id
JOIN   bridge_school_address b ON b.address_id = p.address_id
JOIN   mext_school s ON s.school_code = b.school_code
WHERE  p.postal_code = :postal_code AND p.is_current = 1
  AND  p.verification_status <> 'manually_rejected'
ORDER  BY s.is_active DESC, s.school_type_code, s.school_name_raw;
