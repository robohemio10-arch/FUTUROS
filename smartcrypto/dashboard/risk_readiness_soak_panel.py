from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.ops.paper_shadow_soak_report import monte_carlo_policy_summary


DEFAULT_PAPER_SOAK_REPORT_PATH = Path("data/reports/paper_soak_report.json")
DEFAULT_PAPER_SESSION_REPORT_PATH = Path("data/reports/paper_session_report.json")
DEFAULT_AI_GOVERNANCE_REPORT_PATH = Path("data/reports/ai_governance_dashboard_sources_report.json")
DEFAULT_DATA_QUALITY_REPORT_PATH = Path("data/reports/data_quality_report.json")
DEFAULT_DATASET_MANIFEST_PATH = Path("data/reports/dataset_manifest.json")
DEFAULT_ANTI_LEAKAGE_REPORT_PATH = Path("data/reports/phase23_anti_leakage_report.json")
DEFAULT_MONTE_CARLO_REPORT_PATH = Path("data/reports/monte_carlo_risk_simulation_report.json")
DEFAULT_MONTE_CARLO_RISK_BUDGET_POLICY_REPORT_PATH = Path("data/reports/monte_carlo_risk_budget_policy_report.json")
DEFAULT_BACKTEST_REPORT_PATH = Path("data/reports/event_driven_backtest_report.json")
DEFAULT_READINESS_SNAPSHOT_V2_PATH = Path("data/reports/readiness_snapshot_v2.json")
DEFAULT_KILL_SWITCH_PATH = Path("data/runtime/kill_switch.json")
DEFAULT_ACTIVE_SIGNALS_PATH = Path("data/runtime/active_freqtrade_signals.json")
DEFAULT_SIGNAL_DECISIONS_PATH = Path("data/runtime/freqtrade_signal_decisions.jsonl")

DEFAULT_SOURCES = {
    "paper_soak_report": DEFAULT_PAPER_SOAK_REPORT_PATH,
    "paper_session_report": DEFAULT_PAPER_SESSION_REPORT_PATH,
    "ai_governance_report": DEFAULT_AI_GOVERNANCE_REPORT_PATH,
    "data_quality_report": DEFAULT_DATA_QUALITY_REPORT_PATH,
    "dataset_manifest": DEFAULT_DATASET_MANIFEST_PATH,
    "anti_leakage_report": DEFAULT_ANTI_LEAKAGE_REPORT_PATH,
    "monte_carlo_report": DEFAULT_MONTE_CARLO_REPORT_PATH,
    "monte_carlo_risk_budget_policy_report": DEFAULT_MONTE_CARLO_RISK_BUDGET_POLICY_REPORT_PATH,
    "backtest_report": DEFAULT_BACKTEST_REPORT_PATH,
    "readiness_snapshot_v2": DEFAULT_READINESS_SNAPSHOT_V2_PATH,
    "kill_switch": DEFAULT_KILL_SWITCH_PATH,
    "active_signals": DEFAULT_ACTIVE_SIGNALS_PATH,
    "signal_decisions": DEFAULT_SIGNAL_DECISIONS_PATH,
}

SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
)
OPERATIONAL_FALSE_FLAGS = (
    "sends_orders",
    "changes_risk",
)
RUNTIME_MODE_LABELS = (
    "PAPER",
    "SHADOW",
    "LIVE_LOCKED",
    "PANIC",
    "RECONCILING",
    "STALE_DATA",
    "MISSING_DATA",
)
FORBIDDEN_ACTION_LABELS = (
    "disable kill switch",
    "change risk",
    "start bot",
    "stop bot",
    "promote model",
)
PASS_VALUES = {"pass", "passed", "ok", "valid", "ready"}
BLOCKED_STATUSES = {"blocked", "error", "failed", "invalid"}
WARNING_STATUSES = {"warning", "warn", "degraded"}
OPTIONAL_SOURCE_NAMES = {"monte_carlo_risk_budget_policy_report", "readiness_snapshot_v2"}


