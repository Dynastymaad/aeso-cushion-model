"""
scenarios.py — the weather fan: seven calibrated wind scenarios for every
delivery day 1-13 out, run through the price model, giving P(pool > curve).

    python scenarios.py               today's fan + probabilities -> cache/scenarios.csv
    python scenarios.py --backtest    walk-forward: calibration and P&L by lead

Runs after multiday.py (reads cache/multiday.csv and cache/wind_ens_daily.csv).

WHAT WAS FOUND BUILDING THIS — the part to read
  The ECMWF ensemble is as good as the wind forecast gets from here. Every
  post-processing tried — nonlinear level calibration, seasonal shifts, sub-fuel
  splits, rolling windows — leaves the point MAE where it was: ~285 MW at d1,
  ~560 at d7, ~700 at d13. That is the weather. Nobody in this market has a
  better number than that at a week out.
  What WAS wrong, and is fixed here:
    * a drifting bias. The ensemble ran +340 MW rich through winter 25/26 and
      the expanding-window fit lagged it. A 180-day rolling fit takes the bias
      from +82 to +33 MW at d1.
    * a thin low tail. Realised wind fell below the members' 10th percentile on
      24% of days at d1 (should be 10%). Low-wind surprise is the expensive
      case, so the fan here is built from the model's OWN residual quantiles by
      wind level, not from member spread: q5-q95 coverage 83-91%.
  What the fan buys you is not a better point forecast, it is an honest
  probability. Calibration is monotone (a P<35% call hits 14-27%; a P>65% call
  hits 36-69%) but compressed, so read it as a ranking, not a coin's odds.
  Gating trades on P>70/<30 raises $/MWh per trade two- to three-fold at the
  cost of two-thirds of the trades; total dollars do not rise. It is a SIZING
  input, not a new edge.
"""
import argparse, sys
from pathlib import Path
import numpy as np, pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parent; CACHE = ROOT/'cache'
Q = [0.05, 0.15, 0.30, 0.50, 0.70, 0.85, 0.95]; W = [0.1, 0.1, 0.2, 0.2, 0.2, 0.1, 0.1]
ROLL, MINN, LAM = 180, 100, 3.0
PROD = {'flat': 'a_flat', 'peak': 'a_peak'}


def say(s=''): print(s, flush=True)


def wind_fan(WE, A):
    """per lead: rolling linear recalibration + residual quantiles by wind level"""
    WE = WE.merge(A[['wind']].rename(columns={'wind': 'wind_a'}), left_on='date', right_index=True, how='left')
    edges = np.array([0, 1000, 1750, 2500, 9999]); parts = []
    for L, g in WE.groupby('lead'):
        g = g.sort_values('run').reset_index(drop=True); rows = []
        for i in range(len(g)):
            j = i - L - 1
            h = g.iloc[max(0, j-ROLL):j].dropna(subset=['wind_a']) if j > 0 else g.iloc[0:0]
            if len(h) < MINN: rows.append([np.nan]*(1+len(Q))); continue
            sl, ic = np.polyfit(h['mean'], h.wind_a, 1); pt = ic + sl*g['mean'].iloc[i]
            hh = g.iloc[:j].dropna(subset=['wind_a']); r = hh.wind_a - (ic + sl*hh['mean'])
            b = np.digitize(hh['mean'], edges); bi = np.digitize(g['mean'].iloc[i], edges)
            rb = r[b == bi] if (b == bi).sum() >= 60 else r
            rows.append([pt] + list(np.clip(pt + np.quantile(rb, Q), 0, 5500)))
        O = pd.DataFrame(rows, columns=['wcal'] + [f'w{int(q*100)}' for q in Q], index=g.index)
        parts.append(pd.concat([g[['run', 'date', 'lead']], O], axis=1))
    return pd.concat(parts)


