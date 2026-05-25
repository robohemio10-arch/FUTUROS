param(
    [switch]$Down
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))

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
