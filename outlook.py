"""
outlook.py -- forward cushion outlook from the ECMWF ensembles.

Three ingredients, each used only as far out as it is actually skilful:
  temperature ensemble (100 members, 44 days)  -> drives load
  wind ensemble        ( 51 members, 15 days)  -> drives wind supply
  everything else (thermal, cogen, solar, imports) -> seasonal level + noise

Beyond each source's skill horizon it falls back to climatology, so the
distribution widens honestly instead of pretending to know.

Run from the repo folder:
    python outlook.py              # forecast from the latest runs
    python outlook.py --backtest   # walk-forward check against actuals
Writes: cache/outlook.csv
"""
import argparse, glob, re, sys
from pathlib import Path
import numpy as np, pandas as pd

HERE = Path(__file__).resolve().parent
CACHE = HERE / 'cache'
_SV_CANDIDATES = [
    Path.home() / 'OneDrive - Dynasty Power' / 'Desktop' / 'Grabbing Data' / 'sv_eps',
    Path.home() / 'Desktop' / 'Grabbing Data' / 'sv_eps',
    HERE / 'sv_eps',
]
SV_DIR = next((p for p in _SV_CANDIDATES if p.is_dir()), _SV_CANDIDATES[0])
AB = ['CYYC', 'CYEG', 'CYQF', 'CYMM']
HORIZON = 44
WIND_SKILL_DAYS = 9          # measured: +10% skill at d7-9, +1.6% at d10-13
WIND_BIAS = 165.0            # measured: ensemble over-forecasts wind by ~165 MW
# measured daily-mean error sd of the wind ensemble, by lead (MW)
WIND_ERR_SD = {0:377,1:411,2:430,3:478,4:538,5:612,6:647,7:716,8:760,9:774,
               10:807,11:830,12:844,13:857}
RNG = np.random.default_rng(11)
# tightness thresholds on (model p50 - climatological cushion), measured d3-14
TIGHT = {'STRONG': -551, 'WATCH': -290}      # MW below normal


# ---------------------------------------------------------------- helpers
def harmonics(doy, hour=None, nyear=3, nday=2):
    X = [np.ones(len(doy))]
    for k in range(1, nyear + 1):
        X += [np.cos(2 * np.pi * k * doy / 365.25), np.sin(2 * np.pi * k * doy / 365.25)]
    if hour is not None:
        for k in range(1, nday + 1):
            X += [np.cos(2 * np.pi * k * hour / 24), np.sin(2 * np.pi * k * hour / 24)]
    return np.column_stack(X)


def fit(X, y):
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    return b


# ---------------------------------------------------------------- load data
def load_actuals():
    C = pd.read_csv(CACHE / 'composition.csv', parse_dates=['datetime_begin'])
    C = C[C.lead_bucket == -1].copy()
    C['date'] = C.datetime_begin.dt.normalize()
    A = C.groupby('date').agg(
        ail=('ail', 'mean'), wind=('wind', 'mean'), sc=('sc', 'mean'),
        cogen=('cogen', 'mean'), cc=('cc', 'mean'), gfs=('gfs', 'mean'),
        bio=('biomass_and_other', 'mean'), sol=('solar', 'mean'),
        imp=('net_imports', 'mean'))
    A['other'] = A.sc + A.cogen + A.cc + A.gfs + A.bio + A.sol      # non-wind internal
    A['cushion'] = A.other + A.wind - (A.ail - A.imp)
    return A.dropna()


def load_temp():
    W = pd.read_csv(CACHE / 'wx_ens.csv', parse_dates=['run_date', 'date'])
    W = W[(W.variable == 'tmin2m') & (W.station.isin(AB))]
    ens = (W.groupby(['run_date', 'date', 'lead'])
             .agg(mean=('mean', 'mean'), sd=('sd', 'mean'),
                  p10=('p10', 'mean'), p90=('p90', 'mean')).reset_index())
    truth = (W[W.lead == 0].groupby('date')['mean'].mean().rename('truth'))
    return ens, truth


