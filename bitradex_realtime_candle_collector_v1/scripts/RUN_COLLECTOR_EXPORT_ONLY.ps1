Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    . ".\.venv\Scripts\Activate.ps1"
}
$env:PYTHONPATH = "src"
python -m bitradex_realtime_collector.main --mode export
python -m bitradex_realtime_collector.main --mode stats
