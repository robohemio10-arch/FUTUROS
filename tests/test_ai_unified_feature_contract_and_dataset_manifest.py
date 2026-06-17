from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from smartcrypto.ml.dataset_manifest import (
    build_dataset_file_manifest,
    build_unified_dataset_manifest,
    dataframe_dataset_hash,
)
from smartcrypto.ml.unified_feature_contract import (
    UnifiedFeatureContractError,
    build_contract_from_frame,
)


def valid_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp_utc": pd.to_datetime(
                ["2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z"], utc=True
            ),
            "symbol": ["BTC/USDT:USDT", "BTC/USDT:USDT"],
            "feature_return_1m": [0.01, -0.02],
            "feature_rsi_14": [55.0, 45.0],
        }
    )


def write_csv(path: Path, frame: pd.DataFrame) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path


def test_contract_accepts_valid_dataset() -> None:
    frame = valid_frame()
    contract = build_contract_from_frame(frame, dataset_role="ai_shadow")
    result = contract.validate_frame(frame)
    assert result.status == "ok"
    assert result.feature_columns == ("feature_return_1m", "feature_rsi_14")


def test_contract_blocks_missing_feature() -> None:
    frame = valid_frame()
    contract = build_contract_from_frame(frame, dataset_role="ai_shadow")
    result = contract.validate_frame(frame.drop(columns=["feature_rsi_14"]))
    assert result.status == "blocked"
    assert any("missing_features" in item for item in result.validation_errors)


def test_contract_blocks_extra_feature_in_strict_mode() -> None:
    frame = valid_frame()
    contract = build_contract_from_frame(frame, dataset_role="ai_shadow")
    extra = frame.assign(feature_extra=1.0)
    result = contract.validate_frame(extra)
    assert result.status == "blocked"
    assert any("unexpected_features" in item for item in result.validation_errors)


def test_contract_blocks_feature_order_mismatch() -> None:
    frame = valid_frame()
    contract = build_contract_from_frame(frame, dataset_role="ai_shadow")
    reordered = frame[["timestamp_utc", "symbol", "feature_rsi_14", "feature_return_1m"]]
    result = contract.validate_frame(reordered)
    assert result.status == "blocked"
    assert any("feature_order_mismatch" in item for item in result.validation_errors)


def test_contract_blocks_invalid_dtype() -> None:
    frame = valid_frame()
    contract = build_contract_from_frame(frame, dataset_role="ai_shadow")
    bad = frame.copy()
    bad["feature_rsi_14"] = ["bad", "worse"]
    result = contract.validate_frame(bad)
    assert result.status == "blocked"
    assert any("dtype_invalid:feature_rsi_14" in item for item in result.validation_errors)


def test_contract_blocks_nan() -> None:
    frame = valid_frame()
    contract = build_contract_from_frame(frame, dataset_role="ai_shadow")
    bad = frame.copy()
    bad.loc[0, "feature_rsi_14"] = np.nan
    result = contract.validate_frame(bad)
    assert result.status == "blocked"
    assert any("nan_detected:feature_rsi_14" in item for item in result.validation_errors)


def test_contract_blocks_infinite() -> None:
    frame = valid_frame()
    contract = build_contract_from_frame(frame, dataset_role="ai_shadow")
    bad = frame.copy()
    bad.loc[0, "feature_return_1m"] = np.inf
    result = contract.validate_frame(bad)
    assert result.status == "blocked"
    assert any("infinite_detected:feature_return_1m" in item for item in result.validation_errors)


def test_contract_blocks_range_violation() -> None:
    frame = valid_frame()
    contract = build_contract_from_frame(frame, dataset_role="ai_shadow")
    bad = frame.copy()
    bad.loc[0, "feature_rsi_14"] = 150.0
    result = contract.validate_frame(bad)
    assert result.status == "blocked"
    assert any("range_above_max:feature_rsi_14" in item for item in result.validation_errors)


def test_contract_blocks_future_ret_feature() -> None:
    frame = valid_frame().assign(future_ret_5m=[0.01, 0.02])
    try:
        build_contract_from_frame(frame, dataset_role="ai_shadow")
    except UnifiedFeatureContractError as exc:
        assert "blocked_source_columns" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("future_ret_* column was not blocked")


def test_contract_allows_target_columns_outside_feature_matrix() -> None:
    frame = valid_frame().assign(target_win=[1, 0], pnl_sign_label=[1, -1])
    contract = build_contract_from_frame(frame, dataset_role="ai_shadow")
    assert contract.feature_columns == ("feature_return_1m", "feature_rsi_14")




