"""Versioned per-fill costs, funding, slippage, and accounting reconciliation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Iterable

from smartcrypto.data.canonical_data_foundation_v2.contracts import stable_hash

from .contracts import (
    ContractViolation,
    Fill,
    LiquidityRole,
    Side,
    SlippageModel,
    decimal_text,
    decimal_value,
)

ZERO = Decimal("0")
BPS = Decimal("10000")
MONEY_QUANTUM = Decimal("0.00000001")


@dataclass(frozen=True)
class CostModel:
    maker_fee_bps: Decimal | None
    taker_fee_bps: Decimal | None
    slippage_model: SlippageModel = SlippageModel.CONSERVATIVE_HYBRID
    fixed_slippage_bps: Decimal | None = Decimal("1")
    square_root_impact_coefficient: Decimal | None = Decimal("0.1")
    liquidation_penalty_bps: Decimal = Decimal("50")
    retry_cost: Decimal = ZERO
    reprice_cost: Decimal = ZERO
    version: str = "futures_cost_model_v2"

    def __post_init__(self) -> None:
        for name, value in (
            ("maker_fee_bps", self.maker_fee_bps),
            ("taker_fee_bps", self.taker_fee_bps),
            ("fixed_slippage_bps", self.fixed_slippage_bps),
            ("square_root_impact_coefficient", self.square_root_impact_coefficient),
            ("liquidation_penalty_bps", self.liquidation_penalty_bps),
            ("retry_cost", self.retry_cost),
            ("reprice_cost", self.reprice_cost),
        ):
            if value is not None and decimal_value(value, field_name=name) < 0:
                raise ContractViolation(f"negative_cost_component:{name}")

    @property
    def cost_model_hash(self) -> str:
        return stable_hash(self.to_dict())

    def fee_for_fill(
        self,
        *,
        quantity: Decimal,
        price: Decimal,
        contract_size: Decimal,
        liquidity_role: LiquidityRole,
    ) -> Decimal:
        if liquidity_role == LiquidityRole.UNKNOWN:
            raise ContractViolation("unknown_liquidity_role_blocks_fee")
        bps = (
            self.maker_fee_bps
            if liquidity_role == LiquidityRole.MAKER
            else self.taker_fee_bps
        )
        if bps is None:
            raise ContractViolation(f"{liquidity_role.value}_fee_missing")
        notional = quantity * price * contract_size
        return _money(notional * bps / BPS)

    def funding_cost(
        self,
        *,
        side: Side,
        quantity: Decimal,
        contract_size: Decimal,
        mark_price: Decimal,
        funding_rate: Decimal | None,
    ) -> Decimal:
        if funding_rate is None:
            raise ContractViolation("funding_rate_missing")
        rate = decimal_value(funding_rate, field_name="funding_rate")
        notional = quantity * contract_size * mark_price
        payer_direction = Decimal("1") if side.order_side == Side.BUY else Decimal("-1")
        return _money(notional * rate * payer_direction)

    def liquidation_penalty(
        self,
        *,
        quantity: Decimal,
        contract_size: Decimal,
        mark_price: Decimal,
    ) -> Decimal:
        return _money(
            quantity
            * contract_size
            * mark_price
            * self.liquidation_penalty_bps
            / BPS
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(
            {
                "maker_fee_bps": decimal_text(self.maker_fee_bps),
                "taker_fee_bps": decimal_text(self.taker_fee_bps),
                "slippage_model": self.slippage_model.value,
                "fixed_slippage_bps": decimal_text(self.fixed_slippage_bps),
                "square_root_impact_coefficient": decimal_text(
                    self.square_root_impact_coefficient
                ),
                "liquidation_penalty_bps": decimal_text(
                    self.liquidation_penalty_bps
                ),
                "retry_cost": decimal_text(self.retry_cost),
                "reprice_cost": decimal_text(self.reprice_cost),
            }
        )
        return payload


@dataclass(frozen=True)
class ExecutionCostAttribution:
    observed_spread_cost: Decimal
    observed_book_walk_cost: Decimal
    modeled_slippage_cost: Decimal
    modeled_market_impact_cost: Decimal
    uncertainty_cost: Decimal
    observed_component: Decimal
    modeled_component: Decimal
    authoritative: bool
    assumptions: tuple[str, ...]

    @property
    def explicit_price_cost(self) -> Decimal:
        return _money(
            self.observed_spread_cost
            + self.observed_book_walk_cost
            + self.modeled_slippage_cost
            + self.modeled_market_impact_cost
            + self.uncertainty_cost
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed_spread_cost": decimal_text(self.observed_spread_cost),
            "observed_book_walk_cost": decimal_text(self.observed_book_walk_cost),
            "modeled_slippage_cost": decimal_text(self.modeled_slippage_cost),
            "modeled_market_impact_cost": decimal_text(
                self.modeled_market_impact_cost
            ),
            "uncertainty_cost": decimal_text(self.uncertainty_cost),
            "observed_component": decimal_text(self.observed_component),
            "modeled_component": decimal_text(self.modeled_component),
            "explicit_price_cost": decimal_text(self.explicit_price_cost),
            "authoritative": self.authoritative,
            "assumptions": list(self.assumptions),
        }


@dataclass(frozen=True)
class CostSummary:
    trading_fees: Decimal
    funding_fees: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    market_impact_cost: Decimal
    liquidation_penalty: Decimal
    retry_reprice_costs: Decimal
    other_supported_costs: Decimal
    realized_price_pnl: Decimal
    net_pnl: Decimal
    reconciliation_residual: Decimal

    @property
    def total_cost(self) -> Decimal:
        return _money(
            self.trading_fees
            + self.funding_fees
            + self.spread_cost
            + self.slippage_cost
            + self.market_impact_cost
            + self.liquidation_penalty
            + self.retry_reprice_costs
            + self.other_supported_costs
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trading_fees": decimal_text(self.trading_fees),
            "funding_fees": decimal_text(self.funding_fees),
            "spread_cost": decimal_text(self.spread_cost),
            "slippage_cost": decimal_text(self.slippage_cost),
            "market_impact_cost": decimal_text(self.market_impact_cost),
            "liquidation_penalty": decimal_text(self.liquidation_penalty),
            "retry_reprice_costs": decimal_text(self.retry_reprice_costs),
            "other_supported_costs": decimal_text(self.other_supported_costs),
            "total_cost": decimal_text(self.total_cost),
            "realized_price_pnl": decimal_text(self.realized_price_pnl),
            "net_pnl": decimal_text(self.net_pnl),
            "reconciliation_residual": decimal_text(self.reconciliation_residual),
        }


def attribute_execution_cost(
    *,
    side: Side,
    quantity: Decimal,
    contract_size: Decimal,
    mid_price: Decimal,
    best_quote: Decimal,
    fill_price: Decimal,
    cost_model: CostModel,
    reference_volume: Decimal | None = None,
    volatility: Decimal | None = None,
) -> ExecutionCostAttribution:
    side_sign = Decimal("1") if side.order_side == Side.BUY else Decimal("-1")
    notional_multiplier = quantity * contract_size
    observed_spread = max(
        ZERO,
        side_sign * (best_quote - mid_price) * notional_multiplier,
    )
    observed_walk = max(
        ZERO,
        side_sign * (fill_price - best_quote) * notional_multiplier,
    )
    fixed = ZERO
    square_root = ZERO
    assumptions: list[str] = []
    authoritative = True

    if cost_model.slippage_model in {
        SlippageModel.FIXED_BPS,
        SlippageModel.CONSERVATIVE_HYBRID,
    }:
        if cost_model.fixed_slippage_bps is None:
            raise ContractViolation("fixed_slippage_bps_missing")
        fixed = (
            fill_price
            * notional_multiplier
            * cost_model.fixed_slippage_bps
            / BPS
        )
        assumptions.append("fixed_bps_is_modeled_hypothesis")
        authoritative = False

    if cost_model.slippage_model in {
        SlippageModel.SQUARE_ROOT_IMPACT,
        SlippageModel.CONSERVATIVE_HYBRID,
    }:
        if reference_volume is None or volatility is None:
            if cost_model.slippage_model == SlippageModel.SQUARE_ROOT_IMPACT:
                raise ContractViolation("square_root_impact_inputs_missing")
            assumptions.append("square_root_impact_unavailable")
            authoritative = False
        else:
            if reference_volume <= 0 or volatility < 0:
                raise ContractViolation("invalid_square_root_impact_inputs")
            coefficient = cost_model.square_root_impact_coefficient
            if coefficient is None:
                raise ContractViolation("square_root_impact_coefficient_missing")
            participation = quantity / reference_volume
            square_root = (
                fill_price
                * notional_multiplier
                * coefficient
                * volatility
                * participation.sqrt()
            )
            assumptions.append("square_root_market_impact_modeled")
            authoritative = False

    modeled_slippage = ZERO
    modeled_impact = ZERO
    uncertainty = ZERO
    if cost_model.slippage_model == SlippageModel.FIXED_BPS:
        modeled_slippage = fixed
    elif cost_model.slippage_model == SlippageModel.SQUARE_ROOT_IMPACT:
        modeled_impact = square_root
    elif cost_model.slippage_model == SlippageModel.CONSERVATIVE_HYBRID:
        modeled_slippage = fixed
        modeled_impact = square_root
        uncertainty = max(fixed, square_root)
    elif cost_model.slippage_model != SlippageModel.OBSERVED_BOOK_WALK:
        raise ContractViolation("unsupported_slippage_model")

    observed_component = _money(observed_spread + observed_walk)
    modeled_component = _money(modeled_slippage + modeled_impact)
    return ExecutionCostAttribution(
        observed_spread_cost=_money(observed_spread),
        observed_book_walk_cost=_money(observed_walk),
        modeled_slippage_cost=_money(modeled_slippage),
        modeled_market_impact_cost=_money(modeled_impact),
        uncertainty_cost=_money(uncertainty),
        observed_component=observed_component,
        modeled_component=modeled_component,
        authoritative=authoritative,
        assumptions=tuple(sorted(set(assumptions))),
    )


def reconcile_costs(
    *,
    realized_price_pnl: Decimal,
    fills: Iterable[Fill],
    funding_fees: Decimal,
    spread_cost: Decimal,
    slippage_cost: Decimal,
    market_impact_cost: Decimal,
    liquidation_penalty: Decimal = ZERO,
    retry_reprice_costs: Decimal = ZERO,
    other_supported_costs: Decimal = ZERO,
    reported_net_pnl: Decimal | None = None,
) -> CostSummary:
    fill_rows = tuple(fills)
    if any(fill.fee is None for fill in fill_rows):
        raise ContractViolation("fill_fee_missing_blocks_cost_reconciliation")
    trading_fees = sum(
        (fill.fee or ZERO for fill in fill_rows),
        ZERO,
    )
    expected_net = _money(
        realized_price_pnl
        - trading_fees
        - funding_fees
        - spread_cost
        - slippage_cost
        - market_impact_cost
        - liquidation_penalty
        - retry_reprice_costs
        - other_supported_costs
    )
    observed_net = expected_net if reported_net_pnl is None else _money(reported_net_pnl)
    residual = _money(observed_net - expected_net)
    return CostSummary(
        trading_fees=_money(trading_fees),
        funding_fees=_money(funding_fees),
        spread_cost=_money(spread_cost),
        slippage_cost=_money(slippage_cost),
        market_impact_cost=_money(market_impact_cost),
        liquidation_penalty=_money(liquidation_penalty),
        retry_reprice_costs=_money(retry_reprice_costs),
        other_supported_costs=_money(other_supported_costs),
        realized_price_pnl=_money(realized_price_pnl),
        net_pnl=observed_net,
        reconciliation_residual=residual,
    )


def _money(value: Decimal) -> Decimal:
    return decimal_value(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)
