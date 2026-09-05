"""Chronological out-of-sample economic validation for Paper selector evidence.

Research-only and fail-closed. The validator evaluates only closed Paper outcomes,
uses a deterministic chronological holdout, and never trains/promotes models or
changes runtime/risk/order state.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

SCHEMA_VERSION = "paper_autolearning_economic_oos_validation_v1"
DEFAULT_OUTCOME_PATH = Path("data/feedback/outcome_events.parquet")
DEFAULT_SELECTOR_FIELD = "paper_candidate_filter_decision"
OOS_FRACTION = 0.30
MIN_TOTAL_TRADES = 200
MIN_TRAIN_CONTEXT_TRADES = 100
MIN_OOS_TRADES = 60
MIN_OOS_SELECTED_TRADES = 50
MIN_OOS_PROFIT_FACTOR = 1.10
MIN_OOS_EXPECTANCY = 0.0
MIN_OOS_NET_PNL = 0.0
MIN_EXPECTANCY_UPLIFT = 0.0
MAX_DRAWDOWN_RATIO_TO_BASELINE = 1.05

_ALLOWED_SELECTOR_VALUES = {
    "1",
    "true",
    "allow",
    "allowed",
    "pass",
    "passed",
    "approve",
    "approved",
    "permit",
    "permitted",
}


def build_paper_autolearning_economic_oos_validation_v1(
    *,
    project_root: str | Path,
    rows: Sequence[Mapping[str, Any]] | None = None,
    outcome_path: str | Path | None = None,
    selector_field: str = DEFAULT_SELECTOR_FIELD,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    source = _resolve(root, outcome_path or DEFAULT_OUTCOME_PATH)
    input_rows = [dict(row) for row in rows] if rows is not None else _read_rows(source)
    normalized, invalid_time_count = _normalize_rows(input_rows)

    blockers: list[str] = []
    if not normalized:
        blockers.append("no_valid_closed_outcomes")
    if invalid_time_count:
        blockers.append("unparseable_close_time_detected")
    if normalized and not any(selector_field in row for row in normalized):
        blockers.append(f"missing_selector_field:{selector_field}")
    if len(normalized) < MIN_TOTAL_TRADES:
        blockers.append("min_total_trades_not_met")

    split_index = _split_index(len(normalized))
    train_context = normalized[:split_index]
    oos = normalized[split_index:]
    oos_selected = [row for row in oos if _is_selected(row.get(selector_field))]

    if len(train_context) < MIN_TRAIN_CONTEXT_TRADES:
        blockers.append("min_train_context_trades_not_met")
    if len(oos) < MIN_OOS_TRADES:
        blockers.append("min_oos_trades_not_met")
    if len(oos_selected) < MIN_OOS_SELECTED_TRADES:
        blockers.append("min_oos_selected_trades_not_met")

    baseline_metrics = _metrics(oos)
    selected_metrics = _metrics(oos_selected)
    selected_pf = selected_metrics["profit_factor_numeric"]
    if selected_pf is None or selected_pf < MIN_OOS_PROFIT_FACTOR:
        blockers.append("oos_profit_factor_not_met")
    if selected_metrics["expectancy"] <= MIN_OOS_EXPECTANCY:
        blockers.append("oos_positive_expectancy_not_met")
    if selected_metrics["net_pnl_total"] <= MIN_OOS_NET_PNL:
        blockers.append("oos_positive_net_pnl_not_met")

    expectancy_uplift = selected_metrics["expectancy"] - baseline_metrics["expectancy"]
    if expectancy_uplift <= MIN_EXPECTANCY_UPLIFT:
        blockers.append("oos_expectancy_uplift_not_met")

    drawdown_ratio = _safe_ratio(
        selected_metrics["max_drawdown_abs"], baseline_metrics["max_drawdown_abs"]
    )
    if drawdown_ratio is not None and drawdown_ratio > MAX_DRAWDOWN_RATIO_TO_BASELINE:
        blockers.append("oos_drawdown_ratio_to_baseline_exceeded")

    boundary_ok = _boundary_is_strict(train_context, oos)
    if not boundary_ok and normalized:
        blockers.append("chronological_boundary_invalid")

    robust = not blockers
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if robust else "blocked",
        "reason": "oos_economic_edge_research_only" if robust else blockers[0],
        "decision": "OOS_ECONOMICALLY_PROMISING_RESEARCH_ONLY" if robust else "MANTER_EM_RESEARCH",
        "source_path": str(source),
        "selector_field": selector_field,
        "input_row_count": len(input_rows),
        "valid_closed_outcome_count": len(normalized),
        "invalid_close_time_count": invalid_time_count,
        "split_method": "chronological_holdout",
        "oos_fraction": OOS_FRACTION,
        "split_index": split_index,
        "train_context_trade_count": len(train_context),
        "oos_trade_count": len(oos),
        "oos_selected_trade_count": len(oos_selected),
        "train_context_end_utc": _time_value(train_context[-1]) if train_context else None,
        "oos_start_utc": _time_value(oos[0]) if oos else None,
        "chronological_boundary_valid": boundary_ok,
        "oos_baseline_metrics": _public_metrics(baseline_metrics),
        "oos_selected_metrics": _public_metrics(selected_metrics),
        "oos_expectancy_uplift": round(expectancy_uplift, 10),
        "oos_drawdown_ratio_to_baseline": drawdown_ratio,
        "thresholds": {
            "min_total_trades": MIN_TOTAL_TRADES,
            "min_train_context_trades": MIN_TRAIN_CONTEXT_TRADES,
            "min_oos_trades": MIN_OOS_TRADES,
            "min_oos_selected_trades": MIN_OOS_SELECTED_TRADES,
            "min_oos_profit_factor": MIN_OOS_PROFIT_FACTOR,
            "min_oos_expectancy": MIN_OOS_EXPECTANCY,
            "min_oos_net_pnl": MIN_OOS_NET_PNL,
            "min_expectancy_uplift": MIN_EXPECTANCY_UPLIFT,
            "max_drawdown_ratio_to_baseline": MAX_DRAWDOWN_RATIO_TO_BASELINE,
        },
        "blockers": sorted(set(blockers)),
        "anti_leakage": True,
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "promotion_allowed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "updates_qlib_runtime": False,
        "updates_ai_shadow_runtime": False,
        "changes_risk": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "writes_runtime": False,
        "write_performed": False,
    }


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return []
    if path.suffix.lower() == ".parquet":
        frame = pd.read_parquet(path)
    elif path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
    else:
        raise ValueError(f"unsupported_outcome_format:{path.suffix.lower()}")
    return frame.to_dict(orient="records")


def _normalize_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    normalized: list[dict[str, Any]] = []
    invalid_time_count = 0
    for raw in rows:
        row = dict(raw)
        if row.get("is_closed") is False:
            continue
        pnl = _to_float(row.get("net_pnl"))
        if pnl is None or not math.isfinite(pnl):
            continue
        timestamp = _parse_time(row)
        if timestamp is None:
            invalid_time_count += 1
            continue
        row["net_pnl"] = pnl
        row["__oos_close_time"] = timestamp
        normalized.append(row)
    normalized.sort(key=lambda row: row["__oos_close_time"])
    return normalized, invalid_time_count


def _split_index(count: int) -> int:
    if count <= 0:
        return 0
    oos_count = max(1, math.ceil(count * OOS_FRACTION))
    return max(0, count - oos_count)


def _metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [float(row["net_pnl"]) for row in rows]
    count = len(pnls)
    gross_profit = sum(value for value in pnls if value > 0)
    gross_loss_abs = abs(sum(value for value in pnls if value < 0))
    if gross_loss_abs == 0:
        pf_numeric = float("inf") if gross_profit > 0 else None
    else:
        pf_numeric = gross_profit / gross_loss_abs
    net = sum(pnls)
    wins = sum(1 for value in pnls if value > 0)
    return {
        "trade_count": count,
        "win_rate": round(wins / count, 10) if count else 0.0,
        "net_pnl_total": round(net, 10),
        "expectancy": round(net / count, 10) if count else 0.0,
        "profit_factor_numeric": pf_numeric,
        "max_drawdown_abs": round(_max_drawdown_abs(pnls), 10),
    }


def _public_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    value = metrics.get("profit_factor_numeric")
    return {
        "trade_count": metrics.get("trade_count"),
        "win_rate": metrics.get("win_rate"),
        "net_pnl_total": metrics.get("net_pnl_total"),
        "expectancy": metrics.get("expectancy"),
        "profit_factor": None if value is None else ("inf" if math.isinf(float(value)) else round(float(value), 10)),
        "max_drawdown_abs": metrics.get("max_drawdown_abs"),
    }


def _max_drawdown_abs(pnls: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    maximum = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum


def _parse_time(row: Mapping[str, Any]) -> datetime | None:
    value = row.get("close_time_utc") or row.get("close_time")
    if value is None or str(value).strip() == "":
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _time_value(row: Mapping[str, Any]) -> str | None:
    value = row.get("__oos_close_time")
    return value.isoformat() if isinstance(value, datetime) else None


def _boundary_is_strict(train_context: Sequence[Mapping[str, Any]], oos: Sequence[Mapping[str, Any]]) -> bool:
    if not train_context or not oos:
        return False
    return train_context[-1]["__oos_close_time"] <= oos[0]["__oos_close_time"]


def _is_selected(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value == 1:
        return True
    return str(value).strip().lower() in _ALLOWED_SELECTOR_VALUES


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None if numerator == 0 else float("inf")
    return round(numerator / denominator, 10)


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()
