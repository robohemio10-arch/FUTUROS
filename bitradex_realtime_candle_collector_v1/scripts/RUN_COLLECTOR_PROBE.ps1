param(
    [int]$ProbeDays = 3,
    [int]$ProbeLimit = 1500,
    [int]$ProbeConcurrency = 6
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH = "src"

python -m bitradex_realtime_collector.main `
  --mode probe `
  --symbols BTCUSDT ETHUSDT `
  --timeframes 1m 5m 15m `
  --probe-days $ProbeDays `
  --probe-limit $ProbeLimit `
  --probe-concurrency $ProbeConcurrency `
  --verbose
