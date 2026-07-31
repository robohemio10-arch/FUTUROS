"""Calibration and expected-value drift overlay for the existing drift monitor."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def build_drift_overlay(
    current_calibration: Mapping[str, Any],
    *,
    baseline_calibration: Mapping[str, Any] | None,
    upstream_drift_report_path: Path,
    max_brier_degradation: float,
    max_ece_degradation: float,
    max_expected_value_degradation: float,
) -> dict[str, Any]:
    upstream = _load_json_object(upstream_drift_report_path)
    upstream_summary = _upstream_summary(upstream)
    if baseline_calibration is None:
        return {
            "status": "insufficient_data",
            "calibration_drift_status": "insufficient_data",
            "expected_value_drift_status": "insufficient_data",
            "calibration_drift": {},
            "expected_value_drift": {},
            "upstream_drift": upstream_summary,
            "blockers": [],
            "warnings": ["baseline_calibration_missing"],
        }
    calibration_drift: dict[str, Any] = {}
    expected_value_drift: dict[str, Any] = {}
    blockers: list[str] = []
    warnings: list[str] = []
    for model_key in ("qlib_ranker", "ai_shadow_veto", "ensemble"):
        current_model = _mapping(current_calibration.get(model_key))
        baseline_model = _mapping(baseline_calibration.get(model_key))
        if not current_model or not baseline_model:
            warnings.append(f"calibration_baseline_section_missing:{model_key}")
            continue
        brier_delta = _delta(
            current_model.get("brier_score"),
            baseline_model.get("brier_score"),
        )
        ece_delta = _delta(
            current_model.get("expected_calibration_error"),
            baseline_model.get("expected_calibration_error"),
        )
        ev_delta = _delta(
            current_model.get("overall_expected_value"),
            baseline_model.get("overall_expected_value"),
        )
        calibration_drift[model_key] = {
            "current_brier_score": current_model.get("brier_score"),
            "baseline_brier_score": baseline_model.get("brier_score"),
            "brier_degradation": brier_delta,
            "current_expected_calibration_error": current_model.get(
                "expected_calibration_error"
            ),
            "baseline_expected_calibration_error": baseline_model.get(
                "expected_calibration_error"
            ),
            "ece_degradation": ece_delta,
        }
        expected_value_drift[model_key] = {
            "current_expected_value": current_model.get("overall_expected_value"),
            "baseline_expected_value": baseline_model.get("overall_expected_value"),
            "expected_value_degradation": None if ev_delta is None else round(-ev_delta, 12),
        }
        if brier_delta is not None and brier_delta > max_brier_degradation:
            blockers.append(f"brier_score_drift_critical:{model_key}")
        if ece_delta is not None and ece_delta > max_ece_degradation:
            blockers.append(f"calibration_error_drift_critical:{model_key}")
        if ev_delta is not None and -ev_delta > max_expected_value_degradation:
            blockers.append(f"expected_value_drift_critical:{model_key}")
    status = "blocked" if blockers else ("warning" if warnings else "ok")
    calibration_blocked = any(
        "brier" in item or "calibration" in item for item in blockers
    )
    expected_value_blocked = any("expected_value" in item for item in blockers)
    return {
        "status": status,
        "calibration_drift_status": "blocked" if calibration_blocked else status,
        "expected_value_drift_status": (
            "blocked" if expected_value_blocked else status
        ),
        "calibration_drift": calibration_drift,
        "expected_value_drift": expected_value_drift,
        "upstream_drift": upstream_summary,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }


def _upstream_summary(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {
            "status": "missing",
            "feature_drift_status": "unknown",
            "label_drift_status": "unknown",
            "regime_drift_status": "unknown",
            "source_report_loaded": False,
        }
    feature = _mapping(payload.get("feature_drift_section"))
    target = _mapping(payload.get("target_drift_section"))
    regime = _mapping(payload.get("walkforward_regime_section"))
    return {
        "status": payload.get("status"),
        "feature_drift_status": feature.get("status", "unknown"),
        "label_drift_status": target.get("status", "unknown"),
        "regime_drift_status": regime.get("status", "unknown"),
        "source_report_loaded": True,
        "source_schema_version": payload.get("schema_version"),
        "source_blockers": list(payload.get("blockers", []))
        if isinstance(payload.get("blockers"), list)
        else [],
    }


def _load_json_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _delta(current: Any, baseline: Any) -> float | None:
    if current is None or baseline is None:
        return None
    try:
        return round(float(current) - float(baseline), 12)
    except (TypeError, ValueError):
        return None
