from __future__ import annotations

import importlib.util
import inspect
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "freqtrade/user_data/config.paper.json"
STRATEGY_PATH = (
    ROOT / "freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py"
)
RETRY_REASON = "paper_exit_retry_latched"
TIMEOUT_CANCEL_REASON = "cancelled due to timeout"
CANCELLED_ON_EXCHANGE = "cancelled on exchange"
_DEFAULT_SAFE_FILL = object()


class OrderDouble:
    """Public Order fields used by Freqtrade 2026.6 exit callbacks."""

    def __init__(
        self,
        *,
        side: str | None = "sell",
        is_open: bool | None = False,
        status: str | None = "closed",
        tag: str | None = "roi",
        amount: float | None = 1.0,
        filled: float | None = 0.0,
        safe_filled: object = _DEFAULT_SAFE_FILL,
        cancel_reason: str | None = TIMEOUT_CANCEL_REASON,
    ) -> None:
        self.ft_order_side = side
        self.ft_is_open = is_open
        self.status = status
        self.ft_order_tag = tag
        self.amount = amount
        self.ft_amount = amount
        self.filled = filled
        self._safe_filled = safe_filled
        self.ft_cancel_reason = cancel_reason

    @property
    def safe_amount(self) -> float | None:
        return self.amount or self.ft_amount

    @property
    def safe_filled(self) -> Any:
        if self._safe_filled is not _DEFAULT_SAFE_FILL:
            return self._safe_filled
        return self.filled if self.filled is not None else 0.0


