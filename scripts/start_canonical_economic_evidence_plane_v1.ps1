param(
    [string]$RuntimeRoot = "E:\FUTUROS",
    [string]$IntervalSeconds = "900",
    [string]$Wq6EvidenceRoot = "E:\FUTUROS_LOCAL_CHECKPOINTS\POST_OCR_QUANT\20260917_BRANCH07_WQ6_MATERIALIZED_EVIDENCE_V1"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Get-Location).Path
$ComposeFile = Join-Path $ProjectRoot "docker-compose.canonical-economic-plane.yml"
$RuntimeDataRoot = Join-Path $RuntimeRoot "data"
$OutputRoot = Join-Path $RuntimeDataRoot "reports\canonical_economic_plane"
$SummaryReport = Join-Path $OutputRoot "canonical_economic_evidence_plane_v1.json"
$TreatmentReport = Join-Path $RuntimeDataRoot "reports\canonical_treatment\economic_monitor_v1.json"

$Wq6Files = @(
    @{
        Name = "official_trades_master_qlib_dataset_v1.parquet"
        Sha256 = "70ccb7f88aa281fc90ebbc77920b89340b688477ec29fbe6a373013d15213973"
    },
    @{
        Name = "official_trades_master_qlib_feature_contract_v1.json"
        Sha256 = "fba5a285dc9ce554c6e0ea8e0be2f3ab1daca38052c4f0355f34f5f0e21550e9"
    },
    @{
        Name = "official_trades_master_qlib_dataset_manifest_v1.json"
        Sha256 = "51d39a61893a9be1a70e8a92ef983b8b2366051ec03c6be96cfb5e3e00044f6e"
    },
    @{
        Name = "official_trades_master_qlib_split_manifest_v1.json"
        Sha256 = "b9d5d4fc82c2dbbb5b7793d6adad357e24d10136dfcdc0c4bae5f96da0d715f6"
    }
)

if (-not (Test-Path -LiteralPath $ComposeFile -PathType Leaf)) {
    throw "COMPOSE_FILE_MISSING:$ComposeFile"
}
if (-not (Test-Path -LiteralPath $RuntimeDataRoot -PathType Container)) {
    throw "RUNTIME_DATA_ROOT_MISSING:$RuntimeDataRoot"
}
if (-not (Test-Path -LiteralPath $TreatmentReport -PathType Leaf)) {
    throw "CANONICAL_TREATMENT_MONITOR_MISSING:$TreatmentReport"
}
if (-not (Test-Path -LiteralPath $Wq6EvidenceRoot -PathType Container)) {
    throw "WQ6_EVIDENCE_ROOT_MISSING:$Wq6EvidenceRoot"
}

Write-Host "=== WQ6 FROZEN EVIDENCE GATE ==="
foreach ($Item in $Wq6Files) {
    $Path = Join-Path $Wq6EvidenceRoot $Item.Name
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "WQ6_EVIDENCE_FILE_MISSING:$Path"
    }

    $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    if ($Actual -ne $Item.Sha256) {
        throw "WQ6_EVIDENCE_HASH_MISMATCH:$($Item.Name):$Actual"
    }
    Write-Host "$($Item.Name)|$Actual|PASS"
}

$TreatmentContainers = @(
    docker ps --format "{{.Names}}|{{.Status}}" |
        Select-String "futuros-canonical-treatment-"
)
if ($TreatmentContainers.Count -lt 3) {
    throw "CANONICAL_TREATMENT_STACK_NOT_FULLY_RUNNING"
}
if ($TreatmentContainers | Where-Object { $_.Line -notmatch "\(healthy\)" }) {
    throw "CANONICAL_TREATMENT_STACK_NOT_HEALTHY"
}

