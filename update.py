#!/usr/bin/env python3
"""
update.py — one command, everything current.

    python update.py                 # pull SQL + AESO, rebuild, write the page
    python update.py --from-cache    # skip SQL, reuse cache/ (for testing)
    python update.py --skip-score    # reuse the last calibration (fast, ~20s)

Stages, each printed as it runs:
  1  CANPOWER   composition + wind/solar/load forecast vintages -> cache/
  2  AESO API   load, gencap, poolprice, intertie, wind/solar, CSD -> refresh/
  3  rebuild    actuals, day-ahead forecasts, recalibration, cushion series
  4  score      walk-forward backtest -> calibration maps (the slow stage)
  5  grid       285 cushion x time-of-day cells
  6  page       docs/index.html

Then: git add -A && git commit && git push.
"""
import argparse, json, sys, os, time, urllib.request, csv, io, zipfile, warnings
warnings.filterwarnings('ignore', message='pandas only supports SQLAlchemy')
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'model'))
from pipeline import day_ahead_series, attach_price          # noqa: E402
from core import (E, NB, THR, knots, curve, walk_forward_ref,
                  build_pools, Cal, hot_ratio_curve)          # noqa: E402

CACHE = ROOT/'cache'; REFRESH = ROOT/'refresh'; ARCHIVE = ROOT/'archive'
def say(s): print(f"  {s}", flush=True)
def stage(n, s): print(f"\n[{n}/6] {s}", flush=True)


# ============================================================ 1. CANPOWER ====
def _q(v):
    """Brace-quote an ODBC value for SQL Server. A password containing ';'
    would otherwise end the connection string early and the server would see a
    truncated password - which comes back as 'Login failed', not as a syntax
    error, so it is easy to misread as a wrong password. A literal '}' inside
    is doubled."""
    return '{' + str(v).replace('}', '}}') + '}'


def _qpg(v, field):
    """psqlODBC does NOT strip brace quoting - it passes {mabbasi} through as a
    literal username - so values go in raw. A ';' genuinely cannot be expressed
    this way, so say so plainly rather than failing as 'authentication failed'."""
    v = str(v)
    if ';' in v:
        raise SystemExit(
            f"The PostgreSQL {field} contains a ';', which cannot be passed through an\n"
            f"  ODBC connection string. Change the password, or add a DSN in the ODBC\n"
            f"  Data Source Administrator and set \"dsn\": \"<name>\" in db.json instead.")
    return v


def dialect(cfg):
    return str(cfg.get('dialect', 'mssql')).lower()


def conn_str(cfg):
    """Build the ODBC string. Kept separate because this is where setups differ.

    SQL Server: a non-default port goes inside SERVER as 'host,port'. Driver 18
    encrypts by default, so a self-signed certificate needs
    TrustServerCertificate - the box DBeaver calls 'Trust Server Certificate'.

    PostgreSQL: the port is its own PORT= key, and AWS RDS wants SSLmode."""
    if dialect(cfg) in ('postgres', 'postgresql', 'pg'):
        if cfg.get('dsn'):
            p = [f"DSN={cfg['dsn']}",
                 f"UID={_qpg(cfg.get('username',''),'username')}",
                 f"PWD={_qpg(cfg.get('password',''),'password')}"]
            return ';'.join(p) + ';'
        p = [f"DRIVER={{{cfg['driver']}}}",
             f"SERVER={cfg['server']}",
             f"PORT={cfg.get('port', 5432)}",
             f"DATABASE={cfg['database']}",
             f"UID={_qpg(cfg.get('username',''),'username')}",
             f"PWD={_qpg(cfg.get('password',''),'password')}"]
        p.append(f"SSLmode={cfg.get('sslmode','require')}")
        if cfg.get('odbc_extra'): p.append(str(cfg['odbc_extra']).strip(';'))
        return ';'.join(p) + ';'
    p = [f"DRIVER={{{cfg['driver']}}}",
         f"SERVER={cfg['server']}",
         f"DATABASE={cfg['database']}"]
    if cfg.get('trusted_connection'):
        p.append("Trusted_Connection=yes")
    else:
        p.append(f"UID={_q(cfg['username'])}")
        p.append(f"PWD={_q(cfg['password'])}")
    if cfg.get('trust_server_certificate', True):
        p.append("TrustServerCertificate=yes")
    if cfg.get('encrypt') is not None:
        p.append(f"Encrypt={'yes' if cfg['encrypt'] else 'no'}")
    if cfg.get('odbc_extra'):
        p.append(str(cfg['odbc_extra']).strip(';'))
    return ';'.join(p) + ';'


def comp_cfg(cfg):
    """The composition table may live on a different server entirely - here it
    is PostgreSQL while the forecasts are SQL Server. Falls back to the main
    connection when no separate block is configured."""
    c = cfg.get('composition_db')
    return dict(c) if c else cfg


def upper_cols(df):
    df = df.copy(); df.columns = [str(c).upper() for c in df.columns]; return df


def redact(cs):
    """Never print a password. Handles the brace-quoted form, so a password
    containing ';' is not half-revealed by a naive split."""
    import re as _re
    return _re.sub(r'(PWD=)(\{(?:[^}]|\}\})*\}|[^;]*)', r'\1********', cs)


def _connect(cfg, label):
    """Open one connection, probing the drivers actually installed."""
    import pyodbc
    drv = pyodbc.drivers()
    want = cfg.get('driver','')
    pg = dialect(cfg) in ('postgres','postgresql','pg')
    fam = [x for x in drv if ('PostgreSQL' in x) if pg] or \
          [x for x in drv if 'SQL Server' in x and not pg]
    cands = ([want] if want in drv else []) + [x for x in fam if x != want]
    if not cands:
        raise SystemExit(f"{label}: no {'PostgreSQL' if pg else 'SQL Server'} "
                         f"ODBC driver installed. Have: {', '.join(drv)}")
    if want not in drv:
        say(f"  note: '{want}' not installed; trying {', '.join(fam)}")
    errs=[]
    for d in cands:
        for cert in ((True,) if pg else (True, False)):
            c=dict(cfg); c['driver']=d
            if not pg: c['trust_server_certificate']=cert
            try:
                cn=pyodbc.connect(conn_str(c), timeout=30)
                return cn, d, cert
            except Exception as ex:
                errs.append((d,cert,str(ex).replace('\n',' ')[:170]))
    say("")
    for d,cert,e in errs:
        say(f"  FAILED driver='{d}'" + ("" if pg else f" trustcert={str(cert).lower()}"))
        say(f"         {e}")
    raise SystemExit(f"\n  {label}: could not connect - see the errors above.")


