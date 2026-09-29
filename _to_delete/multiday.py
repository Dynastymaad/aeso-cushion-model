"""
multiday.py — a day-level fair value for every delivery day 1 to 13 days out,
and the disagreement with the daily forward curve.

    python multiday.py                 refresh + today's table
    python multiday.py --backtest      walk-forward test of the whole thing
    python multiday.py --refresh-eps   rebuild cache/wind_ens_daily.csv only

WHY THIS EXISTS
---------------
The daily forward curve knows nothing about a specific day past about a week.
Measured on 565 delivery days, its correlation with the pool runs 0.58 at lead
1, 0.38 at 2, 0.13 at 7 and zero past that — beyond a week the daily strip is a
monthly shape wearing daily clothes.

The ECMWF wind ensemble still correlates 0.64 with Alberta wind at d7 and 0.35
at d13, and the vendor load forecasts are good to d13. So there is information
out there that the curve is not carrying. This file turns that information into
a day-level cushion, maps it to price, and prints the gap against the curve.

WHAT IT IS BUILT FROM — all of it dated before the run date
  wind     ECMWF 51-member ensemble, one archived run per day in
           'Grabbing Data/sv_eps'. Recalibrated per lead on prior days.
  load     the AESO (to d6) and Tesla (to d13) load-forecast vintages already
           in cache/load_fc.csv, blended and recalibrated per lead.
  other    thermal + biomass + solar availability: seasonal level plus a
           per-lead regression on the last 1 / 3 / 7 days' anomaly.
  imports  the same, plus the measured coupling to the wind anomaly.

Uncertainty is the spread of this model's OWN prior out-of-sample errors at
that lead, so the band widens where it has actually been wrong rather than
where its residuals happen to be small.

HOW ACCURATE IT IS, and where the limit is
  cushion MAE 464 MW at d1, 548 at d6, 704 at d13; skill against a seasonal
  climatology 25% at d1, 14% at d6, 0% by d12.
  The binding constraint is NOT weather. Thermal availability ('other') carries
  an error of 225 MW at d1 rising to 455 at d7 and it is the largest single
  term at every lead. AESO publishes forward availability per fuel group 15
  days out (aiesgencapacity-api) and the Postgres fundamentals table holds
  forward snapshots we have never pulled. Either one would cut this materially.
  That is the next thing to do, not another weather source.

HOW TO USE IT — read this before trading off it
  The edge is in CROSSING the curve, never in resting an order. Tested: a
  one-sided limit posted 5-20% away at lead 7 and worked until delivery fills
  40-60% of the time and loses $15-23/MWh. The curve only travels to your
  level when the week has genuinely repriced, and the pool reprices with it.
  Crossing when the disagreement clears $5 returned $11-14/MWh at d7 and
  $13-17 at d13 in a walk-forward test, positive in both halves of the sample.
"""
import argparse, glob, re, sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent
CACHE = ROOT/'cache'
_SV = [Path.home()/'OneDrive - Dynasty Power'/'Desktop'/'Grabbing Data'/'sv_eps',
       Path.home()/'Desktop'/'Grabbing Data'/'sv_eps',
       Path.home()/'Documents'/'Grabbing Data'/'sv_eps',
       ROOT.parent/'Grabbing Data'/'sv_eps', ROOT/'sv_eps']
SV_DIR = next((p for p in _SV if p.is_dir()), _SV[0])
LEADS = range(1, 14)
PROD = {'flat': 'XDT', 'peak': 'XDQ', 'off': 'XDP'}
RNG = np.random.default_rng(7)


def say(s=''): print(s, flush=True)


