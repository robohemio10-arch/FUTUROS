from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from smartcrypto.market.market_data_health import (
    MarketDataHealthLimits,
    run_market_data_health_audit,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)


def write_frame(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    if path.suffix == ".parquet":
        frame.to_parquet(path, index=False)
    elif path.suffix == ".csv":
        frame.to_csv(path, index=False)
    elif path.suffix == ".jsonl":
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    else:
        path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def iso(seconds_ago: int) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).isoformat().replace("+00:00", "Z")


def clean_rows() -> dict[str, list[dict]]:
    return {
        "candles": [
            {
                "symbol": "BTCUSDT",
                "timestamp": iso(60),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 10.0,
            }
        ],
        "ticker": [
            {
                "symbol": "BTCUSDT",
                "timestamp": iso(10),
                "bid": 100.0,
                "ask": 100.1,
                "latency_ms": 100.0,
            }
        ],
        "order_book": [
            {
                "symbol": "BTCUSDT",
                "timestamp": iso(5),
                "bid": 100.0,
                "ask": 100.1,
                "top_of_book_depth": 50_000.0,
                "estimated_slippage_bps": 5.0,
                "latency_ms": 90.0,
            }
        ],
        "trades": [{"symbol": "BTCUSDT", "timestamp": iso(5), "price": 100.05, "amount": 1.0}],
        "ws_heartbeat": [{"symbol": "BTCUSDT", "timestamp": iso(5), "latency_ms": 80.0}],
        "rest_snapshot": [{"symbol": "BTCUSDT", "timestamp": iso(4), "latency_ms": 90.0}],
    }


def write_sources(tmp_path: Path, **overrides) -> dict[str, Path]:
    rows = clean_rows()
    for key, value in overrides.items():
        rows[key] = value
    return {
        "candles_path": write_frame(tmp_path / "candles.parquet", rows["candles"]),
        "ticker_path": write_frame(tmp_path / "ticker.csv", rows["ticker"]),
        "order_book_path": write_frame(tmp_path / "order_book.jsonl", rows["order_book"]),
        "trades_path": write_frame(tmp_path / "trades.json", rows["trades"]),
        "ws_heartbeat_path": write_frame(tmp_path / "ws_heartbeat.csv", rows["ws_heartbeat"]),
        "rest_snapshot_path": write_frame(tmp_path / "rest_snapshot.parquet", rows["rest_snapshot"]),
    }


def audit(paths: dict[str, Path], **kwargs):
    return run_market_data_health_audit(
        **paths,
        report_path=None,
        limits=kwargs.pop("limits", MarketDataHealthLimits()),
        now=kwargs.pop("now", NOW),
        **kwargs,
    )


def reasons(report: dict) -> set[str]:
    return {
        reason.split(":", 1)[-1]
        for reason in report["validation_errors"]
    }


def test_market_data_health_blocks_missing_required_input_in_strict_mode(tmp_path):
    report = run_market_data_health_audit(report_path=None, strict=True, now=NOW)

    assert report["status"] == "blocked"
    assert "missing_required_input" in report["validation_errors"]


def test_data_freshness_guard_blocks_stale_candle(tmp_path):
    paths = write_sources(tmp_path, candles=[{**clean_rows()["candles"][0], "timestamp": iso(1_000)}])

    report = audit(paths)

    assert report["status"] == "blocked"
    assert "candle_stale" in reasons(report)


def test_data_freshness_guard_blocks_stale_ticker(tmp_path):
    paths = write_sources(tmp_path, ticker=[{**clean_rows()["ticker"][0], "timestamp": iso(500)}])

    report = audit(paths)

    assert report["status"] == "blocked"
    assert "ticker_stale" in reasons(report)


def test_order_book_guard_blocks_stale_order_book(tmp_path):
    paths = write_sources(tmp_path, order_book=[{**clean_rows()["order_book"][0], "timestamp": iso(300)}])

    report = audit(paths)

    assert report["status"] == "blocked"
    assert "order_book_stale" in reasons(report)


def test_spread_guard_blocks_high_spread(tmp_path):
    paths = write_sources(tmp_path, order_book=[{**clean_rows()["order_book"][0], "bid": 100.0, "ask": 102.0}])

    report = audit(paths)

    assert report["status"] == "blocked"
    assert "spread_bps_above_limit" in reasons(report)


def test_liquidity_guard_blocks_low_depth(tmp_path):
    paths = write_sources(tmp_path, order_book=[{**clean_rows()["order_book"][0], "top_of_book_depth": 10.0}])

    report = audit(paths)

    assert report["status"] == "blocked"
    assert "top_of_book_depth_below_min" in reasons(report)