def load_wind_ens(run_date=None):
    """ensemble member paths -> daily means, for one run date (latest if None)"""
    fs = sorted(glob.glob(str(SV_DIR / 'eps_*.csv')))
    if not fs:
        fs = sorted(glob.glob(str(HERE / 'sv_eps' / 'eps_*.csv')))
    if not fs:
        return None
    if run_date is not None:
        want = f"eps_{pd.Timestamp(run_date):%Y%m%d}.csv"
        fs = [f for f in fs if f.endswith(want)]
        if not fs:
            return None
    f = fs[-1]
    rd = pd.Timestamp(re.search(r'(\d{8})', Path(f).name).group(1))
    M = pd.read_csv(f).drop(columns=['member']).to_numpy(float)          # 51 x hours
    ts = (rd + pd.to_timedelta(np.arange(M.shape[1]), 'h')).tz_localize('UTC') \
        .tz_convert('America/Edmonton').tz_localize(None)
    day = pd.Series(ts).dt.normalize()
    out = {}
    for d in day.unique():
        m = (day == d).values
        if m.sum() >= 20:                       # only complete-ish days
            out[pd.Timestamp(d)] = M[:, m].mean(axis=1) - WIND_BIAS
    return rd, out


# ---------------------------------------------------------------- fit models
def build_models(A, truth):
    A = A.join(truth.rename('t'), how='inner').dropna(subset=['t'])
    doy = A.index.dayofyear.values
    tt = (A.index - A.index[0]).days.values / 365.25

    # temperature climatology, so we can express anomalies
    Xt = np.column_stack([harmonics(doy), tt])
    bt = fit(Xt, A.t.values)
    A['tclim'] = Xt @ bt
    A['ta'] = A.t - A.tclim

    # LOAD: seasonal shape + separate winter / summer temperature slopes
    win = A.index.month.isin([11, 12, 1, 2, 3]).astype(float)
    sumr = A.index.month.isin([5, 6, 7, 8, 9]).astype(float)
    Xl = np.column_stack([harmonics(doy), tt, A.ta * win, A.ta * sumr,
                          A.ta * (1 - win - sumr)])
    bl = fit(Xl, A.ail.values)
    A['ail_hat'] = Xl @ bl
    ail_res = A.ail - A.ail_hat

    # WIND climatology (for days past the ensemble horizon)
    Xw = np.column_stack([harmonics(doy), tt])
    bw = fit(Xw, A.wind.values)
    wind_res = A.wind - Xw @ bw

    # OTHER (non-wind internal) and IMPORTS: seasonal level
    bo = fit(Xw, A.other.values); other_res = A.other - Xw @ bo
    bi = fit(Xw, A.imp.values);   imp_res = A.imp - Xw @ bi
    # imports absorb wind surprises (measured corr -0.71) - couple them
    bwi = np.polyfit(wind_res.values, imp_res.values, 1)
    imp_free = imp_res.values - np.polyval(bwi, wind_res.values)

    # how much of a recent anomaly survives L days out (fitted, not assumed)
    def phi(res):
        r = pd.Series(res, index=A.index); r7 = r.rolling(7).mean()
        out = {}
        for L in range(0, HORIZON + 1):
            x, y = r7.shift(L), r
            m = (~x.isna()) & (~y.isna())
            out[L] = float(np.clip(np.polyfit(x[m], y[m], 1)[0], 0, 1)) if m.sum() > 60 else 0.0
        return out
    phi_o, phi_i = phi(other_res.values), phi(imp_res.values)

    return dict(bt=bt, bl=bl, bw=bw, bo=bo, bi=bi, t0=A.index[0],
                phi_o=phi_o, phi_i=phi_i, bwi=bwi, imp_free=imp_free,
                other_ser=other_res, imp_ser=imp_res,
                ail_res=ail_res.values, wind_res=wind_res.values,
                other_res=other_res.values, imp_res=imp_res.values,
                ail_sd=ail_res.std(), fitted=A)


def _des(idx, M):
    doy = idx.dayofyear.values
    tt = (idx - M['t0']).days.values / 365.25
    return harmonics(doy), tt, doy


