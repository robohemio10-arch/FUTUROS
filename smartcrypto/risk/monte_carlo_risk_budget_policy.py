from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_MONTE_CARLO_REPORT = Path("data/reports/monte_carlo_risk_simulation_report.json")
DEFAULT_POLICY_REPORT = Path("data/reports/monte_carlo_risk_budget_policy_report.json")
REPORT_VERSION = "1.0"
SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
)


def build_monte_carlo_risk_budget_policy(
    *,
    monte_carlo_report: str | Path = DEFAULT_MONTE_CARLO_REPORT,
    output: str | Path | None = DEFAULT_POLICY_REPORT,
    risk_of_ruin_cap: float = 0.05,
    max_drawdown_cap_pct: float = 40.0,
    min_profit_factor: float = 1.1,
    min_expectancy: float = 0.0,
    initial_capital: float | None = None,
    current_stake: float | None = None,
    current_leverage: float | None = None,
    strict: bool = False,
    safety_overrides: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    source = Path(monte_carlo_report)
    target = Path(output) if output is not None else None
    generated_at = utc_timestamp(now)
    safety = safety_payload(safety_overrides)
    if not source.exists():
        report = base_report(
            status="blocked",
            reason="missing_monte_carlo_report",
            generated_at_utc=generated_at,
            source=source,
            target=target,
            risk_of_ruin_cap=risk_of_ruin_cap,
            max_drawdown_cap_pct=max_drawdown_cap_pct,
            min_profit_factor=min_profit_factor,
            min_expectancy=min_expectancy,
            initial_capital=initial_capital,
            current_stake=current_stake,
            current_leverage=current_leverage,
            safety=safety,
        )
        report["blocking_findings"] = ["missing_monte_carlo_report"]
        write_report(report, target)
        return report

    try:
        monte_carlo = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        report = base_report(
            status="blocked",
            reason=f"invalid_monte_carlo_report:{type(exc).__name__}",
            generated_at_utc=generated_at,
            source=source,
            target=target,
            risk_of_ruin_cap=risk_of_ruin_cap,
            max_drawdown_cap_pct=max_drawdown_cap_pct,
            min_profit_factor=min_profit_factor,
            min_expectancy=min_expectancy,
            initial_capital=initial_capital,
            current_stake=current_stake,
            current_leverage=current_leverage,
            safety=safety,
        )
        report["blocking_findings"] = ["invalid_monte_carlo_report"]
        write_report(report, target)
        return report

    metrics = extract_metrics(monte_carlo)
    parameters = monte_carlo.get("simulation_parameters") if isinstance(monte_carlo.get("simulation_parameters"), dict) else {}
    resolved_initial_capital = numeric_or_default(initial_capital, metrics.get("initial_capital"), parameters.get("initial_capital"), 0.0)
    resolved_current_stake = numeric_or_default(current_stake, metrics.get("stake"), parameters.get("stake"), 0.0)
    resolved_current_leverage = numeric_or_default(current_leverage, metrics.get("leverage"), parameters.get("leverage"), 0.0)

    blocking_findings: list[str] = [f"unsafe_safety_flag:{flag}" for flag in unsafe_safety_flags(safety)]
    warnings: list[str] = []
    required_actions: list[str] = []
    missing = missing_required_metrics(metrics)
    if missing:
        blocking_findings.append(f"missing_monte_carlo_metrics:{','.join(missing)}")
        required_actions.append("rerun_monte_carlo_risk_simulation_with_required_metrics")

    risk_of_ruin = safe_float(metrics.get("risk_of_ruin"))
    expectancy = safe_float(metrics.get("expectancy_per_trade"))
    profit_factor = safe_float(metrics.get("simulated_profit_factor"))
    probability_of_loss = safe_float(metrics.get("probability_of_loss"))
    drawdown = safe_float(metrics.get("p95_max_drawdown_pct"))

    if expectancy is not None and expectancy < float(min_expectancy):
        blocking_findings.append("expectancy_negative" if expectancy < 0 else "expectancy_below_minimum")
        required_actions.append("restore_positive_expectancy_before_risk_increase")
    if risk_of_ruin is not None and risk_of_ruin > float(risk_of_ruin_cap):
        blocking_findings.append("risk_of_ruin_exceeds_cap")
        required_actions.append("reduce_risk_until_risk_of_ruin_is_within_cap")
    if drawdown is not None and drawdown > float(max_drawdown_cap_pct):
        blocking_findings.append("p95_drawdown_exceeds_cap")
        required_actions.append("reduce_drawdown_before_readiness")
    if profit_factor is not None and profit_factor < float(min_profit_factor):
        finding = "profit_factor_below_minimum"
        if profit_factor < 1.0 or strict:
            blocking_findings.append(finding)
            required_actions.append("improve_profit_factor_before_readiness")
        else:
            warnings.append(finding)
    if probability_of_loss is not None and probability_of_loss > 0.5:
        warnings.append("probability_of_loss_above_half")

    if monte_carlo.get("sample_warning") is True:
        warnings.append("monte_carlo_sample_warning")
        if strict:
            blocking_findings.append("sample_warning")

    blocking_findings = sorted(set(blocking_findings))
    warnings = sorted(set(warnings))
    required_actions = sorted(set(required_actions))
    status = "blocked" if blocking_findings else "warning" if warnings else "ok"
    action = policy_action(
        status=status,
        expectancy=expectancy,
        risk_of_ruin=risk_of_ruin,
        risk_of_ruin_cap=float(risk_of_ruin_cap),
        profit_factor=profit_factor,
        min_profit_factor=float(min_profit_factor),
        drawdown=drawdown,
        max_drawdown_cap_pct=float(max_drawdown_cap_pct),
    )
    no_trade_reason = no_trade_reasons(blocking_findings) if action in {"no_trade", "observe_only"} else []
    sizing = conservative_position_sizing(
        action=action,
        initial_capital=resolved_initial_capital,
        current_stake=resolved_current_stake,
        current_leverage=resolved_current_leverage,
        risk_of_ruin=risk_of_ruin,
        risk_of_ruin_cap=float(risk_of_ruin_cap),
        drawdown=drawdown,
        max_drawdown_cap_pct=float(max_drawdown_cap_pct),
        profit_factor=profit_factor,
        min_profit_factor=float(min_profit_factor),
        expectancy=expectancy,
    )
    reason = "ok" if status == "ok" else ";".join(blocking_findings or warnings)
    report = {
        "status": status,
        "reason": reason,
        "generated_at_utc": generated_at,
        "report_version": REPORT_VERSION,
        "source_monte_carlo_report": str(source),
        "output_path": str(target) if target is not None else None,
        "risk_of_ruin": risk_of_ruin,
        "risk_of_ruin_cap": float(risk_of_ruin_cap),
        "expectancy_per_trade": expectancy,
        "min_expectancy": float(min_expectancy),
        "simulated_profit_factor": profit_factor,
        "min_profit_factor": float(min_profit_factor),
        "probability_of_loss": probability_of_loss,
        "p95_max_drawdown_pct": drawdown,
        "max_drawdown_cap_pct": float(max_drawdown_cap_pct),
        "current_stake": resolved_current_stake,
        "current_leverage": resolved_current_leverage,
        "initial_capital": resolved_initial_capital,
        **sizing,
        "policy_action": action,
        "no_trade_reason": no_trade_reason,
        "risk_budget_status": status,
        "recovery_required": status != "ok",
        "risk_recovery_can_leave_panic": status == "ok",
        "readiness_may_proceed": status == "ok",
        "live_release_allowed": False,
        "blocking_findings": blocking_findings,
        "warnings": warnings,
        "required_actions_before_readiness": required_actions,
        "monte_carlo_status": monte_carlo.get("status"),
        "monte_carlo_reason": monte_carlo.get("reason"),
        "input_rows": int_value(metrics.get("input_rows"), monte_carlo.get("input_rows")),
        "usable_rows": int_value(metrics.get("usable_rows"), monte_carlo.get("usable_rows")),
        **safety,
    }
    write_report(report, target)
    return report


def extract_metrics(report: dict[str, Any]) -> dict[str, Any]:
    risk_metrics = report.get("risk_metrics") if isinstance(report.get("risk_metrics"), dict) else {}
    parameters = report.get("simulation_parameters") if isinstance(report.get("simulation_parameters"), dict) else {}
    metrics: dict[str, Any] = {}
    for key in (
        "risk_of_ruin",
        "probability_of_loss",
        "expectancy_per_trade",
        "simulated_profit_factor",
        "p95_max_drawdown_pct",
        "p95_max_losing_streak",
        "median_max_losing_streak",
        "initial_capital",
        "stake",
        "leverage",
        "input_rows",
        "usable_rows",
    ):
        metrics[key] = first_present(report.get(key), risk_metrics.get(key), parameters.get(key))
    return metrics


def missing_required_metrics(metrics: dict[str, Any]) -> list[str]:
    required = ("risk_of_ruin", "expectancy_per_trade", "simulated_profit_factor", "p95_max_drawdown_pct")
    return [key for key in required if safe_float(metrics.get(key)) is None]


def policy_action(
    *,
    status: str,
    expectancy: float | None,
    risk_of_ruin: float | None,
    risk_of_ruin_cap: float,
    profit_factor: float | None,
    min_profit_factor: float,
    drawdown: float | None,
    max_drawdown_cap_pct: float,
) -> str:
    if status == "blocked":
        if (expectancy is not None and expectancy < 0) or (risk_of_ruin is not None and risk_of_ruin > risk_of_ruin_cap):
            return "no_trade"
        return "observe_only"
    if status == "warning":
        return "reduce_risk"
    if (
        profit_factor is not None
        and profit_factor >= min_profit_factor
        and drawdown is not None
        and drawdown <= max_drawdown_cap_pct
        and expectancy is not None
        and expectancy >= 0
    ):
        return "eligible_for_shadow_only"
    return "conservative_paper_only"


def no_trade_reasons(blocking_findings: list[str]) -> list[str]:
    reasons = []
    for item in blocking_findings:
        if item.startswith("missing_monte_carlo_metrics"):
            reasons.append("missing_monte_carlo_metrics")
        elif item.startswith("unsafe_safety_flag"):
            reasons.append("unsafe_safety_flags")
        else:
            reasons.append(item)
    return sorted(set(reasons))


def conservative_position_sizing(
    *,
    action: str,
    initial_capital: float,
    current_stake: float,
    current_leverage: float,
    risk_of_ruin: float | None,
    risk_of_ruin_cap: float,
    drawdown: float | None,
    max_drawdown_cap_pct: float,
    profit_factor: float | None,
    min_profit_factor: float,
    expectancy: float | None,
) -> dict[str, Any]:
    if action in {"no_trade", "observe_only"}:
        return {
            "max_stake_recommended": 0.0,
            "max_leverage_recommended": 0.0,
            "daily_loss_cap_recommended": 0.0,
            "weekly_loss_cap_recommended": 0.0,
            "max_consecutive_losses_recommended": 0,
        }

    base_stake = min(float(current_stake), float(initial_capital) * 0.01) if initial_capital > 0 else 0.0
    ratios = [1.0]
    if risk_of_ruin is not None and risk_of_ruin > 0:
        ratios.append(max(min(risk_of_ruin_cap / risk_of_ruin, 1.0), 0.0))
    if drawdown is not None and drawdown > 0:
        ratios.append(max(min(max_drawdown_cap_pct / drawdown, 1.0), 0.0))
    if profit_factor is not None and min_profit_factor > 0:
        ratios.append(max(min(profit_factor / min_profit_factor, 1.0), 0.0))
    if expectancy is not None and expectancy <= 0:
        ratios.append(0.25)
    multiplier = max(min(ratios), 0.0)
    if action == "reduce_risk":
        multiplier = min(multiplier, 0.5)
    stake = round(base_stake * multiplier, 8)
    leverage = round(min(float(current_leverage), 1.0) * multiplier, 8) if current_leverage > 0 else 0.0
    daily_cap = round(min(float(initial_capital) * 0.01, stake * 2.0), 8) if initial_capital > 0 else 0.0
    weekly_cap = round(min(float(initial_capital) * 0.03, daily_cap * 3.0), 8) if initial_capital > 0 else 0.0
    return {
        "max_stake_recommended": stake,
        "max_leverage_recommended": leverage,
        "daily_loss_cap_recommended": daily_cap,
        "weekly_loss_cap_recommended": weekly_cap,
        "max_consecutive_losses_recommended": 2 if stake > 0 else 0,
    }


def base_report(
    *,
    status: str,
    reason: str,
    generated_at_utc: str,
    source: Path,
    target: Path | None,
    risk_of_ruin_cap: float,
    max_drawdown_cap_pct: float,
    min_profit_factor: float,
    min_expectancy: float,
    initial_capital: float | None,
    current_stake: float | None,
    current_leverage: float | None,
    safety: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "generated_at_utc": generated_at_utc,
        "report_version": REPORT_VERSION,
        "source_monte_carlo_report": str(source),
        "output_path": str(target) if target is not None else None,
        "risk_of_ruin": None,
        "risk_of_ruin_cap": float(risk_of_ruin_cap),
        "expectancy_per_trade": None,
        "min_expectancy": float(min_expectancy),
        "simulated_profit_factor": None,
        "min_profit_factor": float(min_profit_factor),
        "probability_of_loss": None,
        "p95_max_drawdown_pct": None,
        "max_drawdown_cap_pct": float(max_drawdown_cap_pct),
        "current_stake": float(current_stake or 0.0),
        "current_leverage": float(current_leverage or 0.0),
        "initial_capital": float(initial_capital or 0.0),
        "max_stake_recommended": 0.0,
        "max_leverage_recommended": 0.0,
        "daily_loss_cap_recommended": 0.0,
        "weekly_loss_cap_recommended": 0.0,
        "max_consecutive_losses_recommended": 0,
        "policy_action": "observe_only",
        "no_trade_reason": [reason],
        "blocking_findings": [reason],
        "warnings": [],
        "risk_budget_status": status,
        "recovery_required": True,
        "risk_recovery_can_leave_panic": False,
        "readiness_may_proceed": False,
        "live_release_allowed": False,
        "required_actions_before_readiness": ["produce_valid_monte_carlo_report"],
        **safety,
    }


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def numeric_or_default(*values: Any) -> float:
    for value in values:
        parsed = safe_float(value)
        if parsed is not None:
            return parsed
    return 0.0


def int_value(*values: Any) -> int | None:
    for value in values:
        parsed = safe_float(value)
        if parsed is not None:
            return int(parsed)
    return None


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


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
        "risk_manager_updated": False,
        "stake_updated": False,
        "leverage_updated": False,
        "signal_producer_updated": False,
        "model_promoted": False,
        "freqtrade_db_touched": False,
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
    for flag in SAFE_FALSE_FLAGS:
        if payload.get(flag) is True:
            unsafe.append(flag)
    return unsafe


def write_report(report: dict[str, Any], path: Path | None) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")


def utc_timestamp(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
