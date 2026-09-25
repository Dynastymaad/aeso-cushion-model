"""
likeday.py - which past days are most likely to PLAY OUT like today.

Not "which days had similar-looking weather". That was the first design and it
matched days that looked alike while their load landed 370 MW apart. What a
trading desk needs is days whose evening actually behaved the same way.

    target            average load over HE17-21, the window where tightness and
                      price happen. Not daily average load, which is dominated by
                      overnight hours nobody trades.

    method            two stages.
                      1. a weather -> evening-peak model, fitted on history
                      2. match on that PREDICTION, plus a little raw weather
                         shape so the matched days still look recognisably alike

    distance          3.0 * |z(predicted evening peak) difference|
                    + 0.6 * weather shape distance over temp, wind, radiation

Measured on 250 held-out days, top-3 matches: the old equal-weight weather
distance landed 369 MW from today's evening peak. This lands 269 MW. The floor
for any weather-based method is about 214 MW - that is the error of the
weather -> peak model itself, and no matching scheme can beat its own inputs.

CLOUD IS NOT IN THE DISTANCE. Fitted against evening peak load it earns a
weight of zero: it has the weakest power to separate days of the four, and no
usable link to the evening. It is still shown on the card, because you want to
see it - it just no longer dilutes the match.

LOAD GROWTH IS REMOVED BEFORE ANYTHING IS COMPARED. Alberta's evening peak grows
about +320 MW a year. Without that correction a match from 2024 reads ~640 MW
light purely because it is two years old, which is what made the old cards look
so wrong. Every load figure on the card is restated in today's year.
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path

VARS = ['temp', 'wind', 'rad', 'cloud']     # displayed
MATCH_VARS = ['temp', 'wind', 'rad']        # used in the distance - cloud earns 0
PEAK_HOURS = range(16, 21)                  # HE17-21 as 0-based indices
MIN_HOURS = 23
EXCLUDE_RECENT = 7
NTOP = 3
W_PRED, W_SHAPE = 3.0, 0.6                  # the blend, tuned out of sample


def _daily_matrix(wx):
    """hourly frame -> {var: DataFrame(day x hour 0..23)}, complete days only."""
    w = wx.copy()
    w['t'] = pd.to_datetime(w['t'])
    w = w.dropna(subset=['t']).sort_values('t').drop_duplicates('t', keep='last')
    w['day'] = w.t.dt.normalize()
    w['h'] = w.t.dt.hour
    out, keep = {}, None
    for v in VARS:
        p = w.pivot_table(index='day', columns='h', values=v, aggfunc='mean')
        p = p.reindex(columns=range(24))
        ok = p.notna().sum(axis=1) >= MIN_HOURS
        keep = ok if keep is None else (keep & ok)
        out[v] = p
    for v in VARS:
        out[v] = out[v][keep].interpolate(axis=1, limit_direction='both')
    return out


def _calendar(idx):
    """trend + two annual harmonics + Saturday and Sunday, the frame everything
    is measured against."""
    doy = idx.dayofyear.values.astype(float)
    tt = (idx - idx[0]).days.values / 365.25
    dw = idx.dayofweek.values
    return np.column_stack([
        np.ones(len(idx)), tt,
        np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25),
        np.sin(4 * np.pi * doy / 365.25), np.cos(4 * np.pi * doy / 365.25),
        (dw == 5).astype(float), (dw == 6).astype(float)]), tt


def build(root, loads_by_day=None, say=print):
    """Returns the payload block, or None if the weather history is not there."""
    root = Path(root)
    wxp = root / 'cache' / 'wx_hourly.csv'
    if not wxp.exists():
        say("  like-day: cache/wx_hourly.csv is not there - run  python wx.py  first. Panel skipped.")
        return None
    wx = pd.read_csv(wxp)
    if [v for v in VARS if v not in wx.columns]:
        say("  like-day: wx_hourly.csv is missing a variable. Panel skipped.")
        return None

    M = _daily_matrix(wx)
    days = M['temp'].index
    n = len(days)
    if n < 120:
        say(f"  like-day: only {n} usable weather days. Panel skipped.")
        return None
    A = {v: M[v].values for v in VARS}

    # ---- load: hourly, then the evening block --------------------------------
    comp = pd.read_csv(root / 'cache' / 'composition.csv')
    comp['t'] = pd.to_datetime(comp['datetime_begin'])
    if 'lead_bucket' in comp:
        comp = comp[comp.lead_bucket == -1]
    comp = (comp.dropna(subset=['t', 'ail']).sort_values('t')
                .drop_duplicates('t', keep='last'))
    comp['day'] = comp.t.dt.normalize()
    comp['h'] = comp.t.dt.hour
    LD = comp.pivot_table(index='day', columns='h', values='ail', aggfunc='mean')
    LD = LD.reindex(index=days, columns=range(24))
    have = LD.notna().sum(axis=1).values >= MIN_HOURS
    LDv = LD.interpolate(axis=1, limit_direction='both').values

    # days with no settled load at all are all-NaN; compute quietly and let
    # `have` filter them out rather than emitting a warning per row
    with np.errstate(invalid='ignore'):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', RuntimeWarning)
            eve = np.nanmean(LDv[:, list(PEAK_HOURS)], axis=1)   # HE17-21 average
            pk = np.nanmax(LDv, axis=1)                          # single peak hour
    eve = np.nan_to_num(eve, nan=0.0); pk = np.nan_to_num(pk, nan=0.0)

    X, tt = _calendar(days)
    if have.sum() < 120:
        say(f"  like-day: only {int(have.sum())} days of settled load. Panel skipped.")
        return None
    beta, *_ = np.linalg.lstsq(X[have], eve[have], rcond=None)
    growth = float(beta[1])                                  # MW per year
    to_today = growth * (tt[-1] - tt)                        # restate in today's year
    eve_adj = eve + to_today
    pk_adj = pk + to_today
    normal_eve = X @ beta + growth * (tt[-1] - tt)           # normal, also in today's year

    # ---- stage 1: weather -> evening peak ------------------------------------
    T, Wd, C, Rd = A['temp'], A['wind'], A['cloud'], A['rad']
    Tm = T.mean(axis=1)
    feat = np.column_stack([
        Tm, Tm ** 2, np.maximum(15 - Tm, 0), np.maximum(Tm - 18, 0),
        T.min(axis=1), T.max(axis=1), T[:, list(PEAK_HOURS)].mean(axis=1),
        Wd.mean(axis=1), Wd[:, list(PEAK_HOURS)].mean(axis=1),
        C.mean(axis=1), Rd.sum(axis=1) / 1000.0])
    dw = days.dayofweek.values
    Xp = np.column_stack([np.ones(n), feat,
                          (dw == 5).astype(float), (dw == 6).astype(float)])
    bp, *_ = np.linalg.lstsq(Xp[have], eve_adj[have], rcond=None)
    pred = Xp @ bp
    fit_mae = float(np.abs(pred[have] - eve_adj[have]).mean())
    zp = (pred - pred.mean()) / (pred.std() or 1.0)

    # ---- stage 2: the distance ----------------------------------------------
    Zl, Zs = {}, {}
    for v in VARS:
        lev = A[v].mean(axis=1)
        shp = A[v] - lev[:, None]
        Zl[v] = (lev - lev.mean()) / (lev.std() or 1.0)
        Zs[v] = (shp - shp.mean()) / (shp.std() or 1.0)

    i = n - 1
    today = days[i]
    cutoff = today - pd.Timedelta(days=EXCLUDE_RECENT)
    cand = np.array([k for k, d in enumerate(days) if d < cutoff and have[k]])
    if len(cand) < 60:
        say(f"  like-day: only {len(cand)} candidate days. Panel skipped.")
        return None

    shape_d = {v: np.abs(Zl[v] - Zl[v][i])
                  + np.sqrt(((Zs[v] - Zs[v][i]) ** 2).mean(axis=1)) for v in VARS}
    wshape = sum(shape_d[v] for v in MATCH_VARS)
    pred_d = np.abs(zp - zp[i])
    score = W_PRED * pred_d + W_SHAPE * wshape
    top = cand[np.argsort(score[cand])[:NTOP]]

    def series(v, k): return [round(float(x), 1) for x in A[v][k]]
    def stats(k):
        return {'tmean': round(float(A['temp'][k].mean()), 1),
                'wmean': round(float(A['wind'][k].mean()), 1),
                'rtot': round(float(A['rad'][k].sum()) / 1000.0, 2),
                'cmean': round(float(A['cloud'][k].mean()))}

    # settled price, if the model has it
    PX = None
    pxp = root / 'model' / 'price_history.csv'
    if pxp.exists():
        try:
            p = pd.read_csv(pxp, index_col=0, parse_dates=True)
            col = [c for c in p.columns if 'price' in c.lower()][0]
            gg = p[col].groupby(p.index.normalize())
            PX = pd.DataFrame({'avg': gg.mean(), 'max': gg.max(),
                               'eve': p[col][p.index.hour.isin(list(PEAK_HOURS))]
                                       .groupby(p.index[p.index.hour.isin(list(PEAK_HOURS))].normalize()).mean(),
                               'n100': gg.apply(lambda x: int((x > 100).sum()))})
        except Exception as e:
            say(f"  like-day: price_history.csv unreadable ({e}); prices omitted.")

    # today's own load curve: the caller's vector carries AESO's forecast for
    # hours that have not happened yet
    t_load = None
    if loads_by_day:
        v = loads_by_day.get(str(today.date()))
        if v and len(v) == 24:
            t_load = [None if x is None or (isinstance(x, float) and np.isnan(x))
                      else round(float(x)) for x in v]
    if t_load is None and have[i]:
        t_load = [round(float(x)) for x in LDv[i]]

    t_eve = None
    if t_load:
        ev = [t_load[h] for h in PEAK_HOURS if t_load[h] is not None]
        if ev: t_eve = round(float(np.mean(ev)))
    t_norm = float(normal_eve[i])
    ts = stats(i)

    out = {
        'day': str(today.date()), 'dow': today.strftime('%a'),
        'target': 'HE17-21',
        'growth': round(growth),
        'fit_mae': round(fit_mae),
        'span': [str(days.min().date()), str(days.max().date())],
        'ndays': int(len(cand)), 'excluded': EXCLUDE_RECENT,
        'today': dict(ts, load=t_load,
                      temp=series('temp', i), wind=series('wind', i),
                      rad=series('rad', i), cloud=series('cloud', i),
                      eve=t_eve, norm=round(t_norm),
                      partial=bool(not have[i]),
                      vs=None if t_eve is None else round(t_eve - t_norm),
                      vspc=None if t_eve is None else round(100.0 * (t_eve - t_norm) / t_norm, 1)),
        'matches': []}

    for k in top:
        d = days[k]
        st = stats(k)
        m = {'d': str(d.date()), 'dow': d.strftime('%a'),
             'back': int((today - d).days),
             'score': round(float(score[k]), 3),
             'load': [round(float(x)) for x in LDv[k]],
             'temp': series('temp', k), 'wind': series('wind', k),
             'rad': series('rad', k), 'cloud': series('cloud', k),
             'dt': round(st['tmean'] - ts['tmean'], 1),
             'dw': round(st['wmean'] - ts['wmean'], 1),
             'dr': round(st['rtot'] - ts['rtot'], 2),
             'dc': int(st['cmean'] - ts['cmean']),
             # every load figure restated in today's year
             'eve': round(float(eve_adj[k])),
             'eve_raw': round(float(eve[k])),
             'pk': round(float(pk_adj[k])),
             'lift': round(float(to_today[k])),
             'norm': round(float(normal_eve[k])),
             'in_match': {v: round(float(shape_d[v][k]), 2) for v in MATCH_VARS}}
        m.update(st)
        m['vs'] = round(float(eve_adj[k] - normal_eve[k]))
        m['vspc'] = round(100.0 * (eve_adj[k] - normal_eve[k]) / normal_eve[k], 1)
        m['gap'] = None if t_eve is None else round(float(eve_adj[k]) - t_eve)
        if PX is not None and d in PX.index:
            r = PX.loc[d]
            m['px'] = {'avg': round(float(r['avg']), 2), 'max': round(float(r['max']), 2),
                       'eve': None if pd.isna(r.get('eve')) else round(float(r['eve']), 2),
                       'n100': int(r['n100'])}
        out['matches'].append(m)

    gaps = [abs(m['gap']) for m in out['matches'] if m['gap'] is not None]
    say(f"  like-day: {len(cand):,} candidates, best {out['matches'][0]['d']} "
        f"(score {out['matches'][0]['score']})"
        + (f", evening-peak gap {int(np.mean(gaps))} MW" if gaps else ""))
    return out


if __name__ == '__main__':
    r = build(Path(__file__).resolve().parent.parent)
    print(json.dumps(r, indent=2)[:3000] if r else 'no payload')
