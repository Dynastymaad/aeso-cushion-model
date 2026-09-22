"""One-off: does the forward price query work? Writes cache/forward_px.csv."""
import json, sys, warnings
warnings.filterwarnings('ignore')
from pathlib import Path
import pandas as pd, pyodbc
R = Path(__file__).resolve().parent
cfg = json.loads((R/'db.json').read_text())
have = pyodbc.drivers()
cands = ([cfg['driver']] if cfg.get('driver') in have else []) + [d for d in have if 'SQL Server' in d]
for d in cands:
    for cert in (True, False):
        try:
            cs = (f"DRIVER={{{d}}};SERVER={cfg['server']};DATABASE={cfg['database']};"
                  f"UID={{{cfg['username']}}};PWD={{{cfg['password']}}};"
                  + ("TrustServerCertificate=yes;" if cert else ""))
            cn = pyodbc.connect(cs, timeout=30); print(f"connected via '{d}'"); break
        except Exception as e: err = str(e)[:150]
    else: continue
    break
else:
    raise SystemExit(f"could not connect - {err}")
q = (R/'sql'/'50_forward_prices.sql').read_text().replace('{DAYS}','500')
df = pd.read_sql(q, cn); cn.close()
print(f"{len(df):,} rows")
if len(df):
    print(df.head(3).to_string(index=False))
    print(f"dates {df.EffectiveDate.min()} -> {df.EffectiveDate.max()}")
    print(f"{df.strip.nunique()} distinct strips, {df.EffectiveDate.nunique()} trade dates")
    (R/'cache').mkdir(exist_ok=True)
    df.to_csv(R/'cache'/'forward_px.csv', index=False)
    print("wrote cache/forward_px.csv")
