<#
  Daily.ps1 - the morning run, in order.

      .\Daily.ps1              refresh, then outage deltas, then risk
      .\Daily.ps1 -NoPush      same, but don't push the dashboard
      .\Daily.ps1 -SkipUpdate  reuse this morning's refresh (fast re-read)

  1. Update.ps1      pulls CANPOWER + AESO, rebuilds the model, writes
                     docs\index.html and archives today's feeds. The archive
                     is what the outage comparison reads, so this has to run
                     first or the deltas compare today with itself.
  2. outage_delta.py what came off or came back for each day of the week
  3. risk.py         next-day withholding risk, opens the HTML page
  (between 2 and 3: Refresh-Checklist.ps1 rebuilds Checklist_Deviations_AB.xlsx)

  Steps 2 and 3 are read-only. If either fails the dashboard is still published.
#>
param([switch]$NoPush, [switch]$SkipUpdate)
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot

$mp = Join-Path (Split-Path $PSScriptRoot -Parent) 'aeso-market-power'

if (-not $SkipUpdate) {
    Write-Host "`n=== 1/3  refreshing the cushion model ===" -ForegroundColor Cyan
    if ($NoPush) { .\Update.ps1 -NoPush -FromDaily } else { .\Update.ps1 -FromDaily }

    # Do NOT gate the rest on $LASTEXITCODE. After Update.ps1 that code belongs
    # to the last NATIVE command it ran, which is 'git push' - so an expired
    # token or a dropped connection would stop the deltas and the risk page,
    # even though the model rebuilt perfectly. Judge step 1 by what it WROTE.
    $built = Test-Path 'docs\index.html'
    $fresh = $built -and ((Get-Date) - (Get-Item 'docs\index.html').LastWriteTime).TotalMinutes -lt 90
    if (-not $built) {
        Write-Host "docs\index.html was never written - the model did not rebuild. Stopping." -ForegroundColor Red
        exit 1
    }
    if (-not $fresh) {
        Write-Host "docs\index.html is older than 90 minutes - the refresh did not take." -ForegroundColor Red
        Write-Host "Run .\Update.ps1 on its own and read the error, then come back." -ForegroundColor Red
        exit 1
    }
    Write-Host "model rebuilt OK (publishing to GitHub is separate - if that failed, say so above)." -ForegroundColor DarkGray
} else {
    Write-Host "`n=== 1/3  skipped, reusing the last refresh ===" -ForegroundColor DarkGray
}

# Forecast journal. Scores yesterday's calls against what settled and appends to
# verify\hourly.csv. Deliberately non-fatal and deliberately AFTER the refresh, so
# it scores the page that was just published. Nothing downstream reads its output.
Write-Host "`n=== forecast journal ===" -ForegroundColor DarkCyan
python verify.py
if ($LASTEXITCODE -ne 0) { Write-Host "verify.py failed (carrying on)" -ForegroundColor Yellow }

Write-Host "`n=== 2/3  supply changes by day ===" -ForegroundColor Cyan
python outage_delta.py
if ($LASTEXITCODE -ne 0) { Write-Host "outage_delta failed (carrying on)" -ForegroundColor Yellow }

Write-Host "`n=== checklist workbook ===" -ForegroundColor DarkCyan
.\Refresh-Checklist.ps1
if ($LASTEXITCODE -ne 0) { Write-Host "checklist rebuild failed (carrying on)" -ForegroundColor Yellow }

Write-Host "`n=== 3/3  next-day withholding risk ===" -ForegroundColor Cyan
if (Test-Path (Join-Path $mp 'risk.py')) {
    Push-Location $mp
    python risk.py --open
    if ($LASTEXITCODE -ne 0) { Write-Host "risk.py failed (carrying on)" -ForegroundColor Yellow }
    Pop-Location
} else {
    Write-Host "aeso-market-power not found beside this folder - skipping risk.py" -ForegroundColor Yellow
}

Write-Host "`ndone.  dashboard: docs\index.html" -ForegroundColor Green
Write-Host "       deltas:    outage_delta.csv"
Write-Host "       checklist: Checklist_Deviations_AB.xlsx"
Write-Host "       risk:      $mp\risk_nextday.html"
