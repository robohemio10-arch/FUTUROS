"""Deterministic paper-only execution cost simulation utilities.

The module is intentionally research-only. It never contacts exchanges, never
submits orders, and never changes runtime risk parameters.
"""

from __future__ import annotations

import re
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


_NUMERIC_CLEANUP_PATTERN = re.compile(r"[^\d,\.\-\+eE]")
_SYMBOL_CLEANUP_PATTERN = re.compile(r"[^A-Z0-9]")


@dataclass(frozen=True)
class CostModel:
    """Conservative cost assumptions expressed in basis points of notional."""

    fee_rate_bps: float = 8.0
    spread_bps: float = 2.0
    slippage_bps: float = 4.0
    latency_penalty_bps: float = 1.0
    volatility_penalty_bps: float = 2.0
    notional_column: str | None = None
    max_trade_notional_usdt: float = 100_000.0


@dataclass(frozen=True)
class NotionalInference:
    """Per-trade notional inference result plus sanitized provenance."""

    values: pd.Series
    source: str
    price_column: str | None = None
    size_column: str | None = None
    explicit_column: str | None = None
    price_adjusted_rows: int = 0
    size_fallback_rows: int = 0
    invalid_rows: int = 0
    max_trade_notional_usdt: float | None = None


@dataclass(frozen=True)
class PriceNormalization:
    """Price series with row-level OCR scale adjustment count."""

    values: pd.Series
    adjusted_rows: int


def _normalize_numeric_text(value: object) -> str:
    """Normalize OCR/CSV numeric text without corrupting dot-decimal floats."""

    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "nat"}:
        return ""

    text = (
        text.replace("R$", "")
        .replace("USDT", "")
        .replace("USD", "")
        .replace("%", "")
        .replace("\u00a0", "")
        .strip()
    )
    text = _NUMERIC_CLEANUP_PATTERN.sub("", text)
    if not text or text in {"+", "-", ".", ","}:
        return ""

    has_comma = "," in text
    has_dot = "." in text

    if has_comma and has_dot:
        last_comma = text.rfind(",")
        last_dot = text.rfind(".")
        if last_comma > last_dot:
            return text.replace(".", "").replace(",", ".")
        return text.replace(",", "")

    if has_comma:
        if text.count(",") > 1:
            head, tail = text.rsplit(",", 1)
            if len(tail) == 3 and all(len(part) == 3 for part in head.split(",")[1:]):
                return text.replace(",", "")
            return head.replace(",", "") + "." + tail
        return text.replace(",", ".")

    if has_dot and text.count(".") > 1:
        head, tail = text.rsplit(".", 1)
        if len(tail) == 3 and all(len(part) == 3 for part in head.split(".")[1:]):
            return text.replace(".", "")
        return head.replace(".", "") + "." + tail

    return text


