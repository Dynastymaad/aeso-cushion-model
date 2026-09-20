-- Same search as 00, but across EVERY database on the instance.
-- INFORMATION_SCHEMA is per-database, so a table sitting in a different
-- database on the same server is invisible to the plain query.
DECLARE @sql NVARCHAR(MAX) = N'';
SELECT @sql = @sql + N'
SELECT ' + QUOTENAME(name, '''') + N' AS db_name, c.TABLE_SCHEMA, c.TABLE_NAME,
       COUNT(*) AS matching_columns
FROM ' + QUOTENAME(name) + N'.INFORMATION_SCHEMA.COLUMNS c
WHERE c.COLUMN_NAME IN
      (''net_imports_actual_scheduled'',''ail'',''cogen'',''gfs'',
       ''biomass_and_other'',''lead_bucket'',''long_lead_volume'',''energy_storage'')
GROUP BY c.TABLE_SCHEMA, c.TABLE_NAME
HAVING COUNT(*) >= 3
UNION ALL'
FROM sys.databases
WHERE state = 0 AND HAS_DBACCESS(name) = 1
  AND name NOT IN ('master','tempdb','model','msdb');

IF @sql = N'' SELECT NULL AS db_name, NULL AS TABLE_SCHEMA, NULL AS TABLE_NAME,
                     NULL AS matching_columns WHERE 1 = 0;
ELSE
BEGIN
  SET @sql = LEFT(@sql, LEN(@sql) - 9) + N' ORDER BY matching_columns DESC;';
  EXEC sp_executesql @sql;
END
