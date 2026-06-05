from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from scripts import collect_market_data_health_runtime_sources as collector_cli
from smartcrypto.market.market_data_health import MarketDataHealthLimits, run_market_data_health_audit
from smartcrypto.market_data.health_runtime_sources import collect_market_data_health_runtime_sources

NOW = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)


def fake_fetcher(endpoint: str, params: dict, timeout_seconds: float):
    symbol = params.get("symbol", "BTCUSDT")
    if endpoint == "/fapi/v1/ticker/bookTicker":
        return {"symbol": symbol, "bidPrice": "100.00", "askPrice": "100.10", "bidQty": "50", "askQty": "55"}, 12.5
    if endpoint == "/fapi/v1/depth":
        return {
            "lastUpdateId": 1,
            "bids": [["100.00", "50"], ["99.90", "25"]],
            "asks": [["100.10", "55"], ["100.20", "25"]],
        }, 15.0
    if endpoint == "/fapi/v1/trades":
        return [{"id": 1, "price": "100.05", "qty": "1.5", "time": int(NOW.timestamp() * 1000)}], 14.0
    if endpoint == "/fapi/v1/time":
        return {"serverTime": int(NOW.timestamp() * 1000)}, 10.0
    raise AssertionError(f"unexpected endpoint: {endpoint}")


def failing_fetcher(endpoint: str, params: dict, timeout_seconds: float):
    raise TimeoutError("network down")


