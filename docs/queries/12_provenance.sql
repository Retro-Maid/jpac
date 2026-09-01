-- Full provenance for one address's postal edges: which snapshots produced them,
-- with what role, and the SHA-256 of the bytes those snapshots came from.
-- A real id, so the example returns something when pasted. address_id is
-- persistent (docs/IDENTITY_MODEL.md): 東京都新宿区戸山３丁目.
.parameter set :address_id 'jpa15zfxjqx82adax28d'
SELECT b.bridge_id, b.matching_rule_id, b.matching_rule_version,
       i.role, s.dataset_name, s.source_version, s.downloaded_at,
       substr(s.sha256, 1, 16) AS sha256_prefix, s.license_name
FROM   bridge_address_postal_code b
JOIN   match_run_input i ON i.match_run_id = b.match_run_id
JOIN   source_snapshot  s ON s.source_snapshot_id = i.source_snapshot_id
WHERE  b.address_id = :address_id
ORDER  BY i.role, s.dataset_name;
