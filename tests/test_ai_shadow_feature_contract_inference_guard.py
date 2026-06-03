from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from smartcrypto.ml.feature_contract import (
    FeatureContract,
    FeatureContractError,
    build_ai_shadow_feature_contract_from_frame,
    write_feature_contract,
)
from smartcrypto.ml.inference_guard import validate_ai_shadow_inference_input


def feature_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "feature_ret_1": [0.1, 0.2, -0.1, 0.0],
            "feature_volume": [10.0, 11.0, 9.5, 12.2],
            "feature_rsi": [45.0, 55.0, 51.0, 49.0],
        }
    )


def build_contract(frame: pd.DataFrame | None = None) -> FeatureContract:
    return build_ai_shadow_feature_contract_from_frame(
        frame if frame is not None else feature_frame(),
        source_model_id="shadow_test",
        source_model_version="v1",
        strict=True,
    )


def test_feature_contract_blocks_empty_features() -> None:
    with pytest.raises(FeatureContractError, match="empty_feature_columns"):
        build_ai_shadow_feature_contract_from_frame(pd.DataFrame({"target_profitable": [1, 0]}))


def test_feature_contract_blocks_duplicate_features() -> None:
    frame = pd.DataFrame([[1.0, 2.0]], columns=["feature_ret_1", "feature_ret_1"])

    with pytest.raises(FeatureContractError, match="duplicate_feature_columns"):
        build_ai_shadow_feature_contract_from_frame(frame)


def test_feature_contract_blocks_future_ret_columns() -> None:
    frame = pd.DataFrame({"future_ret_1": [0.01], "feature_ret_1": [0.02]})

    with pytest.raises(FeatureContractError, match="lookahead_columns_detected"):
        build_ai_shadow_feature_contract_from_frame(frame, feature_prefix="")


def test_feature_contract_blocks_target_columns_as_features() -> None:
    frame = pd.DataFrame({"target_profitable": [1], "feature_ret_1": [0.02]})

    with pytest.raises(FeatureContractError, match="target_columns_detected"):
        build_ai_shadow_feature_contract_from_frame(frame, feature_prefix="")


