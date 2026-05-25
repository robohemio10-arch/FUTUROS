$ErrorActionPreference = "Stop"

$compose = "docker-compose.paper.yml"
$service = "smartcrypto-bot-paper"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$evidenceRoot = "data\evidence\phase5_$timestamp"
$zipPath = "data\evidence\phase5_$timestamp.zip"

function Step($message) {
    Write-Host ""
    Write-Host "==> $message" -ForegroundColor Cyan
}

if (Test-Path $evidenceRoot) {
    Remove-Item -Recurse -Force $evidenceRoot
}
New-Item -ItemType Directory -Force -Path $evidenceRoot | Out-Null
New-Item -ItemType Directory -Force -Path "$evidenceRoot\reports" | Out-Null

Step "Coletando status Docker"
docker compose -f $compose ps | Out-File "$evidenceRoot\docker_ps.txt" -Encoding utf8

Step "Coletando logs"
docker compose -f $compose logs --tail=300 | Out-File "$evidenceRoot\docker_logs_tail_300.txt" -Encoding utf8

Step "Coletando inventário de arquivos"
Get-ChildItem -Recurse data | Select-Object FullName, Length, LastWriteTime |
    ConvertTo-Json -Depth 4 |
    Out-File "$evidenceRoot\file_inventory.json" -Encoding utf8

Step "Coletando resumo Fase 5"
docker compose -f $compose exec -T $service python scripts/collect_phase5_summary.py |
    Out-File "$evidenceRoot\phase5_summary_stdout.json" -Encoding utf8

Step "Coletando checksums"
Get-ChildItem -Recurse data -File |
    Where-Object { $_.FullName -match "trades|features|reports" } |
    ForEach-Object {
        $hash = Get-FileHash $_.FullName -Algorithm SHA256
        [PSCustomObject]@{
            Path = $_.FullName
            Sha256 = $hash.Hash
        }
    } |
    ConvertTo-Json -Depth 4 |
    Out-File "$evidenceRoot\checksums.json" -Encoding utf8

Step "Copiando relatórios"
if (Test-Path "data\reports") {
    Copy-Item "data\reports\phase5_*.json" "$evidenceRoot\reports" -ErrorAction SilentlyContinue
}

Step "Gerando ZIP de evidências"
if (Test-Path $zipPath) {
    Remove-Item -Force $zipPath
}
Compress-Archive -Path $evidenceRoot -DestinationPath $zipPath -Force

Write-Host ""
Write-Host "Evidência gerada em: $zipPath" -ForegroundColor Green
