from __future__ import annotations

import numpy as np
import pandas as pd

from smartcrypto.learning.quality_gated_v5_contract.projection import (
    grouped_feature_null_rates,
)


def test_grouped_feature_null_rates_handles_missing_month_without_categorical_error() -> None:
    features = pd.DataFrame(
        {
            "prior_1m_ret_1": [1.0, np.nan],
            "prior_5m_ret_1": [1.0, 2.0],
        }
    )
    metadata = pd.DataFrame(
        {
            "symbol": ["BTCUSDT", "ETHUSDT"],
            "side": ["long", "short"],
            "open_time_utc": pd.to_datetime(
                ["2026-07-01T00:00:00Z", None],
                errors="coerce",
                utc=True,
            ),
        }
    )
    provenance = pd.DataFrame(
        {
            "provenance_contract": [
                "historical",
                "ocr_v5_20260714",
            ]
        }
    )

    result = grouped_feature_null_rates(
        features,
        metadata,
        provenance,
    )

    assert result["month"]["2026-07"] == {}
    assert result["month"]["<MISSING>"] == {"prior_1m_ret_1": 1.0}
    assert result["tail_v5"] == {"prior_1m_ret_1": 1.0}


def test_grouped_feature_null_rates_is_deterministic_with_multiple_missing_keys() -> None:
    features = pd.DataFrame(
        {
            "prior_1m_ret_1": [np.nan, 1.0, np.nan],
            "prior_5m_ret_1": [1.0, np.nan, 1.0],
        }
    )
    metadata = pd.DataFrame(
        {
            "symbol": ["BTCUSDT", None, "BTCUSDT"],
            "side": ["long", None, "short"],
            "open_time_utc": pd.to_datetime(
                [None, "2026-07-02T00:00:00Z", None],
                errors="coerce",
                utc=True,
            ),
        }
    )
    provenance = pd.DataFrame(
        {
            "provenance_contract": [
                "historical",
                "ocr_v5_20260714",
                "ocr_v5_20260714",
            ]
        }
    )

    first = grouped_feature_null_rates(features, metadata, provenance)
    second = grouped_feature_null_rates(features, metadata, provenance)

    assert first == second
    assert "<MISSING>" in first["month"]
    assert "<MISSING>" in first["symbol"]
    assert "<MISSING>" in first["side"]
