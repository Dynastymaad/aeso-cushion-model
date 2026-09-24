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

# ---------------------------------------------------------------- publish ---
# Publishing must never jam the repo. Three things go wrong here and every one
# of them used to surface as "another git process seems to be running":
#
#   * a leftover index.lock from a run that was interrupted at the git step
#   * git push hanging on a credential prompt nobody can see
#   * a failure halfway, leaving files staged but never committed
#
# So: clear a stale lock, refuse to fight a live one, never let git prompt, and
# say plainly what happened. The model is already rebuilt by this point - none
# of this can cost you the refresh.

$lock     = Join-Path $PSScriptRoot '.git\index.lock'
$canPush  = $true

if (Test-Path $lock) {
    $age = ((Get-Date) - (Get-Item $lock).LastWriteTime).TotalMinutes
    if ($age -gt 2) {
        Write-Host ("clearing a stale index.lock ({0:N0} min old)" -f $age) -ForegroundColor Yellow
        Remove-Item $lock -Force -ErrorAction SilentlyContinue
    } else {
        Write-Host "`nA git process is active in this repo right now - not publishing." -ForegroundColor Yellow
        Write-Host "The model is rebuilt and docs\index.html is current. Re-run with" -ForegroundColor Yellow
        Write-Host "-SkipUpdate once it clears if you want the GitHub copy updated." -ForegroundColor Yellow
        $canPush = $false
    }
}

if ($canPush) {
    # fail fast instead of hanging on a login box that never appears
    $env:GIT_TERMINAL_PROMPT = '0'
    $env:GCM_INTERACTIVE     = 'Never'

    git add -A
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`ngit add failed. Something is holding a file open - Excel on a CSV" -ForegroundColor Yellow
        Write-Host "in model\ is the usual one. The refresh itself is fine." -ForegroundColor Yellow
        Remove-Item $lock -Force -ErrorAction SilentlyContinue   # do not leave it for next time
    } else {
        # 'git diff --cached --quiet' reports through its EXIT CODE and prints
        # nothing, so if() on the command tests empty output and is always false.
        git diff --cached --quiet
        if ($LASTEXITCODE -eq 0) {
            Write-Host "nothing changed - not committing."
        } else {
            git commit -m $Message
            if ($LASTEXITCODE -ne 0) {
                Write-Host "git commit failed - nothing published." -ForegroundColor Yellow
            } else {
                git push
                if ($LASTEXITCODE -ne 0) {
                    Write-Host "`nPUSH FAILED - almost always an expired GitHub token." -ForegroundColor Yellow
                    Write-Host "Your commit is safe locally; only the GitHub copy is behind." -ForegroundColor Yellow
                    Write-Host "Fix the credential, then just run:  git push" -ForegroundColor Yellow
                } else {
                    Write-Host "`npushed. GitHub Pages usually takes a minute to rebuild."
                }
            }
        }
    }
    Remove-Item $lock -Force -ErrorAction SilentlyContinue       # belt and braces
}

# You almost certainly wanted Daily.ps1. This script refreshes the model and the
# published dashboard; it does NOT write outage_delta.csv or risk_nextday.csv,
# which are what the desk monitor's week-ahead and tomorrow's-shape panels read.
if (-not $FromDaily) {
    Write-Host "`nNOTE: outage deltas and next-day risk were NOT refreshed." -ForegroundColor Cyan
    Write-Host "      Run  .\Daily.ps1 -SkipUpdate   to finish the job (a few seconds)." -ForegroundColor Cyan
}
