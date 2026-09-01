-- Everything the pipeline refused to resolve, grouped as the source left it.
SELECT matching_rule_id, candidate_group_id, candidate_count,
       address_id, target_id, confidence, verification_status
FROM   bridge_address_postal
WHERE  relation_type = 'ambiguous'
ORDER  BY candidate_count DESC, candidate_group_id
LIMIT  100;
