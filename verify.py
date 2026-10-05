"""
verify.py -- the forecast journal.

Every refresh publishes a view of the next seven days. This records what that
view SAID, and then, once the hours settle, what actually happened - so at the
end of a month you can ask which parts of the model earn their keep and which
do not, against evidence rather than memory.

Three things are stored side by side for every hour:

    what THIS MODEL forecast     price bands, and the fundamentals behind them
    what AESO forecast           their own pool price forecast, their wind call
    what actually happened       settled price and settled fundamentals

and then the part that makes it worth keeping - the error is SPLIT:

    err_fund   the miss caused by forecasting the wrong fundamentals
    err_curve  the miss that remains when the price curve is fed the RIGHT
               fundamentals - i.e. the curve itself being wrong
    err_total  = err_fund + err_curve

Those two columns answer different questions and want different fixes. A month
of err_fund says the inputs need work. A month of err_curve says the mapping
from cushion to price does.

    python verify.py                 score whatever has settled since last run
    python verify.py --backfill      walk git history and score everything
    python verify.py --report        print a summary of what is in the journal

Writes verify/hourly.csv, append-only, one row per (vintage, target hour).
Re-running never duplicates: rows are keyed and de-duplicated on that pair.
"""
import argparse, json, os, re, subprocess, sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / 'verify'
LOG = OUT / 'hourly.csv'
PAGE = 'docs/index.html'

KEYS = ['q10', 'q25', 'q50', 'q75', 'q90', 'mean', 'bid', 'offer',
        'p50', 'p100', 'p300']
ZONES = [(-1e9, 600, 'tight'), (600, 1200, 'snug'), (1200, 1800, 'normal'),
         (1800, 2500, 'loose'), (2500, 1e9, 'long')]


def say(m=''):
    print(m, flush=True)


def tgroup(he):
    return 0 if he <= 6 else 1 if he <= 10 else 2 if he <= 16 else 3 if he <= 21 else 4


def grid_index(grid):
    G = {}
    for r in grid:
        G.setdefault((r['g'], r.get('L', 0) or 0), []).append(r)
    for k in G:
        G[k].sort(key=lambda r: r['c'])
    return G


def look(G, cush, g, L=0):
    """Same linear-in-cushion lookup the page does, so the recorded numbers are
    the numbers you were actually shown. L = the lead bucket the page used for
    that hour (pages built since 2026-09-30 carry one grid per bucket)."""
    a = G.get((g, L)) or G[(g, 0)]
    if cush <= a[0]['c']:
        return {k: a[0].get(k) for k in KEYS}
    if cush >= a[-1]['c']:
        return {k: a[-1].get(k) for k in KEYS}
    i = 1
    while i < len(a) and a[i]['c'] < cush:
        i += 1
    lo, hi = a[i - 1], a[i]
    t = (cush - lo['c']) / (hi['c'] - lo['c'])
    out = {}
    for k in KEYS:
        x, y = lo.get(k), hi.get(k)
        out[k] = None if x is None or y is None else x + t * (y - x)
    return out


def cushion_of(h):
    gas = h.get('cc', 0) + h.get('sc', 0) + h.get('cogen', 0) + h.get('gfs', 0)
    internal = gas + h.get('bio', 0) + h.get('wind', 0) + h.get('solar', 0)
    it = h.get('bcmatl', 0) + h.get('sask', 0)
    return internal - (h.get('load', 0) - it), gas, it


def zone_of(c):
    for lo, hi, lab in ZONES:
        if lo <= c < hi:
            return lab
    return '?'


def payload_from(html):
    m = re.search(r'^const D = (\{.*\});\s*$', html, re.M)
    return json.loads(m.group(1)) if m else None


