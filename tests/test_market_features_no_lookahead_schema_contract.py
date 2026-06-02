from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from smartcrypto.data.feature_builder import build_market_features
from smartcrypto.market.market_feature_schema import (
    lookahead_columns,
    sanitize_operational_market_features,
)
from smartcrypto.qlib_engine.market_features_refresh import refresh_qlib_market_features
from smartcrypto.qlib_engine.paper_refresh_supervisor import (
    MARKET_FEATURES_FAILED,
    PaperRefreshSupervisorConfig,
    run_paper_refresh_supervisor,
)


NOW = datetime(2026, 5, 28, 17, 30, tzinfo=timezone.utc)


def raw_frame(ts_end: datetime = NOW, periods: int = 260) -> pd.DataFrame:
    rows = []
    for symbol, base in [("BTCUSDT", 94000.0), ("ETHUSDT", 3500.0)]:
        for idx in range(periods):
            ts = ts_end - timedelta(minutes=5 * (periods - idx - 1))
            close = base + idx * 0.5
            rows.append(
                {
                    "symbol": symbol,
                    "pair": symbol.replace("USDT", "/USDT:USDT"),
                    "tf": "5m",
                    "ts": ts,
                    "open": close - 1,
                    "high": close + 2,
                    "low": close - 2,
                    "close": close,
                    "volume": 100 + idx,
                }
            )
    return pd.DataFrame(rows)


def load_inspector():
    spec = importlib.util.spec_from_file_location(
        "inspect_phase22_outputs",
        Path("scripts/inspect_phase22_outputs.py"),
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_contract_removes_lookahead_and_can_write_explicit_label_artifact(tmp_path: Path) -> None:
    frame = raw_frame(periods=5).assign(future_ret_1=0.01, future_ret_3=0.02)
    labels_path = tmp_path / "market_feature_labels.parquet"

    sanitized, report = sanitize_operational_market_features(
        frame,
        labels_output_path=labels_path,
    )
    labels = pd.read_parquet(labels_path)

    assert lookahead_columns(sanitized) == []
    assert report["operational_feature_schema_ok"] is True
    assert report["lookahead_columns_removed"] == ["future_ret_1", "future_ret_3"]
    assert {"future_ret_1", "future_ret_3"}.issubset(labels.columns)


def test_feature_builder_writes_operational_features_without_future_ret(tmp_path: Path) -> None:
    raw = tmp_path / "raw.parquet"
    output = tmp_path / "market_features_60d.parquet"
    labels = tmp_path / "market_feature_labels.parquet"
    raw_frame().to_parquet(raw, index=False)

    features = build_market_features(raw, output, labels_output_path=labels)
    written = pd.read_parquet(output)
    label_frame = pd.read_parquet(labels)

    assert lookahead_columns(features) == []
    assert lookahead_columns(written) == []
    assert {"future_ret_1", "future_ret_3", "future_ret_5"}.issubset(label_frame.columns)


def test_qlib_market_refresh_removes_lookahead_from_existing_operational_file(tmp_path: Path) -> None:
    source = tmp_path / "raw.parquet"
    existing = tmp_path / "market_features_60d.parquet"
    output = tmp_path / "market_features_60d.parquet"
    raw_frame().to_parquet(source, index=False)
    existing_frame = raw_frame(periods=5).assign(future_ret_1=0.01)
    existing_frame.to_parquet(existing, index=False)

    report = refresh_qlib_market_features(
        source_path=source,
        existing_features_path=existing,
        output_path=output,
        report_path=tmp_path / "report.json",
        public_download_enabled=False,
        max_source_age_minutes=15,
        now=NOW,
    )
    written = pd.read_parquet(output)

    assert report["status"] == "ok"
    assert report["operational_feature_schema_ok"] is True
    assert report["lookahead_columns_removed"] == ["future_ret_1"]
    assert lookahead_columns(written) == []


def test_inspect_phase22_table_info_detects_operational_lookahead_columns(tmp_path: Path) -> None:
    module = load_inspector()
    path = tmp_path / "market_features_60d.parquet"
    raw_frame(periods=3).assign(future_ret_1=0.01).to_parquet(path, index=False)

    info = module.table_info(path, operational=True)

    assert info["status"] == "warning"
    assert info["operational_feature_schema_ok"] is False
    assert info["lookahead_columns"] == ["future_ret_1"]
    assert info["lookahead_columns_count"] == 1


def test_supervisor_blocks_when_market_refresh_reports_operational_schema_invalid(tmp_path: Path) -> None:
    calls = {"predictions": 0}

    def contaminated_market_report(**kwargs):
        return {
            "status": "ok",
            "operational_feature_schema_ok": False,
            "lookahead_columns": ["future_ret_1"],
        }

    def predictions_called(**kwargs):
        calls["predictions"] += 1
        return {"status": "ok"}

    report = run_paper_refresh_supervisor(
        PaperRefreshSupervisorConfig(
            report_path=tmp_path / "supervisor.json",
            public_download_enabled=False,
        ),
        market_refresh_fn=contaminated_market_report,
        prediction_refresh_fn=predictions_called,
        phase13_fn=lambda **kwargs: {"status": "ok"},
        freshness_fn=lambda *args, **kwargs: {"freshness_status": "fresh", "stale": False},
        signal_inspect_fn=lambda path: {"exists": True},
    )

    assert report["status"] == MARKET_FEATURES_FAILED
    assert report["reason"] == "operational_feature_schema_invalid"
    assert calls["predictions"] == 0


def test_phase5_rebuild_scripts_do_not_depend_on_future_ret_columns() -> None:
    text = "\n".join(
        [
            Path("scripts/build_trade_enriched.py").read_text(encoding="utf-8"),
            Path("scripts/rebuild_phase5_datasets.py").read_text(encoding="utf-8"),
            Path("scripts/inspect_phase5_outputs.py").read_text(encoding="utf-8"),
        ]
    )

    assert "future_ret_" not in text
