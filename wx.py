"""
wx.py - hourly Alberta weather history, for the like-day finder.

Four variables, hourly, two years back:

    temperature_2m        deg C     what load responds to
    wind_speed_100m       km/h      hub height, not the 10 m screen value
    shortwave_radiation   W/m^2     what the solar fleet sees
    cloud_cover           %         total cloud

Three points, plain average. They are not a climate network, they are the
three places Alberta's power market actually lives:

    Calgary        51.05 N 114.07 W   load centre, south
    Edmonton       53.55 N 113.49 W   load centre, north, industrial
    Pincher Creek  49.49 N 113.94 W   the wind belt

Averaging them gives one province-wide reading per hour. That is the right
grain for a like-day search: we are asking "was the weather across Alberta
shaped like today", not "what did one airport record".

Source is Open-Meteo, which serves the ECMWF/ERA5 reanalysis free and without
a key. Two endpoints, because the archive runs about five days behind:

    archive-api.open-meteo.com   settled history
    api.open-meteo.com           the last few days and today

Writes cache/wx_hourly.csv. Safe to re-run: it keeps what is already there and
re-pulls only the last 10 days plus anything missing, so a normal run is a few
seconds and one API call per city.

    python wx.py            top up to today
    python wx.py --full     throw the cache away and rebuild 2 years
"""
import sys, json, time
from pathlib import Path
from datetime import date, timedelta
import pandas as pd
import urllib.request, urllib.parse

HERE = Path(__file__).resolve().parent
CACHE = HERE / 'cache'
OUT = CACHE / 'wx_hourly.csv'
YEARS = 2
OVERLAP = 10          # days of settled history to re-pull each run

SITES = [('calgary', 51.05, -114.07),
         ('edmonton', 53.55, -113.49),
         ('pincher', 49.49, -113.94)]

VARS = ['temperature_2m', 'wind_speed_100m', 'shortwave_radiation', 'cloud_cover']
COLS = {'temperature_2m': 'temp', 'wind_speed_100m': 'wind',
        'shortwave_radiation': 'rad', 'cloud_cover': 'cloud'}
TZ = 'America/Edmonton'


def say(m=''):
    print(m, flush=True)


def _ctx_relaxed():
    """Same TLS fallback update.py carries. This machine's OpenSSL refuses a
    handshake to some endpoints that every browser on it accepts; the relaxed
    context loosens the cipher floor and re-allows legacy renegotiation.
    Certificate verification is NOT disabled - the default context still
    validates, this is only tried second if the strict one fails."""
    import ssl
    c = ssl.create_default_context()
    try:
        c.set_ciphers('DEFAULT@SECLEVEL=1')
        c.options |= getattr(ssl, 'OP_LEGACY_SERVER_CONNECT', 0x4)
    except Exception:
        pass
    return c


def _get(url, tries=3):
    req = urllib.request.Request(url)
    req.add_header('accept', 'application/json')
    req.add_header('User-Agent', 'Mozilla/5.0')
    last = None
    for i in range(tries):
        for ctx in (None, _ctx_relaxed()):
            try:
                with urllib.request.urlopen(req, timeout=90, context=ctx) as r:
                    return json.loads(r.read().decode())
            except Exception as e:
                last = e
        if i < tries - 1:
            say(f"    retry {i+1}/{tries-1} after {type(last).__name__}: {last}")
            time.sleep(3 * (i + 1))
    raise SystemExit(
        f"\n  Could not reach {url.split('?')[0]}\n"
        f"  Last error: {type(last).__name__}: {last}\n\n"
        "  Open-Meteo is a public feed with no key. If a browser on this machine\n"
        "  can open that address and python cannot, it is a proxy setting - set\n"
        "  HTTPS_PROXY in the shell and run again. The rest of the model does not\n"
        "  need this file; only the like-day panel does.")


