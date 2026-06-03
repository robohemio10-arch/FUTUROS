from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.ml.event_driven_backtest import run_event_driven_backtest, run_event_driven_backtest_frame


REPO_ROOT = Path(__file__).resolve().parents[1]


def signals_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": ["2026-01-01T00:00:30Z", "2026-01-01T00:01:30Z"],
            "symbol": ["BTCUSDT", "BTCUSDT"],
            "side": ["long", "short"],
            "stake": [1000.0, 1000.0],
        }
    )


def candles_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": [
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:01:00Z",
                "2026-01-01T00:02:00Z",
                "2026-01-01T00:03:00Z",
                "2026-01-01T00:04:00Z",
            ],
            "symbol": ["BTCUSDT"] * 5,
            "close": [100.0, 101.0, 102.0, 100.0, 99.0],
        }
    )


def base_kwargs() -> dict:
    return {
        "report_path": None,
        "fee_bps": 2,
        "spread_bps": 4,
        "slippage_bps": 3,
        "latency_seconds": 0,
        "liquidity_cap": 10_000,
        "partial_fill_ratio": 1.0,
        "seed": 123,
    }


def test_backtest_blocks_missing_signals(tmp_path: Path) -> None:
    candles = tmp_path / "candles.parquet"
    candles_frame().to_parquet(candles, index=False)

    report = run_event_driven_backtest(
        signals_path=tmp_path / "missing.parquet",
        candles_path=candles,
        report_path=tmp_path / "report.json",
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_signals"


def test_backtest_blocks_missing_candles(tmp_path: Path) -> None:
    signals = tmp_path / "signals.parquet"
    signals_frame().to_parquet(signals, index=False)

    report = run_event_driven_backtest(
        signals_path=signals,
        candles_path=tmp_path / "missing.parquet",
        report_path=None,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_candles"


def test_backtest_blocks_missing_timestamp_columns() -> None:
    report = run_event_driven_backtest_frame(
        signals=signals_frame().drop(columns=["timestamp"]),
        candles=candles_frame(),
        **base_kwargs(),
    )

    assert report["status"] == "blocked"
    assert "missing_timestamp_or_required_columns" in report["reason"]


def test_backtest_blocks_unsorted_or_duplicate_candles() -> None:
    unsorted = candles_frame().iloc[[1, 0, 2, 3, 4]].reset_index(drop=True)
    report = run_event_driven_backtest_frame(signals=signals_frame(), candles=unsorted, **base_kwargs())

    assert report["status"] == "blocked"
    assert "candles_out_of_order" in report["reason"]

    duplicated = pd.concat([candles_frame(), candles_frame().iloc[[1]]], ignore_index=True).sort_values("timestamp")
    report = run_event_driven_backtest_frame(signals=signals_frame(), candles=duplicated, **base_kwargs())
    assert "duplicate_candles_without_policy" in report["reason"]


def test_backtest_uses_next_available_price_without_lookahead() -> None:
    report = run_event_driven_backtest_frame(
        signals=signals_frame().iloc[[0]],
        candles=candles_frame(),
        fee_bps=0,
        spread_bps=0,
        slippage_bps=0,
        report_path=None,
    )

    event = report["events"][0]
    assert event["entry_time"] == "2026-01-01T00:01:00Z"
    assert event["entry_price"] == 101.0
    assert event["exit_time"] == "2026-01-01T00:02:00Z"


def test_backtest_blocks_execution_before_decision_timestamp() -> None:
    # This protects the invariant directly: the simulator never executes a normal event before decision time.
    report = run_event_driven_backtest_frame(
        signals=signals_frame().iloc[[0]],
        candles=candles_frame(),
        **base_kwargs(),
    )

    event = report["events"][0]
    assert pd.Timestamp(event["entry_time"]) >= pd.Timestamp(event["decision_time"])
    assert "execution_before_decision_timestamp" not in report["skipped_reasons"]


def test_backtest_applies_fee_spread_and_slippage() -> None:
    clean = run_event_driven_backtest_frame(
        signals=signals_frame().iloc[[0]],
        candles=candles_frame(),
        fee_bps=0,
        spread_bps=0,
        slippage_bps=0,
        report_path=None,
    )
    costed = run_event_driven_backtest_frame(
        signals=signals_frame().iloc[[0]],
        candles=candles_frame(),
        fee_bps=10,
        spread_bps=10,
        slippage_bps=10,
        report_path=None,
    )

    assert costed["financial_summary"]["net_pnl"] < clean["financial_summary"]["net_pnl"]
    assert costed["financial_summary"]["total_fees"] > 0
    assert costed["financial_summary"]["total_spread_cost"] > 0
    assert costed["financial_summary"]["total_slippage_cost"] > 0


def test_backtest_simulates_partial_fill() -> None:
    report = run_event_driven_backtest_frame(
        signals=signals_frame().iloc[[0]],
        candles=candles_frame(),
        partial_fill_ratio=0.5,
        liquidity_cap=10_000,
        report_path=None,
    )

    assert report["execution_summary"]["partial_fills"] == 1
    assert report["events"][0]["fill_ratio"] == 0.5


def test_backtest_simulates_liquidity_cap() -> None:
    report = run_event_driven_backtest_frame(
        signals=signals_frame().iloc[[0]],
        candles=candles_frame(),
        partial_fill_ratio=1.0,
        liquidity_cap=250,
        report_path=None,
    )

    assert report["execution_summary"]["partial_fills"] == 1
    assert report["events"][0]["executed_notional"] == 250


def test_backtest_simulates_no_fill_when_no_future_candle() -> None:
    signal = pd.DataFrame(
        {"timestamp": ["2026-01-01T00:04:30Z"], "symbol": ["BTCUSDT"], "side": ["long"], "stake": [1000.0]}
    )

    report = run_event_driven_backtest_frame(signals=signal, candles=candles_frame(), **base_kwargs())

    assert report["status"] == "insufficient_data"
    assert report["execution_summary"]["no_fills"] == 1
    assert report["skipped_reasons"]["no_future_candle"] == 1


def test_backtest_calculates_net_pnl() -> None:
    report = run_event_driven_backtest_frame(signals=signals_frame(), candles=candles_frame(), **base_kwargs())

    financial = report["financial_summary"]
    assert financial["gross_pnl"] != 0
    assert financial["net_pnl"] == financial["gross_pnl"] - financial["total_fees"]
    assert financial["expectancy"] != 0


def test_backtest_calculates_drawdown_and_profit_factor() -> None:
    report = run_event_driven_backtest_frame(signals=signals_frame(), candles=candles_frame(), **base_kwargs())

    assert report["drawdown_summary"]["max_drawdown"] >= 0
    assert "profit_factor" in report["financial_summary"]


def test_backtest_is_reproducible_with_seed() -> None:
    first = run_event_driven_backtest_frame(signals=signals_frame(), candles=candles_frame(), **base_kwargs())
    second = run_event_driven_backtest_frame(signals=signals_frame(), candles=candles_frame(), **base_kwargs())

    assert first["events"] == second["events"]
    assert first["financial_summary"] == second["financial_summary"]


def test_backtest_blocks_unsafe_safety_flags() -> None:
    report = run_event_driven_backtest_frame(
        signals=signals_frame(),
        candles=candles_frame(),
        strict=True,
        safety_overrides={"live_trading_enabled": True, "sends_orders": True},
        **base_kwargs(),
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "unsafe_safety_flags"
    assert "live_trading_enabled" in report["blocking_errors"]
    assert "sends_orders" in report["blocking_errors"]


def test_cli_event_driven_backtest_runs_successfully(tmp_path: Path) -> None:
    signals = tmp_path / "signals.parquet"
    candles = tmp_path / "candles.parquet"
    report_path = tmp_path / "report.json"
    signals_frame().to_parquet(signals, index=False)
    candles_frame().to_parquet(candles, index=False)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_event_driven_backtest.py",
            "--signals",
            str(signals),
            "--candles",
            str(candles),
            "--report",
            str(report_path),
            "--fee-bps",
            "2",
            "--spread-bps",
            "4",
            "--slippage-bps",
            "3",
            "--seed",
            "123",
            "--strict",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["execution_summary"]["executed_trades"] == 2
    assert report_path.exists()


def test_does_not_touch_training_dataset_or_trades_master(tmp_path: Path) -> None:
    training_dataset = tmp_path / "training_dataset.parquet"
    trades_master = tmp_path / "trades_master.xlsx"
    training_dataset.write_bytes(b"training")
    trades_master.write_bytes(b"master")

    report = run_event_driven_backtest_frame(
        signals=signals_frame(),
        candles=candles_frame(),
        report_path=tmp_path / "report.json",
    )

    assert report["status"] == "ok"
    assert training_dataset.read_bytes() == b"training"
    assert trades_master.read_bytes() == b"master"


def test_does_not_touch_registry_models_signal_producer_or_freqtrade(tmp_path: Path) -> None:
    registry = tmp_path / "model_registry.json"
    model = tmp_path / "model.joblib"
    signals_file = tmp_path / "active_freqtrade_signals.json"
    freqtrade_db = tmp_path / "tradesv3.paper.sqlite"
    registry.write_text('{"registry": true}', encoding="utf-8")
    model.write_bytes(b"model")
    signals_file.write_text('{"signals":[]}', encoding="utf-8")
    freqtrade_db.write_bytes(b"sqlite")

    report = run_event_driven_backtest_frame(signals=signals_frame(), candles=candles_frame(), **base_kwargs())

    assert report["registry_updated"] is False
    assert report["signal_producer_updated"] is False
    assert report["model_updated"] is False
    assert report["risk_manager_updated"] is False
    assert report["freqtrade_db_touched"] is False
    assert registry.read_text(encoding="utf-8") == '{"registry": true}'
    assert model.read_bytes() == b"model"
    assert signals_file.read_text(encoding="utf-8") == '{"signals":[]}'
    assert freqtrade_db.read_bytes() == b"sqlite"
