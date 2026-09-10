from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.learning.paper_autolearning.feedback_store import build_feedback_events
from smartcrypto.learning.paper_autolearning.live_feedback_loop import (
    run_paper_autolearning_live_feedback_loop_v1,
)
from smartcrypto.learning.paper_autolearning.outcome_schema import OUTCOME_EVENT_COLUMNS
from smartcrypto.learning.paper_autolearning.runtime_source import (
    load_authoritative_closed_paper_trades,
)


def _create_realistic_paper_db(
    path: Path,
    *,
    trades: list[dict[str, object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY,
                pair TEXT NOT NULL,
                is_open INTEGER NOT NULL,
                open_date TEXT,
                close_date TEXT,
                open_rate REAL,
                close_rate REAL,
                amount REAL,
                stake_amount REAL,
                close_profit REAL,
                leverage REAL,
                liquidation_price REAL,
                exit_reason TEXT,
                strategy TEXT,
                is_short INTEGER NOT NULL,
                fee_open REAL,
                fee_close REAL,
                funding_fees REAL
            )
            """
        )
        for trade in trades:
            connection.execute(
                """
                INSERT INTO trades (
                    id, pair, is_open, open_date, close_date, open_rate,
                    close_rate, amount, stake_amount, close_profit, leverage,
                    liquidation_price, exit_reason, strategy, is_short,
                    fee_open, fee_close, funding_fees
                ) VALUES (
                    :id, :pair, 0, :open_date, :close_date, :open_rate,
                    :close_rate, :amount, :stake_amount, :close_profit,
                    :leverage, :liquidation_price, :exit_reason, :strategy,
                    :is_short, :fee_open, :fee_close, :funding_fees
                )
                """,
                trade,
            )


def _eth_long_trade(
    *,
    trade_id: int = 1,
    close_date: str = "2026-06-01 22:01:49.990000",
) -> dict[str, object]:
    return {
        "id": trade_id,
        "pair": "ETH/USDT:USDT",
        "open_date": "2026-06-01 20:15:07.045313",
        "close_date": close_date,
        "open_rate": 2002.32,
        "close_rate": 1987.16,
        "amount": 0.049,
        "stake_amount": 49.05684,
        "close_profit": -0.016531557328344805,
        "leverage": 2.0,
        "liquidation_price": 3000.0,
        "exit_reason": "stop_loss",
        "strategy": "PaperStrategy",
        "is_short": 0,
        "fee_open": 0.0002,
        "fee_close": 0.0005,
        "funding_fees": 0.0,
    }


def _btc_short_trade(
    *,
    trade_id: int = 2,
    close_date: str = "2026-06-01 22:08:06.267000",
) -> dict[str, object]:
    return {
        "id": trade_id,
        "pair": "BTC/USDT:USDT",
        "open_date": "2026-06-01 20:15:12.498476",
        "close_date": close_date,
        "open_rate": 71557.0,
        "close_rate": 70807.4,
        "amount": 0.001,
        "stake_amount": 35.7785,
        "close_profit": 0.020159351964863115,
        "leverage": 2.0,
        "liquidation_price": 100000.0,
        "exit_reason": "roi",
        "strategy": "PaperStrategy",
        "is_short": 1,
        "fee_open": 0.0002,
        "fee_close": 0.0002,
        "funding_fees": 0.0,
    }


def test_runtime_source_reconstructs_real_paper_economics_without_profit_abs(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    _create_realistic_paper_db(snapshot, trades=[_eth_long_trade(), _btc_short_trade()])

    selection = load_authoritative_closed_paper_trades(project_root=tmp_path)

    assert selection.status == "ok"
    assert len(selection.rows) == 2

    eth = selection.rows[0]
    assert eth["trade_id"] == 1
    assert eth["order_id"] == "freqtrade-paper-1"
    assert eth["quantity"] == pytest.approx(0.049)
    assert eth["notional"] == pytest.approx(98.11368)
    assert eth["gross_pnl"] == pytest.approx(-0.74284)
    assert eth["trading_fee"] == pytest.approx(0.06830816)
    assert eth["funding_fee"] == pytest.approx(0.0)
    assert eth["net_pnl"] == pytest.approx(-0.81114816)

    btc = selection.rows[1]
    assert btc["side"] == "short"
    assert btc["gross_pnl"] == pytest.approx(0.7496)
    assert btc["trading_fee"] == pytest.approx(0.02847288)
    assert btc["net_pnl"] == pytest.approx(0.72112712)


def test_runtime_source_normalizes_funding_cost_sign(tmp_path: Path) -> None:
    snapshot = tmp_path / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    trade = {
        **_eth_long_trade(),
        "id": 3,
        "pair": "BTC/USDT:USDT",
        "open_rate": 71388.3,
        "close_rate": 70836.0,
        "amount": 0.001,
        "stake_amount": 35.69415,
        "close_profit": -0.017061983753598846,
        "fee_open": 0.0002,
        "fee_close": 0.0005,
        "funding_fees": -0.00713915,
    }
    _create_realistic_paper_db(snapshot, trades=[trade])

    selection = load_authoritative_closed_paper_trades(project_root=tmp_path)
    row = selection.rows[0]

    assert row["gross_pnl"] == pytest.approx(-0.5523)
    assert row["trading_fee"] == pytest.approx(0.04969566)
    assert row["funding_fee"] == pytest.approx(0.00713915)
    assert row["net_pnl"] == pytest.approx(-0.60913481)


def test_selects_freshest_closed_trade_source(tmp_path: Path) -> None:
    runtime = tmp_path / "freqtrade/user_data/tradesv3.paper.sqlite"
    snapshot = tmp_path / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    _create_realistic_paper_db(
        runtime,
        trades=[_eth_long_trade(close_date="2026-09-01 10:30:00")],
    )
    _create_realistic_paper_db(
        snapshot,
        trades=[_eth_long_trade(trade_id=2, close_date="2026-09-05 10:30:00")],
    )

    selection = load_authoritative_closed_paper_trades(project_root=tmp_path)

    assert selection.status == "ok"
    assert selection.selected_path == snapshot.resolve()
    assert len(selection.rows) == 1
    assert selection.rows[0]["trade_id"] == 2


def test_explicit_source_wins_same_time_snapshot_with_newer_mtime(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit.sqlite"
    snapshot = tmp_path / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    close_date = "2026-09-05 10:30:00"
    _create_realistic_paper_db(
        explicit,
        trades=[_eth_long_trade(trade_id=1, close_date=close_date)],
    )
    _create_realistic_paper_db(
        snapshot,
        trades=[_eth_long_trade(trade_id=2, close_date=close_date)],
    )
    os.utime(explicit, (1, 1))

    selection = load_authoritative_closed_paper_trades(
        project_root=tmp_path,
        explicit_path=explicit,
    )

    assert selection.status == "ok"
    assert selection.reason == "explicit_closed_trade_source_selected_read_only"
    assert selection.selected_path == explicit.resolve()
    assert selection.rows[0]["trade_id"] == 1


def test_explicit_source_wins_snapshot_with_newer_close_time(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit.sqlite"
    snapshot = tmp_path / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    _create_realistic_paper_db(
        explicit,
        trades=[_eth_long_trade(trade_id=1, close_date="2026-09-01 10:30:00")],
    )
    _create_realistic_paper_db(
        snapshot,
        trades=[_eth_long_trade(trade_id=2, close_date="2026-09-05 10:30:00")],
    )

    selection = load_authoritative_closed_paper_trades(
        project_root=tmp_path,
        explicit_path=explicit,
    )

    assert selection.status == "ok"
    assert selection.reason == "explicit_closed_trade_source_selected_read_only"
    assert selection.selected_path == explicit.resolve()
    assert selection.rows[0]["trade_id"] == 1


@pytest.mark.parametrize("explicit_state", ["missing", "invalid"])
def test_invalid_explicit_source_falls_back_to_freshest_valid_candidate(
    tmp_path: Path,
    explicit_state: str,
) -> None:
    explicit = tmp_path / "explicit.sqlite"
    if explicit_state == "invalid":
        with sqlite3.connect(explicit) as connection:
            connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    snapshot = tmp_path / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    _create_realistic_paper_db(snapshot, trades=[_btc_short_trade()])

    selection = load_authoritative_closed_paper_trades(
        project_root=tmp_path,
        explicit_path=explicit,
    )

    assert selection.status == "ok"
    assert selection.reason == "freshest_fallback_closed_trade_source_selected_read_only"
    assert selection.selected_path == snapshot.resolve()
    assert selection.rows[0]["trade_id"] == 2
    expected_status = "missing" if explicit_state == "missing" else "invalid_schema"
    assert selection.candidates[0].status == expected_status


def test_source_resolver_never_writes_sqlite(tmp_path: Path) -> None:
    snapshot = tmp_path / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    _create_realistic_paper_db(snapshot, trades=[_eth_long_trade()])
    before_size = snapshot.stat().st_size
    before_mtime_ns = snapshot.stat().st_mtime_ns

    selection = load_authoritative_closed_paper_trades(
        project_root=tmp_path,
        explicit_path=snapshot,
    )

    assert selection.status == "ok"
    assert selection.reason == "explicit_closed_trade_source_selected_read_only"
    assert snapshot.stat().st_size == before_size
    assert snapshot.stat().st_mtime_ns == before_mtime_ns


def test_live_feedback_loop_reports_close_to_feedback_latency_without_writes(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    _create_realistic_paper_db(
        snapshot,
        trades=[_eth_long_trade(close_date="2026-09-10 19:00:00")],
    )

    report = run_paper_autolearning_live_feedback_loop_v1(
        project_root=tmp_path,
        write=False,
    )

    assert report["status"] == "ok"
    assert report["new_outcome_event_count"] == 1
    assert report["feedback_created_at_utc"] is not None
    assert report["new_outcome_latest_close_time_utc"].startswith("2026-09-10T19:00:00")
    assert report["close_to_feedback_latency_seconds_latest"] is not None
    assert report["close_to_feedback_latency_seconds_p50"] is not None
    assert report["close_to_feedback_latency_seconds_max"] is not None
    assert report["write_performed"] is False
    assert report["sends_orders"] is False


def test_live_feedback_loop_reconciles_legacy_row_and_appends_only_unseen_trade(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    _create_realistic_paper_db(snapshot, trades=[_eth_long_trade(), _btc_short_trade()])

    outcome_path = tmp_path / "data/feedback/outcome_events.parquet"
    outcome_path.parent.mkdir(parents=True, exist_ok=True)

    legacy = {column: None for column in OUTCOME_EVENT_COLUMNS}
    legacy.update(
        {
            "event_id": "legacy-outcome-1",
            "source": "paper_closed_trade",
            "order_id": "freqtrade-paper-1",
            "trade_id": "",
            "row_fingerprint": "legacy-row-1",
            "symbol": "ETHUSDT",
            "symbol_norm": "ETHUSDT",
            "market_type": "futures_perpetual",
            "side": "long",
            "position_side": "long",
            "leverage": 2.0,
            "open_time_utc": "2026-06-01T20:15:07.045313+00:00",
            "close_time_utc": "2026-06-01T22:01:49.990000+00:00",
            "duration_seconds": 6402.944687,
            "is_closed": True,
            "entry_price": 2002.32,
            "exit_price": 1987.16,
            "net_pnl": -0.81114816,
            "profit_ratio": -0.016531557328344805,
            "exit_reason": "stop_loss",
            "validation_status": "ok",
            "validation_errors": [],
        }
    )
    pd.DataFrame([legacy], columns=OUTCOME_EVENT_COLUMNS).to_parquet(outcome_path, index=False)

    report = run_paper_autolearning_live_feedback_loop_v1(
        project_root=tmp_path,
        write=True,
    )

    assert report["status"] == "ok"
    assert report["existing_outcome_event_count"] == 1
    assert report["lineage_matched_count"] == 1
    assert report["lineage_update_count"] == 1
    assert report["new_outcome_event_count"] == 1
    assert report["projected_outcome_event_count"] == 2
    assert report["projected_duplicate_order_id_count"] == 0
    assert report["projected_duplicate_trade_id_count"] == 0
    assert report["projected_trade_id_coverage"] == 1.0
    assert report["projected_quantity_coverage"] == 1.0
    assert report["projected_notional_coverage"] == 1.0
    assert report["projected_net_pnl_coverage"] == 1.0
    assert report["microbatch_rows"] == 1
    assert report["writes_sqlite"] is False
    assert report["sends_orders"] is False

    final = pd.read_parquet(outcome_path)
    assert len(final) == 2
    assert set(final["order_id"]) == {"freqtrade-paper-1", "freqtrade-paper-2"}
    assert set(final["trade_id"].astype(str)) == {"1", "2"}
    assert final["quantity"].notna().all()
    assert final["notional"].notna().all()


def test_live_feedback_loop_dry_run_materializes_in_memory_only(tmp_path: Path) -> None:
    snapshot = tmp_path / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    _create_realistic_paper_db(snapshot, trades=[_eth_long_trade()])

    report = run_paper_autolearning_live_feedback_loop_v1(
        project_root=tmp_path,
        write=False,
    )

    assert report["status"] == "ok"
    assert report["reason"] == "incremental_feedback_materialized"
    assert report["new_outcome_event_count"] == 1
    assert report["microbatch_rows"] == 1
    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert report["writes_sqlite"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["changes_risk"] is False
    assert not (tmp_path / "data/feedback/outcome_events.parquet").exists()


def test_known_historical_outcome_is_not_reported_as_reprocessed(tmp_path: Path) -> None:
    snapshot = tmp_path / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    _create_realistic_paper_db(snapshot, trades=[_eth_long_trade()])
    first = run_paper_autolearning_live_feedback_loop_v1(project_root=tmp_path, write=True)

    report = run_paper_autolearning_live_feedback_loop_v1(project_root=tmp_path, write=False)

    assert first["new_outcome_event_count"] == 1
    assert report["new_outcome_event_count"] == 0
    assert report["microbatch_rows"] == 0
    assert report["duplicate_outcome_event_count"] == 1
    assert report["already_known_outcome_event_count"] == 1
    assert report["intra_source_duplicate_outcome_event_count"] == 0
    assert report["duplicate_or_reprocessed_row_count"] == 0
    assert report["write_performed"] is False


def test_intra_source_duplicate_is_reported_separately(tmp_path: Path) -> None:
    trade = {
        "order_id": "freqtrade-paper-1",
        "trade_id": "1",
        "symbol": "ETHUSDT",
        "side": "long",
        "open_time_utc": "2026-06-01T20:15:07.045313+00:00",
        "close_time_utc": "2026-06-01T22:01:49.990000+00:00",
        "entry_price": 2002.32,
        "exit_price": 1987.16,
        "quantity": 0.049,
        "net_pnl": -0.81114816,
    }

    feedback = build_feedback_events(
        project_root=tmp_path,
        closed_trade_rows=[trade, trade],
    )

    assert len(feedback.new_events) == 1
    assert len(feedback.already_known_events) == 0
    assert len(feedback.intra_source_duplicate_events) == 1
    assert len(feedback.duplicate_events) == 1
    assert feedback.duplicate_or_reprocessed_row_count == 1


def test_live_feedback_loop_fails_closed_without_source(tmp_path: Path) -> None:
    report = run_paper_autolearning_live_feedback_loop_v1(
        project_root=tmp_path,
        write=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "no_valid_closed_paper_trade_source"
    assert report["write_performed"] is False
    assert report["sends_orders"] is False
    assert report["live_release_allowed"] is False
