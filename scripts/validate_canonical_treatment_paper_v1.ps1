param(
    [string]$RuntimeRoot = "E:\FUTUROS",
    [string]$ProjectName = "futuros-canonical-treatment"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ComposeFile = Join-Path $RepoRoot "docker-compose.canonical-treatment.yml"

$TreatmentRuntime = Join-Path $RuntimeRoot "data\runtime\canonical_treatment"
$TreatmentLogs = Join-Path $RuntimeRoot "logs\canonical_treatment"
$PublisherReport = Join-Path $RuntimeRoot "data\reports\canonical_treatment\signal_publisher_report_v1.json"
$MonitorReport = Join-Path $RuntimeRoot "data\reports\canonical_treatment\economic_monitor_v1.json"
$Ledger = Join-Path $RuntimeRoot "data\research\canonical_treatment\decision_ledger_v1.json"

Push-Location $RepoRoot
try {
    $env:FUTUROS_RUNTIME_DATA_ROOT = (
        Join-Path $RuntimeRoot "data"
    ).Replace("\", "/")
    $env:FUTUROS_TREATMENT_RUNTIME_ROOT = $TreatmentRuntime.Replace("\", "/")
    $env:FUTUROS_TREATMENT_LOG_ROOT = $TreatmentLogs.Replace("\", "/")

    Write-Host "`n=== PAPER B SERVICES ==="
    docker compose -p $ProjectName -f $ComposeFile ps

    Write-Host "`n=== PUBLISHER ==="
    if (Test-Path -LiteralPath $PublisherReport) {
        $env:PUBLISHER_REPORT = $PublisherReport
        @'
import json
import os
with open(os.environ["PUBLISHER_REPORT"], "r", encoding="utf-8") as handle:
    d = json.load(handle)
print({
    "status": d.get("status"),
    "reason": d.get("reason"),
    "control_signal_count": d.get("control_signal_count"),
    "scored_signal_count": d.get("scored_signal_count"),
    "selected_signal_count": d.get("selected_signal_count"),
    "abstained_signal_count": d.get("abstained_signal_count"),
    "unmatched_signal_count": d.get("unmatched_signal_count"),
    "cumulative_candidate_decision_count": d.get("cumulative_candidate_decision_count"),
    "cumulative_scored_decision_count": d.get("cumulative_scored_decision_count"),
    "cumulative_selected_decision_count": d.get("cumulative_selected_decision_count"),
    "cumulative_scorer_coverage": d.get("cumulative_scorer_coverage"),
    "generated_at_utc": d.get("generated_at_utc"),
})
'@ | python -
    } else {
        Write-Host "PUBLISHER_REPORT_MISSING"
    }

    Write-Host "`n=== ECONOMIC MONITOR ==="
    if (Test-Path -LiteralPath $MonitorReport) {
        $env:MONITOR_REPORT = $MonitorReport
        @'
import json
import os
with open(os.environ["MONITOR_REPORT"], "r", encoding="utf-8") as handle:
    d = json.load(handle)
print({
    "status": d.get("status"),
    "reason": d.get("reason"),
    "experiment_started_at_utc": d.get("experiment_started_at_utc"),
    "scorer": d.get("scorer"),
    "sample": d.get("sample"),
    "control_metrics": d.get("control_metrics"),
    "treatment_metrics": d.get("treatment_metrics"),
    "paired_uplift": d.get("paired_uplift"),
})
'@ | python -
    } else {
        Write-Host "MONITOR_REPORT_MISSING"
    }

    Write-Host "`n=== LEDGER ==="
    if (Test-Path -LiteralPath $Ledger) {
        $env:TREATMENT_LEDGER = $Ledger
        @'
import json
import os
with open(os.environ["TREATMENT_LEDGER"], "r", encoding="utf-8") as handle:
    d = json.load(handle)
rows = d.get("rows", [])
print({
    "experiment_id": d.get("experiment_id"),
    "started_at_utc": d.get("started_at_utc"),
    "row_count": len(rows),
    "scored": sum(r.get("status") == "scored" for r in rows),
    "selected": sum(r.get("selected") is True for r in rows),
    "abstained": sum(r.get("selected") is False for r in rows),
})
print("RECENT:")
for row in rows[-10:]:
    print({
        "operational_decision_event_id": row.get("operational_decision_event_id"),
        "signal_id": row.get("signal_id"),
        "status": row.get("status"),
        "selected": row.get("selected"),
        "ai_shadow_decision": row.get("ai_shadow_decision"),
        "qlib_score": row.get("qlib_score"),
        "valid_until": row.get("valid_until"),
    })
'@ | python -
    } else {
        Write-Host "TREATMENT_LEDGER_MISSING"
    }

    Write-Host "`n=== SAFETY / DRY RUN ==="
    $Treatment = docker ps --format "{{.Names}}" |
        Where-Object { $_ -match "freqtrade-canonical-treatment-paper" } |
        Select-Object -First 1

    if (-not $Treatment) {
        throw "PAPER_B_FREQTRADE_NOT_RUNNING"
    }

    @'
import json
p="/freqtrade/user_data/config.paper.canonical-treatment.json"
d=json.load(open(p, encoding="utf-8"))
print({
    "dry_run": d.get("dry_run"),
    "bot_name": d.get("bot_name"),
    "stake_amount": d.get("stake_amount"),
    "max_open_trades": d.get("max_open_trades"),
    "pair_whitelist": (d.get("exchange") or {}).get("pair_whitelist"),
})
'@ | docker exec -i $Treatment python -

    docker exec $Treatment printenv LIVE_ENABLED
    docker exec $Treatment printenv ORDER_SUBMISSION_ENABLED
    docker exec $Treatment printenv REAL_ORDER_SUBMISSION_ENABLED

    Write-Host "`n=== GIT ==="
    git status -sb
    git status --short
}
finally {
    Pop-Location
}
