-- Supply composition, PostgreSQL dialect.
--
-- This table has no lead_bucket column; the SQL Server one derived it as
--     lead_bucket = FLOOR((datetime_begin - render_time) in hours)
-- verified against 57,990 exported rows at 100% agreement. lead_bucket = -1
-- therefore means "the snapshot taken DURING the target hour" - median 55
-- minutes in - which is the closest thing this table has to an actual.
--
-- Unlike the SQL Server table, this one snapshots every 5 minutes in recent
-- history (hourly further back), so that window holds up to a dozen rows per
-- hour. DISTINCT ON keeps the LAST render_time inside the window, i.e. the
-- most informed reading of that hour, which is what the hourly-cadence history
-- was giving us all along.
SELECT DISTINCT ON (datetime_begin)
       datetime_begin,
       render_time,
       -1 AS lead_bucket,
       forecast_pool_price, ail,
       sc, cogen, cc, gfs, coal, dual_fuel, hydro, energy_storage,
       biomass_and_other, wind, solar, long_lead_volume,
       net_imports, net_imports_actual_scheduled
FROM {TABLE}
WHERE render_time >  datetime_begin
  AND render_time <= datetime_begin + INTERVAL '1 hour'
  AND datetime_begin >= NOW() - INTERVAL '{DAYS} days'
ORDER BY datetime_begin, render_time DESC;
