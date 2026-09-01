-- jp-address-crosswalk — 出荷されている SQLite の実際のスキーマ定義
--
-- このファイルは手書きではありません。出荷済みの成果物からそのまま抽出し、
-- 読みやすいように改行だけを入れたものです。定義そのものは一切変えていません。
--
--   抽出元ビルド: v1.0.0+data-2026-08-23 (built_at 2026-08-23T11:53:50Z)
--   内訳: テーブル 28 / ビュー 3 / 索引 22（PRIMARY KEY の自動索引を除く）
--
-- このファイルが古くならないこと自体を検証しています。
--   py -3 tools/verify_artifacts_agree.py   の 3c で、ここに書かれた定義を
--   スクラッチDBに流し込み、出荷物と 1 オブジェクトずつ突き合わせます。
--   列・型・CHECK・索引のいずれかがずれれば失敗します。
--
-- 現物と突き合わせる:
--   py -3 -c "import sqlite3; c=sqlite3.connect('file:dist/jp_address_crosswalk.sqlite?mode=ro',uri=True); [print(s+';') for (s,) in c.execute('select sql from sqlite_master where sql is not null order by type,name')]"
--
-- 読むときの前提（docs/DB_SCHEMA.md §1 と対応）:
--
--   * コードはすべて TEXT です。先頭ゼロを保つため、数値型は一切使いません。
--     confidence だけが REAL、候補数とフラグ類が INTEGER です。
--
--   * 外部キー制約は宣言されていません。参照関係は論理的なもので、整合性は
--     ビルド時の不変条件テストで担保しています（docs/TEST_STRATEGY.md §3）。
--
--   * PRIMARY KEY と CHECK は宣言されています。とくにブリッジ 6 本の
--     「verification_status <> 'auto' OR (...)」は、自動確定の 6 条件を
--     データベース自身に強制させたものです。
--
--   * address_code / address_key_conflict / match_run_input /
--     snapshot_license_artifact には PRIMARY KEY がありません。複合キーは
--     docs/DATA_MODEL.md 上の論理的な取り決めであって、宣言された制約ではありません。


-- ======================================================================
-- テーブル (28)
-- ======================================================================

CREATE TABLE "address" (
  "address_id"            TEXT PRIMARY KEY,
  "lg_code"               TEXT,
  "jis_city_code"         TEXT,
  "machiaza_id"           TEXT,
  "machiaza_type"         TEXT,
  "pref"                  TEXT,
  "county"                TEXT,
  "city"                  TEXT,
  "ward"                  TEXT,
  "oaza_cho"              TEXT,
  "chome"                 TEXT,
  "chome_number"          TEXT,
  "koaza"                 TEXT,
  "machiaza_dist"         TEXT,
  "oaza_cho_kana"         TEXT,
  "chome_kana"            TEXT,
  "koaza_kana"            TEXT,
  "oaza_cho_roma"         TEXT,
  "koaza_roma"            TEXT,
  "rsdt_addr_flg"         TEXT,
  "rsdt_addr_mtd_code"    TEXT,
  "oaza_cho_aka_flg"      TEXT,
  "koaza_aka_code"        TEXT,
  "status_flg"            TEXT,
  "wake_num_flg"          TEXT,
  "src_code"              TEXT,
  "remarks"               TEXT,
  "full_name_raw"         TEXT,
  "full_name_normalized"  TEXT,
  "normalization_profile" TEXT,
  "rsdt_variant_count"    TEXT,
  "valid_from"            TEXT,
  "valid_to"              TEXT,
  "observed_from"         TEXT,
  "observed_to"           TEXT,
  "source_snapshot_id"    TEXT,
  CHECK (length(address_id) = 20)
);

CREATE TABLE "address_code" (
  "address_id"         TEXT,
  "code_type"          TEXT,
  "code_value"         TEXT,
  "valid_from"         TEXT,
  "valid_to"           TEXT,
  "observed_from"      TEXT,
  "observed_to"        TEXT,
  "source_snapshot_id" TEXT
);

CREATE TABLE "address_entity" (
  "address_id"                 TEXT PRIMARY KEY,
  "genesis_lg_code"            TEXT,
  "genesis_machiaza_id"        TEXT,
  "entity_status"              TEXT,
  "identity_match_rule"        TEXT,
  "first_observed_snapshot_id" TEXT,
  "last_observed_snapshot_id"  TEXT,
  "created_at"                 TEXT,
  "retired_at"                 TEXT,
  "retire_reason"              TEXT
);

CREATE TABLE "address_history" (
  "history_id"         TEXT PRIMARY KEY,
  "address_id"         TEXT,
  "field_name"         TEXT,
  "old_value"          TEXT,
  "new_value"          TEXT,
  "valid_from"         TEXT,
  "observed_at"        TEXT,
  "source_snapshot_id" TEXT
);