def test_db(cfg):
    """Ten seconds instead of three minutes. Checks both connections: the
    forecast tables and, if configured separately, the composition table."""
    try:
        import pyodbc
    except ImportError:
        raise SystemExit("pyodbc is not installed:  pip install pyodbc")
    say("ODBC drivers installed on this machine:")
    for x in pyodbc.drivers(): say(f"    {x}")

    # ---- 1. forecasts (WindForecast / SolarForecast / LoadForecast) ----
    say(""); say(f"[1] forecasts  {cfg.get('database')} on {cfg.get('server')}")
    cn, d, cert = _connect(cfg, 'forecasts')
    with cn:
        say(f"    connected with '{d}'")
        for t in ('WindForecast','SolarForecast','LoadForecast'):
            try:
                n=pd.read_sql(f"SELECT COUNT(*) AS n FROM {t} WHERE MarketName='AESO'", cn).iloc[0].n
                say(f"    {t:<14} {int(n):>10,} AESO rows")
            except Exception as ex:
                say(f"    {t:<14} NOT FOUND ({str(ex)[:70]})")

    # ---- 2. composition ----
    cc = comp_cfg(cfg); sep = cc is not cfg and cc != cfg
    pg = dialect(cc) in ('postgres','postgresql','pg')
    say(""); say(f"[2] composition  {cc.get('database')} on {cc.get('server')}"
                 + ("  (PostgreSQL)" if pg else ""))
    if not sep and not pg:
        say("    same connection as above")
    cn2, d2, _ = _connect(cc, 'composition')
    with cn2:
        say(f"    connected with '{d2}'")
        qf = 'pg_00_find_composition_table.sql' if pg else '00_find_composition_table.sql'
        hits = upper_cols(pd.read_sql((ROOT/'sql'/qf).read_text(), cn2))
        if hits.empty and not pg:
            say(f"    not in '{cc['database']}' - searching the whole instance...")
            try: hits = upper_cols(pd.read_sql((ROOT/'sql'/'01_find_across_databases.sql').read_text(), cn2))
            except Exception as ex: say(f"    cross-database search failed ({str(ex)[:110]})")
        if hits.empty:
            say("")
            say("    No supply-composition table found on this connection.")
            if not sep:
                say("    Add a 'composition_db' block to db.json pointing at the other")
                say("    server - see db.example.json. Or run from CSV exports:")
                say("      python update.py --from-cache")
            raise SystemExit("")
        say(""); say("    candidates (most matching columns first):")
        for _, r in hits.head(6).iterrows():
            dbn = f"[{r.DB_NAME}]." if 'DB_NAME' in hits.columns and pd.notna(r.get('DB_NAME')) else ""
            say(f"      {dbn}{r.TABLE_SCHEMA}.{r.TABLE_NAME}   ({r.MATCHING_COLUMNS} columns)")
        r = hits.iloc[0]
        tbl = f"{r.TABLE_SCHEMA}.{r.TABLE_NAME}"
        say("")
        if 'DB_NAME' in hits.columns and pd.notna(r.get('DB_NAME')) and r.DB_NAME != cc['database']:
            say(f'    Set  "database": "{r.DB_NAME}"  and  "composition_table": "{tbl}"')
        else:
            where = 'the composition_db block of db.json' if sep else 'db.json'
            say(f'    Put this in {where}:')
            say(f'        "composition_table": "{tbl}"')
        # prove the columns we need are really there
        try:
            cols = pd.read_sql(f"SELECT * FROM {tbl} WHERE 1=0", cn2).columns.str.lower().tolist()
            need = ['datetime_begin','ail','sc','cogen','cc','gfs','biomass_and_other',
                    'wind','solar','net_imports_actual_scheduled']
            need += ['render_time'] if pg else ['lead_bucket']
            miss = [c for c in need if c not in cols]
            say("")
            say(f"    column check: {len(need)-len(miss)}/{len(need)} required columns present"
                + (f"  MISSING {', '.join(miss)}" if miss else "  - all good"))
        except Exception as ex:
            say(f"    column check failed: {str(ex)[:90]}")


def pull_sql(cfg, days):
    """Forecast vintages from one connection, supply composition from another
    (they are on different servers here, and different engines)."""
    try:
        import pyodbc                                            # noqa: F401
    except ImportError:
        raise SystemExit(
            "pyodbc is not installed, so update.py cannot reach the databases.\n"
            "  Either:  pip install pyodbc\n"
            "  Or:      export the four queries in sql/ from DBeaver into cache/\n"
            "           as composition.csv, wind_fc.csv, solar_fc.csv, load_fc.csv\n"
            "           and run with --from-cache")
    CACHE.mkdir(exist_ok=True)
    out = {}

    # ---- forecasts -------------------------------------------------------
    cn, d, _ = _connect(cfg, 'forecasts')
    with cn:
        for name, f in (('wind_fc','20_wind_forecast.sql'),
                        ('solar_fc','30_solar_forecast.sql'),
                        ('load_fc','40_load_forecast.sql')):
            q = (ROOT/'sql'/f).read_text().replace('{DAYS}', str(days))
            t0 = time.time()
            df = pd.read_sql(q, cn)
            df.to_csv(CACHE/f'{name}.csv', index=False)
            say(f"{name:<13} {len(df):>9,} rows  ({time.time()-t0:.0f}s)")
            out[name] = df
        # Forward curve. Not required by the model - if the table is not there
        # or is named differently, say so and carry on rather than fail the run.
        try:
            q = (ROOT/'sql'/'50_forward_prices.sql').read_text().replace('{DAYS}','500')
            t0 = time.time()
            df = pd.read_sql(q, cn)
            df.to_csv(CACHE/'forward_px.csv', index=False)
            say(f"{'forward_px':<13} {len(df):>9,} rows  ({time.time()-t0:.0f}s)"
                f"  {df.EffectiveDate.min()} to {df.EffectiveDate.max()}"
                if len(df) else f"{'forward_px':<13} no rows returned")
            out['forward_px'] = df
        except Exception as e:
            say(f"forward_px    skipped - {str(e).splitlines()[0][:110]}")

    # ---- composition -----------------------------------------------------
    cc = comp_cfg(cfg)
    pg = dialect(cc) in ('postgres','postgresql','pg')
    cn2, d2, _ = _connect(cc, 'composition')
    with cn2:
        tbl = cc.get('composition_table') or cfg.get('composition_table','')
        if not tbl or 'REPLACE_ME' in tbl:
            qf = 'pg_00_find_composition_table.sql' if pg else '00_find_composition_table.sql'
            hits = upper_cols(pd.read_sql((ROOT/'sql'/qf).read_text(), cn2))
            if hits.empty:
                raise SystemExit("could not find the supply-composition table - "
                                 "run 'python update.py --test-db' to diagnose")
            tbl = f"{hits.iloc[0].TABLE_SCHEMA}.{hits.iloc[0].TABLE_NAME}"
            say(f"discovered composition table: {tbl}  (add it to db.json to skip this)")
        qf = 'pg_10_composition.sql' if pg else '10_composition.sql'
        q = (ROOT/'sql'/qf).read_text().replace('{TABLE}', tbl).replace('{DAYS}', str(days))
        t0 = time.time()
        df = pd.read_sql(q, cn2)
        df.to_csv(CACHE/'composition.csv', index=False)
        say(f"{'composition':<13} {len(df):>9,} rows  ({time.time()-t0:.0f}s)")
        out['composition'] = df
    return out