# ------------------------------------------------------------ eps reduction --
def refresh_eps(force=False):
    """51 members x ~340 hours per run -> per-member daily means. ~1s per 30 runs."""
    out = CACHE/'wind_ens_daily.csv'
    have = set()
    if out.exists() and not force:
        old = pd.read_csv(out, parse_dates=['run', 'date'])
        have = set(old.run.dt.strftime('%Y%m%d'))
    else:
        old = pd.DataFrame()
    fs = sorted(glob.glob(str(SV_DIR/'eps_*.csv')))
    if not fs:
        if len(old):
            say(f'ensemble folder not found ({SV_DIR}) — using the cached reduction only')
            return old
        sys.exit(f'no ensemble files under {SV_DIR}')
    todo = [f for f in fs if re.search(r'(\d{8})', Path(f).name).group(1) not in have]
    rows = []
    for f in todo:
        rd = pd.Timestamp(re.search(r'(\d{8})', Path(f).name).group(1))
        try: M = pd.read_csv(f).drop(columns=['member']).to_numpy(float)
        except Exception as e: say(f'  skip {Path(f).name}: {e}'); continue
        ts = (rd + pd.to_timedelta(np.arange(M.shape[1]), 'h')).tz_localize('UTC') \
             .tz_convert('America/Edmonton').tz_localize(None)
        day = pd.Series(ts).dt.normalize()
        for d in day.unique():
            m = (day == d).values
            if m.sum() < 20: continue
            v = M[:, m].mean(axis=1)
            rows.append([rd, pd.Timestamp(d), int((pd.Timestamp(d)-rd).days), len(v),
                         v.mean(), v.std(ddof=1), *np.percentile(v, [10, 50, 90])])
    new = pd.DataFrame(rows, columns=['run', 'date', 'lead', 'n', 'mean', 'sd',
                                      'p10', 'p50', 'p90'])
    W = pd.concat([old, new], ignore_index=True) if len(old) else new
    W = W.drop_duplicates(['run', 'date']).sort_values(['run', 'lead'])
    W.to_csv(out, index=False)
    say(f'wind ensemble: {len(todo)} new runs, {W.run.nunique()} total '
        f'({W.run.min():%Y-%m-%d} to {W.run.max():%Y-%m-%d})')
    return W


# ------------------------------------------------------------------ inputs ---
def daily_actuals():
    C = pd.read_csv(CACHE/'composition.csv', parse_dates=['datetime_begin'])
    C = C[C.lead_bucket == -1].copy(); C['date'] = C.datetime_begin.dt.normalize()
    A = C.groupby('date').agg(ail=('ail', 'mean'), wind=('wind', 'mean'),
        sc=('sc', 'mean'), cogen=('cogen', 'mean'), cc=('cc', 'mean'),
        gfs=('gfs', 'mean'), bio=('biomass_and_other', 'mean'), sol=('solar', 'mean'),
        imp_a=('net_imports_actual_scheduled', 'mean'))
    A['other'] = A.sc + A.cogen + A.cc + A.gfs + A.bio + A.sol
    A['cushion'] = A.other + A.wind - (A.ail - A.imp_a)
    ph = pd.read_csv(ROOT/'model'/'price_history.csv', index_col=0, parse_dates=True)
    ph.index.name = 'ts'; ph['date'] = ph.index.normalize(); ph['he'] = ph.index.hour+1
    A = A.join(pd.DataFrame({'flat': ph.groupby('date').price.mean(),
                             'peak': ph[ph.he.between(8, 23)].groupby('date').price.mean(),
                             'off': ph[~ph.he.between(8, 23)].groupby('date').price.mean()}))
    return A.dropna(subset=['cushion'])


def load_vintages(A):
    d = pd.read_csv(CACHE/'load_fc.csv')
    ts = [c for c in d.columns if c.lower() == 'timestamp'][0]
    d['iss'] = pd.to_datetime(d[ts], errors='coerce')
    d['tgt'] = pd.to_datetime(d.EffectiveDateTime, errors='coerce')
    d = d.dropna(subset=['iss', 'tgt', 'Load'])
    d['date'] = d.tgt.dt.normalize(); d['run'] = d.iss.dt.normalize()
    d['leadd'] = (d.date - d.run).dt.days
    g = (d.sort_values('iss').groupby(['DataSourceName', 'run', 'date'], as_index=False)
           .agg(load=('Load', 'mean'), n=('Load', 'size'), leadd=('leadd', 'first')))
    g = g[(g.n >= 20) & g.DataSourceName.isin(['AESO', 'Tesla'])]
    return g.merge(A[['ail']], left_on='date', right_index=True, how='left')


