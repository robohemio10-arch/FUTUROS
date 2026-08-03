"""Paper A/B financial comparison without promotion authority."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .contracts import gate, mapping, mapping_list
from .io import finite, finite_or, positive_int, rounded


def _profit_factor_value(metrics: Mapping[str, Any]) -> float:
    if metrics.get("profit_factor_infinite") is True:
        return math.inf
    value = finite(metrics.get("profit_factor"))
    return value if value is not None else 0.0


def _metric_value(metrics: Mapping[str, Any], field: str) -> float:
    value = finite(metrics.get(field))
    return value if value is not None else 0.0


def strategy_metrics(
    strategy: Mapping[str, Any],
    minimum_trade_count: int,
    maximum_cost_bps: float,
) -> tuple[dict[str, Any], list[str]]:
    """Validate one strategy sample and calculate comparable net metrics."""

    strategy_id = str(strategy.get("strategy_id") or "").strip()
    evaluation_window_id = str(
        strategy.get("evaluation_window_id") or ""
    ).strip()
    trades = mapping_list(strategy.get("trades"))
    errors: list[str] = []

    if not strategy_id:
        errors.append("strategy_id_missing")
    if not evaluation_window_id:
        errors.append("evaluation_window_id_missing")
    if not trades:
        errors.append("trades_missing")
    if len(trades) < minimum_trade_count:
        errors.append("insufficient_trade_count")

    trade_ids: list[str] = []
    net_pnls: list[float] = []
    turnover = 0.0
    total_cost = 0.0

    for index, trade in enumerate(trades):
        trade_id = str(trade.get("trade_id") or "").strip()
        symbol = str(trade.get("symbol") or "").strip().upper()
        side = str(trade.get("side") or "").strip().lower()
        trade_ids.append(trade_id)

        if not trade_id:
            errors.append(f"trade_{index}:trade_id_missing")
        if symbol not in {"BTCUSDT", "ETHUSDT"}:
            errors.append(f"trade_{index}:symbol_invalid")
        if side not in {"long", "short"}:
            errors.append(f"trade_{index}:side_invalid")
        if not str(trade.get("close_time_utc") or "").strip():
            errors.append(f"trade_{index}:close_time_utc_missing")

        net_pnl = finite(trade.get("net_pnl"))
        notional = finite(trade.get("notional"))
        fees = finite(trade.get("fees"))
        funding = finite(trade.get("funding"))
        if any(
            value is None
            for value in (net_pnl, notional, fees, funding)
        ):
            errors.append(f"trade_{index}:financial_fields_invalid")
            continue
        if notional is None or notional <= 0:
            errors.append(f"trade_{index}:notional_must_be_positive")
            continue
        if net_pnl is None or fees is None or funding is None:
            errors.append(f"trade_{index}:financial_fields_invalid")
            continue

        net_pnls.append(net_pnl)
        turnover += notional
        total_cost += abs(fees) + abs(funding)

    if len(trade_ids) != len(set(trade_ids)):
        errors.append("duplicate_trade_ids")

    wins = [value for value in net_pnls if value > 0]
    losses = [value for value in net_pnls if value < 0]
    breakeven = [value for value in net_pnls if value == 0]
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    net_pnl_total = sum(net_pnls)
    total_cost_bps = total_cost / turnover * 10_000 if turnover > 0 else None

    if total_cost_bps is None:
        errors.append("total_cost_bps_unavailable")
    elif total_cost_bps > maximum_cost_bps:
        errors.append("total_cost_bps_exceeds_limit")

    equity = 0.0
    peak = 0.0
    maximum_drawdown = 0.0
    for pnl in net_pnls:
        equity += pnl
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown, peak - equity)

    average_win = sum(wins) / len(wins) if wins else None
    average_loss = sum(losses) / len(losses) if losses else None
    payoff = (
        average_win / abs(average_loss)
        if average_win is not None
        and average_loss is not None
        and average_loss < 0
        else None
    )
    profit_factor = (
        gross_profit / abs(gross_loss)
        if gross_loss < 0
        else None
    )

    metrics = {
        "strategy_id": strategy_id or None,
        "evaluation_window_id": evaluation_window_id or None,
        "trade_count": len(net_pnls),
        "win_count": len(wins),
        "loss_count": len(losses),
        "breakeven_count": len(breakeven),
        "net_pnl_abs": rounded(net_pnl_total),
        "gross_profit_abs": rounded(gross_profit),
        "gross_loss_abs": rounded(gross_loss),
        "profit_factor": rounded(profit_factor),
        "profit_factor_infinite": bool(
            net_pnls and not losses and gross_profit > 0
        ),
        "expectancy_abs_per_trade": rounded(
            net_pnl_total / len(net_pnls) if net_pnls else None
        ),
        "win_rate": rounded(
            len(wins) / len(net_pnls) if net_pnls else None
        ),
        "average_win_abs": rounded(average_win),
        "average_loss_abs": rounded(average_loss),
        "payoff": rounded(payoff),
        "max_drawdown_abs": rounded(maximum_drawdown),
        "turnover_abs": rounded(turnover),
        "total_cost_abs": rounded(total_cost),
        "total_cost_bps": rounded(total_cost_bps),
    }
    return metrics, sorted(set(errors))


def _challenger_is_eligible(
    challenger: Mapping[str, Any],
    champion: Mapping[str, Any],
    config: Mapping[str, Any],
) -> bool:
    expectancy_delta = finite_or(
        config.get("minimum_expectancy_delta"),
        0.0,
    )
    profit_factor_delta = finite_or(
        config.get("minimum_profit_factor_delta"),
        0.0,
    )
    drawdown_regression_ratio = finite_or(
        config.get("maximum_drawdown_regression_ratio"),
        0.10,
    )
    return (
        _metric_value(challenger, "expectancy_abs_per_trade")
        >= _metric_value(champion, "expectancy_abs_per_trade")
        + expectancy_delta
        and _profit_factor_value(challenger)
        >= _profit_factor_value(champion) + profit_factor_delta
        and _metric_value(challenger, "max_drawdown_abs")
        <= _metric_value(champion, "max_drawdown_abs")
        * (1.0 + drawdown_regression_ratio)
    )


def evaluate_paper_ab(
    payload: Any,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate champion and challenger samples on one shared paper window."""

    source = mapping(payload)
    paper_ab_config = mapping(config.get("paper_ab"))
    minimum_trade_count = positive_int(
        paper_ab_config.get("minimum_trades_per_strategy"),
        30,
    )
    maximum_cost_bps = finite_or(
        paper_ab_config.get("maximum_total_cost_bps"),
        50.0,
    )

    champion = mapping(source.get("champion"))
    champion_metrics, champion_errors = strategy_metrics(
        champion,
        minimum_trade_count,
        maximum_cost_bps,
    )
    blockers = [f"champion:{item}" for item in champion_errors]
    evaluation_window_id = str(
        champion.get("evaluation_window_id") or ""
    ).strip()

    challengers: list[dict[str, Any]] = []
    comparable_challengers: list[dict[str, Any]] = []
    for index, candidate in enumerate(
        mapping_list(source.get("challengers"))
    ):
        candidate_id = str(
            candidate.get("strategy_id") or f"challenger-{index}"
        )
        metrics, validation_errors = strategy_metrics(
            candidate,
            minimum_trade_count,
            maximum_cost_bps,
        )
        candidate_window_id = str(
            candidate.get("evaluation_window_id") or ""
        ).strip()
        if evaluation_window_id and candidate_window_id != evaluation_window_id:
            validation_errors.append("evaluation_window_mismatch")

        validation_errors = sorted(set(validation_errors))
        blockers.extend(
            f"{candidate_id}:{item}"
            for item in validation_errors
        )
        row = {
            "strategy_id": candidate_id,
            "metrics": metrics,
            "validation_errors": validation_errors,
        }
        challengers.append(row)
        if not validation_errors:
            comparable_challengers.append(row)

    if not challengers:
        blockers.append("challenger_missing")
    if not comparable_challengers:
        blockers.append("no_comparable_challenger")

    eligible_challengers = [
        row
        for row in comparable_challengers
        if not champion_errors
        and _challenger_is_eligible(
            mapping(row.get("metrics")),
            champion_metrics,
            paper_ab_config,
        )
    ]
    selected = max(
        eligible_challengers,
        key=lambda row: (
            _metric_value(
                mapping(row.get("metrics")),
                "expectancy_abs_per_trade",
            ),
            _profit_factor_value(mapping(row.get("metrics"))),
        ),
        default=None,
    )
    recommendation = {
        "action": (
            "QUARANTINE_CHALLENGER_FOR_SOAK"
            if selected is not None
            else "KEEP_CHAMPION"
        ),
        "strategy_id": (
            selected["strategy_id"]
            if selected is not None
            else champion_metrics.get("strategy_id")
        ),
        "automatic_promotion": False,
        "operational_authority": False,
    }

    passed = not blockers
    return gate(
        passed,
        (
            "paper_ab_evidence_complete"
            if passed
            else "paper_ab_evidence_incomplete_or_invalid"
        ),
        blockers,
        champion=champion_metrics,
        challengers=challengers,
        eligible_challenger_count=len(eligible_challengers),
        recommendation=recommendation,
        model_promotion_performed=False,
        automatic_promotion=False,
    )
