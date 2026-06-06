from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_REPORT_PATH = Path("data/reports/risk_recovery_mode_audit_report.json")
REPORT_VERSION = "1.0"
MODES = ("NORMAL", "CONSERVATIVE", "PROTECTION", "PANIC", "REDUCE_ONLY", "PAUSED", "RECONCILING")
SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
)
RUNTIME_SOURCE_NAMES = (
    "equity_curve",
    "closed_trades",
    "paper_session_report",
    "market_health_report",
    "readiness_report",
    "monte_carlo_report",
    "backtest_report",
    "kill_switch",
    "incidents",
    "state_divergence_report",
)
OPTIONAL_SOURCE_NAMES = RUNTIME_SOURCE_NAMES


@dataclass(frozen=True)
class RiskRecoveryLimits:
    max_daily_loss_pct: float = 3.0
    max_weekly_loss_pct: float = 7.0
    max_drawdown_pct: float = 10.0
    max_consecutive_losses: int = 4
    required_clean_streak_days: int = 3


def run_risk_recovery_mode_audit(
    *,
    equity_curve_path: str | Path | None = None,
    closed_trades_path: str | Path | None = None,
    paper_session_report_path: str | Path | None = None,
    market_health_report_path: str | Path | None = None,
    readiness_report_path: str | Path | None = None,
    monte_carlo_report_path: str | Path | None = None,
    backtest_report_path: str | Path | None = None,
    kill_switch_path: str | Path | None = None,
    incidents_path: str | Path | None = None,
    state_divergence_report_path: str | Path | None = None,
    report_path: str | Path | None = DEFAULT_REPORT_PATH,
    limits: RiskRecoveryLimits | None = None,
    previous_mode: str = "NORMAL",
    strict: bool = False,
    safety_overrides: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = ensure_utc(now or datetime.now(timezone.utc))
    active_limits = limits or RiskRecoveryLimits()
    safety = safety_payload(safety_overrides)
    sources = load_sources(
        {
            "equity_curve": equity_curve_path,
            "closed_trades": closed_trades_path,
            "paper_session_report": paper_session_report_path,
            "market_health_report": market_health_report_path,
            "readiness_report": readiness_report_path,
            "monte_carlo_report": monte_carlo_report_path,
            "backtest_report": backtest_report_path,
            "kill_switch": kill_switch_path,
            "incidents": incidents_path,
            "state_divergence_report": state_divergence_report_path,
        }
    )
    validation_errors = [f"unsafe_safety_flag:{flag}" for flag in unsafe_safety_flags(safety)]
    missing_sources = [name for name, source in sources.items() if not source["exists"]]
    warnings = [f"missing_optional_source:{name}" for name in missing_sources]
    if strict and not any(source["exists"] for source in sources.values()):
        validation_errors.append("missing_required_input")
    validation_errors.extend(
        f"source_read_failed:{name}" for name, source in sources.items() if source["status"] == "blocked"
    )
    equity = sources["equity_curve"]["frame"]
    closed = sources["closed_trades"]["frame"]
    payloads = {name: source["payload"] for name, source in sources.items()}
    risk_metrics = calculate_risk_metrics(equity, closed, current_time)
    recovery_metrics = calculate_recovery_metrics(payloads, risk_metrics, active_limits)
    finding_groups = evaluate_findings(
        payloads=payloads,
        risk_metrics=risk_metrics,
        limits=active_limits,
        safety_errors=validation_errors,
    )
    blocking_findings = sorted(set(finding_groups["critical"] + finding_groups["financial"] + validation_errors))
    warnings.extend(finding_groups["warnings"])
    recommended_mode, transition_reason = recommend_mode(
        previous_mode=previous_mode,
        critical_findings=finding_groups["critical"] + validation_errors,
        financial_findings=finding_groups["financial"],
        warnings=warnings,
        recovery_metrics=recovery_metrics,
        limits=active_limits,
    )
    status = status_from_findings(blocking_findings, warnings, sources, strict)
    evidence = evidence_quality_summary(
        sources=sources,
        payloads=payloads,
        risk_metrics=risk_metrics,
        recommended_mode=recommended_mode,
        blocking_findings=blocking_findings,
        warnings=warnings,
        strict=strict,
    )
    if status == "ok" and evidence["primary_state"] in {
        "missing_runtime_sources",
        "no_drawdown_state",
        "market_health_ok_but_no_recovery_state",
        "recovery_state_empty",
        "recovery_state_invalid",
    }:
        status = "warning"
    report = {
        "status": status,
        "reason": "ok"
        if status == "ok"
        else ";".join(blocking_findings or [str(evidence["primary_state"])] or warnings or ["missing_data"]),
        "generated_at_utc": utc_timestamp(current_time),
        "report_version": REPORT_VERSION,
        "previous_mode": normalize_mode(previous_mode),
        "recommended_mode": recommended_mode,
        "transition_reason": transition_reason,
        "allowed_actions": allowed_actions(recommended_mode),
        "blocked_actions": blocked_actions(recommended_mode),
        "risk_metrics": risk_metrics,
        "recovery_metrics": recovery_metrics,
        "blocking_findings": blocking_findings,
        "warnings": sorted(set(warnings)),
        "sources": source_summary(sources),
        "missing_sources": missing_sources,
        "optional_sources_missing": [name for name in missing_sources if name in OPTIONAL_SOURCE_NAMES],
        "required_sources_missing": missing_sources if strict else [],
        "evidence_quality_summary": evidence,
        "next_required_actions": next_required_actions(evidence, blocking_findings),
        "limits": asdict(active_limits),
        **safety,
    }
    write_json_if_requested(report, Path(report_path) if report_path is not None else None)
    return report


def load_sources(paths: dict[str, str | Path | None]) -> dict[str, dict[str, Any]]:
    return {name: load_source(name, path) for name, path in paths.items()}


def load_source(name: str, path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {"name": name, "path": None, "exists": False, "status": "missing", "frame": pd.DataFrame(), "payload": {}}
    target = Path(path)
    if not target.exists():
        return {"name": name, "path": str(target), "exists": False, "status": "missing", "frame": pd.DataFrame(), "payload": {}}
    try:
        if target.suffix.lower() in {".parquet", ".csv", ".jsonl"}:
            frame = read_table(target)
            payload = {}
        else:
            payload = read_json(target)
            frame = table_from_payload(payload)
    except Exception as exc:
        return {
            "name": name,
            "path": str(target),
            "exists": True,
            "status": "blocked",
            "frame": pd.DataFrame(),
            "payload": {},
            "error": str(exc),
        }
    return {"name": name, "path": str(target), "exists": True, "status": "ok", "frame": frame, "payload": payload}


def read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".jsonl":
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return pd.DataFrame(rows)
    raise ValueError(f"unsupported_table_format:{suffix}")


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        return {"rows": payload}
    return {}


def table_from_payload(payload: dict[str, Any]) -> pd.DataFrame:
    for key in ("rows", "data", "equity_curve", "closed_trades", "incidents"):
        if isinstance(payload.get(key), list):
            return pd.DataFrame(payload[key])
    return pd.DataFrame([payload]) if payload else pd.DataFrame()


def calculate_risk_metrics(equity: pd.DataFrame, closed_trades: pd.DataFrame, now: datetime) -> dict[str, Any]:
    equity_series = normalize_equity(equity)
    daily_loss_pct = weekly_loss_pct = peak_to_valley = current_drawdown = max_drawdown = 0.0
    if not equity_series.empty:
        values = equity_series["equity"].astype(float)
        timestamps = equity_series["timestamp"]
        first = float(values.iloc[0])
        last = float(values.iloc[-1])
        peak = values.cummax()
        drawdown = ((peak - values) / peak.replace(0, np.nan)) * 100.0
        max_drawdown = float(drawdown.max(skipna=True) or 0.0)
        current_drawdown = float(drawdown.iloc[-1] if len(drawdown) else 0.0)
        peak_to_valley = max_drawdown
        today = timestamps.dt.date == now.date()
        if today.any():
            today_values = values.loc[today]
            if len(today_values) > 1:
                daily_loss_pct = pct_change(today_values.iloc[0], today_values.iloc[-1])
            else:
                day_values = values.loc[timestamps >= (now - pd.Timedelta(days=1))]
                if len(day_values) > 1:
                    daily_loss_pct = pct_change(day_values.iloc[0], day_values.iloc[-1])
        cutoff = now - pd.Timedelta(days=7)
        week_values = values.loc[timestamps >= cutoff]
        if not week_values.empty:
            weekly_loss_pct = pct_change(week_values.iloc[0], week_values.iloc[-1])
        elif first:
            weekly_loss_pct = pct_change(first, last)
    consecutive_losses = calculate_consecutive_losses(closed_trades)
    return {
        "daily_loss_pct": round(float(daily_loss_pct), 6),
        "weekly_loss_pct": round(float(weekly_loss_pct), 6),
        "peak_to_valley_drawdown_pct": round(float(peak_to_valley), 6),
        "current_drawdown_pct": round(float(current_drawdown), 6),
        "max_drawdown_pct": round(float(max_drawdown), 6),
        "consecutive_losses": int(consecutive_losses),
    }


def normalize_equity(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["timestamp", "equity"])
    time_col = first_existing(frame, ("timestamp", "timestamp_utc", "date", "created_at"))
    equity_col = first_existing(frame, ("equity", "balance", "equity_usdt", "portfolio_value"))
    if not time_col or not equity_col:
        return pd.DataFrame(columns=["timestamp", "equity"])
    result = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(frame[time_col], utc=True, errors="coerce"),
            "equity": pd.to_numeric(frame[equity_col], errors="coerce"),
        }
    ).dropna()
    return result.sort_values("timestamp")