def curve_hist():
    f = pd.read_csv(CACHE/'fwd_aeso_daily.csv', parse_dates=['EffectiveDate', 'Strip'])
    f['lead'] = (f.Strip - f.EffectiveDate).dt.days
    return f


# ------------------------------------------------------------------- model ---
def harm(idx, n=3):
    doy = np.asarray(idx.dayofyear); X = [np.ones(len(doy))]
    for k in range(1, n+1):
        X += [np.cos(2*np.pi*k*doy/365.25), np.sin(2*np.pi*k*doy/365.25)]
    return np.column_stack(X)


def lstsq(X, y): return np.linalg.lstsq(X, y, rcond=None)[0]


class State:
    def __init__(self, A, upto):
        H = A[A.index < upto]; self.ok = len(H) >= 250
        if not self.ok: return
        self.t0 = A.index[0]
        X = self.des(H.index)
        self.b = {c: lstsq(X, H[c].values) for c in ('other', 'imp_a', 'wind')}
        self.res = {c: pd.Series(H[c].values - X@self.b[c], index=H.index)
                    for c in ('other', 'imp_a', 'wind')}
        self.coef = {}
        for c in ('other', 'imp_a'):
            r = self.res[c]
            a1, a3, a7 = r.shift(1), r.rolling(3).mean().shift(1), r.rolling(7).mean().shift(1)
            for L in LEADS:
                Z = pd.concat([a1.shift(L), a3.shift(L), a7.shift(L), r], axis=1).dropna()
                if len(Z) < 120: self.coef[(c, L)] = np.zeros(4); continue
                M = np.column_stack([np.ones(len(Z))] + [Z.iloc[:, k] for k in (0, 1, 2)])
                self.coef[(c, L)] = lstsq(M, Z.iloc[:, 3].values)
        self.bwi = np.polyfit(self.res['wind'].values, self.res['imp_a'].values, 1)

    def des(self, idx):
        tt = ((idx - self.t0).days.values/365.25)[:, None]
        dw = np.column_stack([(idx.dayofweek == k).astype(float) for k in (5, 6)])
        return np.hstack([harm(idx), tt, dw])

    def base(self, c, date): return float(self.des(pd.DatetimeIndex([date]))[0] @ self.b[c])

    def point(self, c, date, L, a):
        k = self.coef.get((c, L), np.zeros(4))
        return self.base(c, date) + k[0] + k[1]*a[0] + k[2]*a[1] + k[3]*a[2]


def calib(df, x, y, upto, L, leadcol='lead', mn=60):
    g = df[(df[leadcol] == L) & (df.date < upto)].dropna(subset=[x, y])
    if len(g) < mn: return None
    b, a = np.polyfit(g[x].values, g[y].values, 1); return a, b


def knots(c, p, nb=12):
    pts = []
    for k in range(nb):
        lo, hi = np.percentile(c, k*100/nb), np.percentile(c, (k+1)*100/nb)
        m = (c >= lo) & (c <= hi)
        if m.sum() < 3: continue
        s = np.sort(p[m])
        if len(s) >= 5: s = np.concatenate([s[:-1], [s[-2]]])
        pts.append(((lo+hi)/2, s.mean()))
    pts = sorted(pts); return np.array([x[0] for x in pts]), np.array([x[1] for x in pts])


def pcurve(xs, ys, v):
    v = np.atleast_1d(np.asarray(v, float)); o = np.interp(v, xs, ys)
    lo, hi = v < xs[0], v > xs[-1]
    if lo.any(): o[lo] = ys[0] + (ys[1]-ys[0])/(xs[1]-xs[0])*(v[lo]-xs[0])
    if hi.any(): o[hi] = ys[-1] + (ys[-1]-ys[-2])/(xs[-1]-xs[-2])*(v[hi]-xs[-1])
    return np.clip(o, 1.0, 999.99)


