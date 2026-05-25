$ErrorActionPreference = "Stop"

$compose = "docker-compose.paper.yml"
$service = "smartcrypto-bot-paper"

function Step($message) {
    Write-Host ""
    Write-Host "==> $message" -ForegroundColor Cyan
}

function Show-FileStatus($path) {
    if (Test-Path $path) {
        $item = Get-Item $path
        $sizeMb = [Math]::Round($item.Length / 1MB, 2)
        Write-Host "[OK] $path  $sizeMb MB" -ForegroundColor Green
    }
    else {
        Write-Host "[PENDENTE] $path" -ForegroundColor Yellow
    }
}

Step "Status dos containers"
docker compose -f $compose ps

Step "Arquivos esperados"
Show-FileStatus "data\trades\trades_master.xlsx"
Show-FileStatus "data\trades\trades_master.parquet"
Show-FileStatus "data\trades\trades_excel.xlsx"
Show-FileStatus "data\features\trade_enriched.parquet"
Show-FileStatus "data\features\training_dataset.parquet"
Show-FileStatus "data\sqlite\trading_dataset.sqlite"
Show-FileStatus "data\reports\phase5_import_report.json"
Show-FileStatus "data\reports\phase5_rebuild_report.json"

Step "Inspecionando saídas da Fase 5"
docker compose -f $compose exec -T $service python scripts/inspect_phase5_outputs.py

Step "Últimos logs do bot"
docker compose -f $compose logs $service --tail=30

Write-Host ""
Write-Host "Verificação Fase 5 concluída com sucesso." -ForegroundColor Green