def load_cache():
    need = ['composition','wind_fc','solar_fc','load_fc']
    opt  = ['forward_px']
    miss = [n for n in need if not (CACHE/f'{n}.csv').exists()]
    if miss: raise SystemExit(f"cache/ is missing: {', '.join(m+'.csv' for m in miss)}")
    out = {}
    for n in need:
        out[n] = pd.read_csv(CACHE/f'{n}.csv')
        say(f"{n:<13} {len(out[n]):>9,} rows  (cached)")
    for n in opt:
        if (CACHE/f'{n}.csv').exists():
            out[n] = pd.read_csv(CACHE/f'{n}.csv')
            say(f"{n:<13} {len(out[n]):>9,} rows  (cached)")
    return out


def inspect_table(cfg, tbl):
    """Show a table's real shape. Used when the expected columns do not line up
    - the Postgres warehouse names things differently from the SQL Server one."""
    cn, d, _ = _connect(cfg, 'inspect')
    with cn:
        say(f"connected with '{d}'")
        cols = pd.read_sql(f"SELECT * FROM {tbl} WHERE 1=0", cn).columns.tolist()
        say(""); say(f"{len(cols)} columns:")
        for i in range(0, len(cols), 4):
            say("    " + "".join(f"{c:<32}" for c in cols[i:i+4]))
        say(""); say("3 most recent rows:")
        tcol = next((c for c in cols if c.lower() in
                     ('datetime_begin','begin_datetime','datetime','ts','timestamp')), cols[0])
        df = pd.read_sql(f"SELECT * FROM {tbl} ORDER BY {tcol} DESC LIMIT 3", cn)
        with pd.option_context('display.max_columns', None, 'display.width', 250):
            for line in df.to_string().splitlines(): say("    " + line)
        # anything that looks like a snapshot/vintage discriminator
        say("")
        for c in cols:
            lc = c.lower()
            if any(k in lc for k in ('lead','bucket','snapshot','vintage','horizon',
                                     'render','version','revision','is_')):
                try:
                    v = pd.read_sql(
                        f"SELECT {c} AS v, COUNT(*) AS n FROM {tbl} "
                        f"GROUP BY {c} ORDER BY n DESC LIMIT 8", cn)
                    say(f"{c}: " + ", ".join(f"{r.v}({int(r.n):,})" for _, r in v.iterrows()))
                except Exception as ex:
                    say(f"{c}: could not group ({str(ex)[:60]})")
        n = pd.read_sql(f"SELECT COUNT(*) AS n FROM {tbl}", cn).iloc[0].n
        say(""); say(f"{int(n):,} rows total")


# ============================================================= 2. AESO API ===
def fetch(url, key=None, timeout=180):
    """Try, in order, until one works:

      1. HTTPS
      2. HTTPS with the cipher security level relaxed and legacy renegotiation
         allowed - certificate verification still on
      3. plain HTTP, but ONLY for a public feed with no API key. ets.aeso.ca
         serves a TLS configuration OpenSSL 3.x refuses ('sslv3 alert handshake
         failure') while happily serving the same file over port 80, which is
         what Refresh-AESO.ps1 has always done.
      4. PowerShell's Invoke-WebRequest, which uses Windows SChannel instead of
         OpenSSL and does not have the problem.

    The API key is never sent over plain HTTP, and certificate verification is
    never disabled."""
    import ssl, subprocess, tempfile
    https = url.replace('http://', 'https://')
    plain = url.replace('https://', 'http://')

    def go(u, ctx=None):
        req = urllib.request.Request(u)
        req.add_header('accept', 'application/json')
        req.add_header('User-Agent', 'Mozilla/5.0')
        if key: req.add_header('API-Key', key)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.read()

    relaxed = ssl.create_default_context()
    try:
        relaxed.set_ciphers('DEFAULT@SECLEVEL=1')
        relaxed.options |= getattr(ssl, 'OP_LEGACY_SERVER_CONNECT', 0x4)
    except Exception:
        pass

    plan = [('https', https, None), ('https relaxed', https, relaxed)]
    if key is None:                       # public feed: port 80 is acceptable
        plan.append(('http', plain, None))
    first = None; notes = []
    for label, u, ctx in plan:
        try:
            body = go(u, ctx)
            if label != 'https': notes.append(label)
            return body, (notes[0] if notes else None)
        except Exception as ex:
            if first is None: first = ex
    if os.name == 'nt':
        tmp = Path(tempfile.gettempdir())/f"aeso_dl_{os.getpid()}.tmp"
        tmp.unlink(missing_ok=True)
        hdr = (" -Headers @{'API-Key'='" + key + "'}") if key else ""
        cmd = ("$ProgressPreference='SilentlyContinue'; "
               "[Net.ServicePointManager]::SecurityProtocol="
               "[Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls11; "
               f"Invoke-WebRequest -Uri '{url}' -OutFile '{tmp}' "
               f"-TimeoutSec {min(timeout,120)} -UseBasicParsing{hdr}")
        try:
            r = subprocess.run(['powershell', '-NoProfile', '-Command', cmd],
                               capture_output=True, text=True, timeout=timeout+30)
            if tmp.exists() and tmp.stat().st_size > 0:
                body = tmp.read_bytes(); tmp.unlink(missing_ok=True)
                return body, 'powershell'
            err = (r.stderr or r.stdout or '').strip().replace('\n', ' ')[:110]
            if err: raise RuntimeError(f"powershell: {err}")
        except Exception as ex:
            if first is None: first = ex
    raise first


