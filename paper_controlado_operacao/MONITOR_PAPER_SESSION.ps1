param(
    [switch]$Follow,
    [int]$IntervalSeconds = 60,
    [int]$Tail = 80
)

$ErrorActionPreference = "Stop"

function Get-ProjectRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path (Join-Path $scriptDir "..")).Path
}

function Show-Monitor {
    Clear-Host
    Write-Host "==> SmartCrypto Paper Monitor"
    Write-Host ""

    docker compose -f docker-compose.paper.yml ps

    Write-Host ""
    Write-Host "==> Sessão atual"
    $session = "data\reports\paper_sessions\paper_session_current.json"
    if (Test-Path $session) {
        Get-Content $session -Raw
    } else {
        Write-Host "Nenhuma sessão registrada ainda."
    }

    Write-Host ""
    Write-Host "==> Sinais pinned"
    $pinned = "data\runtime\active_freqtrade_signals.json"
    if (Test-Path $pinned) {
        Get-Content $pinned -Raw | Select-Object -First 1
    } else {
        Write-Host "Arquivo pinned não encontrado."
    }

    Write-Host ""
    Write-Host "==> Últimas decisões da strategy"
    $decisions = "data\runtime\freqtrade_signal_decisions.jsonl"
    if (Test-Path $decisions) {
        Get-Content $decisions -Tail $Tail
    } else {
        Write-Host "Decision log não encontrado."
    }
}

$root = Get-ProjectRoot
Set-Location $root

if ($Follow) {
    while ($true) {
        Show-Monitor
        Start-Sleep -Seconds $IntervalSeconds
    }
}

Show-Monitor
