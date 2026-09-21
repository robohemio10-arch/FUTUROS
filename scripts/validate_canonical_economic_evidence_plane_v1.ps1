param(
    [string]$RuntimeRoot = "E:\FUTUROS"
)

$ErrorActionPreference = "Stop"

$RuntimeDataRoot = Join-Path $RuntimeRoot "data"
$Report = Join-Path $RuntimeDataRoot "reports\canonical_economic_plane\canonical_economic_evidence_plane_v1.json"

Write-Host "=== SERVICE ==="
$Service = @(
    docker ps --format "{{.Names}}|{{.Status}}" |
        Select-String "^futuros-canonical-economic-plane-canonical-economic-evidence-plane-1\|"
)

if ($Service.Count -ne 1) {
    throw "CANONICAL_ECONOMIC_PLANE_SERVICE_NOT_RUNNING"
}
if ($Service[0].Line -notmatch "\(healthy\)") {
    throw "CANONICAL_ECONOMIC_PLANE_SERVICE_NOT_HEALTHY:$($Service[0].Line)"
}
Write-Host $Service[0].Line

if (-not (Test-Path -LiteralPath $Report -PathType Leaf)) {
    throw "CANONICAL_ECONOMIC_PLANE_REPORT_MISSING:$Report"
}

Write-Host "`n=== SUMMARY ==="
$Summary = Get-Content -LiteralPath $Report -Raw | ConvertFrom-Json

$Header = [ordered]@{
    status = $Summary.status
    reason = $Summary.reason
    generated_at_utc = $Summary.generated_at_utc
    stage_count = $Summary.stage_count
    stage_status_counts = $Summary.stage_status_counts
    write_performed = $Summary.write_performed
}
Write-Host ($Header | ConvertTo-Json -Depth 8 -Compress)

foreach ($Property in ($Summary.stages.PSObject.Properties | Sort-Object Name)) {
    $Stage = $Property.Value
    $Row = [ordered]@{
        stage = $Property.Name
        status = $Stage.status
        reason = $Stage.reason
        decision = $Stage.decision
    }
    Write-Host ($Row | ConvertTo-Json -Compress)
}

Write-Host ("TREATMENT=" + ($Summary.canonical_treatment_runtime_overlay | ConvertTo-Json -Depth 10 -Compress))
Write-Host ("BRANCH17_INTERIM=" + ($Summary.branch17_forward_observation | ConvertTo-Json -Depth 10 -Compress))

$Safety = [ordered]@{
    paper_only = $Summary.paper_only
    shadow_only = $Summary.shadow_only
    operational_authority = $Summary.operational_authority
    live_trading_enabled = $Summary.live_trading_enabled
    order_submission_enabled = $Summary.order_submission_enabled
    real_order_submission_enabled = $Summary.real_order_submission_enabled
    exchange_private_access = $Summary.exchange_private_access
}
Write-Host ("SAFETY=" + ($Safety | ConvertTo-Json -Compress))

if ($Summary.status -ne "ok") {
    throw "CANONICAL_ECONOMIC_PLANE_STATUS_NOT_OK:$($Summary.status)"
}
if ([int]$Summary.stage_count -ne 9) {
    throw "CANONICAL_ECONOMIC_PLANE_STAGE_COUNT_INVALID:$($Summary.stage_count)"
}
if ($Summary.write_performed -ne $true) {
    throw "CANONICAL_ECONOMIC_PLANE_WRITE_NOT_PERFORMED"
}
if (
    $Summary.paper_only -ne $true -or
    $Summary.shadow_only -ne $true -or
    $Summary.operational_authority -ne $false -or
    $Summary.live_trading_enabled -ne $false -or
    $Summary.order_submission_enabled -ne $false -or
    $Summary.real_order_submission_enabled -ne $false -or
    $Summary.exchange_private_access -ne $false
) {
    throw "CANONICAL_ECONOMIC_PLANE_SAFETY_INVARIANT_FAILED"
}

$BlockedStages = @(
    $Summary.stages.PSObject.Properties |
        Where-Object { $_.Value.status -eq "blocked" } |
        ForEach-Object { $_.Name }
)
if ($BlockedStages.Count -gt 0) {
    throw "ECONOMIC_PLANE_BLOCKED_STAGES:$($BlockedStages -join ',')"
}

Write-Host "`nCANONICAL_ECONOMIC_PLANE_VALIDATION=PASS"
Write-Host "`n=== GIT ==="
git status -sb
git status --short