CREATE TABLE "address_key_conflict" (
  "lg_code"              TEXT,
  "machiaza_id"          TEXT,
  "machiaza_type"        TEXT,
  "pref"                 TEXT,
  "pref_kana"            TEXT,
  "pref_roma"            TEXT,
  "county"               TEXT,
  "county_kana"          TEXT,
  "county_roma"          TEXT,
  "city"                 TEXT,
  "city_kana"            TEXT,
  "city_roma"            TEXT,
  "ward"                 TEXT,
  "ward_kana"            TEXT,
  "ward_roma"            TEXT,
  "oaza_cho"             TEXT,
  "oaza_cho_kana"        TEXT,
  "oaza_cho_roma"        TEXT,
  "chome"                TEXT,
  "chome_kana"           TEXT,
  "chome_number"         TEXT,
  "koaza"                TEXT,
  "koaza_kana"           TEXT,
  "koaza_roma"           TEXT,
  "machiaza_dist"        TEXT,
  "rsdt_addr_flg"        TEXT,
  "rsdt_addr_mtd_code"   TEXT,
  "oaza_cho_aka_flg"     TEXT,
  "koaza_aka_code"       TEXT,
  "oaza_cho_gsi_uncmn"   TEXT,
  "koaza_gsi_uncmn"      TEXT,
  "status_flg"           TEXT,
  "wake_num_flg"         TEXT,
  "efct_date"            TEXT,
  "ablt_date"            TEXT,
  "src_code"             TEXT,
  "post_code"            TEXT,
  "remarks"              TEXT,
  "jis_city_code"        TEXT,
  "full_name_raw"        TEXT,
  "full_name_normalized" TEXT
);

CREATE TABLE "address_lineage" (
  "lineage_id"         TEXT PRIMARY KEY,
  "old_address_id"     TEXT,
  "new_address_id"     TEXT,
  "relation_type"      TEXT,
  "effective_date"     TEXT,
  "observed_at"        TEXT,
  "evidence"           TEXT,
  "evidence_source"    TEXT,
  "source_snapshot_id" TEXT
);

CREATE TABLE "address_rsdt_variant" (
  "rsdt_variant_id"    TEXT PRIMARY KEY,
  "lg_code"            TEXT,
  "machiaza_id"        TEXT,
  "rsdt_addr_flg"      TEXT,
  "rsdt_addr_mtd_code" TEXT,
  "efct_date"          TEXT,
  "ablt_date"          TEXT
);

CREATE TABLE "bridge_address_mlit" (
  "bridge_id"                     TEXT PRIMARY KEY,
  "address_id"                    TEXT,
  "lg_code"                       TEXT,
  "target_id"                     TEXT,
  "direction"                     TEXT,
  "relation_type"                 TEXT,
  "match_method"                  TEXT,
  "matching_rule_id"              TEXT,
  "confidence"                    REAL,
  "candidate_group_id"            TEXT,
  "candidate_count"               INTEGER,
  "candidate_count_is_complete"   INTEGER,
  "is_unique_match"               INTEGER,
  "verification_status"           TEXT,
  "override_stale"                INTEGER,
  "derivation"                    TEXT,
  "coverage_type"                 TEXT,
  "normalization_profile"         TEXT,
  "mismatch_note"                 TEXT,
  "valid_from"                    TEXT,
  "valid_to"                      TEXT,
  "observed_from"                 TEXT,
  "observed_to"                   TEXT,
  "is_current"                    INTEGER,
  "match_run_id"                  TEXT,
  "source_snapshot_id"            TEXT,
  "matching_rule_version"         TEXT,
  "normalization_profile_version" TEXT,
  "created_at"                    TEXT,
  "updated_at"                    TEXT,
  CHECK (confidence >= 0.0 AND confidence <= 1.0),
  CHECK (candidate_count >= 0),
  CHECK (address_id IS NOT NULL OR lg_code IS NOT NULL OR target_id IS NOT NULL),
  CHECK (NOT (is_unique_match = 1 AND candidate_count > 1)),
  CHECK (candidate_count <= 1 OR candidate_group_id IS NOT NULL),
  CHECK (candidate_count > 1 OR candidate_group_id IS NULL),
  CHECK (verification_status <> 'auto' OR ( candidate_count = 1 AND is_unique_match = 1 AND candidate_count_is_complete = 1 AND confidence >= 0.98 AND override_stale = 0 AND relation_type IN ('exact','equivalent'))),
  CHECK (relation_type IN ('exact', 'equivalent', 'parent', 'child', 'contains', 'overlap', 'candidate', 'ambiguous', 'unresolved')),
  CHECK (match_method IN ('direct_code', 'exact_name', 'normalized_name', 'parent_child', 'composite', 'official_area_rule', 'manual_override', 'unresolved')),
  CHECK (verification_status IN ('auto', 'review_required', 'manually_verified', 'manually_rejected'))
);

