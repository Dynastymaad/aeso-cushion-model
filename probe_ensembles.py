"""
probe_ensembles.py -- READ-ONLY. Which wind forecasts that reach days 2-14 have
a saved HISTORY (so the live model can use the same source it is backtested on)?
Checks canpower.aeso_euro_weekly_members_daily_minmax (ECMWF weekly ensemble),
and the recent leads available in Warehouse WindForecast / WeatherHourlyForecast.
Only touches recent rows - fast. Writes cache/ensemble_probe.txt.

    python probe_ensembles.py
"""
import json, traceback
from pathlib import Path
import pandas as pd
HERE = Path(__file__).resolve().parent
import update as u
out = []
def w(s=''): print(s, flush=True); out.append(str(s))
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 20)
def q(cn, label, sql):
    try:
        df = pd.read_sql(sql, cn); w(f'\n--- {label} ({len(df)} rows)'); w(df.head(40).to_string(index=False)); return df
    except Exception:
        w(f'\n--- {label} FAILED'); w(traceback.format_exc()[-600:])
cfg = json.loads((HERE / 'db.json').read_text())
T = 'canpower.aeso_euro_weekly_members_daily_minmax'
try:
    cn, _, _ = u._connect(u.comp_cfg(cfg), 'composition')
    with cn:
        q(cn, 'variables and stations', f"SELECT variable, station, COUNT(*) AS n, MIN(run_date) AS first_run, MAX(run_date) AS last_run FROM {T} WHERE run_date >= CURRENT_DATE - 30 GROUP BY 1,2 ORDER BY 1,2")
        q(cn, 'runs per day and how far ahead (last 20 runs)', f"SELECT run_date, COUNT(*) AS n, MIN(date) AS first_day, MAX(date) AS last_day FROM {T} WHERE run_date >= CURRENT_DATE - 20 GROUP BY 1 ORDER BY 1")
        q(cn, 'history depth', f"SELECT MIN(run_date) AS first_run, MAX(run_date) AS last_run, COUNT(DISTINCT run_date) AS runs FROM {T}")
        q(cn, 'sample rows (latest run)', f'SELECT variable, date, run_date, station, ctl, "1", "2", "3", "50" FROM {T} WHERE run_date = (SELECT MAX(run_date) FROM {T}) ORDER BY variable, station, date LIMIT 30')
except BaseException:
    w('CANPOWER FAILED'); w(traceback.format_exc()[-800:])
try:
    cn, _, _ = u._connect(cfg, 'forwards')
    with cn:
        q(cn, 'Warehouse WindForecast: sources and lead reach, issues in the last 3 days',
          "SELECT DataSourceName, COUNT(*) AS n, MAX(DATEDIFF(hour, [Timestamp], EffectiveDateTime)) AS max_lead_h FROM Warehouse.dbo.WindForecast WITH (NOLOCK) "
          "WHERE [Timestamp] >= DATEADD(day,-3,GETDATE()) GROUP BY DataSourceName ORDER BY 1")
        q(cn, 'Warehouse WeatherHourlyForecast: columns + 5 rows', "SELECT TOP 5 * FROM Warehouse.dbo.WeatherHourlyForecast WITH (NOLOCK) ORDER BY 1 DESC")
except BaseException:
    w('Warehouse FAILED'); w(traceback.format_exc()[-800:])
(HERE / 'cache' / 'ensemble_probe.txt').write_text('\n'.join(out), encoding='utf-8')
print('\nwritten to cache/ensemble_probe.txt')
