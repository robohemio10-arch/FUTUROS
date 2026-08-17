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

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "freqtrade/user_data/config.paper.json"
STRATEGY_PATH = (
    ROOT / "freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py"
)
RETRY_REASON = "paper_exit_retry_latched"
TIMEOUT_CANCEL_REASON = "cancelled due to timeout"


class OrderDouble:
    """Minimal public Order contract with Freqtrade 2026.6 safe properties."""

    def __init__(
        self,
        *,
        side: str | None,
        is_open: bool | None,
        status: str | None,
        tag: str | None,
        amount: float | None,
        filled: float | None,
        cancel_reason: str | None,
    ) -> None:
        self.ft_order_side = side
        self.ft_is_open = is_open
        self.status = status
        self.ft_order_tag = tag
        self.amount = amount
        self.ft_amount = amount
        self.filled = filled
        self.ft_cancel_reason = cancel_reason

    @property
    def safe_amount(self) -> float | None:
        return self.amount or self.ft_amount

    @property
    def safe_filled(self) -> float:
        return self.filled if self.filled is not None else 0.0


@pytest.fixture(scope="module")
def config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def strategy_class() -> type[Any]:
    class IStrategy:
        pass

    freqtrade_module = types.ModuleType("freqtrade")
    constants_module = types.ModuleType("freqtrade.constants")
    strategy_module = types.ModuleType("freqtrade.strategy")
    constants_module.CANCEL_REASON = {"TIMEOUT": TIMEOUT_CANCEL_REASON}
    strategy_module.IStrategy = IStrategy
    freqtrade_module.constants = constants_module
    freqtrade_module.strategy = strategy_module

    module_name = "smartcrypto_test_freqtrade_exit_lifecycle_strategy"
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


def _order(
    *,
    side: str | None = "sell",
    is_open: bool | None = False,
    status: str | None = "cancelled",
    tag: str | None = "roi",
    amount: float | None = 1.0,
    filled: float | None = 0.0,
    cancel_reason: str | None = TIMEOUT_CANCEL_REASON,
) -> OrderDouble:
    return OrderDouble(
        side=side,
        is_open=is_open,
        status=status,
        tag=tag,
        amount=amount,
        filled=filled,
        cancel_reason=cancel_reason,
    )