CREATE TABLE "bridge_address_postal" (
  "bridge_id"                     TEXT PRIMARY KEY,
  "address_id"                    TEXT,
  "lg_code"                       TEXT,
  "target_id"                     TEXT,
  "direction"                     TEXT,
  "relation_type"                 TEXT,
  "match_method"                  TEXT,
  "matching_rule_id"              TEXT,
  "confidence"                    REAL,
  "candidate_group_id"            TEXT,
  "candidate_count"               INTEGER,
  "candidate_count_is_complete"   INTEGER,
  "is_unique_match"               INTEGER,
  "verification_status"           TEXT,
  "override_stale"                INTEGER,
  "derivation"                    TEXT,
  "coverage_type"                 TEXT,
  "normalization_profile"         TEXT,
  "mismatch_note"                 TEXT,
  "valid_from"                    TEXT,
  "valid_to"                      TEXT,
  "observed_from"                 TEXT,
  "observed_to"                   TEXT,
  "is_current"                    INTEGER,
  "match_run_id"                  TEXT,
  "source_snapshot_id"            TEXT,
  "matching_rule_version"         TEXT,
  "normalization_profile_version" TEXT,
  "created_at"                    TEXT,
  "updated_at"                    TEXT,
  CHECK (confidence >= 0.0 AND confidence <= 1.0),
  CHECK (candidate_count >= 0),
  CHECK (address_id IS NOT NULL OR lg_code IS NOT NULL OR target_id IS NOT NULL),
  CHECK (NOT (is_unique_match = 1 AND candidate_count > 1)),
  CHECK (candidate_count <= 1 OR candidate_group_id IS NOT NULL),
  CHECK (candidate_count > 1 OR candidate_group_id IS NULL),
  CHECK (verification_status <> 'auto' OR ( candidate_count = 1 AND is_unique_match = 1 AND candidate_count_is_complete = 1 AND confidence >= 0.98 AND override_stale = 0 AND relation_type IN ('exact','equivalent'))),
  CHECK (relation_type IN ('exact', 'equivalent', 'parent', 'child', 'contains', 'overlap', 'candidate', 'ambiguous', 'unresolved')),
  CHECK (match_method IN ('direct_code', 'exact_name', 'normalized_name', 'parent_child', 'composite', 'official_area_rule', 'manual_override', 'unresolved')),
  CHECK (verification_status IN ('auto', 'review_required', 'manually_verified', 'manually_rejected'))
);

CREATE TABLE "bridge_address_postal_code" (
  "bridge_id"                     TEXT PRIMARY KEY,
  "address_id"                    TEXT,
  "lg_code"                       TEXT,
  "target_id"                     TEXT,
  "direction"                     TEXT,
  "relation_type"                 TEXT,
  "match_method"                  TEXT,
  "matching_rule_id"              TEXT,
  "confidence"                    REAL,
  "candidate_group_id"            TEXT,
  "candidate_count"               INTEGER,
  "candidate_count_is_complete"   INTEGER,
  "is_unique_match"               INTEGER,
  "verification_status"           TEXT,
  "override_stale"                INTEGER,
  "derivation"                    TEXT,
  "coverage_type"                 TEXT,
  "normalization_profile"         TEXT,
  "mismatch_note"                 TEXT,
  "valid_from"                    TEXT,
  "valid_to"                      TEXT,
  "observed_from"                 TEXT,
  "observed_to"                   TEXT,
  "is_current"                    INTEGER,
  "match_run_id"                  TEXT,
  "source_snapshot_id"            TEXT,
  "matching_rule_version"         TEXT,
  "normalization_profile_version" TEXT,
  "created_at"                    TEXT,
  "updated_at"                    TEXT,
  CHECK (confidence >= 0.0 AND confidence <= 1.0),
  CHECK (candidate_count >= 0),
  CHECK (address_id IS NOT NULL OR lg_code IS NOT NULL OR target_id IS NOT NULL),
  CHECK (NOT (is_unique_match = 1 AND candidate_count > 1)),
  CHECK (candidate_count <= 1 OR candidate_group_id IS NOT NULL),
  CHECK (candidate_count > 1 OR candidate_group_id IS NULL),
  CHECK (verification_status <> 'auto' OR ( candidate_count = 1 AND is_unique_match = 1 AND candidate_count_is_complete = 1 AND confidence >= 0.98 AND override_stale = 0 AND relation_type IN ('exact','equivalent'))),
  CHECK (relation_type IN ('exact', 'equivalent', 'parent', 'child', 'contains', 'overlap', 'candidate', 'ambiguous', 'unresolved')),
  CHECK (match_method IN ('direct_code', 'exact_name', 'normalized_name', 'parent_child', 'composite', 'official_area_rule', 'manual_override', 'unresolved')),
  CHECK (verification_status IN ('auto', 'review_required', 'manually_verified', 'manually_rejected'))
);

