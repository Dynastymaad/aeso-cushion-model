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

  Steps 2 and 3 are read-only. If either fails the dashboard is still published.
#>
param([switch]$NoPush, [switch]$SkipUpdate)
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot

$mp = Join-Path (Split-Path $PSScriptRoot -Parent) 'aeso-market-power'

if (-not $SkipUpdate) {
    Write-Host "`n=== 1/3  refreshing the cushion model ===" -ForegroundColor Cyan
    if ($NoPush) { .\Update.ps1 -NoPush } else { .\Update.ps1 }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "update failed - stopping before the deltas, which would be stale." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "`n=== 1/3  skipped, reusing the last refresh ===" -ForegroundColor DarkGray
}

Write-Host "`n=== 2/3  supply changes by day ===" -ForegroundColor Cyan
python outage_delta.py
if ($LASTEXITCODE -ne 0) { Write-Host "outage_delta failed (carrying on)" -ForegroundColor Yellow }

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
Write-Host "       risk:      $mp\risk_nextday.html"
