from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.ml.feature_contract import (
    FeatureContract,
    load_feature_contract,
    lookahead_columns,
    read_table,
    target_columns,
)
from smartcrypto.ml.inference_guard import unsafe_safety_flags


OK = "OK"
WARNING = "WARNING"
BLOCKED = "BLOCKED"

STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_BLOCKED = "blocked"

DEFAULT_BASELINE_PATH = Path("data/models/shadow/ai_shadow_drift_baseline.json")
DEFAULT_REPORT_PATH = Path("data/reports/ai_shadow_drift_monitor_report.json")
DEFAULT_CURRENT_PATH = Path("data/features/incremental_training_microbatch.parquet")
DEFAULT_PSI_WARNING = 0.10
DEFAULT_PSI_BLOCKED = 0.25
DEFAULT_KS_WARNING = 0.10
DEFAULT_KS_BLOCKED = 0.25
DEFAULT_MISSING_RATIO_WARNING = 0.05
DEFAULT_MISSING_RATIO_BLOCKED = 0.20


class DriftMonitorError(ValueError):
    pass


@dataclass(frozen=True)
class FeatureDrift:
    feature: str
    psi: float
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DriftReport:
    status: str
    feature_results: list[FeatureDrift]
    warning_threshold: float
    blocked_threshold: float
    checked_at: str = field(default_factory=lambda: utc_timestamp())

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "feature_results": [item.to_dict() for item in self.feature_results],
            "warning_threshold": self.warning_threshold,
            "blocked_threshold": self.blocked_threshold,
            "checked_at": self.checked_at,
            "safety": {
                "order_submission": False,
                "risk_increase": False,
                "bot_block": False,
            },
        }


class DriftMonitor:
    """Legacy PSI facade kept for the existing governance tests."""

    def __init__(
        self,
        *,
        warning_threshold: float = DEFAULT_PSI_WARNING,
        blocked_threshold: float = DEFAULT_PSI_BLOCKED,
        bins: int = 10,
    ) -> None:
        if warning_threshold < 0 or blocked_threshold <= warning_threshold:
            raise DriftMonitorError("invalid_drift_thresholds")
        self.warning_threshold = float(warning_threshold)
        self.blocked_threshold = float(blocked_threshold)
        self.bins = int(bins)

    def compare(
        self,
        baseline: pd.DataFrame,
        current: pd.DataFrame,
        *,
        features: list[str] | tuple[str, ...],
    ) -> DriftReport:
        if not isinstance(baseline, pd.DataFrame) or not isinstance(current, pd.DataFrame):
            raise DriftMonitorError("drift_inputs_must_be_dataframes")
        feature_results: list[FeatureDrift] = []
        for feature in features:
            if feature not in baseline.columns or feature not in current.columns:
                raise DriftMonitorError(f"drift_feature_missing:{feature}")
            psi_value = population_stability_index(baseline[feature], current[feature], self.bins)
            if psi_value >= self.blocked_threshold:
                status = BLOCKED
            elif psi_value >= self.warning_threshold:
                status = WARNING
            else:
                status = OK
            feature_results.append(FeatureDrift(feature=feature, psi=psi_value, status=status))
        overall = OK
        if any(item.status == BLOCKED for item in feature_results):
            overall = BLOCKED
        elif any(item.status == WARNING for item in feature_results):
            overall = WARNING
        return DriftReport(
            status=overall,
            feature_results=feature_results,
            warning_threshold=self.warning_threshold,
            blocked_threshold=self.blocked_threshold,
        )