# ---------------------------------------------------------------- forecast
def outlook(M, ens, wind_ens, asof, horizon=HORIZON, ndraw=2000):
    E = ens[ens.run_date == asof]
    E = E[(E.lead >= 1) & (E.lead <= horizon)]
    if E.empty:
        return pd.DataFrame()
    idx = pd.DatetimeIndex(E.date)
    H, tt, doy = _des(idx, M)
    tclim = np.column_stack([H, tt]) @ M['bt']
    ta_mean = E['mean'].values - tclim
    ta_sd = E.sd.values                            # ensemble spread = uncertainty

    win = idx.month.isin([11, 12, 1, 2, 3]).astype(float)
    sumr = idx.month.isin([5, 6, 7, 8, 9]).astype(float)
    shoulder = 1 - win - sumr

    wind_rd, wind_days = (wind_ens if wind_ens else (None, {}))
    # recent anomaly AS OF the run date - never uses anything after it
    past_o = M['other_ser'][M['other_ser'].index < asof]
    past_i = M['imp_ser'][M['imp_ser'].index < asof]
    rec_o = float(past_o.iloc[-7:].mean()) if len(past_o) >= 7 else 0.0
    rec_i = float(past_i.iloc[-7:].mean()) if len(past_i) >= 7 else 0.0
    rows = []
    for i, d in enumerate(idx):
        # --- temperature draws: ensemble mean + ensemble spread
        ta = RNG.normal(ta_mean[i], max(ta_sd[i], 0.5), ndraw)
        Xl = np.column_stack([H[i:i + 1].repeat(ndraw, 0), np.full(ndraw, tt[i]),
                              ta * win[i], ta * sumr[i], ta * shoulder[i]])
        ail = Xl @ M['bl'] + RNG.choice(M['ail_res'], ndraw)

        # --- wind: ensemble members while skilful, else climatology
        lead = (d - asof).days
        if d in wind_days and lead <= WIND_SKILL_DAYS:
            mem = wind_days[d]
            tgt = WIND_ERR_SD.get(lead, 860)
            extra = np.sqrt(max(tgt ** 2 - mem.std(ddof=1) ** 2, 1.0))
            w = RNG.choice(mem, ndraw) + RNG.normal(0, extra, ndraw)
            wsrc = 'ens'
        else:
            wc = np.concatenate([H[i], [tt[i]]]) @ M['bw']
            w = wc + RNG.choice(M['wind_res'], ndraw)
            wsrc = 'clim'
        w = np.clip(w, 0, 5200)

        base = np.concatenate([H[i], [tt[i]]])
        po, pi = M['phi_o'].get(lead, 0.0), M['phi_i'].get(lead, 0.0)
        o_res = np.asarray(M['other_res']); i_res = np.asarray(M['imp_res'])
        oth = (base @ M['bo'] + po * rec_o
               + RNG.choice(o_res, ndraw) * np.sqrt(max(1 - po ** 2, 0.05)))
        wclim_i = np.concatenate([H[i], [tt[i]]]) @ M['bw']
        imp = (base @ M['bi'] + pi * rec_i
               + np.polyval(M['bwi'], w - wclim_i)
               + RNG.choice(M['imp_free'], ndraw) * np.sqrt(max(1 - pi ** 2, 0.05)))

        cush = oth + w - (ail - imp)
        q = np.percentile(cush, [10, 25, 50, 75, 90])
        rows.append(dict(date=d, lead=lead, wind_src=wsrc,
                         t_anom=round(float(ta_mean[i]), 1),
                         t_spread=round(float(ta_sd[i]), 1),
                         ail=round(float(ail.mean())), wind=round(float(w.mean())),
                         cush_mean=round(float(cush.mean())),
                         p10=round(q[0]), p25=round(q[1]), p50=round(q[2]),
                         p75=round(q[3]), p90=round(q[4]),
                         sd=round(float(cush.std()))))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- backtest
