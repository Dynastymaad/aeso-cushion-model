# AESO cushion model

Next-day fair-value read for the Alberta pool price, built from the supply
cushion. Published as a static page on GitHub Pages; rebuilt from your own
machine with one command.

---

## What it does

Counts the spare supply in every hour (the *cushion*), looks up what that much
spare supply has been worth over the last seven days, and widens the answer by
every hour of measured dispersion there is. Everything else on the page — the gas ladder,
the analogue days, the fundamentals view — is in service of that one idea.

The accuracy figures are **no longer written into the page by hand**. Every
refresh re-runs the walk-forward backtest and the "How accurate they are"
panel prints that run's own numbers. If the model gets worse, the page says so.

---

## One command

```
.\Update.ps1
```

That is the whole routine. It pulls CANPOWER and the AESO API, rebuilds the
model, rescores it, writes `docs\index.html`, commits and pushes. About three
minutes. Run it before 9am — after that the edge over the market has largely
gone.

| flag | what it does |
|---|---|
| `-NoPush` | refresh and stop, so you can look at `docs\index.html` first |
| `-SkipScore` | reuse the last calibration — about 60 s instead of 180 s |
| `-FromCache` | skip CANPOWER and reuse `cache\` (testing only) |
| `-Message "..."` | your own commit message |

Plus `python update.py --test-db`, which checks the CANPOWER connection and
finds the composition table without running anything else.

There is no separate monthly job any more. The CANPOWER pull, the AESO pull,
the rescore and the page are one script, so a single `git push` puts everything
current at once.

`update.py` prints six numbered stages and what each one did. If a number on
the page looks wrong, the log tells you which stage to look at.

### One-time setup — local

1. **Unzip** the repo to `C:\Users\<you>\dev\aeso-cushion`.

   **Not** Desktop or Documents — OneDrive's Known Folder Move redirects both,
   and the sync client can corrupt a `.git` folder mid-write. Anything you
   create at the profile root is outside OneDrive. Once it is pushed, GitHub is
   the backup, so the local copy does not need syncing at all.

   ```powershell
   New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\dev" | Out-Null
   Expand-Archive "$env:USERPROFILE\Downloads\aeso-cushion model.zip" "$env:USERPROFILE\dev" -Force
   Rename-Item "$env:USERPROFILE\dev\aeso-cushion model" "aeso-cushion"
   ```

   If you want it reachable from the Desktop, put a shortcut there rather than
   the folder itself:

   ```powershell
   $s = (New-Object -ComObject WScript.Shell).CreateShortcut(
          "$env:USERPROFILE\OneDrive - Dynasty Power\Desktop\aeso-cushion.lnk")
   $s.TargetPath = "$env:USERPROFILE\dev\aeso-cushion"; $s.Save()
   ```

   To check any path before you use it:

   ```powershell
   $p = "$env:USERPROFILE\dev"
   if ($p -like "$env:OneDrive*") { "SYNCED - pick somewhere else" } else { "not synced - good" }
   ```

2. **Python packages** (PowerShell):

   ```
   pip install pandas numpy scikit-learn pyodbc
   ```

   `scikit-learn` is required — the calibration will not run without it.
   `pyodbc` is required for the CANPOWER pull.

3. **AESO key** — copy your existing `aeso_key.txt` into the repo root:

   ```
   Copy-Item "$env:USERPROFILE\OneDrive - Dynasty Power\Desktop\Grabbing Data\aeso_key.txt" .
   ```

4. **CANPOWER connection** — copy `db.example.json` to `db.json` and fill it
   in from your DBeaver connection (Edit Connection → Main):

   | db.json | where it comes from |
   |---|---|
   | `server` | Host, then a **comma**, then Port: `host.example.com,1500`. ODBC takes the port inside `SERVER`, not as its own key. Leave the `,port` off only if it is 1433. |
   | `database` | Database / Schema |
   | `trusted_connection` | `false` if DBeaver shows *SQL Server Authentication*; `true` for Windows auth |
   | `username` / `password` | only when `trusted_connection` is `false` |
   | `trust_server_certificate` | `true` if DBeaver has *Trust Server Certificate* ticked. Driver 18 encrypts by default, so this is usually needed. |
   | `driver` | whatever `--test-db` lists — 17 and 18 are both common |

   Then **test it in ten seconds** rather than waiting on a full run:

   ```
   python update.py --test-db
   ```

   That prints the ODBC drivers you actually have, opens the connection, and
   finds the supply-composition table by its columns. Paste the name it prints
   into `composition_table` and you are done. If it connects but finds no
   table, the table is in your other database — point `db.json` there.

   `db.json` is gitignored, so the password stays on this machine.

   **If `--test-db` finds no modern SQL Server driver.** Windows ships a legacy
   driver called plain `SQL Server`; `--test-db` will try it automatically. It
   often cannot reach a current SQL Server — it predates TLS 1.2 and
   `TrustServerCertificate`, and typically fails with *"SQL Server does not
   exist or access denied"* even when DBeaver connects fine, because DBeaver
   uses its own JDBC driver and never touches ODBC.

   The fix is Microsoft's ODBC Driver 18 (18.7.1.1, September 2026):
   <https://go.microsoft.com/fwlink/?linkid=2378279> — x64 MSI, **needs
   administrator rights**, so it may be an IT request on a managed machine.
   It installs side by side with anything already present and breaks nothing.
   Afterwards, set `"driver": "ODBC Driver 18 for SQL Server"` and keep
   `trust_server_certificate` true.

   No admin rights and no prospect of getting them? The fallback is to export
   the four queries in `sql/` from DBeaver as CSVs into `cache/` —
   `composition.csv`, `wind_fc.csv`, `solar_fc.csv`, `load_fc.csv` — and run
   `.\Update.ps1 -FromCache`. Everything downstream is identical; you just
   refresh the exports by hand instead of the script doing it.

5. **First run**, without publishing:

   ```
   .\Update.ps1 -NoPush
   ```

   Roughly three minutes. Open `docs\index.html` and confirm it looks right
   before going any further.

### One-time setup — GitHub Pages

6. **Create an empty private repo** on GitHub named `aeso-cushion`. Do **not**
   add a README, .gitignore or licence — the first push must land on an empty
   repo or you will have to rebase.

7. **Initialise and push** from the repo folder:

   ```
   git init -b main
   git add -A
   git commit -m "AESO cushion model"
   git remote add origin https://github.com/<you>/aeso-cushion.git
   git push -u origin main
   ```

   Check what went up before you push if you like: `git status` should show
   **no** `db.json`, `aeso_key.txt`, `cache/`, `refresh/` or `archive/`. Those
   are gitignored, so your credentials never leave the machine.

8. **Turn on Pages**: repo → Settings → Pages → Source *Deploy from a branch* →
   branch `main`, folder `/docs` → Save. First build takes a minute or two.

9. **Your page** is at `https://<you>.github.io/aeso-cushion/`.

