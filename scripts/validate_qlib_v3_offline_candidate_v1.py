#!/usr/bin/env python3
"""No-pytest offline validation for the Qlib V3 source candidate."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from smartcrypto.learning.paper_autolearning import (
    qlib_market_context_economic_challenger as base,
)
from smartcrypto.learning.paper_autolearning.qlib_v3_reproducible_freeze_epoch import (
    SAFETY_FLAGS,
    _model_semantic_fingerprint,
)

EXPECTED_TRAINING_CONTRACT = {
    "loss": "mse",
    "early_stopping_rounds": 20,
    "num_boost_round": 160,
    "learning_rate": 0.03,
    "max_depth": 3,
    "num_leaves": 15,
    "min_data_in_leaf": 25,
    "feature_fraction": 0.80,
    "bagging_fraction": 0.85,
    "bagging_freq": 1,
    "lambda_l1": 0.10,
    "lambda_l2": 0.50,
    "seed": 42,
    "bagging_seed": 42,
    "feature_fraction_seed": 42,
    "data_random_seed": 42,
    "deterministic": True,
    "force_col_wise": True,
    "num_threads": 1,
    "model_validation_fraction": 0.20,
    "min_model_validation_trades": 20,
    "min_model_train_trades": 70,
}


def _frames() -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.DataFrame]:
    index: NDArray[np.float64] = np.arange(160, dtype=np.float64)
    train = pd.DataFrame(
        {
            "f1": np.sin(index[:120] / 7.0),
            "f2": np.cos(index[:120] / 11.0),
            "f3": (index[:120] % 9.0) / 9.0,
        }
    )
    target = pd.Series(
        2.0 * train["f1"].to_numpy()
        - 0.7 * train["f2"].to_numpy()
        + 0.2 * train["f3"].to_numpy(),
        dtype=float,
    )
    calibration = pd.DataFrame(
        {
            "f1": np.sin(index[120:140] / 7.0),
            "f2": np.cos(index[120:140] / 11.0),
            "f3": (index[120:140] % 9.0) / 9.0,
        }
    )
    test = pd.DataFrame(
        {
            "f1": np.sin(index[140:160] / 7.0),
            "f2": np.cos(index[140:160] / 11.0),
            "f3": (index[140:160] % 9.0) / 9.0,
        }
    )
    return train, target, calibration, test


def run() -> dict[str, Any]:
    checks: dict[str, bool] = {}
    contract = base.native_qlib_lgb_training_contract()
    checks["training_contract_exact"] = contract == EXPECTED_TRAINING_CONTRACT

    train_x, train_y, calibration_x, test_x = _frames()
    with base._native_qlib_lgb_trainer_context() as (trainer, metadata):
        result = trainer(
            train_x,
            train_y,
            calibration_x,
            test_x,
            fold_id="offline_v3_trainer",
        )
    checks["native_trainer_model_text_nonempty"] = bool(result.model_text.strip())
    checks["native_trainer_calibration_finite"] = bool(
        np.isfinite(result.calibration_scores).all()
    )
    checks["native_trainer_test_finite"] = bool(np.isfinite(result.test_scores).all())
    semantic = _model_semantic_fingerprint(result.model_text)
    checks["semantic_fingerprint_sha256"] = len(semantic) == 64
    checks["native_framework_qlib"] = metadata.get("framework") == "Microsoft Qlib"

    with base._native_qlib_lgb_predictor_context() as (predictor, _):
        legacy_calibration, legacy_test = predictor(
            train_x,
            train_y,
            calibration_x,
            test_x,
            fold_id="offline_v3_predictor",
        )
    checks["v2_wrapper_calibration_exact"] = bool(
        np.array_equal(result.calibration_scores, legacy_calibration)
    )
    checks["v2_wrapper_test_exact"] = bool(np.array_equal(result.test_scores, legacy_test))

    safety_ok = (
        SAFETY_FLAGS["operational_authority"] is False
        and SAFETY_FLAGS["sends_orders"] is False
        and SAFETY_FLAGS["exchange_private_access"] is False
        and SAFETY_FLAGS["changes_risk"] is False
    )
    checks["safety_contract"] = safety_ok

    passed = all(checks.values())
    return {
        "status": "ok" if passed else "blocked",
        "reason": "offline_v3_source_candidate_validated" if passed else "offline_v3_source_candidate_check_failed",
        "checks": checks,
        "model_semantic_fingerprint": semantic,
        "best_iteration": int(result.best_iteration),
        "calibration_count": len(result.calibration_scores),
        "test_count": len(result.test_scores),
        "v2_regression_semantics_unchanged": bool(
            checks["training_contract_exact"]
            and checks["v2_wrapper_calibration_exact"]
            and checks["v2_wrapper_test_exact"]
        ),
        "operational_authority": False,
        "sends_orders": False,
    }


def main() -> int:
    try:
        report = run()
    except Exception as exc:
        report = {
            "status": "blocked",
            "reason": f"offline_v3_validation_exception:{type(exc).__name__}",
            "operational_authority": False,
            "sends_orders": False,
        }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0 if report.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
