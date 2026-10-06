"""
risk_engine.py -- the Probabilities tab of the Alberta dashboard (docs/index.html).

For every forecast day it draws 1,000 versions of the day and prices each one:
  wind    : StormVista ECMWF-EPS members (their spread around the model's wind); day 1 uses
            past AESO day-ahead misses instead, because that is the day-1 wind source
  others  : load / solar / thermal / biomass / import misses taken TOGETHER from one past
            date at the same lead (last 365 days, settled before today) -> keeps how they move together
  imports : respond to the drawn tightness with the page's own import rule
  price   : the model's 7-day price curve at the drawn cushion x a residual from the pools; the
            residual ranks come from the same past date, so the hours of a day move together
Then two rolling corrections, using only days that have already settled:
  range   : where past settles really fell inside past ranges (weighted by how close past days' level was) remaps the range
  odds    : the chance a short wins is mapped through its own track record
and the page's fair value gets a rolling correction (page EV -> what settled).

Backtest (replay of the live model, Dec 2024 - Sep 2026, 7,389 day-reads, prior-only):
  settle below P10 11.4%, above P90 9.7%  (averaging the hourly percentiles: 3.5% / 14.7%)

Reads : docs/index.html (const D), model/risk_price.npz (written by update.py), cache/stormvista,
        cache/risk_lib.npz (+ new settled days from archive/live), verify/risk_calibration_history.csv,
        verify/risk_book_log.csv, refresh/outage_90d.csv
Writes: docs/index.html (const P line), cache/risk_today.json, verify/risk_book_log.csv (appends)

    python risk_engine.py
"""
import json, re, csv, io, glob, zipfile, sys
from pathlib import Path
import numpy as np, pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'model'))
from core import E, NB, curve, build_pools
N = 1000; TAU = (np.arange(500) + 0.5) / 500; QL = np.arange(1, 100) / 100
COLS = ['m_solar', 'm_load', 'm_gas', 'm_bio', 'm_nires', 'm_wind']
LOG = HERE / 'verify' / 'risk_book_log.csv'
def say(m): print('  ' + m, flush=True)
def tg(he): return 0 if he <= 6 else 1 if he <= 10 else 2 if he <= 16 else 3 if he <= 21 else 4


def load_page():
    s = (HERE / 'docs' / 'index.html').read_text(encoding='utf-8')
    return json.loads(re.search(r'^const D = (\{.*\});\s*$', s, re.M).group(1))


def actuals():
    C = pd.read_csv(HERE / 'cache' / 'composition.csv', parse_dates=['datetime_begin'])
    if 'lead_bucket' in C: C = C[C.lead_bucket == -1]
    C = C.drop_duplicates('datetime_begin', keep='last').set_index('datetime_begin').sort_index()
    C['gas'] = C[['cc', 'sc', 'cogen', 'gfs']].sum(axis=1)
    return C


def library(IX, IY, C, today):
    """Past misses by lead: replay library + every settled day archived since (archive/live)."""
    z = np.load(HERE / 'cache' / 'risk_lib.npz', allow_pickle=False)
    o = list(z['o']); d = list(z['day']); L = list(z['lead']); th = list(z['th']); M = list(z['M'])
    have = set(zip(np.array(o, 'datetime64[D]').astype(str), np.array(d, 'datetime64[D]').astype(str)))
    add = 0
    for zp in sorted(glob.glob(str(HERE / 'archive' / 'live' / '20??-??-??.zip'))):
        run = pd.Timestamp(Path(zp).stem)
        try: H = pd.read_csv(io.BytesIO(zipfile.ZipFile(zp).read('model_hours.csv')))
        except Exception: continue
        H['t'] = pd.to_datetime(H.dt); H['day'] = H.t.dt.normalize()
        for day, g in H[H.status == 'FORECAST'].groupby('day'):
            lead = int((day - run).days)
            if not (1 <= lead <= 13) or day >= today or len(g) != 24 or (str(run.date()), str(day.date())) in have: continue
            a = C.reindex(g.t.values)
            if a[['ail', 'wind', 'solar', 'gas', 'biomass_and_other', 'net_imports_actual_scheduled']].isna().any().any(): continue
            gas = (g.cc + g.sc + g.cogen + g.gfs).values
            a_int = a.gas.values + a.biomass_and_other.values + a.wind.values + a.solar.values
            nires = a.net_imports_actual_scheduled.values - np.interp(a_int - a.ail.values - 125.0, IX, IY)
            m = np.c_[a.solar.values - g.solar.values, a.ail.values - g.load.values, a.gas.values - gas,
                      a.biomass_and_other.values - g.bio.values, nires, a.wind.values - g.wind.values]
            o.append(np.datetime64(run, 'ns')); d.append(np.datetime64(day, 'ns')); L.append(lead); th.append('aeso24'); M.append(m.astype(np.float32)); add += 1
    return dict(o=np.array(o, 'datetime64[ns]'), day=np.array(d, 'datetime64[ns]'), lead=np.array(L), th=np.array(th), M=np.stack(M)), add


