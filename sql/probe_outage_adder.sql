-- Measures the day-ahead gas-outage adder from your own history.
--
-- canpower.aeso_fundamentals_snapshots stores a snapshot every few minutes,
-- and each snapshot carries roughly 34 hours of FORWARD hours. So for any
-- target hour we can see what AESO said gas availability would be at a range
-- of leads, and compare it with what it actually turned out to be.
--
--   lead_h = FLOOR((datetime_begin - render_time) in hours)
--   lead_h = -1  ->  the reading taken during the hour itself = the actual
--   lead_h = 18  ->  what was published ~18 hours before the hour
--
-- The adder is (value at lead_h) - (value at lead_h = -1): how much the
-- forward view OVERSTATES what is really available.
--
-- Run against the SANDBOX (PostgreSQL) connection. Export as CSV named
-- outage_leads.csv.  Expect roughly 100-200k rows.
WITH s AS (
  SELECT datetime_begin,
         render_time,
         FLOOR(EXTRACT(EPOCH FROM (datetime_begin - render_time)) / 3600.0)::int AS lead_h,
         COALESCE(sc,0) + COALESCE(cogen,0) + COALESCE(cc,0) + COALESCE(gfs,0) AS gas_avail,
         COALESCE(sc,0)   AS sc,
         COALESCE(cogen,0) AS cogen,
         COALESCE(cc,0)   AS cc,
         COALESCE(gfs,0)  AS gfs,
         COALESCE(hydro,0) AS hydro,
         COALESCE(energy_storage,0) AS energy_storage,
         ail, wind, solar, net_imports_actual_scheduled
  FROM canpower.aeso_fundamentals_snapshots
  WHERE datetime_begin >= NOW() - INTERVAL '500 days'
)
SELECT DISTINCT ON (datetime_begin, lead_h)
       datetime_begin, lead_h, gas_avail, sc, cogen, cc, gfs,
       hydro, energy_storage, ail, wind, solar, net_imports_actual_scheduled
FROM s
WHERE lead_h IN (-1, 2, 6, 10, 14, 18, 22, 26, 30)
ORDER BY datetime_begin, lead_h, render_time DESC;
