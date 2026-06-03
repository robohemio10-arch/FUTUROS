from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from smartcrypto.ml.drift_monitor import (
    build_ai_shadow_drift_baseline,
    run_ai_shadow_drift_monitor,
)
from smartcrypto.ml.feature_contract import (
    FeatureContract,
    build_ai_shadow_feature_contract_from_frame,
    write_feature_contract,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def stable_frame(rows: int = 100) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "feature_ret_1": np.linspace(0.0, 1.0, rows),
            "feature_volume": np.linspace(10.0, 20.0, rows),
        }
    )


def contract_for(frame: pd.DataFrame | None = None) -> FeatureContract:
    return build_ai_shadow_feature_contract_from_frame(frame if frame is not None else stable_frame())


def test_drift_monitor_blocks_missing_baseline(tmp_path: Path) -> None:
    current_path = tmp_path / "current.parquet"
    stable_frame().to_parquet(current_path)

    report = run_ai_shadow_drift_monitor(
        baseline_path=tmp_path / "missing.json",
        current_path=current_path,
        report_path=tmp_path / "report.json",
        strict=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_baseline"


def test_drift_monitor_blocks_missing_current(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    build_ai_shadow_drift_baseline(
        frame=stable_frame(),
        output_path=baseline_path,
    )

    report = run_ai_shadow_drift_monitor(
        baseline_path=baseline_path,
        current_path=tmp_path / "missing.parquet",
        report_path=tmp_path / "report.json",
        strict=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_current"


def test_drift_monitor_blocks_empty_features() -> None:
    report = build_ai_shadow_drift_baseline(frame=pd.DataFrame({"target_profitable": [1, 0]}))

    assert report["status"] == "blocked"
    assert "empty_features" in report["reason"]


def test_drift_monitor_blocks_future_ret_columns() -> None:
    frame = pd.DataFrame({"future_ret_1": [0.1, 0.2], "feature_ret_1": [0.1, 0.2]})

    report = build_ai_shadow_drift_baseline(frame=frame, feature_prefix="")

    assert report["status"] == "blocked"
    assert "lookahead_columns_detected" in report["reason"]
    assert report["lookahead_columns"] == ["future_ret_1"]


def test_drift_monitor_blocks_target_columns_as_features() -> None:
    frame = pd.DataFrame({"target_profitable": [1, 0], "feature_ret_1": [0.1, 0.2]})

    report = build_ai_shadow_drift_baseline(frame=frame, feature_prefix="")

    assert report["status"] == "blocked"
    assert "target_columns_detected" in report["reason"]
    assert report["target_columns"] == ["target_profitable"]


def test_drift_monitor_accepts_stable_distribution() -> None:
    baseline = build_ai_shadow_drift_baseline(frame=stable_frame())

    report = run_ai_shadow_drift_monitor(
        baseline=baseline,
        current_frame=stable_frame(),
        report_path=None,
        strict=True,
    )

    assert report["status"] == "ok"
    assert all(item["drift_status"] == "ok" for item in report["feature_results"])
    assert report["paper_only"] is True
    assert report["order_submission_enabled"] is False


def test_drift_monitor_warns_on_moderate_drift() -> None:
    baseline = build_ai_shadow_drift_baseline(frame=stable_frame())
    current = stable_frame()
    current["feature_ret_1"] = current["feature_ret_1"] + 0.08

    report = run_ai_shadow_drift_monitor(
        baseline=baseline,
        current_frame=current,
        report_path=None,
        psi_warning=100.0,
        psi_blocked=200.0,
        ks_warning=0.05,
        ks_blocked=0.90,
        strict=True,
    )

    assert report["status"] == "warning"
    assert any(item["drift_status"] == "warning" for item in report["feature_results"])


def test_drift_monitor_blocks_on_critical_drift() -> None:
    baseline = build_ai_shadow_drift_baseline(frame=stable_frame())
    current = stable_frame()
    current["feature_ret_1"] = current["feature_ret_1"] + 10.0

    report = run_ai_shadow_drift_monitor(
        baseline=baseline,
        current_frame=current,
        report_path=None,
        psi_warning=0.10,
        psi_blocked=0.25,
        ks_warning=0.10,
        ks_blocked=0.25,
        strict=True,
    )

    assert report["status"] == "blocked"
    assert any(item["drift_status"] == "blocked" for item in report["feature_results"])
    assert report["registry_updated"] is False


def test_drift_monitor_blocks_missing_contract_features() -> None:
    frame = stable_frame()
    contract = contract_for(frame)
    baseline = build_ai_shadow_drift_baseline(frame=frame, contract=contract)
    current = frame.drop(columns=["feature_volume"])

    report = run_ai_shadow_drift_monitor(
        baseline=baseline,
        current_frame=current,
        contract=contract,
        report_path=None,
        strict=True,
    )

    assert report["status"] == "blocked"
    assert "missing_contract_features" in report["reason"]
    assert report["missing_features"] == ["feature_volume"]


def test_drift_monitor_blocks_nan_and_infinite_values() -> None:
    baseline = build_ai_shadow_drift_baseline(frame=stable_frame())
    current = stable_frame()
    current.loc[0, "feature_ret_1"] = np.nan
    current.loc[1, "feature_volume"] = np.inf

    report = run_ai_shadow_drift_monitor(
        baseline=baseline,
        current_frame=current,
        report_path=None,
        strict=True,
    )

    assert report["status"] == "blocked"
    assert "nan_violations" in report["reason"]
    assert "infinite_values_detected" in report["reason"]


def test_drift_monitor_blocks_unsafe_safety_flags() -> None:
    payload = contract_for().to_dict()
    payload["live_trading_enabled"] = True
    unsafe_contract = FeatureContract.from_dict(payload)
    baseline = build_ai_shadow_drift_baseline(frame=stable_frame(), contract=contract_for())

    report = run_ai_shadow_drift_monitor(
        baseline=baseline,
        current_frame=stable_frame(),
        contract=unsafe_contract,
        report_path=None,
        strict=True,
    )

    assert report["status"] == "blocked"
    assert "unsafe_safety_flags" in report["reason"]
    assert report["unsafe_safety_flags"] == ["live_trading_enabled"]


def test_drift_baseline_serializes_to_json(tmp_path: Path) -> None:
    output_path = tmp_path / "baseline.json"

    report = build_ai_shadow_drift_baseline(frame=stable_frame(), output_path=output_path)
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert report["status"] == "ok"
    assert payload["feature_count"] == 2
    assert payload["feature_profiles"]["feature_ret_1"]["count"] == 100
    assert payload["paper_only"] is True


def test_cli_build_drift_baseline_runs_successfully(tmp_path: Path) -> None:
    input_path = tmp_path / "input.parquet"
    output_path = tmp_path / "baseline.json"
    stable_frame().to_parquet(input_path)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_ai_shadow_drift_baseline.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--strict",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert output_path.exists()


def test_cli_run_drift_monitor_runs_successfully(tmp_path: Path) -> None:
    current_path = tmp_path / "current.parquet"
    baseline_path = tmp_path / "baseline.json"
    report_path = tmp_path / "report.json"
    contract_path = tmp_path / "contract.json"
    frame = stable_frame()
    frame.to_parquet(current_path)
    write_feature_contract(contract_for(frame), contract_path)
    build_ai_shadow_drift_baseline(
        frame=frame,
        contract_path=contract_path,
        output_path=baseline_path,
        strict=True,
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_ai_shadow_drift_monitor.py",
            "--baseline",
            str(baseline_path),
            "--current",
            str(current_path),
            "--contract",
            str(contract_path),
            "--report",
            str(report_path),
            "--strict",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
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

    baseline = build_ai_shadow_drift_baseline(frame=stable_frame())
    report = run_ai_shadow_drift_monitor(
        baseline=baseline,
        current_frame=stable_frame(),
        report_path=tmp_path / "report.json",
        strict=True,
    )

    assert report["status"] == "ok"
    assert training_dataset.read_bytes() == b"training"
    assert trades_master.read_bytes() == b"master"


def test_feature_contract_documentation_exists() -> None:
    doc_path = REPO_ROOT / "docs" / "AI_SHADOW_FEATURE_CONTRACT_AND_INFERENCE_GUARD.md"

    assert doc_path.exists()
    content = doc_path.read_text(encoding="utf-8")
    assert "FeatureContract" in content
    assert "InferenceGuard" in content