def load_risk_readiness_soak_state(
    *,
    source_paths: dict[str, str | Path | None] | None = None,
    required_paper_days: int = 7,
    max_stale_signal_age_seconds: int = 900,
    strict: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = ensure_utc(now or datetime.now(timezone.utc))
    paths = build_source_paths(source_paths)
    sources = {name: load_source(name, path) for name, path in paths.items()}
    payloads = {name: source.get("payload") or {} for name, source in sources.items()}
    monte_carlo_policy = monte_carlo_policy_summary(
        payloads.get("monte_carlo_risk_budget_policy_report", {}),
        source_exists=sources.get("monte_carlo_risk_budget_policy_report", {}).get("exists", False),
    )
    signal_decisions = sources["signal_decisions"].get("rows") or []
    safety_flags = collect_safety_flags(payloads)
    soak = summarize_soak(payloads["paper_soak_report"], required_paper_days=required_paper_days)
    session = summarize_session(payloads["paper_session_report"])
    kill_switch = summarize_kill_switch(payloads["kill_switch"], sources["kill_switch"])
    active_signal_freshness = latest_json_timestamp_details(payloads["active_signals"], current_time)
    active_signal_age = active_signal_freshness["age_seconds"]
    latest_shadow_decision_age = latest_jsonl_timestamp_age(signal_decisions, current_time)
    stale_data_count = int_value(
        first_present(
            payloads["paper_soak_report"],
            ("stale_data_count", "stale_sources_count"),
            default=0,
        )
    )
    if active_signal_age is None:
        stale_data_count += 1
    elif active_signal_age > max_stale_signal_age_seconds:
        stale_data_count += 1
    stale_source_details = build_stale_source_details(
        payloads=payloads,
        sources=sources,
        active_signal_freshness=active_signal_freshness,
        max_stale_signal_age_seconds=max_stale_signal_age_seconds,
    )
    runtime_modes = classify_runtime_modes(
        safety_flags=safety_flags,
        kill_switch=kill_switch,
        missing_sources=missing_sources(sources),
        stale_data_count=stale_data_count,
        divergence_count=soak["divergence_count"],
    )
    blocked_reasons = aggregate_blockers(
        safety_flags=safety_flags,
        soak=soak,
        session=session,
        kill_switch=kill_switch,
        stale_data_count=stale_data_count,
        required_paper_days=required_paper_days,
        payloads=payloads,
        sources=sources,
        monte_carlo_policy=monte_carlo_policy,
    )
    warnings = aggregate_warnings(
        sources=sources,
        soak=soak,
        latest_signal_age_seconds=active_signal_age,
        latest_shadow_decision_age_seconds=latest_shadow_decision_age,
        max_stale_signal_age_seconds=max_stale_signal_age_seconds,
        stale_data_count=stale_data_count,
    )
    status = aggregate_status(blocked_reasons, warnings, sources, strict=strict)
    paper_days_observed = soak["paper_days"]
    paper_days_required = required_paper_days
    paper_days_remaining = max(float(required_paper_days) - float(soak["paper_days"]), 0.0)
    no_trade_present = monte_carlo_policy["no_trade_policy_present"]
    return {
        "status": status,
        "reason": "ok" if status == "ok" else ";".join(blocked_reasons or warnings or ["missing_data"]),
        "generated_at_utc": utc_timestamp(current_time),
        "sources": sources,
        "runtime_modes": runtime_modes,
        "runtime_mode": primary_runtime_mode(runtime_modes),
        "paper_only": safety_flags["paper_only"],
        "shadow_only": safety_flags["shadow_only"],
        "live_trading_enabled": safety_flags["live_trading_enabled"],
        "order_submission_enabled": safety_flags["order_submission_enabled"],
        "real_order_submission_enabled": safety_flags["real_order_submission_enabled"],
        "exchange_private_access": safety_flags["exchange_private_access"],
        "paper_days": soak["paper_days"],
        "paper_days_observed": paper_days_observed,
        "required_paper_days": required_paper_days,
        "paper_days_required": paper_days_required,
        "remaining_paper_days": paper_days_remaining,
        "paper_days_remaining": paper_days_remaining,
        "freqtrade_paper_db_selected": soak["freqtrade_paper_db_selected"],
        "freqtrade_paper_db_stale_candidates": soak["freqtrade_paper_db_stale_candidates"],
        "clean_streak_days": soak["clean_streak_days"],
        "duplicate_orders_count": soak["duplicate_orders_count"],
        "unknown_state_count": soak["unknown_state_count"],
        "divergence_count": soak["divergence_count"],
        "stale_data_count": stale_data_count,
        "stale_sources": [item["source"] for item in stale_source_details],
        "stale_source_details": stale_source_details,
        "stale_data_limit_seconds": int(max_stale_signal_age_seconds),
        "shadow_order_attempts": soak["shadow_order_attempts"],
        "controlled_live_attempts": soak["controlled_live_attempts"],
        "kill_switch_status": kill_switch["status"],
        "kill_switch": kill_switch,
        "backup_status": session["backup_status"],
        "restore_status": session["restore_status"],
        "offsite_status": session["offsite_status"],
        "external_copy_status": session["external_copy_status"],
        "latest_signal_age_seconds": active_signal_age,
        "latest_shadow_decision_age_seconds": latest_shadow_decision_age,
        "open_incidents": session["open_incidents"],
        "p0_incidents": session["p0_incidents"],
        "p1_incidents": session["p1_incidents"],
        "readiness_approved": status == "ok",
        "readiness_blockers": blocked_reasons,
        "readiness_warnings": warnings,
        "monte_carlo_risk_budget_policy_status": monte_carlo_policy["status"],
        "monte_carlo_risk_budget_policy_action": monte_carlo_policy["policy_action"],
        "monte_carlo_risk_treated": monte_carlo_policy["monte_carlo_risk_treated"]
        and normalize_status(payloads["monte_carlo_report"].get("status")) == "blocked",
        "no_trade_policy_present": no_trade_present,
        "readiness_may_proceed": False if no_trade_present else status == "ok",
        "live_release_allowed": False,
        "readiness_snapshot_v2_status": normalize_status(payloads["readiness_snapshot_v2"].get("status")),
        "readiness_snapshot_v2": {
            "exists": sources["readiness_snapshot_v2"]["exists"],
            "status": normalize_status(payloads["readiness_snapshot_v2"].get("status")),
            "path": sources["readiness_snapshot_v2"]["path"],
            "observed_soak_days": payloads["readiness_snapshot_v2"].get("observed_soak_days"),
            "readiness_soak_reached": payloads["readiness_snapshot_v2"].get("readiness_soak_reached"),
            "live_release_allowed": False,
        },
        "no_trade_exit_requirements": no_trade_exit_requirements(
            observed_days=paper_days_observed,
            required_days=paper_days_required,
            no_trade_policy_present=no_trade_present,
        ),
        "next_collection_targets": next_collection_targets(
            remaining_days=paper_days_remaining,
            missing=missing_sources(sources),
            stale_data_count=stale_data_count,
            no_trade_policy_present=no_trade_present,
        ),
        "safety_flags": safety_flags,
        "is_read_only": True,
        "read_only": True,
        "forbidden_actions_present": [],
        "max_stale_signal_age_seconds": int(max_stale_signal_age_seconds),
        "missing_sources": sorted(missing_sources(sources)),
    }


def build_source_paths(overrides: dict[str, str | Path | None] | None) -> dict[str, Path]:
    paths = dict(DEFAULT_SOURCES)
    for key, value in (overrides or {}).items():
        if (key in paths or key in OPTIONAL_SOURCE_NAMES) and value is not None:
            paths[key] = Path(value)
    return {key: Path(value) for key, value in paths.items()}


def load_source(name: str, path: Path) -> dict[str, Any]:
    if name == "signal_decisions":
        return load_jsonl_source(name, path)
    return load_json_source(name, path)


def load_json_source(name: str, path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"name": name, "path": str(path), "exists": False, "status": "missing", "payload": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception as exc:
        return {
            "name": name,
            "path": str(path),
            "exists": True,
            "status": "blocked",
            "payload": {},
            "error": f"json_read_failed:{exc}",
        }
    if not isinstance(payload, dict):
        return {
            "name": name,
            "path": str(path),
            "exists": True,
            "status": "blocked",
            "payload": {},
            "error": "json_root_not_object",
        }
    return {"name": name, "path": str(path), "exists": True, "status": "ok", "payload": payload}


def load_jsonl_source(name: str, path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"name": name, "path": str(path), "exists": False, "status": "missing", "rows": []}
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception as exc:
        return {
            "name": name,
            "path": str(path),
            "exists": True,
            "status": "blocked",
            "rows": [],
            "error": f"jsonl_read_failed:{exc}",
        }
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return {"name": name, "path": str(path), "exists": True, "status": "ok", "rows": rows}


def summarize_soak(payload: dict[str, Any], *, required_paper_days: int) -> dict[str, Any]:
    paper_days = float_value(
        first_present(
            payload,
            ("observed_soak_days", "paper_days", "soak_days", "paper_runtime_days"),
            default=0,
        )
    )
    stale_candidates = payload.get("freqtrade_paper_db_stale_candidates")
    return {
        "paper_days": paper_days,
        "required_paper_days": int_value(first_present(payload, ("required_paper_days",), default=required_paper_days)),
        "clean_streak_days": int_value(first_present(payload, ("clean_streak_days", "clean_days"), default=0)),
        "duplicate_orders_count": int_value(first_present(payload, ("duplicate_orders_count", "duplicate_orders"), default=0)),
        "unknown_state_count": int_value(first_present(payload, ("unknown_state_count", "unknown_states"), default=0)),
        "divergence_count": int_value(first_present(payload, ("divergence_count", "divergences"), default=0)),
        "shadow_order_attempts": int_value(first_present(payload, ("shadow_order_attempts",), default=0)),
        "controlled_live_attempts": int_value(first_present(payload, ("controlled_live_attempts",), default=0)),
        "freqtrade_paper_db_selected": payload.get("freqtrade_paper_db_selected"),
        "freqtrade_paper_db_stale_candidates": stale_candidates
        if isinstance(stale_candidates, list)
        else [],
    }


def summarize_session(payload: dict[str, Any]) -> dict[str, Any]:
    incidents = payload.get("incidents") if isinstance(payload.get("incidents"), dict) else {}
    return {
        "backup_status": normalize_status(first_present(payload, ("backup_status",), default="missing")),
        "restore_status": normalize_status(first_present(payload, ("restore_status",), default="missing")),
        "offsite_status": normalize_status(first_present(payload, ("offsite_status",), default="missing")),
        "external_copy_status": normalize_status(first_present(payload, ("external_copy_status",), default="missing")),
        "open_incidents": int_value(first_present(payload, ("open_incidents",), default=incidents.get("open", 0))),
        "p0_incidents": int_value(first_present(payload, ("p0_incidents",), default=incidents.get("p0", 0))),
        "p1_incidents": int_value(first_present(payload, ("p1_incidents",), default=incidents.get("p1", 0))),
    }


def summarize_kill_switch(payload: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    if source.get("status") == "missing":
        return {"status": "missing", "active": False, "classification_clear": False, "payload": payload}
    status = normalize_status(payload.get("status") or payload.get("classification") or payload.get("state"))
    enabled = as_bool(payload.get("enabled"), default=False) or as_bool(payload.get("active"), default=False)
    classification_clear = bool(payload.get("status") or payload.get("classification") or payload.get("label") or payload.get("reason"))
    if enabled and not classification_clear:
        status = "active_unclear"
    elif enabled and status in {"missing", "ok"}:
        status = "active"
    elif not enabled and status == "missing":
        status = "inactive"
    return {
        "status": status,
        "active": enabled,
        "classification_clear": classification_clear,
        "payload": payload,
    }


def collect_safety_flags(payloads: dict[str, dict[str, Any]]) -> dict[str, Any]:
    flags: dict[str, Any] = {
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }
    for payload in payloads.values():
        merge_safety_flags(flags, payload)
    return flags


def merge_safety_flags(flags: dict[str, Any], payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        return
    safety = payload.get("safety_status") if isinstance(payload.get("safety_status"), dict) else {}
    for key in ("paper_only", "shadow_only"):
        if payload.get(key) is False or safety.get(key) is False:
            flags[key] = False
    for key in (*SAFE_FALSE_FLAGS, *OPERATIONAL_FALSE_FLAGS):
        if payload.get(key) is True or safety.get(key) is True:
            flags[key] = True
    for value in payload.values():
        if isinstance(value, dict):
            merge_safety_flags(flags, value)


def aggregate_blockers(
    *,
    safety_flags: dict[str, Any],
    soak: dict[str, int],
    session: dict[str, Any],
    kill_switch: dict[str, Any],
    stale_data_count: int,
    required_paper_days: int,
    payloads: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    monte_carlo_policy: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if safety_flags.get("paper_only") is not True:
        blockers.append("paper_only_not_true")
    if safety_flags.get("shadow_only") is not True:
        blockers.append("shadow_only_not_true")
    for key in SAFE_FALSE_FLAGS:
        if safety_flags.get(key) is True:
            blockers.append(f"{key}_true")
    if soak["shadow_order_attempts"] > 0:
        blockers.append("shadow_order_attempts_gt_0")
    if soak["controlled_live_attempts"] > 0:
        blockers.append("controlled_live_attempts_gt_0")
    for key in ("duplicate_orders_count", "unknown_state_count", "divergence_count"):
        if soak[key] > 0:
            blockers.append(f"{key}_gt_0")
    if session["p0_incidents"] > 0:
        blockers.append("p0_incidents_gt_0")
    if session["p1_incidents"] > 0:
        blockers.append("p1_incidents_gt_0")
    if sources["kill_switch"]["exists"] and kill_switch["active"] and not kill_switch["classification_clear"]:
        blockers.append("kill_switch_active_without_clear_classification")
    if sources["paper_session_report"]["exists"]:
        for key in ("backup_status", "restore_status"):
            value = session[key]
            if value not in PASS_VALUES:
                blockers.append(f"{key}_not_pass")
    if sources["paper_soak_report"]["exists"] and soak["paper_days"] < int(required_paper_days):
        blockers.append("paper_days_below_required")
    if sources["active_signals"]["exists"] and stale_data_count > 0:
        blockers.append("stale_data_count_above_limit")
    if monte_carlo_policy["unsafe_findings"]:
        blockers.append("unsafe_policy_report")
    if monte_carlo_policy["no_trade_policy_present"]:
        blockers.append("monte_carlo_no_trade_policy_active")
    for name, payload in payloads.items():
        if name in OPTIONAL_SOURCE_NAMES:
            continue
        if name == "monte_carlo_report" and normalize_status(payload.get("status")) == "blocked" and monte_carlo_policy["no_trade_policy_present"]:
            continue
        if name == "monte_carlo_risk_budget_policy_report" and normalize_status(payload.get("status")) == "blocked" and monte_carlo_policy["no_trade_policy_present"]:
            continue
        if name == "paper_soak_report" and normalize_status(payload.get("status")) == "blocked" and paper_soak_policy_or_duration_blocked(payload):
            continue
        if normalize_status(payload.get("status")) in BLOCKED_STATUSES:
            blockers.append(f"artifact_status_blocked:{name}")
    return sorted(set(blockers))


def no_trade_exit_requirements(*, observed_days: int, required_days: int, no_trade_policy_present: bool) -> list[str]:
    requirements = [
        f"paper_soak_days:{observed_days}_of_{required_days}",
        "expectancy_non_negative_or_positive",
        "profit_factor_above_minimum",
        "risk_of_ruin_within_policy_cap",
        "fresh_market_and_runtime_evidence",
        "manual_go_no_go_required_before_live",
    ]
    if no_trade_policy_present:
        requirements.insert(1, "monte_carlo_policy_action_not_no_trade")
    return requirements


def next_collection_targets(
    *,
    remaining_days: float,
    missing: set[str],
    stale_data_count: int,
    no_trade_policy_present: bool,
) -> list[str]:
    targets: list[str] = []
    if remaining_days > 0:
        targets.append(f"collect_paper_shadow_soak_days:{format_number(remaining_days)}")
    if missing:
        targets.append("refresh_missing_runtime_sources")
    if stale_data_count > 0:
        targets.append("refresh_market_data_and_active_signals")
    if no_trade_policy_present:
        targets.append("collect_financial_outcomes_for_expectancy_profit_factor_risk_of_ruin")
    targets.append("keep_live_release_disabled")
    return sorted(set(targets))


def aggregate_warnings(
    *,
    sources: dict[str, dict[str, Any]],
    soak: dict[str, int],
    latest_signal_age_seconds: int | None,
    latest_shadow_decision_age_seconds: int | None,
    max_stale_signal_age_seconds: int,
    stale_data_count: int,
) -> list[str]:
    warnings = [f"missing_source:{name}" for name, source in sources.items() if source["status"] == "missing" and name not in OPTIONAL_SOURCE_NAMES]
    if soak["clean_streak_days"] < soak["required_paper_days"]:
        warnings.append("clean_streak_below_required")
    if latest_signal_age_seconds is None or latest_signal_age_seconds > max_stale_signal_age_seconds:
        warnings.append("stale_or_missing_active_signal")
    if latest_shadow_decision_age_seconds is None:
        warnings.append("missing_recent_shadow_decision")
    if soak["paper_days"] < soak["required_paper_days"]:
        warnings.append("soak_partial")
    if soak["freqtrade_paper_db_stale_candidates"]:
        warnings.append("freqtrade_paper_db_stale_candidates")
    if stale_data_count > 0:
        warnings.append("stale_data_detected")
    return sorted(set(warnings))


def aggregate_status(
    blockers: list[str],
    warnings: list[str],
    sources: dict[str, dict[str, Any]],
    *,
    strict: bool,
) -> str:
    if blockers:
        return "blocked"
    has_missing = any(source["status"] == "missing" for name, source in sources.items() if name not in OPTIONAL_SOURCE_NAMES)
    if strict and has_missing:
        return "blocked"
    if has_missing and warnings:
        return "missing_data"
    if warnings:
        return "warning"
    return "ok"


def classify_runtime_modes(
    *,
    safety_flags: dict[str, Any],
    kill_switch: dict[str, Any],
    missing_sources: set[str],
    stale_data_count: int,
    divergence_count: int,
) -> list[str]:
    modes = ["PAPER"]
    if safety_flags.get("shadow_only") is True:
        modes.append("SHADOW")
    if safety_flags.get("live_trading_enabled") is False:
        modes.append("LIVE_LOCKED")
    if kill_switch["active"]:
        modes.append("PANIC")
    if divergence_count > 0:
        modes.append("RECONCILING")
    if stale_data_count > 0:
        modes.append("STALE_DATA")
    if missing_sources:
        modes.append("MISSING_DATA")
    return [mode for mode in RUNTIME_MODE_LABELS if mode in set(modes)]


def primary_runtime_mode(modes: list[str]) -> str:
    for mode in ("PANIC", "RECONCILING", "STALE_DATA", "MISSING_DATA", "PAPER"):
        if mode in modes:
            return mode
    return "PAPER"


def missing_sources(sources: dict[str, dict[str, Any]]) -> set[str]:
    return {name for name, source in sources.items() if source["status"] == "missing" and name not in OPTIONAL_SOURCE_NAMES}


def paper_soak_policy_or_duration_blocked(payload: dict[str, Any]) -> bool:
    blockers = payload.get("readiness_blockers")
    if not isinstance(blockers, list):
        return False
    allowed = {"monte_carlo_no_trade_policy_active", "no_trade_policy_active", "soak_days_below_required"}
    return bool(blockers) and all(str(item) in allowed for item in blockers)


def first_present(payload: dict[str, Any], keys: tuple[str, ...], *, default: Any = None) -> Any:
    for key in keys:
        if payload.get(key) is not None:
            return payload[key]
    return default


def latest_json_timestamp_age(payload: dict[str, Any], now: datetime) -> int | None:
    return latest_json_timestamp_details(payload, now)["age_seconds"]


def latest_json_timestamp_details(payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    candidates: list[Any] = []
    for key in ("generated_at_utc", "generated_at", "updated_at_utc", "created_at", "timestamp", "valid_until"):
        if payload.get(key) is not None:
            candidates.append(payload[key])
    if isinstance(payload.get("signals"), list):
        for item in payload["signals"]:
            if isinstance(item, dict):
                candidates.extend(item.get(key) for key in ("generated_at_utc", "generated_at", "created_at", "timestamp") if item.get(key) is not None)
    latest = latest_time(candidates)
    return {
        "timestamp_utc": utc_timestamp(latest) if latest is not None else None,
        "age_seconds": int(max((now - latest).total_seconds(), 0)) if latest is not None else None,
    }


def latest_jsonl_timestamp_age(rows: list[dict[str, Any]], now: datetime) -> int | None:
    candidates: list[Any] = []
    for row in rows:
        candidates.extend(row.get(key) for key in ("generated_at_utc", "created_at", "timestamp", "created_at_utc") if row.get(key) is not None)
    return latest_age(candidates, now)


def latest_age(values: list[Any], now: datetime) -> int | None:
    latest = latest_time(values)
    if latest is None:
        return None
    return int(max((now - latest).total_seconds(), 0))


def latest_time(values: list[Any]) -> datetime | None:
    parsed = [parse_time(value) for value in values]
    valid = [value for value in parsed if value is not None]
    return max(valid) if valid else None


def build_stale_source_details(
    *,
    payloads: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    active_signal_freshness: dict[str, Any],
    max_stale_signal_age_seconds: int,
) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    soak = payloads["paper_soak_report"]
    raw_sources = soak.get("stale_source_details") or soak.get("stale_sources")
    if isinstance(raw_sources, list):
        for item in raw_sources:
            if isinstance(item, dict):
                details.append(
                    {
                        "source": str(item.get("source") or item.get("name") or "paper_soak_report"),
                        "timestamp_utc": item.get("timestamp_utc") or item.get("last_seen_utc") or item.get("generated_at_utc"),
                        "age_seconds": int_value(item.get("age_seconds") or item.get("last_age_seconds")),
                        "limit_seconds": int_value(item.get("limit_seconds") or max_stale_signal_age_seconds),
                    }
                )
            else:
                details.append(
                    {
                        "source": str(item),
                        "timestamp_utc": None,
                        "age_seconds": None,
                        "limit_seconds": int(max_stale_signal_age_seconds),
                    }
                )
    elif int_value(first_present(soak, ("stale_data_count", "stale_sources_count"), default=0)) > 0:
        details.append(
            {
                "source": "paper_soak_report",
                "timestamp_utc": soak.get("generated_at_utc") or soak.get("created_at_utc"),
                "age_seconds": int_value(soak.get("stale_data_count")),
                "limit_seconds": int(max_stale_signal_age_seconds),
            }
        )
    if not sources["active_signals"]["exists"]:
        details.append(
            {
                "source": "active_signals",
                "timestamp_utc": None,
                "age_seconds": None,
                "limit_seconds": int(max_stale_signal_age_seconds),
                "reason": "missing_source",
            }
        )
    elif active_signal_freshness["age_seconds"] is None or active_signal_freshness["age_seconds"] > max_stale_signal_age_seconds:
        details.append(
            {
                "source": "active_signals",
                "timestamp_utc": active_signal_freshness["timestamp_utc"],
                "age_seconds": active_signal_freshness["age_seconds"],
                "limit_seconds": int(max_stale_signal_age_seconds),
                "reason": "stale" if active_signal_freshness["age_seconds"] is not None else "missing_timestamp",
            }
        )
    seen: set[tuple[Any, Any]] = set()
    unique = []
    for item in details:
        key = (item.get("source"), item.get("timestamp_utc"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return ensure_utc(parsed)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_status(value: Any) -> str:
    if value in (None, ""):
        return "missing"
    text = str(value).strip().lower()
    if text in PASS_VALUES:
        return "pass" if text in {"pass", "passed"} else "ok"
    if text in BLOCKED_STATUSES:
        return "blocked"
    if text in WARNING_STATUSES:
        return "warning"
    if text in {"inactive", "clear", "disabled"}:
        return "inactive"
    if text in {"active", "panic"}:
        return "active"
    return text


def int_value(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def format_number(value: float | int) -> str:
    numeric = float(value)
    return str(int(numeric)) if numeric.is_integer() else str(round(numeric, 6))


def as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "active", "enabled"}


def utc_timestamp(value: datetime | None = None) -> str:
    return ensure_utc(value or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")


def render_risk_readiness_soak_panel(st_module: Any, state: dict[str, Any] | None = None) -> None:
    state = state or load_risk_readiness_soak_state()
    st_module.subheader("Readiness Operacional e Soak Paper/Shadow")
    st_module.caption("Painel read-only. Nao altera risco, nao aciona bot e nao envia ordens.")
    if state["status"] == "blocked":
        st_module.error({"status": state["status"], "blockers": state["readiness_blockers"]})
    elif state["status"] in {"warning", "missing_data"}:
        st_module.warning({"status": state["status"], "warnings": state["readiness_warnings"]})
    else:
        st_module.success("Readiness paper/shadow sem bloqueios críticos.")
    c1, c2, c3, c4 = st_module.columns(4)
    c1.metric("Status", state["status"])
    c2.metric("Runtime", state["runtime_mode"])
    c3.metric("Paper days", state["paper_days"])
    c4.metric("Remaining", state["remaining_paper_days"])
    st_module.write(
        {
            "runtime_modes": state["runtime_modes"],
            "paper_only": state["paper_only"],
            "shadow_only": state["shadow_only"],
            "live_trading_enabled": state["live_trading_enabled"],
            "order_submission_enabled": state["order_submission_enabled"],
            "real_order_submission_enabled": state["real_order_submission_enabled"],
            "exchange_private_access": state["exchange_private_access"],
            "kill_switch_status": state["kill_switch_status"],
            "backup_status": state["backup_status"],
            "restore_status": state["restore_status"],
            "offsite_status": state["offsite_status"],
            "external_copy_status": state["external_copy_status"],
            "latest_signal_age_seconds": state["latest_signal_age_seconds"],
            "latest_shadow_decision_age_seconds": state["latest_shadow_decision_age_seconds"],
            "readiness_approved": state["readiness_approved"],
            "readiness_snapshot_v2_status": state["readiness_snapshot_v2_status"],
        }
    )
    st_module.subheader("Blockers")
    st_module.json(state["readiness_blockers"])
    st_module.subheader("Warnings")
    st_module.json(state["readiness_warnings"])
    st_module.subheader("Fontes")
    st_module.json({name: {"path": source["path"], "exists": source["exists"], "status": source["status"]} for name, source in state["sources"].items()})