def build_ai_shadow_drift_baseline(
    *,
    frame: pd.DataFrame | None = None,
    input_path: str | Path | None = None,
    contract: FeatureContract | None = None,
    contract_path: str | Path | None = None,
    output_path: str | Path | None = None,
    feature_prefix: str = "feature_",
    strict: bool = False,
) -> dict[str, Any]:
    if frame is None:
        if input_path is None:
            return blocked_payload("missing_input", output_path=output_path)
        input_file = Path(input_path)
        if not input_file.exists():
            return blocked_payload("missing_input", input_path=input_file, output_path=output_path)
        frame = read_table(input_file)
    if contract is None and contract_path is not None and Path(contract_path).exists():
        contract = load_feature_contract(contract_path)

    feature_columns = resolve_feature_columns(
        frame,
        contract=contract,
        feature_prefix=feature_prefix,
    )
    validation = validate_feature_frame(
        frame,
        feature_columns=feature_columns,
        contract=contract,
        strict=strict,
    )
    if validation["blocking_errors"]:
        payload = blocked_payload(
            ";".join(validation["blocking_errors"]),
            input_path=input_path,
            output_path=output_path,
            contract=contract,
        )
        payload.update(validation)
        write_json_if_requested(payload, output_path)
        return payload

    profiles = {
        feature: build_feature_profile(frame[feature])
        for feature in feature_columns
    }
    created_at = utc_timestamp()
    baseline = {
        "status": STATUS_OK,
        "reason": STATUS_OK,
        "baseline_id": stable_hash(
            {
                "features": feature_columns,
                "created_at_utc": created_at,
                "input_path": str(input_path) if input_path else None,
            }
        )[:16],
        "created_at_utc": created_at,
        "input_path": str(input_path) if input_path else None,
        "output_path": str(output_path) if output_path else None,
        "contract_id": contract.contract_id if contract else None,
        "contract_version": contract.contract_version if contract else None,
        "feature_columns": feature_columns,
        "feature_count": len(feature_columns),
        "feature_profiles": profiles,
        "write_performed": output_path is not None,
        **safety_payload(),
    }
    write_json_if_requested(baseline, output_path)
    return baseline