def _numeric_series(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    """Return a float64 series with institutional OCR/CSV numeric normalization."""

    if column not in frame.columns:
        return pd.Series(np.full(len(frame), default), index=frame.index, dtype="float64")

    raw = frame[column]
    if pd.api.types.is_numeric_dtype(raw):
        values = pd.to_numeric(raw, errors="coerce")
    else:
        normalized = raw.map(_normalize_numeric_text)
        values = pd.to_numeric(normalized, errors="coerce")

    return values.replace([np.inf, -np.inf], np.nan).fillna(default).astype("float64")


def _first_existing(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    return next((column for column in candidates if column in frame.columns), None)


def _symbol_series(frame: pd.DataFrame) -> pd.Series:
    for column in ("symbol", "pair", "moeda", "market", "instrument"):
        if column in frame.columns:
            return frame[column].astype(str).str.upper().map(lambda value: _SYMBOL_CLEANUP_PATTERN.sub("", value))
    return pd.Series(["" for _ in range(len(frame))], index=frame.index, dtype="object")


def _symbol_price_bounds(symbol: str) -> tuple[float, float] | None:
    normalized = _SYMBOL_CLEANUP_PATTERN.sub("", str(symbol).upper())
    if "BTC" in normalized:
        return (10_000.0, 200_000.0)
    if "ETH" in normalized:
        return (500.0, 10_000.0)
    return None


def _normalize_single_price(value: float, symbol: str) -> tuple[float, bool]:
    if not np.isfinite(value) or value <= 0.0:
        return (0.0, False)

    bounds = _symbol_price_bounds(symbol)
    if bounds is None:
        return (float(value), False)

    lower, upper = bounds
    if lower <= value <= upper:
        return (float(value), False)

    candidates = [float(value)]
    scaled_down = float(value)
    for _ in range(6):
        scaled_down /= 10.0
        candidates.append(scaled_down)

    scaled_up = float(value)
    for _ in range(6):
        scaled_up *= 10.0
        candidates.append(scaled_up)

    valid_candidates = [candidate for candidate in candidates if lower <= candidate <= upper]
    if valid_candidates:
        midpoint = (lower + upper) / 2.0
        selected = min(valid_candidates, key=lambda candidate: abs(candidate - midpoint))
        return (float(selected), not np.isclose(selected, value))

    return (float(value), False)


def _normalize_price_series(frame: pd.DataFrame, price_column: str) -> PriceNormalization:
    raw_price = _numeric_series(frame, price_column).abs()
    symbols = _symbol_series(frame)
    adjusted = pd.Series(np.zeros(len(frame), dtype=bool), index=frame.index)
    normalized_values: list[float] = []

    for index, value in raw_price.items():
        normalized, was_adjusted = _normalize_single_price(float(value), str(symbols.loc[index]))
        normalized_values.append(normalized)
        adjusted.loc[index] = bool(was_adjusted)

    values = pd.Series(normalized_values, index=frame.index, dtype="float64")
    return PriceNormalization(values=values, adjusted_rows=int(adjusted.sum()))


def _build_price_times_size_notional(
    frame: pd.DataFrame,
    *,
    price_column: str,
    preferred_size_column: str,
    max_trade_notional_usdt: float,
) -> tuple[pd.Series, int, int, int]:
    price_normalization = _normalize_price_series(frame, price_column)
    price = price_normalization.values.abs()

    size_candidates = (
        preferred_size_column,
        "volume_fechado",
        "volume_transacao",
        "closed_volume",
        "transaction_volume",
        "amount",
        "position_volume",
        "volume_posicao",
        "volume",
    )
    unique_size_candidates = tuple(dict.fromkeys(column for column in size_candidates if column in frame.columns))
    preferred_size = _numeric_series(frame, preferred_size_column).abs()
    selected_size = preferred_size.copy()
    selected_notional = (price * selected_size).astype("float64")

    valid_cap = float(max_trade_notional_usdt) if max_trade_notional_usdt > 0 else float("inf")
    invalid_mask = (selected_notional <= 0.0) | (selected_notional > valid_cap) | ~np.isfinite(selected_notional)
    fallback_rows = pd.Series(np.zeros(len(frame), dtype=bool), index=frame.index)

    for size_column in unique_size_candidates:
        if not bool(invalid_mask.any()):
            break
        candidate_size = _numeric_series(frame, size_column).abs()
        candidate_notional = (price * candidate_size).astype("float64")
        candidate_valid = (candidate_notional > 0.0) & (candidate_notional <= valid_cap) & np.isfinite(candidate_notional)
        replacement_mask = invalid_mask & candidate_valid
        if not bool(replacement_mask.any()):
            continue
        selected_size.loc[replacement_mask] = candidate_size.loc[replacement_mask]
        selected_notional.loc[replacement_mask] = candidate_notional.loc[replacement_mask]
        fallback_rows.loc[replacement_mask] = size_column != preferred_size_column
        invalid_mask = (selected_notional <= 0.0) | (selected_notional > valid_cap) | ~np.isfinite(selected_notional)

    selected_notional = selected_notional.mask(invalid_mask, 0.0).astype("float64")
    return (
        selected_notional,
        int(price_normalization.adjusted_rows),
        int(fallback_rows.sum()),
        int(invalid_mask.sum()),
    )


def infer_notional_details(
    frame: pd.DataFrame,
    *,
    explicit_column: str | None = None,
    max_trade_notional_usdt: float = 100_000.0,
) -> NotionalInference:
    """Infer per-trade notional from common SmartCrypto/Freqtrade/OCR schemas."""

    if explicit_column and explicit_column in frame.columns:
        values = _numeric_series(frame, explicit_column).abs()
        if float(values.sum()) > 0.0:
            return NotionalInference(
                values=values,
                source="explicit_notional_column",
                explicit_column=explicit_column,
                max_trade_notional_usdt=max_trade_notional_usdt,
            )

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
                return NotionalInference(
                    values=values,
                    source="direct_notional_column",
                    explicit_column=candidate,
                    max_trade_notional_usdt=max_trade_notional_usdt,
                )

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
    price_col = _first_existing(frame, price_candidates)
    size_col = _first_existing(frame, size_candidates)
    if price_col and size_col:
        values, price_adjusted_rows, size_fallback_rows, invalid_rows = _build_price_times_size_notional(
            frame,
            price_column=price_col,
            preferred_size_column=size_col,
            max_trade_notional_usdt=max_trade_notional_usdt,
        )
        if float(values.sum()) > 0.0:
            return NotionalInference(
                values=values,
                source="price_times_size",
                price_column=price_col,
                size_column=size_col,
                price_adjusted_rows=price_adjusted_rows,
                size_fallback_rows=size_fallback_rows,
                invalid_rows=invalid_rows,
                max_trade_notional_usdt=max_trade_notional_usdt,
            )

    fallback = pd.Series(np.ones(len(frame)), index=frame.index, dtype="float64")
    return NotionalInference(
        values=fallback,
        source="unit_fallback",
        invalid_rows=0,
        max_trade_notional_usdt=max_trade_notional_usdt,
    )


def infer_notional(frame: pd.DataFrame, *, explicit_column: str | None = None) -> pd.Series:
    """Infer per-trade notional from common SmartCrypto/Freqtrade/OCR schemas."""

    return infer_notional_details(frame, explicit_column=explicit_column).values


def _series_quantile(values: pd.Series, quantile: float) -> float:
    finite = values.replace([np.inf, -np.inf], np.nan).dropna()
    if finite.empty:
        return 0.0
    return float(finite.quantile(quantile))


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
    inference = infer_notional_details(
        output,
        explicit_column=model.notional_column,
        max_trade_notional_usdt=float(model.max_trade_notional_usdt),
    )
    notional = inference.values.astype("float64")

    fee_cost = notional * (float(model.fee_rate_bps) / 10_000.0)
    spread_cost = notional * (float(model.spread_bps) / 10_000.0)
    slippage_cost = notional * (float(model.slippage_bps) / 10_000.0)
    latency_cost = notional * (float(model.latency_penalty_bps) / 10_000.0)
    volatility_cost = notional * (float(model.volatility_penalty_bps) / 10_000.0)
    total_cost = fee_cost + spread_cost + slippage_cost + latency_cost + volatility_cost
    after_costs = before_costs - total_cost

    output["validation_before_costs_pnl_usdt"] = before_costs.astype("float64")
    output["validation_notional_usdt"] = notional.astype("float64")
    output["validation_fee_cost_usdt"] = fee_cost.astype("float64")
    output["validation_spread_cost_usdt"] = spread_cost.astype("float64")
    output["validation_slippage_cost_usdt"] = slippage_cost.astype("float64")
    output["validation_latency_cost_usdt"] = latency_cost.astype("float64")
    output["validation_volatility_cost_usdt"] = volatility_cost.astype("float64")
    output["validation_total_cost_usdt"] = total_cost.astype("float64")
    output["validation_after_costs_pnl_usdt"] = after_costs.astype("float64")

    notional_quality_status = "ok" if inference.invalid_rows == 0 else "warning"

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
        "notional_median_usdt": float(notional.median()) if len(notional) else 0.0,
        "notional_p95_usdt": _series_quantile(notional, 0.95),
        "notional_p99_usdt": _series_quantile(notional, 0.99),
        "notional_max_usdt": float(notional.max()) if len(notional) else 0.0,
        "notional_source": inference.source,
        "notional_price_column": inference.price_column,
        "notional_size_column": inference.size_column,
        "notional_explicit_column": inference.explicit_column,
        "notional_price_adjusted_rows": int(inference.price_adjusted_rows),
        "notional_size_fallback_rows": int(inference.size_fallback_rows),
        "notional_invalid_rows": int(inference.invalid_rows),
        "notional_quality_status": notional_quality_status,
        "notional_max_trade_cap_usdt": float(model.max_trade_notional_usdt),
        "cost_model": {
            "fee_rate_bps": float(model.fee_rate_bps),
            "spread_bps": float(model.spread_bps),
            "slippage_bps": float(model.slippage_bps),
            "latency_penalty_bps": float(model.latency_penalty_bps),
            "volatility_penalty_bps": float(model.volatility_penalty_bps),
            "notional_column": model.notional_column,
            "max_trade_notional_usdt": float(model.max_trade_notional_usdt),
        },
    }
