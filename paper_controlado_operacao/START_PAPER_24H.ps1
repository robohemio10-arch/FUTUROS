param(
    [switch]$OpenDashboard,
    [int]$SessionHours = 24,
    [int]$SignalRefreshSeconds = 1500,
    [int]$SignalValidityMinutes = 45,
    [int]$FreqtradeProcessingWaitSeconds = 90,
    [int]$FeedbackWaitSeconds = 120,
    [switch]$SkipFeedback
)

$ErrorActionPreference = "Stop"

function Get-ProjectRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path (Join-Path $scriptDir "..")).Path
}

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

function Write-Warn {
    param([string]$Message)
    Write-Host "WARNING: $Message" -ForegroundColor Yellow
}

function Invoke-DockerCompose {
    param([string[]]$Arguments)
    & docker compose -f "docker-compose.paper.yml" @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose falhou com exit code $LASTEXITCODE"
    }
}

function Invoke-SignalRefresh {
    param([int]$ValidityMinutes)

    Write-Step "Renovando sinais ativos sem walk-forward"

    $directArgs = @(
        "compose", "-f", "docker-compose.paper.yml",
        "exec", "-T", "smartcrypto-bot-paper",
        "python", "/app/scripts/phase13_generate_active_signals.py",
        "--force-from-predictions",
        "--validity-minutes", "$ValidityMinutes"
    )

    & docker @directArgs
    if ($LASTEXITCODE -eq 0) {
        return
    }

    Write-Warn "Refresh direto de sinais falhou. Tentando fallback com Hotfix Fase 13 v2."

    $fallback = ".\paper_controlado_hotfix_20260515_v2\RUN_HOTFIX_PHASE13_V2_FIX_AND_VERIFY.ps1"
    if (Test-Path $fallback) {
        & $fallback
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "Fallback Fase 13 v2 retornou exit code $LASTEXITCODE."
        }
        return
    }

    Write-Warn "Fallback Fase 13 v2 não encontrado. Sinais podem expirar antes do próximo ciclo."
}

function Invoke-FeedbackSync {
    param([int]$WaitSeconds)

    if ($SkipFeedback) {
        Write-Warn "Coleta de feedback ignorada por -SkipFeedback."
        return
    }

    Write-Step "Coletando feedback paper e reconstruindo datasets"

    $phase14 = ".\paper_controlado_fase_14\RUN_PHASE14_FULL_FEEDBACK_SYNC.ps1"
    if (-not (Test-Path $phase14)) {
        Write-Warn "Fase 14 não encontrada em $phase14."
        return
    }

    try {
        & $phase14 -WaitSeconds $WaitSeconds -RunPhase5
        return
    } catch {
        Write-Warn "Chamada nomeada da Fase 14 falhou: $($_.Exception.Message)"
    }

    try {
        & $phase14 $WaitSeconds -RunPhase5
        return
    } catch {
        Write-Warn "Chamada posicional da Fase 14 também falhou: $($_.Exception.Message)"
    }

    Write-Warn "Feedback não foi coletado neste ciclo. O paper continua rodando; corrija a Fase 14 se persistir."
}

function Save-SessionState {
    param(
        [string]$SessionId,
        [datetime]$StartedAt,
        [datetime]$EndsAt,
        [string]$Status
    )

    $dir = "data\reports\paper_sessions"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null

    $state = [ordered]@{
        session_id = $SessionId
        status = $Status
        started_at = $StartedAt.ToUniversalTime().ToString("o")
        ends_at = $EndsAt.ToUniversalTime().ToString("o")
        signal_refresh_seconds = $SignalRefreshSeconds
        signal_validity_minutes = $SignalValidityMinutes
        feedback_wait_seconds = $FeedbackWaitSeconds
        updated_at = (Get-Date).ToUniversalTime().ToString("o")
    }

    $json = $state | ConvertTo-Json -Depth 5
    $json | Set-Content -Path (Join-Path $dir "paper_session_current.json") -Encoding UTF8
    $json | Set-Content -Path (Join-Path $dir "paper_session_$SessionId.json") -Encoding UTF8
}

$root = Get-ProjectRoot
Set-Location $root

$sessionId = Get-Date -Format "yyyyMMdd_HHmmss"
$startedAt = Get-Date
$endsAt = $startedAt.AddHours($SessionHours)

Write-Step "Operação paper segura por $SessionHours horas — $sessionId"
Write-Host "Dashboard: http://localhost:8502"
Write-Host "Walk-forward: desativado neste fluxo. Rode Fase 21 manualmente/offline quando necessário."

Save-SessionState -SessionId $sessionId -StartedAt $startedAt -EndsAt $endsAt -Status "starting"

Write-Step "Subindo containers"
Invoke-DockerCompose @("up", "-d")
Invoke-DockerCompose @("ps")

if ($OpenDashboard) {
    Start-Process "http://localhost:8502"
}

Save-SessionState -SessionId $sessionId -StartedAt $startedAt -EndsAt $endsAt -Status "running"

Invoke-SignalRefresh -ValidityMinutes $SignalValidityMinutes
Start-Sleep -Seconds $FreqtradeProcessingWaitSeconds
Invoke-FeedbackSync -WaitSeconds $FeedbackWaitSeconds

while ((Get-Date) -lt $endsAt) {
    Write-Step "Ciclo paper ativo: sinais, feedback e status"

    Invoke-DockerCompose @("up", "-d")
    Invoke-SignalRefresh -ValidityMinutes $SignalValidityMinutes

    Write-Step "Aguardando Freqtrade processar candles/sinais"
    Start-Sleep -Seconds $FreqtradeProcessingWaitSeconds

    Invoke-FeedbackSync -WaitSeconds $FeedbackWaitSeconds
    Invoke-DockerCompose @("ps")

    Save-SessionState -SessionId $sessionId -StartedAt $startedAt -EndsAt $endsAt -Status "running"

    $remaining = [int]([Math]::Max(0, ($endsAt - (Get-Date)).TotalSeconds))
    $sleepSeconds = [Math]::Min($SignalRefreshSeconds, $remaining)

    if ($sleepSeconds -gt 0) {
        Write-Step "Aguardando próximo ciclo por $sleepSeconds segundos"
        Start-Sleep -Seconds $sleepSeconds
    }
}

Write-Step "Sessão paper finalizada — coletando feedback final"
Invoke-FeedbackSync -WaitSeconds $FeedbackWaitSeconds
Save-SessionState -SessionId $sessionId -StartedAt $startedAt -EndsAt $endsAt -Status "finished"

Write-Step "Status final"
Invoke-DockerCompose @("ps")

Write-Host ""
Write-Host "Sessão paper concluída: $sessionId"
Write-Host "Dashboard: http://localhost:8502"
