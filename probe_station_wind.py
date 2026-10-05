"""
probe_station_wind.py -- READ-ONLY. Does Warehouse.dbo.WeatherHourlyForecast hold
StormVista (or other) WIND SPEED forecasts for Alberta stations, how far ahead,
and how far back does the history go? Writes cache/station_wind_probe.txt.

    python probe_station_wind.py
"""
import json, traceback
from pathlib import Path
import pandas as pd
HERE = Path(__file__).resolve().parent
import update as u
out = []
def w(s=''): print(s, flush=True); out.append(str(s))
pd.set_option('display.width', 250); pd.set_option('display.max_rows', 200)
def q(cn, label, sql):
    try:
        df = pd.read_sql(sql, cn); w(f'\n--- {label} ({len(df)} rows)'); w(df.head(80).to_string(index=False)); return df
    except Exception:
        w(f'\n--- {label} FAILED'); w(traceback.format_exc()[-600:])
cfg = json.loads((HERE / 'db.json').read_text())
T = 'Warehouse.dbo.WeatherHourlyForecast WITH (NOLOCK)'
try:
    cn, _, _ = u._connect(cfg, 'forwards')
    with cn:
        q(cn, 'Canadian stations, variables and sources issued in the last 2 days, with lead reach',
          f"SELECT WeatherStationId, Description, DataSourceName, COUNT(*) AS n, "
          f"MAX(DATEDIFF(hour, ObservationDateTime, EffectiveDateTime)) AS max_lead_h "
          f"FROM {T} WHERE ObservationDateTime >= DATEADD(day,-2,GETDATE()) AND WeatherStationId LIKE 'CY%' "
          f"GROUP BY WeatherStationId, Description, DataSourceName ORDER BY 1,2,3")
        q(cn, 'history depth for Calgary (CYYC) wind speed, by source',
          f"SELECT DataSourceName, MIN(ObservationDateTime) AS first_issue, MAX(ObservationDateTime) AS last_issue, "
          f"COUNT(DISTINCT CAST(ObservationDateTime AS date)) AS issue_days "
          f"FROM {T} WHERE WeatherStationId = 'CYYC' AND Description LIKE 'Wind Speed%' GROUP BY DataSourceName ORDER BY 1")
        q(cn, 'all StormVista sources and their lead reach (last 2 days, any station)',
          f"SELECT DataSourceName, Description, MAX(DATEDIFF(hour, ObservationDateTime, EffectiveDateTime)) AS max_lead_h, COUNT(DISTINCT WeatherStationId) AS stations "
          f"FROM {T} WHERE ObservationDateTime >= DATEADD(day,-2,GETDATE()) GROUP BY DataSourceName, Description ORDER BY 1,2")
except BaseException:
    w('FAILED'); w(traceback.format_exc()[-800:])
(HERE / 'cache' / 'station_wind_probe.txt').write_text('\n'.join(out), encoding='utf-8')
print('\nwritten to cache/station_wind_probe.txt')
