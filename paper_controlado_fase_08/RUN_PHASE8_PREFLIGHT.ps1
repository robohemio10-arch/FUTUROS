$ErrorActionPreference = "Stop"

$compose = "docker-compose.paper.yml"
$service = "smartcrypto-bot-paper"

function Step($message) {
    Write-Host ""
    Write-Host "==> $message" -ForegroundColor Cyan
}

function Ensure-File($path) {
    if (-not (Test-Path $path)) {
        throw "Arquivo obrigatorio ausente: $path"
    }
    Write-Host "[OK] $path" -ForegroundColor Green
}

function Ensure-Dir($path) {
    if (-not (Test-Path $path)) {
        New-Item -ItemType Directory -Force -Path $path | Out-Null
    }
    Write-Host "[OK] $path" -ForegroundColor Green
}

function Ensure-DisabledFlag($name) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ($null -ne $value -and $value.Trim().ToLowerInvariant() -in @("1", "true", "yes", "y", "on")) {
        throw "$name habilitado; Fase 8 deve permanecer paper/research/shadow."
    }
    Write-Host "[OK] $name nao habilitado" -ForegroundColor Green
}

Step "Validando guardrails paper"
Ensure-DisabledFlag "LIVE_ENABLED"
Ensure-DisabledFlag "ORDER_SUBMISSION_ENABLED"
Ensure-DisabledFlag "REAL_ORDER_SUBMISSION_ENABLED"

Step "Validando arquivos-base da Fase 8 / Qlib"
Ensure-File $compose
Ensure-File "docker\qlib\Dockerfile"
Ensure-File "docker\qlib\requirements.txt"
Ensure-File "config\qlib_model.yml"
Ensure-File "scripts\phase8_preflight.py"
Ensure-File "scripts\build_qlib_dataset.py"
Ensure-File "scripts\train_qlib_market_model.py"
Ensure-File "scripts\export_qlib_predictions.py"
Ensure-File "scripts\export_qlib_freqtrade_signals.py"

Step "Garantindo diretorios runtime locais"
Ensure-Dir "data\reports"
Ensure-Dir "data\qlib"

Step "Validando Docker"
docker version
docker compose version

Step "Garantindo containers paper ativos"
docker compose -f $compose up -d
docker compose -f $compose ps

Step "Validando servico paper"
$runningServices = docker compose -f $compose ps --services --status running
if ($runningServices -notcontains $service) {
    throw "Servico $service nao esta running."
}
Write-Host "[OK] $service esta running" -ForegroundColor Green

Step "Executando preflight seguro da Fase 8 / Qlib"
docker compose -f $compose exec -T $service python scripts/phase8_preflight.py

Write-Host ""
Write-Host "Preflight Fase 8 / Qlib concluido em modo paper/research/shadow." -ForegroundColor Green