def run_model(A, W, LV, runs):
    """point cushion forecast for every (run date, lead) in `runs`"""
    out = []; S = None; cm = None; wc = {}; lc = {}
    WJ = W.merge(A[['wind']], left_on='date', right_index=True, how='left')
    for R in runs:
        R = pd.Timestamp(R); mk = R.to_period('M')
        if mk != cm:
            cm = mk; S = State(A, R); wc = {}; lc = {}
            if not S.ok: continue
            for L in LEADS:
                wc[L] = calib(WJ, 'mean', 'wind', R, L)
                lc[L] = {s: calib(LV[LV.DataSourceName == s], 'load', 'ail', R, L, 'leadd')
                         for s in ('AESO', 'Tesla')}
        if S is None or not S.ok: continue
        an = {}
        for c in ('other', 'imp_a'):
            r = S.res[c][S.res[c].index < R]
            if len(r) < 8: an = None; break
            an[c] = (float(r.iloc[-1]), float(r.iloc[-3:].mean()), float(r.iloc[-7:].mean()))
        if an is None: continue
        for L in LEADS:
            D = R + pd.Timedelta(days=L)
            we = W[(W.run == R) & (W.lead == L)]
            if we.empty or wc.get(L) is None: continue
            a, b = wc[L]; w = a + b*float(we['mean'].iloc[0])
            pr = [c[0] + c[1]*float(v.load.iloc[0])
                  for s, c in (lc.get(L) or {}).items() if c is not None
                  for v in [LV[(LV.DataSourceName == s) & (LV.date == D) & (LV.leadd == L)]]
                  if len(v)]
            if not pr: continue
            ail = float(np.mean(pr))
            oth = S.point('other', D, L, an['other'])
            imp = S.point('imp_a', D, L, an['imp_a']) + np.polyval(S.bwi, w - S.base('wind', D))
            out.append(dict(run=R, date=D, lead=L, wind=round(w), ail=round(ail),
                            cush=oth + w - (ail - imp),
                            clim=S.base('other', D) + S.base('wind', D) + S.base('imp_a', D) - ail))
    return pd.DataFrame(out)


def add_price(F, A, ndraw=500):
    """band from this model's own prior errors; then cushion -> daily price"""
    F = F.merge(A[['cushion']], left_on='date', right_index=True, how='left')
    F['err'] = F.cush - F.cushion
    parts = []
    for L, g in F.groupby('lead'):
        g = g.sort_values('run').copy(); e = g.err.values
        sd = np.full(len(g), np.nan)
        for i in range(len(g)):
            j = i - L - 1
            if j < 60: continue
            past = e[:j][~np.isnan(e[:j])]
            if len(past) >= 60: sd[i] = past.std(ddof=1)
        g['sd'] = sd; parts.append(g)
    F = pd.concat(parts).sort_values(['run', 'lead'])
    F['sd'] = F.sd.fillna(F.groupby('lead').sd.transform('median')).fillna(700.)

    rows = []; cm = None; fit = None
    for R, g in F.groupby('run'):
        R = pd.Timestamp(R); mk = R.to_period('M')
        if mk != cm:
            cm = mk; H = A[A.index < R]
            fit = None
            if len(H) >= 250:
                fit = {}
                for b in PROD:
                    xs, ys = knots(H.cushion.values, H[b].values)
                    fit[b] = (xs, ys, np.log1p(H[b].values) -
                              np.log1p(pcurve(xs, ys, H.cushion.values)))
        if fit is None: continue
        for _, r in g.iterrows():
            draws = r.cush + RNG.normal(0, r.sd, ndraw)
            o = dict(r)
            for b in PROD:
                xs, ys, res = fit[b]
                px = np.clip(np.expm1(np.log1p(pcurve(xs, ys, draws)) +
                                      RNG.choice(res, ndraw)), 0, 999.99)
                o[f'ev_{b}'] = float(px.mean()); o[f'p50_{b}'] = float(np.median(px))
            rows.append(o)
    return pd.DataFrame(rows)


def level_correct(P, A):
    """walk-forward: scale the EV so its dollars match settled dollars. The EV of a
    right-skewed draw runs rich, and a rich EV is a permanent buy signal."""
    P = P.merge(A[list(PROD)].rename(columns={b: f'a_{b}' for b in PROD}),
                left_on='date', right_index=True, how='left')
    parts = []
    for L, g in P.groupby('lead'):
        g = g.sort_values('run').copy()
        for b in PROD:
            ev, ac = g[f'ev_{b}'].values, g[f'a_{b}'].values
            k = np.full(len(g), np.nan)
            for i in range(len(g)):
                j = i - L - 1
                if j < 60: continue
                m = ~np.isnan(ac[:j])
                if m.sum() >= 60: k[i] = ac[:j][m].sum()/max(ev[:j][m].sum(), 1e-9)
            g[f'curve_{b}'] = ev*np.where(np.isnan(k), 1.0, k)
        parts.append(g)
    return pd.concat(parts).sort_values(['run', 'lead'])