def test_contract_treats_model_backend_as_context_not_feature() -> None:
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01T00:00:00Z", "2026-01-01T00:05:00Z"], utc=True),
            "symbol": ["BTCUSDT", "ETHUSDT"],
            "prob_up": [0.61, 0.44],
            "score": [0.11, -0.06],
            "predicted_direction": [1, -1],
            "model_backend": ["qlib_lgbm_v1", "qlib_lgbm_v1"],
        }
    )
    contract = build_contract_from_frame(frame, dataset_role="qlib_predictions")
    assert "model_backend" in contract.context_columns
    assert "model_backend" not in contract.feature_columns
    assert contract.feature_columns == ("prob_up", "score", "predicted_direction")
    assert contract.validate_frame(frame).status == "ok"


def test_contract_blocks_target_column_when_explicitly_selected_as_feature() -> None:
    frame = valid_frame().assign(target_win=[1, 0])
    try:
        build_contract_from_frame(
            frame,
            dataset_role="ai_shadow",
            feature_columns=["feature_return_1m", "feature_rsi_14", "target_win"],
        )
    except UnifiedFeatureContractError as exc:
        assert "forbidden_feature_columns" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("target_* feature column was not blocked")


def test_contract_generates_stable_schema_hash() -> None:
    frame = valid_frame()
    first = build_contract_from_frame(frame, dataset_role="ai_shadow")
    second = build_contract_from_frame(frame, dataset_role="ai_shadow")
    assert first.schema_hash == second.schema_hash
    assert first.feature_order_hash == second.feature_order_hash


def test_dataset_manifest_generates_stable_dataset_hash() -> None:
    frame = valid_frame()
    assert dataframe_dataset_hash(frame) == dataframe_dataset_hash(frame.copy())


def test_manifest_detects_empty_dataset(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "empty.csv", pd.DataFrame(columns=valid_frame().columns))
    manifest = build_dataset_file_manifest(path, role="ai_shadow")
    assert "dataset_empty" in manifest.validation_errors


def test_manifest_detects_duplicate_timestamp(tmp_path: Path) -> None:
    frame = valid_frame()
    frame.loc[1, "timestamp_utc"] = frame.loc[0, "timestamp_utc"]
    path = write_csv(tmp_path / "dupe.csv", frame)
    manifest = build_dataset_file_manifest(path, role="ai_shadow")
    assert any("duplicate_timestamp" in item or "duplicate_identity_key" in item for item in manifest.validation_errors)


def test_manifest_preserves_safe_flags(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "valid.csv", valid_frame())
    manifest = build_unified_dataset_manifest({"ai_shadow": path}, strict=True)
    assert manifest.paper_only is True
    assert manifest.sends_orders is False
    assert manifest.changes_risk is False


def test_contract_preserves_safe_flags() -> None:
    contract = build_contract_from_frame(valid_frame(), dataset_role="ai_shadow")
    payload = contract.to_dict()
    assert payload["paper_only"] is True
    assert payload["sends_orders"] is False
    assert payload["changes_risk"] is False


def test_auditor_cli_returns_zero_when_status_ok(tmp_path: Path) -> None:
    market = write_csv(tmp_path / "market.csv", valid_frame())
    predictions = write_csv(tmp_path / "predictions.csv", valid_frame())
    shadow = write_csv(tmp_path / "shadow.csv", valid_frame())
    report_dir = tmp_path / "reports"
    command = [
        sys.executable,
        "scripts/audit_ai_unified_feature_contract.py",
        "--market-features",
        str(market),
        "--qlib-predictions",
        str(predictions),
        "--ai-shadow-dataset",
        str(shadow),
        "--report-dir",
        str(report_dir),
    ]
    completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["status"] == "ok"
    assert (report_dir / "ai_unified_feature_contract.json").exists()
    assert (report_dir / "qlib_feature_contract.json").exists()
    assert (report_dir / "ai_shadow_feature_contract.json").exists()
    assert (report_dir / "ai_unified_dataset_manifest.json").exists()


def test_auditor_cli_returns_one_when_status_blocked(tmp_path: Path) -> None:
    market = write_csv(tmp_path / "market.csv", valid_frame().assign(future_ret_5m=[0.1, 0.2]))
    predictions = write_csv(tmp_path / "predictions.csv", valid_frame())
    shadow = write_csv(tmp_path / "shadow.csv", valid_frame())
    command = [
        sys.executable,
        "scripts/audit_ai_unified_feature_contract.py",
        "--market-features",
        str(market),
        "--qlib-predictions",
        str(predictions),
        "--ai-shadow-dataset",
        str(shadow),
        "--report-dir",
        str(tmp_path / "reports"),
    ]
    completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True)
    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["status"] == "blocked"
    assert "future_ret_5m" in payload["reason"]
