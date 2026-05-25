$ErrorActionPreference = "Stop"

$compose = "docker-compose.paper.yml"
$service = "smartcrypto-bot-paper"

function Step($message) {
    Write-Host ""
    Write-Host "==> $message" -ForegroundColor Cyan
}

function Ensure-File($path) {
    if (-not (Test-Path $path)) {
        throw "Arquivo obrigatório ausente: $path"
    }
    Write-Host "[OK] $path" -ForegroundColor Green
}

function Ensure-Dir($path) {
    if (-not (Test-Path $path)) {
        New-Item -ItemType Directory -Force -Path $path | Out-Null
    }
    Write-Host "[OK] $path" -ForegroundColor Green
}

Step "Validando arquivos-base"
Ensure-File $compose
Ensure-File "scripts\phase5_preflight.py"
Ensure-File "scripts\import_trades_incremental.py"
Ensure-File "scripts\rebuild_phase5_datasets.py"
Ensure-File "scripts\inspect_phase5_outputs.py"
Ensure-File "scripts\collect_phase5_summary.py"
Ensure-File "data\features\market_features_60d.parquet"
Ensure-File "data\sqlite\trading_dataset.sqlite"

Step "Garantindo diretórios da Fase 5"
Ensure-Dir "data\trades\inbox"
Ensure-Dir "data\trades\processed"
Ensure-Dir "data\reports"
Ensure-Dir "data\evidence"
Ensure-Dir "data\tmp"

Step "Validando Docker"
docker version
docker compose version

Step "Garantindo containers ativos"
docker compose -f $compose up -d
docker compose -f $compose ps

Step "Validando serviços"
$runningServices = docker compose -f $compose ps --services --status running
$requiredServices = @("smartcrypto-bot-paper", "smartcrypto-dashboard-paper", "freqtrade-paper")

foreach ($required in $requiredServices) {
    if ($runningServices -notcontains $required) {
        throw "Serviço $required não está running."
    }
    Write-Host "[OK] $required está running" -ForegroundColor Green
}

Step "Validando dependências e caminhos no container"
docker compose -f $compose exec -T $service python scripts/phase5_preflight.py

Write-Host ""
Write-Host "Preflight Fase 5 concluído com sucesso." -ForegroundColor Green
Write-Host ""
Write-Host "Para importar novos lotes, coloque arquivos em: data\trades\inbox" -ForegroundColor Yellow
