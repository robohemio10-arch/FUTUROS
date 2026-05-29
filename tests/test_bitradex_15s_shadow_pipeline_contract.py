from __future__ import annotations

import importlib.util
import json
import py_compile
import sys
from pathlib import Path

import pandas as pd
import pytest


SCRIPT_PATHS = [
    Path("scripts/audit_bitradex_15s_close_only_v5.py"),
    Path("scripts/build_15s_microstructure_shadow_features.py"),
    Path("scripts/join_training_dataset_with_15s_shadow_features.py"),
    Path("scripts/join_training_dataset_with_15s_shadow_features_v2.py"),
]


def load_module(path: Path):
    name = path.stem
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_scripts_exist_compile_and_have_main_entrypoint() -> None:
    for path in SCRIPT_PATHS:
        assert path.exists()
        py_compile.compile(str(path), doraise=True)
        module = load_module(path)
        assert callable(getattr(module, "main", None))
        assert 'if __name__ == "__main__"' in path.read_text(encoding="utf-8")


def test_pipeline_does_not_use_live_order_private_flags_or_env_mutation() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in SCRIPT_PATHS)
    forbidden = [
        "ccxt",
        "create_order",
        "submit_order",
        "fetch_balance",
        "LIVE_ENABLED=true",
        "ORDER_SUBMISSION_ENABLED=true",
        "REAL_ORDER_SUBMISSION_ENABLED=true",
        ".env",
        "dotenv",
    ]
    assert all(token not in text for token in forbidden)


def write_v5_comparison(v4_dir: Path, symbol: str = "BTCUSDT") -> None:
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01T00:00:00Z", periods=4, freq="min"),
            "binance_close": [100.0, 100.1, 100.2, 100.3],
            "bitradex_close": [100.0, 100.11, 100.19, 100.29],
        }
    )
    frame.to_csv(v4_dir / f"{symbol}_complete15s_agg1m_vs_binance1m.csv", index=False)


def test_audit_v5_accepts_tmp_path_and_writes_serializable_report(tmp_path) -> None:
    module = load_module(SCRIPT_PATHS[0])
    v4_dir = tmp_path / "v4"
    out_dir = tmp_path / "reports"
    v4_dir.mkdir()
    write_v5_comparison(v4_dir)

    module.main(
        [
            "--symbols",
            "BTCUSDT",
            "--v4-dir",
            str(v4_dir),
            "--out-dir",
            str(out_dir),
            "--min-rows",
            "1",
        ]
    )

    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["safety"]["shadow_only"] is True
    assert summary["symbols"]["BTCUSDT"]["policy"] == "close_only_microstructure_shadow"
    assert json.dumps(summary, sort_keys=True)
    assert not (tmp_path / "data").exists()


def write_clean_15s(v4_dir: Path, symbol: str = "BTCUSDT") -> None:
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01T00:00:00Z", periods=8, freq="15s"),
            "captured_at": pd.date_range("2026-01-01T00:00:01Z", periods=8, freq="15s"),
            "close": [100.0, 100.1, 100.2, 100.3, 100.4, 100.5, 100.6, 100.7],
        }
    )
    frame.to_csv(v4_dir / f"{symbol}_bitradex_15s_clean_window.csv", index=False)


