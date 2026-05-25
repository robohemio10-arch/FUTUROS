Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH = "src"

python -m bitradex_realtime_collector.main `
  --mode daemon `
  --symbols BTCUSDT ETHUSDT `
  --timeframes 1m 5m 15m `
  --capture-seconds 0 `
  --scroll-rounds 5 `
  --export-every-seconds 60 `
  --heartbeat-seconds 30 `
  --disable-route-validation
