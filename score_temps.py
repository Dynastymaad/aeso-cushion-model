"""
score_temps.py - READ-ONLY test: which temperature forecast is more accurate for Alberta,
StormVista's ensemble or Open-Meteo (what the dashboard uses)? Changes nothing in the model.

Truth   : Environment Canada daily max / min observed at Calgary Intl and Edmonton Intl
          (api.weather.gc.ca, climate-daily).
StormVista: Warehouse canpower.aeso_euro_weekly_members_daily_minmax (ensemble mean of the
          members' daily min / max, stations CYYC / CYEG), every run since Dec 2023, leads 1-14.
Open-Meteo: the forecast as it stood 1..7 days before (previous-runs API, hourly temperature
          -> local-day min / max) at the same two airports. That is the forecast the dashboard
          pulls each morning (wx_fcst.py), scored at the same leads.

Writes verify/temp_source_scores.csv and prints average misses by lead.
    python score_temps.py
"""
import json, sys, time, urllib.request, urllib.parse
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent
START = '2024-01-01'; END = (pd.Timestamp.now() - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
STN = {'CYYC': ('CALGARY INT', 51.1139, -114.0203), 'CYEG': ('EDMONTON INT', 53.3097, -113.5797)}
def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'aeso-model'}), timeout=120) as r: return json.loads(r.read())
        except Exception as e:
            if i == tries - 1: raise
            time.sleep(3)
# ---- 1. observations (Environment Canada)
st = get('https://api.weather.gc.ca/collections/climate-stations/items?' + urllib.parse.urlencode({'f': 'json', 'PROV_STATE_TERR_CODE': 'AB', 'limit': 3000}))
S = pd.DataFrame([f['properties'] for f in st['features']])
obs = []
for code, (name, lat, lon) in STN.items():
    c = S[S.STATION_NAME.str.upper().str.contains(name) & S.DLY_LAST_DATE.notna()].sort_values('DLY_LAST_DATE')
    if not len(c): print('no Environment Canada station found for', name); continue
    cid = c.iloc[-1].CLIMATE_IDENTIFIER; print(f'{code}: Environment Canada {c.iloc[-1].STATION_NAME} ({cid})')
    js = get('https://api.weather.gc.ca/collections/climate-daily/items?' + urllib.parse.urlencode(
        {'f': 'json', 'CLIMATE_IDENTIFIER': cid, 'datetime': f'{START}/{END}', 'limit': 10000, 'sortby': 'LOCAL_DATE'}))
    o = pd.DataFrame([f['properties'] for f in js['features']])
    obs.append(pd.DataFrame({'station': code, 'date': pd.to_datetime(o.LOCAL_DATE).dt.normalize(), 'obs_max': o.MAX_TEMPERATURE, 'obs_min': o.MIN_TEMPERATURE}))
O = pd.concat(obs).dropna(subset=['obs_max', 'obs_min'])
print(f'observations: {len(O)} station-days')
# ---- 2. StormVista ensemble (Warehouse)
sys.path.insert(0, str(HERE)); import update as u
cfg = json.loads((HERE / 'db.json').read_text()); cn, _, _ = u._connect(u.comp_cfg(cfg), 'composition')
with cn:
    X = pd.read_sql("SELECT * FROM canpower.aeso_euro_weekly_members_daily_minmax WHERE station IN ('CYYC','CYEG') "
                    f"AND date >= '{START}' AND date <= run_date + INTERVAL '14 days'", cn)
mem = [c for c in X.columns if c == 'ctl' or str(c).isdigit()]
X['f'] = X[mem].astype(float).mean(axis=1); X['c'] = (X.f - 32) * 5 / 9
X['date'] = pd.to_datetime(X.date); X['run_date'] = pd.to_datetime(X.run_date); X['lead'] = (X.date - X.run_date).dt.days
SV = X.pivot_table(index=['station', 'date', 'lead'], columns='variable', values='c').reset_index().rename(columns={'tmax2m': 'fc_max', 'tmin2m': 'fc_min'})
SV['source'] = 'StormVista ensemble'
print(f'StormVista: {len(SV)} station-day-leads')
# ---- 3. Open-Meteo forecast as it stood N days before (previous-runs API)
om = []
for code, (_, lat, lon) in STN.items():
    hv = ','.join(['temperature_2m'] + [f'temperature_2m_previous_day{k}' for k in range(1, 8)])
    for a, b in [(f'{y}-01-01', f'{y}-12-31') for y in range(int(START[:4]), int(END[:4]) + 1)]:
        b = min(b, END)
        js = get('https://previous-runs-api.open-meteo.com/v1/forecast?' + urllib.parse.urlencode(
            {'latitude': lat, 'longitude': lon, 'hourly': hv, 'timezone': 'America/Edmonton', 'start_date': a, 'end_date': b}))
        H = pd.DataFrame(js['hourly']); H['date'] = pd.to_datetime(H.time).dt.normalize()
        for k in range(1, 8):
            col = f'temperature_2m_previous_day{k}'
            if col not in H: continue
            d = H.groupby('date')[col].agg(['max', 'min', 'count']); d = d[d['count'] >= 20]
            om.append(pd.DataFrame({'station': code, 'date': d.index, 'lead': k, 'fc_max': d['max'].values, 'fc_min': d['min'].values}))
OM = pd.concat(om); OM['source'] = 'Open-Meteo (dashboard source)'
print(f'Open-Meteo: {len(OM)} station-day-leads')
# ---- score on the SAME days, stations and leads for both
A = pd.concat([SV, OM]).merge(O, on=['station', 'date'])
A['e_max'] = A.fc_max - A.obs_max; A['e_min'] = A.fc_min - A.obs_min
both = A.groupby(['station', 'date', 'lead']).source.transform('nunique') == 2
A = A[both & A.lead.between(1, 7)]
A.to_csv(HERE / 'verify' / 'temp_source_scores.csv', index=False)
pd.set_option('display.width', 200)
def tab(g):
    return pd.Series({'days': len(g), 'high MAE C': g.e_max.abs().mean(), 'high bias C': g.e_max.mean(), 'low MAE C': g.e_min.abs().mean(),
                      'low bias C': g.e_min.mean(), 'misses > 3C %': 100 * ((g.e_max.abs() > 3) | (g.e_min.abs() > 3)).mean()})
print(f"\nSame days for both sources: {A.date.min():%Y-%m-%d} to {A.date.max():%Y-%m-%d}\n=== by source")
print(A.groupby('source').apply(tab).round(2).to_string())
print('\n=== by source and station'); print(A.groupby(['station', 'source']).apply(tab).round(2).to_string())
print('\n=== by lead (days ahead)'); print(A.groupby(['lead', 'source']).apply(tab).round(2).to_string())
print('\n=== last 90 days'); print(A[A.date >= A.date.max() - pd.Timedelta(days=90)].groupby('source').apply(tab).round(2).to_string())
