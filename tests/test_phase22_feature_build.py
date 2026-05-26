from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


MODULE_PATH = Path("scripts/build_phase22_market_features.py")


def load_module():
    spec = importlib.util.spec_from_file_location("build_phase22_market_features", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def duplicate_column_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            [1_700_000_000_000, "BTCUSDT", "BTCUSDT", "1m", 100, 101, 99, 100.5, 10],
            [1_700_000_060_000, "BTCUSDT", "BTCUSDT", "1m", 100.5, 102, 100, 101, 12],
            [1_700_000_120_000, "BTCUSDT", "BTCUSDT", "1m", 101, 103, 100.5, 102, 15],
        ],
        columns=[
            "open_time",
            "symbol",
            "symbol",
            "tf",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ],
    )


def test_normalize_raw_collapses_duplicate_columns_to_1d_series() -> None:
    module = load_module()

    normalized = module.normalize_raw(duplicate_column_frame(), interval="1m")

    assert list(normalized.columns) == module.BASE_COLS
    assert normalized["symbol"].tolist() == ["BTCUSDT", "BTCUSDT", "BTCUSDT"]
    assert normalized["tf"].tolist() == ["1m", "1m", "1m"]
    assert pd.api.types.is_integer_dtype(normalized["ts_ms"])
    assert pd.api.types.is_numeric_dtype(normalized["close"])


def test_normalize_raw_accepts_timestamp_alias_and_symbol_concat() -> None:
    module = load_module()
    btc = pd.DataFrame(
        {
            "timestamp": ["2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z"],
            "symbol": ["BTCUSDT", "BTCUSDT"],
            "open": [100, 101],
            "high": [102, 103],
            "low": [99, 100],
            "close": [101, 102],
            "volume": [10, 11],
        }
    )
    eth = btc.assign(symbol="ETHUSDT", close=[200, 201])

    combined = pd.concat(
        [module.normalize_raw(btc), module.normalize_raw(eth)],
        ignore_index=True,
    )

    assert set(combined["symbol"]) == {"BTCUSDT", "ETHUSDT"}
    assert combined.groupby("symbol")["ts_ms"].nunique().to_dict() == {
        "BTCUSDT": 2,
        "ETHUSDT": 2,
    }


def test_normalize_raw_raises_clear_error_for_missing_required_columns() -> None:
    module = load_module()
    broken = pd.DataFrame({"timestamp": ["2026-01-01T00:00:00Z"], "symbol": ["BTCUSDT"]})

    with pytest.raises(module.Phase22FeatureBuildError, match="missing_required_columns"):
        module.normalize_raw(broken)


def test_build_group_features_returns_expected_feature_columns() -> None:
    module = load_module()
    rows = []
    for idx in range(240):
        rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=idx),
                "symbol": "BTCUSDT",
                "open": 100 + idx,
                "high": 101 + idx,
                "low": 99 + idx,
                "close": 100.5 + idx,
                "volume": 10 + idx,
            }
        )
    normalized = module.normalize_raw(pd.DataFrame(rows))

    features = module.build_group_features(normalized)

    for column in ["ret_1", "future_ret_1", "ema_20", "rsi_14", "atr_pct_14"]:
        assert column in features.columns
    assert len(features) == len(normalized)