def run_ai_shadow_drift_monitor(
    *,
    baseline: dict[str, Any] | None = None,
    baseline_path: str | Path | None = DEFAULT_BASELINE_PATH,
    current_frame: pd.DataFrame | None = None,
    current_path: str | Path | None = DEFAULT_CURRENT_PATH,
    contract: FeatureContract | None = None,
    contract_path: str | Path | None = None,
    report_path: str | Path | None = DEFAULT_REPORT_PATH,
    psi_warning: float = DEFAULT_PSI_WARNING,
    psi_blocked: float = DEFAULT_PSI_BLOCKED,
    ks_warning: float = DEFAULT_KS_WARNING,
    ks_blocked: float = DEFAULT_KS_BLOCKED,
    missing_ratio_warning: float = DEFAULT_MISSING_RATIO_WARNING,
    missing_ratio_blocked: float = DEFAULT_MISSING_RATIO_BLOCKED,
    strict: bool = False,
    sends_orders: bool = False,
    changes_risk: bool = False,
) -> dict[str, Any]:
    if baseline is None:
        if baseline_path is None or not Path(baseline_path).exists():
            report = blocked_payload(
                "missing_baseline",
                baseline_path=baseline_path,
                current_path=current_path,
                report_path=report_path,
            )
            write_json_if_requested(report, report_path)
            return report
        baseline = load_json(Path(baseline_path))
    if current_frame is None:
        if current_path is None or not Path(current_path).exists():
            report = blocked_payload(
                "missing_current",
                baseline_path=baseline_path,
                current_path=current_path,
                report_path=report_path,
            )
            write_json_if_requested(report, report_path)
            return report
        current_frame = read_table(current_path)
    if contract is None and contract_path is not None and Path(contract_path).exists():
        contract = load_feature_contract(contract_path)

    feature_columns = list(baseline.get("feature_columns") or [])
    if contract is not None:
        feature_columns = list(contract.feature_columns)
    validation = validate_feature_frame(
        current_frame,
        feature_columns=feature_columns,
        contract=contract,
        strict=strict,
        sends_orders=sends_orders,
        changes_risk=changes_risk,
    )
    baseline_validation = validate_baseline_payload(baseline, feature_columns)
    blocking_errors = baseline_validation + validation["blocking_errors"]
    if blocking_errors:
        report = blocked_payload(
            ";".join(blocking_errors),
            baseline_path=baseline_path,
            current_path=current_path,
            report_path=report_path,
            contract=contract,
        )
        report.update(validation)
        report["feature_results"] = []
        write_json_if_requested(report, report_path)
        return report

    feature_results = []
    any_warning = False
    any_blocked = False
    for feature in feature_columns:
        baseline_profile = baseline["feature_profiles"][feature]
        current_series = current_frame[feature]
        current_profile = build_feature_profile(current_series)
        psi = population_stability_index(
            pd.Series(baseline_profile["values"]),
            current_series,
        )
        ks_stat = ks_statistic(pd.Series(baseline_profile["values"]), current_series)
        drift_status = STATUS_OK
        drift_reasons: list[str] = []
        if psi >= psi_blocked:
            drift_status = STATUS_BLOCKED
            drift_reasons.append("psi_blocked")
        elif psi >= psi_warning:
            drift_status = STATUS_WARNING
            drift_reasons.append("psi_warning")
        if ks_stat >= ks_blocked:
            drift_status = STATUS_BLOCKED
            drift_reasons.append("ks_blocked")
        elif drift_status != STATUS_BLOCKED and ks_stat >= ks_warning:
            drift_status = STATUS_WARNING
            drift_reasons.append("ks_warning")
        if current_profile["missing_ratio"] >= missing_ratio_blocked:
            drift_status = STATUS_BLOCKED
            drift_reasons.append("missing_ratio_blocked")
        elif (
            drift_status != STATUS_BLOCKED
            and current_profile["missing_ratio"] >= missing_ratio_warning
        ):
            drift_status = STATUS_WARNING
            drift_reasons.append("missing_ratio_warning")

        any_warning = any_warning or drift_status == STATUS_WARNING
        any_blocked = any_blocked or drift_status == STATUS_BLOCKED
        feature_results.append(
            {
                "feature": feature,
                "psi": psi,
                "ks_statistic": ks_stat,
                "baseline_count": baseline_profile["count"],
                "current_count": current_profile["count"],
                "baseline_missing_ratio": baseline_profile["missing_ratio"],
                "current_missing_ratio": current_profile["missing_ratio"],
                "baseline_mean": baseline_profile["mean"],
                "current_mean": current_profile["mean"],
                "baseline_std": baseline_profile["std"],
                "current_std": current_profile["std"],
                "drift_status": drift_status,
                "drift_reason": "ok" if not drift_reasons else ";".join(drift_reasons),
            }
        )

    status = STATUS_BLOCKED if any_blocked else STATUS_WARNING if any_warning else STATUS_OK
    report = {
        "status": status,
        "reason": status,
        "baseline_path": str(baseline_path) if baseline_path else None,
        "current_path": str(current_path) if current_path else None,
        "report_path": str(report_path) if report_path else None,
        "contract_id": contract.contract_id if contract else baseline.get("contract_id"),
        "contract_version": (
            contract.contract_version if contract else baseline.get("contract_version")
        ),
        "feature_count": len(feature_columns),
        "feature_columns": feature_columns,
        "feature_results": feature_results,
        "psi_warning": psi_warning,
        "psi_blocked": psi_blocked,
        "ks_warning": ks_warning,
        "ks_blocked": ks_blocked,
        "missing_ratio_warning": missing_ratio_warning,
        "missing_ratio_blocked": missing_ratio_blocked,
        "checked_at_utc": utc_timestamp(),
        "promotion_gate_effect": "promotion_blocked" if status == STATUS_BLOCKED else "none",
        "registry_updated": False,
        "model_updated": False,
        "signal_producer_updated": False,
        **validation,
        **safety_payload(sends_orders=sends_orders, changes_risk=changes_risk),
    }
    write_json_if_requested(report, report_path)
    return report


