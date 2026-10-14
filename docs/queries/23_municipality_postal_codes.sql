-- 市区町村の郵便番号をすべて。2つのブリッジを合わせます。
--   P2 bridge_municipality_postal_code  町域の郵便番号7桁
--   P3 bridge_municipality_postal       「以下に掲載がない場合」など町域を持たない特殊な
--                                       レコード。端点は郵便番号ではなく postal_record_id
--                                       なので、版の表から7桁を引きます
-- 片方だけを見ると、もう片方が黙って抜け落ちます（v2.0.0 で表を2つに分けた理由です）。
.parameter set :lg_code '131041'
SELECT 'P2' AS rule, b.postal_code, NULL AS record_kind, NULL AS town_raw,
       b.relation_type, b.verification_status
FROM   bridge_municipality_postal_code b
WHERE  b.lg_code = :lg_code AND b.relation_type <> 'unresolved'
UNION ALL
SELECT 'P3', v.postal_code, v.record_kind, v.town_raw,
       b.relation_type, b.verification_status
FROM   bridge_municipality_postal b
JOIN   postal_record_version v
       ON v.postal_record_id = b.postal_record_id AND v.is_current = 1
WHERE  b.lg_code = :lg_code AND b.relation_type <> 'unresolved'
ORDER  BY rule DESC, postal_code;
