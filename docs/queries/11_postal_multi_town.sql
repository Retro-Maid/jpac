-- Postal codes covering more than one 町字 (Japan Post's own flag 13 says so).
-- Any "one postal code = one town" assumption fails on every row here.
-- The bridge stores the far end of the edge in target_id, not in a column named
-- after the system it points at; an earlier revision of this query assumed
-- otherwise and never ran.
SELECT target_id AS postal_code, COUNT(DISTINCT address_id) AS towns
FROM   bridge_address_postal_code
WHERE  relation_type <> 'unresolved'
GROUP  BY target_id
HAVING towns > 1
ORDER  BY towns DESC
LIMIT  50;
