"""Isolated/cross margin, mark-price authority, and conservative liquidation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .contracts import (
    ContractViolation,
    LiquidityRole,
    MarginMode,
    Side,
    decimal_text,
    decimal_value,
)
from .costs import BPS, CostModel, ZERO


@dataclass(frozen=True)
class MaintenanceTier:
    notional_cap: Decimal
    maintenance_margin_rate: Decimal
    maintenance_amount: Decimal = ZERO

    def __post_init__(self) -> None:
        if self.notional_cap <= 0:
            raise ContractViolation("maintenance_tier_cap_must_be_positive")
        if not ZERO <= self.maintenance_margin_rate < Decimal("1"):
            raise ContractViolation("invalid_maintenance_margin_rate")
        if self.maintenance_amount < 0:
            raise ContractViolation("negative_maintenance_amount")


@dataclass(frozen=True)
class Position:
    position_id: str
    symbol: str
    side: Side
    quantity: Decimal
    entry_price: Decimal
    contract_size: Decimal | None
    leverage: Decimal | None
    margin_mode: MarginMode | None
    isolated_margin: Decimal | None = None
    realized_pnl: Decimal = ZERO
    funding_accrued: Decimal | None = ZERO

    def validate(self) -> None:
        if self.quantity <= 0:
            raise ContractViolation("position_quantity_must_be_positive")
        if self.entry_price <= 0:
            raise ContractViolation("position_entry_price_must_be_positive")
        if self.contract_size is None:
            raise ContractViolation("contract_size_missing")
        if self.contract_size <= 0:
            raise ContractViolation("contract_size_must_be_positive")
        if self.leverage is None:
            raise ContractViolation("leverage_missing")
        if self.leverage <= 0:
            raise ContractViolation("leverage_must_be_positive")
        if self.margin_mode is None:
            raise ContractViolation("margin_mode_missing")
        if self.funding_accrued is None:
            raise ContractViolation("funding_accrued_missing")
        if self.margin_mode == MarginMode.ISOLATED:
            if self.isolated_margin is None:
                raise ContractViolation("isolated_margin_missing")
            if self.isolated_margin < 0:
                raise ContractViolation("negative_isolated_margin")

    def notional(self, mark_price: Decimal) -> Decimal:
        self.validate()
        return self.quantity * (self.contract_size or ZERO) * mark_price

    def unrealized_pnl(self, mark_price: Decimal) -> Decimal:
        self.validate()
        return (
            (mark_price - self.entry_price)
            * self.quantity
            * (self.contract_size or ZERO)
            * self.side.direction
        )


@dataclass(frozen=True)
class MarginAccount:
    wallet_balance: Decimal
    available_balance: Decimal
    positions: tuple[Position, ...] = ()
    realized_pnl: Decimal = ZERO

    def __post_init__(self) -> None:
        if self.wallet_balance < 0 or self.available_balance < 0:
            raise ContractViolation("negative_margin_balance")
        if self.available_balance > self.wallet_balance + self.realized_pnl:
            raise ContractViolation("available_balance_exceeds_wallet")


@dataclass(frozen=True)
class MarginMetrics:
    mode: MarginMode
    wallet_balance: Decimal
    available_balance: Decimal
    initial_margin: Decimal
    maintenance_margin: Decimal
    position_margin: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    funding_accrued: Decimal
    liquidation_buffer: Decimal
    margin_ratio: Decimal | None
    liquidated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "margin_mode": self.mode.value,
            "wallet_balance": decimal_text(self.wallet_balance),
            "available_balance": decimal_text(self.available_balance),
            "initial_margin": decimal_text(self.initial_margin),
            "maintenance_margin": decimal_text(self.maintenance_margin),
            "position_margin": decimal_text(self.position_margin),
            "unrealized_pnl": decimal_text(self.unrealized_pnl),
            "realized_pnl": decimal_text(self.realized_pnl),
            "funding_accrued": decimal_text(self.funding_accrued),
            "liquidation_buffer": decimal_text(self.liquidation_buffer),
            "margin_ratio": decimal_text(self.margin_ratio),
            "liquidated": self.liquidated,
        }


@dataclass(frozen=True)
class LiquidationResult:
    liquidated: bool
    partial_liquidation: bool
    liquidation_price: Decimal | None
    liquidation_quantity: Decimal
    penalty: Decimal
    residual_position: Position | None
    bankruptcy_shortfall: Decimal
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "liquidated": self.liquidated,
            "partial_liquidation": self.partial_liquidation,
            "liquidation_price": decimal_text(self.liquidation_price),
            "liquidation_quantity": decimal_text(self.liquidation_quantity),
            "penalty": decimal_text(self.penalty),
            "residual_position": (
                position_to_dict(self.residual_position)
                if self.residual_position is not None
                else None
            ),
            "bankruptcy_shortfall": decimal_text(self.bankruptcy_shortfall),
            "reason": self.reason,
        }


class MarginEngine:
    def __init__(
        self,
        *,
        tiers: Sequence[MaintenanceTier],
        cost_model: CostModel,
        partial_liquidation_fraction: Decimal = Decimal("0.5"),
    ) -> None:
        if not tiers:
            raise ContractViolation("maintenance_tiers_required")
        ordered = tuple(sorted(tiers, key=lambda item: item.notional_cap))
        if len({tier.notional_cap for tier in ordered}) != len(ordered):
            raise ContractViolation("duplicate_maintenance_tier_cap")
        if not ZERO < partial_liquidation_fraction < Decimal("1"):
            raise ContractViolation("invalid_partial_liquidation_fraction")
        self.tiers = ordered
        self.cost_model = cost_model
        self.partial_liquidation_fraction = partial_liquidation_fraction

    def tier_for(self, notional: Decimal) -> MaintenanceTier:
        for tier in self.tiers:
            if notional <= tier.notional_cap:
                return tier
        raise ContractViolation("maintenance_tier_missing_for_notional")

    def initial_margin(self, position: Position, mark_price: Decimal) -> Decimal:
        position.validate()
        return position.notional(mark_price) / (position.leverage or Decimal("1"))

    def maintenance_margin(
        self,
        position: Position,
        mark_price: Decimal,
    ) -> Decimal:
        notional = position.notional(mark_price)
        tier = self.tier_for(notional)
        return max(
            ZERO,
            notional * tier.maintenance_margin_rate - tier.maintenance_amount,
        )

    def evaluate(
        self,
        *,
        account: MarginAccount,
        mark_prices: Mapping[str, Decimal],
        mode: MarginMode,
    ) -> MarginMetrics:
        if not account.positions:
            return MarginMetrics(
                mode=mode,
                wallet_balance=account.wallet_balance,
                available_balance=account.available_balance,
                initial_margin=ZERO,
                maintenance_margin=ZERO,
                position_margin=ZERO,
                unrealized_pnl=ZERO,
                realized_pnl=account.realized_pnl,
                funding_accrued=ZERO,
                liquidation_buffer=account.wallet_balance + account.realized_pnl,
                margin_ratio=ZERO,
                liquidated=False,
            )
        positions = tuple(
            position for position in account.positions if position.margin_mode == mode
        )
        if not positions:
            raise ContractViolation("no_positions_for_margin_mode")
        marks: dict[str, Decimal] = {}
        for position in positions:
            position.validate()
            if position.symbol not in mark_prices:
                raise ContractViolation(f"mark_price_missing:{position.symbol}")
            mark = decimal_value(
                mark_prices[position.symbol],
                field_name=f"mark_price:{position.symbol}",
            )
            if mark <= 0:
                raise ContractViolation("mark_price_must_be_positive")
            marks[position.symbol] = mark

        initial = sum(
            (self.initial_margin(position, marks[position.symbol]) for position in positions),
            ZERO,
        )
        maintenance = sum(
            (
                self.maintenance_margin(position, marks[position.symbol])
                for position in positions
            ),
            ZERO,
        )
        unrealized = sum(
            (position.unrealized_pnl(marks[position.symbol]) for position in positions),
            ZERO,
        )
        funding = sum(
            (position.funding_accrued or ZERO for position in positions),
            ZERO,
        )
        closing_fees = sum(
            (
                self.cost_model.fee_for_fill(
                    quantity=position.quantity,
                    price=marks[position.symbol],
                    contract_size=position.contract_size or ZERO,
                    liquidity_role=LiquidityRole.TAKER,
                )
                for position in positions
            ),
            ZERO,
        )
        if mode == MarginMode.CROSS:
            position_margin = account.wallet_balance + account.realized_pnl
        else:
            position_margin = sum(
                (position.isolated_margin or ZERO for position in positions),
                ZERO,
            )
        equity = position_margin + unrealized - funding
        requirement = maintenance + closing_fees
        buffer = equity - requirement
        ratio = requirement / equity if equity > 0 else None
        return MarginMetrics(
            mode=mode,
            wallet_balance=account.wallet_balance,
            available_balance=account.available_balance,
            initial_margin=initial,
            maintenance_margin=maintenance,
            position_margin=position_margin,
            unrealized_pnl=unrealized,
            realized_pnl=account.realized_pnl,
            funding_accrued=funding,
            liquidation_buffer=buffer,
            margin_ratio=ratio,
            liquidated=equity <= requirement,
        )

    def liquidation_threshold(self, position: Position) -> Decimal:
        position.validate()
        quantity_contract = position.quantity * (position.contract_size or ZERO)
        reference_notional = quantity_contract * position.entry_price
        tier = self.tier_for(reference_notional)
        taker_fee_rate = self._taker_fee_rate()
        funding = position.funding_accrued or ZERO
        allocated = (
            position.isolated_margin
            if position.margin_mode == MarginMode.ISOLATED
            else reference_notional / (position.leverage or Decimal("1"))
        )
        if allocated is None:
            raise ContractViolation("liquidation_margin_unavailable")
        if position.side.order_side == Side.BUY:
            denominator = quantity_contract * (
                Decimal("1") - tier.maintenance_margin_rate - taker_fee_rate
            )
            numerator = (
                quantity_contract * position.entry_price
                - allocated
                + funding
                - tier.maintenance_amount
            )
        else:
            denominator = quantity_contract * (
                Decimal("1") + tier.maintenance_margin_rate + taker_fee_rate
            )
            numerator = (
                allocated
                + quantity_contract * position.entry_price
                - funding
                + tier.maintenance_amount
            )
        if denominator <= 0:
            raise ContractViolation("invalid_liquidation_denominator")
        threshold = numerator / denominator
        if threshold <= 0:
            raise ContractViolation("invalid_liquidation_threshold")
        return threshold

    def liquidate(
        self,
        *,
        position: Position,
        account: MarginAccount,
        mark_price: Decimal,
        allow_partial: bool = True,
    ) -> LiquidationResult:
        metrics = self.evaluate(
            account=replace(account, positions=(position,)),
            mark_prices={position.symbol: mark_price},
            mode=position.margin_mode or MarginMode.ISOLATED,
        )
        if not metrics.liquidated:
            return LiquidationResult(
                liquidated=False,
                partial_liquidation=False,
                liquidation_price=None,
                liquidation_quantity=ZERO,
                penalty=ZERO,
                residual_position=position,
                bankruptcy_shortfall=ZERO,
                reason="margin_buffer_positive",
            )
        partial_quantity = (
            position.quantity * self.partial_liquidation_fraction
            if allow_partial
            else position.quantity
        )
        penalty = self.cost_model.liquidation_penalty(
            quantity=partial_quantity,
            contract_size=position.contract_size or ZERO,
            mark_price=mark_price,
        )
        residual_quantity = position.quantity - partial_quantity
        residual = (
            replace(position, quantity=residual_quantity)
            if residual_quantity > 0
            else None
        )
        partial_success = False
        if residual is not None and allow_partial:
            residual_margin = max(ZERO, (position.isolated_margin or ZERO) - penalty)
            residual = replace(residual, isolated_margin=residual_margin)
            try:
                residual_metrics = self.evaluate(
                    account=replace(account, positions=(residual,)),
                    mark_prices={position.symbol: mark_price},
                    mode=position.margin_mode or MarginMode.ISOLATED,
                )
            except ContractViolation:
                partial_success = False
            else:
                partial_success = not residual_metrics.liquidated
        if partial_success and residual is not None:
            return LiquidationResult(
                liquidated=True,
                partial_liquidation=True,
                liquidation_price=mark_price,
                liquidation_quantity=partial_quantity,
                penalty=penalty,
                residual_position=residual,
                bankruptcy_shortfall=ZERO,
                reason="partial_liquidation_restored_margin_buffer",
            )

        full_penalty = self.cost_model.liquidation_penalty(
            quantity=position.quantity,
            contract_size=position.contract_size or ZERO,
            mark_price=mark_price,
        )
        shortfall = max(ZERO, -metrics.liquidation_buffer + full_penalty)
        return LiquidationResult(
            liquidated=True,
            partial_liquidation=False,
            liquidation_price=mark_price,
            liquidation_quantity=position.quantity,
            penalty=full_penalty,
            residual_position=None,
            bankruptcy_shortfall=shortfall,
            reason="full_liquidation_required",
        )

    def _taker_fee_rate(self) -> Decimal:
        if self.cost_model.taker_fee_bps is None:
            raise ContractViolation("taker_fee_missing")
        return self.cost_model.taker_fee_bps / BPS


def resolve_stop_vs_liquidation(
    *,
    stop_reachable: bool,
    liquidation_reachable: bool,
    intrabar_order_known: bool,
    stop_first_when_known: bool | None = None,
) -> str:
    if stop_reachable and liquidation_reachable:
        if not intrabar_order_known:
            return "LIQUIDATION_FIRST"
        return "STOP_FIRST" if stop_first_when_known else "LIQUIDATION_FIRST"
    if liquidation_reachable:
        return "LIQUIDATION_ONLY"
    if stop_reachable:
        return "STOP_ONLY"
    return "NO_TRIGGER"


def position_to_dict(position: Position) -> dict[str, Any]:
    return {
        "position_id": position.position_id,
        "symbol": position.symbol,
        "side": position.side.value,
        "quantity": decimal_text(position.quantity),
        "entry_price": decimal_text(position.entry_price),
        "contract_size": decimal_text(position.contract_size),
        "leverage": decimal_text(position.leverage),
        "margin_mode": (
            position.margin_mode.value if position.margin_mode is not None else None
        ),
        "isolated_margin": decimal_text(position.isolated_margin),
        "realized_pnl": decimal_text(position.realized_pnl),
        "funding_accrued": decimal_text(position.funding_accrued),
    }