CREATE TABLE "bridge_address_telephone" (
  "bridge_id"                     TEXT PRIMARY KEY,
  "address_id"                    TEXT,
  "lg_code"                       TEXT,
  "target_id"                     TEXT,
  "direction"                     TEXT,
  "relation_type"                 TEXT,
  "match_method"                  TEXT,
  "matching_rule_id"              TEXT,
  "confidence"                    REAL,
  "candidate_group_id"            TEXT,
  "candidate_count"               INTEGER,
  "candidate_count_is_complete"   INTEGER,
  "is_unique_match"               INTEGER,
  "verification_status"           TEXT,
  "override_stale"                INTEGER,
  "derivation"                    TEXT,
  "coverage_type"                 TEXT,
  "normalization_profile"         TEXT,
  "mismatch_note"                 TEXT,
  "valid_from"                    TEXT,
  "valid_to"                      TEXT,
  "observed_from"                 TEXT,
  "observed_to"                   TEXT,
  "is_current"                    INTEGER,
  "match_run_id"                  TEXT,
  "source_snapshot_id"            TEXT,
  "matching_rule_version"         TEXT,
  "normalization_profile_version" TEXT,
  "created_at"                    TEXT,
  "updated_at"                    TEXT,
  CHECK (confidence >= 0.0 AND confidence <= 1.0),
  CHECK (candidate_count >= 0),
  CHECK (address_id IS NOT NULL OR lg_code IS NOT NULL OR target_id IS NOT NULL),
  CHECK (NOT (is_unique_match = 1 AND candidate_count > 1)),
  CHECK (candidate_count <= 1 OR candidate_group_id IS NOT NULL),
  CHECK (candidate_count > 1 OR candidate_group_id IS NULL),
  CHECK (verification_status <> 'auto' OR ( candidate_count = 1 AND is_unique_match = 1 AND candidate_count_is_complete = 1 AND confidence >= 0.98 AND override_stale = 0 AND relation_type IN ('exact','equivalent'))),
  CHECK (relation_type IN ('exact', 'equivalent', 'parent', 'child', 'contains', 'overlap', 'candidate', 'ambiguous', 'unresolved')),
  CHECK (match_method IN ('direct_code', 'exact_name', 'normalized_name', 'parent_child', 'composite', 'official_area_rule', 'manual_override', 'unresolved')),
  CHECK (verification_status IN ('auto', 'review_required', 'manually_verified', 'manually_rejected'))
);

CREATE TABLE "bridge_municipality_postal" (
  "bridge_id"                     TEXT PRIMARY KEY,
  "address_id"                    TEXT,
  "lg_code"                       TEXT,
  "target_id"                     TEXT,
  "direction"                     TEXT,
  "relation_type"                 TEXT,
  "match_method"                  TEXT,
  "matching_rule_id"              TEXT,
  "confidence"                    REAL,
  "candidate_group_id"            TEXT,
  "candidate_count"               INTEGER,
  "candidate_count_is_complete"   INTEGER,
  "is_unique_match"               INTEGER,
  "verification_status"           TEXT,
  "override_stale"                INTEGER,
  "derivation"                    TEXT,
  "coverage_type"                 TEXT,
  "normalization_profile"         TEXT,
  "mismatch_note"                 TEXT,
  "valid_from"                    TEXT,
  "valid_to"                      TEXT,
  "observed_from"                 TEXT,
  "observed_to"                   TEXT,
  "is_current"                    INTEGER,
  "match_run_id"                  TEXT,
  "source_snapshot_id"            TEXT,
  "matching_rule_version"         TEXT,
  "normalization_profile_version" TEXT,
  "created_at"                    TEXT,
  "updated_at"                    TEXT,
  CHECK (confidence >= 0.0 AND confidence <= 1.0),
  CHECK (candidate_count >= 0),
  CHECK (address_id IS NOT NULL OR lg_code IS NOT NULL OR target_id IS NOT NULL),
  CHECK (NOT (is_unique_match = 1 AND candidate_count > 1)),
  CHECK (candidate_count <= 1 OR candidate_group_id IS NOT NULL),
  CHECK (candidate_count > 1 OR candidate_group_id IS NULL),
  CHECK (verification_status <> 'auto' OR ( candidate_count = 1 AND is_unique_match = 1 AND candidate_count_is_complete = 1 AND confidence >= 0.98 AND override_stale = 0 AND relation_type IN ('exact','equivalent'))),
  CHECK (relation_type IN ('exact', 'equivalent', 'parent', 'child', 'contains', 'overlap', 'candidate', 'ambiguous', 'unresolved')),
  CHECK (match_method IN ('direct_code', 'exact_name', 'normalized_name', 'parent_child', 'composite', 'official_area_rule', 'manual_override', 'unresolved')),
  CHECK (verification_status IN ('auto', 'review_required', 'manually_verified', 'manually_rejected'))
);

