from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.ops.paper_shadow_soak_report import (
    DEFAULT_ANTI_LEAKAGE_REPORT,
    DEFAULT_CRITICAL_ALERTING_REPORT,
    DEFAULT_DATA_QUALITY_REPORT,
    DEFAULT_EVENT_BACKTEST_REPORT,
    DEFAULT_LEDGER_REPORT,
    DEFAULT_MARKET_HEALTH_REPORT,
    DEFAULT_MONTE_CARLO_REPORT,
    DEFAULT_RISK_RECOVERY_REPORT,
    DEFAULT_STATE_RECONCILIATION_REPORT,
    as_bool,
    ensure_utc,
    int_value,
    iso,
    load_source,
    monte_carlo_policy_summary,
    safety_payload,
    status_is_blocked,
    unsafe_safety_flags,
    write_report,
)


DEFAULT_PAPER_SOAK_REPORT = Path("data/reports/paper_soak_report.json")
DEFAULT_RUNTIME_SAFETY_REPORT = Path("data/reports/runtime_safety_config_validation_report.json")
DEFAULT_REPORT_PATH = Path("data/reports/readiness_gate_report.json")
CRITICAL_GATES = (
    "paper_soak_report",
    "runtime_safety_report",
    "critical_alerting_report",
    "risk_recovery_report",
    "market_health_report",
    "state_reconciliation_report",
    "ledger_report",
    "data_quality_report",
    "anti_leakage_report",
    "monte_carlo_report",
    "event_backtest_report",
)


