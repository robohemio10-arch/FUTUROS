param(
    [switch]$Down
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))

function Invoke-PaperSessionLockStaleCleanup {
    Write-Host ""
    Write-Host "==> Limpando lock stale de sessão paper, se houver"
    & python -m smartcrypto.runtime.paper_session_lock release `
        --pid $PID `
        --script "STOP_PAPER_SESSION.ps1" `
        --cleanup-stale
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: lock de sessão paper pertence a processo vivo ou outra sessão. Não removido." -ForegroundColor Yellow
    }
}

if ($Down) {
    Write-Host ""
    Write-Host "==> Parando todos os serviços paper"
    docker compose -f docker-compose.paper.yml down --remove-orphans
}
else {
    Write-Host ""
    Write-Host "==> Parando somente freqtrade-paper"
    docker compose -f docker-compose.paper.yml stop freqtrade-paper
    docker compose -f docker-compose.paper.yml ps
}

Invoke-PaperSessionLockStaleCleanup
