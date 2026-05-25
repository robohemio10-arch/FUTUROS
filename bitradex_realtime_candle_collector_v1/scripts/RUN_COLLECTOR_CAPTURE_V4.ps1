Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    . ".\.venv\Scripts\Activate.ps1"
}
$env:PYTHONPATH = "src"
python -m bitradex_realtime_collector.main `
  --mode capture `
  --symbols BTCUSDT ETHUSDT `
  --timeframes 1m 5m 15m `
  --capture-seconds 300 `
  --scroll-rounds 10 `
  --export-every-seconds 30 `
  --heartbeat-seconds 30 `
  --disable-route-validation `
  --audit-all-network `
  --verbose