CREATE TABLE "bridge_municipality_telephone" (
  "bridge_id"                     TEXT PRIMARY KEY,
  "address_id"                    TEXT,
  "lg_code"                       TEXT,
  "target_id"                     TEXT,
  "direction"                     TEXT,
  "relation_type"                 TEXT,
  "match_method"                  TEXT,
  "matching_rule_id"              TEXT,
  "confidence"                    REAL,
  "candidate_group_id"            TEXT,
  "candidate_count"               INTEGER,
  "candidate_count_is_complete"   INTEGER,
  "is_unique_match"               INTEGER,
  "verification_status"           TEXT,
  "override_stale"                INTEGER,
  "derivation"                    TEXT,
  "coverage_type"                 TEXT,
  "normalization_profile"         TEXT,
  "mismatch_note"                 TEXT,
  "valid_from"                    TEXT,
  "valid_to"                      TEXT,
  "observed_from"                 TEXT,
  "observed_to"                   TEXT,
  "is_current"                    INTEGER,
  "match_run_id"                  TEXT,
  "source_snapshot_id"            TEXT,
  "matching_rule_version"         TEXT,
  "normalization_profile_version" TEXT,
  "created_at"                    TEXT,
  "updated_at"                    TEXT,
  CHECK (confidence >= 0.0 AND confidence <= 1.0),
  CHECK (candidate_count >= 0),
  CHECK (address_id IS NOT NULL OR lg_code IS NOT NULL OR target_id IS NOT NULL),
  CHECK (NOT (is_unique_match = 1 AND candidate_count > 1)),
  CHECK (candidate_count <= 1 OR candidate_group_id IS NOT NULL),
  CHECK (candidate_count > 1 OR candidate_group_id IS NULL),
  CHECK (verification_status <> 'auto' OR ( candidate_count = 1 AND is_unique_match = 1 AND candidate_count_is_complete = 1 AND confidence >= 0.98 AND override_stale = 0 AND relation_type IN ('exact','equivalent'))),
  CHECK (relation_type IN ('exact', 'equivalent', 'parent', 'child', 'contains', 'overlap', 'candidate', 'ambiguous', 'unresolved')),
  CHECK (match_method IN ('direct_code', 'exact_name', 'normalized_name', 'parent_child', 'composite', 'official_area_rule', 'manual_override', 'unresolved')),
  CHECK (verification_status IN ('auto', 'review_required', 'manually_verified', 'manually_rejected'))
);

CREATE TABLE "match_run" (
  "match_run_id"                  TEXT PRIMARY KEY,
  "started_at"                    TEXT,
  "matching_rule_version"         TEXT,
  "normalization_profile_version" TEXT,
  "code_version"                  TEXT
);

CREATE TABLE "match_run_input" (
  "match_run_id"       TEXT,
  "source_snapshot_id" TEXT,
  "role"               TEXT
);

CREATE TABLE "mlit_town" (
  "mlit_record_id"             TEXT PRIMARY KEY,
  "mlit_code"                  TEXT,
  "jis_city_code"              TEXT,
  "first_observed_snapshot_id" TEXT
);

CREATE TABLE "mlit_town_version" (
  "mlit_town_version_id" TEXT PRIMARY KEY,
  "mlit_record_id"       TEXT,
  "mlit_code"            TEXT,
  "jis_city_code"        TEXT,
  "pref_code"            TEXT,
  "pref_name"            TEXT,
  "city_name"            TEXT,
  "town_name_raw"        TEXT,
  "town_name_normalized" TEXT,
  "latitude"             REAL,
  "longitude"            REAL,
  "source_material_code" TEXT,
  "aza_class_code"       TEXT,
  "fiscal_year"          TEXT,
  "isj_version"          TEXT,
  "observed_from"        TEXT,
  "observed_to"          TEXT,
  "is_current"           INTEGER,
  "source_snapshot_id"   TEXT,
  CHECK (latitude IS NULL OR (latitude BETWEEN 20 AND 46)),
  CHECK (longitude IS NULL OR (longitude BETWEEN 122 AND 154))
);

CREATE TABLE "municipality" (
  "lg_code"                    TEXT PRIMARY KEY,
  "jis_city_code"              TEXT,
  "first_observed_snapshot_id" TEXT
);

CREATE TABLE "municipality_version" (
  "municipality_version_id" TEXT PRIMARY KEY,
  "lg_code"                 TEXT,
  "jis_city_code"           TEXT,
  "pref"                    TEXT,
  "county"                  TEXT,
  "city"                    TEXT,
  "ward"                    TEXT,
  "pref_kana"               TEXT,
  "county_kana"             TEXT,
  "city_kana"               TEXT,
  "ward_kana"               TEXT,
  "pref_roma"               TEXT,
  "county_roma"             TEXT,
  "city_roma"               TEXT,
  "ward_roma"               TEXT,
  "valid_from"              TEXT,
  "valid_to"                TEXT,
  "observed_from"           TEXT,
  "observed_to"             TEXT,
  "is_current"              INTEGER,
  "source_snapshot_id"      TEXT
);

