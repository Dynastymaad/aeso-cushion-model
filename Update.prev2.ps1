<#
  Update.ps1 - one command, everything current.

      .\Update.ps1                 refresh and publish
      .\Update.ps1 -NoPush         refresh, review docs\index.html, push yourself
      .\Update.ps1 -FromCache      skip CANPOWER, reuse cache\ (testing)
      .\Update.ps1 -SkipScore      reuse the last calibration (fast, ~30s)

  It runs update.py, which pulls CANPOWER and the AESO API, rebuilds the model,
  rescores it, and writes docs\index.html. Nothing else needs running.
#>
param([switch]$NoPush, [switch]$FromCache, [switch]$SkipScore, [string]$Message,
      [switch]$FromDaily)   # set by Daily.ps1 so the reminder below stays quiet

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$flags = @()
if ($FromCache) { $flags += '--from-cache' }
if ($SkipScore) { $flags += '--skip-score' }

python update.py @flags
if ($LASTEXITCODE -ne 0) { Write-Error "update.py failed - nothing was published."; exit 1 }

if (-not (Test-Path 'docs\index.html')) { Write-Error "docs\index.html was not written."; exit 1 }
$size = (Get-Item 'docs\index.html').Length / 1MB
Write-Host ("`ndocs\index.html  {0:N2} MB" -f $size)
if ($size -lt 0.05) { Write-Error "docs\index.html looks truncated - not publishing."; exit 1 }

if ($NoPush) { Write-Host "-NoPush: review docs\index.html, then commit and push."; exit 0 }

if (-not $Message) { $Message = "refresh $(Get-Date -Format 'yyyy-MM-dd HH:mm')" }
git add -A
# 'git diff --cached --quiet' says what it means through its EXIT CODE and
# prints nothing. PowerShell's if() tests the OUTPUT, which is always empty, so
# the old guard never fired and this always tried to commit. Read the code.
git diff --cached --quiet
$noChanges = ($LASTEXITCODE -eq 0)
if ($noChanges) {
    Write-Host "nothing changed - not committing."
} else {
    git commit -m $Message
    git push
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`nPUSH FAILED. The model is rebuilt and docs\index.html is current -" -ForegroundColor Yellow
        Write-Host "only the GitHub copy is behind. Nothing downstream depends on it." -ForegroundColor Yellow
    } else {
        Write-Host "`npushed. GitHub Pages usually takes a minute to rebuild."
    }
}

# You almost certainly wanted Daily.ps1. This script refreshes the model and the
# published dashboard; it does NOT write outage_delta.csv or risk_nextday.csv,
# which are what the desk monitor's week-ahead and tomorrow's-shape panels read.
if (-not $FromDaily) {
    Write-Host "`nNOTE: outage deltas and next-day risk were NOT refreshed." -ForegroundColor Cyan
    Write-Host "      Run  .\Daily.ps1 -SkipUpdate   to finish the job (a few seconds)." -ForegroundColor Cyan
}
