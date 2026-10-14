-- 市区町村が1つに決まらなかった駅とバス停（要確認の行）。
--   ambiguous  候補が複数（旧浜松市北区の分割、府県境をまたぐ駅、境界の重なりなど）
--   unresolved どの境界にも入らなかった（埋立地・水域など）。最寄りに寄せていません
-- 1つの駅・バス停を1行にまとめ、候補の市区町村を ; 区切りで並べます。
SELECT 'station' AS kind, s.n02_group_code AS id, s.station_name_raw AS name,
       s.operator_name_raw AS operator, s.relation_type, s.match_method,
       COUNT(*) AS candidate_rows,
       GROUP_CONCAT(s.lg_code, ';') AS candidate_lg_codes
FROM   bridge_station_municipality s
WHERE  s.verification_status = 'review_required'
GROUP  BY s.n02_group_code
UNION ALL
SELECT 'bus_stop', b.p11_stop_id, b.stop_name_raw, b.operator_name_raw,
       b.relation_type, b.match_method,
       COUNT(*), GROUP_CONCAT(b.lg_code, ';')
FROM   bridge_bus_stop_municipality b
WHERE  b.verification_status = 'review_required'
GROUP  BY b.p11_stop_id
ORDER  BY kind DESC, relation_type, id;