def calculate_consecutive_losses(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    pnl_col = first_existing(frame, ("pnl", "pnl_usdt", "profit_abs", "pnl_fechado", "return_pct"))
    if not pnl_col:
        return 0
    values = pd.to_numeric(frame[pnl_col], errors="coerce").fillna(0.0)
    streak = 0
    for value in reversed(values.tolist()):
        if value < 0:
            streak += 1
        else:
            break
    return streak


def calculate_recovery_metrics(
    payloads: dict[str, dict[str, Any]],
    risk_metrics: dict[str, Any],
    limits: RiskRecoveryLimits,
) -> dict[str, Any]:
    session = payloads["paper_session_report"]
    readiness = payloads["readiness_report"]
    clean_streak = int_value(first_present(session, readiness, keys=("clean_streak_days",), default=0))
    recovery_approved = as_bool(first_present(session, readiness, keys=("recovery_approved", "readiness_approved"), default=False))
    drawdown = float(risk_metrics.get("current_drawdown_pct") or 0.0)
    progress = 100.0
    if limits.max_drawdown_pct > 0:
        progress = max(0.0, min(100.0, (1.0 - (drawdown / limits.max_drawdown_pct)) * 100.0))
    return {
        "recovery_progress_pct": round(progress, 6),
        "clean_streak_days": clean_streak,
        "required_clean_streak_days": int(limits.required_clean_streak_days),
        "recovery_approved": recovery_approved,
    }


def evaluate_findings(
    *,
    payloads: dict[str, dict[str, Any]],
    risk_metrics: dict[str, Any],
    limits: RiskRecoveryLimits,
    safety_errors: list[str],
) -> dict[str, list[str]]:
    critical: list[str] = []
    financial: list[str] = []
    warnings: list[str] = []
    if risk_metrics["daily_loss_pct"] <= -abs(limits.max_daily_loss_pct):
        financial.append("daily_loss_limit_exceeded")
    if risk_metrics["weekly_loss_pct"] <= -abs(limits.max_weekly_loss_pct):
        financial.append("weekly_loss_limit_exceeded")
    if risk_metrics["max_drawdown_pct"] >= abs(limits.max_drawdown_pct):
        financial.append("max_drawdown_limit_exceeded")
    if risk_metrics["consecutive_losses"] > int(limits.max_consecutive_losses):
        financial.append("consecutive_losses_limit_exceeded")
    if risk_metrics["current_drawdown_pct"] >= abs(limits.max_drawdown_pct) * 0.5:
        warnings.append("drawdown_warning")

    market = payloads["market_health_report"]
    readiness = payloads["readiness_report"]
    session = payloads["paper_session_report"]
    divergence = payloads["state_divergence_report"]
    kill = payloads["kill_switch"]
    incidents = payloads["incidents"]
    monte = payloads["monte_carlo_report"]
    backtest = payloads["backtest_report"]

    if normalized_status(market.get("status")) == "blocked":
        critical.append("market_health_block")
    elif normalized_status(market.get("status")) == "warning":
        warnings.append("market_health_warning")
    if int_value(market.get("stale_data_count") or (market.get("global_summary") or {}).get("stale_data_count")) > 0:
        critical.append("stale_data_block")
    if as_bool(readiness.get("prediction_stale_block"), default=False) or normalized_status(readiness.get("prediction_freshness")) == "blocked":
        critical.append("prediction_stale_block")
    if normalized_status(readiness.get("status")) == "blocked":
        critical.append("readiness_block")
    if normalized_status(monte.get("status")) == "warning":
        warnings.append("monte_carlo_warning")
    if normalized_status(backtest.get("status")) == "warning":
        warnings.append("backtest_warning")
    for key, finding in (("backup_status", "backup_restore_block"), ("restore_status", "backup_restore_block")):
        if key in session and normalized_status(session.get(key)) not in {"pass", "ok"}:
            critical.append(finding)
    if as_bool(divergence.get("reconciliation_required"), default=False) or int_value(divergence.get("divergence_count")) > 0:
        critical.append("reconciliation_required")
    if as_bool(kill.get("enabled"), default=False) or as_bool(kill.get("active"), default=False) or normalized_status(kill.get("status")) == "active":
        critical.append("kill_switch_active")
    p0 = max(
        int_value(first_present(session, keys=("p0_incidents", "p0"), default=0)),
        int_value(first_present(incidents, keys=("p0_incidents", "p0"), default=0)),
    )
    p1 = max(
        int_value(first_present(session, keys=("p1_incidents", "p1"), default=0)),
        int_value(first_present(incidents, keys=("p1_incidents", "p1"), default=0)),
    )
    if p0 > 0 or p1 > 0:
        critical.append("incident_block")
    if as_bool(session.get("paused"), default=False) or normalized_status(session.get("operator_mode")) == "paused":
        critical.append("paper_session_paused")
    if safety_errors:
        critical.append("unsafe_safety_flags")
    return {"critical": sorted(set(critical)), "financial": sorted(set(financial)), "warnings": sorted(set(warnings))}


def recommend_mode(
    *,
    previous_mode: str,
    critical_findings: list[str],
    financial_findings: list[str],
    warnings: list[str],
    recovery_metrics: dict[str, Any],
    limits: RiskRecoveryLimits,
) -> tuple[str, str]:
    previous = normalize_mode(previous_mode)
    if "paper_session_paused" in critical_findings:
        return "PAUSED", "operator_or_system_paused"
    if "reconciliation_required" in critical_findings:
        return "RECONCILING", "state_divergence_requires_reconciliation"
    if critical_findings:
        return "PANIC", "critical_block_detected"
    if previous == "PANIC":
        if recovery_metrics["recovery_approved"] and recovery_metrics["clean_streak_days"] >= limits.required_clean_streak_days:
            return "CONSERVATIVE", "panic_recovery_approved_to_conservative"
        return "PANIC", "panic_requires_explicit_recovery"
    if financial_findings:
        return "PROTECTION", "financial_limit_exceeded"
    if warnings:
        if all(str(warning).startswith("missing_optional_source:") for warning in warnings):
            return "NORMAL", "missing_optional_sources_no_recovery_activation"
        return "CONSERVATIVE", "warning_conditions_detected"
    return "NORMAL", "normal_conditions"


def status_from_findings(
    blocking_findings: list[str],
    warnings: list[str],
    sources: dict[str, dict[str, Any]],
    strict: bool,
) -> str:
    if blocking_findings:
        return "blocked"
    missing = any(not source["exists"] for source in sources.values())
    if strict and missing:
        return "blocked"
    if missing and warnings:
        return "missing_data"
    if warnings:
        return "warning"
    return "ok"


def allowed_actions(mode: str) -> list[str]:
    base = ["observe", "paper_only_monitor", "shadow_evaluate"]
    if mode in {"NORMAL", "CONSERVATIVE"}:
        return base + ["keep_or_reduce_risk"]
    if mode == "PROTECTION":
        return base + ["reduce_only_conceptual", "pause_new_entries"]
    if mode == "REDUCE_ONLY":
        return base + ["reduce_only_conceptual"]
    if mode == "RECONCILING":
        return ["observe", "reconcile_state_read_only"]
    return ["observe"]


def blocked_actions(mode: str) -> list[str]:
    blocked = [
        "increase_risk",
        "increase_stake",
        "increase_leverage",
        "enable_live",
        "send_order",
        "change_runtime",
        "change_config",
    ]
    if mode in {"PANIC", "PAUSED", "RECONCILING", "PROTECTION"}:
        blocked.append("open_new_position")
    return sorted(set(blocked))


def normalize_mode(value: str | None) -> str:
    text = str(value or "NORMAL").strip().upper()
    return text if text in MODES else "NORMAL"


def source_summary(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        name: {
            "path": source["path"],
            "exists": source["exists"],
            "status": source["status"],
            "rows": int(len(source["frame"])) if isinstance(source.get("frame"), pd.DataFrame) else 0,
            "error": source.get("error"),
        }
        for name, source in sources.items()
    }


def evidence_quality_summary(
    *,
    sources: dict[str, dict[str, Any]],
    payloads: dict[str, dict[str, Any]],
    risk_metrics: dict[str, Any],
    recommended_mode: str,
    blocking_findings: list[str],
    warnings: list[str],
    strict: bool,
) -> dict[str, Any]:
    present = sorted(name for name, source in sources.items() if source["exists"] and source["status"] == "ok")
    missing = sorted(name for name, source in sources.items() if not source["exists"])
    blocked = sorted(name for name, source in sources.items() if source["status"] == "blocked")
    has_drawdown_state = sources["equity_curve"]["exists"] and not sources["equity_curve"]["frame"].empty
    has_closed_trade_state = sources["closed_trades"]["exists"] and not sources["closed_trades"]["frame"].empty
    has_recovery_state = has_drawdown_state or has_closed_trade_state or bool(payloads["paper_session_report"])
    market_health_ok = normalized_status(payloads["market_health_report"].get("status")) == "ok"
    incidents = payloads["incidents"]
    incidents_count = max(
        int_value(first_present(incidents, keys=("p0_incidents", "p0"), default=0)),
        int_value(first_present(incidents, keys=("p1_incidents", "p1"), default=0)),
        int_value(first_present(incidents, keys=("open", "open_incidents"), default=0)),
    )

    if blocked:
        primary_state = "recovery_state_invalid"
    elif not present:
        primary_state = "missing_runtime_sources"
    elif not has_drawdown_state and not has_closed_trade_state:
        primary_state = "market_health_ok_but_no_recovery_state" if market_health_ok else "no_drawdown_state"
    elif recommended_mode == "NORMAL":
        primary_state = "recovery_mode_inactive"
    else:
        primary_state = "recovery_mode_active"

    if present and not has_recovery_state:
        state_detail = "recovery_state_empty"
    elif incidents_count == 0 and sources["incidents"]["exists"]:
        state_detail = "no_incidents_observed"
    else:
        state_detail = primary_state

    complete = (
        primary_state == "recovery_mode_inactive"
        and has_drawdown_state
        and has_closed_trade_state
        and not blocked
        and not blocking_findings
    )
    return {
        "primary_state": primary_state,
        "state_detail": state_detail,
        "operational_evidence_complete": bool(complete),
        "missing_runtime_sources": bool(not present),
        "recovery_policy_present": True,
        "has_drawdown_state": bool(has_drawdown_state),
        "has_closed_trade_state": bool(has_closed_trade_state),
        "has_recovery_state": bool(has_recovery_state),
        "market_health_ok": bool(market_health_ok),
        "no_incidents_observed": bool(incidents_count == 0 and sources["incidents"]["exists"]),
        "recovery_mode_active": recommended_mode != "NORMAL",
        "recovery_mode_inactive": recommended_mode == "NORMAL",
        "required_sources_missing": missing if strict else [],
        "optional_sources_missing": [name for name in missing if name in OPTIONAL_SOURCE_NAMES],
        "missing_sources": missing,
        "blocked_sources": blocked,
        "warnings": sorted(set(warnings)),
        "risk_metrics_available": any(float(risk_metrics.get(key) or 0.0) != 0.0 for key in risk_metrics),
    }


def next_required_actions(evidence: dict[str, Any], blocking_findings: list[str]) -> list[str]:
    actions: list[str] = []
    state = str(evidence.get("primary_state") or "")
    if state == "missing_runtime_sources":
        actions.append("materialize_runtime_reports_before_full_recovery_evidence")
    if state in {"no_drawdown_state", "market_health_ok_but_no_recovery_state"}:
        actions.append("provide_equity_curve_or_closed_trades_snapshot")
    if state == "recovery_state_invalid":
        actions.append("repair_invalid_runtime_recovery_source")
    if evidence.get("no_incidents_observed"):
        actions.append("continue_observing_without_fabricating_incidents")
    if blocking_findings:
        actions.append("resolve_blocking_risk_recovery_findings_before_readiness")
    actions.append("keep_live_and_order_submission_disabled")
    return sorted(set(actions))


def first_existing(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    return next((column for column in candidates if column in frame.columns), None)


def pct_change(first: Any, last: Any) -> float:
    first_value = float(first)
    last_value = float(last)
    if first_value == 0:
        return 0.0
    return ((last_value - first_value) / first_value) * 100.0


def first_present(*payloads: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for payload in payloads:
        for key in keys:
            if payload.get(key) is not None:
                return payload[key]
    return default


def normalized_status(value: Any) -> str:
    if value in (None, ""):
        return "missing"
    text = str(value).strip().lower()
    if text in {"ok", "pass", "passed", "valid", "ready"}:
        return "pass" if text in {"pass", "passed"} else "ok"
    if text in {"blocked", "failed", "error", "invalid"}:
        return "blocked"
    if text in {"warning", "warn", "degraded"}:
        return "warning"
    if text in {"active", "panic"}:
        return "active"
    if text in {"paused", "pause"}:
        return "paused"
    return text


def int_value(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "active", "enabled"}


def safety_payload(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "paper_only": True,
        "shadow_only": True,
        "runtime_mode": "paper",
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }
    if overrides:
        payload.update(overrides)
    return payload


def unsafe_safety_flags(payload: dict[str, Any]) -> list[str]:
    unsafe = []
    if payload.get("paper_only") is not True:
        unsafe.append("paper_only")
    if payload.get("shadow_only") is not True:
        unsafe.append("shadow_only")
    if payload.get("runtime_mode") != "paper":
        unsafe.append("runtime_mode")
    for key in SAFE_FALSE_FLAGS:
        if payload.get(key) is True:
            unsafe.append(key)
    return unsafe


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def utc_timestamp(value: datetime | None = None) -> str:
    return ensure_utc(value or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")


def write_json_if_requested(payload: dict[str, Any], path: Path | None) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, default=str),
        encoding="utf-8",
    )
