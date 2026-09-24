-- Postal codes covering more than one 町字 (Japan Post's own flag 13 says so).
-- Any "one postal code = one town" assumption fails on every row here.
-- v2.0.0 以降、ブリッジの端点は指す先の名前を持つ（`postal_code`）。それ以前は
-- 多態な `target_id` で、どの表を指すのかスキーマが言っていなかった
-- （docs/BRIDGE_ENDPOINT_MIGRATION.md）。
SELECT postal_code, COUNT(DISTINCT address_id) AS towns
FROM   bridge_address_postal_code
WHERE  relation_type <> 'unresolved'
GROUP  BY postal_code
HAVING towns > 1
ORDER  BY towns DESC
LIMIT  50;
