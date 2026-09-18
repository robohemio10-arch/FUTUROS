from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.research.aibot_parity.economic_benchmark import (
    EconomicBenchmarkError,
    METHOD_HASH,
    UNAVAILABLE_CROSS_SYSTEM_DIMENSIONS,
    build_economic_benchmark,
    normalize_symbol,
)


def _write_sqlite(path: Path, rows: list[tuple[object, ...]]) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY,
                pair TEXT NOT NULL,
                is_short INTEGER NOT NULL,
                open_rate REAL NOT NULL,
                close_rate REAL NOT NULL,
                amount REAL NOT NULL,
                contract_size REAL NOT NULL,
                leverage REAL NOT NULL,
                close_profit_abs REAL NOT NULL,
                open_date TEXT NOT NULL,
                close_date TEXT NOT NULL,
                is_open INTEGER NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO trades (
                id, pair, is_short, open_rate, close_rate, amount,
                contract_size, leverage, close_profit_abs,
                open_date, close_date, is_open
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()
    finally:
        connection.close()


def _fixture_sources(tmp_path: Path) -> tuple[Path, Path, Path]:
    master_path = tmp_path / "trades_master.xlsx"
    paper_path = tmp_path / "paper.csv"
    sqlite_path = tmp_path / "paper.sqlite"

    master = pd.DataFrame(
        {
            "trade_sequence": [1, 2],
            "symbol": ["BTC/USDT", "ETH/USDT"],
            "side": ["long", "short"],
            "horario_abertura": [
                "2026-06-02T00:00:00Z",
                "2026-06-02T01:00:00Z",
            ],
            "horario_fechamento": [
                "2026-06-02T00:10:00Z",
                "2026-06-02T01:20:00Z",
            ],
            "reported_pnl": [1.0, -0.5],
            "economic_net_pnl": [1.0, -0.5],
            "taxa_lucros_perdas_fechados_pct": [1.0, -0.5],
        }
    )
    with pd.ExcelWriter(master_path, engine="openpyxl") as writer:
        master.to_excel(writer, sheet_name="TRADES", index=False)

    paper = pd.DataFrame(
        {
            "order_id": ["freqtrade-paper-1", "freqtrade-paper-2"],
            "moeda": ["BTC/USDT:USDT", "ETH/USDT:USDT"],
            "fechar_side": ["long", "short"],
            "pnl_fechado": [0.8, -0.4],
        }
    )
    paper.to_csv(paper_path, index=False)

    _write_sqlite(
        sqlite_path,
        [
            (
                1,
                "BTC/USDT:USDT",
                0,
                100.0,
                101.0,
                1.0,
                1.0,
                1.0,
                0.8,
                "2026-06-02T00:00:00Z",
                "2026-06-02T00:10:00Z",
                0,
            ),
            (
                2,
                "ETH/USDT:USDT",
                1,
                100.0,
                99.0,
                1.0,
                1.0,
                1.0,
                -0.4,
                "2026-06-02T01:00:00Z",
                "2026-06-02T01:20:00Z",
                0,
            ),
        ],
    )
    return master_path, paper_path, sqlite_path


def test_normalize_symbol_freqtrade_pair() -> None:
    assert normalize_symbol("BTC/USDT:USDT") == "BTCUSDT"
    assert normalize_symbol("ETH-USDT") == "ETHUSDT"


def test_synthetic_benchmark_is_read_only_and_deterministic(tmp_path: Path) -> None:
    master, paper, sqlite = _fixture_sources(tmp_path)
    before = {path: path.read_bytes() for path in (master, paper, sqlite)}

    report = build_economic_benchmark(
        master_path=master,
        paper_path=paper,
        sqlite_path=sqlite,
        min_trades=1,
        enforce_master_hash=False,
        enforce_frozen_baseline=False,
    )

    assert report["status"] == "ok"
    assert report["method_hash"] == METHOD_HASH
    assert report["global"]["aibot"]["trade_count"] == 2
    assert report["global"]["smart"]["trade_count"] == 2
    assert report["trade_identity_matching"] is False
    assert report["approximate_matching"] is False
    assert report["operational_authority"] is False
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["changes_model"] is False
    assert report["unavailable_cross_system_dimensions"] == UNAVAILABLE_CROSS_SYSTEM_DIMENSIONS
    json.dumps(report, allow_nan=False, sort_keys=True)

    after = {path: path.read_bytes() for path in (master, paper, sqlite)}
    assert after == before


def test_paper_csv_sqlite_identity_mismatch_blocks(tmp_path: Path) -> None:
    master, paper, sqlite = _fixture_sources(tmp_path)
    frame = pd.read_csv(paper)
    frame.loc[1, "order_id"] = "freqtrade-paper-3"
    frame.to_csv(paper, index=False)

    with pytest.raises(EconomicBenchmarkError, match="paper_csv_sqlite_identity_set_mismatch"):
        build_economic_benchmark(
            master_path=master,
            paper_path=paper,
            sqlite_path=sqlite,
            min_trades=1,
            enforce_master_hash=False,
            enforce_frozen_baseline=False,
        )


def test_frozen_baseline_is_fail_closed_for_noncanonical_sources(tmp_path: Path) -> None:
    master, paper, sqlite = _fixture_sources(tmp_path)
    with pytest.raises(EconomicBenchmarkError, match="master_sha256_mismatch"):
        build_economic_benchmark(
            master_path=master,
            paper_path=paper,
            sqlite_path=sqlite,
            min_trades=1,
            enforce_master_hash=True,
            enforce_frozen_baseline=True,
        )
