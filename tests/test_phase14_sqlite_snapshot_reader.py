from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.data import paper_trade_lifecycle as lifecycle
from smartcrypto.data.paper_trade_lifecycle import (
    PaperFeedbackConfig,
    PaperTradeLifecycleError,
    cleanup_sqlite_snapshot,
    collect_closed_feedback,
    create_sqlite_snapshot,
    inspect_open_positions,
    read_trades,
)


def create_trade_db(path: Path) -> Path:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            create table trades (
                id integer primary key,
                pair text,
                is_open integer,
                is_short integer,
                open_rate real,
                close_rate real,
                open_date text,
                close_date text,
                enter_tag text,
                exit_reason text,
                realized_profit real,
                close_profit real,
                close_profit_abs real,
                amount real,
                leverage real,
                fee_open_cost real,
                fee_close_cost real
            )
            """
        )
        conn.executemany(
            """
            insert into trades (
                id, pair, is_open, is_short, open_rate, close_rate, open_date, close_date,
                enter_tag, exit_reason, realized_profit, close_profit, close_profit_abs,
                amount, leverage, fee_open_cost, fee_close_cost
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "BTC/USDT:USDT", 1, 0, 100.0, None, "2026-01-01", None, "paper", None, 0.0, 0.0, 0.0, 0.1, 2.0, 0.0, 0.0),
                (2, "ETH/USDT:USDT", 0, 1, 200.0, 190.0, "2026-01-02", "2026-01-03", "paper", "roi", 1.2, 0.03, 1.2, 0.2, 3.0, 0.1, 0.1),
            ],
        )
    return path


def config(tmp_path: Path, db_path: Path) -> PaperFeedbackConfig:
    return PaperFeedbackConfig(
        db_candidates=(db_path,),
        raw_export=tmp_path / "runtime" / "freqtrade_paper_trades_raw.parquet",
        closed_export_parquet=tmp_path / "runtime" / "freqtrade_paper_closed_smartcrypto.parquet",
        closed_export_csv=tmp_path / "runtime" / "freqtrade_paper_closed_smartcrypto.csv",
        inbox_export_csv=tmp_path / "runtime" / "inbox" / "freqtrade_paper_closed_trades.csv",
        open_positions_report=tmp_path / "runtime" / "phase14_open_positions_report.json",
        closed_feedback_report=tmp_path / "runtime" / "phase14_closed_feedback_report.json",
        output_summary=tmp_path / "runtime" / "phase14_output_summary.json",
        summary=tmp_path / "runtime" / "phase14_summary.json",
        expected_pairs=("BTC/USDT:USDT", "ETH/USDT:USDT"),
        max_open_trades=2,
    )


def test_creates_sqlite_snapshot_in_tmp_path(tmp_path) -> None:
    source = create_trade_db(tmp_path / "tradesv3.paper.sqlite")

    snapshot = create_sqlite_snapshot(source, snapshot_dir=tmp_path / "snapshot")

    assert snapshot.exists()
    assert snapshot != source
    assert snapshot.parent == tmp_path / "snapshot"
    cleanup_sqlite_snapshot(snapshot)


def test_reads_trades_from_snapshot(tmp_path) -> None:
    source = create_trade_db(tmp_path / "tradesv3.paper.sqlite")

    trades = read_trades(source, use_snapshot=True)

    assert len(trades) == 2
    assert list(trades["id"]) == [1, 2]


def test_use_snapshot_does_not_connect_to_source_path(tmp_path, monkeypatch) -> None:
    source = create_trade_db(tmp_path / "tradesv3.paper.sqlite")
    original_connect = lifecycle.sqlite3.connect
    connected_paths: list[Path] = []

    def tracking_connect(path, *args, **kwargs):
        connected_paths.append(Path(path))
        return original_connect(path, *args, **kwargs)

    monkeypatch.setattr(lifecycle.sqlite3, "connect", tracking_connect)

    trades = read_trades(source, use_snapshot=True)

    assert len(trades) == 2
    assert connected_paths
    assert all(path != source for path in connected_paths)


def test_cleanup_sqlite_snapshot_removes_snapshot_when_possible(tmp_path) -> None:
    source = create_trade_db(tmp_path / "tradesv3.paper.sqlite")
    snapshot_dir = tmp_path / "phase14_sqlite_snapshot_test"
    snapshot = create_sqlite_snapshot(source, snapshot_dir=snapshot_dir)

    assert snapshot.exists()

    cleanup_sqlite_snapshot(snapshot)

    assert not snapshot.exists()


def test_missing_source_returns_controlled_error(tmp_path) -> None:
    with pytest.raises(PaperTradeLifecycleError, match="sqlite_source_missing"):
        read_trades(tmp_path / "missing.sqlite", use_snapshot=True)


def test_inspect_open_positions_uses_snapshot(tmp_path) -> None:
    source = create_trade_db(tmp_path / "tradesv3.paper.sqlite")
    cfg = config(tmp_path, source)

    report = inspect_open_positions(cfg)

    assert report["status"] == "ok"
    assert report["db_snapshot_used"] is True
    assert report["rows"] == 2
    assert report["open_rows"] == 1
    assert cfg.open_positions_report.exists()


def test_collect_closed_feedback_uses_snapshot(tmp_path) -> None:
    source = create_trade_db(tmp_path / "tradesv3.paper.sqlite")
    cfg = config(tmp_path, source)

    report = collect_closed_feedback(cfg)

    assert report["status"] == "ok"
    assert report["db_snapshot_used"] is True
    assert report["raw_rows"] == 2
    assert report["closed_rows"] == 1
    assert cfg.raw_export.exists()
    assert cfg.closed_export_csv.exists()
    assert cfg.inbox_export_csv.exists()


def test_snapshot_read_error_is_written_to_report(tmp_path, monkeypatch) -> None:
    source = create_trade_db(tmp_path / "tradesv3.paper.sqlite")
    cfg = config(tmp_path, source)

    def failing_read_trades(*args, **kwargs):
        raise PaperTradeLifecycleError("sqlite_snapshot_copy_failed:test")

    monkeypatch.setattr(lifecycle, "read_trades", failing_read_trades)

    report = inspect_open_positions(cfg)

    assert report["status"] == "blocked"
    assert report["reason"] == "freqtrade_db_read_failed"
    assert report["db_snapshot_used"] is True
    assert "sqlite_snapshot_copy_failed" in report["error"]
    assert json.loads(cfg.open_positions_report.read_text(encoding="utf-8"))["status"] == "blocked"


def test_wrapper_path_exists_in_repository() -> None:
    wrapper = Path("paper_controlado_fase_14/RUN_PHASE14_FULL_FEEDBACK_SYNC.ps1")
    text = wrapper.read_text(encoding="utf-8")

    assert wrapper.exists()
    assert "$LASTEXITCODE" in text
    assert "Assert-NativeOk" in text


def test_no_exchange_private_or_mutating_project_files_referenced() -> None:
    text = "\n".join(
        [
            Path("smartcrypto/data/paper_trade_lifecycle.py").read_text(encoding="utf-8"),
            Path("paper_controlado_fase_14/RUN_PHASE14_FULL_FEEDBACK_SYNC.ps1").read_text(encoding="utf-8"),
        ]
    )
    forbidden = [
        "ccxt",
        "create_order",
        "fetch_balance",
        "LIVE_ENABLED=true",
        "ORDER_SUBMISSION_ENABLED=true",
        "REAL_ORDER_SUBMISSION_ENABLED=true",
        ".env",
        "START_PAPER_24H",
    ]
    assert all(token not in text for token in forbidden)
