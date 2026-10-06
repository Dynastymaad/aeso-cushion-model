"""compare_temps.py - READ-ONLY: StormVista ensemble daily min/max (Warehouse table
canpower.aeso_euro_weekly_members_daily_minmax, deg F) vs the Open-Meteo temps the
dashboard uses (cache/wx_fcst.csv, 3-site average Calgary / Edmonton / Pincher Creek).
    python compare_temps.py            (Wednesday Oct 7 by default)
    python compare_temps.py 2026-10-08
"""
import sys, json
from pathlib import Path
import pandas as pd
HERE = Path(__file__).resolve().parent
import update as u
day = sys.argv[1] if len(sys.argv) > 1 else '2026-10-07'
cfg = json.loads((HERE / 'db.json').read_text())
cn, _, _ = u._connect(u.comp_cfg(cfg), 'composition')
with cn:
    X = pd.read_sql(f"SELECT * FROM canpower.aeso_euro_weekly_members_daily_minmax WHERE date = '{day}' "
                    f"AND run_date = (SELECT MAX(run_date) FROM canpower.aeso_euro_weekly_members_daily_minmax WHERE date = '{day}')", cn)
mem = [c for c in X.columns if c == 'ctl' or str(c).isdigit()]
X['ens_mean'] = X[mem].astype(float).mean(axis=1); X['ens_min'] = X[mem].astype(float).min(axis=1); X['ens_max'] = X[mem].astype(float).max(axis=1)
print(f"StormVista run {X.run_date.iloc[0] if len(X) else '?'} for {day}  (deg F, ensemble mean [lowest - highest member])")
for st, g in X.groupby('station'):
    r = {v: g[g.variable == v].iloc[0] for v in g.variable}
    lo = r.get('tmin2m'); hi = r.get('tmax2m')
    print(f"  {st}: min {lo.ens_mean:5.1f} [{lo.ens_min:.0f}-{lo.ens_max:.0f}]   max {hi.ens_mean:5.1f} [{hi.ens_min:.0f}-{hi.ens_max:.0f}]"
          f"   ({(lo.ens_mean-32)*5/9:.1f} / {(hi.ens_mean-32)*5/9:.1f} C)")
w = pd.read_csv(HERE / 'cache' / 'wx_fcst.csv', parse_dates=['t']); w = w[w.t.dt.strftime('%Y-%m-%d') == day]
print(f"dashboard (Open-Meteo, 3-site average, local day): min {w.temp.min()*9/5+32:.1f} F  max {w.temp.max()*9/5+32:.1f} F"
      f"  ({w.temp.min():.1f} / {w.temp.max():.1f} C)")