@pytest.fixture(scope="module")
def strategy_class() -> type[Any]:
    class IStrategy:
        pass

    freqtrade_module = types.ModuleType("freqtrade")
    constants_module = types.ModuleType("freqtrade.constants")
    strategy_module = types.ModuleType("freqtrade.strategy")
    setattr(constants_module, "CANCEL_REASON", {"TIMEOUT": TIMEOUT_CANCEL_REASON})
    setattr(strategy_module, "IStrategy", IStrategy)
    setattr(freqtrade_module, "constants", constants_module)
    setattr(freqtrade_module, "strategy", strategy_module)

    module_name = "smartcrypto_test_duplicate_full_exit_race_guard_strategy"
    previous_modules = {
        name: sys.modules.get(name)
        for name in (
            "freqtrade",
            "freqtrade.constants",
            "freqtrade.strategy",
            module_name,
        )
    }
    sys.modules["freqtrade"] = freqtrade_module
    sys.modules["freqtrade.constants"] = constants_module
    sys.modules["freqtrade.strategy"] = strategy_module

    try:
        spec = importlib.util.spec_from_file_location(module_name, STRATEGY_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        loaded_class = module.SmartCryptoSignalStrategy
    finally:
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous

    return loaded_class


@pytest.fixture
def strategy(strategy_class: type[Any]) -> Any:
    instance = strategy_class()
    instance._write_decision = lambda _payload: None
    return instance


def _trade(
    orders: list[Any] | tuple[Any, ...] | None = None,
    *,
    amount: float = 1.0,
    is_short: bool = False,
    is_open: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        pair="BTC/USDT:USDT",
        is_open=is_open,
        is_short=is_short,
        amount=amount,
        entry_side="sell" if is_short else "buy",
        exit_side="buy" if is_short else "sell",
        orders=[] if orders is None else orders,
    )


def _confirm(
    strategy: Any,
    trade: Any,
    *,
    exit_reason: str = "roi",
    amount: float | None = None,
) -> bool:
    requested_amount = trade.amount if amount is None else amount
    return strategy.confirm_trade_exit(
        pair=trade.pair,
        trade=trade,
        order_type="limit",
        amount=requested_amount,
        rate=100_000.0,
        time_in_force="GTC",
        exit_reason=exit_reason,
        current_time=datetime(2026, 8, 17, tzinfo=timezone.utc),
    )


def _custom_exit(strategy: Any, trade: Any) -> str | None:
    return strategy.custom_exit(
        pair=trade.pair,
        trade=trade,
        current_time=datetime(2026, 8, 17, tzinfo=timezone.utc),
        current_rate=100_000.0,
        current_profit=0.02,
    )


def test_confirm_trade_exit_signature_matches_freqtrade_2026_6(
    strategy_class: type[Any],
) -> None:
    assert list(inspect.signature(strategy_class.confirm_trade_exit).parameters) == [
        "self",
        "pair",
        "trade",
        "order_type",
        "amount",
        "rate",
        "time_in_force",
        "exit_reason",
        "current_time",
        "kwargs",
    ]


def test_first_roi_without_prior_exit_is_allowed(strategy: Any) -> None:
    assert _confirm(strategy, _trade()) is True


def test_open_exit_same_side_is_denied(strategy: Any) -> None:
    order = OrderDouble(is_open=True, status="open")
    assert _confirm(strategy, _trade([order])) is False


def test_open_status_metadata_inconsistency_is_denied(strategy: Any) -> None:
    order = OrderDouble(is_open=False, status="open")
    assert _confirm(strategy, _trade([order])) is False


def test_unknown_open_state_is_denied(strategy: Any) -> None:
    order = OrderDouble(is_open=None, status="open")
    assert _confirm(strategy, _trade([order])) is False


def test_trade_653_second_roi_is_denied(strategy: Any) -> None:
    prior_exit = OrderDouble(
        side="buy",
        status="closed",
        tag="roi",
        amount=0.051,
        filled=0.051,
        cancel_reason=CANCELLED_ON_EXCHANGE,
    )
    assert _confirm(strategy, _trade([prior_exit], amount=0.051, is_short=True)) is False


def test_trade_669_second_roi_is_denied(strategy: Any) -> None:
    prior_exit = OrderDouble(
        side="buy",
        status="closed",
        tag="roi",
        amount=0.001,
        filled=0.001,
        cancel_reason=CANCELLED_ON_EXCHANGE,
    )
    assert _confirm(strategy, _trade([prior_exit], amount=0.001, is_short=True)) is False


def test_partial_filled_prior_exit_is_denied(strategy: Any) -> None:
    assert _confirm(strategy, _trade([OrderDouble(filled=0.25)])) is False


def test_any_positive_prior_fill_is_denied_without_zero_tolerance(
    strategy: Any,
) -> None:
    assert _confirm(strategy, _trade([OrderDouble(filled=1e-15)])) is False


def test_unknown_raw_fill_is_denied_even_when_safe_fill_masks_zero(
    strategy: Any,
) -> None:
    order = OrderDouble(filled=None)
    assert order.safe_filled == 0.0
    assert _confirm(strategy, _trade([order])) is False


@pytest.mark.parametrize("filled", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_prior_fill_is_denied(strategy: Any, filled: float) -> None:
    assert _confirm(strategy, _trade([OrderDouble(filled=filled)])) is False


def test_inconsistent_safe_fill_is_denied(strategy: Any) -> None:
    order = OrderDouble(filled=0.0, safe_filled=0.1)
    assert _confirm(strategy, _trade([order])) is False


def test_any_raw_safe_fill_difference_is_denied(strategy: Any) -> None:
    order = OrderDouble(filled=0.0, safe_filled=1e-15)
    assert _confirm(strategy, _trade([order])) is False


def test_requested_amount_must_equal_full_trade_amount(strategy: Any) -> None:
    assert _confirm(strategy, _trade(amount=1.0), amount=0.5) is False


def test_invalid_trade_amount_is_denied(strategy: Any) -> None:
    assert _confirm(strategy, _trade(amount=float("nan")), amount=1.0) is False


def test_entry_orders_are_not_confused_with_exits(strategy: Any) -> None:
    entry = OrderDouble(side="buy", is_open=True, status="open", filled=1.0)
    assert _confirm(strategy, _trade([entry])) is True


def test_stoploss_order_is_not_confused_with_regular_exit(strategy: Any) -> None:
    stoploss = OrderDouble(
        side="stoploss",
        status="closed",
        tag="stop_loss",
        filled=1.0,
    )
    assert _confirm(strategy, _trade([stoploss])) is True


@pytest.mark.parametrize(
    "exit_reason",
    [
        "stop_loss",
        "stoploss_on_exchange",
        "trailing_stop_loss",
        "liquidation",
        "emergency_exit",
        "force_exit",
    ],
)
def test_critical_exit_semantics_are_preserved(
    strategy: Any,
    exit_reason: str,
) -> None:
    ambiguous_prior_exit = OrderDouble(
        is_open=None,
        status=None,
        filled=None,
        cancel_reason=None,
    )
    assert _confirm(
        strategy,
        _trade([ambiguous_prior_exit]),
        exit_reason=exit_reason,
    ) is True


def test_regular_retry_requires_explicit_latched_reason(strategy: Any) -> None:
    timed_out = OrderDouble(status="canceled", filled=0.0)
    trade = _trade([timed_out])
    assert _confirm(strategy, trade, exit_reason="roi") is False
    assert _confirm(strategy, trade, exit_reason=RETRY_REASON) is True


def test_trade_676_timeout_zero_fill_latches_and_confirms(strategy: Any) -> None:
    timed_out = OrderDouble(
        status="closed",
        filled=0.0,
        cancel_reason=TIMEOUT_CANCEL_REASON,
    )
    trade = _trade([timed_out])
    assert _custom_exit(strategy, trade) == RETRY_REASON
    assert _confirm(strategy, trade, exit_reason=RETRY_REASON) is True


def test_trade_676_unknown_fill_never_latches_or_confirms(strategy: Any) -> None:
    timed_out = OrderDouble(filled=None)
    trade = _trade([timed_out])
    assert _custom_exit(strategy, trade) is None
    assert _confirm(strategy, trade, exit_reason=RETRY_REASON) is False


@pytest.mark.parametrize(
    "cancel_reason",
    ["user requested order cancel", CANCELLED_ON_EXCHANGE],
)
def test_trade_676_non_timeout_provenance_never_latches_or_confirms(
    strategy: Any,
    cancel_reason: str,
) -> None:
    prior_exit = OrderDouble(filled=0.0, cancel_reason=cancel_reason)
    trade = _trade([prior_exit])
    assert _custom_exit(strategy, trade) is None
    assert _confirm(strategy, trade, exit_reason=RETRY_REASON) is False


def test_retry_latch_with_open_exit_is_denied(strategy: Any) -> None:
    prior_exit = OrderDouble(is_open=True, status="open", filled=0.0)
    assert _confirm(
        strategy,
        _trade([prior_exit]),
        exit_reason=RETRY_REASON,
    ) is False


def test_retry_latch_with_partial_fill_is_denied(strategy: Any) -> None:
    prior_exit = OrderDouble(filled=0.25)
    assert _confirm(
        strategy,
        _trade([prior_exit]),
        exit_reason=RETRY_REASON,
    ) is False


def test_closed_trade_is_denied_for_regular_exit(strategy: Any) -> None:
    assert _confirm(strategy, _trade(is_open=False)) is False


def test_leverage_cap_remains_two(strategy: Any) -> None:
    assert strategy.leverage(
        pair="BTC/USDT:USDT",
        current_time=datetime(2026, 8, 17, tzinfo=timezone.utc),
        current_rate=100_000.0,
        proposed_leverage=25.0,
        max_leverage=50.0,
        entry_tag=None,
        side="long",
    ) == 2.0


def test_paper_config_never_enables_live_canary_or_real_orders() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert config["dry_run"] is True
    assert config["exchange"]["key"] == ""
    assert config["exchange"]["secret"] == ""
    assert config["api_server"]["enabled"] is False
    assert config["force_entry_enable"] is False
    assert config.get("live_trading_enabled", False) is False
    assert config.get("live_release_allowed", False) is False
    assert config.get("canary_release_allowed", False) is False
    assert config.get("order_submission_enabled", False) is False
    assert config.get("real_order_submission_enabled", False) is False


def test_confirm_trade_exit_hot_path_has_no_io_network_or_subprocess(
    strategy_class: type[Any],
) -> None:
    source = "\n".join(
        inspect.getsource(method)
        for method in (
            strategy_class.confirm_trade_exit,
            strategy_class._evaluate_paper_full_exit_guard,
            strategy_class._evaluate_paper_exit_retry,
        )
    ).lower()
    forbidden_tokens = (
        "requests",
        "httpx",
        "aiohttp",
        "ccxt",
        "subprocess",
        "docker",
        "sleep(",
        "os.environ",
        "getenv(",
        "path(",
        "open(",
        ".read_",
        ".write_",
        "sqlite",
        "create_order",
        "execute_trade_exit",
    )
    assert all(token not in source for token in forbidden_tokens)
