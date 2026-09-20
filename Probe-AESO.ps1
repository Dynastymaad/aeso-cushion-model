# Probe-AESO.ps1 — find out exactly which AESO endpoints your key can reach.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\Probe-AESO.ps1
#
# Run this ONCE. It tries every candidate route, prints which work, and writes
# probe_results.json. Send me that file and I will lock the refresh script to
# the routes that actually exist.
#
# Why this exists: AESO retired Current Supply Demand v1 on 30 September 2025
# and the developer portal needs a login, so the correct paths cannot be
# confirmed from outside. This asks your key directly.

$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$k = ((Get-Content (Join-Path $Root 'aeso_key.txt') -Raw) -replace '(?s).*[:=]\s*','').Trim()
$H = @{ accept='application/json'; 'API-Key'=$k }

$today = (Get-Date).ToString('yyyy-MM-dd')
$wk    = (Get-Date).AddDays(-7).ToString('yyyy-MM-dd')
$wkc   = (Get-Date).AddDays(-7).ToString('yyyyMMdd')
$tdc   = (Get-Date).ToString('yyyyMMdd')

$cands = @(
  # --- already in use, confirming they still work ---
  @{g='load';      n='actualforecast v1';        u="https://apimgw.aeso.ca/public/actualforecast-api/v1/load/albertaInternalLoad?startDate=$wk&endDate=$today"}
  @{g='gencap';    n='aiesgencapacity v1';       u="https://apimgw.aeso.ca/public/aiesgencapacity-api/v1/AIESGenCapacity?startDate=$wk&endDate=$today"}
  @{g='poolprice'; n='poolprice v1.1';           u="https://apimgw.aeso.ca/public/poolprice-api/v1.1/price/poolPrice?startDate=$wk&endDate=$today"}
  @{g='poolprice'; n='poolprice v2';             u="https://apimgw.aeso.ca/public/poolprice-api/v2/price/poolPrice?startDate=$wk&endDate=$today"}

  # --- current supply demand: v1 is retired, find the live route ---
  @{g='csd_sum';   n='csd v2 summary current';   u="https://apimgw.aeso.ca/public/currentsupplydemand-api/v2/csd/summary/current"}
  @{g='csd_sum';   n='csd v2 root';              u="https://apimgw.aeso.ca/public/currentsupplydemand-api/v2"}
  @{g='csd_sum';   n='csd v1 summary (retired?)';u="https://apimgw.aeso.ca/public/currentsupplydemand-api/v1/csd/summary/current"}
  @{g='csd_ast';   n='csd v2 assets';            u="https://apimgw.aeso.ca/public/currentsupplydemand-api/v2/csd/generation/assets/current"}
  @{g='csd_ast';   n='csd v1 assets';            u="https://apimgw.aeso.ca/public/currentsupplydemand-api/v1/csd/generation/assets/current"}

  # --- intertie: we want ACTUAL historical flow, not just capability ---
  @{g='itc';       n='itc v1 interchange (ATC)'; u="https://apimgw.aeso.ca/public/itc/v1/interchange?startDate=$wkc&endDate=$tdc&startHE=1&endHE=24&version=false&dataType=ATC&intertieOrFlowgate=SYSTEM"}
  @{g='itc_flow';  n='itc v1 actual flow';       u="https://apimgw.aeso.ca/public/itc/v1/interchange?startDate=$wkc&endDate=$tdc&startHE=1&endHE=24&version=false&dataType=ACTUAL_FLOW&intertieOrFlowgate=SYSTEM"}
  @{g='itc_flow';  n='interchange-api v1';       u="https://apimgw.aeso.ca/public/interchange-api/v1/interchange?startDate=$wk&endDate=$today"}

  # --- merit order: embargoed, but confirm the route and the lag ---
  @{g='merit';     n='energymeritorder v1';      u="https://apimgw.aeso.ca/public/energymeritorder-api/v1/meritOrder/energy?startDate=$wk&endDate=$today"}

  # --- system marginal price: finer than pool price, useful for grading ---
  @{g='smp';       n='systemmarginalprice v1.1'; u="https://apimgw.aeso.ca/public/systemmarginalprice-api/v1.1/price/systemMarginalPrice?startDate=$wk&endDate=$today"}
)

$out = @()
Write-Host "`nprobing $($cands.Count) candidate endpoints`n" -ForegroundColor Cyan
foreach ($c in $cands) {
    $r = [ordered]@{ group=$c.g; name=$c.n; url=$c.u; status=$null; bytes=0; shape=$null; error=$null }
    try {
        $resp = Invoke-WebRequest -Uri $c.u -Headers $H -TimeoutSec 60 -UseBasicParsing
        $r.status = [int]$resp.StatusCode
        $r.bytes  = $resp.RawContentLength
        try {
            $j = $resp.Content | ConvertFrom-Json
            $ret = if ($j.PSObject.Properties.Name -contains 'return') { $j.'return' } else { $j }
            if ($ret -is [System.Array]) { $r.shape = "array[$($ret.Count)] :: " + (($ret[0].PSObject.Properties.Name) -join ',') }
            else { $r.shape = (($ret.PSObject.Properties.Name) -join ',') }
            if ($r.shape.Length -gt 400) { $r.shape = $r.shape.Substring(0,400) + '...' }
        } catch { $r.shape = '(not json)' }
        Write-Host ("  OK   {0,-30} {1,8:N0} B  {2}" -f $c.n, $r.bytes, $r.shape) -ForegroundColor Green
    } catch {
        $code = $null
        if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        $r.status = $code; $r.error = $_.Exception.Message
        Write-Host ("  ---  {0,-30} {1}" -f $c.n, ("HTTP $code")) -ForegroundColor DarkYellow
    }
    $out += [pscustomobject]$r
}
$out | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $Root 'probe_results.json')
Write-Host "`nwrote probe_results.json - send me that file" -ForegroundColor Cyan
$ok = ($out | Where-Object { $_.status -eq 200 }).Count
Write-Host "$ok of $($cands.Count) reachable`n"
