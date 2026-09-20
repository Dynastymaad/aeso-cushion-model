-- Supply composition: hourly actuals plus the ACTUAL intertie flow.
-- This is the only source for net_imports_actual_scheduled - the AESO API does
-- not publish it. {TABLE} is filled in from db.json.
SELECT datetime_begin, render_time, lead_bucket, forecast_pool_price, ail,
       sc, cogen, cc, gfs, coal, dual_fuel, hydro, energy_storage,
       biomass_and_other, wind, solar, long_lead_volume,
       net_imports, net_imports_actual_scheduled
FROM {TABLE}
WHERE lead_bucket = -1
  AND datetime_begin >= DATEADD(day, -{DAYS}, GETDATE())
ORDER BY datetime_begin ASC;
