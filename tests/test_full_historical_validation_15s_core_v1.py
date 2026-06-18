from __future__ import annotations

from pathlib import Path

import pandas as pd

from smartcrypto.research.execution_costs import apply_execution_costs, infer_notional
from smartcrypto.research.historical_validation_15s import ValidationInputs, audit_15s_candle_coverage, run_full_historical_validation
from smartcrypto.research.monte_carlo_risk import MonteCarloConfig, run_monte_carlo
from smartcrypto.research.walkforward_validation import WalkForwardConfig, run_walkforward_validation


def _full_day_15s(symbol: str, date: str) -> pd.DataFrame:
    start = pd.Timestamp(date, tz="UTC")
    timestamps = [start + pd.Timedelta(seconds=15 * i) for i in range(5760)]
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "symbol": [symbol for _ in timestamps],
            "open": [100.0 + (i * 0.01) for i in range(len(timestamps))],
            "high": [100.1 + (i * 0.01) for i in range(len(timestamps))],
            "low": [99.9 + (i * 0.01) for i in range(len(timestamps))],
            "close": [100.05 + (i * 0.01) for i in range(len(timestamps))],
            "volume": [10.0 for _ in timestamps],
        }
    )


def _write_fixture_project(root: Path, *, rows: int = 80) -> None:
    trade_dir = root / "data" / "features"
    candle_root = root / "data" / "raw" / "binance_futures_klines_15s"
    trade_dir.mkdir(parents=True, exist_ok=True)

    start = pd.Timestamp("2026-01-05T00:00:00Z")
    trades = pd.DataFrame(
        {
            "trade_id": [f"t{i:04d}" for i in range(rows)],
            "symbol": ["BTCUSDT" if i % 2 == 0 else "ETHUSDT" for i in range(rows)],
            "open_ts": [start + pd.Timedelta(minutes=i) for i in range(rows)],
            "pnl": [1.0 if i % 3 else -0.65 for i in range(rows)],
            "entry_price": [100.0 + i for i in range(rows)],
            "volume_posicao": ["0,10" for _ in range(rows)],
            "mfe_pct": [0.1 for _ in range(rows)],
            "mae_pct": [-0.05 for _ in range(rows)],
        }
    )
    trades.to_csv(trade_dir / "trade_enriched.csv", index=False)

    for symbol in ("BTCUSDT", "ETHUSDT"):
        symbol_dir = candle_root / symbol
        symbol_dir.mkdir(parents=True, exist_ok=True)
        _full_day_15s(symbol, "2026-01-05").to_csv(symbol_dir / f"{symbol}_15s_20260105.csv", index=False)


def _simple_frame(rows: int = 80) -> pd.DataFrame:
    start = pd.Timestamp("2026-01-05T00:00:00Z")
    return pd.DataFrame(
        {
            "open_time_utc": [start + pd.Timedelta(minutes=i) for i in range(rows)],
            "reported_pnl_usdt": [1.25 if i % 4 else -0.75 for i in range(rows)],
            "entry_price": [100.0 + i for i in range(rows)],
            "volume_posicao": ["0,20" for _ in range(rows)],
        }
    )


def test_15s_candle_coverage_blocks_without_canonical_sources(tmp_path: Path) -> None:
    report = audit_15s_candle_coverage(ValidationInputs(project_root=tmp_path, from_date="2026-01-05", timeframe="15s"))
    assert report["status"] == "blocked"
    assert report["reason"] == "canonical_15s_coverage_validation_errors"
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False


def test_15s_candle_coverage_accepts_only_canonical_full_day_sources(tmp_path: Path) -> None:
    _write_fixture_project(tmp_path)
    report = audit_15s_candle_coverage(ValidationInputs(project_root=tmp_path, from_date="2026-01-05", timeframe="15s"))
    assert report["status"] == "ok"
    assert report["valid_canonical_source_count"] == 2
    assert report["coverage_through_date"] == "2026-01-05"
    assert report["expected_row_count_through_coverage"] == 11520


def test_15s_candle_coverage_rejects_partial_day_sources(tmp_path: Path) -> None:
    candle_root = tmp_path / "data" / "raw" / "binance_futures_klines_15s" / "BTCUSDT"
    candle_root.mkdir(parents=True, exist_ok=True)
    partial = _full_day_15s("BTCUSDT", "2026-01-05").head(10)
    partial.to_csv(candle_root / "BTCUSDT_15s_20260105.csv", index=False)
    report = audit_15s_candle_coverage(ValidationInputs(project_root=tmp_path, from_date="2026-01-05", timeframe="15s"))
    assert report["status"] == "blocked"
    assert report["invalid_canonical_source_count"] == 1


