-- pg_20_forward_supply.sql
--
-- AESO's FORWARD view of supply, as it stood at each render time, for every
-- target hour. This is the history the multi-day model does not have: what the
-- market could see about thermal availability 1..15 days before delivery.
--
-- pg_10_composition.sql keeps only lead_bucket = -1 (the post-hour snapshot,
-- i.e. the actual). This keeps every forward snapshot, one row per (render
-- day, target day) at the daily level, so it stays small (~760 days x 15
-- leads x 1 row). Thermal availability is what we are after; wind and load
-- forecasts we already have from the vendor vintages.
--
-- Run against the canpower sandbox (credentials: db.json -> composition_db).
-- Save as cache/forward_supply.csv. multiday.py will pick it up as a feature
-- the same way it picks up the outage report.

WITH snaps AS (
    SELECT
        date_trunc('day', render_time)     AS render_day,
        date_trunc('day', datetime_begin)  AS target_day,
        FLOOR(EXTRACT(EPOCH FROM (datetime_begin - render_time)) / 86400) AS lead_days,
        sc, cogen, cc, gfs, coal, dual_fuel, hydro, energy_storage,
        biomass_and_other, wind, solar, ail, net_imports, forecast_pool_price,
        render_time
    FROM canpower.aeso_fundamentals_snapshots
    WHERE datetime_begin >= NOW() - INTERVAL '{DAYS} days'
      AND render_time < datetime_begin            -- forward only
),
last_per_day AS (
    -- the LAST snapshot each render day made for each target hour
    SELECT DISTINCT ON (render_day, datetime_begin_h)
        *
    FROM (
        SELECT s.*, date_trunc('hour', s.render_time) AS datetime_begin_h
        FROM snaps s
    ) x
    ORDER BY render_day, datetime_begin_h, render_time DESC
)
SELECT
    render_day, target_day, MIN(lead_days) AS lead_days,
    COUNT(*)                                AS hours,
    AVG(sc)  AS sc,  AVG(cogen) AS cogen, AVG(cc) AS cc, AVG(gfs) AS gfs,
    AVG(coal) AS coal, AVG(dual_fuel) AS dual_fuel,
    AVG(hydro) AS hydro, AVG(energy_storage) AS energy_storage,
    AVG(biomass_and_other) AS biomass_and_other,
    AVG(wind) AS wind, AVG(solar) AS solar, AVG(ail) AS ail,
    AVG(net_imports) AS net_imports, AVG(forecast_pool_price) AS forecast_pool_price
FROM last_per_day
GROUP BY render_day, target_day
HAVING COUNT(*) >= 20
ORDER BY render_day, target_day;

-- Sanity checks before trusting it:
--   1. SELECT lead_days, COUNT(*) FROM (...) GROUP BY 1  -> how far forward the
--      table actually reaches. If it stops at 1-2 days, this is not the source.
--   2. Compare sc+cogen+cc+gfs at lead 1 against the lead_bucket=-1 actual for
--      the same day. If they are identical the "forward" is being back-filled
--      and carries no information. If they differ by a few hundred MW with a
--      consistent sign, it is a real forecast with a real bias.
