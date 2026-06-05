from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


DEFAULT_FINANCIAL_EVENT_LOG = Path("data/reports/financial_event_log.jsonl")
DEFAULT_CRITICAL_ALERTING_REPORT = Path("data/reports/critical_alerting_report.json")
DEFAULT_RISK_RECOVERY_REPORT = Path("data/reports/risk_recovery_mode_audit_report.json")
DEFAULT_MARKET_HEALTH_REPORT = Path("data/reports/market_data_health_audit_report.json")
DEFAULT_STATE_RECONCILIATION_REPORT = Path("data/reports/state_reconciliation_audit_report.json")
DEFAULT_LEDGER_REPORT = Path("data/reports/order_intent_capital_ledger_audit_report.json")
DEFAULT_AI_GOVERNANCE_REPORT = Path("data/reports/ai_governance_dashboard_sources_report.json")
DEFAULT_RISK_READINESS_REPORT = Path("data/reports/risk_readiness_soak_dashboard_sources_report.json")
DEFAULT_DRIFT_REPORT = Path("data/reports/ai_shadow_drift_monitor_report.json")
DEFAULT_FINANCIAL_THRESHOLD_REPORT = Path("data/reports/ai_shadow_financial_threshold_evaluation_report.json")
DEFAULT_ANTI_LEAKAGE_REPORT = Path("data/reports/phase23_anti_leakage_report.json")
DEFAULT_MONTE_CARLO_REPORT = Path("data/reports/monte_carlo_risk_simulation_report.json")
DEFAULT_MONTE_CARLO_RISK_BUDGET_POLICY_REPORT = Path("data/reports/monte_carlo_risk_budget_policy_report.json")
DEFAULT_EVENT_BACKTEST_REPORT = Path("data/reports/event_driven_backtest_report.json")
DEFAULT_DATA_QUALITY_REPORT = Path("data/reports/data_quality_report.json")
DEFAULT_DATASET_MANIFEST = Path("data/reports/dataset_manifest.json")
DEFAULT_REPORT_PATH = Path("data/reports/paper_soak_report.json")
SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
)
REQUIRED_SOURCE_NAMES = (
    "critical_alerting_report",
    "risk_recovery_report",
    "market_health_report",
    "state_reconciliation_report",
    "ledger_report",
    "anti_leakage_report",
    "monte_carlo_report",
    "event_backtest_report",
    "data_quality_report",
)


