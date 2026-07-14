from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from smartcrypto.data.trader_master_fingerprint_v2.quarantine_forensics import (
    ACCOUNTING_UNEXPLAINED,
    RECOVERED,
    TARGET_TRADE_IDS,
    analyze_quarantined_trade,
    build_targeted_quarantine_forensics_report,
)


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config/freqtrade_paper_closed_trades_source_profile_v2.json"
SCRIPT = ROOT / "scripts/analyze_freqtrade_paper_quarantine_forensics_v2.py"
EPSILON = Decimal("0.00000001")


def trade_row(trade_id: int = 141, **overrides: Any) -> dict[str, Any]:
    row = {
        "id": trade_id,
        "is_short": 0,
        "open_rate": 100.0,
        "close_rate": 110.0,
        "close_rate_requested": 999.0,
        "amount": 1.0,
        "amount_requested": 1.1,
        "stake_amount": 50.0,
        "max_stake_amount": 50.0,
        "open_trade_value": 100.0,
        "contract_size": 1.0,
        "leverage": 1.0,
        "fee_open_cost": 0.0,
        "fee_close_cost": 0.0,
        "funding_fees": 0.0,
        "close_profit_abs": 10.0,
        "realized_profit": 10.0,
    }
    row.update(overrides)
    return row


def order_row(
    order_id: int,
    trade_id: int,
    side: str,
    average: float,
    filled: float,
    **overrides: Any,
) -> dict[str, Any]:
    row = {
        "id": order_id,
        "ft_trade_id": trade_id,
        "status": "closed",
        "side": side,
        "average": average,
        "filled": filled,
        "remaining": 0.0,
        "order_filled_date": "2026-06-01 10:05:00.000000",
        "ft_cancel_reason": None,
    }
    row.update(overrides)
    return row


