from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from smartcrypto.data.feature_builder import build_market_features
from smartcrypto.market.market_feature_schema import lookahead_columns
from smartcrypto.qlib_engine.market_features_refresh import refresh_qlib_market_features


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)


def load_module():
    path = ROOT / "scripts" / "sanitize_market_features_lookahead.py"
    spec = importlib.util.spec_from_file_location("sanitize_market_features_lookahead", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def market_frame(periods: int = 6, *, contaminated: bool = True) -> pd.DataFrame:
    rows = []
    for idx in range(periods):
        row = {
            "symbol": "BTCUSDT",
            "pair": "BTC/USDT:USDT",
            "tf": "5m",
            "ts": NOW - timedelta(minutes=5 * (periods - idx - 1)),
            "open": 100 + idx,
            "high": 101 + idx,
            "low": 99 + idx,
            "close": 100.5 + idx,
            "volume": 10 + idx,
            "ret_1": 0.01,
            "ema_20": 100.0,
            "rsi_14": 50.0,
            "market_regime": "range",
        }
        if contaminated:
            row["future_ret_1"] = 0.01
            row["future_ret_3"] = 0.02
            row["future_ret_5"] = 0.03
        rows.append(row)
    return pd.DataFrame(rows)


def raw_ohlcv(periods: int = 260) -> pd.DataFrame:
    rows = []
    for symbol, base in [("BTCUSDT", 100.0), ("ETHUSDT", 200.0)]:
        for idx in range(periods):
            ts = NOW - timedelta(minutes=5 * (periods - idx - 1))
            rows.append(
                {
                    "symbol": symbol,
                    "pair": symbol.replace("USDT", "/USDT:USDT"),
                    "tf": "5m",
                    "ts": ts,
                    "open": base + idx,
                    "high": base + idx + 1,
                    "low": base + idx - 1,
                    "close": base + idx + 0.5,
                    "volume": 100 + idx,
                }
            )
    return pd.DataFrame(rows)


def run_sanitizer(tmp_path: Path, input_path: Path, *, apply: bool = False) -> dict:
    module = load_module()
    return module.sanitize_market_features_lookahead(
        input_path=input_path,
        report_path=tmp_path / "reports" / "sanitize_market_features_lookahead_report.json",
        backup_dir=tmp_path / "backups",
        apply=apply,
    )


def test_dry_run_detects_future_ret_without_writing(tmp_path: Path) -> None:
    target = tmp_path / "features" / "market_features_60d.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    market_frame().to_parquet(target, index=False)
    before = pd.read_parquet(target)

    report = run_sanitizer(tmp_path, target)
    after = pd.read_parquet(target)

    assert report["status"] == "ok"
    assert report["dry_run"] is True
    assert report["write_performed"] is False
    assert report["removed_columns"] == ["future_ret_1", "future_ret_3", "future_ret_5"]
    assert list(after.columns) == list(before.columns)
    assert lookahead_columns(after) == ["future_ret_1", "future_ret_3", "future_ret_5"]


def test_apply_removes_future_ret_and_creates_backup(tmp_path: Path) -> None:
    target = tmp_path / "features" / "market_features_60d.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    original = market_frame()
    original.to_parquet(target, index=False)

    report = run_sanitizer(tmp_path, target, apply=True)
    cleaned = pd.read_parquet(target)
    backup = pd.read_parquet(report["backup_path"])

    assert report["status"] == "ok"
    assert report["reason"] == "sanitized"
    assert report["write_performed"] is True
    assert report["backup_path"]
    assert Path(report["backup_path"]).exists()
    assert lookahead_columns(cleaned) == []
    assert lookahead_columns(backup) == ["future_ret_1", "future_ret_3", "future_ret_5"]


def test_apply_preserves_rows_and_non_lookahead_columns(tmp_path: Path) -> None:
    target = tmp_path / "features" / "market_features_60d.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    original = market_frame()
    original.to_parquet(target, index=False)

    report = run_sanitizer(tmp_path, target, apply=True)
    cleaned = pd.read_parquet(target)

    assert report["rows_before"] == report["rows_after"] == len(original)
    assert report["columns_before_count"] == len(original.columns)
    assert report["columns_after_count"] == len(original.columns) - 3
    for column in [column for column in original.columns if not column.startswith("future_ret_")]:
        assert column in cleaned.columns
        assert cleaned[column].tolist() == original[column].tolist()


def test_clean_file_returns_no_action(tmp_path: Path) -> None:
    target = tmp_path / "features" / "market_features_60d.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    market_frame(contaminated=False).to_parquet(target, index=False)

    report = run_sanitizer(tmp_path, target, apply=True)

    assert report["status"] == "ok"
    assert report["reason"] == "no_action"
    assert report["removed_columns"] == []
    assert report["write_performed"] is False
    assert report["backup_path"] is None


def test_report_contains_hashes_and_metrics(tmp_path: Path) -> None:
    target = tmp_path / "features" / "market_features_60d.parquet"
    report_path = tmp_path / "reports" / "sanitize_market_features_lookahead_report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    market_frame().to_parquet(target, index=False)

    report = run_sanitizer(tmp_path, target, apply=True)
    saved = json.loads(report_path.read_text(encoding="utf-8"))

    for key in [
        "status",
        "reason",
        "dry_run",
        "apply",
        "input_path",
        "backup_path",
        "rows_before",
        "rows_after",
        "columns_before_count",
        "columns_after_count",
        "removed_columns",
        "removed_columns_count",
        "source_hash_before",
        "source_hash_after",
        "write_performed",
        "paper_only",
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "created_at",
    ]:
        assert key in report
        assert key in saved
    assert report["source_hash_before"] != report["source_hash_after"]


def test_origins_do_not_write_future_ret_to_operational_artifact(tmp_path: Path) -> None:
    raw = tmp_path / "raw.parquet"
    output = tmp_path / "market_features_60d.parquet"
    labels = tmp_path / "market_feature_labels.parquet"
    raw_ohlcv().to_parquet(raw, index=False)

    built = build_market_features(raw, output, labels_output_path=labels)
    written = pd.read_parquet(output)
    label_frame = pd.read_parquet(labels)

    assert lookahead_columns(built) == []
    assert lookahead_columns(written) == []
    assert {"future_ret_1", "future_ret_3", "future_ret_5"}.issubset(label_frame.columns)


def test_qlib_refresh_origin_removes_future_ret_from_existing_operational_file(tmp_path: Path) -> None:
    source = tmp_path / "raw.parquet"
    output = tmp_path / "market_features_60d.parquet"
    existing = tmp_path / "market_features_60d.parquet"
    raw_ohlcv(periods=260).to_parquet(source, index=False)
    market_frame(contaminated=True).to_parquet(existing, index=False)

    report = refresh_qlib_market_features(
        source_path=source,
        existing_features_path=existing,
        output_path=output,
        report_path=tmp_path / "reports" / "qlib_market_features_refresh_report.json",
        public_download_enabled=False,
        max_source_age_minutes=10_000,
        now=NOW,
    )
    written = pd.read_parquet(output)

    assert report["status"] == "ok"
    assert report["operational_feature_schema_ok"] is True
    assert "future_ret_1" in report["lookahead_columns_removed"]
    assert lookahead_columns(written) == []


def test_preserves_paper_shadow_only_safety(tmp_path: Path) -> None:
    target = tmp_path / "features" / "market_features_60d.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    market_frame().to_parquet(target, index=False)

    report = run_sanitizer(tmp_path, target)
    text = (ROOT / "scripts" / "sanitize_market_features_lookahead.py").read_text(encoding="utf-8")

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["runtime_mode"] == "paper"
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False
    for forbidden in ["create_order(", "fetch_balance(", "ccxt.", "Freqtrade API", "trades_master", "training_dataset"]:
        assert forbidden not in text
