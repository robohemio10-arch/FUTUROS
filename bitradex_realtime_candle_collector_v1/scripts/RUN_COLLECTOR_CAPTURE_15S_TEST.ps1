param(
    [int]$CaptureSeconds = 900,
    [int]$ScrollRounds = 10
)

$ErrorActionPreference = "Stop"

Set-Location "E:\FUTUROS\bitradex_realtime_candle_collector_v1"

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    . ".\.venv\Scripts\Activate.ps1"
}

$env:PYTHONPATH = "src"
$env:PYTHONUNBUFFERED = "1"

python -m bitradex_realtime_collector.main `
  --mode capture `
  --symbols BTCUSDT ETHUSDT `
  --timeframes 15s 1m 5m 15m `
  --capture-seconds $CaptureSeconds `
  --scroll-rounds $ScrollRounds `
  --export-every-seconds 30 `
  --heartbeat-seconds 30 `
  --disable-route-validation `
  --audit-all-network `
  --verbose

