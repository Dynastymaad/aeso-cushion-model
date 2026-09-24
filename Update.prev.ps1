<#
  Update.ps1 - one command, everything current.

      .\Update.ps1                 refresh and publish
      .\Update.ps1 -NoPush         refresh, review docs\index.html, push yourself
      .\Update.ps1 -FromCache      skip CANPOWER, reuse cache\ (testing)
      .\Update.ps1 -SkipScore      reuse the last calibration (fast, ~30s)

  It runs update.py, which pulls CANPOWER and the AESO API, rebuilds the model,
  rescores it, and writes docs\index.html. Nothing else needs running.
#>
param([switch]$NoPush, [switch]$FromCache, [switch]$SkipScore, [string]$Message)

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
if (git diff --cached --quiet) { Write-Host "nothing changed - not committing."; exit 0 }
git commit -m $Message
git push
Write-Host "`npushed. GitHub Pages usually takes a minute to rebuild."