def _frame(js, tag):
    """One site's JSON -> tidy hourly frame. Raises with a readable message
    rather than a KeyError if the API answered with something else."""
    if 'hourly' not in js:
        raise SystemExit(f"\n  {tag}: the API did not return hourly data.\n"
                         f"  It said: {json.dumps(js)[:400]}")
    h = js['hourly']
    missing = [v for v in VARS if v not in h]
    if missing:
        raise SystemExit(f"\n  {tag}: these variables came back empty: {', '.join(missing)}")
    d = pd.DataFrame({'t': pd.to_datetime(h['time'])})
    for v in VARS:
        d[COLS[v]] = pd.to_numeric(pd.Series(h[v]), errors='coerce')
    return d.dropna(subset=['t'])


def pull_site(lat, lon, start, end, name):
    """Settled history from the archive, the recent tail from the forecast
    endpoint. The archive lags about five days; past_days covers the gap."""
    q = {'latitude': lat, 'longitude': lon, 'hourly': ','.join(VARS), 'timezone': TZ}
    parts = []

    arch_end = min(end, date.today() - timedelta(days=6))
    if arch_end >= start:
        u = ('https://archive-api.open-meteo.com/v1/archive?'
             + urllib.parse.urlencode(dict(q, start_date=start.isoformat(),
                                           end_date=arch_end.isoformat())))
        parts.append(_frame(_get(u), f'{name} archive'))

    # past_days maxes out at 92; we only ever need the gap plus a little slack
    back = min(92, max(1, (end - arch_end).days + 2)) if arch_end >= start \
        else min(92, max(1, (end - start).days + 2))
    u = ('https://api.open-meteo.com/v1/forecast?'
         + urllib.parse.urlencode(dict(q, past_days=back, forecast_days=2)))
    parts.append(_frame(_get(u), f'{name} recent'))

    d = pd.concat(parts, ignore_index=True)
    d = d.sort_values('t').drop_duplicates('t', keep='last')   # forecast wins the overlap
    return d[(d.t.dt.date >= start) & (d.t.dt.date <= end)]


def pull(start, end):
    """Average the three sites into one province-wide hourly reading."""
    frames = []
    for name, lat, lon in SITES:
        say(f"  {name:<10} {start} -> {end}")
        d = pull_site(lat, lon, start, end, name).set_index('t')
        frames.append(d[list(COLS.values())])
    wide = pd.concat(frames, axis=1, keys=[s[0] for s in SITES])
    out = pd.DataFrame(index=wide.index)
    for c in COLS.values():
        out[c] = wide.xs(c, axis=1, level=1).mean(axis=1)
    n = wide.xs('temp', axis=1, level=1).notna().sum(axis=1)
    out = out[n == len(SITES)]                      # only hours all three sites have
    return out.round(2).reset_index()


def main():
    full = '--full' in sys.argv
    CACHE.mkdir(exist_ok=True)
    today = date.today()
    want_start = today - timedelta(days=365 * YEARS + 5)

    old = None
    if OUT.exists() and not full:
        old = pd.read_csv(OUT, parse_dates=['t'])
        if len(old):
            have_from = old.t.dt.date.min()
            have_to = old.t.dt.date.max()
            say(f"  cache holds {have_from} -> {have_to}  ({len(old):,} hours)")
            start = min(want_start, have_from) if have_from > want_start \
                else have_to - timedelta(days=OVERLAP)
        else:
            old, start = None, want_start
    else:
        start = want_start

    if full:
        say("  --full: rebuilding the whole two years")

    say(f"\n  pulling {start} -> {today}")
    new = pull(start, today)
    say(f"  got {len(new):,} hours")

    if old is not None and len(old):
        d = pd.concat([old, new], ignore_index=True)
    else:
        d = new
    d = (d.dropna(subset=['t']).sort_values('t')
          .drop_duplicates('t', keep='last'))                  # new run wins
    d = d[d.t.dt.date >= want_start]

    tmp = OUT.with_suffix('.csv.tmp')
    d.to_csv(tmp, index=False)
    import os
    os.replace(tmp, OUT)

    days = d.t.dt.date.nunique()
    short = int((d.groupby(d.t.dt.date).size() < 23).sum())
    say(f"\n  wrote {OUT}")
    say(f"  {len(d):,} hours over {days:,} days, "
        f"{d.t.dt.date.min()} -> {d.t.dt.date.max()}")
    if short:
        say(f"  {short} day(s) hold fewer than 23 hours - the like-day search skips those.")


if __name__ == '__main__':
    main()
