# Watch-CSD.ps1  ·  the intraday tracker
#
# Pulls only the Current Supply Demand report and appends one row per run to
# csd_log.csv. Schedule it hourly in Task Scheduler and by evening you can see
# how far the live system has drifted from the morning's read - which matters
# most on exactly the days the read is least reliable.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\Watch-CSD.ps1

$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$k = ((Get-Content (Join-Path $Root 'aeso_key.txt') -Raw) -replace '(?s).*[:=]\s*','').Trim()
$H = @{ accept='application/json'; 'API-Key'=$k }

$r = Invoke-RestMethod -Uri "https://apimgw.aeso.ca/public/currentsupplydemand-api/v2/csd/summary/current" -Headers $H -TimeoutSec 60
$d = $r.return

# the generation block is a list of fuel types; pull the ones the model uses
$gen = @{}
foreach ($g in $d.generation_data_list) { $gen[$g.fuel_type] = $g }

$row = [PSCustomObject]@{
    pulled_at        = (Get-Date).ToString('yyyy-MM-dd HH:mm')
    last_updated     = $d.last_updated_datetime_mpt
    alberta_load     = $d.alberta_internal_load
    net_interchange  = $d.net_actual_interchange
    pool_price       = $d.pool_price
    forecast_price   = $d.forecast_pool_price
    gas_mc           = $gen['GAS'].maximum_capability
    gas_tng          = $gen['GAS'].net_generation
    wind_tng         = $gen['WIND'].net_generation
    wind_mc          = $gen['WIND'].maximum_capability
    solar_tng        = $gen['SOLAR'].net_generation
    hydro_tng        = $gen['HYDRO'].net_generation
    storage_tng      = $gen['ENERGY STORAGE'].net_generation
    dispatched_rsrv  = $d.dispatched_contingency_reserve_total
}
$csv = Join-Path $Root 'csd_log.csv'
if (Test-Path $csv) { $row | Export-Csv $csv -NoTypeInformation -Append }
else                { $row | Export-Csv $csv -NoTypeInformation }
"{0}  load {1,6:N0}  wind {2,6:N0}  price {3,8}" -f $row.pulled_at, $row.alberta_load, $row.wind_tng, $row.pool_price
