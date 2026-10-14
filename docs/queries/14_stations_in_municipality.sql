-- 市区町村の中の駅と、その駅を通る路線。
-- 政令市の親コード（例: 011002 札幌市）を渡すと、その区（011011 中央区 …）に広げて引きます。
-- 駅は区で市区町村に結ばれていて、親コードには1駅も結ばれていないためです。
-- 乗換駅は路線の数だけ行が出ます（新宿は7路線で7行）。駅は N02 の駅グループ
-- （n02_group_code）単位で、運営会社が違っても同じグループなら1駅です。
-- 境界上で市区町村が決まらない駅は relation_type = 'ambiguous' のまま、候補ごとに出ます。
.parameter set :lg_code '011002'
WITH target AS (
  SELECT lg_code FROM municipality_version
   WHERE lg_code = :lg_code AND is_current = 1
  UNION
  -- 政令市の親（ward が NULL）なら、同じ都道府県・市名で ward を持つ行がその区
  SELECT w.lg_code
    FROM municipality_version p
    JOIN municipality_version w
      ON w.pref = p.pref AND w.city = p.city
     AND w.ward IS NOT NULL AND w.is_current = 1
   WHERE p.lg_code = :lg_code AND p.is_current = 1 AND p.ward IS NULL
)
SELECT m.city, m.ward, s.n02_group_code, s.station_name_raw,
       l.line_name_raw, l.operator_name_raw,
       s.relation_type, s.verification_status, s.match_method
FROM   bridge_station_municipality s
JOIN   target t ON t.lg_code = s.lg_code
JOIN   municipality_version m ON m.lg_code = s.lg_code AND m.is_current = 1
JOIN   bridge_station_line l ON l.n02_group_code = s.n02_group_code
WHERE  s.verification_status <> 'manually_rejected'
ORDER  BY m.lg_code, s.station_name_raw, l.operator_name_raw, l.line_name_raw;
