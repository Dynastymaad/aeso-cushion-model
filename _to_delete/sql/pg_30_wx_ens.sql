-- pg_30_wx_ens.sql
--
-- The 100-member ECMWF temperature ensemble, 44 days out, in the shape
-- outlook.py expects as cache/wx_ens.csv:
--     run_date, date, lead, variable, station, mean, sd, p10, p90
--
-- outlook.py already does "100 iterations of weather x 44 days" - temperature
-- members drive load, the 51 wind members (sv_eps) drive wind, everything else
-- falls back to seasonal noise past its skill horizon. It has been unrunnable
-- only because nothing writes this file. Run this against the canpower sandbox
-- (db.json -> composition_db), save the result as cache/wx_ens.csv, then
--     python outlook.py --backtest
--
-- Column names below are the ones the data brief records for the table
-- (run_date, valid_date, member, station, variable, value). If the real names
-- differ, one look at:  python update.py --inspect canpower.aeso_euro_weekly_members_daily_minmax
-- gives them; only the four identifiers need renaming.

SELECT
    run_date,
    valid_date                                   AS date,
    (valid_date::date - run_date::date)          AS lead,
    variable,
    station,
    AVG(value)                                   AS mean,
    STDDEV_SAMP(value)                           AS sd,
    PERCENTILE_CONT(0.10) WITHIN GROUP (ORDER BY value) AS p10,
    PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY value) AS p90
FROM canpower.aeso_euro_weekly_members_daily_minmax
WHERE station IN ('CYYC', 'CYEG', 'CYQF', 'CYMM')
  AND variable IN ('tmin2m', 'tmax2m')
  AND run_date >= NOW() - INTERVAL '{DAYS} days'
GROUP BY run_date, valid_date, variable, station
ORDER BY run_date, valid_date, variable, station;