def analyze(
    trade: dict[str, Any],
    orders: list[dict[str, Any]],
    custom_data: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return analyze_quarantined_trade(
        trade,
        orders,
        custom_data or [],
        epsilon=EPSILON,
    )


def test_multiple_entries_use_weighted_average() -> None:
    trade = trade_row(open_rate=104.0, close_profit_abs=6.0, realized_profit=6.0)
    orders = [
        order_row(1, 141, "buy", 100.0, 0.5),
        order_row(2, 141, "buy", 108.0, 0.5),
        order_row(3, 141, "sell", 110.0, 1.0),
    ]
    result = analyze(trade, orders)
    assert result["multiple_entry_fills"] is True
    assert Decimal(result["weighted_entry_price"]) == Decimal("104")
    assert result["recovery_decision"] == RECOVERED


def test_multiple_partial_exits_use_weighted_average() -> None:
    trade = trade_row(close_rate=116.0, close_profit_abs=16.0, realized_profit=16.0)
    orders = [
        order_row(1, 141, "buy", 100.0, 1.0),
        order_row(2, 141, "sell", 110.0, 0.4),
        order_row(3, 141, "sell", 120.0, 0.6),
    ]
    result = analyze(trade, orders)
    assert result["multiple_exit_fills"] is True
    assert result["partial_exit_detected"] is True
    assert Decimal(result["weighted_exit_price"]) == Decimal("116")
    assert result["recovery_decision"] == RECOVERED


def test_cancelled_order_is_ignored() -> None:
    orders = [
        order_row(1, 141, "buy", 100.0, 1.0),
        order_row(2, 141, "sell", 999.0, 1.0, status="canceled"),
        order_row(3, 141, "sell", 110.0, 1.0),
    ]
    result = analyze(trade_row(), orders)
    assert result["recovery_decision"] == RECOVERED
    assert result["ignored_orders"] == [
        {"order_row_id": 2, "reason": "cancelled_or_rejected_status"}
    ]


def test_unfilled_order_is_ignored() -> None:
    orders = [
        order_row(1, 141, "buy", 100.0, 1.0),
        order_row(2, 141, "sell", 999.0, 0.0, status="open", remaining=1.0),
        order_row(3, 141, "sell", 110.0, 1.0),
    ]
    result = analyze(trade_row(), orders)
    assert result["recovery_decision"] == RECOVERED
    assert {item["reason"] for item in result["ignored_orders"]} == {"not_filled"}


def test_close_rate_requested_is_never_execution_evidence() -> None:
    trade = trade_row(close_rate=None, close_rate_requested=110.0)
    orders = [
        order_row(1, 141, "buy", 100.0, 1.0),
        order_row(2, 141, "sell", 110.0, 1.0),
    ]
    result = analyze(trade, orders)
    assert result["recovery_decision"] == RECOVERED
    assert result["close_rate_requested_used"] is False
    assert all(
        "close_rate_requested" not in candidate["source_columns"]
        for candidate in result["formula_candidates"]
    )


def test_weighted_average_fill_records_authoritative_lineage() -> None:
    result = analyze(
        trade_row(),
        [
            order_row(10, 141, "buy", 100.0, 1.0),
            order_row(11, 141, "sell", 110.0, 1.0),
        ],
    )
    assert result["weighted_average_fill_validated"] is True
    assert result["formula_version"] == "filled_orders_weighted_average_v1"
    assert result["evidence_table"] == ["trades", "orders"]
    assert result["evidence_row_ids"]["orders"] == [10, 11]
    assert Decimal(result["residual"]) == 0


def test_incompatible_total_quantity_remains_quarantined() -> None:
    result = analyze(
        trade_row(close_profit_abs=20.0, realized_profit=20.0),
        [
            order_row(1, 141, "buy", 100.0, 1.0),
            order_row(2, 141, "sell", 110.0, 1.0),
            order_row(3, 141, "sell", 110.0, 1.0),
        ],
    )
    assert result["filled_entry_quantity"] == "1.0"
    assert result["filled_exit_quantity"] == "2.0"
    assert result["recovery_decision"] == ACCOUNTING_UNEXPLAINED
    assert "filled_order_quantity_mismatch" in result["remaining_blockers"]


def test_residual_divergence_remains_quarantined() -> None:
    result = analyze(
        trade_row(close_profit_abs=9.0, realized_profit=9.0),
        [
            order_row(1, 141, "buy", 100.0, 1.0),
            order_row(2, 141, "sell", 110.0, 1.0),
        ],
    )
    assert result["recovery_decision"] == ACCOUNTING_UNEXPLAINED
    assert "financial_accounting_identity_violation" in result["remaining_blockers"]


def _write_snapshot(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """CREATE TABLE trades (
            id INTEGER PRIMARY KEY, is_short INTEGER, open_rate REAL,
            close_rate REAL, close_rate_requested REAL, amount REAL,
            amount_requested REAL, stake_amount REAL, max_stake_amount REAL,
            open_trade_value REAL, contract_size REAL, leverage REAL,
            fee_open_cost REAL, fee_close_cost REAL, funding_fees REAL,
            close_profit_abs REAL, realized_profit REAL
            )"""
        )
        connection.execute(
            """CREATE TABLE orders (
            id INTEGER PRIMARY KEY, ft_trade_id INTEGER, status TEXT, side TEXT,
            average REAL, filled REAL, remaining REAL, order_filled_date TEXT,
            ft_cancel_reason TEXT
            )"""
        )
        connection.execute(
            "CREATE TABLE trade_custom_data (id INTEGER PRIMARY KEY, ft_trade_id INTEGER, cd_key TEXT)"
        )
        for trade_id in sorted(TARGET_TRADE_IDS):
            trade = trade_row(trade_id)
            connection.execute(
                "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(trade.values()),
            )
            connection.execute(
                "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)",
                tuple(order_row(trade_id * 10, trade_id, "buy", 100.0, 1.0).values()),
            )
            connection.execute(
                "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)",
                tuple(order_row(trade_id * 10 + 1, trade_id, "sell", 110.0, 1.0).values()),
            )
        connection.commit()
    finally:
        connection.close()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_snapshot_read_is_query_only_and_does_not_mutate_source(tmp_path: Path) -> None:
    snapshot = tmp_path / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    _write_snapshot(snapshot)
    before = sha256(snapshot)
    report = build_targeted_quarantine_forensics_report(
        project_root=tmp_path,
        source_profile_path=PROFILE,
        authoritative_sqlite_path=snapshot,
    )
    assert report["status"] == "ok"
    assert report["snapshot_temp_copy_used"] is True
    assert report["snapshot_query_only"] is True
    assert report["snapshot_source_hashes_preserved"] is True
    assert report["related_tables_inspected"] == ["trades", "orders", "trade_custom_data"]
    assert report["write_performed"] is False
    assert sha256(snapshot) == before


def test_runtime_sqlite_is_explicitly_rejected(tmp_path: Path) -> None:
    runtime_db = tmp_path / "freqtrade/user_data/tradesv3.paper.sqlite"
    _write_snapshot(runtime_db)
    report = build_targeted_quarantine_forensics_report(
        project_root=tmp_path,
        source_profile_path=PROFILE,
        authoritative_sqlite_path=runtime_db,
    )
    assert report["reason"] == "explicitly_non_authoritative_sqlite_forbidden"
    assert report["snapshot_temp_copy_used"] is False


def test_cli_executes_without_writing(tmp_path: Path) -> None:
    snapshot = tmp_path / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    _write_snapshot(snapshot)
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(tmp_path),
            "--source-profile",
            str(PROFILE),
            "--authoritative-sqlite",
            str(snapshot),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    payload = json.loads(completed.stdout)
    assert payload["status"] == "ok"
    assert payload["target_trade_ids"] == sorted(TARGET_TRADE_IDS)
    assert payload["write_performed"] is False
    assert payload["sends_exchange_orders"] is False
    assert payload["exchange_private_access"] is False


def test_real_probe_reports_all_five_ids_when_snapshot_exists() -> None:
    snapshot = ROOT / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    if not snapshot.exists():
        pytest.skip("authoritative runtime snapshot is not available")
    report = build_targeted_quarantine_forensics_report(project_root=ROOT)
    assert report["status"] == "ok"
    assert [item["trade_id"] for item in report["trade_results"]] == sorted(TARGET_TRADE_IDS)
    assert report["write_performed"] is False
