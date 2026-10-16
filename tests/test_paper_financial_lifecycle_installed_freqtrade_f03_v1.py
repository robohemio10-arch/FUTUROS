"""Characterize the installed engine without an exchange, database or core patch."""

from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


try:
    from freqtrade.freqtradebot import FreqtradeBot
    from freqtrade.persistence import Order, Trade
except ImportError:
    FreqtradeBot = None


class InstalledLifecycleTest(unittest.TestCase):
    def test_closed_fill_followed_by_cancel_handler_loses_summary_not_execution(self) -> None:
        if FreqtradeBot is None:
            if os.environ.get("F03_REQUIRE_INSTALLED_FREQTRADE") == "1":
                self.fail("actual pinned Freqtrade package required")
            self.skipTest("actual pinned Freqtrade package unavailable on this host")
        from smartcrypto.data.trader_master_fingerprint_v2.financial_lifecycle import (
            reconcile_financial_lifecycle,
        )
        from smartcrypto.data.trader_master_fingerprint_v2.source_profile import load_source_profile

        opened = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
        closed = datetime(2026, 6, 1, 10, 5, tzinfo=UTC)
        trade = Trade(
            id=999, pair="BTC/USDT:USDT", exchange="binance", is_open=True,
            is_short=False, amount=1, open_rate=100, stake_amount=100,
            fee_open=0, fee_close=0, leverage=1, contract_size=1,
            open_date=opened, amount_precision=8, price_precision=8,
            precision_mode=2, precision_mode_price=2,
        )
        entry = Order(
            id=1, ft_trade_id=999, order_id="entry", ft_pair=trade.pair,
            ft_order_side="buy", side="buy", ft_is_open=False, status="closed",
            amount=1, filled=1, remaining=0, average=100, price=100,
            order_date=opened, order_filled_date=opened, order_type="limit",
        )
        exit_order = Order(
            id=2, ft_trade_id=999, order_id="exit", ft_pair=trade.pair,
            ft_order_side="sell", side="sell", ft_is_open=True, status="open",
            amount=1, filled=0, remaining=1, average=None, price=110,
            order_date=closed, order_type="limit",
        )
        trade.orders = [entry, exit_order]
        bot = object.__new__(FreqtradeBot)
        bot.exchange = SimpleNamespace(check_order_canceled_empty=lambda _: False)
        bot.strategy = SimpleNamespace(order_types={"exit": "limit"})
        bot.handle_order_fee = Mock()
        observed: list[float] = []

        def after_fill(current: Trade, order: Order, send_msg: bool) -> Trade:
            self.assertFalse(current.is_open)
            self.assertEqual(current.close_rate, 110)
            observed.append(current.close_rate)
            return current

        bot._update_trade_after_fill = after_fill
        bot.order_close_notify = Mock()
        bot._notify_exit_cancel = Mock()
        payload = {
            "id": "exit", "symbol": trade.pair, "status": "closed", "type": "limit",
            "side": "sell", "amount": 1, "filled": 1, "remaining": 0,
            "average": 110, "price": 110,
            "timestamp": int(closed.timestamp() * 1000),
            "lastTradeTimestamp": int(closed.timestamp() * 1000),
        }
        with patch.object(Trade, "commit"):
            bot.handle_cancel_exit(trade, payload, exit_order, "fixture timeout")
        self.assertEqual(observed, [110])
        self.assertFalse(trade.is_open)
        self.assertIsNone(trade.close_rate)
        self.assertEqual(exit_order.average, 110)
        self.assertEqual(exit_order.filled, 1)
        self.assertEqual(exit_order.status, "closed")

        # The read-only boundary must not infer or silently write a replacement price.
        summary = {
            key: getattr(trade, key) for key in (
                "id", "pair", "exchange", "is_short", "is_open", "amount", "open_rate",
                "close_rate", "open_date", "close_date", "contract_size", "leverage",
                "close_profit_abs", "realized_profit", "funding_fees",
            )
        }
        summary.update(fee_open_cost=0, fee_close_cost=0,
                       fee_open_currency="USDT", fee_close_currency="USDT")
        orders = [{key: getattr(order, key) for key in (
            "id", "ft_trade_id", "order_id", "ft_pair", "ft_order_side", "side", "ft_is_open",
            "status", "amount", "filled", "remaining", "average", "order_date", "order_filled_date",
        )} for order in trade.orders]
        root = Path(__file__).resolve().parents[1]
        profile = load_source_profile(root / "config/freqtrade_paper_closed_trades_source_profile_v2.json")
        result = reconcile_financial_lifecycle([summary], orders, profile=profile)
        self.assertEqual(result["accepted_count"], 0)
        self.assertIn("close_rate_missing_or_invalid", result["trade_results"][0]["reasons"])
        self.assertIsNone(trade.close_rate)


if __name__ == "__main__":
    unittest.main()
