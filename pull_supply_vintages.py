"""
pull_supply_vintages.py -- READ-ONLY export of the forward-supply history found
by find_thermal_sources.py, so its accuracy for days 1-14 can be measured.

  canpower.aeso_24m_supply_demand_forecast   AESO 24-month supply/demand outlook,
      one row per day (peak hour), re-published daily since 2025-11-03
  canpower.aeso_supply_demand_forecast       the older version, retrieved 2023-04 to 2025-04
  Warehouse GenerationOutages / IIRPlantOutage / IIRPlantUnits   columns + 5 sample rows only

Writes cache/supply_vintages_24m.csv, cache/supply_vintages_old.csv and
cache/outage_tables_sample.txt. Only rows 0-20 days ahead of each publish date
are kept, so the files stay small.

    python pull_supply_vintages.py
"""
import json, time, traceback
from pathlib import Path
import pandas as pd
HERE = Path(__file__).resolve().parent
import update as u
cfg = json.loads((HERE / 'db.json').read_text())
def say(m): print(f'  {m}', flush=True)
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40)

Q24 = """
SELECT DISTINCT ON (vday, report_date)
       vday, report_date, he_peak, total_expected_internal_supply_mw, total_expected_internal_supply_no_wind_solar_mw,
       import_capacity_bc_sk_matl_mw, ail_load_operating_reserves_mw, surplus_mw, surplus_no_wind_solar_mw, create_datetime
FROM (SELECT *, date_trunc('day', create_datetime) AS vday FROM canpower.aeso_24m_supply_demand_forecast) s
WHERE report_date >= date_trunc('day', create_datetime)
  AND report_date <= date_trunc('day', create_datetime) + INTERVAL '20 days'
ORDER BY vday, report_date, create_datetime DESC
"""
QOLD = """
SELECT DISTINCT ON (date_retrieved, report_date)
       date_retrieved, report_date, he_peak, total_expected_internal_supply, import_capacity_from_bc_sk_matl,
       ail_load_plus_operating_reserve, surplus
FROM canpower.aeso_supply_demand_forecast
WHERE report_date >= date_retrieved AND report_date <= date_retrieved + INTERVAL '20 days'
ORDER BY date_retrieved, report_date
"""
try:
    cn, _, _ = u._connect(u.comp_cfg(cfg), 'composition')
    with cn:
        for name, q in (('supply_vintages_24m', Q24), ('supply_vintages_old', QOLD)):
            t0 = time.time()
            try:
                df = pd.read_sql(q, cn); df.to_csv(HERE / 'cache' / f'{name}.csv', index=False)
                say(f'{name:<22} {len(df):>7,} rows  ({time.time()-t0:.0f}s)')
                print(df.head(3).to_string(index=False))
            except Exception:
                say(f'{name} FAILED'); print(traceback.format_exc())
except BaseException:
    print(traceback.format_exc())
out = []
try:
    cn, _, _ = u._connect(cfg, 'forwards')
    with cn:
        for t in ('GenerationOutages', 'IIRPlantOutage', 'IIRPlantUnits', 'GenerationOutageCategory'):
            try:
                df = pd.read_sql(f'SELECT TOP 5 * FROM Warehouse.dbo.{t} WITH (NOLOCK)', cn)
                out.append(f'=== {t} ===\n' + df.to_string(index=False))
            except Exception as e:
                out.append(f'=== {t} === could not read: {str(e).splitlines()[0][:150]}')
except BaseException:
    out.append(traceback.format_exc())
(HERE / 'cache' / 'outage_tables_sample.txt').write_text('\n\n'.join(out), encoding='utf-8')
print('\n'.join(out)[:6000])
print('\nwritten: cache/supply_vintages_24m.csv, cache/supply_vintages_old.csv, cache/outage_tables_sample.txt')