def git_versions(limit=None):
    """(sha, committed_at, html) for every published page in history, oldest first."""
    try:
        out = subprocess.run(['git', 'log', '--format=%H %cI', '--', PAGE],
                             cwd=HERE, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        lines = [l for l in out.stdout.splitlines() if l.strip()]
    except Exception as e:
        say(f"  git history unavailable ({e}); using the working copy only")
        lines = []
    lines.reverse()
    if limit:
        lines = lines[-limit:]
    for line in lines:
        sha, iso = line.split(' ', 1)
        try:
            blob = subprocess.run(['git', 'show', f'{sha}:{PAGE}'], cwd=HERE,
                                  capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60).stdout
        except Exception:
            continue
        if blob:
            yield sha[:10], iso.strip(), blob


def outturn(root):
    """Settled price and settled fundamentals, hour by hour."""
    c = pd.read_csv(root / 'cache' / 'composition.csv')
    c['t'] = pd.to_datetime(c['datetime_begin'])
    if 'lead_bucket' in c:
        c = c[c.lead_bucket == -1]
    c = c.dropna(subset=['t']).sort_values('t').drop_duplicates('t', keep='last').set_index('t')
    c['gas_a'] = c[['sc', 'cogen', 'cc', 'gfs']].sum(axis=1)
    c['it_a'] = c['net_imports_actual_scheduled']
    c['cush_a'] = (c.gas_a + c.biomass_and_other + c.wind + c.solar) - (c.ail - c.it_a)
    A = c[['ail', 'wind', 'solar', 'gas_a', 'it_a', 'cush_a', 'forecast_pool_price']].copy()
    A.columns = ['load_a', 'wind_a', 'solar_a', 'gas_a', 'it_a', 'cush_a', 'aeso_px_fc']

    p = root / 'model' / 'price_history.csv'
    if p.exists():
        px = pd.read_csv(p, index_col=0, parse_dates=True)
        A['price'] = px.iloc[:, 0].reindex(A.index)
    else:
        A['price'] = np.nan

    # AESO's own day-ahead wind call, for comparison against ours
    try:
        w = pd.read_csv(root / 'cache' / 'wind_fc.csv')
        w = w[w.DataSourceName == 'AESO'].copy()
        w['iss'] = pd.to_datetime(w.Timestamp, errors='coerce')
        w['tgt'] = pd.to_datetime(w.EffectiveDateTime, errors='coerce')
        w = w.dropna(subset=['iss', 'tgt'])
        w['lead'] = (w.tgt - w.iss).dt.total_seconds() / 3600
        w = w[(w.lead >= 12) & (w.lead <= 40)].sort_values('iss')
        A['aeso_wind_da'] = w.groupby('tgt').Value.last().reindex(A.index)
    except Exception:
        A['aeso_wind_da'] = np.nan
    return A


def score(root, limit=None, backfill=False):
    A = outturn(root)
    rows = []
    seen = set()
    if LOG.exists():
        old = pd.read_csv(LOG, usecols=['vintage', 'target'])
        seen = set(zip(old.vintage, old.target))

    versions = list(git_versions(limit)) if backfill else list(git_versions(20))   # the last 20 published pages, so every morning run's forecast gets scored once it settles
    cur = (root / PAGE)
    if cur.exists():
        versions.append(('working', datetime.now().isoformat(timespec='seconds'),
                         cur.read_text(encoding='utf-8')))
    if not versions:
        say("  nothing to score - no published page found.")
        return pd.DataFrame()

    for sha, iso, html in versions:
        D = payload_from(html)
        if not D or 'grid' not in D or 'hours' not in D:
            continue
        G = grid_index(D['grid'])
        pub = pd.Timestamp(iso).tz_localize(None) if 'T' in iso else pd.Timestamp(iso)
        for h in D['hours']:
            if h.get('status') != 'FORECAST':
                continue                      # only score what was a forecast then
            t = pd.Timestamp(h['dt'])
            if (sha, str(t)) in seen:
                continue
            if t not in A.index or pd.isna(A.at[t, 'price']):
                continue                      # not settled yet - leave for a later run
            g = tgroup(int(h['he']))
            cf, gas_fc, it_fc = cushion_of(h)
            ca = float(A.at[t, 'cush_a'])
            at_fc = look(G, cf, g, h.get('lb', 0) or 0)   # what the page showed
            at_act = look(G, ca, g, h.get('lb', 0) or 0)  # what it would have shown, inputs right
            px = float(A.at[t, 'price'])
            r = {
                'vintage': sha, 'published': pub.strftime('%Y-%m-%d %H:%M'),
                'target': str(t), 'he': int(h['he']),
                'lead_h': round((t - pub).total_seconds() / 3600, 1),
                # --- what this model said
                'cush_fc': round(cf, 1), 'zone_fc': zone_of(cf),
                'p10': at_fc['q10'], 'p25': at_fc['q25'], 'p50': at_fc['q50'],
                'p75': at_fc['q75'], 'p90': at_fc['q90'], 'ev': at_fc['mean'],
                'bid': at_fc['bid'], 'offer': at_fc['offer'],
                'pr50': at_fc['p50'], 'pr100': at_fc['p100'], 'pr300': at_fc['p300'],
                # --- the fundamentals it assumed
                'load_fc': h.get('load'), 'wind_fc': h.get('wind'),
                'solar_fc': h.get('solar'), 'gas_fc': round(gas_fc, 1),
                'it_fc': round(it_fc, 1), 'hydstor_fc': h.get('hydstor'),
                # --- what AESO said
                'aeso_px_fc': A.at[t, 'aeso_px_fc'],
                'aeso_wind_da': A.at[t, 'aeso_wind_da'],
                # --- what happened
                'price': px, 'cush_a': round(ca, 1), 'zone_a': zone_of(ca),
                'load_a': A.at[t, 'load_a'], 'wind_a': A.at[t, 'wind_a'],
                'solar_a': A.at[t, 'solar_a'], 'gas_a': A.at[t, 'gas_a'],
                'it_a': A.at[t, 'it_a'],
            }
            # --- input errors, forecast minus actual
            r['err_load'] = round(r['load_fc'] - r['load_a'], 1)
            r['err_wind'] = round(r['wind_fc'] - r['wind_a'], 1)
            r['err_solar'] = round(r['solar_fc'] - r['solar_a'], 1)
            r['err_gas'] = round(r['gas_fc'] - r['gas_a'], 1)
            r['err_it'] = round(r['it_fc'] - r['it_a'], 1)
            r['err_cush'] = round(cf - ca, 1)
            # --- THE SPLIT. p50 and EV each decomposed.
            for tag, k in (('p50', 'q50'), ('ev', 'mean')):
                fc, act = at_fc[k], at_act[k]
                if fc is None or act is None:
                    continue
                r[f'{tag}_at_act'] = round(act, 2)
                r[f'{tag}_err_total'] = round(fc - px, 2)
                r[f'{tag}_err_fund'] = round(fc - act, 2)      # wrong inputs
                r[f'{tag}_err_curve'] = round(act - px, 2)     # wrong mapping
            # --- was the settled price inside the stated bands?
            if at_fc['q25'] is not None:
                r['in_p25_p75'] = int(at_fc['q25'] <= px <= at_fc['q75'])
                r['in_p10_p90'] = int(at_fc['q10'] <= px <= at_fc['q90'])
            r['aeso_err'] = (None if pd.isna(r['aeso_px_fc'])
                             else round(float(r['aeso_px_fc']) - px, 2))
            rows.append(r)
            seen.add((sha, str(t)))

    df = pd.DataFrame(rows)
    if df.empty:
        say("  no new settled hours to score.")
        return df
    OUT.mkdir(exist_ok=True)
    hdr = not LOG.exists()
    tmp = LOG.with_suffix('.csv.tmp')
    if LOG.exists():
        pd.concat([pd.read_csv(LOG), df], ignore_index=True) \
          .drop_duplicates(['vintage', 'target'], keep='last').to_csv(tmp, index=False)
    else:
        df.to_csv(tmp, index=False)
    os.replace(tmp, LOG)
    say(f"  scored {len(df):,} hour(s) -> {LOG}")
    return df


def report(root):
    if not LOG.exists():
        say("  nothing recorded yet - run  python verify.py --backfill  first.")
        return
    d = pd.read_csv(LOG)
    d['target'] = pd.to_datetime(d.target)
    say(f"\nFORECAST JOURNAL  {len(d):,} scored hours, "
        f"{d.target.min():%Y-%m-%d} to {d.target.max():%Y-%m-%d}, "
        f"{d.vintage.nunique()} publish vintages\n")

    def blk(s, lab):
        if not len(s):
            return
        say(f"{lab:<22}{len(s):>7,}"
            f"{s.p50_err_total.abs().mean():>11.2f}{s.p50_err_total.mean():>+10.2f}"
            f"{s.p50_err_fund.abs().mean():>11.2f}{s.p50_err_curve.abs().mean():>11.2f}"
            f"{100*s.in_p25_p75.mean():>9.0f}%{100*s.in_p10_p90.mean():>9.0f}%")

    say(f"{'':<22}{'n':>7}{'|err| P50':>11}{'bias':>10}"
        f"{'from inputs':>11}{'from curve':>11}{'in 25-75':>10}{'in 10-90':>9}")
    blk(d, 'ALL')
    say('')
    for lo, hi, lab in [(0, 6, 'lead 0-6 h'), (6, 18, 'lead 6-18 h'),
                        (18, 42, 'lead 18-42 h'), (42, 999, 'lead 42 h+')]:
        blk(d[(d.lead_h >= lo) & (d.lead_h < hi)], f'  {lab}')
    say('')
    for z in ['tight', 'snug', 'normal', 'loose', 'long']:
        blk(d[d.zone_fc == z], f'  cushion {z}')

    say(f"\n{'WHERE THE INPUTS WENT WRONG':<28}{'bias':>10}{'MAE':>10}")
    for k, lab in [('err_load', 'load'), ('err_wind', 'wind'), ('err_solar', 'solar'),
                   ('err_gas', 'gas avail'), ('err_it', 'intertie'), ('err_cush', 'CUSHION')]:
        if k in d:
            say(f"  {lab:<26}{d[k].mean():>+10.0f}{d[k].abs().mean():>10.0f}  MW")
    if 'aeso_err' in d and d.aeso_err.notna().any():
        a = d.dropna(subset=['aeso_err'])
        say(f"\nAESO's forecast_pool_price, same hours  ({len(a):,}) "
            f"|err| ${a.aeso_err.abs().mean():.2f} vs this model ${a.p50_err_total.abs().mean():.2f}")
        say("  NOT A FAIR FIGHT. That field comes from the lead_bucket = -1 snapshot,")
        say("  taken a median 55 minutes INTO the hour it forecasts - a nowcast with")
        say("  most of the hour already observed. This model is called up to 7 days out.")
        say("  Treat it as a floor on what is knowable, not as a rival forecast.")

    # one row per target hour, newest call only - the honest headline number,
    # since every hour appears once per publish vintage in the table above
    u = d.sort_values('lead_h').drop_duplicates('target', keep='first')
    say(f"\nNEWEST CALL PER HOUR ONLY  ({len(u):,} distinct hours)")
    say(f"  |err| P50 ${u.p50_err_total.abs().mean():>7.2f}   bias ${u.p50_err_total.mean():>+7.2f}"
        f"   in P25-P75 {100*u.in_p25_p75.mean():>3.0f}% (want 50)"
        f"   in P10-P90 {100*u.in_p10_p90.mean():>3.0f}% (want 80)")
    da = d[(d.lead_h >= 12) & (d.lead_h <= 40)].sort_values('lead_h').drop_duplicates('target', keep='first')
    if len(da):
        say(f"\nDAY-AHEAD VINTAGE ONLY, 12-40 h  ({len(da):,} hours) - the comparable number")
        say(f"  |err| P50 ${da.p50_err_total.abs().mean():>7.2f}   bias ${da.p50_err_total.mean():>+7.2f}"
            f"   from inputs ${da.p50_err_fund.abs().mean():>6.2f}   from curve ${da.p50_err_curve.abs().mean():>6.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--backfill', action='store_true',
                    help='walk git history and score every past publish')
    ap.add_argument('--limit', type=int, default=None,
                    help='with --backfill, only the last N versions')
    ap.add_argument('--report', action='store_true', help='summarise the journal')
    a = ap.parse_args()
    if a.report:
        report(HERE)
        return
    score(HERE, limit=a.limit, backfill=a.backfill)
    report(HERE)


if __name__ == '__main__':
    main()