def attach_curve(P, f):
    FW = f.pivot_table(index=['Strip', 'lead'], columns='ExchangeCode', values='Price')
    idx = pd.MultiIndex.from_arrays([P.date, P.lead])
    for b, pr in PROD.items():
        if pr in FW.columns:
            P[f'fwd_{b}'] = FW[pr].reindex(idx).values
    return P


# --------------------------------------------------------- fuel & neighbour --
def extra_forwards():
    """AB-NIT daily gas (XBG) and Mid-C daily power (MPD) at every lead. Pulled
    out of the 600 MB fwd_daily.csv once and cached; the pull is a plain grep."""
    p = CACHE/'fwd_extra.csv'
    if not p.exists():
        big = CACHE/'fwd_daily.csv'
        if not big.exists(): say('  no fwd_daily.csv — gas and Mid-C left out'); return None
        say('  extracting gas and Mid-C from fwd_daily.csv (one-off)')
        rows = []
        for chunk in pd.read_csv(big, chunksize=500_000):
            rows.append(chunk[chunk.ExchangeCode.isin(['XBG', 'MPD'])])
        pd.concat(rows).to_csv(p, index=False)
    f = pd.read_csv(p, parse_dates=['EffectiveDate', 'Strip'])
    f['lead'] = (f.Strip - f.EffectiveDate).dt.days
    return f