### A note on who can see it

A private repo keeps the *code* private. The **published page is still
reachable by anyone who has the URL** — GitHub only offers access-controlled
Pages on Enterprise Cloud. The URL is unguessable and is not indexed by search
engines, which is how most desks run this, but it is not authentication. If the
page itself must be behind a login, that is an Enterprise Cloud conversation
with whoever administers your GitHub org.

### Optional: the intraday tracker

```
.\Watch-CSD.ps1
```

Pulls only the Current Supply Demand report and appends a row to `csd_log.csv`.
Schedule it hourly in Task Scheduler and by evening you can see how far the
live system has drifted from the morning's read — which matters most on exactly
the days the read is least reliable.

---

## What is in here

| path | what it is |
|---|---|
| `update.py` | **the only entry point.** Six stages, pull to page |
| `Update.ps1` | thin wrapper: run `update.py`, sanity-check the output, commit, push |
| `Probe-AESO.ps1` | one-time: reports which AESO endpoints your key can reach |
| `Watch-CSD.ps1` | hourly Current Supply Demand logger |
| `sql/` | the four CANPOWER queries, plus the table-discovery query |
| `model/pipeline.py` | raw exports → hourly actuals, day-ahead vintages, the cushion |
| `model/core.py` | the model: level curve, residual pools, calibration |
| `model/page.py` | model → `docs/index.html` |
| `model/template.html` | the page, with a `__DATA__` placeholder |
| `model/price_history.csv` | accumulated settled price — the one piece of state |
| `model/calibration.pkl` | the fitted maps, so `-SkipScore` has something to reuse |
| `model/itbands.json` | the endogenous intertie curve |
| `model/fund_bands.json`, `model/itp10.json` | fundamentals bands, worst-tenth import |
| `docs/index.html` | what GitHub Pages serves |

