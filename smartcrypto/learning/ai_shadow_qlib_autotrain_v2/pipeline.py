"""B05 consolidation pipeline for AI Shadow, Qlib and governed autotrain."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .calibration import build_calibration_suite
from .contracts import (
    DECISION_RESEARCH,
    DEFAULT_REPORT_JSON,
    DEFAULT_REPORT_MD,
    DEFAULT_UPSTREAM_DRIFT_REPORT,
    SCHEMA_VERSION,
    SafetyFlags,
    canonical_hash,
    load_pipeline_config,
)
from .counterfactual import build_counterfactual_harness, normalize_rows
from .drift import build_drift_overlay
from .governance import build_cadence_governance, evaluate_training_eligibility
from .reporting import write_reports

UPSTREAM_EVIDENCE_PATHS: tuple[tuple[str, Path], ...] = (
    ("qlib_trainer", Path("data/reports/qlib_institutional_ranking_trainer_v1.json")),
    ("ai_shadow_trainer", Path("data/reports/ai_shadow_quality_veto_trainer_v1.json")),
    (
        "ensemble_calibration",
        Path("data/reports/qlib_shadow_ensemble_threshold_calibration_v1.json"),
    ),
    ("drift_monitor", DEFAULT_UPSTREAM_DRIFT_REPORT),
    ("autotrain_feedback_loop", Path("data/reports/paper_autotrain_feedback_loop_v1.json")),
    (
        "autotrain_quarantine",
        Path("data/reports/paper_autotrain_daily_quarantine_activation_v1.json"),
    ),
    (
        "watermark",
        Path("data/reports/paper_autotrain_incremental_watermark_fix_v1.json"),
    ),
)


def build_ai_shadow_qlib_autotrain_v2(
    *,
    project_root: str | Path,
    input_path: str | Path | None = None,
    config_path: str | Path | None = None,
    baseline_calibration_path: str | Path | None = None,
    write_report: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    config = load_pipeline_config(root, config_path)
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    input_payload, fixture_mode, input_source = _load_input(root, input_path)
    rows = _list_of_mappings(input_payload.get("rows"))
    training_state = _mapping(input_payload.get("training_state"))
    normalized_rows, row_blockers = normalize_rows(rows)
    calibration_suite = (
        build_calibration_suite(
            normalized_rows,
            bin_count=config.calibration_bins,
            min_bucket_rows=config.min_bucket_rows,
        )
        if not row_blockers
        else _empty_calibration_suite(row_blockers)
    )
    counterfactual = build_counterfactual_harness(rows, config.policies)
    training_governance = _safe_training_gate(
        training_state,
        min_training_sample_rows=config.min_training_sample_rows,
    )
    cadence_governance = build_cadence_governance(config.cadence)
    baseline_calibration = _load_optional_calibration(root, baseline_calibration_path)
    drift_overlay = build_drift_overlay(
        calibration_suite,
        baseline_calibration=baseline_calibration,
        upstream_drift_report_path=root / DEFAULT_UPSTREAM_DRIFT_REPORT,
        max_brier_degradation=config.max_brier_degradation,
        max_ece_degradation=config.max_ece_degradation,
        max_expected_value_degradation=config.max_expected_value_degradation,
    )
    upstream_evidence = _upstream_evidence_inventory(root)
    blockers = sorted(
        set(
            row_blockers
            + list(counterfactual.get("blockers", []))
            + list(training_governance.get("blockers", []))
            + list(drift_overlay.get("blockers", []))
        )
    )
    warnings = sorted(
        set(
            _calibration_warnings(calibration_suite)
            + list(drift_overlay.get("warnings", []))
        )
    )
    status = "blocked" if blockers else ("warning" if warnings else "ok")
    reason = (
        blockers[0]
        if blockers
        else (warnings[0] if warnings else "b05_research_evidence_built")
    )
    safety = SafetyFlags().as_dict()
    output_json = _resolve(root, output_json_path, DEFAULT_REPORT_JSON)
    output_md = _resolve(root, output_markdown_path, DEFAULT_REPORT_MD)
    result_payload = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "decision": DECISION_RESEARCH,
        "authoritative_result": not fixture_mode,
        "fixture_mode": fixture_mode,
        "input_source": input_source,
        "input_row_count": len(rows),
        "normalized_row_count": len(normalized_rows),
        "calibration_suite": calibration_suite,
        "counterfactual_harness": counterfactual,
        "training_governance": training_governance,
        "cadence_governance": cadence_governance,
        "drift_overlay": drift_overlay,
        "upstream_evidence": upstream_evidence,
        "blockers": blockers,
        "warnings": warnings,
        "safety_flags": safety,
    }
    result_hash = canonical_hash(result_payload)
    report: dict[str, Any] = {
        **result_payload,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "result_hash": result_hash,
        "write_requested": bool(write_report),
        "write_performed": False,
        "output_paths": {
            "json": str(output_json),
            "markdown": str(output_md),
        },
        **safety,
    }
    if write_report:
        report["write_performed"] = True
        write_reports(
            report,
            project_root=root,
            json_path=output_json,
            markdown_path=output_md,
        )
    return report


def _load_input(
    project_root: Path,
    input_path: str | Path | None,
) -> tuple[dict[str, Any], bool, str]:
    if input_path is None:
        return _fixture_payload(), True, "built_in_sanitized_fixture"
    candidate = Path(input_path)
    path = candidate if candidate.is_absolute() else project_root / candidate
    parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(parsed, list):
        return {"rows": parsed, "training_state": _default_training_state(parsed)}, False, str(path)
    if not isinstance(parsed, dict):
        raise ValueError("input JSON must be an object or a list")
    return parsed, False, str(path)


def _fixture_payload() -> dict[str, Any]:
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    rows: list[dict[str, Any]] = []
    for index in range(24):
        label = 1 if index % 3 != 0 else 0
        qlib_score = round((index % 12) / 11.0, 6)
        shadow_probability = round(0.18 + (index % 10) * 0.075, 6)
        entry = 100.0 + index
        pnl = 2.5 + index * 0.05 if label else -(1.75 + index * 0.03)
        side = "long" if index % 2 == 0 else "short"
        exit_price = entry + pnl if side == "long" else entry - pnl
        rows.append(
            {
                "event_id": f"fixture-event-{index:03d}",
                "candle_time_utc": (base_time + timedelta(minutes=5 * index)).isoformat(),
                "symbol": "BTCUSDT" if index % 2 == 0 else "ETHUSDT",
                "side": side,
                "expected_entry": round(entry, 6),
                "expected_exit": round(exit_price, 6),
                "net_pnl": round(pnl, 6),
                "label": label,
                "qlib_score": qlib_score,
                "ai_shadow_probability": shadow_probability,
            }
        )
    return {
        "rows": rows,
        "training_state": {
            "new_unique_trade_count": 24,
            "total_unique_sample_count": 24,
            "previous_watermark": "2025-12-31T23:00:00+00:00",
            "current_watermark": "2026-01-01T01:55:00+00:00",
            "previous_dataset_hash": hashlib.sha256(b"fixture-previous").hexdigest(),
            "current_dataset_hash": hashlib.sha256(b"fixture-current").hexdigest(),
            "prior_microbatch_hashes": [hashlib.sha256(b"fixture-old-batch").hexdigest()],
        },
    }


def _default_training_state(rows: Sequence[Any]) -> dict[str, Any]:
    payload_hash = hashlib.sha256(
        json.dumps(rows, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return {
        "new_unique_trade_count": len(rows),
        "total_unique_sample_count": len(rows),
        "previous_watermark": "1970-01-01T00:00:00+00:00",
        "current_watermark": "1970-01-01T00:00:01+00:00",
        "previous_dataset_hash": hashlib.sha256(b"empty").hexdigest(),
        "current_dataset_hash": payload_hash,
        "prior_microbatch_hashes": [],
    }


def _safe_training_gate(
    state: Mapping[str, Any],
    *,
    min_training_sample_rows: int,
) -> dict[str, Any]:
    try:
        return evaluate_training_eligibility(
            state,
            min_training_sample_rows=min_training_sample_rows,
        )
    except ValueError as exc:
        return {
            "status": "blocked",
            "research_training_eligible": False,
            "blockers": [f"invalid_training_state:{exc}"],
            "training_requested": False,
            "training_performed": False,
            "automatic_training": False,
            "challenger_destination": "quarantine_research_only",
            "promotion_allowed": False,
            "automatic_promotion": False,
            "active_model_changed": False,
            "writes_active_registry": False,
            "updates_qlib_runtime": False,
            "updates_ai_shadow_runtime": False,
        }


def _empty_calibration_suite(blockers: Sequence[str]) -> dict[str, Any]:
    return {
        "status": "blocked",
        "row_count": 0,
        "blockers": list(blockers),
        "qlib_ranker": {},
        "ai_shadow_veto": {},
        "ensemble": {},
        "calibration_applied_to_runtime": False,
        "thresholds_applied_to_runtime": False,
    }


def _load_optional_calibration(
    project_root: Path,
    baseline_path: str | Path | None,
) -> dict[str, Any] | None:
    if baseline_path is None:
        return None
    candidate = Path(baseline_path)
    path = candidate if candidate.is_absolute() else project_root / candidate
    parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(parsed, dict):
        raise ValueError("baseline calibration root must be an object")
    if isinstance(parsed.get("calibration_suite"), Mapping):
        return dict(parsed["calibration_suite"])
    return parsed


def _upstream_evidence_inventory(project_root: Path) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source_id, relative_path in UPSTREAM_EVIDENCE_PATHS:
        path = project_root / relative_path
        exists = path.is_file()
        output.append(
            {
                "source_id": source_id,
                "relative_path": relative_path.as_posix(),
                "exists": exists,
                "sha256": _file_sha256(path) if exists else None,
                "read_only": True,
            }
        )
    return output


def _calibration_warnings(calibration_suite: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    for model_key in ("qlib_ranker", "ai_shadow_veto", "ensemble"):
        section = calibration_suite.get(model_key)
        if not isinstance(section, Mapping):
            continue
        raw = section.get("warnings", [])
        if isinstance(raw, list):
            warnings.extend(f"{model_key}:{item}" for item in raw)
    return warnings


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    candidate = Path(value) if value is not None else default
    return candidate if candidate.is_absolute() else root / candidate


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list_of_mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]