def regress_price(P, A, f_extra, lam=3.0, minn=100):
    """The price step. Measured with perfect foresight, a plain cushion explains
    44% of daily log price; the same MW split into wind / load / thermal /
    imports explains 63%, because a megawatt of wind is worth about twice a
    megawatt of thermal availability at the margin. Add gas (+16% per $/GJ)
    and the Mid-C price and it is 64%. So the mapping is a ridge regression on
    the forecast components, fitted per lead on prior settled days only — which
    also means each lead learns its own shrinkage, instead of one curve being
    applied to a day-13 cushion as if it were a day-1 cushion.

    Walk-forward: MAE $3-7/MWh below the cushion curve at every lead, both
    blocks; de-biased edge against the curve up at d2, d3, d7, d10, d13."""
    P = P.copy()
    P['oi'] = P.cush - P.wind + P.ail
    P['tight'] = np.clip(1500 - P.cush, 0, None)/1000
    P['wk'] = (P.date.dt.dayofweek >= 5).astype(float)
    doy = P.date.dt.dayofyear
    P['s1'] = np.sin(2*np.pi*doy/365.25); P['c1'] = np.cos(2*np.pi*doy/365.25)
    cols = ['wind', 'ail', 'oi', 'tight', 'wk', 's1', 'c1']
    # AESO's 90-day outage plan, as it stood on the run date, for the delivery day.
    # Carried as a feature so the regression decides how much of it to believe at
    # each lead - the plan runs light and slips - but only once outages.py has
    # built enough history for that decision to be a fitted one.
    oh = CACHE/'outage_history.csv'
    if oh.exists():
        O = pd.read_csv(oh, parse_dates=['rep', 'date'])
        O = O[O.fuel.isin(['SC', 'Cogen', 'CC', 'GFS'])].groupby(['rep', 'date']).mw.sum()
        if O.index.get_level_values('rep').nunique() >= 120:
            P['rep_out'] = O.reindex(pd.MultiIndex.from_arrays([P.run, P.date])).values
            cols += ['rep_out']
            say('  outage plan in the regression (history is deep enough)')
        else:
            say(f"  outage plan not yet used: {O.index.get_level_values('rep').nunique()} reports on file, need 120")
    if f_extra is not None:
        idx = pd.MultiIndex.from_arrays([P.date, P.lead])
        for code, name in (('XBG', 'gas_f'), ('MPD', 'midc_f')):
            s = f_extra[f_extra.ExchangeCode == code].pivot_table(
                    index=['Strip', 'lead'], values='Price', aggfunc='first')['Price']
            P[name] = s.reindex(idx).values
        # a missing gas/Mid-C print on a weekend day: carry the last one for that delivery
        for name in ('gas_f', 'midc_f'):
            P[name] = P.groupby('date')[name].transform(lambda s: s.bfill().ffill())
        P['lgas'] = P.gas_f; P['lmidc'] = np.log1p(P.midc_f.clip(0))
        cols += ['lgas', 'lmidc']
    for b in PROD:
        P[f'fair_{b}'] = np.nan
    parts = []
    for L, g in P.groupby('lead'):
        g = g.sort_values('run').reset_index(drop=True)
        ok = g[cols].notna().all(axis=1)
        X = g[cols].values.astype(float)
        for b in PROD:
            y = np.log1p(g[f'a_{b}'].values); out = np.full(len(g), np.nan)
            for i in range(len(g)):
                if not ok[i]: continue
                j = i - L - 1
                if j < minn: continue
                m = ok.values[:j] & ~np.isnan(y[:j])
                if m.sum() < minn: continue
                Xt = X[:j][m]; mu = Xt.mean(0); sd = Xt.std(0) + 1e-9
                Z = np.column_stack([np.ones(m.sum()), (Xt - mu)/sd])
                R = lam*np.eye(Z.shape[1]); R[0, 0] = 0
                beta = np.linalg.solve(Z.T@Z + R, Z.T@y[:j][m])
                out[i] = np.r_[1, (X[i] - mu)/sd] @ beta
            g[f'fair_{b}'] = np.expm1(out)
        parts.append(g)
    P = pd.concat(parts).sort_values(['run', 'lead']).reset_index(drop=True)
    # the regression predicts a typical day; the settled mean runs above it
    # because of the spike tail. Same walk-forward dollar correction as before.
    for L, g in P.groupby('lead'):
        for b in PROD:
            fv, ac = g[f'fair_{b}'].values, g[f'a_{b}'].values
            k = np.full(len(g), np.nan)
            for i in range(len(g)):
                j = i - L - 1
                if j < 60: continue
                m = ~np.isnan(ac[:j]) & ~np.isnan(fv[:j])
                if m.sum() >= 60: k[i] = ac[:j][m].sum()/max(fv[:j][m].sum(), 1e-9)
            P.loc[g.index, f'fair_{b}'] = fv*np.where(np.isnan(k), 1.0, k)
    # where the regression has no history yet, fall back to the curve mapping
    for b in PROD:
        P[f'fair_{b}'] = P[f'fair_{b}'].fillna(P[f'curve_{b}'])
        if f'fwd_{b}' in P: P[f'edge_{b}'] = P[f'fair_{b}'] - P[f'fwd_{b}']
    return P