def run(P, L, b):
    """the price regression from multiday, re-run per scenario -> probability and EV"""
    yc = PROD[b]; fwd = f'fwd_{b}'
    need = ['wcal', 'ail', 'oi', 'wk', 's1', 'c1'] + (['lgas', 'lmidc'] if 'lgas' in P else [])
    g = P[P.lead == L].dropna(subset=need).sort_values('run').reset_index(drop=True)
    def X_of(wind):
        t = np.clip(1500 - (g.oi + wind - g.ail), 0, None)/1000
        cols = [wind, g.ail, g.oi, t, g.wk] + ([g.lgas, g.lmidc] if 'lgas' in g else []) + [g.s1, g.c1]
        return np.column_stack(cols).astype(float)
    Xc = X_of(g.wcal.values); Y = np.log1p(g[yc].values); n = len(g)
    prob = np.full(n, np.nan); ev = np.full(n, np.nan); lo = np.full(n, np.nan); hi = np.full(n, np.nan)
    for i in range(n):
        j = i - L - 1
        m = ~np.isnan(Y[:j]) if j > 0 else np.array([], bool)
        if j < MINN or m.sum() < MINN: continue
        Xt = Xc[:j][m]; mu = Xt.mean(0); sd = Xt.std(0) + 1e-9; Z = np.column_stack([np.ones(m.sum()), (Xt-mu)/sd])
        R = LAM*np.eye(Z.shape[1]); R[0, 0] = 0; beta = np.linalg.solve(Z.T@Z + R, Z.T@Y[:j][m])
        rs = (Y[:j][m] - Z@beta).std()
        ps = []; evs = []; ys = []
        for q, w in zip(Q, W):
            yk = np.r_[1, (X_of(g[f'w{int(q*100)}'].values)[i] - mu)/sd] @ beta
            ys.append(np.expm1(yk)); evs.append(w*np.expm1(yk + rs**2/2))
            if not np.isnan(g[fwd].iloc[i]): ps.append(w*norm.cdf((yk - np.log1p(g[fwd].iloc[i]))/rs))
        ev[i] = sum(evs); lo[i] = ys[0]; hi[i] = ys[-1]; prob[i] = sum(ps) if ps else np.nan
    g[f'ev_{b}'] = ev; g[f'p_low_{b}'] = lo; g[f'p_high_{b}'] = hi; g[f'prob_{b}'] = prob
    return g[['run', 'date', 'lead', f'ev_{b}', f'p_low_{b}', f'p_high_{b}', f'prob_{b}']]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--backtest', action='store_true'); a = ap.parse_args()
    P = pd.read_csv(CACHE/'multiday.csv', parse_dates=['run', 'date'])
    if not (CACHE/'wind_ens_daily.csv').exists(): sys.exit('run multiday.py first')
    WE = pd.read_csv(CACHE/'wind_ens_daily.csv', parse_dates=['run', 'date'])
    import multiday as M
    A = M.daily_actuals()
    F = wind_fan(WE, A); P = P.merge(F, on=['run', 'date', 'lead'], how='left')
    P['oi'] = P.cush - P.wind + P.ail; P['wk'] = (P.date.dt.dayofweek >= 5).astype(float)
    doy = P.date.dt.dayofyear; P['s1'] = np.sin(2*np.pi*doy/365.25); P['c1'] = np.cos(2*np.pi*doy/365.25)
    if 'gas_f' in P: P['lgas'] = P.gas_f; P['lmidc'] = np.log1p(P.midc_f.clip(0))
    out = P[['run', 'date', 'lead', 'wcal'] + [f'w{int(q*100)}' for q in Q] + [c for c in P if c.startswith('fwd_')]].copy()
    for b in PROD:
        parts = [run(P, L, b) for L in range(1, 14)]
        out = out.merge(pd.concat(parts), on=['run', 'date', 'lead'], how='left')
    out.to_csv(CACHE/'scenarios.csv', index=False); say(f'wrote cache/scenarios.csv ({len(out)} rows)')
    last = out[out.run == out.run.max()].sort_values('lead')
    say(f"\nfan from the run of {out.run.max():%Y-%m-%d}   (wind MW: p5 / p50 / p95;  peak $: low / EV / high;  P = chance the peak settles above the curve)\n")
    say(f"  {'delivery':<12}{'lead':>5}{'wind p5':>9}{'p50':>7}{'p95':>7}{'peak low':>10}{'EV':>8}{'high':>8}{'curve':>8}{'P>curve':>9}")
    for _, r in last.iterrows():
        say(f"  {r.date:%a %b-%d  }{int(r.lead):>5}{r.w5:>9.0f}{r.w50:>7.0f}{r.w95:>7.0f}{r.p_low_peak:>10.0f}{r.ev_peak:>8.0f}{r.p_high_peak:>8.0f}"
            f"{r.get('fwd_peak', np.nan):>8.2f}{r.prob_peak:>9.0%}")
    if not a.backtest: return
    say('\nBACKTEST — de-biased (premium removed), $1 cost')
    for b in PROD:
        yc = PROD[b]; fwd = f'fwd_{b}'; h = 24 if b == 'flat' else 16
        g = out.merge(P[['run', 'date', 'lead', yc]], on=['run', 'date', 'lead']).dropna(subset=[f'prob_{b}', yc, fwd])
        say(f"\n  --- {b} ---\n  {'lead':>5}{'n':>5}{'sign $/MWh':>12}{'t':>6}{'P>70/<30 n':>12}{'$/MWh':>8}{'t':>6}   P bucket -> hit rate")
        for L in (2, 3, 5, 7, 10, 13):
            d = g[g.lead == L]
            if len(d) < 60: continue
            pr = d[f'prob_{b}'] - d[f'prob_{b}'].mean() + 0.5; o = d[yc] - d[fwd]; o = o - o.mean()
            p0 = np.sign(d[f'ev_{b}'] - d[fwd] - (d[f'ev_{b}'] - d[fwd]).mean())*o - 1
            m = (pr > .7) | (pr < .3); p1 = np.sign(pr[m] - .5)*o[m] - 1
            cal = (o > 0).groupby(pd.cut(pr, [0, .35, .5, .65, 1]), observed=True).mean()
            t = lambda p: p.mean()/(p.std(ddof=1)/np.sqrt(len(p))) if len(p) > 5 else np.nan
            say(f"  {L:>5}{len(d):>5}{p0.mean():>12.2f}{t(p0):>6.2f}{int(m.sum()):>12}{p1.mean() if len(p1) else 0:>8.2f}{t(p1):>6.2f}   "
                + ' '.join(f'{v:.0%}' for v in cal.values))


if __name__ == '__main__':
    main()
