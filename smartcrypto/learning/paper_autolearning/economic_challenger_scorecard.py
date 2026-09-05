"""Economic scorecard for Paper challenger evidence.

Research-only. Computes deterministic economic metrics from closed Paper outcomes
without sending orders, changing risk, promoting models or updating runtime.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

SCHEMA_VERSION = "paper_autolearning_economic_challenger_scorecard_v1"
DEFAULT_OUTCOME_PATH = Path("data/feedback/outcome_events.parquet")
DEFAULT_SELECTOR_FIELD = "paper_candidate_filter_decision"
MIN_SELECTED_TRADES = 100
MIN_PROFIT_FACTOR = 1.10
MIN_EXPECTANCY = 0.0
MIN_NET_PNL = 0.0
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


def build_paper_autolearning_economic_challenger_scorecard_v1(
    *,
    project_root: str | Path,
    rows: Sequence[Mapping[str, Any]] | None = None,
    outcome_path: str | Path | None = None,
    selector_field: str = DEFAULT_SELECTOR_FIELD,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    source = _resolve(root, outcome_path or DEFAULT_OUTCOME_PATH)
    input_rows = [dict(row) for row in rows] if rows is not None else _read_rows(source)
    normalized = _normalize_rows(input_rows)
    selected = [row for row in normalized if _is_selected(row.get(selector_field))]

    blockers: list[str] = []
    if not normalized:
        blockers.append("no_valid_closed_outcomes")
    if normalized and not any(selector_field in row for row in normalized):
        blockers.append(f"missing_selector_field:{selector_field}")

    baseline_metrics = _metrics(normalized)
    selected_metrics = _metrics(selected)

    if selected_metrics["trade_count"] < MIN_SELECTED_TRADES:
        blockers.append("min_selected_trades_not_met")
    if not _profit_factor_passes(selected_metrics):
        blockers.append("min_profit_factor_not_met")
    if selected_metrics["expectancy"] <= MIN_EXPECTANCY:
        blockers.append("positive_expectancy_not_met")
    if selected_metrics["net_pnl_total"] <= MIN_NET_PNL:
        blockers.append("positive_net_pnl_not_met")

    expectancy_uplift = selected_metrics["expectancy"] - baseline_metrics["expectancy"]
    if expectancy_uplift <= MIN_EXPECTANCY_UPLIFT:
        blockers.append("expectancy_uplift_not_met")

    drawdown_ratio = _drawdown_ratio(
        selected_metrics["max_drawdown_abs"],
        baseline_metrics["max_drawdown_abs"],
    )
    if baseline_metrics["max_drawdown_abs"] == 0 and selected_metrics["max_drawdown_abs"] > 0:
        blockers.append("drawdown_worse_than_zero_drawdown_baseline")
    elif drawdown_ratio is not None and drawdown_ratio > MAX_DRAWDOWN_RATIO_TO_BASELINE:
        blockers.append("drawdown_ratio_to_baseline_exceeded")

    economically_promising = not blockers
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if economically_promising else "blocked",
        "reason": "economic_edge_research_only" if economically_promising else blockers[0],
        "decision": (
            "ECONOMICALLY_PROMISING_RESEARCH_ONLY"
            if economically_promising
            else "MANTER_EM_RESEARCH"
        ),
        "source_path": str(source),
        "selector_field": selector_field,
        "input_row_count": len(input_rows),
        "valid_closed_outcome_count": len(normalized),
        "selected_trade_count": len(selected),
        "baseline_metrics": baseline_metrics,
        "selected_metrics": selected_metrics,
        "expectancy_uplift": round(expectancy_uplift, 10),
        "drawdown_ratio_to_baseline": drawdown_ratio,
        "thresholds": {
            "min_selected_trades": MIN_SELECTED_TRADES,
            "min_profit_factor": MIN_PROFIT_FACTOR,
            "min_expectancy": MIN_EXPECTANCY,
            "min_net_pnl": MIN_NET_PNL,
            "min_expectancy_uplift": MIN_EXPECTANCY_UPLIFT,
            "max_drawdown_ratio_to_baseline": MAX_DRAWDOWN_RATIO_TO_BASELINE,
        },
        "blockers": sorted(set(blockers)),
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


def _normalize_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        if row.get("is_closed") is False:
            continue
        pnl = _to_float(row.get("net_pnl"))
        if pnl is None or not math.isfinite(pnl):
            continue
        row["net_pnl"] = pnl
        normalized.append(row)
    normalized.sort(key=_sort_key)
    return normalized


def _metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [float(row["net_pnl"]) for row in rows]
    count = len(pnls)
    gross_profit = sum(value for value in pnls if value > 0)
    gross_loss_abs = abs(sum(value for value in pnls if value < 0))
    profit_factor_infinite = gross_profit > 0 and gross_loss_abs == 0
    profit_factor = (
        None
        if gross_loss_abs == 0
        else round(gross_profit / gross_loss_abs, 10)
    )
    net = sum(pnls)
    expectancy = net / count if count else 0.0
    wins = sum(1 for value in pnls if value > 0)
    losses = sum(1 for value in pnls if value < 0)
    max_drawdown = _max_drawdown_abs(pnls)
    return {
        "trade_count": count,
        "win_count": wins,
        "loss_count": losses,
        "win_rate": round(wins / count, 10) if count else 0.0,
        "gross_profit": round(gross_profit, 10),
        "gross_loss_abs": round(gross_loss_abs, 10),
        "net_pnl_total": round(net, 10),
        "expectancy": round(expectancy, 10),
        "profit_factor": profit_factor,
        "profit_factor_infinite": profit_factor_infinite,
        "max_drawdown_abs": round(max_drawdown, 10),
    }


def _profit_factor_passes(metrics: Mapping[str, Any]) -> bool:
    if metrics.get("profit_factor_infinite") is True:
        return True
    value = metrics.get("profit_factor")
    return isinstance(value, (int, float)) and float(value) >= MIN_PROFIT_FACTOR


def _max_drawdown_abs(pnls: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return max_drawdown


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


def _sort_key(row: Mapping[str, Any]) -> tuple[int, str]:
    value = row.get("close_time_utc") or row.get("close_time") or ""
    text = str(value)
    try:
        return (0, datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat())
    except ValueError:
        return (1, text)


def _drawdown_ratio(selected: float, baseline: float) -> float | None:
    if baseline == 0:
        return None
    return round(selected / baseline, 10)


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()