def _trade(
    orders: list[Any] | tuple[Any, ...] | None = None,
    *,
    is_short: bool = False,
    is_open: bool = True,
    amount: float = 1.0,
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


def _custom_exit(strategy: Any, trade: Any) -> str | None:
    return strategy.custom_exit(
        pair="BTC/USDT:USDT",
        trade=trade,
        current_time=datetime(2026, 8, 16, tzinfo=timezone.utc),
        current_rate=100_000.0,
        current_profit=0.01,
    )


def _approved_entry_frame(strategy: Any, side: str) -> pd.DataFrame:
    payload = {
        "signals": [
            {
                "pair": "BTC/USDT:USDT",
                "symbol": "BTCUSDT",
                "side": side,
                "risk_approved": True,
                "confidence": 0.75,
            }
        ]
    }
    strategy._read_first_active_signal_file = lambda: (
        payload,
        Path("synthetic-signals.json"),
        "active_signals_found",
    )
    frame = strategy.populate_indicators(
        pd.DataFrame([{"close": 100_000.0}]),
        {"pair": "BTC/USDT:USDT"},
    )
    return strategy.populate_entry_trend(frame, {"pair": "BTC/USDT:USDT"})


def test_config_remains_dry_run(config: dict[str, Any]) -> None:
    assert config["dry_run"] is True


def test_config_exit_order_remains_limit(config: dict[str, Any]) -> None:
    assert config["order_types"]["exit"] == "limit"


def test_config_emergency_exit_remains_market(config: dict[str, Any]) -> None:
    assert config["order_types"]["emergency_exit"] == "market"


def test_config_exit_timeout_remains_ten_minutes(config: dict[str, Any]) -> None:
    assert config["unfilledtimeout"]["exit"] == 10
    assert config["unfilledtimeout"]["entry"] == 10
    assert config["unfilledtimeout"]["unit"] == "minutes"


def test_config_exit_timeout_count_is_two(config: dict[str, Any]) -> None:
    assert config["unfilledtimeout"]["exit_timeout_count"] == 2


def test_config_stoploss_is_unchanged(config: dict[str, Any]) -> None:
    assert config["stoploss"] == -0.015
    assert config["order_types"]["stoploss"] == "market"


def test_config_minimal_roi_is_unchanged(config: dict[str, Any]) -> None:
    assert config["minimal_roi"] == {"0": 0.02}


def test_config_max_open_trades_is_unchanged(config: dict[str, Any]) -> None:
    assert config["max_open_trades"] == 2


def test_config_force_entry_remains_disabled(config: dict[str, Any]) -> None:
    assert config["force_entry_enable"] is False


def test_trade_without_canceled_exit_returns_none(strategy: Any) -> None:
    assert _custom_exit(strategy, _trade()) is None


def test_canceled_entry_order_returns_none(strategy: Any) -> None:
    assert _custom_exit(strategy, _trade([_order(side="buy")])) is None


def test_open_exit_order_returns_none(strategy: Any) -> None:
    order = _order(is_open=True, status="open")
    assert _custom_exit(strategy, _trade([order])) is None


def test_canceled_roi_exit_latches(strategy: Any) -> None:
    assert _custom_exit(strategy, _trade([_order(tag="roi")])) == RETRY_REASON


def test_raw_zero_filled_exit_is_eligible(strategy: Any) -> None:
    order = _order(filled=0.0)
    assert order.safe_filled == 0.0
    assert _custom_exit(strategy, _trade([order])) == RETRY_REASON


def test_raw_none_filled_is_not_proven_by_safe_filled(strategy: Any) -> None:
    order = _order(filled=None)
    assert order.safe_filled == 0.0
    assert _custom_exit(strategy, _trade([order])) is None


@pytest.mark.parametrize("filled", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_raw_filled_returns_none(strategy: Any, filled: float) -> None:
    assert _custom_exit(strategy, _trade([_order(filled=filled)])) is None


def test_canceled_short_roi_exit_latches(strategy: Any) -> None:
    order = _order(side="buy", tag="roi")
    assert _custom_exit(strategy, _trade([order], is_short=True)) == RETRY_REASON


def test_canceled_exit_signal_latches(strategy: Any) -> None:
    order = _order(status="canceled", tag="exit_signal")
    assert _custom_exit(strategy, _trade([order])) == RETRY_REASON


def test_expired_phase15_controlled_exit_latches(strategy: Any) -> None:
    order = _order(status="expired", tag="phase15_controlled_paper_exit")
    assert _custom_exit(strategy, _trade([order])) == RETRY_REASON


def test_canceled_prior_retry_latches(strategy: Any) -> None:
    order = _order(tag=RETRY_REASON)
    assert _custom_exit(strategy, _trade([order])) == RETRY_REASON


@pytest.mark.parametrize(
    "cancel_reason",
    [
        "user requested order cancel",
        "cancelled to be replaced by new limit order",
        "cancelled (all unfilled and partially filled open orders cancelled)",
        "forcesold",
        "cancelled on exchange",
        None,
        "unknown cancel provenance",
    ],
)
def test_non_timeout_cancel_provenance_returns_none(
    strategy: Any,
    cancel_reason: str | None,
) -> None:
    order = _order(cancel_reason=cancel_reason)
    assert _custom_exit(strategy, _trade([order])) is None


def test_closed_timeout_exit_with_raw_zero_fill_latches(strategy: Any) -> None:
    order = _order(status="closed", filled=0.0)
    assert _custom_exit(strategy, _trade([order])) == RETRY_REASON


def test_closed_exit_without_timeout_provenance_returns_none(strategy: Any) -> None:
    order = _order(status="closed", cancel_reason=None)
    assert _custom_exit(strategy, _trade([order])) is None


def test_closed_timeout_exit_with_unknown_raw_fill_returns_none(strategy: Any) -> None:
    order = _order(status="closed", filled=None)
    assert order.safe_filled == 0.0
    assert _custom_exit(strategy, _trade([order])) is None


def test_closed_timeout_exit_with_positive_raw_fill_returns_none(strategy: Any) -> None:
    order = _order(status="closed", filled=0.25)
    assert _custom_exit(strategy, _trade([order])) is None


def test_canceled_stoploss_returns_none(strategy: Any) -> None:
    order = _order(side="stoploss", tag="stop_loss")
    assert _custom_exit(strategy, _trade([order])) is None


def test_canceled_liquidation_returns_none(strategy: Any) -> None:
    order = _order(tag="liquidation")
    assert _custom_exit(strategy, _trade([order])) is None


def test_malformed_order_metadata_returns_none(strategy: Any) -> None:
    order = SimpleNamespace(ft_order_side="sell", ft_is_open=False)
    assert _custom_exit(strategy, _trade([order])) is None


@pytest.mark.parametrize("status", [None, "unknown", "rejected"])
def test_unknown_exit_order_state_returns_none(
    strategy: Any,
    status: str | None,
) -> None:
    assert _custom_exit(strategy, _trade([_order(status=status)])) is None


def test_multiple_orders_select_semantic_exit_without_entry_confusion(
    strategy: Any,
) -> None:
    orders = [
        _order(side="buy", status="cancelled", tag=None, amount=None),
        _order(side="sell", status="closed", tag="exit_signal", filled=1.0),
        _order(side="sell", status="expired", tag="roi"),
    ]
    assert _custom_exit(strategy, _trade(orders)) == RETRY_REASON


def test_any_current_full_exit_prevents_duplicate_intent(strategy: Any) -> None:
    orders = [
        _order(status="cancelled", tag="roi"),
        _order(is_open=True, status="open", tag="exit_signal"),
    ]
    assert _custom_exit(strategy, _trade(orders)) is None


def test_partially_filled_canceled_exit_returns_none(strategy: Any) -> None:
    assert _custom_exit(strategy, _trade([_order(filled=0.25)])) is None


def test_non_full_canceled_exit_returns_none(strategy: Any) -> None:
    assert _custom_exit(strategy, _trade([_order(amount=0.5)])) is None


def test_closed_trade_returns_none(strategy: Any) -> None:
    assert _custom_exit(strategy, _trade([_order()], is_open=False)) is None


def test_long_risk_approved_entry_still_works(strategy: Any) -> None:
    frame = _approved_entry_frame(strategy, "long")
    assert frame.iloc[-1]["enter_long"] == 1
    assert frame.iloc[-1]["enter_short"] == 0


def test_short_risk_approved_entry_still_works(strategy: Any) -> None:
    frame = _approved_entry_frame(strategy, "short")
    assert frame.iloc[-1]["enter_short"] == 1
    assert frame.iloc[-1]["enter_long"] == 0


@pytest.mark.parametrize("approval", [None, False])
def test_missing_or_false_risk_approval_remains_fail_closed(
    strategy: Any,
    approval: bool | None,
) -> None:
    signal: dict[str, Any] = {
        "pair": "BTC/USDT:USDT",
        "side": "long",
    }
    if approval is not None:
        signal["risk_approved"] = approval
    payload = {"signals": [signal]}
    strategy._read_first_active_signal_file = lambda: (
        payload,
        Path("synthetic-signals.json"),
        "active_signals_found",
    )
    result = strategy._find_signal_for_pair("BTC/USDT:USDT")
    assert result["accepted"] is False


def test_phase15_controlled_exit_still_works(strategy: Any) -> None:
    frame = pd.DataFrame([{"smartcrypto_exit_requested": True}])
    result = strategy.populate_exit_trend(frame, {"pair": "BTC/USDT:USDT"})
    assert result.iloc[-1]["exit_long"] == 1
    assert result.iloc[-1]["exit_short"] == 1
    assert result.iloc[-1]["exit_tag"] == "phase15_controlled_paper_exit"


def test_opposite_signal_alone_does_not_close_trade(strategy: Any) -> None:
    frame = pd.DataFrame(
        [
            {
                "smartcrypto_signal_side": "short",
                "smartcrypto_exit_requested": False,
            }
        ]
    )
    result = strategy.populate_exit_trend(frame, {"pair": "BTC/USDT:USDT"})
    assert result.iloc[-1]["exit_long"] == 0
    assert result.iloc[-1]["exit_short"] == 0
    assert result.iloc[-1]["exit_tag"] is None


def test_custom_exit_has_no_private_exchange_access(strategy_class: type[Any]) -> None:
    source = inspect.getsource(strategy_class.custom_exit)
    assert "exchange" not in source.lower()
    assert "orderbook" not in source.lower()


def test_custom_exit_has_no_direct_database_access(strategy_class: type[Any]) -> None:
    source = inspect.getsource(strategy_class.custom_exit)
    assert "sqlite" not in source.lower()
    assert ".session" not in source
    assert ".commit" not in source


def test_custom_exit_does_not_submit_orders(strategy_class: type[Any]) -> None:
    source = inspect.getsource(strategy_class.custom_exit)
    assert "create_order" not in source
    assert "execute_trade_exit" not in source
    assert "send_order" not in source


def test_custom_exit_does_not_change_risk(strategy_class: type[Any]) -> None:
    source = inspect.getsource(strategy_class.custom_exit)
    assert "RiskManager" not in source
    assert "risk_approved" not in source
    assert "leverage" not in source


def test_custom_exit_does_not_change_models(strategy_class: type[Any]) -> None:
    source = inspect.getsource(strategy_class.custom_exit)
    assert "qlib" not in source.lower()
    assert "model" not in source.lower()


def test_config_never_enables_live_or_canary(config: dict[str, Any]) -> None:
    assert config["dry_run"] is True
    assert config.get("live_trading_enabled", False) is False
    assert config.get("canary_release_allowed", False) is False


def test_custom_exit_signature_matches_freqtrade_2026_6(
    strategy_class: type[Any],
) -> None:
    parameters = list(inspect.signature(strategy_class.custom_exit).parameters)
    assert parameters == [
        "self",
        "pair",
        "trade",
        "current_time",
        "current_rate",
        "current_profit",
        "kwargs",
    ]