def pull_aeso(key, outdir, days=760):
    outdir.mkdir(parents=True, exist_ok=True)
    today = pd.Timestamp.now(); f7 = today - pd.Timedelta(days=7)
    f15 = today + pd.Timedelta(days=15)
    base = 'https://apimgw.aeso.ca/public'
    feeds = [
        ('load',     f"{base}/actualforecast-api/v1/load/albertaInternalLoad"
                     f"?startDate={f7:%Y-%m-%d}&endDate={f15:%Y-%m-%d}", True),
        ('gencap',   f"{base}/aiesgencapacity-api/v1/AIESGenCapacity"
                     f"?startDate={f7:%Y-%m-%d}&endDate={f15:%Y-%m-%d}", True),
        ('poolprice',f"{base}/poolprice-api/v1.1/price/poolPrice"
                     f"?startDate={f7:%Y-%m-%d}&endDate={today+pd.Timedelta(days=1):%Y-%m-%d}", True),
        ('intertie', f"{base}/itc/v1/interchange?startDate={f7:%Y%m%d}&endDate={f15:%Y%m%d}"
                     f"&startHE=1&endHE=24&version=false&dataType=ATC&dataType=TTC"
                     f"&intertieOrFlowgate=BC&intertieOrFlowgate=SK&intertieOrFlowgate=MATL", True),
        ('csd_summary', f"{base}/currentsupplydemand-api/v2/csd/summary/current", True),
        # The 122-day filed outage report. Public, no key. Each daily copy is a
        # VINTAGE: outages get filed progressively, so comparing today's view of
        # a future date against a later view of the same date is the only way to
        # measure the gas adder. Archived every day for exactly that reason.
        ('outage_90d', 'http://ets.aeso.ca/ets_web/ip/Market/Reports/'
                       'DailyOutageReportServlet?contentType=csv', False),
        ('wind_fc',  'http://ets.aeso.ca/Market/Reports/Manual/Operations/prodweb_reports/'
                     'wind_solar_forecast/wind_rpt_longterm.csv', False),
        ('solar_fc', 'http://ets.aeso.ca/Market/Reports/Manual/Operations/prodweb_reports/'
                     'wind_solar_forecast/solar_rpt_longterm.csv', False),
    ]
    # the settled-price history, in 365-day chunks - the API refuses a wider
    # single window. This is the backtest's dependent variable, so it is pulled
    # in full every run rather than accumulated, and nothing here can drift.
    start = (today - pd.Timedelta(days=days)).normalize(); i = 0
    while start < today:
        stop = min(start + pd.Timedelta(days=364), today + pd.Timedelta(days=1))
        feeds.append((f'poolprice_h{i}',
                      f"{base}/poolprice-api/v1.1/price/poolPrice"
                      f"?startDate={start:%Y-%m-%d}&endDate={stop:%Y-%m-%d}", True))
        start = stop + pd.Timedelta(days=1); i += 1
    for name, url, needkey in feeds:
        ext = 'csv' if (url.endswith('.csv') or 'contentType=csv' in url) else 'json'
        dest = outdir/f'{name}.{ext}'
        try:
            body, via = fetch(url, key if needkey else None)
            dest.write_bytes(body)
            say(f"{name:<13} {len(body)/1024:>9,.0f} KB" + (f"   (via {via})" if via else ""))
        except Exception as ex:
            if dest.exists():
                age = (time.time()-dest.stat().st_mtime)/3600
                say(f"{name:<13} FAILED ({str(ex)[:60]}) - keeping the copy "
                    f"from {age:.0f}h ago")
            else:
                say(f"{name:<13} FAILED: {str(ex)[:110]}")
    # keep one pull per day forever: each is a day-ahead vintage the API
    # itself cannot reproduce later
    ARCHIVE.mkdir(exist_ok=True)
    z = ARCHIVE/f"{today:%Y-%m-%d}.zip"
    if not z.exists():
        with zipfile.ZipFile(z,'w',zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(outdir.iterdir()): zf.write(p, p.name)
        say(f"archived {z.name}")


# ============================================================== 3. rebuild ===
def read_pool(folder):
    """Every poolprice*.json in the folder, concatenated. The chunked history
    files and the 7-day recent file overlap; last one in wins, and they agree.

    The result is merged into model/price_history.csv, which is the safety net:
    if a chunk pull fails on a given day the history does not silently shorten,
    it just stops growing at the top."""
    parts = []
    for p in sorted(folder.glob('poolprice*.json')):
        try:
            rows = json.loads(p.read_text(encoding='utf-8-sig'))['return']['Pool Price Report']
        except Exception as ex:
            say(f"{p.name}: unreadable ({ex})"); continue
        parts.append(pd.Series({pd.Timestamp(r['begin_datetime_mpt']):
                                pd.to_numeric(r.get('pool_price'), errors='coerce')
                                for r in rows}))
    s = pd.concat(parts) if parts else pd.Series(dtype=float)
    s = s[~s.index.duplicated(keep='last')].dropna().sort_index()
    hp = ROOT/'model'/'price_history.csv'
    if hp.exists():
        old = pd.read_csv(hp, index_col=0, parse_dates=True).iloc[:, 0]
        n0 = len(s)
        s = pd.concat([old, s]); s = s[~s.index.duplicated(keep='last')].sort_index()
        if len(s) > n0: say(f"price_history.csv carried {len(s)-n0:,} hours the pull did not return")
    s.rename('price').to_csv(hp)
    say(f"settled price: {len(s):,} hours, {s.index.min():%Y-%m-%d} to {s.index.max():%Y-%m-%d}")
    return s


def main():
    global CACHE
    ap = argparse.ArgumentParser()
    ap.add_argument('--from-cache', action='store_true', help='skip SQL, reuse cache/')
    ap.add_argument('--skip-score', action='store_true',
                    help='reuse the calibration from the last full run (fast)')
    ap.add_argument('--refresh', default=None, help='use an existing AESO folder')
    ap.add_argument('--cache', default=None, help='use an alternate cache folder')
    ap.add_argument('--test-db', action='store_true',
                    help='test the CANPOWER connection and find the composition table, then stop')
    ap.add_argument('--inspect', default=None, metavar='TABLE',
                    help='dump a table\'s columns and a few rows, then stop')
    a = ap.parse_args()
    if a.cache: CACHE = Path(a.cache)

    cfgp = ROOT/'db.json'
    if not cfgp.exists() and not a.from_cache:
        raise SystemExit("db.json not found - copy db.example.json to db.json and fill it in")
    cfg = json.loads(cfgp.read_text()) if cfgp.exists() else {}
    days = int(cfg.get('history_days', 760))

    if a.test_db:
        print("\n[test] CANPOWER connection", flush=True)
        test_db(cfg); print("\nlooks good - now run:  .\\Update.ps1 -NoPush", flush=True); return

    if a.inspect:
        print(f"\n[inspect] {a.inspect}", flush=True)
        inspect_table(comp_cfg(cfg), a.inspect); return

    stage(1, 'CANPOWER — composition and forecast vintages')
    src = load_cache() if a.from_cache else pull_sql(cfg, days)

    stage(2, 'AESO API — forward fundamentals and settled price')
    folder = Path(a.refresh) if a.refresh else REFRESH
    if not a.refresh:
        keyf = ROOT/'aeso_key.txt'
        if not keyf.exists(): raise SystemExit('aeso_key.txt not found')
        key = keyf.read_text().split(':')[-1].split('=')[-1].strip()
        pull_aeso(key, folder, days)
    else:
        say(f"using {folder}")

    stage(3, 'rebuild — actuals, day-ahead forecasts, recalibration')
    t0=time.time()
    d, wind_cor, load_cor, comp = day_ahead_series(
        src['composition'], src['wind_fc'], src['solar_fc'], src['load_fc'])
    pool = read_pool(folder)
    d = attach_price(d, pool)
    d = d.dropna(subset=['cush'])
    say(f"{len(d):,} hours  {d.index.min():%Y-%m-%d} to {d.index.max():%Y-%m-%d}  ({time.time()-t0:.0f}s)")
    say(f"settled price attached for {int(d.price.notna().sum()):,} hours")

    # The composition table lags the AESO feeds by up to a day, and those last
    # hours are the freshest information the 7-day price curve has. Take their
    # cushion from the live feeds, exactly as the page does, so the curve is
    # never a day stale. They carry no cush_full, so they enter the curve and
    # the residual pool but never the backtest's test set - which is right:
    # there was no day-ahead forecast to score them against.
    fr, itb = pagemod().frame(ROOT, folder, comp, say)
    tail = fr[(fr.index > d.index.max()) & fr.price.notna()]
    if len(tail):
        ext = pd.DataFrame({'cush': tail.cush, 'price': tail.price, 'gas': tail.gas,
                            'wind_a': tail.wind, 'solar_a': tail.solar,
                            'load': tail['load'], 'ni': tail.it,
                            'he': tail.he, 'g': tail.g})
        d = pd.concat([d, ext]).sort_index()
        d = d[~d.index.duplicated(keep='last')]
        say(f"history extended by {len(ext)} settled hours the composition table "
            f"has not reached yet (to {d.index.max():%Y-%m-%d %H:00})")

    scored = d.dropna(subset=['price']).copy()
    scored['ref7'] = walk_forward_ref(scored)
    scored = scored.dropna(subset=['ref7'])
    scored['r7'] = np.log1p(scored.price) - np.log1p(scored.ref7)
    say(f"residuals computed for {len(scored):,} hours")

    stage(4, 'score — walk-forward backtest and calibration')
    calp = ROOT/'model'/'calibration.pkl'
    if a.skip_score and calp.exists():
        import pickle
        cal, hotcal, hotratio, gates = pickle.load(open(calp,'rb'))
        say(f"reused {calp.name} (--skip-score)")
        for t in THR:
            say(f"  >${t:<4} skill {gates[t]['skill']:>+6.1f}%   AUC {gates[t]['auc']:.3f}")
    else:
        if a.skip_score: say('no saved calibration yet - scoring in full')
        cal, hotcal, hotratio, gates = score(scored)
        import pickle
        pickle.dump((cal, hotcal, hotratio, gates), open(calp,'wb'))

    stage(5, 'grid — 285 cushion x time-of-day cells')
    grid, tr7, pdays = make_grid(scored, cal, hotcal, hotratio, gates)

    stage(6, 'page')
    write_page(ROOT, folder, d, scored, grid, tr7, comp, load_cor, gates, fr, itb, pdays)
    print("\ndone.  git add -A && git commit -m \"refresh\" && git push", flush=True)


# ================================================================ 4. score ===
def score(d, seed=53):
    """Walk-forward: every day scored only against days before it."""
    rng=np.random.default_rng(seed)
    d=d.copy(); d['day']=d.index.normalize()
    days=sorted(d.day.unique()); out=[]; cm=None; POOL=None; ERR=None
    dd=d.dropna(subset=['cush_full'])
    bins_all=np.clip(np.digitize(d.cush.values,E)-1,0,NB-1); d['b']=bins_all
    for i,dy in enumerate(days):
        if i<400: continue
        tr7=d[(d.day>=days[i-7])&(d.day<dy)]; te=dd[dd.day==dy]
        if len(tr7)<120 or len(te)==0: continue
        xs,ys=knots(tr7.cush.values,tr7.price.values)
        mk=pd.Timestamp(dy).to_period('M')
        if mk!=cm:
            # Every hour of history before today, not a rolling year. Measured:
            # the residual distribution in the TIGHT cells is statistically
            # indistinguishable between the first and second year of history
            # (KS p>0.01 in every tight cell), so old data is still valid
            # exactly where the sample is thinnest. It has drifted in the loose
            # cells above 2,100 MW - where there are thousands of hours either
            # way and nothing is at stake. Expanding beats a rolling 365 on
            # Brier at $50/$100/$300 and catches more spikes in its top 1%.
            cm=mk
            h=d[d.index<pd.Timestamp(dy)]
            hf=h.dropna(subset=['cush_full'])
            if len(h)<2000 or len(hf)<500: POOL=None; continue
            POOL=build_pools(h.b.values,h.g.values,h.r7.values)
            # same shrink the grid uses, fitted on prior days only, so the
            # calibration is learnt against the draws it will later be applied to
            SB1,SB0=np.polyfit(hf.cush_full.values,hf.cush.values,1)
            ERR=hf.cush.values-(SB0+SB1*hf.cush_full.values)
        if POOL is None: continue
        sm=(SB0+SB1*te.cush_full.values)[:,None]+rng.choice(ERR,size=(len(te),300),replace=True)
        bi=np.clip(np.digitize(sm,E)-1,0,NB-1)
        rf=curve(xs,ys,sm.ravel()).reshape(sm.shape)
        for k,(ts,row) in enumerate(te.iterrows()):
            g=int(row.g)
            res=np.array([rng.choice(POOL[(int(b),g)]) for b in bi[k]])
            px=np.clip(np.expm1(np.log1p(rf[k])+res),0,999.99)
            r={'ts':ts,'price':row.price,'cfc':row.cush_full,'g':g,'mean_px':float(px.mean())}
            for t in THR: r[f'p{t}']=float((px>t).mean())
            for q in (10,25,50,75,90,95): r[f'q{q}']=float(np.percentile(px,q))
            out.append(r)
    R=pd.DataFrame(out).set_index('ts'); R['m']=R.index.to_period('M')
    R['hot']=((R.cfc<900)&(R.g.isin([3,4]))).astype(int)
    R.to_pickle(ROOT/'model'/'_raw_scores.pkl')
    hotratio=hot_ratio_curve(d)
    HX=np.array([p[0] for p in hotratio]); HY=np.array([p[1] for p in hotratio])
    months=sorted(R.m.unique()); cols={}
    for t in THR:
        o=pd.Series(index=R.index,dtype=float)
        for i,m in enumerate(months):
            te=R[R.m==m]; tr=R[R.m<m]
            if i<5 or len(tr)<2500: o.loc[te.index]=te[f'p{t}']; continue
            gm=Cal(tr[f'p{t}'].values,(tr.price>t).astype(float).values)
            trh=tr[tr.hot==1]
            hm=Cal(trh[f'p{t}'].values,(trh.price>t).astype(float).values) \
               if (t in (300,700) and len(trh)>=120) else None
            v=te[f'p{t}'].values; res=gm(v)
            if hm is not None:
                mk2=te.hot.values==1
                if mk2.any(): res[mk2]=hm(v[mk2])
            o.loc[te.index]=res
        cols[t]=o.values
    for j,t in enumerate(THR):
        v=cols[t].copy()
        if t==300:
            hot=R.hot.values==1
            v[hot]=np.minimum(v[hot], np.interp(R.cfc.values[hot],HX,HY,left=HY[0],right=HY[-1])*cols[100][hot])
        cols[t]=v
    cols[100]=np.minimum(cols[100],cols[50]); cols[300]=np.minimum(cols[300],cols[100])
    cols[700]=np.minimum(cols[700],cols[300])
    for t in THR: R[f'c{t}']=cols[t]
    R=R[R.m>=months[5]]
    from sklearn.metrics import roc_auc_score
    gates={}
    for t in THR:
        y=(R.price>t).astype(float)
        gates[t]={'skill':round(100*(1-((R[f'c{t}']-y)**2).mean()/((y.mean()-y)**2).mean()),1),
                  'auc':round(float(roc_auc_score(y.astype(int),R[f'c{t}'])),3)}
        say(f"  >${t:<4} skill {gates[t]['skill']:>+6.1f}%   AUC {gates[t]['auc']:.3f}")
    say(f"  hourly MAE ${float((R.mean_px-R.price).abs().mean()):.2f}   "
        f"P90 coverage {100*float((R.price<=R.q90).mean()):.1f}%")
    say("  realised gain:loss by cushion band (target "+f"{RATIO:.0f}"+":1)")
    # the reliability table the page shows: when it said X, how often did it happen
    y100=(R.price>100).astype(float); rel=[]
    for lab,lo,hi in [('under 1%',0,.01),('1–2%',.01,.02),('2–5%',.02,.05),('5–10%',.05,.10),
                      ('10–20%',.10,.20),('20–35%',.20,.35),('above 35%',.35,1.01)]:
        m=(R.c100>=lo)&(R.c100<hi)
        if m.sum()<5: continue
        rel.append({'lab':lab,'n':int(m.sum()),'said':round(100*float(R.c100[m].mean()),1),
                    'was':round(100*float(y100[m].mean()),1)})
    gates['acc']={'n':int(len(R)),'start':str(R.index.min().date()),'end':str(R.index.max().date()),
                  'rel':rel,'mae':round(float((R.mean_px-R.price).abs().mean()),2),
                  'cov':{q:round(100*float((R.price<=R[f'q{q}']).mean()),1) for q in (10,25,50,75,90,95)}}
    # Clamped at zero. Solved freely it comes out NEGATIVE - with this much
    # right skew, paying the median already earns better than 3:1, so a true
    # 3:1 bid would sit ABOVE the most likely outcome. That is arithmetically
    # correct and useless as a bid, so the bid is held at the median or below
    # and simply earns more than 3:1 when the skew is steep.
    LAMB_RAW=solve_lam(R.q10,R.q50,R.q90,R.price,'bid')
    LAMB=max(0.0,LAMB_RAW)
    LAMO=solve_lam(R.q10,R.q50,R.q90,R.price,'offer')
    R['bid'],R['offer']=bid_offer_q(R.q10.values,R.q50.values,R.q90.values,LAMB,LAMO)
    R['opt']=np.maximum(R.q90-R.q50,1e-6)/np.maximum(R.q50-R.q10,1e-6)
    say(f"  bid/offer lambdas for {RATIO:.0f}:1 -> bid {LAMB:+.3f}"
        f"{' (clamped from '+format(LAMB_RAW,'+.3f')+')' if LAMB_RAW<0 else ''}"
        f"  offer {LAMO:+.3f}   (lam 0 = at the median)")

    # LEVEL CORRECTION. The EV runs a few percent rich, much more so in some
    # cushion bands. k is what settled over what was predicted, by band,
    # shrunk toward 1 so a thin band cannot swing it. It scales the grid's
    # PRICE outputs only - the probabilities keep their own isotonic
    # calibration, which is already good and was fitted on the unscaled draws.
    LB=[-1e9,400,800,1200,1800,2500,1e9]; lv=[]
    lnum=np.clip(np.digitize(R.cfc.values,np.array(LB))-1,0,len(LB)-2)
    for i in range(len(LB)-1):
        m=lnum==i; n=int(m.sum())
        if n<40: lv.append({'lo':LB[i],'hi':LB[i+1],'k':1.0,'n':n}); continue
        sub=R[m]; raw=float(sub.price.sum()/max(sub.mean_px.sum(),1e-9))
        w=n/(n+400.0)
        lv.append({'lo':LB[i],'hi':LB[i+1],'k':round(1.0+w*(raw-1.0),4),'n':n})
    # A whole day settles at the MEAN of its hours, and that average is far
    # less dispersed than any single hour - so the same 3:1 rule sits much
    # closer to fair value. Averaging the 24 hourly levels gives 6:1 on the
    # bid, which is money left on the table. Solve the daily pair directly.
    DD=R.groupby(R.index.normalize()).agg(ev=('mean_px','mean'),px=('price','mean'),
                                          n=('price','size'))
    DD=DD[DD.n>=23]
    def _dm(side,target=RATIO,lo=0.0,hi=0.99):
        for _ in range(70):
            m=(lo+hi)/2
            lvl=DD.ev*(1-m) if side=='bid' else DD.ev*(1+m)
            g=np.maximum(DD.px-lvl,0) if side=='bid' else np.maximum(lvl-DD.px,0)
            l=np.maximum(lvl-DD.px,0) if side=='bid' else np.maximum(DD.px-lvl,0)
            if float(g.mean()/max(l.mean(),1e-9))<target: lo=m
            else: hi=m
        return (lo+hi)/2
    if len(DD)>=60:
        db,do=1-_dm('bid'),1+_dm('offer')
    else:
        db,do=0.72,1.28
    gates['day']={'bid':round(float(db),3),'offer':round(float(do),3),'n':int(len(DD))}
    say(f"  daily levels: bid = day EV x {db:.3f}, offer x {do:.3f}  ({len(DD)} full days)")

    gates['lvl']=lv; gates['lam']={'bid':LAMB,'offer':LAMO}
    say("  level correction by cushion: "+"  ".join(f"{x['k']:.2f}" for x in lv))

    # How well has the 3:1 level actually held, by cushion band? Measured on
    # the same walk-forward hours, and shipped so the page can mark the bands
    # where the bid or the offer has not been delivering what it promises.
    BB=[-1e9,400,800,1200,1800,2500,1e9]
    BL=['under 400','400-800','800-1200','1200-1800','1800-2500','2500+']
    bo=[]
    bnum=np.clip(np.digitize(R.cfc.values,np.array(BB))-1,0,len(BL)-1)
    for i,lab in enumerate(BL):
        m=bnum==i
        if m.sum()<20: continue
        sub=R[m]; e={'lab':lab,'lo':BB[i],'hi':BB[i+1],'n':int(m.sum())}
        for side in ('bid','offer'):
            if side=='bid': g_=np.maximum(sub.price-sub[side],0); l_=np.maximum(sub[side]-sub.price,0)
            else:           g_=np.maximum(sub[side]-sub.price,0); l_=np.maximum(sub.price-sub[side],0)
            e[side]=round(float(g_.mean()/max(l_.mean(),1e-9)),2)
            e[side+'_net']=round(float((g_-l_).mean()),2)
            e[side+'_win']=round(float((g_>0).mean()),3)
        bo.append(e)
        say(f"  {lab:>11}  n={e['n']:>5}   bid {e['bid']:>5.2f}:1   offer {e['offer']:>5.2f}:1")
    gates['bo']={'target':RATIO,'bands':bo}

    hot=R[R.hot==1]
    CAL={t:Cal(R[f'p{t}'].values,(R.price>t).astype(float).values) for t in THR}
    HOT={t:Cal(hot[f'p{t}'].values,(hot.price>t).astype(float).values) for t in (300,700)}
    R.to_pickle(ROOT/'model'/'_scores.pkl')
    return CAL,HOT,hotratio,gates


# ================================================================= 5. grid ===
RATIO = 3.0          # expected gain : expected loss demanded before crossing


def bid_offer_q(q10, q50, q90, lam_b, lam_o):
    """Bid and offer anchored on the median, each side scaled by its own tail.

        down = P50 - P10        how far the downside runs
        up   = P90 - P50        how far the upside runs
        bid   = P50 - lam_b * down
        offer = P50 + lam_o * up

    No skew term is needed: up and down already carry it, so a lopsided hour
    widens on the lopsided side by itself. The two lambdas are not assumed -
    score() solves them each run so the realised gain:loss lands on RATIO."""
    down = np.maximum(q50 - q10, 1e-6); up = np.maximum(q90 - q50, 1e-6)
    return np.maximum(q50 - lam_b * down, 0.0), q50 + lam_o * up


def solve_lam(q10, q50, q90, price, side, target=RATIO, lo=-3.0, hi=4.0):
    """The lambda that would have delivered `target` on these hours."""
    q10, q50, q90, price = map(np.asarray, (q10, q50, q90, price))
    def ratio(m):
        lvl = (np.maximum(q50 - m*np.maximum(q50-q10,1e-6), 0.0) if side == 'bid'
               else q50 + m*np.maximum(q90-q50,1e-6))
        g = np.maximum(price-lvl, 0) if side == 'bid' else np.maximum(lvl-price, 0)
        l = np.maximum(lvl-price, 0) if side == 'bid' else np.maximum(price-lvl, 0)
        return float(g.mean()/max(l.mean(), 1e-9))
    for _ in range(80):
        m = (lo+hi)/2
        if ratio(m) < target: lo = m
        else: hi = m
    return round((lo+hi)/2, 4)


def bid_offer(px, r=RATIO):
    """Bid and offer, by the QB three-scenario weighting.

    The sheet this comes from works on three cases - Low, Base, High - with a
    probability each. The model carries a full distribution instead, so it is
    reduced to the same three-point shape first: the bottom quarter of the
    draws, the middle half, and the top quarter, each represented by its own
    average. Split by rank rather than by value so ties at $0 and at the
    $999.99 cap cannot distort the weights, and because the quarters are exact
    the three points reproduce the distribution's mean exactly.

    Then, verbatim:
        bid   = [pH*H + r*(pL*L + pB*B)] / [pH + r*(pL + pB)]
        offer = [pL*L + pB*B + r*pH*H] / [pL + pB + r*pH]

    The bid weights the downside cases r times over, the offer weights the
    upside case r times over, which is what pulls each away from fair value."""
    s = np.sort(np.asarray(px, float)); n = s.size
    if n < 8 or s[-1] - s[0] < 1e-9:
        v = round(float(s.mean()), 2); return v, v
    i, j = n // 4, (3 * n) // 4
    L, B, H = float(s[:i].mean()), float(s[i:j].mean()), float(s[j:].mean())
    pL, pB, pH = i / n, (j - i) / n, (n - j) / n
    bid   = (pH * H + r * (pL * L + pB * B)) / (pH + r * (pL + pB))
    offer = (pL * L + pB * B + r * pH * H) / (pL + pB + r * pH)
    return round(bid, 2), round(offer, 2)


def make_grid(d, CAL, HOT, hotratio, gates=None, seed=71):
    end=d.index.max()
    tr7=d[d.index>end-pd.Timedelta(days=7)]
    hist=d                      # every settled hour, not a rolling year
    # The day-ahead cushion regresses to the mean: forecast 300 and the actual
    # lands near 440; forecast 3,900 and it lands near 3,720. Smearing
    # symmetrically around the forecast therefore puts weight on cushions that
    # do not happen - too tight at the tight end, too loose at the loose end -
    # and because the price curve is forty times steeper when tight than when
    # loose, that error is worth $90/MWh there and -$5 at the other extreme.
    # So smear around E[actual | forecast] instead, using the spread that is
    # left once the drift is taken out.
    j=hist.dropna(subset=['cush_full','cush'])
    SB1,SB0=np.polyfit(j.cush_full.values,j.cush.values,1)
    err=j.cush.values-(SB0+SB1*j.cush_full.values)
    say(f"cushion shrink: actual = {SB0:+.0f} + {SB1:.3f} x forecast, "
        f"residual sd {err.std():.0f} MW")
    XS,YS=knots(tr7.cush.values,tr7.price.values)
    bins=np.clip(np.digitize(hist.cush.values,E)-1,0,NB-1)
    POOL=build_pools(bins,hist.g.values,hist.r7.values)
    HX=np.array([p[0] for p in hotratio]); HY=np.array([p[1] for p in hotratio])
    pdays=int((hist.index.max()-hist.index.min()).days)
    say(f"curve {tr7.index.min():%Y-%m-%d} to {tr7.index.max():%Y-%m-%d} "
        f"({len(tr7)} hrs, mean ${tr7.price.mean():.2f}); "
        f"pool {len(hist):,} hrs over {pdays} days (all history)")
    LV=(gates or {}).get('lvl') or []
    LAM=(gates or {}).get('lam') or {'bid':0.0,'offer':0.6}
    def klvl(c):
        for x in LV:
            if x['lo']<=c<x['hi']: return float(x['k'])
        return 1.0
    rng=np.random.default_rng(seed); rows=[]
    for g in range(5):
        for c in np.arange(-1200,4401,100):
            sm=(SB0+SB1*c)+rng.choice(err,size=4000)
            bi=np.clip(np.digitize(sm,E)-1,0,NB-1)
            base=curve(XS,YS,sm)
            res=np.array([rng.choice(POOL[(int(b),g)]) for b in bi])
            px=np.clip(np.expm1(np.log1p(base)+res),0,999.99)
            r={'g':g,'c':int(c)}
            # probabilities below come from the UNSCALED draws, which is the
            # basis their calibration was fitted on. Prices use the corrected
            # ones.
            pxl=px*klvl(c)
            for q in (10,25,50,75,90,95): r[f'q{q}']=round(float(np.percentile(pxl,q)),2)
            r['mean']=round(float(pxl.mean()),2)
            _b,_o=bid_offer_q(*[float(np.percentile(pxl,q)) for q in (10,50,90)],
                              LAM['bid'],LAM['offer'])
            r['bid'],r['offer']=round(float(_b),2),round(float(_o),2)
            _q10,_q50,_q90=[float(np.percentile(pxl,q)) for q in (10,50,90)]
            r['opt']=round(max(_q90-_q50,1e-6)/max(_q50-_q10,1e-6),2)
            isHot=(c<900) and (g in (3,4))
            p=[float((HOT[t] if (isHot and t in HOT) else CAL[t])([float((px>t).mean())])[0]) for t in THR]
            if isHot: p[2]=min(p[2], float(np.interp(c,HX,HY,left=HY[0],right=HY[-1]))*p[1])
            p[1]=min(p[1],p[0]); p[2]=min(p[2],p[1]); p[3]=min(p[3],p[2])
            for t,v in zip(THR,p): r[f'p{t}']=round(v,4)
            rows.append(r)
    return rows,tr7,pdays


# ================================================================= 6. page ===
_PG=[None]
def pagemod():
    if _PG[0] is None:
        import importlib.util
        spec=importlib.util.spec_from_file_location('pg', ROOT/'model'/'page.py')
        m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); _PG[0]=m
    return _PG[0]


def write_page(root, folder, d, scored, grid, tr7, comp, load_cor, gates, f=None, itb=None, pdays=365):
    pagemod().build(root, folder, d, scored, grid, tr7, comp, load_cor, gates, say, f, itb, pdays)


if __name__ == '__main__':
    main()
