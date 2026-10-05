"""
diag_thermal.py -- READ-ONLY check of why the forward-thermal pull fails.
Runs each piece of pull_daily_inputs.py's thermal step on its own against
canpower.aeso_fundamentals_snapshots and prints the full error for any that
fail. Writes the same report to cache/diag_thermal.txt. Changes nothing and
prints no credentials.

    python diag_thermal.py
"""
import json, time, traceback
from pathlib import Path
import pandas as pd
HERE = Path(__file__).resolve().parent
import update as u, pull_daily_inputs as p
out = []
def w(s=''): print(s, flush=True); out.append(str(s))
def q(cn, label, sql):
    t0 = time.time()
    try:
        df = pd.read_sql(sql, cn); w(f'\n--- {label}  ({time.time()-t0:.1f}s, {len(df)} rows)'); w(df.head(40).to_string(index=False)); return df
    except Exception:
        w(f'\n--- {label}  FAILED after {time.time()-t0:.1f}s'); w(traceback.format_exc()); return None
pd.set_option('display.width', 250)
cfg = json.loads((HERE / 'db.json').read_text()); cc = u.comp_cfg(cfg)
tbl = cc.get('composition_table') or 'canpower.aeso_fundamentals_snapshots'
w(f'table: {tbl}')
try:
    cn, drv, _ = u._connect(cc, 'composition'); w(f'connected (driver {drv})')
except BaseException:
    w('CONNECT FAILED'); w(traceback.format_exc()); cn = None
if cn is not None:
    with cn:
        sch, name = tbl.split('.') if '.' in tbl else ('public', tbl)
        q(cn, 'server clock and time zone', "SELECT NOW() AS server_now, current_setting('TimeZone') AS tz")
        q(cn, 'column types', f"SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='{sch}' AND table_name='{name}' AND column_name IN ('datetime_begin','render_time','sc','cogen','cc','gfs')")
        q(cn, 'latest render and furthest hour (renders in the last day)',
          f"SELECT MAX(render_time) AS last_render, MIN(datetime_begin) AS first_hour, MAX(datetime_begin) AS last_hour, COUNT(*) AS n FROM {tbl} WHERE render_time >= NOW() - INTERVAL '1 day'")
        q(cn, 'how far ahead each recent render reaches (hours)',
          f"""SELECT CASE WHEN h<0 THEN 'past' WHEN h<24 THEN '0-24h' WHEN h<48 THEN '24-48h' WHEN h<168 THEN '2-7 days'
                         WHEN h<384 THEN '7-16 days' ELSE '16+ days' END AS ahead, COUNT(*) AS rows_, COUNT(DISTINCT datetime_begin) AS hours
              FROM (SELECT datetime_begin, EXTRACT(EPOCH FROM (datetime_begin - render_time))/3600.0 AS h
                    FROM {tbl} WHERE render_time >= NOW() - INTERVAL '1 day') s GROUP BY 1 ORDER BY 1""")
        df = q(cn, 'the exact FWD_THERMAL query pull_daily_inputs.py runs', p.FWD_THERMAL.replace('{TABLE}', tbl))
        if df is not None and len(df):
            df.columns = [c.lower() for c in df.columns]
            w(f'forward hours returned {len(df)}: {df.datetime_begin.min()} to {df.datetime_begin.max()}; null sc/cogen/cc/gfs: '
              + str(df[['sc','cogen','cc','gfs']].isna().sum().to_dict()))
        q(cn, 'FWD_SUPPLY, 3 days only', p.FWD_SUPPLY.replace('{TABLE}', tbl).replace('{DAYS}', '3'))
(HERE / 'cache' / 'diag_thermal.txt').write_text('\n'.join(out), encoding='utf-8')
print('\nwritten to cache/diag_thermal.txt')
