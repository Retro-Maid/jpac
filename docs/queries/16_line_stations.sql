-- 路線の駅と、各駅の市区町村。
-- N02 は駅の並び順（起点から何番目か）を持たないので、この一覧は市区町村コード順です。
-- 運行順の並びが必要な用途には使えません。
-- 市区町村が決まらない駅は、lg_code が NULL（unresolved）か、候補ごとの複数行（ambiguous）です。
.parameter set :line_name '京王線'
.parameter set :operator '京王電鉄'
SELECT l.n02_group_code, l.station_name_raw,
       s.lg_code, m.pref, m.city, m.ward,
       s.relation_type, s.verification_status, s.candidate_count
FROM   bridge_station_line l
LEFT   JOIN bridge_station_municipality s
       ON s.n02_group_code = l.n02_group_code
      AND s.verification_status <> 'manually_rejected'
LEFT   JOIN municipality_version m ON m.lg_code = s.lg_code AND m.is_current = 1
WHERE  l.line_name_raw = :line_name AND l.operator_name_raw = :operator
ORDER  BY s.lg_code, l.station_name_raw;