def backtest(M, ens, A, every=7):
    runs = sorted(ens.run_date.unique())
    runs = [r for r in runs if pd.Timestamp(r) >= A.index.min()][::every]
    out = []
    for r in runs:
        r = pd.Timestamp(r)
        we = load_wind_ens(r)
        f = outlook(M, ens, we, r, ndraw=600)
        if f.empty:
            continue
        f['run'] = r
        out.append(f)
    F = pd.concat(out, ignore_index=True).merge(
        A.cushion.rename('actual'), left_on='date', right_index=True, how='inner')
    H, tt, _ = _des(pd.DatetimeIndex(F.date), M)
    B = np.column_stack([H, tt]); Z = np.zeros((len(F), 3))
    F['clim'] = (B @ M['bo'] + B @ M['bw'] + B @ M['bi']
                 - np.column_stack([H, tt, Z]) @ M['bl'])
    F['e'] = F.p50 - F.actual
    F['ce'] = F.clim - F.actual
    F['lb'] = pd.cut(F.lead, [0, 2, 4, 7, 9, 14, 21, 30, 44],
                     labels=['d1-2', 'd3-4', 'd5-7', 'd8-9', 'd10-14',
                             'd15-21', 'd22-30', 'd31-44'])
    print(f'\n{"lead":>8} {"n":>6} {"MAE model":>10} {"MAE clim":>9} {"skill":>8} '
          f'{"bias":>7} {"in P10-P90":>11}')
    for k, g in F.groupby('lb', observed=True):
        mf, mc = g.e.abs().mean(), g.ce.abs().mean()
        cov = ((g.actual >= g.p10) & (g.actual <= g.p90)).mean()
        print(f'{str(k):>8} {len(g):>6} {mf:10.0f} {mc:9.0f} {1 - mf / mc:+8.1%} '
              f'{g.e.mean():+7.0f} {cov:11.1%}')
    print('\n  (in P10-P90 should be ~80% if the uncertainty band is honest)')
    print('\n  by wind source:')
    for k, g in F.groupby('wind_src'):
        print(f'    {k:>5} n={len(g):>5}  MAE model {g.e.abs().mean():5.0f}  '
              f'MAE clim {g.ce.abs().mean():5.0f}  skill {1 - g.e.abs().mean() / g.ce.abs().mean():+.1%}')
    return F


# ---------------------------------------------------------------- main
if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--backtest', action='store_true')
    a = ap.parse_args()

    A = load_actuals()
    ens, truth = load_temp()
    M = build_models(A, truth)
    print(f'fitted on {len(M["fitted"])} days, {M["fitted"].index.min():%Y-%m-%d} '
          f'-> {M["fitted"].index.max():%Y-%m-%d}')
    b = M['bl']
    print(f'  load sensitivity: winter {b[-3]:+.1f} MW/degF   summer {b[-2]:+.1f} MW/degF')

    if a.backtest:
        backtest(M, ens, A)
        sys.exit()

    asof = pd.Timestamp(ens.run_date.max())
    F = outlook(M, ens, load_wind_ens(), asof)
    F.to_csv(CACHE / 'outlook.csv', index=False)
    print(f'\noutlook from run {asof:%Y-%m-%d}  ->  cache/outlook.csv')
    # how tight is this vs normal for the time of year, and what is it trading at
    H, tt, _ = _des(pd.DatetimeIndex(F.date), M)
    B = np.column_stack([H, tt]); Z = np.zeros((len(F), 3))
    F['clim'] = (B @ M['bo'] + B @ M['bw'] + B @ M['bi']
                 - np.column_stack([H, tt, Z]) @ M['bl'])
    F['tight'] = (F.p50 - F.clim).round()
    F['flag'] = np.where(F.lead.between(3, 14) & (F.tight <= TIGHT['STRONG']), 'TIGHT',
                np.where(F.lead.between(3, 14) & (F.tight <= TIGHT['WATCH']), 'watch', ''))
    fwd = CACHE / 'fwd_aeso_daily.csv'
    if fwd.exists():
        Q = pd.read_csv(fwd, parse_dates=['EffectiveDate', 'Strip'])
        Q = Q[Q.ExchangeCode == 'XDT'].sort_values('EffectiveDate')
        Q = Q.groupby('Strip').Price.last().rename('fwd')
        F = F.join(Q, on='date')

    cols = ['date', 'lead', 'wind_src', 't_anom', 'ail', 'wind',
            'p10', 'p50', 'p90', 'tight', 'flag']
    if 'fwd' in F: cols.insert(-1, 'fwd')
    show = F[cols].copy()
    show['date'] = show.date.dt.strftime('%a %b-%d')
    print(show.head(21).to_string(index=False))
    print('\n  tight = model p50 minus normal cushion for that date (MW)')
    print('  TIGHT  (<= -551 MW, d3-14): historically settled $8.13 ABOVE the daily forward,')
    print('         avg settle $63 vs $41 - the days not to be short.')
    print('  watch  (<= -290 MW, d3-14): +$5.37 vs forward.')
    print('  no flag: the forward is rich; d3-14 days settled $4.64 BELOW it on average.')
    print('\n  wind_src ens = real wind forecast (skilful to ~d9); clim = seasonal average')
    print('  measured skill vs climatology: d1-2 +25%, d3-4 +21%, d5-7 +14%, '
          'd8-9 +6%, d10-14 +3%, past d14 none')