def write_v5_summary(path: Path, symbol: str = "BTCUSDT") -> None:
    payload = {
        "final_verdict": "approved_close_only_shadow_for_all_symbols",
        "symbols": {symbol: {"status": "approved_close_only_shadow"}},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_build_features_accepts_tmp_path_and_is_close_only_shadow(tmp_path) -> None:
    module = load_module(SCRIPT_PATHS[1])
    v4_dir = tmp_path / "v4"
    feature_dir = tmp_path / "features"
    report_dir = tmp_path / "reports"
    v4_dir.mkdir()
    write_clean_15s(v4_dir)
    v5_summary = tmp_path / "summary.json"
    write_v5_summary(v5_summary)

    module.main(
        [
            "--symbols",
            "BTCUSDT",
            "--v4-dir",
            str(v4_dir),
            "--v5-summary",
            str(v5_summary),
            "--feature-dir",
            str(feature_dir),
            "--report-dir",
            str(report_dir),
            "--start-utc",
            "2026-01-01T00:00:00Z",
            "--min-15s-per-minute",
            "4",
        ]
    )

    summary = json.loads((report_dir / "bitradex_15s_microstructure_shadow_features_summary.json").read_text())
    manifest = pd.read_csv(report_dir / "bitradex_15s_microstructure_shadow_feature_manifest.csv")
    assert summary["safety"]["shadow_only"] is True
    assert summary["policy"]["blocked"] == ["high", "low", "range", "wicks", "ohlc_candle_patterns", "live_execution", "risk_change"]
    micro_columns = manifest[manifest["column"].str.startswith("micro15s_")]["column"]
    assert not micro_columns.str.contains("high|low|range|wick", case=False, regex=True).any()
    assert (feature_dir / "bitradex_15s_microstructure_shadow_features.csv").exists()
    assert not (tmp_path / "data").exists()


def shadow_features_no_overlap() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "feature_minute_utc": ["2026-02-01T00:00:00Z"],
            "join_time_utc": ["2026-02-01T00:01:00Z"],
            "usable_from_utc": ["2026-02-01T00:01:00Z"],
            "timestamp": ["2026-02-01T00:00:45Z"],
            "captured_at": ["2026-02-01T00:00:46Z"],
            "micro15s_close": [100.0],
            "micro15s_ret_15s": [0.001],
            "feature_source": ["bitradex_15s_close_only_shadow"],
            "feature_policy": ["close_returns_micro_momentum_only"],
            "allow_live_execution": [False],
            "allow_shadow_only": [True],
        }
    )


def base_dataset_no_overlap() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": ["t1", "t2"],
            "symbol": ["BTCUSDT", "BTCUSDT"],
            "open_1m_ts": ["2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z"],
            "target_win": [1, 0],
        }
    )


def test_join_v2_accepts_tmp_path_and_matched_zero_is_not_failure(tmp_path) -> None:
    module = load_module(SCRIPT_PATHS[3])
    base_path = tmp_path / "base.csv"
    shadow_path = tmp_path / "shadow.csv"
    base_dataset_no_overlap().to_csv(base_path, index=False)
    shadow_features_no_overlap().to_csv(shadow_path, index=False)
    report_dir = tmp_path / "reports"
    feature_dir = tmp_path / "features"

    module.main(
        [
            "--base",
            str(base_path),
            "--shadow",
            str(shadow_path),
            "--time-col",
            "open_1m_ts",
            "--symbol-col",
            "symbol",
            "--output-parquet",
            str(feature_dir / "joined.parquet"),
            "--output-csv",
            str(feature_dir / "joined.csv"),
            "--matched-csv",
            str(report_dir / "matched.csv"),
            "--unmatched-csv",
            str(report_dir / "unmatched.csv"),
            "--manifest-csv",
            str(report_dir / "manifest.csv"),
            "--summary-json",
            str(report_dir / "summary.json"),
        ]
    )

    summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "ok"
    assert summary["rows"]["matched"] == 0
    assert summary["rows"]["unmatched"] == 2
    assert summary["safety"]["shadow_only"] is True
    assert summary["policy"]["blocked_shadow_features"] == [
        "micro15s_high",
        "micro15s_low",
        "micro15s_range",
        "micro15s_wicks",
        "micro15s_ohlc_patterns",
    ]
    assert not (tmp_path / "data").exists()


def test_forbidden_micro_ohlc_columns_are_rejected() -> None:
    module = load_module(SCRIPT_PATHS[3])
    bad = shadow_features_no_overlap().assign(micro15s_high=101.0, micro15s_range=2.0)

    with pytest.raises(RuntimeError, match="colunas proibidas"):
        module.validate_shadow_features(bad)


def test_close_only_policy_is_documented() -> None:
    doc = Path("docs/BITRADEX_15S_CLOSE_ONLY_SHADOW_PIPELINE.md").read_text(encoding="utf-8").lower()
    assert "close-only shadow" in doc
    assert "matched=0" in doc
    assert "não libera live trading" in doc or "nao libera live trading" in doc
