from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from smartcrypto.ops.dashboard_real_paper_sources import (
    SCHEMA_VERSION,
    build_real_paper_sources_snapshot,
)


def _create_freqtrade_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.execute(
        """
        create table KeyValueStore (
            id integer,
            key varchar(25),
            value_type varchar(20),
            string_value varchar(255),
            datetime_value datetime,
            float_value float,
            int_value integer
        )
        """
    )
    cur.execute(
        """
        create table trades (
            id integer,
            exchange varchar(25),
            pair varchar(25),
            base_currency varchar(25),
            stake_currency varchar(25),
            is_open boolean,
            fee_open float,
            fee_open_cost float,
            fee_open_currency varchar(25),
            fee_close float,
            fee_close_cost float,
            fee_close_currency varchar(25),
            open_rate float,
            open_rate_requested float,
            open_trade_value float,
            close_rate float,
            close_rate_requested float,
            realized_profit float,
            close_profit float,
            close_profit_abs float,
            stake_amount float,
            max_stake_amount float,
            amount float,
            amount_requested float,
            open_date datetime,
            close_date datetime,
            stop_loss float,
            stop_loss_pct float,
            initial_stop_loss float,
            initial_stop_loss_pct float,
            is_stop_loss_trailing boolean,
            max_rate float,
            min_rate float,
            exit_reason varchar(255),
            exit_order_status varchar(100),
            strategy varchar(100),
            enter_tag varchar(255),
            timeframe integer,
            trading_mode varchar(7),
            amount_precision float,
            price_precision float,
            precision_mode integer,
            precision_mode_price integer,
            contract_size float,
            leverage float,
            is_short boolean,
            liquidation_price float,
            interest_rate float,
            funding_fees float,
            funding_fee_running float,
            record_version integer
        )
        """
    )
    cur.execute(
        """
        create table orders (
            id integer,
            ft_trade_id integer,
            ft_order_side varchar(25),
            ft_pair varchar(25),
            ft_is_open boolean,
            ft_amount float,
            ft_price float,
            ft_cancel_reason varchar(255),
            order_id varchar(255),
            status varchar(255),
            symbol varchar(25),
            order_type varchar(50),
            side varchar(25),
            price float,
            average float,
            amount float,
            filled float,
            remaining float,
            cost float,
            stop_price float,
            order_date datetime,
            order_filled_date datetime,
            order_update_date datetime,
            funding_fee float,
            ft_fee_base float,
            ft_order_tag varchar(255)
        )
        """
    )
    cur.execute(
        """
        insert into KeyValueStore values
        (1, 'bot_start_time', 'datetime', null, '2026-06-01 20:07:13.370547', null, null)
        """
    )
    cur.execute(
        """
        insert into trades values
        (1, 'binance', 'BTC/USDT:USDT', 'BTC', 'USDT', 0, 0.0002, 0.01, 'USDT',
         0.0002, 0.02, 'USDT', 100.0, 100.0, 50.0, 110.0, 110.0, 5.0,
         0.10, 5.0, 50.0, 50.0, 0.5, 0.5, '2026-06-01 00:00:00',
         '2026-06-01 01:00:00', 95.0, -0.05, 95.0, -0.05, 0, 112.0, 99.0,
         'roi', 'closed', 'SmartCryptoSignalStrategy', 'smartcrypto_long', 5,
         'FUTURES', 0.001, 0.01, 4, 4, 1.0, 2.0, 0, 50.0, 0.0, 0.0, 0.0, 2)
        """
    )
    cur.execute(
        """
        insert into trades values
        (2, 'binance', 'ETH/USDT:USDT', 'ETH', 'USDT', 0, 0.0002, 0.01, 'USDT',
         0.0002, 0.02, 'USDT', 100.0, 100.0, 50.0, 90.0, 90.0, -4.0,
         -0.08, -4.0, 50.0, 50.0, 0.5, 0.5, '2026-06-01 02:00:00',
         '2026-06-01 03:00:00', 95.0, -0.05, 95.0, -0.05, 0, 102.0, 89.0,
         'stop_loss', 'closed', 'SmartCryptoSignalStrategy', 'smartcrypto_short', 5,
         'FUTURES', 0.001, 0.01, 4, 4, 1.0, 2.0, 1, 150.0, 0.0, 0.0, 0.0, 2)
        """
    )
    cur.execute(
        """
        insert into trades values
        (3, 'binance', 'ETH/USDT:USDT', 'ETH', 'USDT', 1, 0.0002, 0.01, 'USDT',
         0.0002, null, 'USDT', 100.0, 100.0, 60.0, null, null, 0.0,
         0.0, null, 60.0, 60.0, 0.6, 0.6, '2026-06-01 04:00:00',
         null, 95.0, -0.05, 95.0, -0.05, 0, 104.0, 98.0,
         null, null, 'SmartCryptoSignalStrategy', 'smartcrypto_long', 5,
         'FUTURES', 0.001, 0.01, 4, 4, 1.0, 2.0, 0, 50.0, 0.0, 0.0, 0.0, 2)
        """
    )
    cur.execute(
        """
        insert into orders values
        (1, 1, 'buy', 'BTC/USDT:USDT', 0, 0.5, 100.0, null, 'dry_run_buy_1',
         'closed', 'BTC/USDT:USDT', 'limit', 'buy', 100.0, 100.0, 0.5, 0.5,
         0.0, 50.0, null, '2026-06-01 00:00:00', '2026-06-01 00:00:05',
         '2026-06-01 00:00:05', null, null, 'smartcrypto_long')
        """
    )
    cur.execute(
        """
        insert into orders values
        (2, 1, 'sell', 'BTC/USDT:USDT', 0, 0.5, 110.0, null, 'dry_run_sell_1',
         'closed', 'BTC/USDT:USDT', 'limit', 'sell', 110.0, 110.0, 0.5, 0.5,
         0.0, 55.0, null, '2026-06-01 01:00:00', '2026-06-01 01:00:05',
         '2026-06-01 01:00:05', null, null, 'smartcrypto_long')
        """
    )
    con.commit()
    con.close()


