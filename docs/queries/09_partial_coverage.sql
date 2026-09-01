-- The official wording behind every partial coverage statement, verbatim.
SELECT numbering_area_code, pref_name, municipality_name,
       qualifier, coverage_type, exception_text, clause_raw
FROM   telephone_area_coverage
WHERE  coverage_type = 'partial'
ORDER  BY numbering_area_code;
