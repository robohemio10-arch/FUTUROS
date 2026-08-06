"""Capacity and market-impact envelope evaluation for B06."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from typing import Any

from .contracts import gate, mapping, mapping_list
from .io import finite, finite_or, positive_int, rounded

_CAPACITY_FIELDS = (
    "stake",
    "notional",
    "depth_usdt",
    "leverage",
    "participation_ratio",
    "frequency_per_hour",
    "turnover_per_day",
    "spread_bps",
    "slippage_bps",
    "market_impact_bps",
    "liquidation_buffer_pct",
)


def _capacity_values(
    row: Mapping[str, Any],
) -> tuple[
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
] | None:
    values = [finite(row.get(field)) for field in _CAPACITY_FIELDS]
    if any(value is None for value in values):
        return None
    return tuple(float(value) for value in values)  # type: ignore[arg-type,return-value]


def evaluate_capacity(
    payload: Any,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and summarize capacity by symbol, stake and leverage."""

    rows = mapping_list(mapping(payload).get("observations"))
    capacity_config = mapping(config.get("capacity"))
    required_symbols = tuple(
        str(item).upper()
        for item in (
            capacity_config.get("required_symbols")
            or ("BTCUSDT", "ETHUSDT")
        )
    )
    minimum_observations = positive_int(
        capacity_config.get("minimum_observations_per_symbol"),
        3,
    )
    maximum_cost_bps = finite_or(
        capacity_config.get("maximum_total_execution_cost_bps"),
        50.0,
    )
    maximum_participation_ratio = finite_or(
        capacity_config.get("maximum_participation_ratio"),
        0.05,
    )
    maximum_leverage = finite_or(
        capacity_config.get("maximum_leverage"),
        3.0,
    )
    minimum_liquidation_buffer_pct = finite_or(
        capacity_config.get("minimum_liquidation_buffer_pct"),
        15.0,
    )

    blockers: list[str] = []
    observation_ids: list[str] = []
    counts: Counter[str] = Counter()
    metrics: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for index, row in enumerate(rows):
        observation_id = str(
            row.get("observation_id") or ""
        ).strip()
        symbol = str(row.get("symbol") or "").strip().upper()
        display_id = observation_id or f"observation-{index}"
        observation_ids.append(observation_id)

        if not observation_id:
            blockers.append(f"{display_id}:observation_id_missing")
        if symbol not in required_symbols:
            blockers.append(f"{display_id}:symbol_not_required")

        values = _capacity_values(row)
        if values is None:
            blockers.append(f"{display_id}:numeric_fields_invalid")
            continue
        (
            stake,
            notional,
            depth_usdt,
            leverage,
            participation_ratio,
            frequency_per_hour,
            turnover_per_day,
            spread_bps,
            slippage_bps,
            market_impact_bps,
            liquidation_buffer_pct,
        ) = values

        if symbol not in required_symbols:
            continue
        counts[symbol] += 1
        symbol_metrics = metrics[symbol]
        for field, value in zip(_CAPACITY_FIELDS, values, strict=True):
            symbol_metrics[field].append(value)

        if stake <= 0 or notional <= 0 or depth_usdt <= 0:
            blockers.append(
                f"{display_id}:stake_notional_and_depth_must_be_positive"
            )
        expected_notional = stake * leverage
        relative_notional_error = (
            abs(notional - expected_notional) / expected_notional
            if expected_notional > 0
            else 1.0
        )
        if relative_notional_error > 0.05:
            blockers.append(
                f"{display_id}:stake_notional_leverage_mismatch"
            )
        if frequency_per_hour <= 0:
            blockers.append(
                f"{display_id}:frequency_per_hour_must_be_positive"
            )
        if turnover_per_day <= 0:
            blockers.append(
                f"{display_id}:turnover_per_day_must_be_positive"
            )
        if participation_ratio > maximum_participation_ratio:
            blockers.append(
                f"{display_id}:participation_ratio_exceeds_limit"
            )
        if leverage > maximum_leverage:
            blockers.append(f"{display_id}:leverage_exceeds_limit")
        if liquidation_buffer_pct < minimum_liquidation_buffer_pct:
            blockers.append(
                f"{display_id}:liquidation_buffer_below_limit"
            )
        total_execution_cost_bps = (
            spread_bps + slippage_bps + market_impact_bps
        )
        if total_execution_cost_bps > maximum_cost_bps:
            blockers.append(
                f"{display_id}:total_execution_cost_exceeds_limit"
            )

    nonempty_ids = [item for item in observation_ids if item]
    if len(nonempty_ids) != len(set(nonempty_ids)):
        blockers.append("duplicate_observation_ids")
    blockers.extend(
        f"{symbol}:insufficient_capacity_observations"
        for symbol in required_symbols
        if counts[symbol] < minimum_observations
    )

    envelope_by_symbol: dict[str, dict[str, Any]] = {}
    for symbol in required_symbols:
        symbol_metrics = metrics.get(symbol, {})
        depths = symbol_metrics.get("depth_usdt", [])
        if not depths:
            continue
        safe_notional = min(depths) * maximum_participation_ratio
        safe_stake = safe_notional / maximum_leverage
        execution_costs = [
            spread + slippage + impact
            for spread, slippage, impact in zip(
                symbol_metrics["spread_bps"],
                symbol_metrics["slippage_bps"],
                symbol_metrics["market_impact_bps"],
                strict=True,
            )
        ]
        envelope_by_symbol[symbol] = {
            "observation_count": counts[symbol],
            "safe_notional_abs": rounded(safe_notional),
            "safe_stake_at_max_leverage_abs": rounded(safe_stake),
            "maximum_configured_leverage": rounded(maximum_leverage),
            "observed_maximum_leverage": rounded(
                max(symbol_metrics["leverage"])
            ),
            "observed_maximum_frequency_per_hour": rounded(
                max(symbol_metrics["frequency_per_hour"])
            ),
            "observed_maximum_turnover_per_day_abs": rounded(
                max(symbol_metrics["turnover_per_day"])
            ),
            "observed_maximum_execution_cost_bps": rounded(
                max(execution_costs)
            ),
            "observed_maximum_market_impact_bps": rounded(
                max(symbol_metrics["market_impact_bps"])
            ),
        }

    safe_notional_by_symbol = {
        symbol: envelope["safe_notional_abs"]
        for symbol, envelope in envelope_by_symbol.items()
    }
    passed = not blockers
    return gate(
        passed,
        (
            "capacity_evidence_complete"
            if passed
            else "capacity_evidence_incomplete_or_invalid"
        ),
        blockers,
        required_symbols=list(required_symbols),
        minimum_observations_per_symbol=minimum_observations,
        observation_count_by_symbol=dict(counts),
        safe_notional_by_symbol=safe_notional_by_symbol,
        envelope_by_symbol=envelope_by_symbol,
        dimensions_measured=[
            "stake",
            "symbol",
            "frequency",
            "turnover",
            "leverage",
            "spread",
            "slippage",
            "market_impact",
            "liquidation_buffer",
        ],
        recommendations_are_advisory=True,
        risk_configuration_changed=False,
    )