def members(today):
    fs = sorted(glob.glob(str(HERE / 'cache' / 'stormvista' / 'ecmwf-eps' / 'aeso_wind_*_00z.csv')))
    fs = [f for f in fs if pd.Timestamp(f[-16:-8]) <= today]
    if not fs: return None, None
    run = pd.Timestamp(fs[-1][-16:-8])
    if (today - run).days > 2: return None, run
    v = pd.read_csv(fs[-1]).iloc[:, 1:].astype(float).values
    t = (run + pd.to_timedelta(np.arange(v.shape[1]), unit='h')).tz_localize('UTC').tz_convert('America/Edmonton').tz_localize(None)
    X = pd.DataFrame((v - v.mean(axis=0)).T, index=t); X = X[~X.index.duplicated()]
    return X, run


def history(today):
    """Settled reads for the rolling corrections: replay history + this engine's own log once settled."""
    H = pd.read_csv(HERE / 'verify' / 'risk_calibration_history.csv', parse_dates=['run', 'day'])
    if LOG.exists():
        try:
            Lg = pd.read_csv(LOG, parse_dates=['run', 'day'])
            Lg = Lg.drop_duplicates(['run', 'day'], keep='last')
            ph = pd.read_csv(HERE / 'model' / 'price_history.csv', index_col=0, parse_dates=True).iloc[:, 0]
            pool = ph.groupby(ph.index.normalize()).agg(['mean', 'size']); pool = pool[pool['size'] >= 23]['mean']
            Lg['settle'] = Lg.day.map(pool); Lg = Lg.dropna(subset=['settle'])
            qc = [c for c in Lg.columns if c.startswith('q') and c[1:].isdigit()]
            if len(Lg) and qc:
                Q = Lg[qc].values; Lg['pit_raw'] = [np.interp(s, q, QL, left=0, right=1) for s, q in zip(Lg.settle, Q)]
                Lg['win'] = (Lg.settle < Lg.fwd - 2.5).astype(float).where(Lg.fwd.notna())
                Lg['source'] = 'live'
                Lg['e50_raw'] = Lg['q50']
                H = pd.concat([H, Lg[['run', 'day', 'lead', 'mincush', 'settle', 'ev_page', 'pit_raw', 'e50_raw', 'pwin_raw', 'win', 'fwd', 'source']]], ignore_index=True)
        except Exception as e: say(f'WARNING risk log not read ({e})')
    return H[H.day < today]


def iso_table(x, y, lo=None, hi=None):
    from sklearn.isotonic import IsotonicRegression
    r = IsotonicRegression(out_of_bounds='clip', y_min=lo, y_max=hi).fit(x, y)
    xs = np.linspace(np.min(x), np.max(x), 60); return xs, r.predict(xs)


def outage_events(today):
    """Gas scheduled back on a day (outage report drops vs the day before) - the obvious named risk."""
    p = HERE / 'refresh' / 'outage_90d.csv'
    if not p.exists(): return {}
    R = list(csv.reader(io.StringIO(p.read_text(encoding='utf-8-sig'))))
    try: hi = [i for i, r in enumerate(R) if r and r[0].strip() == 'Date'][0]
    except IndexError: return {}
    Hh = [c.strip() for c in R[hi]]; rows = {}
    for r in R[hi + 1:]:
        if len(r) < len(Hh) or not r[0].strip(): continue
        try: rows[pd.Timestamp(r[0].strip())] = {k: float(r[Hh.index(k)]) for k in ('SC', 'Cogen', 'CC', 'GFS')}
        except Exception: continue
    ev = {}
    for d, v in rows.items():
        p_ = rows.get(d - pd.Timedelta(days=1))
        if p_ is None or d <= today: continue
        drops = {k: p_[k] - v[k] for k in v if p_[k] - v[k] >= 200}
        if drops:
            k = max(drops, key=drops.get)
            ev[d] = dict(fuel={'CC': 'combined cycle', 'Cogen': 'cogen', 'SC': 'simple cycle', 'GFS': 'gas-fired steam'}[k], mw=round(drops[k]),
                         out_before=round(p_[k]), out_day=round(v[k]))
    return ev


