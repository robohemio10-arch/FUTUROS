param(
    [switch]$NoArchive
)

$ErrorActionPreference = "Stop"

$compose = "docker-compose.paper.yml"
$service = "smartcrypto-bot-paper"

function Step($message) {
    Write-Host ""
    Write-Host "==> $message" -ForegroundColor Cyan
}

if (-not (Test-Path "data\trades\inbox")) {
    New-Item -ItemType Directory -Force -Path "data\trades\inbox" | Out-Null
}

Step "Garantindo containers ativos"
docker compose -f $compose up -d
docker compose -f $compose ps

Step "Importando lotes OCR incrementalmente"
$args = @("scripts/import_trades_incremental.py")

if ($NoArchive) {
    $args += "--no-archive"
}

docker compose -f $compose exec -T $service python @args

Write-Host ""
Write-Host "Importação Fase 5 concluída. Rode RUN_PHASE5_REBUILD_DATASETS.ps1 para reconstruir trade_enriched e training_dataset." -ForegroundColor Green
