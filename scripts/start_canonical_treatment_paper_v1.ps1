param(
    [string]$RuntimeRoot = "E:\FUTUROS",
    [string]$ProjectName = "futuros-canonical-treatment"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ComposeFile = Join-Path $RepoRoot "docker-compose.canonical-treatment.yml"
$RuntimeEnv = Join-Path $RuntimeRoot ".env"
$LocalEnv = Join-Path $RepoRoot ".env"
$Evidence = Join-Path $RuntimeRoot "data\research\qlib_v3\prospective_evidence\qlib-v3-b0b722c25fd961938943af8c\a1756853239b2f52f5604b44ddfb15e6d1355fbfc50533ad046d5f04a5ce9fa9\evidence.json"

if (-not (Test-Path -LiteralPath $ComposeFile -PathType Leaf)) {
    throw "CANONICAL_TREATMENT_COMPOSE_MISSING:$ComposeFile"
}
if (-not (Test-Path -LiteralPath $RuntimeEnv -PathType Leaf)) {
    throw "RUNTIME_ENV_MISSING:$RuntimeEnv"
}
if (-not (Test-Path -LiteralPath $Evidence -PathType Leaf)) {
    throw "V3_EVIDENCE_MISSING:$Evidence"
}

Push-Location $RepoRoot
try {
    git rev-parse --is-inside-work-tree | Out-Null

    if (-not (Test-Path -LiteralPath $LocalEnv -PathType Leaf)) {
        git check-ignore -q .env
        if ($LASTEXITCODE -ne 0) {
            throw "DOTENV_NOT_GITIGNORED_ABORTING"
        }
        Copy-Item -LiteralPath $RuntimeEnv -Destination $LocalEnv
    }

    $ObserverQlib = docker ps --format "{{.Names}}" |
        Where-Object {
            $_ -match "canonical-observer-canonical-qlib-refresh-supervisor-paper"
        } |
        Select-Object -First 1

    $ObserverScheduler = docker ps --format "{{.Names}}" |
        Where-Object {
            $_ -match "canonical-observer-canonical-paper-autolearning-scheduler"
        } |
        Select-Object -First 1

    if (-not $ObserverQlib -or -not $ObserverScheduler) {
        throw "CANONICAL_OBSERVER_NOT_RUNNING"
    }

    $CrosswalkCount = @'
import json
import sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    data = json.load(handle)
print(sum(
    isinstance(row, dict)
    and isinstance(row.get("operational_crosswalk"), dict)
    for row in data.get("signals", [])
))
'@ | python - $Evidence

    if ([int]$CrosswalkCount -lt 1) {
        throw "CANONICAL_TREATMENT_REQUIRES_PROSPECTIVE_CROSSWALK"
    }

    $TreatmentRuntime = Join-Path $RuntimeRoot "data\runtime\canonical_treatment"
    $TreatmentReports = Join-Path $RuntimeRoot "data\reports\canonical_treatment"
    $TreatmentResearch = Join-Path $RuntimeRoot "data\research\canonical_treatment"
    $TreatmentLogs = Join-Path $RuntimeRoot "logs\canonical_treatment"

    foreach ($Path in @(
        $TreatmentRuntime,
        $TreatmentReports,
        $TreatmentResearch,
        $TreatmentLogs
    )) {
        New-Item -ItemType Directory -Force -Path $Path | Out-Null
    }

    $env:FUTUROS_RUNTIME_DATA_ROOT = (
        Join-Path $RuntimeRoot "data"
    ).Replace("\", "/")
    $env:FUTUROS_TREATMENT_RUNTIME_ROOT = $TreatmentRuntime.Replace("\", "/")
    $env:FUTUROS_TREATMENT_LOG_ROOT = $TreatmentLogs.Replace("\", "/")

    docker volume inspect futuros_freqtrade_paper_db | Out-Null

    docker compose `
        -p $ProjectName `
        -f $ComposeFile `
        config --quiet

    Write-Host "CANONICAL_TREATMENT_CONFIG_GATE=PASS"
    Write-Host "V3_CROSSWALK_COUNT=$CrosswalkCount"

    Write-Host "`n=== FREQTRADE CONFIG PREFLIGHT ==="
    docker compose `
        -p $ProjectName `
        -f $ComposeFile `
        run `
        --rm `
        --no-deps `
        freqtrade-canonical-treatment-paper `
        show-config `
        --config /freqtrade/user_data/config.paper.canonical-treatment.json | Out-Null

    if ($LASTEXITCODE -ne 0) {
        throw "FREQTRADE_TREATMENT_CONFIG_PREFLIGHT_FAILED"
    }

    Write-Host "FREQTRADE_TREATMENT_CONFIG_PREFLIGHT=PASS"

    docker compose `
        -p $ProjectName `
        -f $ComposeFile `
        up -d --build

    Start-Sleep -Seconds 75

    docker compose `
        -p $ProjectName `
        -f $ComposeFile `
        ps

    $Running = @(
        docker compose `
            -p $ProjectName `
            -f $ComposeFile `
            ps `
            --status running `
            --services
    )

    foreach ($Required in @(
        "canonical-treatment-signal-publisher",
        "freqtrade-canonical-treatment-paper",
        "canonical-treatment-economic-monitor"
    )) {
        if ($Running -notcontains $Required) {
            docker compose `
                -p $ProjectName `
                -f $ComposeFile `
                logs --tail 160
            throw "CANONICAL_TREATMENT_START_GATE_FAILED:$Required"
        }
    }

    Write-Host "CANONICAL_TREATMENT_START_GATE=PASS"
    Write-Host "PAPER_B_ACTIVE=true"
    Write-Host "LIVE_ENABLED=false"
    Write-Host "ORDER_SUBMISSION_ENABLED=false"
    Write-Host "REAL_ORDER_SUBMISSION_ENABLED=false"
}
finally {
    Pop-Location
}
