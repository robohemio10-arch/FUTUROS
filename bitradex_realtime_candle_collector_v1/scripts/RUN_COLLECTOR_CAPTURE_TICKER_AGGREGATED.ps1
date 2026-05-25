param(
    [int]$CaptureSeconds = 300
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH = "src"

python -m bitradex_realtime_collector.main `
  --mode capture `
  --symbols BTCUSDT ETHUSDT `
  --timeframes 1m 5m 15m `
  --capture-seconds $CaptureSeconds `
  --scroll-rounds 10 `
  --export-every-seconds 30 `
  --heartbeat-seconds 30 `
  --disable-route-validation `
  --audit-all-network `
  --verbose
