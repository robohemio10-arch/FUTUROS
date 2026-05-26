$ErrorActionPreference = "Stop"

Set-Location "C:\Smart Cripto\FUTUROS\bitradex_realtime_candle_collector_v1"

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    . ".\.venv\Scripts\Activate.ps1"
}

$env:PYTHONPATH = "src"
$env:PYTHONUNBUFFERED = "1"

python -m bitradex_realtime_collector.main `
  --mode daemon `
  --symbols BTCUSDT ETHUSDT `
  --timeframes 15s 1m 5m 15m `
  --capture-seconds 0 `
  --scroll-rounds 10 `
  --export-every-seconds 60 `
  --heartbeat-seconds 30 `
  --disable-route-validation `
  --audit-all-network `
  --verbose