def resolve_feature_columns(
    frame: pd.DataFrame,
    *,
    contract: FeatureContract | None = None,
    feature_prefix: str = "feature_",
) -> list[str]:
    if contract is not None:
        return list(contract.feature_columns)
    columns = [str(column) for column in frame.columns]
    features = [
        column
        for column in columns
        if (not feature_prefix or column.startswith(feature_prefix))
        and not column.endswith("_utc")
        and not column.endswith("_ts")
        and column not in {"feature_timestamp_utc", "feature_age_seconds"}
    ]
    return features


def validate_feature_frame(
    frame: pd.DataFrame,
    *,
    feature_columns: list[str],
    contract: FeatureContract | None = None,
    strict: bool = False,
    sends_orders: bool = False,
    changes_risk: bool = False,
) -> dict[str, Any]:
    columns = [str(column) for column in frame.columns]
    blocking_errors: list[str] = []
    if not feature_columns:
        blocking_errors.append("empty_features")

    blocked_lookahead = lookahead_columns(columns)
    blocked_targets = target_columns(feature_columns)
    if blocked_lookahead:
        blocking_errors.append("lookahead_columns_detected")
    if blocked_targets:
        blocking_errors.append("target_columns_detected")

    missing_features = [feature for feature in feature_columns if feature not in frame.columns]
    if missing_features:
        blocking_errors.append("missing_contract_features")

    dtype_violations: dict[str, str] = {}
    nan_violations: dict[str, float] = {}
    infinite_violations: dict[str, int] = {}
    for feature in feature_columns:
        if feature not in frame.columns:
            continue
        series = frame[feature]
        if isinstance(series, pd.DataFrame):
            dtype_violations[feature] = "not_1d"
            continue
        if not pd.api.types.is_numeric_dtype(series):
            dtype_violations[feature] = str(series.dtype)
            continue
        numeric = pd.to_numeric(series, errors="coerce")
        missing_ratio = float(numeric.isna().mean()) if len(numeric) else 1.0
        allowed_ratio = contract.allowed_missing_ratio if contract else 0.0
        if missing_ratio > allowed_ratio:
            nan_violations[feature] = missing_ratio
        values = numeric.dropna().to_numpy(dtype=float, copy=False)
        infinite_count = int(np.isinf(values).sum()) if values.size else 0
        if infinite_count:
            infinite_violations[feature] = infinite_count
    if dtype_violations:
        blocking_errors.append("dtype_violations")
    if infinite_violations:
        blocking_errors.append("infinite_values_detected")
    if strict and nan_violations:
        blocking_errors.append("nan_violations")

    unsafe_flags = unsafe_safety_flags(contract) if contract else []
    if sends_orders:
        unsafe_flags.append("sends_orders")
    if changes_risk:
        unsafe_flags.append("changes_risk")
    if unsafe_flags:
        blocking_errors.append("unsafe_safety_flags")

    return {
        "blocking_errors": blocking_errors,
        "missing_features": missing_features,
        "lookahead_columns": blocked_lookahead,
        "target_columns": blocked_targets,
        "dtype_violations": dtype_violations,
        "nan_violations": nan_violations,
        "infinite_violations": infinite_violations,
        "unsafe_safety_flags": sorted(set(unsafe_flags)),
    }


def validate_baseline_payload(baseline: dict[str, Any], feature_columns: list[str]) -> list[str]:
    errors: list[str] = []
    profiles = baseline.get("feature_profiles")
    if not isinstance(profiles, dict):
        errors.append("invalid_baseline")
        return errors
    if not feature_columns:
        errors.append("empty_features")
    for feature in feature_columns:
        if feature not in profiles:
            errors.append(f"baseline_feature_missing:{feature}")
    return errors


