$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

. ".\.venv\Scripts\Activate.ps1"
$env:PYTHONPATH = "src"
python -m bitradex_realtime_collector.main --mode stats
python -m bitradex_realtime_collector.main --mode export