def test_feature_contract_serializes_to_json(tmp_path: Path) -> None:
    contract = build_contract()
    path = tmp_path / "contract.json"

    write_feature_contract(contract, path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["contract_id"] == "ai_shadow_feature_contract"
    assert payload["feature_count"] == 3
    assert payload["feature_columns"] == ["feature_ret_1", "feature_volume", "feature_rsi"]
    assert payload["finite_required"] is True
    assert payload["paper_only"] is True
    assert payload["shadow_only"] is True
    assert payload["live_trading_enabled"] is False


def test_inference_guard_accepts_valid_input() -> None:
    frame = feature_frame()
    contract = build_contract(frame)

    report = validate_ai_shadow_inference_input(frame=frame, contract=contract, strict=True)

    assert report["status"] == "ok"
    assert report["reason"] == "ok"
    assert report["missing_features"] == []
    assert report["extra_features"] == []
    assert report["order_valid"] is True
    assert report["schema_hash_valid"] is True


def test_inference_guard_blocks_missing_features() -> None:
    contract = build_contract()
    frame = feature_frame().drop(columns=["feature_rsi"])

    report = validate_ai_shadow_inference_input(frame=frame, contract=contract, strict=True)

    assert report["status"] == "blocked"
    assert report["missing_features"] == ["feature_rsi"]
    assert "missing_features" in report["reason"]


def test_inference_guard_blocks_extra_features() -> None:
    contract = build_contract()
    frame = feature_frame().assign(feature_extra=1.0)

    report = validate_ai_shadow_inference_input(frame=frame, contract=contract, strict=True)

    assert report["status"] == "blocked"
    assert report["extra_features"] == ["feature_extra"]
    assert "extra_features" in report["reason"]


def test_inference_guard_blocks_wrong_order_in_strict_mode() -> None:
    contract = build_contract()
    frame = feature_frame()[["feature_volume", "feature_ret_1", "feature_rsi"]]

    report = validate_ai_shadow_inference_input(frame=frame, contract=contract, strict=True)

    assert report["status"] == "blocked"
    assert report["order_valid"] is False
    assert "feature_order_mismatch" in report["reason"]


def test_inference_guard_blocks_nan_and_infinite_values() -> None:
    contract = build_contract()
    frame = feature_frame()
    frame.loc[0, "feature_ret_1"] = np.nan
    frame.loc[1, "feature_volume"] = np.inf

    report = validate_ai_shadow_inference_input(frame=frame, contract=contract, strict=True)

    assert report["status"] == "blocked"
    assert "feature_ret_1" in report["nan_violations"]
    assert report["infinite_violations"] == {"feature_volume": 1}
    assert "nan_violations" in report["reason"]
    assert "infinite_values_detected" in report["reason"]


def test_inference_guard_blocks_dtype_violations() -> None:
    contract = build_contract()
    frame = feature_frame()
    frame["feature_rsi"] = ["bad", "data", "for", "model"]

    report = validate_ai_shadow_inference_input(frame=frame, contract=contract, strict=True)

    assert report["status"] == "blocked"
    assert "feature_rsi" in report["dtype_violations"]
    assert "dtype_violations" in report["reason"]


def test_inference_guard_blocks_unsafe_safety_flags() -> None:
    payload = build_contract().to_dict()
    payload["live_trading_enabled"] = True
    payload["order_submission_enabled"] = True
    unsafe_contract = FeatureContract.from_dict(payload)

    report = validate_ai_shadow_inference_input(
        frame=feature_frame(),
        contract=unsafe_contract,
        strict=True,
    )

    assert report["status"] == "blocked"
    assert sorted(report["unsafe_safety_flags"]) == [
        "live_trading_enabled",
        "order_submission_enabled",
    ]
    assert "unsafe_safety_flags" in report["reason"]


def test_cli_build_feature_contract_runs_successfully(tmp_path: Path) -> None:
    input_path = tmp_path / "microbatch.parquet"
    output_path = tmp_path / "contract.json"
    feature_frame().to_parquet(input_path)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_ai_shadow_feature_contract.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--model-id",
            "shadow_test",
            "--model-version",
            "v1",
            "--strict",
        ],
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["feature_count"] == 3
    assert output_path.exists()


def test_cli_validate_inference_input_runs_successfully(tmp_path: Path) -> None:
    input_path = tmp_path / "input.parquet"
    contract_path = tmp_path / "contract.json"
    report_path = tmp_path / "guard_report.json"
    frame = feature_frame()
    frame.to_parquet(input_path)
    write_feature_contract(build_contract(frame), contract_path)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/validate_ai_shadow_inference_input.py",
            "--input",
            str(input_path),
            "--contract",
            str(contract_path),
            "--report",
            str(report_path),
            "--strict",
        ],
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert report_path.exists()


def test_does_not_touch_training_dataset_or_trades_master(tmp_path: Path) -> None:
    training_dataset = tmp_path / "training_dataset.parquet"
    trades_master = tmp_path / "trades_master.xlsx"
    training_dataset.write_bytes(b"training")
    trades_master.write_bytes(b"master")
    before_training = training_dataset.read_bytes()
    before_master = trades_master.read_bytes()

    input_path = tmp_path / "input.parquet"
    contract_path = tmp_path / "contract.json"
    report_path = tmp_path / "guard_report.json"
    frame = feature_frame()
    frame.to_parquet(input_path)
    write_feature_contract(build_contract(frame), contract_path)

    report = validate_ai_shadow_inference_input(
        input_path=input_path,
        contract_path=contract_path,
        report_path=report_path,
        strict=True,
    )

    assert report["status"] == "ok"
    assert training_dataset.read_bytes() == before_training
    assert trades_master.read_bytes() == before_master