The old `model_bundle.json` is gone. Everything it carried is either recomputed
each run (history, normals, analogue library, calibration) or split into the
three small static JSON files above.

### Why the spread uses all the history

The price *level* comes from seven days, because the cushion-to-price
relationship drifts fast — the same 1,000 MW meant a 34% chance of a spike in
one quarter and 1% in another. The *spread* around that level uses every hour
ever recorded, and grows each run.

That is not just "more is better". It was measured. Splitting the history in
half and testing every cushion × time-of-day cell, the residual distribution is
statistically indistinguishable between the two halves **in every tight cell**
(KS p > 0.01). It has drifted only in the loose cells above 2,100 MW — where
there are thousands of hours either way and nothing is at stake. So old data is
still valid exactly where the sample is thinnest and a tail probability needs
it most.

Walk-forward, expanding beat a rolling 365 days on skill at all four thresholds
and on AUC at $50, $100 and $700, and caught more spikes in its top 1% of
readings. It cost $0.07 an hour on expected value. The pool is 17,930 hours
today and gets better every day you run the refresh — a rolling year never
would have.

### Calendar reminder — re-run the split-half test in 2029

**Re-run that split-half test once you have four or five years of history.**
Drift that doesn't show across two years could show across five, and if it ever
does, the fix is to go back to a bounded window.

To repeat it: split the scored frame in half by date, bucket both halves by
cushion bin × time-of-day block, and run a two-sample KS test on the residual
(`r7`) in each cell. Today, every *tight* cell comes back p > 0.01 (no
detectable drift) and only the loose cells above 2,100 MW differ. If tight
cells start failing, switch the pool in `update.py` back to a rolling window —
two lines, both marked with a comment pointing here:

- `score()` — `h = d[d.index < pd.Timestamp(dy)]`
- `make_grid()` — `hist = d`

Replace each with a `pd.Timedelta(days=N)` filter and re-run the window
comparison (180 / 365 / expanding) to pick N on evidence rather than habit.

### The one piece of state

`model/price_history.csv` accumulates settled pool price. Each run pulls the
full history from the API in 365-day chunks and merges it in, so the file is
normally redundant — but if a chunk pull fails, the history stops growing
instead of silently shortening and taking the backtest with it. It is committed
on each run; that is the only file that grows.

---

## What the six stages do

1. **CANPOWER** — supply composition and the wind / solar / load forecast
   vintages, into `cache/`
2. **AESO API** — load, gencap, intertie, wind/solar, CSD, and the settled
   price history in 365-day chunks, into `refresh/`; one zip per day kept
   forever in `archive/`
3. **rebuild** — hourly actuals, day-ahead forecasts at their true vintage,
   walk-forward recalibration of each vendor, the cushion, residuals against
   the curve that prevailed at the time
4. **score** — walk-forward backtest, then the calibration maps, with a
   separate map for tight after-dark hours
5. **grid** — 285 cushion × time-of-day cells, 4,000 draws each, smeared over
   the measured cushion error
6. **page** — the 30-day normal hour, the analogue days, `docs/index.html`

Stage 4 is the slow one. `-SkipScore` skips it.

---

## What each feed is for

| feed | used by the build? | why it is pulled |
|---|---|---|
| `load` | **yes** | actual and forecast Alberta internal load |
| `gencap` | **yes** | availability by sub-fuel — the gas ladder, hydro, storage, biomass |
| `poolprice` + `poolprice_h*` | **yes** | the curve, the residual pool, and the backtest's truth |
| `wind_fc` / `solar_fc` | **yes** | Most Likely plus AESO's own Min-to-Max band |
| `intertie` | **yes** | caps the import assumption at published transfer capability |
| `csd_summary` | no | the live snapshot — a single instant, so it cannot feed a next-day model |

The intertie cap matters more than it looks. The model assumes neighbours send
power when Alberta tightens, but that assumption should never exceed what the
wires can carry. Import capability in a normal week runs about 1,045 MW and
drops to 399 MW when a line is derated — exactly the case the rule would
otherwise miss. The cap is silent when capability is normal and binds when it
is not; the build prints a line whenever it bites.

On hours that have already settled the assumption is dropped entirely and the
observed interchange (`net_imports_actual_scheduled`) is used instead.

