param(
    [int]$CaptureSeconds = 180,
    [int]$ScrollRounds = 40,
    [switch]$Headful,
    [switch]$DisableRouteValidation
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH = "src"

$argsList = @(
  "-m", "bitradex_realtime_collector.main",
  "--mode", "capture",
  "--symbols", "BTCUSDT", "ETHUSDT",
  "--timeframes", "1m", "5m", "15m",
  "--capture-seconds", "$CaptureSeconds",
  "--scroll-rounds", "$ScrollRounds",
  "--export-every-seconds", "30",
  "--verbose"
)
if ($Headful) { $argsList += "--headful" }
if ($DisableRouteValidation) { $argsList += "--disable-route-validation" }

python @argsList