CREATE TABLE "postal_code_entity" (
  "postal_code"        TEXT PRIMARY KEY,
  "record_count"       TEXT,
  "observed_from"      TEXT,
  "observed_to"        TEXT,
  "source_snapshot_id" TEXT,
  CHECK (length(postal_code) = 7)
);

CREATE TABLE "postal_record" (
  "postal_record_id"           TEXT PRIMARY KEY,
  "postal_code"                TEXT,
  "jis_city_code"              TEXT,
  "first_observed_snapshot_id" TEXT
);

CREATE TABLE "postal_record_version" (
  "postal_record_version_id" TEXT PRIMARY KEY,
  "postal_record_id"         TEXT,
  "postal_code"              TEXT,
  "jis_city_code"            TEXT,
  "old_postal_code_raw"      TEXT,
  "old_postal_code"          TEXT,
  "pref_kana"                TEXT,
  "city_kana"                TEXT,
  "town_kana"                TEXT,
  "pref"                     TEXT,
  "city"                     TEXT,
  "town"                     TEXT,
  "town_raw"                 TEXT,
  "town_normalized"          TEXT,
  "parenthetical_raw"        TEXT,
  "parenthetical_class"      TEXT,
  "flag_multi_code"          TEXT,
  "flag_koaza_banchi"        TEXT,
  "flag_has_chome"           TEXT,
  "flag_multi_town"          TEXT,
  "update_flag"              TEXT,
  "change_reason"            TEXT,
  "record_kind"              TEXT,
  "valid_from"               TEXT,
  "valid_to"                 TEXT,
  "observed_from"            TEXT,
  "observed_to"              TEXT,
  "is_current"               INTEGER,
  "source_snapshot_id"       TEXT
);

CREATE TABLE "snapshot_license_artifact" (
  "artifact_id"        TEXT,
  "source"             TEXT,
  "role"               TEXT,
  "license_name"       TEXT,
  "license_url"        TEXT,
  "text_sha256"        TEXT,
  "baseline_sha256"    TEXT,
  "reviewed_on"        TEXT,
  "review_decision"    TEXT,
  "note"               TEXT,
  "source_snapshot_id" TEXT
);

CREATE TABLE "source_snapshot" (
  "source_snapshot_id"  TEXT PRIMARY KEY,
  "provider"            TEXT,
  "dataset_name"        TEXT,
  "source_page_url"     TEXT,
  "download_url"        TEXT,
  "license_name"        TEXT,
  "license_url"         TEXT,
  "license_text_sha256" TEXT,
  "source_version"      TEXT,
  "published_at"        TEXT,
  "downloaded_at"       TEXT,
  "etag"                TEXT,
  "last_modified"       TEXT,
  "sha256"              TEXT,
  "file_size"           INTEGER,
  "row_count"           INTEGER,
  "schema_fingerprint"  TEXT,
  "parser_version"      TEXT,
  "resolved_via"        TEXT,
  "status"              TEXT
);

CREATE TABLE "telephone_area" (
  "numbering_area_code"        TEXT PRIMARY KEY,
  "first_observed_snapshot_id" TEXT
);

CREATE TABLE "telephone_area_coverage" (
  "numbering_area_code" TEXT,
  "clause_raw"          TEXT,
  "pref_name"           TEXT,
  "county_name"         TEXT,
  "municipality_name"   TEXT,
  "sub_municipal_text"  TEXT,
  "qualifier"           TEXT,
  "coverage_type"       TEXT,
  "exception_text"      TEXT,
  "parse_rule"          TEXT,
  "coverage_id"         TEXT PRIMARY KEY,
  "source_snapshot_id"  TEXT
);

CREATE TABLE "telephone_area_version" (
  "telephone_area_version_id" TEXT PRIMARY KEY,
  "numbering_area_code"       TEXT,
  "area_code"                 TEXT,
  "area_code_raw"             TEXT,
  "area_text_raw"             TEXT,
  "local_digit_pattern"       TEXT,
  "current_as_of"             TEXT,
  "observed_from"             TEXT,
  "observed_to"               TEXT,
  "is_current"                INTEGER,
  "source_snapshot_id"        TEXT
);

CREATE TABLE "telephone_number_block" (
  "numbering_area_code" TEXT,
  "number"              TEXT,
  "area_code"           TEXT,
  "local_code"          TEXT,
  "carrier"             TEXT,
  "usage_status"        TEXT,
  "remarks"             TEXT,
  "current_as_of"       TEXT,
  "block_id"            TEXT PRIMARY KEY,
  "source_snapshot_id"  TEXT
);

