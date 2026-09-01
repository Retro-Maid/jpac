-- Records that matched nothing. They are kept, never deleted, so a match rate
-- can never be improved by making rows disappear (docs/POLICY.md §5).
SELECT kind, COUNT(*) AS n FROM unmatched_records GROUP BY kind ORDER BY n DESC;