def build_paper_shadow_soak_report(
    *,
    financial_event_log: str | Path | None = DEFAULT_FINANCIAL_EVENT_LOG,
    critical_alerting_report: str | Path | None = DEFAULT_CRITICAL_ALERTING_REPORT,
    risk_recovery_report: str | Path | None = DEFAULT_RISK_RECOVERY_REPORT,
    market_health_report: str | Path | None = DEFAULT_MARKET_HEALTH_REPORT,
    state_reconciliation_report: str | Path | None = DEFAULT_STATE_RECONCILIATION_REPORT,
    ledger_report: str | Path | None = DEFAULT_LEDGER_REPORT,
    ai_governance_report: str | Path | None = DEFAULT_AI_GOVERNANCE_REPORT,
    risk_readiness_report: str | Path | None = DEFAULT_RISK_READINESS_REPORT,
    drift_report: str | Path | None = DEFAULT_DRIFT_REPORT,
    financial_threshold_report: str | Path | None = DEFAULT_FINANCIAL_THRESHOLD_REPORT,
    anti_leakage_report: str | Path | None = DEFAULT_ANTI_LEAKAGE_REPORT,
    monte_carlo_report: str | Path | None = DEFAULT_MONTE_CARLO_REPORT,
    monte_carlo_risk_budget_policy_report: str | Path | None = None,
    event_backtest_report: str | Path | None = DEFAULT_EVENT_BACKTEST_REPORT,
    data_quality_report: str | Path | None = DEFAULT_DATA_QUALITY_REPORT,
    dataset_manifest: str | Path | None = DEFAULT_DATASET_MANIFEST,
    report_path: str | Path | None = DEFAULT_REPORT_PATH,
    required_soak_days: int = 7,
    strict: bool = False,
    now: datetime | None = None,
    safety_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    current_time = ensure_utc(now or datetime.now(timezone.utc))
    paths = {
        "financial_event_log": financial_event_log,
        "critical_alerting_report": critical_alerting_report,
        "risk_recovery_report": risk_recovery_report,
        "market_health_report": market_health_report,
        "state_reconciliation_report": state_reconciliation_report,
        "ledger_report": ledger_report,
        "ai_governance_report": ai_governance_report,
        "risk_readiness_report": risk_readiness_report,
        "drift_report": drift_report,
        "financial_threshold_report": financial_threshold_report,
        "anti_leakage_report": anti_leakage_report,
        "monte_carlo_report": monte_carlo_report,
        "monte_carlo_risk_budget_policy_report": monte_carlo_risk_budget_policy_report,
        "event_backtest_report": event_backtest_report,
        "data_quality_report": data_quality_report,
        "dataset_manifest": dataset_manifest,
    }
    sources = {
        name: load_source(path, jsonl=name == "financial_event_log")
        for name, path in paths.items()
        if path is not None
    }
    payloads = {name: source["payload"] for name, source in sources.items()}
    events = sources["financial_event_log"]["rows"]
    times = [parse_utc(first_present(event, "occurred_at_utc", "created_at_utc", "timestamp_utc", "generated_at_utc")) for event in events]
    times = [value for value in times if value is not None]
    source_times = [
        parse_utc(first_present(payload, "generated_at_utc", "created_at_utc", "soak_start_utc"))
        for payload in payloads.values()
        if isinstance(payload, dict)
    ]
    times.extend(value for value in source_times if value is not None)
    soak_start = min(times) if times else None
    soak_end = current_time if times else None
    soak_days = round(((soak_end - soak_start).total_seconds() / 86400.0), 6) if soak_start and soak_end else 0.0
    safety = safety_payload(safety_overrides)
    monte_carlo_policy = monte_carlo_policy_summary(
        payloads.get("monte_carlo_risk_budget_policy_report", {}),
        source_exists=sources.get("monte_carlo_risk_budget_policy_report", {}).get("exists", False),
    )
    report = {
        "status": "ok",
        "reason": "paper_shadow_soak_ok",
        "generated_at_utc": iso(current_time),
        "soak_start_utc": iso(soak_start) if soak_start else None,
        "soak_end_utc": iso(soak_end) if soak_end else None,
        "soak_days": soak_days,
        "required_soak_days": int(required_soak_days),
        "remaining_soak_days": max(0.0, round(float(required_soak_days) - soak_days, 6)),
        "clean_streak_days": int_value(first_present(payloads["risk_readiness_report"], "clean_streak_days", default=0)),
        "paper_events_count": count_events(events, "paper"),
        "shadow_events_count": count_events(events, "shadow"),
        "total_decisions": count_decisions(events),
        "total_shadow_decisions": count_shadow_decisions(events),
        "total_simulated_order_intents": int_value(payloads["ledger_report"].get("order_intents_count")),
        "duplicate_order_intents": int_value(payloads["ledger_report"].get("duplicate_idempotency_key_count")),
        "duplicate_client_order_ids": int_value(payloads["ledger_report"].get("duplicate_client_order_id_count")),
        "dispatch_unknown_count": int_value(payloads["ledger_report"].get("dispatch_unknown_count")),
        "reconciliation_required_count": 1 if as_bool(payloads["state_reconciliation_report"].get("reconciliation_required")) else 0,
        "state_divergence_count": int_value(payloads["state_reconciliation_report"].get("state_divergence_count")),
        "stale_data_count": int_value(first_present(payloads["market_health_report"], "stale_data_count", "stale_count", default=0)),
        "high_spread_blocks": int_value(first_present(payloads["market_health_report"], "high_spread_blocks", "spread_blocked_count", default=0)),
        "low_liquidity_blocks": int_value(first_present(payloads["market_health_report"], "low_liquidity_blocks", "liquidity_blocked_count", default=0)),
        "latency_blocks": int_value(first_present(payloads["market_health_report"], "latency_blocks", "latency_blocked_count", default=0)),
        "drift_blocks": 1 if status_is_blocked(payloads["drift_report"]) else int_value(payloads["drift_report"].get("drift_blocks")),
        "risk_blocks": len(list_value(payloads["risk_recovery_report"].get("blocking_findings"))),
        "kill_switch_events": count_event_type(events, "kill_switch_triggered"),
        "p0_incidents": int_value(first_present(payloads["risk_readiness_report"], "p0_incidents", default=0)),
        "p1_incidents": int_value(first_present(payloads["risk_readiness_report"], "p1_incidents", default=0)),
        "p2_incidents": int_value(first_present(payloads["risk_readiness_report"], "p2_incidents", default=0)),
        "backup_failures": count_event_type(events, "backup_failed") + int_value(payloads["critical_alerting_report"].get("backup_failures")),
        "restore_failures": count_event_type(events, "restore_failed") + int_value(payloads["critical_alerting_report"].get("restore_failures")),
        "restart_drill_status": str(payloads["risk_readiness_report"].get("restart_drill_status", "missing")),
        "kill_switch_drill_status": str(payloads["risk_readiness_report"].get("kill_switch_drill_status", "missing")),
        "api_timeout_drill_status": str(payloads["risk_readiness_report"].get("api_timeout_drill_status", "missing")),
        "partial_fill_drill_status": str(payloads["risk_readiness_report"].get("partial_fill_drill_status", "missing")),
        "flash_crash_drill_status": str(payloads["risk_readiness_report"].get("flash_crash_drill_status", "missing")),
        "paper_pnl_net": float_value(first_present(payloads["financial_threshold_report"], "paper_pnl_net", "total_pnl", default=0.0)),
        "shadow_pnl_net": float_value(first_present(payloads["financial_threshold_report"], "shadow_pnl_net", "shadow_total_pnl", default=0.0)),
        "max_drawdown_pct": float_value(first_present(payloads["risk_recovery_report"], "max_drawdown_pct", default=nested(payloads["risk_recovery_report"], "risk_metrics", "max_drawdown_pct", default=0.0))),
        "monte_carlo_status": str(payloads["monte_carlo_report"].get("status", "missing")),
        "monte_carlo_risk_budget_policy_status": monte_carlo_policy["status"],
        "monte_carlo_risk_budget_policy_action": monte_carlo_policy["policy_action"],
        "monte_carlo_risk_treated": monte_carlo_policy["monte_carlo_risk_treated"] and status_is_blocked(payloads["monte_carlo_report"]),
        "no_trade_policy_present": monte_carlo_policy["no_trade_policy_present"],
        "monte_carlo_risk_budget_policy_active": monte_carlo_policy["no_trade_policy_present"],
        "monte_carlo_policy_unsafe_findings": monte_carlo_policy["unsafe_findings"],
        "readiness_may_proceed": False if monte_carlo_policy["no_trade_policy_present"] else None,
        "live_release_allowed": False,
        "event_backtest_status": str(payloads["event_backtest_report"].get("status", "missing")),
        "data_quality_status": str(payloads["data_quality_report"].get("status", "missing")),
        "readiness_status": "unknown",
        "readiness_blockers": [],
        "readiness_warnings": [],
        "missing_sources": [name for name, source in sources.items() if not source["exists"]],
        "sources": {name: {"path": source["path"], "exists": source["exists"], "status": source["status"]} for name, source in sources.items()},
        **safety,
    }
    blockers, warnings = evaluate_soak_blockers(report, payloads, sources, strict)
    report["readiness_blockers"] = sorted(set(blockers))
    report["readiness_warnings"] = sorted(set(warnings))
    if report["readiness_blockers"]:
        report["status"] = "insufficient_soak" if only_insufficient_soak(report["readiness_blockers"]) else "blocked"
        report["reason"] = ";".join(report["readiness_blockers"])
    elif report["readiness_warnings"]:
        report["status"] = "missing_data" if report["missing_sources"] else "warning"
        report["reason"] = ";".join(report["readiness_warnings"])
    else:
        report["status"] = "ok"
        report["reason"] = "paper_shadow_soak_ok"
    report["readiness_status"] = report["status"]
    write_report(report, report_path)
    return report


def evaluate_soak_blockers(report: dict[str, Any], payloads: dict[str, dict[str, Any]], sources: dict[str, dict[str, Any]], strict: bool) -> tuple[list[str], list[str]]:
    blockers = [f"unsafe_safety_flag:{flag}" for flag in unsafe_safety_flags(report)]
    warnings = [f"missing_source:{name}" for name in report["missing_sources"]]
    monte_carlo_policy = monte_carlo_policy_summary(
        payloads.get("monte_carlo_risk_budget_policy_report", {}),
        source_exists=sources.get("monte_carlo_risk_budget_policy_report", {}).get("exists", False),
    )
    monte_carlo_treated = monte_carlo_policy["monte_carlo_risk_treated"] and status_is_blocked(payloads["monte_carlo_report"])
    if monte_carlo_policy["unsafe_findings"]:
        blockers.append("unsafe_policy_report")
    for source_name, payload in payloads.items():
        blockers.extend(f"unsafe_source_safety_flag:{source_name}:{flag}" for flag in unsafe_safety_flags(payload))
    for name in REQUIRED_SOURCE_NAMES:
        if strict and not sources[name]["exists"]:
            blockers.append(f"missing_required_evidence:{name}")
    if strict and report["soak_days"] < report["required_soak_days"]:
        blockers.append("soak_days_below_required")
    elif report["soak_days"] < report["required_soak_days"]:
        warnings.append("soak_days_below_required")
    numeric_blocks = {
        "p0_incidents": "p0_incidents_gt_0",
        "p1_incidents": "p1_incidents_gt_0",
        "duplicate_client_order_ids": "duplicate_client_order_id_gt_0",
        "duplicate_order_intents": "duplicate_order_intents_gt_0",
        "dispatch_unknown_count": "dispatch_unknown_gt_0",
        "reconciliation_required_count": "reconciliation_required_gt_0",
        "state_divergence_count": "state_divergence_gt_0",
    }
    for key, reason in numeric_blocks.items():
        if int_value(report.get(key)) > 0:
            blockers.append(reason)
    if critical_alert_count(payloads["critical_alerting_report"]) > 0:
        blockers.append("critical_alerts_gt_0")
    if status_is_blocked(payloads["market_health_report"]):
        blockers.append("market_data_health_blocked")
    if str(payloads["risk_recovery_report"].get("recommended_mode", "")).upper() in {"PANIC", "RECONCILING"}:
        blockers.append(f"risk_recovery_mode_{payloads['risk_recovery_report'].get('recommended_mode')}".lower())
    for source_name, reason in (
        ("data_quality_report", "data_quality_blocked"),
        ("anti_leakage_report", "anti_leakage_blocked"),
        ("event_backtest_report", "event_driven_backtest_blocked"),
        ("ledger_report", "ledger_audit_blocked"),
    ):
        if status_is_blocked(payloads[source_name]):
            blockers.append(reason)
    if status_is_blocked(payloads["monte_carlo_report"]):
        if monte_carlo_treated:
            blockers.append("monte_carlo_no_trade_policy_active")
        else:
            blockers.append("monte_carlo_blocked")
    if int_value(report.get("stale_data_count")) > 0:
        blockers.append("stale_data_count_gt_0")
    for key in ("high_spread_blocks", "low_liquidity_blocks", "latency_blocks", "drift_blocks", "backup_failures", "restore_failures"):
        if int_value(report.get(key)) > 0:
            blockers.append(f"{key}_gt_0")
    return blockers, warnings


def load_source(path: str | Path | None, *, jsonl: bool = False) -> dict[str, Any]:
    if path is None:
        return {"path": None, "exists": False, "status": "missing", "payload": {}, "rows": []}
    target = Path(path)
    if not target.exists():
        return {"path": str(target), "exists": False, "status": "missing", "payload": {}, "rows": []}
    try:
        if jsonl:
            rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
            return {"path": str(target), "exists": True, "status": "ok", "payload": {"rows": rows}, "rows": rows}
        payload = json.loads(target.read_text(encoding="utf-8") or "{}")
        if isinstance(payload, list):
            payload = {"rows": payload}
        if not isinstance(payload, dict):
            payload = {}
    except Exception as exc:
        return {"path": str(target), "exists": True, "status": "blocked", "payload": {"status": "blocked", "error": str(exc)}, "rows": []}
    return {"path": str(target), "exists": True, "status": str(payload.get("status", "ok")), "payload": payload, "rows": list_value(payload.get("rows"))}


def monte_carlo_policy_summary(payload: Mapping[str, Any], *, source_exists: bool) -> dict[str, Any]:
    if not source_exists:
        return {
            "exists": False,
            "status": "missing",
            "policy_action": None,
            "no_trade_policy_present": False,
            "monte_carlo_risk_treated": False,
            "readiness_may_proceed": None,
            "live_release_allowed": False,
            "unsafe_findings": [],
        }
    if not isinstance(payload, Mapping) or not payload:
        return {
            "exists": True,
            "status": "invalid",
            "policy_action": None,
            "no_trade_policy_present": False,
            "monte_carlo_risk_treated": False,
            "readiness_may_proceed": None,
            "live_release_allowed": False,
            "unsafe_findings": ["invalid_policy_report"],
        }
    unsafe = unsafe_safety_flags(payload)
    if payload.get("live_release_allowed") is True:
        unsafe.append("live_release_allowed")
    unsafe = sorted(set(unsafe))
    action = str(payload.get("policy_action", "")).strip().lower() or None
    status = str(payload.get("risk_budget_status") or payload.get("status") or "missing").strip().lower()
    no_trade = bool(action == "no_trade" and not unsafe)
    return {
        "exists": True,
        "status": status,
        "policy_action": action,
        "no_trade_policy_present": no_trade,
        "monte_carlo_risk_treated": no_trade,
        "readiness_may_proceed": as_bool(payload.get("readiness_may_proceed")),
        "live_release_allowed": as_bool(payload.get("live_release_allowed")),
        "unsafe_findings": unsafe,
    }


def safety_payload(overrides: Mapping[str, Any] | None = None) -> dict[str, bool]:
    payload = {"paper_only": True, "shadow_only": True, "live_trading_enabled": False, "order_submission_enabled": False, "real_order_submission_enabled": False, "exchange_private_access": False, "sends_orders": False, "changes_risk": False}
    if overrides:
        payload.update({key: bool(value) for key, value in overrides.items() if key in payload})
    return payload


def unsafe_safety_flags(payload: Mapping[str, Any]) -> list[str]:
    unsafe = []
    if "paper_only" in payload and payload.get("paper_only") is not True:
        unsafe.append("paper_only")
    if "shadow_only" in payload and payload.get("shadow_only") is not True:
        unsafe.append("shadow_only")
    for flag in SAFE_FALSE_FLAGS:
        if flag in payload and payload.get(flag):
            unsafe.append(flag)
    return unsafe


def write_report(report: dict[str, Any], report_path: str | Path | None) -> None:
    if report_path is None:
        return
    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return ensure_utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def first_present(payload: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if isinstance(payload, Mapping) and payload.get(key) is not None:
            return payload[key]
    return default


def nested(payload: Mapping[str, Any], section: str, key: str, default: Any = None) -> Any:
    value = payload.get(section)
    return value.get(key, default) if isinstance(value, Mapping) else default


def int_value(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def status_is_blocked(payload: Mapping[str, Any]) -> bool:
    return str(payload.get("status", "")).lower() in {"blocked", "critical", "failed"}


def critical_alert_count(payload: Mapping[str, Any]) -> int:
    return int_value(first_present(payload, "critical_alerts", "critical_alert_count", "critical_alerts_count", default=0)) + len(list_value(payload.get("critical_findings")))


def count_event_type(events: list[dict[str, Any]], event_type: str) -> int:
    return sum(1 for event in events if event.get("event_type") == event_type)


def count_events(events: list[dict[str, Any]], mode: str) -> int:
    return sum(1 for event in events if event.get(f"{mode}_only") is True or str(event.get("runtime_mode", "")).lower() == mode)


def count_decisions(events: list[dict[str, Any]]) -> int:
    return sum(1 for event in events if "decision" in json.dumps(event, ensure_ascii=False).lower() or str(event.get("event_type", "")).startswith("signal_"))


def count_shadow_decisions(events: list[dict[str, Any]]) -> int:
    return sum(1 for event in events if "shadow" in json.dumps(event, ensure_ascii=False).lower())


def only_insufficient_soak(blockers: list[str]) -> bool:
    return bool(blockers) and set(blockers) == {"soak_days_below_required"}
