from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from smartcrypto.learning.paper_autolearning.live_feedback_loop import (
    run_paper_autolearning_live_feedback_loop_v1,
)
from smartcrypto.learning.paper_autolearning.runtime_source import (
    load_authoritative_closed_paper_trades,
)


def _create_paper_db(path: Path, *, trade_id: int, close_date: str, profit_abs: float) -> None:
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
                profit_abs REAL,
                close_profit REAL,
                leverage REAL,
                liquidation_price REAL,
                exit_reason TEXT,
                strategy TEXT,
                is_short INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO trades (
                id, pair, is_open, open_date, close_date, open_rate, close_rate,
                amount, stake_amount, profit_abs, close_profit, leverage,
                liquidation_price, exit_reason, strategy, is_short
            ) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                "ETH/USDT:USDT",
                "2026-09-04 10:00:00",
                close_date,
                4400.0,
                4420.0,
                0.1,
                440.0,
                profit_abs,
                profit_abs / 440.0,
                10.0,
                4000.0,
                "roi" if profit_abs > 0 else "stop_loss",
                "PaperStrategy",
                0,
            ),
        )


def test_selects_freshest_closed_trade_source(tmp_path: Path) -> None:
    runtime = tmp_path / "freqtrade/user_data/tradesv3.paper.sqlite"
    snapshot = tmp_path / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    _create_paper_db(runtime, trade_id=1, close_date="2026-09-01 10:30:00", profit_abs=-1.0)
    _create_paper_db(snapshot, trade_id=2, close_date="2026-09-05 10:30:00", profit_abs=2.0)

    selection = load_authoritative_closed_paper_trades(project_root=tmp_path)

    assert selection.status == "ok"
    assert selection.selected_path == snapshot.resolve()
    assert len(selection.rows) == 1
    assert selection.rows[0]["trade_id"] == 2
    assert selection.rows[0]["pair"] == "ETH/USDT:USDT"
    assert selection.rows[0]["side"] == "long"


def test_source_resolver_never_writes_sqlite(tmp_path: Path) -> None:
    snapshot = tmp_path / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    _create_paper_db(snapshot, trade_id=7, close_date="2026-09-05 11:30:00", profit_abs=1.5)
    before_size = snapshot.stat().st_size
    before_mtime_ns = snapshot.stat().st_mtime_ns

    selection = load_authoritative_closed_paper_trades(project_root=tmp_path)

    assert selection.status == "ok"
    assert snapshot.stat().st_size == before_size
    assert snapshot.stat().st_mtime_ns == before_mtime_ns


def test_live_feedback_loop_dry_run_materializes_in_memory_only(tmp_path: Path) -> None:
    snapshot = tmp_path / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    _create_paper_db(snapshot, trade_id=11, close_date="2026-09-05 12:30:00", profit_abs=3.0)

    report = run_paper_autolearning_live_feedback_loop_v1(
        project_root=tmp_path,
        write=False,
    )

    assert report["status"] == "ok"
    assert report["reason"] == "incremental_feedback_materialized"
    assert report["paper_source_path"] == str(snapshot.resolve())
    assert report["new_outcome_event_count"] == 1
    assert report["microbatch_rows"] == 1
    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert report["writes_sqlite"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["changes_risk"] is False
    assert not (tmp_path / "data/feedback/outcome_events.parquet").exists()
    assert not (tmp_path / "data/feedback/paper_closed_trades_incremental.parquet").exists()


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