def _create_notifications_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.execute(
        """
        create table trade_event_notifications (
            notification_key text,
            trade_id integer,
            event_type text,
            pair text,
            side text,
            event_time_utc text,
            status text,
            dry_run integer,
            created_at_utc text,
            payload_json text
        )
        """
    )
    cur.execute(
        """
        create table trade_event_notification_channels (
            notification_key text,
            channel text,
            trade_id integer,
            event_type text,
            pair text,
            side text,
            event_time_utc text,
            status text,
            dry_run integer,
            created_at_utc text,
            payload_json text
        )
        """
    )
    cur.execute(
        """
        insert into trade_event_notifications values
        ('1:OPEN_LONG', 1, 'OPEN_LONG', 'BTC/USDT:USDT', 'LONG',
         '2026-06-01T00:00:00Z', 'sent', 0, '2026-06-01T00:00:10Z', '{}')
        """
    )
    cur.execute(
        """
        insert into trade_event_notification_channels values
        ('1:OPEN_LONG', 'telegram', 1, 'OPEN_LONG', 'BTC/USDT:USDT', 'LONG',
         '2026-06-01T00:00:00Z', 'sent', 0, '2026-06-01T00:00:10Z', '{}')
        """
    )
    con.commit()
    con.close()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_build_real_paper_sources_snapshot_from_fixture(tmp_path: Path) -> None:
    _create_freqtrade_db(tmp_path / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite")
    _create_notifications_db(tmp_path / "data/runtime/trade_event_notifications.sqlite")

    _write_json(
        tmp_path / "data/reports/qlib_fresh_prediction_runner_report.json",
        {
            "status": "ok",
            "rows": 2,
            "model_version": "qlib_lgbm_v1",
            "timeframe": "5m",
            "input_data_status": "input_data_fresh",
            "input_data_age_minutes": 3.2,
            "runtime_mode": "paper",
            "shadow_only": True,
        },
    )
    _write_json(
        tmp_path / "data/reports/phase13_signal_producer_report.json",
        {
            "status": "ok",
            "signals_after": 2,
            "prediction_rows": 2,
            "pairs": ["BTC/USDT:USDT", "ETH/USDT:USDT"],
            "sides": ["long", "short"],
            "valid_until_min": "2026-06-01T01:00:00Z",
            "valid_until_max": "2026-06-01T01:00:00Z",
        },
    )
    _write_json(
        tmp_path / "data/runtime/active_freqtrade_signals.json",
        {
            "generated_at": "2026-06-01T00:00:00Z",
            "model_version": "qlib_lgbm_v1",
            "runtime_mode": "paper",
            "signals": [{"pair": "BTC/USDT:USDT"}, {"pair": "ETH/USDT:USDT"}],
        },
    )
    _write_json(
        tmp_path / "data/reports/phase14_open_positions_report.json",
        {
            "status": "ok",
            "rows": 3,
            "open_rows": 1,
            "closed_rows": 2,
            "max_open_trades": 2,
            "saturated": False,
            "expected_pairs": ["BTC/USDT:USDT", "ETH/USDT:USDT"],
            "open_pairs": ["ETH/USDT:USDT"],
            "recent": [],
            "db_snapshot_used": True,
        },
    )
    _write_json(
        tmp_path / "data/reports/freqtrade_paper_db_snapshot_export.json",
        {
            "status": "ok",
            "output": "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite",
            "output_size_bytes": 123,
            "paper_only": True,
            "live_trading_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
        },
    )
    _write_json(
        tmp_path / "data/reports/phase14_output_summary.json",
        {"phase14_status": {"status": "ok"}},
    )
    _write_json(
        tmp_path / "data/reports/trade_event_notifications_report.json",
        {"status": "ok", "events_detected": 1},
    )

    result = build_real_paper_sources_snapshot(project_root=tmp_path, write=True)

    assert result.exit_code == 0
    assert result.snapshot["schema_version"] == SCHEMA_VERSION
    assert result.snapshot["status"] == "ok"
    assert result.snapshot["dashboard_readonly"] is True
    assert result.snapshot["paper_only"] is True
    assert result.snapshot["shadow_only"] is True
    assert result.snapshot["live_trading_enabled"] is False
    assert result.snapshot["order_submission_enabled"] is False
    assert result.snapshot["real_order_submission_enabled"] is False
    assert result.snapshot["exchange_private_access"] is False
    assert result.snapshot["sends_orders"] is False
    assert result.snapshot["sends_notifications"] is False

    assert result.snapshot["freqtrade"]["trades_total"] == 3
    assert result.snapshot["freqtrade"]["orders_total"] == 2
    assert result.snapshot["freqtrade"]["open_trades"] == 1
    assert result.snapshot["freqtrade"]["closed_trades"] == 2
    assert result.snapshot["freqtrade"]["realized_pnl_abs"] == 1.0
    assert result.snapshot["portfolio_risk"]["win_rate"] == 50.0
    assert result.snapshot["portfolio_risk"]["open_exposure_usdt"] == 60.0
    assert result.snapshot["alerts_messaging"]["events_total"] == 1
    assert result.snapshot["alerts_messaging"]["channels_total"] == 1
    assert result.snapshot["qlib"]["model_version"] == "qlib_lgbm_v1"
    assert result.snapshot["qlib"]["signals_count"] == 2

    output = tmp_path / "data/reports/dashboard_real_paper_sources_snapshot.json"
    assert output.exists()
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["freqtrade"]["trades_total"] == 3


def test_missing_freqtrade_snapshot_blocks_without_runtime_side_effects(tmp_path: Path) -> None:
    result = build_real_paper_sources_snapshot(project_root=tmp_path, write=False)

    assert result.exit_code == 2
    assert result.snapshot["status"] == "blocked"
    assert result.snapshot["reason"] == "missing_required_real_paper_sources"
    assert result.snapshot["dashboard_readonly"] is True
    assert result.snapshot["live_trading_enabled"] is False
    assert result.snapshot["order_submission_enabled"] is False
    assert result.snapshot["exchange_private_access"] is False