-- ======================================================================
-- ビュー (3)
-- ======================================================================

CREATE VIEW address_crosswalk AS
SELECT
  a.address_id, a.lg_code, a.jis_city_code,
  a.pref AS pref_name, a.city AS city_name, a.ward AS ward_name,
  a.full_name_raw AS town_name, a.full_name_normalized AS town_name_normalized,
  a.machiaza_id,
  bp.target_id AS postal_code,
  bp.relation_type AS postal_relation_type, bp.match_method AS postal_match_method,
  bp.matching_rule_id AS postal_rule, bp.confidence AS postal_confidence,
  bp.candidate_count AS postal_candidate_count,
  bp.candidate_group_id AS postal_candidate_group,
  bp.is_unique_match AS postal_is_unique, bp.verification_status AS postal_status,
  bm.relation_type AS mlit_relation_type, bm.match_method AS mlit_match_method,
  bm.matching_rule_id AS mlit_rule, bm.confidence AS mlit_confidence,
  bm.candidate_count AS mlit_candidate_count,
  bm.candidate_group_id AS mlit_candidate_group,
  bm.is_unique_match AS mlit_is_unique, bm.verification_status AS mlit_status,
  bt.target_id AS numbering_area_code,
  bt.relation_type AS telephone_relation_type,
  bt.match_method AS telephone_match_method,
  bt.matching_rule_id AS telephone_rule, bt.confidence AS telephone_confidence,
  bt.candidate_count AS telephone_candidate_count,
  bt.candidate_group_id AS telephone_candidate_group,
  bt.is_unique_match AS telephone_is_unique,
  bt.verification_status AS telephone_status,
  bt.coverage_type AS telephone_coverage_type,
  bt.derivation AS telephone_derivation,
  mv.mlit_code, mv.latitude AS mlit_latitude, mv.longitude AS mlit_longitude,
  tv.area_code, tv.area_text_raw AS numbering_area_name,
  op.old_postal_code
FROM address a
LEFT JOIN (SELECT * FROM bridge_address_postal_code WHERE is_current = 1 AND verification_status <> 'manually_rejected') bp                ON bp.address_id = a.address_id
LEFT JOIN (SELECT * FROM bridge_address_mlit WHERE is_current = 1 AND verification_status <> 'manually_rejected') bm                ON bm.address_id = a.address_id
LEFT JOIN mlit_town_version mv   ON mv.mlit_record_id = bm.target_id
                                AND mv.is_current = 1
LEFT JOIN (SELECT * FROM bridge_address_telephone WHERE is_current = 1 AND verification_status <> 'manually_rejected') bt                ON bt.address_id = a.address_id
LEFT JOIN telephone_area_version tv ON tv.numbering_area_code = bt.target_id
                                AND tv.is_current = 1
LEFT JOIN (
  -- group_concat has no defined order, so the rows are ordered in a subquery
  -- first. 19 postal codes currently carry two former codes and the flat file
  -- must spell them the same way in both artifacts. The ORDER BY inside
  -- aggregate syntax would be clearer but needs SQLite 3.44, and this view text
  -- is parsed by whatever SQLite the reader has, not the one that built the
  -- file. The ordered-subquery idiom is checked at build time on the build
  -- machine only; a reader whose planner flattens the subquery could in
  -- principle see those 19 pairs in the other order.
  SELECT postal_code, group_concat(old_postal_code, ';') AS old_postal_code
    FROM (SELECT DISTINCT postal_code, old_postal_code
            FROM postal_record_version
           WHERE is_current = 1 AND old_postal_code IS NOT NULL
           ORDER BY postal_code, old_postal_code)
   GROUP BY postal_code
) op ON op.postal_code = bp.target_id;

CREATE VIEW address_crosswalk_all AS
SELECT
  a.address_id, a.lg_code, a.jis_city_code,
  a.pref AS pref_name, a.city AS city_name, a.ward AS ward_name,
  a.full_name_raw AS town_name, a.full_name_normalized AS town_name_normalized,
  a.machiaza_id,
  bp.target_id AS postal_code,
  bp.relation_type AS postal_relation_type, bp.match_method AS postal_match_method,
  bp.matching_rule_id AS postal_rule, bp.confidence AS postal_confidence,
  bp.candidate_count AS postal_candidate_count,
  bp.candidate_group_id AS postal_candidate_group,
  bp.is_unique_match AS postal_is_unique, bp.verification_status AS postal_status,
  bm.relation_type AS mlit_relation_type, bm.match_method AS mlit_match_method,
  bm.matching_rule_id AS mlit_rule, bm.confidence AS mlit_confidence,
  bm.candidate_count AS mlit_candidate_count,
  bm.candidate_group_id AS mlit_candidate_group,
  bm.is_unique_match AS mlit_is_unique, bm.verification_status AS mlit_status,
  bt.target_id AS numbering_area_code,
  bt.relation_type AS telephone_relation_type,
  bt.match_method AS telephone_match_method,
  bt.matching_rule_id AS telephone_rule, bt.confidence AS telephone_confidence,
  bt.candidate_count AS telephone_candidate_count,
  bt.candidate_group_id AS telephone_candidate_group,
  bt.is_unique_match AS telephone_is_unique,
  bt.verification_status AS telephone_status,
  bt.coverage_type AS telephone_coverage_type,
  bt.derivation AS telephone_derivation,
  mv.mlit_code, mv.latitude AS mlit_latitude, mv.longitude AS mlit_longitude,
  tv.area_code, tv.area_text_raw AS numbering_area_name,
  op.old_postal_code
