"""
extend.py - carry the forward feeds out to 14 days.

The AESO wind and solar feeds stop at 216 hours (about day 8); gencap, the AESO
load forecast and published ATC run to day 14. Past the wind / solar feeds:

  wind   the 100 m wind-speed forecast (cache/wx_fcst.csv, written by wx_fcst.py)
         turned into MW with a capacity-factor-by-speed curve fitted on the last
         365 days of settled hours, then shrunk toward the 30-day normal for that
         hour by the measured skill at that lead (model/leadcal.json). With no
         weather forecast on file it is the 30-day normal.
  solar  the median of the last 14 days at that hour.
  load   AESO's 14-day forecast; any hour past it from the newest Tesla vintage,
         then Meteologica.

Hours filled this way carry ext = 1. Nothing here touches a settled hour.
"""
import json
from pathlib import Path
import numpy as np, pandas as pd


def _comp(root):
    p = root/'cache'/'composition.csv'
    if not p.exists(): return None
    c = pd.read_csv(p, parse_dates=['datetime_begin'])
    if 'lead_bucket' in c: c = c[c.lead_bucket == -1]
    c = c.set_index('datetime_begin').sort_index()
    return c[~c.index.duplicated(keep='last')]


def _latest_vintage(root, src):
    p = root/'cache'/'load_fc.csv'
    if not p.exists(): return pd.Series(dtype=float)
    d = pd.read_csv(p)
    d = d[d.DataSourceName == src]
    if not len(d): return pd.Series(dtype=float)
    ts = [c for c in d.columns if c.lower() == 'timestamp'][0]
    d['iss'] = pd.to_datetime(d[ts], errors='coerce'); d['tgt'] = pd.to_datetime(d.EffectiveDateTime, errors='coerce')
    d = d[d.iss == d.iss.max()].dropna(subset=['tgt'])
    s = d.set_index('tgt').Load.astype(float)
    if getattr(s.index, 'tz', None) is not None: s.index = s.index.tz_localize(None)
    return s[~s.index.duplicated(keep='last')].sort_index()


def extend(x, folder, say=print):
    root = Path(folder).resolve().parent
    cal = json.loads((root/'model'/'leadcal.json').read_text()) if (root/'model'/'leadcal.json').exists() else {}
    shrink = {int(k): float(v) for k, v in (cal.get('wind_beyond_feed', {}).get('shrink', {}) or {}).items()}
    x = x.copy(); x['ext'] = 0
    end = x[['cc', 'sc', 'cogen', 'gfs']].dropna().index.max()          # gencap reach
    if pd.isna(end): return x
    C = _comp(root)
    today = pd.Timestamp.now().normalize()
    # ---- load past the AESO forecast
    lo_end = x.ail_fc.dropna().index.max()
    if pd.notna(lo_end) and lo_end < end:
        idx = pd.date_range(lo_end + pd.Timedelta(hours=1), end, freq='h')
        fill = pd.Series(np.nan, index=idx)
        for src in ('Tesla', 'Meteologica'):
            v = _latest_vintage(root, src).reindex(idx); fill = fill.fillna(v)
        x = x.reindex(x.index.union(idx))
        x.loc[idx, 'ail_fc'] = fill.values
        x['load'] = x.ail_act.fillna(x.ail_fc)
        n = int(fill.notna().sum())
        if n: say(f"load extended {n} hours past the AESO forecast (Tesla / Meteologica)")
    if C is None: return x
    # ---- 30-day normals by hour ending
    rec = C[C.index > C.index.max() - pd.Timedelta(days=30)]
    wnorm = rec.groupby(rec.index.hour).wind.median()
    rec14 = C[C.index > C.index.max() - pd.Timedelta(days=14)]
    snorm = rec14.groupby(rec14.index.hour).solar.median()
    # ---- wind from the wind-speed forecast
    cfcurve = None; wf = None
    wfp = root/'cache'/'wx_fcst.csv'; whp = root/'cache'/'wx_hourly.csv'
    if wfp.exists() and whp.exists():
        try:
            wf = pd.read_csv(wfp, parse_dates=['t']).set_index('t').wind
            wh = pd.read_csv(whp, parse_dates=[0]); wh = wh.set_index(wh.columns[0]).wind
            h = C[C.index > C.index.max() - pd.Timedelta(days=365)][['wind']].copy()
            h['cap'] = C.wind.rolling(24*365, min_periods=24*30).max().reindex(h.index)
            h['ws'] = wh.reindex(h.index); h = h.dropna()
            h['bin'] = (h.ws // 4) * 4
            g = (h.wind / h.cap).groupby(h.bin); cfcurve = g.mean()[g.size() >= 30]
            capnow = float(C.wind.rolling(24*365, min_periods=24*30).max().iloc[-1])
        except Exception as e:
            say(f"wind-speed curve skipped ({e})"); cfcurve = None
    idx = x.index[(x.index <= end) & x.wind_ml.isna() & x.wind_a.isna() & (x.index > (x.wind_ml.dropna().index.max() if x.wind_ml.notna().any() else today))]
    nw = 0
    for t in idx:
        L = (t.normalize() - today).days
        base = float(wnorm.get(t.hour, np.nan))
        v = base
        if cfcurve is not None and wf is not None and t in wf.index and pd.notna(wf.get(t)):
            b = min(max((wf[t] // 4) * 4, cfcurve.index.min()), cfcurve.index.max())
            cf = cfcurve.get(b, np.nan)
            if np.isnan(cf): cf = cfcurve.iloc[int(np.argmin(np.abs(cfcurve.index - b)))]
            k = shrink.get(L, 0.1 if L > 9 else 1.0)
            v = base + k * (cf * capnow - base)
        x.loc[t, 'wind_ml'] = v; x.loc[t, 'ext'] = 1; nw += 1
    # ---- solar at the recent normal
    idx = x.index[(x.index <= end) & x.solar_ml.isna() & x.solar_a.isna() & (x.index > (x.solar_ml.dropna().index.max() if x.solar_ml.notna().any() else today))]
    for t in idx:
        x.loc[t, 'solar_ml'] = float(snorm.get(t.hour, 0.0)); x.loc[t, 'ext'] = 1
    x['wind'] = x.wind_a.fillna(x.wind_ml); x['solar'] = x.solar_a.fillna(x.solar_ml)
    if nw:
        src = 'the 100 m wind-speed forecast, shrunk to normal by lead' if cfcurve is not None and wf is not None else 'the 30-day normal (no weather forecast on file - run wx_fcst.py)'
        say(f"wind / solar extended {nw} hours past the 216-h feed to {end:%Y-%m-%d %H:00}: wind from {src}; solar at its 14-day normal")
    return x