def main():
    now = pd.Timestamp.now(tz='America/Edmonton').tz_localize(None); today = now.normalize()
    D = load_page(); rng = np.random.default_rng(int(today.value // 86400e9))
    G = {}
    for r in D['grid']: G.setdefault((r['g'], r.get('L', 0) or 0), []).append(r)
    for k in G: G[k].sort(key=lambda r: r['c'])
    def look(c, he, L, k='mean'):
        a = G.get((tg(he), L)) or G[(tg(he), 0)]
        return np.interp(c, [r['c'] for r in a], [r[k] for r in a])
    itb = D['itbands']; IX = np.array([b['cushion'] for b in itb if b.get('med') is not None]); IY = np.array([b['med'] for b in itb if b.get('med') is not None])
    o_ = np.argsort(IX); IX, IY = IX[o_], IY[o_]
    # price model from update.py
    z = np.load(HERE / 'model' / 'risk_price.npz')
    XS, YS = z['XS'], z['YS']; ht = pd.to_datetime(z['t']); hc, hg, hr = z['cush'], z['g'], z['r7']
    bins = np.clip(np.digitize(hc, E) - 1, 0, NB - 1); POOL = build_pools(bins, hg, hr); SP = {k: np.sort(v) for k, v in POOL.items()}
    LV = z['lv']
    def kfac(c):
        for lo, hi, k in LV:
            if lo <= c < hi: return k
        return 1.0
    u = np.empty(len(hr))
    for (b, g) in set(zip(bins, hg)):
        m = (bins == b) & (hg == g); p = SP[(int(b), int(g))]; u[m] = np.searchsorted(p, hr[m]) / max(len(p) - 1, 1)
    Uf = pd.DataFrame({'u': u, 'day': ht.normalize()}, index=ht); Uf = Uf[Uf.day >= today - pd.Timedelta(days=400)]
    cnt = Uf.groupby('day').size(); Ud = {d: g.u.values for d, g in Uf[Uf.day.isin(cnt[cnt == 24].index)].groupby('day')}
    Udays = list(Ud)
    C = actuals(); LIB, added = library(IX, IY, C, today)
    MEM, run = members(today)
    H = history(today); H365 = H[H.day >= today - pd.Timedelta(days=365)]
    fvx, fvy = iso_table(H365.ev_page.values, H365.settle.values)
    hw = H365.dropna(subset=['pwin_raw', 'win']); pwx, pwy = iso_table(hw.pwin_raw.values, hw.win.values, 0, 1)
    EV = outage_events(today)
    # forecast days on the page
    F = pd.DataFrame(D['hours']); F['t'] = pd.to_datetime(F.dt); F['day'] = F.t.dt.normalize()
    F = F[(F.status == 'FORECAST') & (F.day > today)]
    F['gas'] = F.cc + F.sc + F.cogen + F.gfs; F['ni'] = F.bcmatl + F.sask
    F['internal'] = F.gas + F.bio + F.wind + F.solar; F['cush'] = F.internal - (F.load - F.ni)
    fwd = None
    try:
        fq = pd.read_csv(HERE / 'cache' / 'fwd_aeso_daily.csv', parse_dates=['EffectiveDate', 'Strip'])
        fq = fq[(fq.EffectiveDate <= today) & (fq.ExchangeCode == 'XDT')]; fwd = fq[fq.EffectiveDate == fq.EffectiveDate.max()].set_index('Strip').Price
    except Exception: pass
    out = {}; logrows = []
    for day, g in F.groupby('day'):
        if len(g) != 24: continue
        g = g.sort_values('t'); L = int((day - today).days); Ll = min(max(L, 1), 13)
        he = g.he.values; base = g.cush.values; lb = int(g.lb.iloc[0] or 0)
        sel = (LIB['lead'] == Ll) & (LIB['day'] < np.datetime64(today)) & (LIB['day'] >= np.datetime64(today - pd.Timedelta(days=365)))
        same = sel & (LIB['th'] == 'aeso24'); sel = same if same.sum() >= 45 else sel
        if sel.sum() < 30: say(f'{day:%b %d}: library too thin ({sel.sum()})'); continue
        lib = LIB['M'][sel]; ldays = LIB['day'][sel]
        pick = rng.integers(0, len(lib), N); m = lib[pick]
        uu = np.stack([Ud.get(pd.Timestamp(ldays[i]).normalize(), Ud[Udays[rng.integers(0, len(Udays))]]) for i in pick])
        if L <= 1 or MEM is None:
            dw = m[:, :, 5]
        else:
            md = MEM.reindex(g.t.values).values.T                         # members x 24
            if np.isnan(md).any(): dw = m[:, :, 5]
            else: dw = np.maximum(g.wind.values + md[rng.integers(0, md.shape[0], N)], 0) - g.wind.values
        def cush_of(ds, dl, dg, db, dn, dww):
            ti = g.internal.values + ds + dg + db + dww; tl = g.load.values + dl
            return ti - (tl - (np.interp(ti - tl - 125.0, IX, IY) + dn))
        ref = cush_of(0, 0, 0, 0, np.median(lib[:, :, 4], axis=0), 0)       # the rule's own cushion with no misses
        true = base + (cush_of(m[:, :, 0], m[:, :, 1], m[:, :, 2], m[:, :, 3], m[:, :, 4], dw) - ref)
        b = np.clip(np.digitize(true, E) - 1, 0, NB - 1); res = np.empty_like(true)
        for h in range(24):
            for bb in np.unique(b[:, h]):
                s_ = b[:, h] == bb; p = SP[(int(bb), int(tg(he[h])))]
                res[s_, h] = p[np.clip((uu[s_, h] * (len(p) - 1)).astype(int), 0, len(p) - 1)]
        k = np.array([kfac(c) for c in base])
        px = np.clip(np.expm1(np.log1p(curve(XS, YS, true.ravel()).reshape(true.shape)) + res), 0, 999.99) * k[None, :]
        dayp = px.mean(axis=1); qraw = np.quantile(dayp, QL)
        # rolling range correction: where past settles fell inside past ranges at a similar level
        # (weighted by closeness of the engine's own raw median; last 365 days). Backtest: 11.4% below P10, 9.7% above P90.
        # sliding version: past reads weighted by how close their raw level was (no bracket edges)
        hz = H365.dropna(subset=['pit_raw', 'e50_raw']); z0 = np.log(np.median(dayp) + 5); bw = 0.3
        for _ in range(6):
            w = np.exp(-0.5 * ((np.log(hz.e50_raw.values + 5) - z0) / bw) ** 2); neff = w.sum() ** 2 / max((w ** 2).sum(), 1e-12)
            if neff >= 60: break
            bw *= 1.5
        if len(hz) >= 60:
            o_ = np.argsort(hz.pit_raw.values); cw = np.cumsum(w[o_]); cw = cw / cw[-1]
            lev = np.interp(TAU, cw, hz.pit_raw.values[o_])
        else: lev = TAU
        hh = hz
        Q = np.quantile(dayp, np.clip(lev, 0, 1))
        evp = float(np.mean([look(c, h_, lb) for c, h_ in zip(base, he)]))
        pg = {q: float(np.mean([look(c, h_, lb, f'q{q}') for c, h_ in zip(base, he)])) for q in (10, 50, 90)}
        f_ = float(fwd.get(day)) if fwd is not None and day in fwd.index else None
        pwr = float((Q < f_ - 2.5).mean()) if f_ is not None else None
        # each risk on its own: its bad (10%) and good (90%) day, others at base, priced off the next-day curve
        def fv_of(c): return float(np.mean([look(x, h_, 0) for x, h_ in zip(c, he)]))
        swings = {}
        drv = {'wind': None, 'load': 1, 'thermal': 2, 'imports': 4, 'solar': 0}
        for name, ci in drv.items():
            if name == 'wind' and L > 1 and MEM is not None and not np.isnan(MEM.reindex(g.t.values).values).any():
                md = MEM.reindex(g.t.values).values.T; paths = np.maximum(g.wind.values + md, 0) - g.wind.values; src = f'{md.shape[0]} StormVista members'
            else:
                paths = lib[:, :, 5 if name == 'wind' else ci] * (-1 if name == 'load' else 1); src = f'{len(lib)} past days at this lead'
            # cushion effect of each path (load misses reduce cushion: sign flipped above)
            dc = []
            for pth in paths:
                args = [0, 0, 0, 0, np.median(lib[:, :, 4], axis=0), 0]
                if name == 'wind': args[5] = pth
                elif name == 'load': args[1] = -pth
                elif name == 'imports': args[4] = pth
                else: args[ci] = pth
                dc.append(cush_of(*args) - ref)
            dc = np.array(dc); dm = dc.mean(axis=1); ib = int(np.argsort(dm)[int(0.1 * (len(dm) - 1))]); ig = int(np.argsort(dm)[int(0.9 * (len(dm) - 1))])
            swings[name] = dict(bad=[round(float(x)) for x in dc[ib]], good=[round(float(x)) for x in dc[ig]],
                                bad_mw=round(float(dm[ib])), good_mw=round(float(dm[ig])), fv_bad=round(fv_of(base + dc[ib]), 2), fv_good=round(fv_of(base + dc[ig]), 2), src=src)
        evt = None
        if day in EV:
            e = EV[day]; gm = lib[:, :, 2].mean(axis=1)
            evt = dict(e, fv=round(fv_of(base - e['mw']), 2), base_rate=round(float((gm <= -e['mw']).mean()), 3),
                       label=f"{e['mw']:,} MW of {e['fuel']} scheduled back on {day:%a %b %d} stays out")
        fvb = fv_of(base); main = max(swings, key=lambda k: abs(swings[k]['fv_bad'] - swings[k]['fv_good']))
        rec = dict(lead=L, mincush=round(float(base.min())), he=[int(x) for x in he], base=[round(float(x)) for x in base],
                   q=[round(float(x), 2) for x in np.quantile(Q, QL)], p10=round(float(np.quantile(Q, .1)), 2), p50=round(float(np.quantile(Q, .5)), 2),
                   p90=round(float(np.quantile(Q, .9)), 2), mean=round(float(Q.mean()), 2), ev_page=round(evp, 2),
                   fv_corr=round(float(np.interp(evp, fvx, fvy)), 2), fv_next=round(fvb, 2),
                   pg10=round(pg[10], 2), pg50=round(pg[50], 2), pg90=round(pg[90], 2), fwd=f_,
                   pwin=None if pwr is None else round(float(np.interp(pwr, pwx, pwy)), 3), pwin_raw=pwr,
                   swings=swings, main=main, event=evt, nlib=int(len(lib)), ncal=int(len(hh)))
        out[str(day.date())] = rec
        logrows.append(dict(run=str(today.date()), day=str(day.date()), lead=L, mincush=rec['mincush'], ev_page=rec['ev_page'], fwd=f_, pwin_raw=pwr,
                            **{f'q{int(round(x*100)):02d}': round(float(v), 3) for x, v in zip(QL, qraw)}))
    P = dict(built=str(now)[:16], today=str(today.date()), members_run=None if run is None else str(run.date()),
             members_ok=MEM is not None, lib_added=added, ncal=int(len(H365)), pw_x=[round(float(x), 4) for x in pwx], pw_y=[round(float(x), 4) for x in pwy],
             grid_note='per-risk values use the next-day price curve (the risk itself is drawn explicitly)', days=out)
    (HERE / 'cache' / 'risk_today.json').write_text(json.dumps(P), encoding='utf-8')
    s = (HERE / 'docs' / 'index.html').read_text(encoding='utf-8')
    n = len(re.findall(r'^const P = .*;\s*$', s, flags=re.M))
    if n != 1: raise SystemExit(f"docs/index.html has {n} 'const P' lines (expected 1) - rebuild with update.py")
    s = re.sub(r'^const P = .*;\s*$', lambda m_: 'const P = ' + json.dumps(P, separators=(',', ':')) + ';', s, count=1, flags=re.M)
    tmp = HERE / 'docs' / 'index.tmp'; tmp.write_text(s, encoding='utf-8'); tmp.replace(HERE / 'docs' / 'index.html')
    if logrows:
        new = pd.DataFrame(logrows)
        Lg = pd.concat([pd.read_csv(LOG), new], ignore_index=True).drop_duplicates(['run', 'day'], keep='last') if LOG.exists() else new
        t_ = LOG.with_suffix('.csv.tmp'); Lg.to_csv(t_, index=False); t_.replace(LOG)
    warn = '' if MEM is not None else '  WARNING: no StormVista ECMWF run from today or yesterday - wind risk uses past misses'
    say(f"risk engine: {len(out)} days · wind members {P['members_run']} · library +{added} new days · calibration reads {P['ncal']}{warn}")
    for d_, r in out.items():
        pw = '-' if r['pwin'] is None else f"{100 * r['pwin']:.0f}%"
        say(f"  {d_} d{r['lead']:<2} P10 {r['p10']:7.2f}  P50 {r['p50']:7.2f}  P90 {r['p90']:7.2f}  FV page {r['ev_page']:7.2f} -> corr {r['fv_corr']:7.2f}"
            f"  short wins {pw}  main risk {r['main']}")


if __name__ == '__main__':
    main()
