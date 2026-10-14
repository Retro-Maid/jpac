-- 郵便番号 → 市区町村 → その市区町村の駅。
-- これは「その郵便番号の区域の最寄り駅」ではありません。jpac は駅を市区町村までしか
-- 結ばないので、郵便番号が属する市区町村にある駅をすべて返します。
-- 1つの郵便番号が2つの市区町村にまたがる場合は、両方の駅が出ます（lg_code で区別）。
-- address_crosswalk ビューでも書けますが、ビューは旧郵便番号の集約を全件で作るので
-- 数分かかります。郵便番号から市区町村を引くだけならブリッジを直接使うほうが速いです。
-- 町字側のブリッジ（bridge_address_*）の lg_code 列は NULL です。市区町村は address から引きます。
.parameter set :postal_code '1600023'
WITH towns AS (
  SELECT DISTINCT a.lg_code
  FROM   bridge_address_postal_code b
  JOIN   address a ON a.address_id = b.address_id
  WHERE  b.postal_code = :postal_code
    AND  b.is_current = 1 AND b.verification_status <> 'manually_rejected'
)
SELECT :postal_code AS postal_code, m.lg_code, m.pref, m.city, m.ward,
       s.n02_group_code, s.station_name_raw, s.operator_name_raw, s.relation_type
FROM   towns t
JOIN   municipality_version m ON m.lg_code = t.lg_code AND m.is_current = 1
JOIN   bridge_station_municipality s
       ON s.lg_code = t.lg_code AND s.verification_status <> 'manually_rejected'
ORDER  BY m.lg_code, s.station_name_raw, s.n02_group_code;