def run_readiness_gate_audit(
    *,
    paper_soak_report: str | Path | None = DEFAULT_PAPER_SOAK_REPORT,
    runtime_safety_report: str | Path | None = DEFAULT_RUNTIME_SAFETY_REPORT,
    critical_alerting_report: str | Path | None = DEFAULT_CRITICAL_ALERTING_REPORT,
    risk_recovery_report: str | Path | None = DEFAULT_RISK_RECOVERY_REPORT,
    market_health_report: str | Path | None = DEFAULT_MARKET_HEALTH_REPORT,
    state_reconciliation_report: str | Path | None = DEFAULT_STATE_RECONCILIATION_REPORT,
    ledger_report: str | Path | None = DEFAULT_LEDGER_REPORT,
    data_quality_report: str | Path | None = DEFAULT_DATA_QUALITY_REPORT,
    anti_leakage_report: str | Path | None = DEFAULT_ANTI_LEAKAGE_REPORT,
    monte_carlo_report: str | Path | None = DEFAULT_MONTE_CARLO_REPORT,
    monte_carlo_risk_budget_policy_report: str | Path | None = None,
    event_backtest_report: str | Path | None = DEFAULT_EVENT_BACKTEST_REPORT,
    report_path: str | Path | None = DEFAULT_REPORT_PATH,
    required_soak_days: int = 7,
    strict: bool = False,
    now: datetime | None = None,
    safety_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    current_time = ensure_utc(now or datetime.now(timezone.utc))
    paths = {
        "paper_soak_report": paper_soak_report,
        "runtime_safety_report": runtime_safety_report,
        "critical_alerting_report": critical_alerting_report,
        "risk_recovery_report": risk_recovery_report,
        "market_health_report": market_health_report,
        "state_reconciliation_report": state_reconciliation_report,
        "ledger_report": ledger_report,
        "data_quality_report": data_quality_report,
        "anti_leakage_report": anti_leakage_report,
        "monte_carlo_report": monte_carlo_report,
        "monte_carlo_risk_budget_policy_report": monte_carlo_risk_budget_policy_report,
        "event_backtest_report": event_backtest_report,
    }
    sources = {name: load_source(path) for name, path in paths.items() if path is not None}
    payloads = {name: source["payload"] for name, source in sources.items()}
    safety = safety_payload(safety_overrides)
    gates = build_gates(payloads, sources, required_soak_days=required_soak_days, strict=strict)
    monte_carlo_policy = monte_carlo_policy_summary(
        payloads.get("monte_carlo_risk_budget_policy_report", {}),
        source_exists=sources.get("monte_carlo_risk_budget_policy_report", {}).get("exists", False),
    )
    blocking_gates = sorted(name for name, gate in gates.items() if gate["status"] == "blocked")
    warning_gates = sorted(name for name, gate in gates.items() if gate["status"] == "warning")
    missing_gates = sorted(name for name, gate in gates.items() if gate["status"] == "missing")
    readiness_blockers = [f"gate_blocked:{name}" for name in blocking_gates]
    readiness_warnings = [f"gate_warning:{name}" for name in warning_gates] + [f"gate_missing:{name}" for name in missing_gates]
    readiness_blockers.extend(f"unsafe_safety_flag:{flag}" for flag in unsafe_safety_flags(safety))
    if monte_carlo_policy["unsafe_findings"]:
        readiness_blockers.append("unsafe_policy_report")
    if monte_carlo_policy["no_trade_policy_present"]:
        readiness_blockers.append("no_trade_policy_active")
        readiness_blockers.append("readiness_may_proceed_false")
    if strict:
        readiness_blockers.extend(f"missing_required_evidence:{name}" for name in missing_gates)
    observed_soak_days = float(payloads["paper_soak_report"].get("soak_days") or payloads["paper_soak_report"].get("paper_days") or 0.0)
    if observed_soak_days < required_soak_days:
        readiness_blockers.append("soak_days_below_required")
    readiness_blockers = sorted(set(readiness_blockers))
    readiness_warnings = sorted(set(readiness_warnings))
    approved = not readiness_blockers and not missing_gates and all(gate["status"] == "ok" for gate in gates.values())
    report = {
        "status": "ok" if approved else "blocked",
        "reason": "readiness_approved" if approved else ";".join(readiness_blockers or readiness_warnings or ["readiness_not_approved"]),
        "generated_at_utc": iso(current_time),
        "readiness_approved": bool(approved),
        "required_soak_days": int(required_soak_days),
        "observed_soak_days": observed_soak_days,
        "gates": gates,
        "blocking_gates": blocking_gates,
        "warning_gates": warning_gates,
        "missing_gates": missing_gates,
        "readiness_blockers": readiness_blockers,
        "readiness_warnings": readiness_warnings,
        "monte_carlo_risk_budget_policy_status": monte_carlo_policy["status"],
        "monte_carlo_risk_budget_policy_action": monte_carlo_policy["policy_action"],
        "monte_carlo_risk_treated": monte_carlo_policy["monte_carlo_risk_treated"]
        and status_is_blocked(payloads["monte_carlo_report"]),
        "no_trade_policy_present": monte_carlo_policy["no_trade_policy_present"],
        "readiness_may_proceed": False if monte_carlo_policy["no_trade_policy_present"] else bool(approved),
        "live_release_allowed": False,
        "next_required_actions": next_required_actions(readiness_blockers, readiness_warnings, missing_gates),
        **safety,
    }
    write_report(report, report_path)
    return report


def build_gates(payloads: dict[str, dict[str, Any]], sources: dict[str, dict[str, Any]], *, required_soak_days: int, strict: bool) -> dict[str, dict[str, Any]]:
    gates = {}
    monte_carlo_policy = monte_carlo_policy_summary(
        payloads.get("monte_carlo_risk_budget_policy_report", {}),
        source_exists=sources.get("monte_carlo_risk_budget_policy_report", {}).get("exists", False),
    )
    for name in CRITICAL_GATES:
        source = sources[name]
        payload = payloads[name]
        if not source["exists"]:
            gates[name] = {"status": "missing", "reason": "missing_source", "path": source["path"]}
            continue
        gate_status = "ok"
        reason = "ok"
        if status_is_blocked(payload):
            gate_status = "blocked"
            reason = f"status_{payload.get('status')}"
            if name == "monte_carlo_report" and monte_carlo_policy["no_trade_policy_present"]:
                reason = "monte_carlo_no_trade_policy_active"
        elif str(payload.get("status", "")).lower() in {"warning", "missing_data"}:
            gate_status = "warning"
            reason = f"status_{payload.get('status')}"
        unsafe_flags = unsafe_safety_flags(payload)
        if unsafe_flags:
            gate_status = "blocked"
            reason = "unsafe_safety_flags:" + ",".join(sorted(unsafe_flags))
        if name == "paper_soak_report":
            if float(payload.get("soak_days") or payload.get("paper_days") or 0.0) < required_soak_days:
                gate_status = "blocked"
                reason = "soak_days_below_required"
            if payload.get("readiness_blockers"):
                gate_status = "blocked"
                reason = "soak_report_blockers"
        if name == "critical_alerting_report" and int_value(payload.get("critical_alerts")) > 0:
            gate_status = "blocked"
            reason = "critical_alerts_gt_0"
        if name == "risk_recovery_report" and str(payload.get("recommended_mode", "")).upper() in {"PANIC", "RECONCILING"}:
            gate_status = "blocked"
            reason = f"risk_mode_{payload.get('recommended_mode')}"
        if name == "state_reconciliation_report" and as_bool(payload.get("reconciliation_required")):
            gate_status = "blocked"
            reason = "reconciliation_required"
        gates[name] = {"status": gate_status, "reason": reason, "path": source["path"]}
    return gates


def next_required_actions(blockers: list[str], warnings: list[str], missing: list[str]) -> list[str]:
    actions: list[str] = []
    if missing:
        actions.append("generate_missing_evidence")
    if any("soak_days_below_required" in item for item in blockers):
        actions.append("continue_paper_shadow_soak")
    if any("reconciliation" in item for item in blockers):
        actions.append("run_state_reconciliation")
    if any("market_health" in item or "stale" in item for item in blockers):
        actions.append("restore_market_data_freshness")
    if blockers:
        actions.append("keep_live_disabled")
    if "no_trade_policy_active" in blockers or any("monte_carlo" in item for item in blockers):
        actions.append("respect_no_trade_policy")
        actions.append("improve_expectancy_before_readiness")
        actions.append("collect_more_paper_shadow_evidence")
    if warnings and not blockers:
        actions.append("review_warnings_before_promotion")
    return sorted(set(actions or ["no_action_required"]))
