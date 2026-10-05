"""
pull_stormvista.py -- StormVista API: AESO wind (and solar) generation forecasts,
every model run saved, so the live model and the backtest can use the SAME source.

Endpoints (from StormVista's own API spec, https://www.stormvistawxmodels.com/api):
  {BASE}/model-data/{model}/{YYYYMMDD}/{cycle}z/renewables/aeso-windgen-forecast-hourly.csv
      hourly AESO wind generation, INCLUDING ENSEMBLE MEMBERS; archived back to
      when the product started (best effort, files can be missing)
  {BASE}/model-data/{model}/{YYYYMMDD}/{cycle}z/renewables/aeso-solargen-forecast.csv
  {BASE}/model-data/history/aeso-wind-generation-full-history.csv   observed AESO wind
  {BASE}/model-data/meta/aeso_wind_capacity_{YYYYMMDD}.csv          installed capacity
  models used here: ecmwf-eps (51 members, ~15 days), gfs-ens-mem, cmc-ens, ecmwf-weekly (46 days)

The key is read from stormvista_key.txt (this folder, or Grabbing Data) and is
never printed or written anywhere. Files land in cache/stormvista/<model>/.

    python pull_stormvista.py --test                         # one file, shows its layout
    python pull_stormvista.py                                # daily: last 4 days of runs, any missing
    python pull_stormvista.py --start 2024-08-01 --end 2026-10-05   # backfill (skips files already saved)
"""
import argparse, sys, time
from pathlib import Path
import pandas as pd
import urllib.request, urllib.parse, urllib.error

HERE = Path(__file__).resolve().parent
OUT = HERE / 'cache' / 'stormvista'
BASE = 'https://api.stormvistawxmodels.com/v1/model-data'
KEYS = [HERE / 'stormvista_key.txt', Path.home() / 'Documents' / 'Grabbing Data' / 'stormvista_key.txt',
        Path.home() / 'OneDrive - Dynasty Power' / 'Desktop' / 'Grabbing Data' / 'stormvista_key.txt',
        Path.home() / 'Desktop' / 'Grabbing Data' / 'stormvista_key.txt', HERE.parent / 'Grabbing Data' / 'stormvista_key.txt']
PRODUCTS = {'wind': 'renewables/aeso-windgen-forecast-hourly.csv', 'solar': 'renewables/aeso-solargen-forecast.csv'}

def key():
    for p in KEYS:
        if p.exists():
            k = p.read_text().strip().split()[-1]
            if k: return k
    sys.exit('stormvista_key.txt not found (looked in this folder and Grabbing Data)')

class R:
    """Minimal response (standard library only - no extra packages needed)."""
    def __init__(self, code, content): self.status_code, self.content = code, content
    @property
    def ok(self): return self.status_code == 200
    @property
    def text(self): return self.content.decode('utf-8', 'replace')

def get(url, k, tries=3):
    full = url + '?' + urllib.parse.urlencode({'apikey': k})
    r = R(0, b'')
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(full, headers={'User-Agent': 'aeso-cushion-model'}), timeout=60) as resp:
                return R(resp.status, resp.read())
        except urllib.error.HTTPError as e:
            r = R(e.code, b'')
            if e.code == 429: time.sleep(5 * (i + 1)); continue
            return r
        except Exception as e:
            r = R(-1, b''); time.sleep(3)
    return r

def say(m): print(f'  {m}', flush=True)

DAILY = OUT / 'sv_daily.csv'
def read_run(f):
    """One saved run -> hourly ensemble mean in Calgary local time (hour-beginning,
    matching the composition table). Column k = valid time run(00z UTC) + k hours;
    verified against StormVista's own observed history (corr 0.996 at this alignment).
    The repeated hour at the autumn DST change is dropped."""
    run = pd.Timestamp(f.name.split('_')[2]).tz_localize('UTC') + pd.Timedelta(hours=int(f.name.split('_')[3][:2]))
    d = pd.read_csv(f)
    k = [int(c) for c in d.columns[1:]]
    v = d.iloc[:, 1:].astype(float)
    t = (run + pd.to_timedelta(k, unit='h')).tz_convert('America/Edmonton').tz_localize(None)
    s = pd.Series(v.mean(axis=0).values, index=t)
    return run, s[~s.index.duplicated(keep='first')]

