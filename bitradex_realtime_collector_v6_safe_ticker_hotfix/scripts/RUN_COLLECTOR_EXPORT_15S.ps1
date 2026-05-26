$ErrorActionPreference = "Stop"

Set-Location "E:\FUTUROS\bitradex_realtime_candle_collector_v1"

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    . ".\.venv\Scripts\Activate.ps1"
}

$env:PYTHONPATH = "src"
$env:PYTHONUNBUFFERED = "1"

python -m bitradex_realtime_collector.main `
  --mode export `
  --symbols BTCUSDT ETHUSDT `
  --timeframes 15s 1m 5m 15m

python -m bitradex_realtime_collector.main --mode stats

