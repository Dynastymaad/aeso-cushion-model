"""
wx_fcst.py - the next 16 days of Alberta weather, hourly, same three sites and
same columns as wx_hourly.csv (temp, wind at 100 m, radiation, cloud), from the
Open-Meteo forecast endpoint. Writes cache/wx_fcst.csv (overwritten each run).
Used by checklist_ab.py for the forward weather, load-from-temperature and
wind-from-wind-speed views. Days beyond 16 fall back to climatology there.

    python wx_fcst.py
"""
import urllib.parse
import pandas as pd
from wx import _get, _frame, SITES, VARS, TZ, CACHE, say

def main():
    frames = []
    for name, lat, lon in SITES:
        q = {'latitude': lat, 'longitude': lon, 'hourly': ','.join(VARS), 'timezone': TZ, 'forecast_days': 16}
        js = _get('https://api.open-meteo.com/v1/forecast?' + urllib.parse.urlencode(q))
        f = _frame(js, name); f['site'] = name; frames.append(f); say(f'  {name}: {len(f)} hours')
    d = pd.concat(frames).groupby('t')[['temp', 'wind', 'rad', 'cloud']].mean().reset_index()
    CACHE.mkdir(exist_ok=True); d.to_csv(CACHE / 'wx_fcst.csv', index=False)
    say(f'wrote cache/wx_fcst.csv: {d.t.min()} to {d.t.max()}')

if __name__ == '__main__':
    main()
