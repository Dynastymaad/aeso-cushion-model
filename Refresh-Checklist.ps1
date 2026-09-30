<#
  Refresh-Checklist.ps1 - rebuild Checklist_Deviations_AB.xlsx from the current caches.

      .\Refresh-Checklist.ps1              for tomorrow (Calgary time)
      .\Refresh-Checklist.ps1 2026-10-03   for another delivery day

  Reads the same caches as the model (composition, price history, forecasts,
  gencap, outage report, forward curves). Run Daily.ps1 or Update.ps1 first if
  you want today's pulls in it; Daily.ps1 already calls this at the end.
  Close the workbook in Excel before running, or the swap at the end is skipped.
#>
param([string]$Day)
Set-Location $PSScriptRoot
$tmp = 'Checklist_Deviations_AB.new.xlsx'
if ($Day) { python checklist_ab.py . $tmp $Day } else { python checklist_ab.py . $tmp }
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $tmp)) { Write-Host "checklist build failed - the old workbook is untouched" -ForegroundColor Red; exit 1 }
try {
    Move-Item -Force $tmp 'Checklist_Deviations_AB.xlsx' -ErrorAction Stop
    Write-Host "Checklist_Deviations_AB.xlsx refreshed" -ForegroundColor Green
} catch {
    Write-Host "Checklist_Deviations_AB.xlsx is open in Excel - close it and run this again (new build is in $tmp)" -ForegroundColor Yellow
    exit 1
}