FROM address a
LEFT JOIN bridge_address_postal_code bp                ON bp.address_id = a.address_id
LEFT JOIN bridge_address_mlit bm                ON bm.address_id = a.address_id
LEFT JOIN mlit_town_version mv   ON mv.mlit_record_id = bm.target_id
                                AND mv.is_current = 1
LEFT JOIN bridge_address_telephone bt                ON bt.address_id = a.address_id
LEFT JOIN telephone_area_version tv ON tv.numbering_area_code = bt.target_id
                                AND tv.is_current = 1
LEFT JOIN (
  -- group_concat has no defined order, so the rows are ordered in a subquery
  -- first. 19 postal codes currently carry two former codes and the flat file
  -- must spell them the same way in both artifacts. The ORDER BY inside
  -- aggregate syntax would be clearer but needs SQLite 3.44, and this view text
  -- is parsed by whatever SQLite the reader has, not the one that built the
  -- file. The ordered-subquery idiom is checked at build time on the build
  -- machine only; a reader whose planner flattens the subquery could in
  -- principle see those 19 pairs in the other order.
  SELECT postal_code, group_concat(old_postal_code, ';') AS old_postal_code
    FROM (SELECT DISTINCT postal_code, old_postal_code
            FROM postal_record_version
           WHERE is_current = 1 AND old_postal_code IS NOT NULL
           ORDER BY postal_code, old_postal_code)
   GROUP BY postal_code
) op ON op.postal_code = bp.target_id;

CREATE VIEW unmatched_records AS
  SELECT 'postal_record' AS kind, target_id AS record_id, matching_rule_id
    FROM bridge_address_postal
   WHERE relation_type = 'unresolved' AND address_id IS NULL
  UNION ALL
  SELECT 'mlit_town', target_id, matching_rule_id
    FROM bridge_address_mlit
   WHERE relation_type = 'unresolved' AND address_id IS NULL
  UNION ALL
  SELECT 'address', address_id, matching_rule_id
    FROM bridge_address_mlit
   WHERE relation_type = 'unresolved' AND target_id IS NULL;

-- ======================================================================
-- 索引 (22)
-- ======================================================================

CREATE INDEX idx_addr_jis_norm ON address(jis_city_code, full_name_normalized);
CREATE INDEX idx_addr_lg ON address(lg_code);
CREATE INDEX idx_bam_addr ON bridge_address_mlit(address_id);
CREATE INDEX idx_bam_rec ON bridge_address_mlit(target_id);
CREATE INDEX idx_bap_addr ON bridge_address_postal(address_id);
CREATE INDEX idx_bap_rec ON bridge_address_postal(target_id);
CREATE INDEX idx_bap_rel ON bridge_address_postal(relation_type);
CREATE INDEX idx_bapc_addr ON bridge_address_postal_code(address_id);
CREATE INDEX idx_bapc_code ON bridge_address_postal_code(target_id);
CREATE INDEX idx_bat_addr ON bridge_address_telephone(address_id);
CREATE INDEX idx_bat_area ON bridge_address_telephone(target_id);
CREATE INDEX idx_bmp_lg ON bridge_municipality_postal(lg_code);
CREATE INDEX idx_bmt_area ON bridge_municipality_telephone(target_id);
CREATE INDEX idx_lineage_new ON address_lineage(new_address_id);
CREATE INDEX idx_lineage_old ON address_lineage(old_address_id);
CREATE INDEX idx_mlv_code ON mlit_town_version(mlit_code);
CREATE INDEX idx_mlv_jis_norm ON mlit_town_version(jis_city_code, town_name_normalized);
CREATE INDEX idx_pcv_code ON postal_record_version(postal_code);
CREATE INDEX idx_pcv_jis_norm ON postal_record_version(jis_city_code, town_normalized);
CREATE INDEX idx_pcv_old ON postal_record_version(old_postal_code);
CREATE INDEX idx_tav_area ON telephone_area_version(area_code);
CREATE INDEX idx_tnb_area ON telephone_number_block(area_code, local_code);
