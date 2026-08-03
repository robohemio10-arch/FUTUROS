"""Paper A/B financial comparison without promotion authority."""
from __future__ import annotations
import math
from collections.abc import Mapping
from typing import Any
from .contracts import gate, mapping, mapping_list
from .io import finite, finite_or, positive_int, rounded

def _pf(metrics: Mapping[str, Any]) -> float:
    return math.inf if metrics.get("profit_factor_infinite") else float(metrics.get("profit_factor") or 0.0)

def strategy_metrics(strategy: Mapping[str, Any], minimum: int,
                     maximum_cost_bps: float) -> tuple[dict[str, Any], list[str]]:
    strategy_id = str(strategy.get("strategy_id") or "")
    window_id = str(strategy.get("evaluation_window_id") or "")
    trades = mapping_list(strategy.get("trades")); errors: list[str] = []
    if not strategy_id: errors.append("strategy_id_missing")
    if not window_id: errors.append("evaluation_window_id_missing")
    if not trades: errors.append("trades_missing")
    if len(trades) < minimum: errors.append("insufficient_trade_count")
    ids: list[str] = []; pnls: list[float] = []; turnover = total_cost = 0.0
    for index, trade in enumerate(trades):
        trade_id = str(trade.get("trade_id") or ""); ids.append(trade_id)
        if not trade_id: errors.append(f"trade_{index}:trade_id_missing")
        if str(trade.get("symbol") or "").upper() not in {"BTCUSDT", "ETHUSDT"}:
            errors.append(f"trade_{index}:symbol_invalid")
        if str(trade.get("side") or "").lower() not in {"long", "short"}:
            errors.append(f"trade_{index}:side_invalid")
        if not trade.get("close_time_utc"): errors.append(f"trade_{index}:close_time_utc_missing")
        values = [finite(trade.get(name)) for name in ("net_pnl", "notional", "fees", "funding")]
        if any(value is None for value in values):
            errors.append(f"trade_{index}:financial_fields_invalid"); continue
        pnl, notional, fee, funding = values
        assert pnl is not None and notional is not None and fee is not None and funding is not None
        if notional <= 0: errors.append(f"trade_{index}:notional_must_be_positive"); continue
        pnls.append(pnl); turnover += notional; total_cost += abs(fee) + abs(funding)
    if len(ids) != len(set(ids)): errors.append("duplicate_trade_ids")
    wins = [value for value in pnls if value > 0]; losses = [value for value in pnls if value < 0]
    gross_profit, gross_loss = sum(wins), sum(losses)
    cost_bps = total_cost / turnover * 10_000 if turnover else None
    if cost_bps is None: errors.append("total_cost_bps_unavailable")
    elif cost_bps > maximum_cost_bps: errors.append("total_cost_bps_exceeds_limit")
    equity = peak = drawdown = 0.0
    for pnl in pnls:
        equity += pnl; peak = max(peak, equity); drawdown = max(drawdown, peak - equity)
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    metrics = {
        "strategy_id": strategy_id or None, "evaluation_window_id": window_id or None,
        "trade_count": len(pnls), "win_count": len(wins), "loss_count": len(losses),
        "net_pnl_abs": rounded(sum(pnls)),
        "profit_factor": rounded(gross_profit / abs(gross_loss) if gross_loss < 0 else None),
        "profit_factor_infinite": bool(pnls and not losses and gross_profit > 0),
        "expectancy_abs_per_trade": rounded(sum(pnls) / len(pnls) if pnls else None),
        "win_rate": rounded(len(wins) / len(pnls) if pnls else None),
        "payoff": rounded(avg_win / abs(avg_loss) if avg_win is not None and avg_loss else None),
        "max_drawdown_abs": rounded(drawdown), "turnover_abs": rounded(turnover),
        "total_cost_abs": rounded(total_cost), "total_cost_bps": rounded(cost_bps),
    }
    return metrics, sorted(set(errors))

def evaluate_paper_ab(payload: Any, config: Mapping[str, Any]) -> dict[str, Any]:
    source, cfg = mapping(payload), mapping(config.get("paper_ab"))
    minimum = positive_int(cfg.get("minimum_trades_per_strategy"), 30)
    max_cost = finite_or(cfg.get("maximum_total_cost_bps"), 50.0)
    champion = mapping(source.get("champion"))
    champion_metrics, champion_errors = strategy_metrics(champion, minimum, max_cost)
    blockers = [f"champion:{item}" for item in champion_errors]
    window = str(champion.get("evaluation_window_id") or "")
    challengers: list[dict[str, Any]] = []; comparable: list[dict[str, Any]] = []
    for index, candidate in enumerate(mapping_list(source.get("challengers"))):
        candidate_id = str(candidate.get("strategy_id") or f"challenger-{index}")
        metrics, errors = strategy_metrics(candidate, minimum, max_cost)
        if window and str(candidate.get("evaluation_window_id") or "") != window:
            errors.append("evaluation_window_mismatch")
        blockers += [f"{candidate_id}:{item}" for item in errors]
        row = {"strategy_id": candidate_id, "metrics": metrics, "validation_errors": sorted(set(errors))}
        challengers.append(row)
        if not errors: comparable.append(row)
    if not challengers: blockers.append("challenger_missing")
    if not comparable: blockers.append("no_comparable_challenger")
    eligible: list[dict[str, Any]] = []
    if not champion_errors:
        for row in comparable:
            metrics = row["metrics"]
            if (float(metrics.get("expectancy_abs_per_trade") or 0) >= float(champion_metrics.get("expectancy_abs_per_trade") or 0) + finite_or(cfg.get("minimum_expectancy_delta"), 0)
                and _pf(metrics) >= _pf(champion_metrics) + finite_or(cfg.get("minimum_profit_factor_delta"), 0)
                and float(metrics.get("max_drawdown_abs") or 0) <= float(champion_metrics.get("max_drawdown_abs") or 0) * (1 + finite_or(cfg.get("maximum_drawdown_regression_ratio"), .1))):
                eligible.append(row)
    selected = max(eligible, key=lambda row: (float(row["metrics"].get("expectancy_abs_per_trade") or 0), _pf(row["metrics"])), default=None)
    recommendation = {
        "action": "QUARANTINE_CHALLENGER_FOR_SOAK" if selected else "KEEP_CHAMPION",
        "strategy_id": selected["strategy_id"] if selected else champion_metrics.get("strategy_id"),
        "automatic_promotion": False, "operational_authority": False,
    }
    return gate(not blockers, "paper_ab_evidence_complete" if not blockers else "paper_ab_evidence_incomplete_or_invalid", blockers,
                champion=champion_metrics, challengers=challengers,
                recommendation=recommendation, model_promotion_performed=False,
                automatic_promotion=False)
