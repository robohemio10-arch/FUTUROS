"""Research-only aggregate futures exposure and correlation diagnostics."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from .contracts import ContractViolation, Side, decimal_text
from .margin import Position


def aggregate_exposure(
    *,
    positions: Sequence[Position],
    mark_prices: Mapping[str, Decimal],
    correlations: Mapping[tuple[str, str], Decimal] | None = None,
) -> dict[str, Any]:
    notionals: dict[str, Decimal] = {}
    signed: dict[str, Decimal] = {}
    cross_margin_symbols: set[str] = set()
    for position in positions:
        position.validate()
        if position.symbol not in mark_prices:
            raise ContractViolation(f"mark_price_missing:{position.symbol}")
        notional = position.notional(mark_prices[position.symbol])
        notionals[position.symbol] = notionals.get(position.symbol, Decimal("0")) + notional
        sign = Decimal("1") if position.side.order_side == Side.BUY else Decimal("-1")
        signed[position.symbol] = signed.get(position.symbol, Decimal("0")) + notional * sign
        if position.margin_mode is not None and position.margin_mode.value == "CROSS":
            cross_margin_symbols.add(position.symbol)

    gross = sum(notionals.values(), Decimal("0"))
    net = sum(signed.values(), Decimal("0"))
    long_exposure = sum(
        (value for value in signed.values() if value > 0), Decimal("0")
    )
    short_exposure = abs(
        sum((value for value in signed.values() if value < 0), Decimal("0"))
    )
    concentration = (
        max(notionals.values(), default=Decimal("0")) / gross
        if gross > 0
        else Decimal("0")
    )

    symbols = sorted(notionals)
    correlated_exposure: Decimal | None = Decimal("0")
    missing_pairs: list[str] = []
    if len(symbols) > 1:
        if correlations is None:
            correlated_exposure = None
            missing_pairs = [
                f"{left}:{right}"
                for index, left in enumerate(symbols)
                for right in symbols[index + 1 :]
            ]
        else:
            for index, left in enumerate(symbols):
                for right in symbols[index + 1 :]:
                    key = (left, right)
                    reverse = (right, left)
                    if key in correlations:
                        correlation = correlations[key]
                    elif reverse in correlations:
                        correlation = correlations[reverse]
                    else:
                        missing_pairs.append(f"{left}:{right}")
                        continue
                    if not Decimal("-1") <= correlation <= Decimal("1"):
                        raise ContractViolation("invalid_correlation")
                    correlated_exposure = (
                        correlated_exposure or Decimal("0")
                    ) + min(notionals[left], notionals[right]) * abs(correlation)
            if missing_pairs:
                correlated_exposure = None

    liquidation_cascade_proxy = (
        None
        if correlated_exposure is None
        else correlated_exposure
        * Decimal(str(max(len(cross_margin_symbols) - 1, 0)))
    )
    return {
        "gross_exposure": decimal_text(gross),
        "net_exposure": decimal_text(net),
        "long_exposure": decimal_text(long_exposure),
        "short_exposure": decimal_text(short_exposure),
        "symbol_concentration": decimal_text(concentration),
        "correlated_exposure": decimal_text(correlated_exposure),
        "correlation_status": (
            "blocked_missing_correlations" if missing_pairs else "available"
        ),
        "missing_correlation_pairs": missing_pairs,
        "cross_margin_dependency": len(cross_margin_symbols) > 1,
        "liquidation_cascade_proxy": decimal_text(liquidation_cascade_proxy),
        "risk_manager_updated": False,
        "operational_limits_published": False,
    }
