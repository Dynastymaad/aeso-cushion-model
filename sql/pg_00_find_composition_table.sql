-- PostgreSQL version of the composition-table search.
-- Postgres folds unquoted identifiers to lower case, so information_schema
-- columns come back lower case where SQL Server returns them upper case.
-- lead_bucket is deliberately NOT required: this warehouse stores render_time
-- instead and the bucket is derived in pg_10_composition.sql.
SELECT table_schema, table_name, COUNT(*) AS matching_columns
FROM information_schema.columns
WHERE column_name IN
      ('net_imports_actual_scheduled','ail','cogen','gfs',
       'biomass_and_other','render_time','long_lead_volume','energy_storage')
GROUP BY table_schema, table_name
HAVING COUNT(*) >= 4
ORDER BY matching_columns DESC;