# ---------------------------------------------------------------- backtest ---
def backtest(P, cost):
    HRS = {'flat': 24, 'peak': 16, 'off': 8}
    say('\ncushion forecast, against a seasonal climatology')
    say(f"  {'lead':>5}{'n':>6}{'bias':>8}{'MAE':>8}{'corr':>7}{'clim':>8}{'skill':>8}")
    for L in LEADS:
        g = P[(P.lead == L)].dropna(subset=['cushion'])
        if len(g) < 50: continue
        e = g.cush - g.cushion; ec = g.clim - g.cushion
        say(f"  {L:>5}{len(g):>6}{e.mean():>8.0f}{e.abs().mean():>8.0f}"
            f"{g.cush.corr(g.cushion):>7.3f}{ec.abs().mean():>8.0f}"
            f"{1-e.abs().mean()/ec.abs().mean():>8.1%}")

    say(f'\ncrossing the curve when the model disagrees, de-biased, ${cost:.0f}/MWh cost')
    say('  (de-biased = the sample-wide forward premium removed from both sides, so a')
    say('   standing short earns nothing and what is left is day-picking)')
    for b in ('flat', 'peak'):
        say(f"\n  --- {b} ---")
        say(f"  {'lead':>5}{'T':>5}{'n':>6}{'MAE curve':>10}{'MAE regr':>10}{'corr mdl':>10}{'corr fwd':>10}{'$/MWh':>9}"
            f"{'hit':>6}{'$/MW':>10}{'t':>7}{'1st':>8}{'2nd':>8}")
        for L in (1, 2, 3, 5, 7, 9, 11, 13):
            d = P[P.lead == L].dropna(subset=[f'fwd_{b}', f'fair_{b}', f'a_{b}'])
            d = d.sort_values('run').reset_index(drop=True)
            if len(d) < 60: continue
            cm_, cf_ = d[f'fair_{b}'].corr(d[f'a_{b}']), d[f'fwd_{b}'].corr(d[f'a_{b}'])
            mae_c = (d[f'curve_{b}'] - d[f'a_{b}']).abs().mean()
            mae_r = (d[f'fair_{b}'] - d[f'a_{b}']).abs().mean()
            s = d[f'edge_{b}'] - d[f'edge_{b}'].mean()
            o = d[f'a_{b}'] - d[f'fwd_{b}']; o = o - o.mean()
            for T in (0, 5):
                m = s.abs() > T
                if m.sum() < 30: continue
                p = (np.sign(s)*o - cost)[m]; half = len(d)//2
                say(f"  {L:>5}{T:>5}{int(m.sum()):>6}{mae_c:>10.2f}{mae_r:>10.2f}{cm_:>10.3f}{cf_:>10.3f}{p.mean():>9.2f}"
                    f"{(p>0).mean():>6.0%}{p.sum()*HRS[b]:>10,.0f}"
                    f"{p.mean()/(p.std(ddof=1)/np.sqrt(len(p))):>7.2f}"
                    f"{p[p.index<half].mean():>8.2f}{p[p.index>=half].mean():>8.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--backtest', action='store_true')
    ap.add_argument('--refresh-eps', action='store_true')
    ap.add_argument('--cost', type=float, default=1.0)
    a = ap.parse_args()
    W = refresh_eps()
    if a.refresh_eps: return
    W['run'] = pd.to_datetime(W.run); W['date'] = pd.to_datetime(W.date)
    A = daily_actuals(); LV = load_vintages(A); f = curve_hist()
    say(f'actuals {len(A)} days, {A.index.min():%Y-%m-%d} to {A.index.max():%Y-%m-%d}')

    runs = sorted(W.run.unique()) if a.backtest else sorted(W.run.unique())[-420:]
    F = run_model(A, W, LV, runs)
    P = attach_curve(level_correct(add_price(F, A), A), f)
    P = regress_price(P, A, extra_forwards())
    CACHE.mkdir(exist_ok=True)
    P.to_csv(CACHE/'multiday.csv', index=False)
    say(f'wrote cache/multiday.csv  ({len(P)} rows)')

    if a.backtest: backtest(P, a.cost); return
    last = P[P.run == P.run.max()]
    say(f"\nfrom the run of {P.run.max():%Y-%m-%d}\n")
    say(f"  {'delivery':<12}{'lead':>5}{'cushion':>9}{'vs norm':>9}{'fair 7x24':>11}"
        f"{'curve':>8}{'edge':>8}{'fair peak':>11}{'curve':>8}{'edge':>8}")
    for _, r in last.sort_values('lead').iterrows():
        say(f"  {r.date:%a %b-%d  }{int(r.lead):>5}{r.cush:>9.0f}{r.cush-r.clim:>9.0f}"
            f"{r.fair_flat:>11.2f}{r.get('fwd_flat', float('nan')):>8.2f}"
            f"{r.get('edge_flat', float('nan')):>8.2f}"
            f"{r.fair_peak:>11.2f}{r.get('fwd_peak', float('nan')):>8.2f}"
            f"{r.get('edge_peak', float('nan')):>8.2f}")
    say('\n  edge = fair minus the curve at that lead. Positive means the curve is cheap.')
    say('  Cross the curve when it clears about $5. Do not rest an order: tested at')
    say('  leads 5-13, resting fills 40-60% of the time and loses $15-23/MWh, because')
    say('  the curve only comes to you when the week has genuinely repriced.')


if __name__ == '__main__':
    main()