if (-not (Test-Path -LiteralPath ".env" -PathType Leaf)) {
    git check-ignore -q .env
    if ($LASTEXITCODE -ne 0) {
        throw "LOCAL_ENV_NOT_GITIGNORED"
    }
    $RuntimeEnv = Join-Path $RuntimeRoot ".env"
    if (-not (Test-Path -LiteralPath $RuntimeEnv -PathType Leaf)) {
        throw "RUNTIME_ENV_MISSING:$RuntimeEnv"
    }
    Copy-Item -LiteralPath $RuntimeEnv -Destination ".env"
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

$env:FUTUROS_RUNTIME_DATA_ROOT = ($RuntimeDataRoot -replace "\\", "/")
$env:FUTUROS_WQ6_EVIDENCE_ROOT = ($Wq6EvidenceRoot -replace "\\", "/")
$env:FUTUROS_ECONOMIC_PLANE_INTERVAL_SECONDS = $IntervalSeconds

Write-Host "=== CONFIG GATE ==="
docker compose `
    -p futuros-canonical-economic-plane `
    -f $ComposeFile `
    config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "CANONICAL_ECONOMIC_PLANE_CONFIG_FAILED"
}

Write-Host "=== ONE-SHOT PREFLIGHT ==="
docker compose `
    -p futuros-canonical-economic-plane `
    -f $ComposeFile `
    run --rm `
    canonical-economic-evidence-plane `
    python scripts/run_canonical_economic_evidence_plane_v1.py `
    --project-root /app `
    --data-root /app/data `
    --output-dir /app/data/reports/canonical_economic_plane `
    --write `
    --json
if ($LASTEXITCODE -ne 0) {
    throw "CANONICAL_ECONOMIC_PLANE_PREFLIGHT_FAILED"
}

if (-not (Test-Path -LiteralPath $SummaryReport -PathType Leaf)) {
    throw "CANONICAL_ECONOMIC_PLANE_PREFLIGHT_REPORT_MISSING:$SummaryReport"
}

$Preflight = Get-Content -LiteralPath $SummaryReport -Raw | ConvertFrom-Json
if ($Preflight.status -ne "ok") {
    throw "CANONICAL_ECONOMIC_PLANE_PREFLIGHT_STATUS:$($Preflight.status)"
}
if ([int]$Preflight.stage_count -ne 9) {
    throw "CANONICAL_ECONOMIC_PLANE_PREFLIGHT_STAGE_COUNT:$($Preflight.stage_count)"
}

$BlockedStages = @(
    $Preflight.stages.PSObject.Properties |
        Where-Object { $_.Value.status -eq "blocked" } |
        ForEach-Object { $_.Name }
)
if ($BlockedStages.Count -gt 0) {
    throw "ECONOMIC_PLANE_PREFLIGHT_BLOCKED_STAGES:$($BlockedStages -join ',')"
}

Write-Host "PREFLIGHT_STAGE_STATUS_COUNTS=$($Preflight.stage_status_counts | ConvertTo-Json -Compress)"

Write-Host "=== START ==="
docker compose `
    -p futuros-canonical-economic-plane `
    -f $ComposeFile `
    up -d --build
if ($LASTEXITCODE -ne 0) {
    throw "CANONICAL_ECONOMIC_PLANE_START_FAILED"
}

Start-Sleep -Seconds 35

$Service = docker ps --format "{{.Names}}|{{.Status}}" |
    Select-String "futuros-canonical-economic-plane-canonical-economic-evidence-plane-1"

if (-not $Service) {
    throw "CANONICAL_ECONOMIC_PLANE_SERVICE_NOT_RUNNING"
}
if ($Service.Line -notmatch "\(healthy\)") {
    throw "CANONICAL_ECONOMIC_PLANE_SERVICE_NOT_HEALTHY:$($Service.Line)"
}

Write-Host $Service.Line
Write-Host "CANONICAL_ECONOMIC_PLANE_START_GATE=PASS"
Write-Host "PAPER_ONLY=true"
Write-Host "LIVE_ENABLED=false"
Write-Host "ORDER_SUBMISSION_ENABLED=false"
Write-Host "REAL_ORDER_SUBMISSION_ENABLED=false"