def test_liquidity_guard_blocks_high_estimated_slippage(tmp_path):
    paths = write_sources(tmp_path, order_book=[{**clean_rows()["order_book"][0], "estimated_slippage_bps": 50.0}])

    report = audit(paths)

    assert report["status"] == "blocked"
    assert "estimated_slippage_bps_above_limit" in reasons(report)


def test_latency_guard_blocks_high_latency(tmp_path):
    paths = write_sources(tmp_path, ticker=[{**clean_rows()["ticker"][0], "latency_ms": 2_000.0}])

    report = audit(paths)

    assert report["status"] == "blocked"
    assert "latency_ms_above_limit" in reasons(report)


def test_ws_rest_divergence_guard_blocks_large_delta(tmp_path):
    paths = write_sources(
        tmp_path,
        ws_heartbeat=[{**clean_rows()["ws_heartbeat"][0], "timestamp": iso(1)}],
        rest_snapshot=[{**clean_rows()["rest_snapshot"][0], "timestamp": iso(60)}],
    )

    report = audit(paths)

    assert report["status"] == "blocked"
    assert "ws_rest_timestamp_delta_above_limit" in reasons(report)


def test_market_data_health_accepts_clean_market_data(tmp_path):
    paths = write_sources(tmp_path)

    report = audit(paths, strict=True)

    assert report["status"] == "ok"
    assert report["blocked_symbols"] == []
    assert report["global_summary"]["stale_data_count"] == 0
    assert report["paper_only"] is True
    assert report["shadow_only"] is True


def test_market_data_health_reports_warning_for_missing_optional_sources(tmp_path):
    paths = {
        "candles_path": write_frame(tmp_path / "candles.parquet", clean_rows()["candles"]),
        "ticker_path": write_frame(tmp_path / "ticker.csv", clean_rows()["ticker"]),
    }

    report = audit(paths, strict=False)

    assert report["status"] == "warning"
    assert any(item.startswith("missing_optional_source:") for item in report["warnings"])


def test_market_data_health_blocks_unsafe_safety_flags(tmp_path):
    paths = write_sources(tmp_path)

    report = run_market_data_health_audit(
        **paths,
        report_path=None,
        safety_overrides={"live_trading_enabled": True, "order_submission_enabled": True},
        now=NOW,
    )

    assert report["status"] == "blocked"
    assert "unsafe_safety_flag:live_trading_enabled" in report["validation_errors"]
    assert "unsafe_safety_flag:order_submission_enabled" in report["validation_errors"]


def test_cli_run_market_data_health_audit_runs_successfully(tmp_path):
    current = datetime.now(timezone.utc)

    def current_iso(seconds_ago: int) -> str:
        return (current - timedelta(seconds=seconds_ago)).isoformat().replace("+00:00", "Z")

    rows = clean_rows()
    for source_rows in rows.values():
        for row in source_rows:
            row["timestamp"] = current_iso(1)
    paths = write_sources(tmp_path, **rows)
    report_path = tmp_path / "market_data_health.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_market_data_health_audit.py"),
            "--candles",
            str(paths["candles_path"]),
            "--ticker",
            str(paths["ticker_path"]),
            "--order-book",
            str(paths["order_book_path"]),
            "--trades",
            str(paths["trades_path"]),
            "--ws-heartbeat",
            str(paths["ws_heartbeat_path"]),
            "--rest-snapshot",
            str(paths["rest_snapshot_path"]),
            "--report",
            str(report_path),
            "--strict",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert report_path.exists()


def test_does_not_touch_training_dataset_or_trades_master(tmp_path):
    trades_master = tmp_path / "trades_master.parquet"
    training_dataset = tmp_path / "training_dataset.parquet"
    write_frame(trades_master, [{"symbol": "BTCUSDT", "timestamp": iso(1), "price": 100.0}])
    write_frame(training_dataset, [{"symbol": "BTCUSDT", "timestamp": iso(1), "feature": 1.0}])
    before = {trades_master: trades_master.read_bytes(), training_dataset: training_dataset.read_bytes()}

    audit(write_sources(tmp_path / "sources"))

    assert {path: path.read_bytes() for path in before} == before


def test_does_not_touch_registry_models_signal_producer_risk_manager_or_freqtrade(tmp_path):
    sentinels = [
        tmp_path / "model_registry.json",
        tmp_path / "shadow_model.pkl",
        tmp_path / "active_freqtrade_signals.json",
        tmp_path / "risk_manager.yml",
        tmp_path / "tradesv3.paper.sqlite",
    ]
    for sentinel in sentinels:
        sentinel.write_text(f"sentinel:{sentinel.name}", encoding="utf-8")
    before = {path: path.read_text(encoding="utf-8") for path in sentinels}

    audit(write_sources(tmp_path / "sources"))

    assert {path: path.read_text(encoding="utf-8") for path in sentinels} == before
