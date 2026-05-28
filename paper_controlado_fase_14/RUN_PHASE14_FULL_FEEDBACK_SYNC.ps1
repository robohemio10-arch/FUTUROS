param(
    [int]$WaitSeconds = 60,
    [switch]$RunPhase5
)

$ErrorActionPreference = "Stop"
$compose = "docker-compose.paper.yml"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

function Assert-NativeOk {
    param([string]$Context)
    if ($LASTEXITCODE -ne 0) {
        throw "$Context falhou com exit code $LASTEXITCODE"
    }
}

function Ensure-Containers {
    Write-Step "Garantindo containers paper ativos"
    docker compose -f $compose up -d smartcrypto-bot-paper freqtrade-paper smartcrypto-dashboard-paper
    Assert-NativeOk "docker compose up"
    docker compose -f $compose ps
    Assert-NativeOk "docker compose ps"
}

function Exec-Bot {
    param([string[]]$ArgsList)
    docker compose -f $compose exec -T smartcrypto-bot-paper @ArgsList
    Assert-NativeOk "docker compose exec smartcrypto-bot-paper $($ArgsList -join ' ')"
}

Write-Host ""
Write-Host "==> Fase 14 — Paper Trade Lifecycle + Feedback Sync"

Write-Step "Validando arquivos-base"
$required = @(
    "docker-compose.paper.yml",
    "config\paper_feedback.yml",
    "smartcrypto\data\paper_trade_lifecycle.py",
    "scripts\phase14_preflight.py",
    "scripts\inspect_phase14_open_positions.py",
    "scripts\collect_phase14_closed_feedback.py",
    "scripts\inspect_phase14_outputs.py",
    "scripts\collect_phase14_summary.py"
)

foreach ($file in $required) {
    if (Test-Path $file) {
        Write-Host "[OK] $file"
    } else {
        throw "Arquivo obrigatório ausente: $file"
    }
}

Ensure-Containers

Write-Step "Executando preflight"
Exec-Bot @("python", "/app/scripts/phase14_preflight.py")

Write-Step "Inspecionando posições paper abertas"
Exec-Bot @("python", "/app/scripts/inspect_phase14_open_positions.py")

if ($WaitSeconds -gt 0) {
    Write-Step "Aguardando $WaitSeconds segundos para permitir fechamento natural no Freqtrade"
    Start-Sleep -Seconds $WaitSeconds
}

Write-Step "Coletando trades paper fechados"
Exec-Bot @("python", "/app/scripts/collect_phase14_closed_feedback.py")

Write-Step "Inspecionando saídas da Fase 14"
Exec-Bot @("python", "/app/scripts/inspect_phase14_outputs.py")

if ($RunPhase5) {
    Write-Step "Rodando Fase 5 para importar feedback fechado, se existir"

    if (Test-Path ".\paper_controlado_fase_05\RUN_PHASE5_IMPORT_TRADES.ps1") {
        & ".\paper_controlado_fase_05\RUN_PHASE5_IMPORT_TRADES.ps1"
        & ".\paper_controlado_fase_05\RUN_PHASE5_REBUILD_DATASETS.ps1"
        & ".\paper_controlado_fase_05\RUN_PHASE5_VERIFY_OUTPUTS.ps1"
    } else {
        Write-Host "[PENDENTE] Fase 5 não encontrada. Importação incremental não executada."
    }
}

Write-Step "Coletando evidência Fase 14"

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$evidenceDir = "data\evidence\phase14_$timestamp"

New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
New-Item -ItemType Directory -Force -Path "$evidenceDir\reports" | Out-Null
New-Item -ItemType Directory -Force -Path "$evidenceDir\logs" | Out-Null
New-Item -ItemType Directory -Force -Path "$evidenceDir\signals" | Out-Null

Exec-Bot @("python", "/app/scripts/collect_phase14_summary.py")

docker compose -f $compose ps > "$evidenceDir\docker_ps.txt"
Assert-NativeOk "docker compose ps evidence"
docker compose -f $compose logs --tail=300 freqtrade-paper > "$evidenceDir\logs\freqtrade-paper.log"
Assert-NativeOk "docker compose logs freqtrade-paper"
docker compose -f $compose logs --tail=120 smartcrypto-bot-paper > "$evidenceDir\logs\smartcrypto-bot-paper.log"
Assert-NativeOk "docker compose logs smartcrypto-bot-paper"

Copy-Item "data\reports\phase14_*.json" "$evidenceDir\reports" -ErrorAction SilentlyContinue
Copy-Item "data\freqtrade_signals.json" "$evidenceDir\signals" -ErrorAction SilentlyContinue
Copy-Item "data\runtime\active_freqtrade_signals.json" "$evidenceDir\signals" -ErrorAction SilentlyContinue
Copy-Item "data\trades\freqtrade_paper_trades_raw.parquet" "$evidenceDir" -ErrorAction SilentlyContinue
Copy-Item "data\trades\freqtrade_paper_closed_smartcrypto.csv" "$evidenceDir" -ErrorAction SilentlyContinue

$zipPath = "data\evidence\phase14_$timestamp.zip"
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

Compress-Archive -Path "$evidenceDir\*" -DestinationPath $zipPath -Force

Write-Host ""
Write-Host "Evidência Fase 14 gerada em: $zipPath"
Write-Host ""
Write-Host "Fase 14 concluída."
