-- Run this ONCE. It finds the table that holds the supply-composition series -
-- the one with actual intertie flows. You exported it before as b_<stamp>.csv
-- but the table name was not in the saved queries.
SELECT c.TABLE_SCHEMA, c.TABLE_NAME, COUNT(*) AS matching_columns
FROM INFORMATION_SCHEMA.COLUMNS c
WHERE c.COLUMN_NAME IN
      ('net_imports_actual_scheduled','ail','cogen','gfs','biomass_and_other','lead_bucket')
GROUP BY c.TABLE_SCHEMA, c.TABLE_NAME
HAVING COUNT(*) >= 4
ORDER BY matching_columns DESC;
