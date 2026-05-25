$ErrorActionPreference = "Stop"

$compose = "docker-compose.paper.yml"
$service = "smartcrypto-bot-paper"

function Step($message) {
    Write-Host ""
    Write-Host "==> $message" -ForegroundColor Cyan
}

Step "Garantindo containers ativos"
docker compose -f $compose up -d
docker compose -f $compose ps

Step "Reconstruindo trade_enriched, training_dataset e SQLite"
docker compose -f $compose exec -T $service python scripts/rebuild_phase5_datasets.py

Step "Inspecionando saídas"
docker compose -f $compose exec -T $service python scripts/inspect_phase5_outputs.py

Write-Host ""
Write-Host "Reconstrução Fase 5 concluída." -ForegroundColor Green
