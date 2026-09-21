-- RUN THIS FIRST (5 seconds). Confirms how far forward the snapshots reach,
-- so we know the adder test is possible before exporting anything large.
SELECT FLOOR(EXTRACT(EPOCH FROM (datetime_begin - render_time)) / 3600.0)::int AS lead_h,
       COUNT(*) AS rows,
       MIN(datetime_begin)::date AS first_target,
       MAX(datetime_begin)::date AS last_target
FROM canpower.aeso_fundamentals_snapshots
WHERE datetime_begin >= NOW() - INTERVAL '60 days'
GROUP BY 1
ORDER BY 1;
