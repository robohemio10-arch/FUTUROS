from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from smartcrypto.ml.feature_contract import FeatureContract, FeatureContractError


def contract() -> FeatureContract:
    return FeatureContract.from_dict(
        {
            "contract_version": "test_v1",
            "strict_order": True,
            "allow_extra_columns": False,
            "allow_nan": False,
            "allow_infinite": False,
            "features": [
                {"name": "ret_1", "dtype": "numeric", "min_value": -1.0, "max_value": 1.0},
                {"name": "volume_rel_30", "dtype": "numeric", "min_value": 0.0},
                {"name": "rsi_14", "dtype": "numeric", "min_value": 0.0, "max_value": 100.0},
            ],
        }
    )


def valid_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ret_1": [0.01, -0.02],
            "volume_rel_30": [1.1, 0.9],
            "rsi_14": [45.0, 55.0],
        }
    )


def test_feature_contract_accepts_valid_dataframe() -> None:
    result = contract().validate(valid_frame())

    assert result.valid
    assert result.errors == []


def test_feature_contract_rejects_missing_column() -> None:
    frame = valid_frame().drop(columns=["rsi_14"])

    result = contract().validate(frame)

    assert not result.valid
    assert "missing_features:['rsi_14']" in result.errors


def test_feature_contract_rejects_wrong_order_when_strict() -> None:
    frame = valid_frame()[["rsi_14", "ret_1", "volume_rel_30"]]

    result = contract().validate(frame)

    assert not result.valid
    assert any(error.startswith("feature_order_mismatch") for error in result.errors)


def test_feature_contract_rejects_nan_when_not_allowed() -> None:
    frame = valid_frame()
    frame.loc[0, "ret_1"] = np.nan

    result = contract().validate(frame)

    assert "feature_contains_nan:ret_1" in result.errors


def test_feature_contract_rejects_infinite() -> None:
    frame = valid_frame()
    frame.loc[0, "volume_rel_30"] = np.inf

    result = contract().validate(frame)

    assert "feature_contains_infinite:volume_rel_30" in result.errors


def test_feature_contract_rejects_non_numeric_type() -> None:
    frame = valid_frame()
    frame["rsi_14"] = ["bad", "data"]

    result = contract().validate(frame)

    assert "feature_not_numeric:rsi_14" in result.errors


def test_feature_contract_assert_valid_raises_clear_error() -> None:
    with pytest.raises(FeatureContractError, match="missing_features"):
        contract().assert_valid(valid_frame().drop(columns=["ret_1"]))
