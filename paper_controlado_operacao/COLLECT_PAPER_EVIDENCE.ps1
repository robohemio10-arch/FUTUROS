param()

$ErrorActionPreference = "Continue"

$scriptDir = Split-Path -Parent $PSCommandPath
$root = (Resolve-Path (Join-Path $scriptDir "..")).Path
Set-Location $root

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outDir = "data\evidence\paper_ops_$timestamp"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

docker compose -f docker-compose.paper.yml ps | Out-File -FilePath (Join-Path $outDir "docker_ps.txt") -Encoding UTF8
docker compose -f docker-compose.paper.yml logs --tail=300 | Out-File -FilePath (Join-Path $outDir "docker_logs_tail.txt") -Encoding UTF8

$paths = @(
    "data\reports\paper_sessions",
    "data\reports\phase5_import_report.json",
    "data\reports\phase5_rebuild_report.json",
    "data\reports\phase14_summary.json",
    "data\reports\phase13_signal_runtime_summary.json",
    "data\runtime\active_freqtrade_signals.json",
    "data\runtime\freqtrade_signal_decisions.jsonl"
)

foreach ($path in $paths) {
    if (Test-Path $path) {
        Copy-Item $path -Destination $outDir -Recurse -Force
    }
}

$zip = "data\evidence\paper_ops_$timestamp.zip"
if (Test-Path $zip) {
    Remove-Item $zip -Force
}
Compress-Archive -Path (Join-Path $outDir "*") -DestinationPath $zip -Force

Write-Host "Evidência gerada em: $zip"