def build_daily(rebuild=False):
    """Daily means per (model, product, run date, lead L = local day - run date) -
    the table the page uses to correct each lead by its own recent miss."""
    old = pd.read_csv(DAILY, parse_dates=['run_date', 'day']) if (DAILY.exists() and not rebuild) else None
    have = set() if old is None else set(zip(old.model, old['prod'], old.run_date.dt.strftime('%Y%m%d')))
    rows = []
    for mdir in sorted(p for p in OUT.iterdir() if p.is_dir() and p.name != 'history'):
        for f in sorted(mdir.glob('aeso_*_*_00z.csv')):
            pr, ds = f.name.split('_')[1], f.name.split('_')[2]
            if (mdir.name, pr, ds) in have: continue
            try:
                run, s = read_run(f)
            except Exception as e:
                say(f'unreadable {f.name}: {e}'); continue
            rd = pd.Timestamp(ds); g = s.groupby(s.index.normalize())
            for day, v in g:
                if len(v) >= 23:
                    rows.append((mdir.name, pr, rd, day, int((day - rd).days), float(v.mean())))
    new = pd.DataFrame(rows, columns=['model', 'prod', 'run_date', 'day', 'L', 'mean'])
    out = new if old is None else pd.concat([old, new], ignore_index=True)
    out = out.drop_duplicates(['model', 'prod', 'run_date', 'day'], keep='last').sort_values(['model', 'prod', 'run_date', 'day'])
    tmp = DAILY.with_suffix('.csv.tmp'); out.to_csv(tmp, index=False); tmp.replace(DAILY)
    say(f'sv_daily.csv: {len(new):,} new day-rows, {len(out):,} total, runs to {out.run_date.max():%Y-%m-%d}')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start'); ap.add_argument('--end')
    ap.add_argument('--models', default='ecmwf-eps,gfs-ens-mem,cmc-ens')
    ap.add_argument('--cycles', default='00')
    ap.add_argument('--products', default='wind,solar')
    ap.add_argument('--test', action='store_true'); ap.add_argument('--rebuild-daily', action='store_true')
    a = ap.parse_args(); k = key()
    if a.test:
        d = pd.Timestamp.now().normalize() - pd.Timedelta(days=1)
        url = f"{BASE}/ecmwf-eps/{d:%Y%m%d}/00z/{PRODUCTS['wind']}"
        r = get(url, k); say(f'GET {url}  -> HTTP {r.status_code}, {len(r.content):,} bytes')
        if r.ok:
            lines = r.text.splitlines(); say(f'{len(lines)} lines; first 4 lines (truncated):')
            for l in lines[:4]: print('    ' + l[:220])
        return 0 if r.ok else 1
    end = pd.Timestamp(a.end) if a.end else pd.Timestamp.now().normalize()
    start = pd.Timestamp(a.start) if a.start else end - pd.Timedelta(days=3)
    got = miss = skip = 0; t0 = time.time()
    for d in pd.date_range(start, end):
        for m in a.models.split(','):
            for c in a.cycles.split(','):
                for pr in a.products.split(','):
                    f = OUT / m / f"aeso_{pr}_{d:%Y%m%d}_{c}z.csv"
                    if f.exists() and f.stat().st_size > 0: skip += 1; continue
                    r = get(f"{BASE}/{m}/{d:%Y%m%d}/{c}z/{PRODUCTS[pr]}", k)
                    if r.ok and r.content:
                        f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(r.content); got += 1
                    else:
                        miss += 1
                        if r.status_code not in (404,): say(f'{m} {d:%Y-%m-%d} {c}z {pr}: HTTP {r.status_code}')
                    time.sleep(0.25)
    for name, url in (('aeso-wind-generation-full-history.csv', f'{BASE}/history/aeso-wind-generation-full-history.csv'),
                      (f'aeso_wind_capacity_{end:%Y%m%d}.csv', f'{BASE}/meta/aeso_wind_capacity_{end:%Y%m%d}.csv')):
        r = get(url, k)
        if r.ok: (OUT / 'history').mkdir(parents=True, exist_ok=True); (OUT / 'history' / name).write_bytes(r.content)
        else: say(f'{name}: HTTP {r.status_code}')
    say(f'stormvista: {got} new files, {skip} already saved, {miss} not available ({time.time()-t0:.0f}s) -> cache/stormvista/')
    build_daily(a.rebuild_daily)
    # freshness: the page refuses runs older than 2 days, so say so loudly here
    lw = sorted((OUT / 'ecmwf-eps').glob('aeso_wind_*_00z.csv'))
    if not lw or pd.Timestamp(lw[-1].name.split('_')[2]) < end - pd.Timedelta(days=1):
        say('WARNING: no ECMWF-EPS wind run for today or yesterday - the page will fall back for days 2-14')
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