def test_execution_costs_adds_after_costs_column_and_infers_ocr_notional() -> None:
    frame = _simple_frame(20)
    notional = infer_notional(frame)
    assert float(notional.sum()) > 0.0
    output, report = apply_execution_costs(frame, pnl_column="reported_pnl_usdt")
    assert "validation_after_costs_pnl_usdt" in output.columns
    assert report["status"] == "ok"
    assert report["notional_sum_usdt"] > 0.0
    assert report["notional_source"] == "price_times_size"
    assert report["notional_price_column"] == "entry_price"
    assert report["notional_size_column"] == "volume_posicao"
    assert report["sends_orders"] is False


def test_execution_costs_preserves_dot_decimal_price_strings() -> None:
    frame = pd.DataFrame(
        {
            "reported_pnl_usdt": [0.42, -0.21],
            "entry_price": ["60391.6488785", "1858.5528285"],
            "volume_posicao": ["0.1642935", "1,2500000"],
        }
    )
    notional = infer_notional(frame)
    assert 9900.0 < float(notional.iloc[0]) < 10000.0
    assert 2300.0 < float(notional.iloc[1]) < 2400.0

    _, report = apply_execution_costs(frame, pnl_column="reported_pnl_usdt")
    assert report["status"] == "ok"
    assert report["notional_sum_usdt"] < 13000.0
    assert report["total_cost_usdt"] < 30.0
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False



def test_execution_costs_normalizes_symbol_scaled_ocr_prices() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["BTCUSDT", "ETHUSDT", "ETHUSDT"],
            "reported_pnl_usdt": [1.0, 1.0, 1.0],
            "entry_price": ["677701.0", "217777.06", "20770.140053"],
            "volume_posicao": ["0,147412", "4,584163", "4,820929"],
        }
    )

    notional = infer_notional(frame)
    assert 9_900.0 < float(notional.iloc[0]) < 10_100.0
    assert 9_900.0 < float(notional.iloc[1]) < 10_100.0
    assert 9_900.0 < float(notional.iloc[2]) < 10_100.0

    _, report = apply_execution_costs(frame, pnl_column="reported_pnl_usdt")
    assert report["status"] == "ok"
    assert report["notional_source"] == "price_times_size"
    assert report["notional_price_adjusted_rows"] == 3
    assert report["notional_invalid_rows"] == 0
    assert report["notional_max_usdt"] < 11_000.0
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False


def test_execution_costs_falls_back_from_corrupt_position_volume() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["BTC_USDT"],
            "reported_pnl_usdt": [1.421194],
            "entry_price": ["69089.454156"],
            "volume_posicao": ["34.0"],
            "volume_fechado": ["0.14445"],
            "volume_transacao": ["0.14445"],
        }
    )

    notional = infer_notional(frame)
    assert 9_900.0 < float(notional.iloc[0]) < 10_100.0

    _, report = apply_execution_costs(frame, pnl_column="reported_pnl_usdt")
    assert report["status"] == "ok"
    assert report["notional_source"] == "price_times_size"
    assert report["notional_size_fallback_rows"] == 1
    assert report["notional_invalid_rows"] == 0
    assert report["notional_max_usdt"] < 11_000.0
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False


def test_monte_carlo_is_deterministic() -> None:
    frame = _simple_frame(50)
    first = run_monte_carlo(frame, pnl_column="reported_pnl_usdt", config=MonteCarloConfig(iterations=50, seed=7))
    second = run_monte_carlo(frame, pnl_column="reported_pnl_usdt", config=MonteCarloConfig(iterations=50, seed=7))
    assert first["status"] == "ok"
    assert first["bootstrap"]["final_equity_percentiles"] == second["bootstrap"]["final_equity_percentiles"]


def test_walkforward_generates_temporal_folds() -> None:
    frame = _simple_frame(90)
    report = run_walkforward_validation(
        frame,
        timestamp_column="open_time_utc",
        pnl_column="reported_pnl_usdt",
        config=WalkForwardConfig(min_train_rows=30, test_rows=15, embargo_rows=2, max_folds=3),
    )
    assert report["status"] == "ok"
    assert report["fold_count"] == 3
    assert report["folds"][0]["train_end_index_exclusive"] < report["folds"][0]["test_start_index"]


def test_full_validation_uses_trade_enriched_and_preserves_safety(tmp_path: Path) -> None:
    _write_fixture_project(tmp_path, rows=80)
    report = run_full_historical_validation(
        project_root=tmp_path,
        from_date="2026-01-05",
        no_write=True,
        min_trades=10,
        iterations=20,
    )
    assert report["status"] == "ok"
    assert report["reports"]["trade_base"]["source"].endswith("trade_enriched.csv")
    assert report["reports"]["trade_base"]["pnl_column"] == "pnl"
    assert report["reports"]["candle_coverage"]["status"] == "ok"
    assert report["sends_orders"] is False
    assert report["order_submission_enabled"] is False
    assert report["write_performed"] is False
    assert report["readiness"]["status"] == "blocked"
