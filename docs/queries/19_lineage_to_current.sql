-- 古い市区町村コードを、廃置分合をたどって現行のコードまで追いかける（再帰）。
-- 旧→新の辺を施行日の順につなぎます。分割・分離・政令市化では行き先が複数に分かれるので、
-- 行き先ごとに行が分かれます。どれか1つには決めません。
-- is_current_code = 1 の行が、現行の市区町村に着いた行です。現行の人口も添えます。
-- all_unique = 0 の行は、途中に行き先が一意でない辺（分割・分離など）を含みます。
-- 存続（continues: 旧→旧）の辺はコードが変わらないので、たどりません。
-- 例の 343463 は 1972年に広島市へ編入され、1980年の政令市化で区に分かれます。
.parameter set :old_lg_code '343463'
WITH RECURSIVE walk(lg_code, effective_date, depth, path, all_unique) AS (
  SELECT :old_lg_code, '0000-00-00', 0, :old_lg_code, 1
  UNION ALL
  SELECT e.new_lg_code, e.effective_date, w.depth + 1,
         w.path || ' →(' || e.effective_date || ' ' || e.event_type || ') ' || e.new_lg_code,
         w.all_unique * e.is_unique_match
  FROM   walk w
  JOIN   municipality_lineage_edge e ON e.old_lg_code = w.lg_code
  WHERE  e.effective_date > w.effective_date
    AND  e.new_lg_code <> w.lg_code
)
SELECT w.depth, w.path, w.lg_code,
       m.pref, m.city, m.ward,
       (m.lg_code IS NOT NULL) AS is_current_code,
       w.all_unique,
       p.population_total
FROM   walk w
LEFT   JOIN municipality_version m ON m.lg_code = w.lg_code AND m.is_current = 1
LEFT   JOIN census_population_municipality p
       ON p.lg_code = w.lg_code AND p.census_year = 2025
ORDER  BY w.depth, w.lg_code;
