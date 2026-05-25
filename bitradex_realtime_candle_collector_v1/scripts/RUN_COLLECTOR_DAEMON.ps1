param(
    [int]$ScrollRounds = 20,
    [int]$ExportEverySeconds = 60,
    [int]$HeartbeatSeconds = 30,
    [switch]$MirrorPhase22,
    [switch]$Headful,
    [switch]$DisableRouteValidation
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH = "src"

$argsList = @(
  "-m", "bitradex_realtime_collector.main",
  "--mode", "daemon",
  "--symbols", "BTCUSDT", "ETHUSDT",
  "--timeframes", "1m", "5m", "15m",
  "--capture-seconds", "0",
  "--scroll-rounds", "$ScrollRounds",
  "--export-every-seconds", "$ExportEverySeconds",
  "--heartbeat-seconds", "$HeartbeatSeconds"
)
if ($MirrorPhase22) { $argsList += @("--mirror-phase22-dir", "..\data\raw\bitradex_candles") }
if ($Headful) { $argsList += "--headful" }
if ($DisableRouteValidation) { $argsList += "--disable-route-validation" }

python @argsList