def load_rows(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["rows"]


def collect(tmp_path: Path, *, strict: bool = False, fetcher=fake_fetcher, now: datetime = NOW) -> dict:
    return collect_market_data_health_runtime_sources(
        symbols=["BTCUSDT", "ETHUSDT"],
        output_dir=tmp_path / "runtime" / "market_health",
        report_path=tmp_path / "reports" / "market_data_health_runtime_sources_report.json",
        timeout_seconds=1.0,
        strict=strict,
        fetcher=fetcher,
        now=now,
    )


def write_candles(path: Path, *, seconds_ago: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {"symbol": "BTCUSDT", "timestamp": (NOW - timedelta(seconds=seconds_ago)).isoformat().replace("+00:00", "Z"), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
            {"symbol": "ETHUSDT", "timestamp": (NOW - timedelta(seconds=seconds_ago)).isoformat().replace("+00:00", "Z"), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        ]
    ).to_parquet(path, index=False)
    return path


def test_collects_public_market_health_sources_without_private_access(tmp_path: Path) -> None:
    report = collect(tmp_path)
    assert report["status"] == "ok"
    assert report["exchange"] == "binance_usdt_m_futures_public"
    assert report["public_data_only"] is True
    assert report["private_endpoints_used"] is False
    assert report["exchange_private_access"] is False
    assert report["sends_orders"] is False


def test_outputs_ticker_order_book_trades_rest_snapshot_and_heartbeat(tmp_path: Path) -> None:
    report = collect(tmp_path)
    paths = {name: Path(path) for name, path in report["runtime_source_paths"].items()}
    assert set(paths) == {"ticker", "order_book", "trades", "rest_snapshot", "ws_heartbeat"}
    assert all(path.exists() for path in paths.values())
    assert len(load_rows(paths["ticker"])) == 2
    assert len(load_rows(paths["order_book"])) == 2
    assert len(load_rows(paths["trades"])) == 2
    assert len(load_rows(paths["rest_snapshot"])) == 2
    assert len(load_rows(paths["ws_heartbeat"])) == 2


def test_computes_spread_bps_depth_slippage_and_latency(tmp_path: Path) -> None:
    report = collect(tmp_path)
    metrics = report["metrics"]["BTCUSDT"]
    assert metrics["spread_bps"] > 0
    assert metrics["top_of_book_depth"] > 10_000
    assert metrics["estimated_slippage_bps"] > 0
    assert metrics["latency_ms"] >= 15.0
    assert metrics["last_ticker_age_seconds"] == 0.0
    assert metrics["last_order_book_age_seconds"] == 0.0
    assert metrics["rest_snapshot_age_seconds"] == 0.0
    assert metrics["ws_heartbeat_age_seconds"] == 0.0
    assert metrics["ws_rest_timestamp_delta_seconds"] == 0.0


def test_blocks_or_warns_on_stale_sources(tmp_path: Path) -> None:
    report = collect(tmp_path, now=NOW)
    candles = write_candles(tmp_path / "candles.parquet", seconds_ago=1)
    audit = run_market_data_health_audit(
        candles_path=candles,
        ticker_path=report["runtime_source_paths"]["ticker"],
        order_book_path=report["runtime_source_paths"]["order_book"],
        trades_path=report["runtime_source_paths"]["trades"],
        rest_snapshot_path=report["runtime_source_paths"]["rest_snapshot"],
        ws_heartbeat_path=report["runtime_source_paths"]["ws_heartbeat"],
        report_path=None,
        limits=MarketDataHealthLimits(max_ticker_age_seconds=60, max_ws_heartbeat_age_seconds=60, max_order_book_age_seconds=60),
        now=NOW + timedelta(seconds=120),
    )
    assert audit["status"] == "blocked"
    assert any("ticker_stale" in item for item in audit["validation_errors"])


def test_handles_network_failure_without_crash(tmp_path: Path) -> None:
    report = collect(tmp_path, strict=False, fetcher=failing_fetcher)
    assert report["status"] == "warning"
    assert report["warnings"]
    assert report["source_counts"]["ws_heartbeat"] == 2

    strict_report = collect(tmp_path / "strict", strict=True, fetcher=failing_fetcher)
    assert strict_report["status"] == "blocked"
    assert "missing_runtime_source:ticker" in strict_report["blocking_errors"]


def test_market_health_audit_accepts_optional_runtime_sources(tmp_path: Path) -> None:
    report = collect(tmp_path)
    candles = write_candles(tmp_path / "candles.parquet", seconds_ago=1)
    audit = run_market_data_health_audit(
        candles_path=candles,
        ticker_path=report["runtime_source_paths"]["ticker"],
        order_book_path=report["runtime_source_paths"]["order_book"],
        trades_path=report["runtime_source_paths"]["trades"],
        rest_snapshot_path=report["runtime_source_paths"]["rest_snapshot"],
        ws_heartbeat_path=report["runtime_source_paths"]["ws_heartbeat"],
        report_path=None,
        now=NOW,
        strict=True,
    )
    assert audit["status"] == "ok"
    assert audit["global_summary"]["sources_present"] == ["candles", "order_book", "rest_snapshot", "ticker", "trades", "ws_heartbeat"]


def test_market_health_audit_remains_backward_compatible_with_candles_only(tmp_path: Path) -> None:
    candles = write_candles(tmp_path / "candles.parquet", seconds_ago=1)
    audit = run_market_data_health_audit(candles_path=candles, report_path=None, now=NOW, strict=False)
    assert audit["status"] == "warning"
    assert "candles" in audit["global_summary"]["sources_present"]
    assert "ticker" in audit["global_summary"]["sources_missing"]


def test_never_sends_orders_or_accesses_private_exchange() -> None:
    checked = [
        Path("smartcrypto/market_data/health_runtime_sources.py"),
        Path("scripts/collect_market_data_health_runtime_sources.py"),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8").lower() for path in checked)
    forbidden = ["create_order", "fetch_balance", "private_get", "freqtradeapi", "ccxt.", "requests.post", "/sapi/", "/api/v3/account"]
    assert not any(token in combined for token in forbidden)


def test_runtime_sources_report_contains_paper_shadow_safety_flags(tmp_path: Path) -> None:
    report = collect(tmp_path)
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["runtime_mode"] == "paper"
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False


def test_does_not_touch_freqtrade_db_models_registry_training_dataset_or_trades_master(tmp_path: Path) -> None:
    protected = [
        tmp_path / "freqtrade" / "user_data" / "tradesv3.paper.sqlite",
        tmp_path / "data" / "models" / "registry" / "model_registry.json",
        tmp_path / "data" / "models" / "shadow" / "model.joblib",
        tmp_path / "data" / "features" / "training_dataset.parquet",
        tmp_path / "data" / "trades" / "trades_master.xlsx",
        tmp_path / "data" / "runtime" / "active_freqtrade_signals.json",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("sentinel", encoding="utf-8")
    collect(tmp_path / "collector")
    assert all(path.read_text(encoding="utf-8") == "sentinel" for path in protected)


def test_cli_collect_market_data_health_runtime_sources_runs_successfully(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("smartcrypto.market_data.health_runtime_sources.public_binance_futures_fetcher", fake_fetcher)
    rc = collector_cli.main(
        [
            "--symbols",
            "BTCUSDT",
            "ETHUSDT",
            "--output-dir",
            str(tmp_path / "runtime" / "market_health"),
            "--report",
            str(tmp_path / "reports" / "report.json"),
            "--timeout-seconds",
            "1",
            "--strict",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert output["status"] == "ok"
    assert Path(output["runtime_source_paths"]["ticker"]).exists()
