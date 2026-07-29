from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.runtime.integrity_traceability_v2 import atomic_write_json


DEFAULT_INPUT_PATH = Path("data/reports/ai_shadow_model_outcomes.jsonl")
DEFAULT_REPORT_PATH = Path("data/reports/monte_carlo_risk_simulation_report.json")
RETURN_CANDIDATES = ("target_return", "pnl_fechado", "net_pnl", "return", "realized_return")
MINIMUM_TRADES = 30
MINIMUM_SIMULATIONS = 100
MINIMUM_HORIZON_TRADES = 5


class MonteCarloRiskSimulationError(ValueError):
    pass


def run_monte_carlo_risk_simulation(
    *,
    input_path: str | Path = DEFAULT_INPUT_PATH,
    report_path: str | Path | None = DEFAULT_REPORT_PATH,
    initial_capital: float = 1000.0,
    stake: float = 100.0,
    leverage: float = 1.0,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    spread_bps: float = 0.0,
    stress_multiplier: float = 1.0,
    simulations: int = 1000,
    horizon_trades: int = 100,
    seed: int = 42,
    ruin_threshold_pct: float = 30.0,
    max_acceptable_drawdown_pct: float = 40.0,
    min_trades: int = MINIMUM_TRADES,
    strict: bool = False,
    safety_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    input_file = Path(input_path)
    report_file = Path(report_path) if report_path is not None else None
    if not input_file.exists():
        report = blocked_report(
            reason="missing_input",
            input_path=input_file,
            report_path=report_file,
            parameters=simulation_parameters(
                initial_capital=initial_capital,
                stake=stake,
                leverage=leverage,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
                spread_bps=spread_bps,
                stress_multiplier=stress_multiplier,
                simulations=simulations,
                horizon_trades=horizon_trades,
                seed=seed,
                ruin_threshold_pct=ruin_threshold_pct,
                max_acceptable_drawdown_pct=max_acceptable_drawdown_pct,
                min_trades=min_trades,
            ),
        )
        write_report(report, report_file)
        return report
    try:
        frame = read_table(input_file)
    except Exception as exc:
        report = blocked_report(
            reason=f"invalid_input:{exc}",
            input_path=input_file,
            report_path=report_file,
            parameters={},
        )
        write_report(report, report_file)
        return report
    return run_monte_carlo_risk_simulation_frame(
        frame=frame,
        input_path=input_file,
        report_path=report_file,
        initial_capital=initial_capital,
        stake=stake,
        leverage=leverage,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        spread_bps=spread_bps,
        stress_multiplier=stress_multiplier,
        simulations=simulations,
        horizon_trades=horizon_trades,
        seed=seed,
        ruin_threshold_pct=ruin_threshold_pct,
        max_acceptable_drawdown_pct=max_acceptable_drawdown_pct,
        min_trades=min_trades,
        strict=strict,
        safety_overrides=safety_overrides,
    )


def run_monte_carlo_risk_simulation_frame(
    *,
    frame: pd.DataFrame,
    input_path: str | Path | None = None,
    report_path: str | Path | None = None,
    initial_capital: float = 1000.0,
    stake: float = 100.0,
    leverage: float = 1.0,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    spread_bps: float = 0.0,
    stress_multiplier: float = 1.0,
    simulations: int = 1000,
    horizon_trades: int = 100,
    seed: int = 42,
    ruin_threshold_pct: float = 30.0,
    max_acceptable_drawdown_pct: float = 40.0,
    min_trades: int = MINIMUM_TRADES,
    strict: bool = False,
    safety_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report_file = Path(report_path) if report_path is not None else None
    params = simulation_parameters(
        initial_capital=initial_capital,
        stake=stake,
        leverage=leverage,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        spread_bps=spread_bps,
        stress_multiplier=stress_multiplier,
        simulations=simulations,
        horizon_trades=horizon_trades,
        seed=seed,
        ruin_threshold_pct=ruin_threshold_pct,
        max_acceptable_drawdown_pct=max_acceptable_drawdown_pct,
        min_trades=min_trades,
    )
    safe = safety_payload(safety_overrides)
    safety_errors = unsafe_safety_flags(safe)
    validation_errors = validate_parameters(params)
    if strict and safety_errors:
        validation_errors.append("unsafe_safety_flags")
    if validation_errors:
        report = blocked_report(
            reason=";".join(validation_errors),
            input_path=input_path,
            report_path=report_file,
            parameters=params,
            safety=safe,
        )
        report["blocking_errors"] = validation_errors + safety_errors
        write_report(report, report_file)
        return report
    if not isinstance(frame, pd.DataFrame):
        report = blocked_report(
            reason="input_must_be_dataframe",
            input_path=input_path,
            report_path=report_file,
            parameters=params,
            safety=safe,
        )
        write_report(report, report_file)
        return report
    if frame.empty:
        report = blocked_report(
            reason="empty_input",
            input_path=input_path,
            report_path=report_file,
            parameters=params,
            safety=safe,
        )
        write_report(report, report_file)
        return report

    return_column = first_existing([str(column) for column in frame.columns], RETURN_CANDIDATES)
    if return_column is None:
        report = blocked_report(
            reason="missing_return_column",
            input_path=input_path,
            report_path=report_file,
            parameters=params,
            safety=safe,
        )
        report["input_rows"] = int(len(frame))
        write_report(report, report_file)
        return report

    returns = extract_usable_returns(frame, return_column)
    usable_rows = int(len(returns))
    sample_warning = usable_rows < int(min_trades)
    if usable_rows == 0:
        report = blocked_report(
            reason="no_usable_returns",
            input_path=input_path,
            report_path=report_file,
            parameters=params,
            safety=safe,
        )
        report.update({"input_rows": int(len(frame)), "return_column_used": return_column})
        write_report(report, report_file)
        return report
    if strict and sample_warning:
        report = blocked_report(
            reason="insufficient_usable_rows",
            input_path=input_path,
            report_path=report_file,
            parameters=params,
            safety=safe,
        )
        report.update(
            {
                "input_rows": int(len(frame)),
                "usable_rows": usable_rows,
                "return_column_used": return_column,
                "sample_warning": True,
            }
        )
        write_report(report, report_file)
        return report

    simulation = simulate_bootstrap_equity(
        returns=returns,
        initial_capital=float(initial_capital),
        stake=float(stake),
        leverage=float(leverage),
        fee_bps=float(fee_bps),
        slippage_bps=float(slippage_bps),
        spread_bps=float(spread_bps),
        stress_multiplier=float(stress_multiplier),
        simulations=int(simulations),
        horizon_trades=int(horizon_trades),
        seed=int(seed),
    )
    risk_metrics = compute_risk_metrics(
        simulation,
        initial_capital=float(initial_capital),
        ruin_threshold_pct=float(ruin_threshold_pct),
    )
    recommendation = recommendation_payload(
        risk_metrics,
        usable_rows=usable_rows,
        min_trades=int(min_trades),
        max_acceptable_drawdown_pct=float(max_acceptable_drawdown_pct),
    )
    status = recommendation["recommendation_status"]
    if sample_warning and status == "ok":
        status = "insufficient_data"
    report = {
        "status": status,
        "reason": recommendation["recommendation_reason"]
        if status != "insufficient_data"
        else "sample_warning",
        "generated_at_utc": utc_timestamp(),
        "input_path": str(input_path) if input_path is not None else None,
        "report_path": str(report_file) if report_file else None,
        "input_rows": int(len(frame)),
        "usable_rows": usable_rows,
        "return_column_used": return_column,
        "simulation_parameters": params,
        "risk_metrics": risk_metrics,
        "stress_metrics": {
            "stress_fee_bps": float(fee_bps) * float(stress_multiplier),
            "stress_slippage_bps": float(slippage_bps) * float(stress_multiplier),
            "stress_spread_bps": float(spread_bps) * float(stress_multiplier),
            "stress_multiplier": float(stress_multiplier),
        },
        "distribution_summary": distribution_summary(simulation),
        "recommendation_status": recommendation["recommendation_status"],
        "recommendation_reason": recommendation["recommendation_reason"],
        "sample_warning": bool(sample_warning),
        "minimum_recommended_trades": int(min_trades),
        "signal_producer_updated": False,
        "registry_updated": False,
        "threshold_updated": False,
        "model_updated": False,
        "risk_manager_updated": False,
        "freqtrade_db_touched": False,
        **safe,
    }
    write_report(report, report_file)
    return report


def simulate_bootstrap_equity(
    *,
    returns: np.ndarray,
    initial_capital: float,
    stake: float,
    leverage: float,
    fee_bps: float,
    slippage_bps: float,
    spread_bps: float,
    stress_multiplier: float,
    simulations: int,
    horizon_trades: int,
    seed: int,
) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(int(seed))
    sampled = rng.choice(returns, size=(int(simulations), int(horizon_trades)), replace=True)
    cost_rate = ((float(fee_bps) + float(slippage_bps) + float(spread_bps)) / 10000.0) * float(stress_multiplier)
    pnl = (sampled * float(stake) * float(leverage)) - (float(stake) * cost_rate)
    equity = float(initial_capital) + np.cumsum(pnl, axis=1)
    final_equity = equity[:, -1]
    running_max = np.maximum.accumulate(
        np.concatenate([np.full((equity.shape[0], 1), float(initial_capital)), equity], axis=1),
        axis=1,
    )[:, 1:]
    drawdown_pct = np.maximum((running_max - equity) / np.maximum(running_max, 1e-12) * 100.0, 0.0)
    max_drawdown_pct = drawdown_pct.max(axis=1)
    losing_streaks = np.apply_along_axis(max_losing_streak, 1, pnl)
    return {
        "pnl": pnl,
        "equity": equity,
        "final_equity": final_equity,
        "max_drawdown_pct": max_drawdown_pct,
        "max_losing_streak": losing_streaks,
    }


def compute_risk_metrics(
    simulation: dict[str, np.ndarray],
    *,
    initial_capital: float,
    ruin_threshold_pct: float,
) -> dict[str, Any]:
    final_equity = simulation["final_equity"]
    max_drawdown_pct = simulation["max_drawdown_pct"]
    losing_streak = simulation["max_losing_streak"]
    returns_pct = (final_equity - float(initial_capital)) / float(initial_capital) * 100.0
    pnl = simulation["pnl"].reshape(-1)
    losses = pnl[pnl < 0]
    wins = pnl[pnl > 0]
    sorted_returns = np.sort(returns_pct)
    var_95 = float(np.percentile(returns_pct, 5))
    tail = sorted_returns[sorted_returns <= var_95]
    cvar_95 = float(tail.mean()) if tail.size else var_95
    gross_loss = abs(float(losses.sum())) if losses.size else 0.0
    gross_profit = float(wins.sum()) if wins.size else 0.0
    profit_factor = None if gross_loss == 0 and gross_profit > 0 else gross_profit / gross_loss if gross_loss else 0.0
    ruin_level = float(initial_capital) * (1.0 - float(ruin_threshold_pct) / 100.0)
    return {
        "simulations": int(len(final_equity)),
        "horizon_trades": int(simulation["pnl"].shape[1]),
        "median_final_equity": float(np.median(final_equity)),
        "mean_final_equity": float(np.mean(final_equity)),
        "min_final_equity": float(np.min(final_equity)),
        "max_final_equity": float(np.max(final_equity)),
        "p05_final_equity": float(np.percentile(final_equity, 5)),
        "p95_final_equity": float(np.percentile(final_equity, 95)),
        "expected_return_pct": float(np.mean(returns_pct)),
        "probability_of_loss": float(np.mean(final_equity < float(initial_capital))),
        "risk_of_ruin": float(np.mean(final_equity <= ruin_level)),
        "median_max_drawdown_pct": float(np.median(max_drawdown_pct)),
        "p95_max_drawdown_pct": float(np.percentile(max_drawdown_pct, 95)),
        "worst_max_drawdown_pct": float(np.max(max_drawdown_pct)),
        "median_max_losing_streak": float(np.median(losing_streak)),
        "p95_max_losing_streak": float(np.percentile(losing_streak, 95)),
        "var_95": var_95,
        "cvar_95": cvar_95,
        "simulated_profit_factor": profit_factor,
        "expectancy_per_trade": float(np.mean(pnl)),
    }


def recommendation_payload(
    risk_metrics: dict[str, Any],
    *,
    usable_rows: int,
    min_trades: int,
    max_acceptable_drawdown_pct: float,
) -> dict[str, str]:
    if usable_rows < min_trades:
        return {
            "recommendation_status": "blocked",
            "recommendation_reason": "insufficient_usable_rows",
        }
    if float(risk_metrics["risk_of_ruin"]) > 0.0:
        return {
            "recommendation_status": "blocked",
            "recommendation_reason": "risk_of_ruin_exceeds_limit",
        }
    if float(risk_metrics["p95_max_drawdown_pct"]) > float(max_acceptable_drawdown_pct):
        return {
            "recommendation_status": "blocked",
            "recommendation_reason": "p95_drawdown_exceeds_limit",
        }
    if float(risk_metrics["cvar_95"]) < -float(max_acceptable_drawdown_pct):
        return {
            "recommendation_status": "blocked",
            "recommendation_reason": "cvar_95_excessively_negative",
        }
    if float(risk_metrics["probability_of_loss"]) > 0.5:
        return {
            "recommendation_status": "warning",
            "recommendation_reason": "probability_of_loss_above_half",
        }
    return {"recommendation_status": "ok", "recommendation_reason": "risk_within_limits"}


def distribution_summary(simulation: dict[str, np.ndarray]) -> dict[str, Any]:
    final_equity = simulation["final_equity"]
    return {
        "final_equity_count": int(len(final_equity)),
        "final_equity_std": float(np.std(final_equity)),
        "final_equity_p25": float(np.percentile(final_equity, 25)),
        "final_equity_p75": float(np.percentile(final_equity, 75)),
    }


def extract_usable_returns(frame: pd.DataFrame, column: str) -> np.ndarray:
    values = pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return values.to_numpy(dtype=float)


def read_table(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(file_path)
    if suffix == ".csv":
        return pd.read_csv(file_path)
    if suffix == ".jsonl":
        rows = []
        with file_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
        return pd.DataFrame(rows)
    if suffix == ".json":
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return pd.DataFrame(payload)
        if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
            return pd.DataFrame(payload["rows"])
        if isinstance(payload, dict) and isinstance(payload.get("threshold_results"), list):
            rows = []
            for item in payload["threshold_results"]:
                threshold_pass = item.get("threshold_pass", {})
                if "net_pnl" in threshold_pass:
                    rows.append({"net_pnl": threshold_pass["net_pnl"]})
            return pd.DataFrame(rows)
        return pd.DataFrame([payload])
    raise MonteCarloRiskSimulationError(f"unsupported_input_format:{suffix}")


def validate_parameters(params: dict[str, Any]) -> list[str]:
    errors = []
    if float(params["initial_capital"]) <= 0:
        errors.append("invalid_initial_capital")
    if float(params["stake"]) <= 0:
        errors.append("invalid_stake")
    if float(params["leverage"]) <= 0:
        errors.append("invalid_leverage")
    if int(params["simulations"]) < MINIMUM_SIMULATIONS:
        errors.append("insufficient_simulations")
    if int(params["horizon_trades"]) < MINIMUM_HORIZON_TRADES:
        errors.append("insufficient_horizon_trades")
    return errors


def first_existing(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    return next((candidate for candidate in candidates if candidate in columns), None)


def max_losing_streak(values: np.ndarray) -> int:
    longest = 0
    current = 0
    for value in values:
        if value < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return int(longest)


def simulation_parameters(**kwargs: Any) -> dict[str, Any]:
    return {
        "initial_capital": float(kwargs["initial_capital"]),
        "stake": float(kwargs["stake"]),
        "leverage": float(kwargs["leverage"]),
        "fee_bps": float(kwargs["fee_bps"]),
        "slippage_bps": float(kwargs["slippage_bps"]),
        "spread_bps": float(kwargs["spread_bps"]),
        "stress_multiplier": float(kwargs["stress_multiplier"]),
        "simulations": int(kwargs["simulations"]),
        "horizon_trades": int(kwargs["horizon_trades"]),
        "seed": int(kwargs["seed"]),
        "ruin_threshold_pct": float(kwargs["ruin_threshold_pct"]),
        "max_acceptable_drawdown_pct": float(kwargs["max_acceptable_drawdown_pct"]),
        "min_trades": int(kwargs["min_trades"]),
    }


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
    for key in (
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
    ):
        if payload.get(key) is True:
            unsafe.append(key)
    return unsafe


def blocked_report(
    *,
    reason: str,
    input_path: str | Path | None,
    report_path: str | Path | None,
    parameters: dict[str, Any],
    safety: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": reason,
        "generated_at_utc": utc_timestamp(),
        "input_path": str(input_path) if input_path is not None else None,
        "report_path": str(report_path) if report_path is not None else None,
        "input_rows": 0,
        "usable_rows": 0,
        "return_column_used": None,
        "simulation_parameters": parameters,
        "risk_metrics": {},
        "stress_metrics": {},
        "distribution_summary": {},
        "recommendation_status": "blocked",
        "recommendation_reason": reason,
        "sample_warning": True,
        "signal_producer_updated": False,
        "registry_updated": False,
        "threshold_updated": False,
        "model_updated": False,
        "risk_manager_updated": False,
        "freqtrade_db_touched": False,
        **(safety or safety_payload()),
    }


def write_report(report: dict[str, Any], report_path: Path | None) -> None:
    if report_path is None:
        return
    atomic_write_json(report_path, report, sort_keys=False)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