## What actually comes from where — audited

I checked every input against the API rather than assuming.

| input | CANPOWER | AESO API | verdict |
|---|---|---|---|
| gas availability by sub-fuel | yes | yes | **API is enough** — mean difference under 1 MW |
| hydro / storage / biomass availability | yes | yes | **API is enough** — mean difference under 1 MW |
| load actual | snapshot ~1h after the hour | final published | **API is better** |
| wind / solar actual | snapshot ~1h after the hour | final published | **API is better** |
| pool price | close, not equal (MAE $2.93) | settled | **API is the source** |
| **actual net interchange** | **yes** | **no confirmed route** | **CANPOWER required** |
| **day-ahead forecast vintages** | **yes** | **snapshot only** | **CANPOWER required for history** |
| vendor forecasts (Meteologica, Frontier, DYNASTY, Tesla) | yes | no | research only — not needed |
| coal, dual_fuel, long_lead_volume | yes | partly | the model does not use them |

Two findings worth stating plainly.

**The CANPOWER `b_*` table is not a table of actuals.** Its `lead_bucket` column
spans −1, 0 and +1 hours, and even `lead_bucket = -1` is a reading taken about
an hour *after* the hour began. Its `forecast_pool_price` at that bucket is
within $2.93 of the settled price on average — close, but not the settled
price, which is why price comes from the API.

**The day-ahead vintage is the real dependency.** The model is trained on what
the forecast said *a day ahead*, and measuring that requires knowing what was
published yesterday for today. The `WindForecast` and `LoadForecast` tables have
exactly that — a `Timestamp` when the forecast was made and an
`EffectiveDateTime` it applies to, median lead +28.5 hours. **AESO's API cannot
reproduce this retrospectively.** Each pull is a single snapshot of the current
forecast; nothing in it says what yesterday's forecast was.

So my earlier claim that the DBeaver data was mostly unnecessary was wrong. It
is necessary, for two specific things — and both are now pulled automatically
in stage 1, so there is nothing to export by hand.

**And let the archive build.** Stage 2 keeps one zip per day in `archive/`
permanently. That is not housekeeping — it is data collection. Each morning's
pull *is* a day-ahead vintage. After a year of them the API can measure its own
day-ahead error and the forecast half of the CANPOWER dependency disappears.
The interchange half only closes if `Probe-AESO.ps1` finds an actual-flow route.

## Known limits

- **Wind is the binding constraint on horizon.** AESO's wind and solar report is
  a rolling 216-hour window, so the page runs about seven days forward and the
  last day is usually partial. Gas availability runs 15 days and load 15, but
  without a renewables forecast there is no cushion.
- **Net imports are assumed, not scheduled**, on forecast hours. Intertie flows
  are set close to real time, so the model substitutes a rule fitted to history.
  On tight evenings the help fails to arrive about 19% of the time.
- **Gas availability reflects filed outage plans.** Accurate about planned work,
  blind to a unit tripping at 4pm.
- **The deep tail is still shy after dark.** On tight evening hours the model
  says roughly 18% chance of clearing $300 where 22% happened. Read it as a
  floor. The page's accuracy panel measures this fresh each run.
- **The backtest uses actual gas availability and intertie flows**, because no
  forecast archive exists for either. Expect live results slightly worse than
  the panel's figures.
- **The probability ladder has visible plateaus.** Isotonic calibration on a
  finite sample produces flat steps, so two adjacent cushion cells can read the
  same. That is the evidence running out, shown rather than smoothed over.

---

## If something breaks

| symptom | likely cause |
|---|---|
| `aeso_key.txt not found` | key file missing or in the wrong folder |
| `pyodbc is not installed` | `pip install pyodbc`, or export the four `sql/` queries to `cache/` and run `-FromCache` |
| `could not find the supply-composition table` | run `sql\00_find_composition_table.sql` by hand and put the name in `db.json` |
| a feed reports FAILED | AESO endpoint down, or the key lacks that product |
| `cache/ is missing: ...` | `-FromCache` with nothing cached — run without it |
| `price_history.csv carried N hours the pull did not return` | a price chunk failed; harmless once, worth checking if it repeats |
| page shows a short last day | normal: the renewables feed ends mid-day |
| `git push failed` | not authenticated, or the branch is behind — `git pull --rebase` |
