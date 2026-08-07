from __future__ import annotations

import importlib.util
import sys
import types
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
STRATEGY_PATH = (
    ROOT
    / "freqtrade"
    / "user_data"
    / "strategies"
    / "SmartCryptoSignalStrategy.py"
)


class StubIStrategy:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}


def _load_strategy_class() -> type:
    freqtrade_module = types.ModuleType("freqtrade")
    strategy_module = types.ModuleType("freqtrade.strategy")
    strategy_module.IStrategy = StubIStrategy
    freqtrade_module.strategy = strategy_module

    previous_freqtrade = sys.modules.get("freqtrade")
    previous_strategy = sys.modules.get("freqtrade.strategy")
    sys.modules["freqtrade"] = freqtrade_module
    sys.modules["freqtrade.strategy"] = strategy_module
    try:
        spec = importlib.util.spec_from_file_location(
            "smartcrypto_test_strategy_exit_idempotency",
            STRATEGY_PATH,
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.SmartCryptoSignalStrategy
    finally:
        if previous_freqtrade is None:
            sys.modules.pop("freqtrade", None)
        else:
            sys.modules["freqtrade"] = previous_freqtrade
        if previous_strategy is None:
            sys.modules.pop("freqtrade.strategy", None)
        else:
            sys.modules["freqtrade.strategy"] = previous_strategy


Strategy = _load_strategy_class()


@dataclass
class FakeOrder:
    ft_is_open: bool | None = True
    ft_order_side: str | None = None
    side: str | None = None
    status: str | None = "open"


@dataclass
class FakeTrade:
    id: int
    amount: float
    exit_side: str
    open_orders: list[FakeOrder]
    is_open: bool = True


class BrokenOpenOrdersTrade:
    id = 999
    amount = 0.05
    exit_side = "buy"
    is_open = True

    @property
    def open_orders(self) -> list[FakeOrder]:
        raise LookupError("simulated open-order inspection failure")


def _strategy(*, dry_run: bool = True) -> Any:
    return Strategy({"dry_run": dry_run})


def _confirm(
    strategy: Any,
    trade: Any,
    *,
    amount: float | None = None,
    exit_reason: str = "roi",
) -> bool:
    requested_amount = trade.amount if amount is None else amount
    return bool(
        strategy.confirm_trade_exit(
            pair="ETH/USDT:USDT",
            trade=trade,
            order_type="limit",
            amount=requested_amount,
            rate=1900.0,
            time_in_force="GTC",
            exit_reason=exit_reason,
            current_time=datetime(2026, 8, 7, 14, 5, tzinfo=timezone.utc),
        )
    )


@pytest.mark.parametrize(
    ("trade_id", "amount", "exit_side"),
    [
        (141, 0.060, "sell"),
        (258, 0.060, "sell"),
        (561, 0.056, "sell"),
        (653, 0.051, "buy"),
    ],
)
def test_duplicate_full_roi_exit_is_rejected_for_observed_incidents(
    trade_id: int,
    amount: float,
    exit_side: str,
) -> None:
    trade = FakeTrade(
        id=trade_id,
        amount=amount,
        exit_side=exit_side,
        open_orders=[
            FakeOrder(
                ft_is_open=True,
                ft_order_side=exit_side,
                side=exit_side,
                status="open",
            )
        ],
    )

    assert _confirm(_strategy(), trade, exit_reason="roi") is False


def test_first_full_roi_exit_without_pending_order_is_allowed() -> None:
    trade = FakeTrade(id=653, amount=0.051, exit_side="buy", open_orders=[])

    assert _confirm(_strategy(), trade, exit_reason="roi") is True


def test_open_entry_order_does_not_block_exit() -> None:
    trade = FakeTrade(
        id=653,
        amount=0.051,
        exit_side="buy",
        open_orders=[
            FakeOrder(
                ft_is_open=True,
                ft_order_side="sell",
                side="sell",
                status="open",
            )
        ],
    )

    assert _confirm(_strategy(), trade, exit_reason="roi") is True


@pytest.mark.parametrize(
    "exit_reason",
    [
        "stop_loss",
        "stoploss_on_exchange",
        "trailing_stop_loss",
        "emergency_exit",
        "force_exit",
    ],
)
def test_protective_exit_reasons_are_never_blocked_by_pending_regular_exit(
    exit_reason: str,
) -> None:
    trade = FakeTrade(
        id=653,
        amount=0.051,
        exit_side="buy",
        open_orders=[
            FakeOrder(
                ft_is_open=True,
                ft_order_side="buy",
                side="buy",
                status="open",
            )
        ],
    )

    assert _confirm(_strategy(), trade, exit_reason=exit_reason) is True


def test_partial_exit_request_is_not_blocked_by_full_exit_guard() -> None:
    trade = FakeTrade(
        id=653,
        amount=0.051,
        exit_side="buy",
        open_orders=[
            FakeOrder(
                ft_is_open=True,
                ft_order_side="buy",
                side="buy",
                status="open",
            )
        ],
    )

    assert _confirm(_strategy(), trade, amount=0.025, exit_reason="roi") is True


def test_non_paper_runtime_bypasses_paper_only_guard() -> None:
    trade = FakeTrade(
        id=653,
        amount=0.051,
        exit_side="buy",
        open_orders=[
            FakeOrder(
                ft_is_open=True,
                ft_order_side="buy",
                side="buy",
                status="open",
            )
        ],
    )

    assert _confirm(_strategy(dry_run=False), trade, exit_reason="roi") is True


def test_closed_trade_rejects_non_protective_exit_fail_closed() -> None:
    trade = FakeTrade(
        id=653,
        amount=0.051,
        exit_side="buy",
        open_orders=[],
        is_open=False,
    )

    assert _confirm(_strategy(), trade, exit_reason="roi") is False


def test_open_order_inspection_failure_rejects_non_protective_exit_fail_closed() -> None:
    assert _confirm(_strategy(), BrokenOpenOrdersTrade(), exit_reason="roi") is False


def test_protective_exit_bypasses_broken_open_order_inspection() -> None:
    assert _confirm(_strategy(), BrokenOpenOrdersTrade(), exit_reason="stop_loss") is True


def test_unknown_open_order_state_rejects_non_protective_exit_fail_closed() -> None:
    trade = FakeTrade(
        id=653,
        amount=0.051,
        exit_side="buy",
        open_orders=[
            FakeOrder(
                ft_is_open=None,
                ft_order_side="buy",
                side="buy",
                status=None,
            )
        ],
    )

    assert _confirm(_strategy(), trade, exit_reason="roi") is False


def test_terminal_order_does_not_block_new_exit() -> None:
    trade = FakeTrade(
        id=653,
        amount=0.051,
        exit_side="buy",
        open_orders=[
            FakeOrder(
                ft_is_open=False,
                ft_order_side="buy",
                side="buy",
                status="closed",
            )
        ],
    )

    assert _confirm(_strategy(), trade, exit_reason="roi") is True


def test_status_fallback_detects_pending_exit_when_ft_is_open_is_missing() -> None:
    trade = FakeTrade(
        id=653,
        amount=0.051,
        exit_side="buy",
        open_orders=[
            FakeOrder(
                ft_is_open=None,
                ft_order_side="buy",
                side="buy",
                status="open",
            )
        ],
    )

    assert _confirm(_strategy(), trade, exit_reason="roi") is False


@pytest.mark.parametrize("bad_amount", [0.0, -0.1, float("nan"), float("inf")])
def test_invalid_full_exit_amount_rejects_non_protective_exit_fail_closed(
    bad_amount: float,
) -> None:
    trade = FakeTrade(id=653, amount=0.051, exit_side="buy", open_orders=[])

    assert _confirm(_strategy(), trade, amount=bad_amount, exit_reason="roi") is False


def test_invalid_trade_amount_rejects_non_protective_exit_fail_closed() -> None:
    trade = FakeTrade(id=653, amount=float("nan"), exit_side="buy", open_orders=[])

    assert _confirm(_strategy(), trade, amount=0.051, exit_reason="roi") is False


def test_existing_risk_parameters_are_unchanged() -> None:
    assert Strategy.minimal_roi == {"0": 0.02}
    assert Strategy.stoploss == -0.015
    assert Strategy.trailing_stop is False
    assert Strategy.use_exit_signal is True
    assert Strategy.ignore_roi_if_entry_signal is False


def test_guard_does_not_add_position_adjustment_or_live_controls() -> None:
    assert not hasattr(Strategy, "adjust_trade_position")
    assert not hasattr(Strategy, "custom_stoploss")
    assert not hasattr(Strategy, "custom_roi")