def build_feature_profile(series: pd.Series) -> dict[str, Any]:
    numeric = pd.to_numeric(series, errors="coerce")
    total = int(len(numeric))
    missing_ratio = float(numeric.isna().mean()) if total else 1.0
    finite = numeric.replace([np.inf, -np.inf], np.nan).dropna()
    values = [float(value) for value in finite.to_list()]
    return {
        "count": int(len(finite)),
        "total_count": total,
        "missing_ratio": missing_ratio,
        "mean": float(finite.mean()) if len(finite) else None,
        "std": float(finite.std(ddof=0)) if len(finite) else None,
        "min": float(finite.min()) if len(finite) else None,
        "max": float(finite.max()) if len(finite) else None,
        "values": values,
    }


def population_stability_index(
    baseline: pd.Series,
    current: pd.Series,
    bins: int = 10,
) -> float:
    base = pd.to_numeric(baseline, errors="coerce").replace([np.inf, -np.inf], np.nan)
    curr = pd.to_numeric(current, errors="coerce").replace([np.inf, -np.inf], np.nan)
    base_values = base.dropna().to_numpy(dtype=float)
    curr_values = curr.dropna().to_numpy(dtype=float)
    if base_values.size == 0 or curr_values.size == 0:
        raise DriftMonitorError("drift_feature_has_no_numeric_values")
    quantiles = np.linspace(0, 1, max(2, bins + 1))
    edges = np.unique(np.quantile(base_values, quantiles))
    if edges.size < 2:
        edges = np.array([base_values.min() - 0.5, base_values.max() + 0.5], dtype=float)
    edges[0] = -np.inf
    edges[-1] = np.inf
    base_counts, _ = np.histogram(base_values, bins=edges)
    curr_counts, _ = np.histogram(curr_values, bins=edges)
    base_pct = np.clip(base_counts / max(base_counts.sum(), 1), 1e-6, None)
    curr_pct = np.clip(curr_counts / max(curr_counts.sum(), 1), 1e-6, None)
    return float(np.sum((curr_pct - base_pct) * np.log(curr_pct / base_pct)))


def ks_statistic(baseline: pd.Series, current: pd.Series) -> float:
    base = pd.to_numeric(baseline, errors="coerce").replace([np.inf, -np.inf], np.nan)
    curr = pd.to_numeric(current, errors="coerce").replace([np.inf, -np.inf], np.nan)
    base_values = np.sort(base.dropna().to_numpy(dtype=float))
    curr_values = np.sort(curr.dropna().to_numpy(dtype=float))
    if base_values.size == 0 or curr_values.size == 0:
        raise DriftMonitorError("drift_feature_has_no_numeric_values")
    points = np.sort(np.unique(np.concatenate([base_values, curr_values])))
    base_cdf = np.searchsorted(base_values, points, side="right") / base_values.size
    curr_cdf = np.searchsorted(curr_values, points, side="right") / curr_values.size
    return float(np.max(np.abs(base_cdf - curr_cdf)))


def blocked_payload(
    reason: str,
    *,
    input_path: str | Path | None = None,
    output_path: str | Path | None = None,
    baseline_path: str | Path | None = None,
    current_path: str | Path | None = None,
    report_path: str | Path | None = None,
    contract: FeatureContract | None = None,
) -> dict[str, Any]:
    return {
        "status": STATUS_BLOCKED,
        "reason": reason,
        "input_path": str(input_path) if input_path else None,
        "output_path": str(output_path) if output_path else None,
        "baseline_path": str(baseline_path) if baseline_path else None,
        "current_path": str(current_path) if current_path else None,
        "report_path": str(report_path) if report_path else None,
        "contract_id": contract.contract_id if contract else None,
        "contract_version": contract.contract_version if contract else None,
        "feature_count": 0,
        "feature_columns": [],
        "feature_results": [],
        "write_performed": False,
        "checked_at_utc": utc_timestamp(),
        **safety_payload(),
    }


def safety_payload(*, sends_orders: bool = False, changes_risk: bool = False) -> dict[str, Any]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "runtime_mode": "paper",
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": sends_orders,
        "changes_risk": changes_risk,
    }


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_if_requested(payload: dict[str, Any], path: str | Path | None) -> None:
    if path is None:
        return
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def stable_hash(payload: dict[str, Any]) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
