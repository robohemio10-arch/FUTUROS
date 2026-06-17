"""Deterministic paper-only execution cost simulation utilities.

The module is intentionally research-only. It never contacts exchanges, never
submits orders, and never changes runtime risk parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


SAFETY_FLAGS: dict[str, Any] = {
    "paper_only": True,
    "shadow_only": True,
    "runtime_mode": "paper",
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "changes_training_dataset": False,
    "research_only": True,
}


@dataclass(frozen=True)
class CostModel:
    """Conservative cost assumptions expressed in basis points of notional."""

    fee_rate_bps: float = 8.0
    spread_bps: float = 2.0
    slippage_bps: float = 4.0
    latency_penalty_bps: float = 1.0
    volatility_penalty_bps: float = 2.0
    notional_column: str | None = None


def _numeric_series(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.full(len(frame), default), index=frame.index, dtype="float64")
    raw = frame[column]
    if raw.dtype == object or pd.api.types.is_string_dtype(raw):
        normalized = raw.astype(str).str.strip().str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
        values = pd.to_numeric(normalized, errors="coerce")
    else:
        values = pd.to_numeric(raw, errors="coerce")
    return values.fillna(default).astype("float64")


def infer_notional(frame: pd.DataFrame, *, explicit_column: str | None = None) -> pd.Series:
    """Infer per-trade notional from common SmartCrypto/Freqtrade/OCR schemas."""

    if explicit_column and explicit_column in frame.columns:
        values = _numeric_series(frame, explicit_column).abs()
        if float(values.sum()) > 0.0:
            return values

    for candidate in (
        "notional_usdt",
        "stake_amount",
        "position_notional_usdt",
        "quote_volume",
        "position_value_usdt",
        "volume_posicao_usdt",
        "volume_fechado_usdt",
    ):
        if candidate in frame.columns:
            values = _numeric_series(frame, candidate).abs()
            if float(values.sum()) > 0.0:
                return values

    price_candidates = (
        "entry_price",
        "preco_abertura",
        "preco_transacao",
        "open_rate",
        "transaction_price",
        "price",
    )
    size_candidates = (
        "position_volume",
        "volume_posicao",
        "volume_fechado",
        "volume_transacao",
        "amount",
        "closed_volume",
        "transaction_volume",
        "volume",
    )
    price_col = next((column for column in price_candidates if column in frame.columns), None)
    size_col = next((column for column in size_candidates if column in frame.columns), None)
    if price_col and size_col:
        values = (_numeric_series(frame, price_col).abs() * _numeric_series(frame, size_col).abs()).astype("float64")
        if float(values.sum()) > 0.0:
            return values

    return pd.Series(np.ones(len(frame)), index=frame.index, dtype="float64")


def apply_execution_costs(
    frame: pd.DataFrame,
    *,
    pnl_column: str,
    cost_model: CostModel | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return a copy with deterministic before/after-cost PnL columns."""

    if pnl_column not in frame.columns:
        return frame.copy(), {
            **SAFETY_FLAGS,
            "status": "blocked",
            "reason": "pnl_column_missing_for_execution_costs",
            "pnl_column": pnl_column,
        }

    model = cost_model or CostModel()
    output = frame.copy()
    before_costs = _numeric_series(output, pnl_column)
    notional = infer_notional(output, explicit_column=model.notional_column)

    fee_cost = notional * (float(model.fee_rate_bps) / 10_000.0)
    spread_cost = notional * (float(model.spread_bps) / 10_000.0)
    slippage_cost = notional * (float(model.slippage_bps) / 10_000.0)
    latency_cost = notional * (float(model.latency_penalty_bps) / 10_000.0)
    volatility_cost = notional * (float(model.volatility_penalty_bps) / 10_000.0)
    total_cost = fee_cost + spread_cost + slippage_cost + latency_cost + volatility_cost
    after_costs = before_costs - total_cost

    output["validation_before_costs_pnl_usdt"] = before_costs.astype("float64")
    output["validation_fee_cost_usdt"] = fee_cost.astype("float64")
    output["validation_spread_cost_usdt"] = spread_cost.astype("float64")
    output["validation_slippage_cost_usdt"] = slippage_cost.astype("float64")
    output["validation_latency_cost_usdt"] = latency_cost.astype("float64")
    output["validation_volatility_cost_usdt"] = volatility_cost.astype("float64")
    output["validation_total_cost_usdt"] = total_cost.astype("float64")
    output["validation_after_costs_pnl_usdt"] = after_costs.astype("float64")

    return output, {
        **SAFETY_FLAGS,
        "status": "ok",
        "reason": "execution_costs_applied",
        "rows": int(len(output)),
        "pnl_column": pnl_column,
        "before_costs_net_pnl_usdt": float(before_costs.sum()),
        "fee_cost_usdt": float(fee_cost.sum()),
        "spread_cost_usdt": float(spread_cost.sum()),
        "slippage_cost_usdt": float(slippage_cost.sum()),
        "latency_cost_usdt": float(latency_cost.sum()),
        "volatility_cost_usdt": float(volatility_cost.sum()),
        "total_cost_usdt": float(total_cost.sum()),
        "after_costs_net_pnl_usdt": float(after_costs.sum()),
        "notional_sum_usdt": float(notional.sum()),
        "notional_mean_usdt": float(notional.mean()) if len(notional) else 0.0,
        "cost_model": {
            "fee_rate_bps": float(model.fee_rate_bps),
            "spread_bps": float(model.spread_bps),
            "slippage_bps": float(model.slippage_bps),
            "latency_penalty_bps": float(model.latency_penalty_bps),
            "volatility_penalty_bps": float(model.volatility_penalty_bps),
            "notional_column": model.notional_column,
        },
    }
