"""
archive_live.py -- keep EVERYTHING the live model saw and said each morning,
so the model can be re-tested and re-calibrated on its own real record every
couple of months (instead of on reconstructed substitutes).

Runs at the end of Daily.ps1. Local only (archive/ is never published).

  archive/live/YYYY-MM-DD.zip   one per day:
      model_hours.csv     every hour on the page: all inputs as used (load, wind,
                          solar, cogen/cc/gas steam/simple cycle, bio, imports,
                          hydro/storage), lead, cushion, and the model's EV and
                          P10/P25/P50/P75/P90 from that day's grid
      grid.csv, meta.json the price grid and calibration that produced them
      fwd_thermal.csv, thermal_status.json, supply outlook rows of the day
      wx_fcst.csv         the temperature / wind-speed forecast used past day 7
      fwd_settle.csv      the ICE settles the page priced against
      trades.html, possible_trades rows, week_signal row
  archive/live/forecasts_master.csv   the same hourly forecast rows appended
                          every day (one row per run day x target hour), ready
                          to score against what settled
  archive/YYYY-MM-DD.zip  (already written by update.py) the raw AESO feeds

    python archive_live.py
"""
import json, re, zipfile, io
from pathlib import Path
import numpy as np, pandas as pd

HERE = Path(__file__).resolve().parent
LIVE = HERE / 'archive' / 'live'; LIVE.mkdir(parents=True, exist_ok=True)
def tg(he): return 0 if he <= 6 else 1 if he <= 10 else 2 if he <= 16 else 3 if he <= 21 else 4

def main():
    now = pd.Timestamp.now(tz='America/Edmonton').tz_localize(None); day = now.normalize()
    s = (HERE / 'docs' / 'index.html').read_text(encoding='utf-8')
    D = json.loads(re.search(r'^const D = (\{.*\});\s*$', s, re.M).group(1))
    G = {}
    for r in D['grid']: G.setdefault((r['g'], r.get('L', 0) or 0), []).append(r)
    for k in G: G[k].sort(key=lambda r: r['c'])
    def look(c, g, L, key):
        a = G.get((g, L)) or G[(g, 0)]
        return float(np.interp(c, [r['c'] for r in a], [r[key] for r in a]))
    H = pd.DataFrame(D['hours'])
    H['cush'] = H.cc + H.sc + H.cogen + H.gfs + H.bio + H.wind + H.solar - (H.load - H.bcmatl - H.sask)
    for k in ('mean', 'q10', 'q25', 'q50', 'q75', 'q90'):
        H['m_' + k] = [round(look(c, tg(he), int(lb or 0), k), 2) for c, he, lb in zip(H.cush, H.he, H.get('lb', pd.Series(0, index=H.index)).fillna(0))]
    H.insert(0, 'run_date', str(day.date())); H.insert(1, 'run_time', str(now)[:16])
    files = {'model_hours.csv': H.to_csv(index=False), 'grid.csv': pd.DataFrame(D['grid']).to_csv(index=False),
             'meta.json': json.dumps(D['meta'], indent=1, default=str)}
    def add(name, p):
        if p.exists(): files[name] = p.read_bytes()
    add('fwd_thermal.csv', HERE / 'cache' / 'fwd_thermal.csv')
    add('thermal_status.json', HERE / 'cache' / 'thermal_status.json')
    add('wx_fcst.csv', HERE / 'cache' / 'wx_fcst.csv')
    add('outlook.csv', HERE / 'cache' / 'outlook.csv')
    add('index.html', HERE / 'docs' / 'index.html')   # dashboard incl. the Trades tab data (const T)
    add('leadcal.json', HERE / 'model' / 'leadcal.json')
    add('risk_today.json', HERE / 'cache' / 'risk_today.json')
    try:
        v = pd.read_csv(HERE / 'cache' / 'supply_vintages_24m.csv'); files['supply_outlook_today.csv'] = v[v.vday.astype(str).str[:10] == str(day.date())].to_csv(index=False)
    except Exception: pass
    try:
        f = pd.read_csv(HERE / 'cache' / 'fwd_aeso_daily.csv', parse_dates=['EffectiveDate']); files['fwd_settle.csv'] = f[f.EffectiveDate == f.EffectiveDate.max()].to_csv(index=False)
    except Exception: pass
    for name, p in (('possible_trades_today.csv', HERE / 'verify' / 'possible_trades.csv'), ('week_signal_today.csv', HERE / 'verify' / 'week_signal.csv')):
        try:
            x = pd.read_csv(p); files[name] = x[x.run.astype(str).str[:10] == str(day.date())].to_csv(index=False)
        except Exception: pass
    z = LIVE / f'{day:%Y-%m-%d}.zip'
    with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED) as zf:     # rewritten if run twice the same day: last run wins
        for n, b in files.items(): zf.writestr(n, b)
    fc = H[H.status == 'FORECAST'].drop(columns=[c for c in ('w_lo', 'w_hi', 's_lo', 's_hi', 'price') if c in H])
    mp = LIVE / 'forecasts_master.csv'
    if mp.exists():
        old = pd.read_csv(mp); old = old[old.run_date.astype(str) != str(day.date())]
        fc = pd.concat([old, fc], ignore_index=True)
    tmp = mp.with_suffix('.csv.tmp'); fc.to_csv(tmp, index=False); tmp.replace(mp)
    print(f"  archived {z.relative_to(HERE)} ({z.stat().st_size/1e3:.0f} kB, {len(files)} files); "
          f"forecasts_master.csv now {len(fc):,} rows over {fc.run_date.nunique()} run days")

if __name__ == '__main__':
    main()
