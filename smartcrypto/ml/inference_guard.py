from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.ml.feature_contract import (
    FeatureContract,
    FeatureContractError,
    hash_json,
    load_feature_contract,
    lookahead_columns,
    read_table,
    target_columns,
    utc_timestamp,
)


DEFAULT_REPORT_PATH = Path("data/reports/ai_shadow_inference_guard_report.json")


class InferenceGuardError(ValueError):
    pass


def validate_ai_shadow_inference_input(
    *,
    frame: pd.DataFrame | None = None,
    input_path: str | Path | None = None,
    contract: FeatureContract | None = None,
    contract_path: str | Path | None = None,
    report_path: str | Path | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    if frame is None:
        if input_path is None:
            raise InferenceGuardError("input_required")
        input_file = Path(input_path)
        if not input_file.exists():
            report = blocked_report("missing_input", contract=contract)
            return write_report_if_requested(report, report_path)
        frame = read_table(input_file)
    if contract is None:
        if contract_path is None:
            raise InferenceGuardError("contract_required")
        contract_file = Path(contract_path)
        if not contract_file.exists():
            report = blocked_report("missing_contract", contract=None)
            return write_report_if_requested(report, report_path)
        contract = load_feature_contract(contract_file)

    report = build_guard_report(frame, contract, strict=strict)
    return write_report_if_requested(report, report_path)


def build_guard_report(
    frame: pd.DataFrame,
    contract: FeatureContract,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    if not isinstance(frame, pd.DataFrame):
        raise InferenceGuardError("input_must_be_dataframe")

    expected = list(contract.feature_names)
    columns = [str(column) for column in frame.columns]
    observed_feature_columns = [column for column in columns if column in expected]
    missing_features = [feature for feature in expected if feature not in columns]
    extra_features = [column for column in columns if column not in expected]
    order_valid = observed_feature_columns == expected if contract.strict_order else True
    dtype_violations: dict[str, str] = {}
    nan_violations: dict[str, float] = {}
    infinite_violations: dict[str, int] = {}
    range_violations: dict[str, dict[str, float | None]] = {}

    for feature in expected:
        if feature not in frame.columns:
            continue
        series = frame[feature]
        if isinstance(series, pd.DataFrame):
            dtype_violations[feature] = "not_1d"
            continue
        expected_dtype = contract.expected_dtypes().get(feature, "numeric")
        if expected_dtype == "numeric" and not pd.api.types.is_numeric_dtype(series):
            dtype_violations[feature] = str(series.dtype)
            continue
        numeric = pd.to_numeric(series, errors="coerce")
        missing_ratio = float(numeric.isna().mean()) if len(numeric) else 1.0
        allowed_ratio = contract.allowed_missing_ratio
        if missing_ratio > allowed_ratio:
            nan_violations[feature] = missing_ratio
        values = numeric.dropna().to_numpy(dtype=float, copy=False)
        infinite_count = int(np.isinf(values).sum()) if values.size else 0
        if infinite_count:
            infinite_violations[feature] = infinite_count
        finite = values[np.isfinite(values)] if values.size else values
        limits = contract.ranges.get(feature, {})
        lower = limits.get("min_value")
        upper = limits.get("max_value")
        below = bool(finite.size and lower is not None and float(np.min(finite)) < lower)
        above = bool(finite.size and upper is not None and float(np.max(finite)) > upper)
        if below or above:
            range_violations[feature] = {
                "min_value": lower,
                "max_value": upper,
                "observed_min": float(np.min(finite)) if finite.size else None,
                "observed_max": float(np.max(finite)) if finite.size else None,
            }

    schema_hash_valid = compute_observed_schema_hash(frame, contract) == contract.schema_hash
    found_lookahead = lookahead_columns(columns)
    found_targets = target_columns(columns)
    unsafe = unsafe_safety_flags(contract)
    reasons = collect_reasons(
        missing_features=missing_features,
        extra_features=extra_features,
        order_valid=order_valid,
        dtype_violations=dtype_violations,
        nan_violations=nan_violations,
        infinite_violations=infinite_violations,
        range_violations=range_violations,
        schema_hash_valid=schema_hash_valid,
        lookahead_columns=found_lookahead,
        target_columns=found_targets,
        unsafe_safety_flags=unsafe,
        strict=strict,
    )
    status = "blocked" if reasons else "ok"

    return {
        "status": status,
        "reason": "ok" if not reasons else ";".join(reasons),
        "strict": strict,
        "contract_id": contract.contract_id,
        "contract_version": contract.contract_version,
        "expected_feature_count": len(expected),
        "observed_feature_count": len(observed_feature_columns),
        "missing_features": missing_features,
        "extra_features": extra_features,
        "dtype_violations": dtype_violations,
        "nan_violations": nan_violations,
        "infinite_violations": infinite_violations,
        "range_violations": range_violations,
        "order_valid": order_valid,
        "schema_hash_valid": schema_hash_valid,
        "lookahead_columns": found_lookahead,
        "target_columns": found_targets,
        "unsafe_safety_flags": unsafe,
        "checked_at_utc": utc_timestamp(),
        **contract.safety_flags(),
    }


def collect_reasons(
    *,
    missing_features: list[str],
    extra_features: list[str],
    order_valid: bool,
    dtype_violations: dict[str, str],
    nan_violations: dict[str, float],
    infinite_violations: dict[str, int],
    range_violations: dict[str, dict[str, float | None]],
    schema_hash_valid: bool,
    lookahead_columns: list[str],
    target_columns: list[str],
    unsafe_safety_flags: list[str],
    strict: bool,
) -> list[str]:
    reasons: list[str] = []
    if lookahead_columns:
        reasons.append("lookahead_columns_detected")
    if target_columns:
        reasons.append("target_columns_detected")
    if infinite_violations:
        reasons.append("infinite_values_detected")
    if dtype_violations:
        reasons.append("dtype_violations")
    if unsafe_safety_flags:
        reasons.append("unsafe_safety_flags")
    if strict:
        if missing_features:
            reasons.append("missing_features")
        if extra_features:
            reasons.append("extra_features")
        if not order_valid:
            reasons.append("feature_order_mismatch")
        if nan_violations:
            reasons.append("nan_violations")
        if range_violations:
            reasons.append("range_violations")
        if not schema_hash_valid:
            reasons.append("schema_hash_mismatch")
    return reasons


def compute_observed_schema_hash(frame: pd.DataFrame, contract: FeatureContract) -> str:
    expected = list(contract.feature_names)
    if any(feature not in frame.columns for feature in expected):
        return ""
    return hash_json(
        {
            "feature_columns": expected,
            "dtypes": contract.expected_dtypes(),
            "nullable_policy": contract.expected_nullable_policy(),
            "finite_required": contract.finite_required,
            "allowed_missing_ratio": contract.allowed_missing_ratio,
            "ranges": contract.ranges,
        }
    )


def unsafe_safety_flags(contract: FeatureContract) -> list[str]:
    flags = []
    if not contract.paper_only:
        flags.append("paper_only")
    if not contract.shadow_only:
        flags.append("shadow_only")
    if contract.runtime_mode != "paper":
        flags.append("runtime_mode")
    if contract.live_trading_enabled:
        flags.append("live_trading_enabled")
    if contract.order_submission_enabled:
        flags.append("order_submission_enabled")
    if contract.real_order_submission_enabled:
        flags.append("real_order_submission_enabled")
    if contract.exchange_private_access:
        flags.append("exchange_private_access")
    return flags


def blocked_report(reason: str, *, contract: FeatureContract | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "blocked",
        "reason": reason,
        "contract_id": contract.contract_id if contract else None,
        "contract_version": contract.contract_version if contract else None,
        "expected_feature_count": contract.feature_count if contract else 0,
        "observed_feature_count": 0,
        "missing_features": [],
        "extra_features": [],
        "dtype_violations": {},
        "nan_violations": {},
        "infinite_violations": {},
        "range_violations": {},
        "order_valid": False,
        "schema_hash_valid": False,
        "lookahead_columns": [],
        "target_columns": [],
        "checked_at_utc": utc_timestamp(),
    }
    if contract is not None:
        payload.update(contract.safety_flags())
    else:
        payload.update(
            {
                "paper_only": True,
                "shadow_only": True,
                "runtime_mode": "paper",
                "live_trading_enabled": False,
                "order_submission_enabled": False,
                "real_order_submission_enabled": False,
                "exchange_private_access": False,
            }
        )
    return payload


def write_report_if_requested(
    report: dict[str, Any],
    report_path: str | Path | None,
) -> dict[str, Any]:
    if report_path is not None:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def report_to_exit_code(report: dict[str, Any]) -> int:
    return 0 if report.get("status") == "ok" else 1
