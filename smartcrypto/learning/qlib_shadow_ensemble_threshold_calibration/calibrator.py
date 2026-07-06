"""Research-only Qlib/IA Shadow ensemble threshold calibration.

The calibrator builds candidate ensemble thresholds from existing evidence. It
does not apply thresholds, update runtime, train, promote models, write
registries, or send orders. Domain functions are intentionally in-memory only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "qlib_shadow_ensemble_threshold_calibration_v1"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"

DEFAULT_REPORT_JSON = Path("data/reports/qlib_shadow_ensemble_threshold_calibration_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/qlib_shadow_ensemble_threshold_calibration_v1.md")

INPUT_SOURCES: tuple[tuple[str, Path, bool], ...] = (
    ("target_store", Path("data/reports/financial_label_target_store_v1.json"), True),
    ("paper_autotrain_feedback_loop", Path("data/reports/paper_autotrain_feedback_loop_v1.json"), True),
    ("drift_monitor", Path("data/reports/ai_qlib_drift_regime_monitor_v1.json"), True),
    ("execution_cost_gate", Path("data/reports/event_driven_backtest_execution_cost_gate_v1.json"), True),
    ("qlib_trainer", Path("data/reports/qlib_institutional_ranking_trainer_v1.json"), False),
    ("ai_shadow_quality_veto", Path("data/reports/ai_shadow_quality_veto_trainer_v1.json"), False),
)

THRESHOLD_GRID = tuple(round(value / 100.0, 2) for value in range(10, 96, 5))


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    relative_path: str
    path: Path
    required: bool
    exists: bool
    sha256: str | None
    load_error: str | None
    payload: dict[str, Any]

    def public_record(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "relative_path": self.relative_path,
            "path": str(self.path),
            "required": self.required,
            "exists": self.exists,
            "sha256": self.sha256,
            "load_error": self.load_error,
        }


def build_qlib_shadow_ensemble_threshold_calibration_v1(
    *,
    project_root: str | Path,
    write: bool = False,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build calibration evidence in memory.

    The ``write`` parameter is accepted for interface compatibility but ignored
    by the domain builder. File writes are owned only by the CLI wrapper.
    """

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    sources = load_sources(root)
    payloads = {source.source_id: source.payload for source in sources if source.payload}
    missing_required = [
        f"missing_required_source:{source.relative_path}"
        for source in sources
        if source.required and (not source.exists or source.load_error is not None)
    ]
    optional_warnings = [
        f"missing_optional_source:{source.relative_path}"
        for source in sources
        if not source.required and (not source.exists or source.load_error is not None)
    ]
    calibration_rows, row_warnings = build_calibration_rows(payloads)
    warnings = sorted(set(optional_warnings + row_warnings))
    threshold_grid = evaluate_threshold_grid(calibration_rows)
    recommended = select_recommended_candidate(threshold_grid)
    blockers = sorted(set(missing_required + calibration_blockers(calibration_rows)))
    status, reason = decide_status(blockers, warnings)
    safety = safety_flags()
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "decision": DECISION_RESEARCH,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "input_sources": [source.public_record() for source in sources],
        "lineage_hashes": build_lineage_hashes(payloads),
        "calibration_row_count": len(calibration_rows),
        "threshold_grid": threshold_grid,
        "recommended_candidate": recommended,
        "thresholds_applied": False,
        "ensemble_score_policy": {
            "qlib_score_source": "row_score_fields_else_neutral_0_5",
            "ai_shadow_score_source": "ai_shadow_decision_sample_probability_quality_else_row_score_fields_else_neutral_0_5",
            "ensemble_score": "mean(qlib_score, ai_shadow_score)",
            "applies_thresholds_to_runtime": False,
        },
        "blockers": blockers,
        "warnings": warnings,
        "write_requested": bool(write),
        "write_performed": False,
        **safety,
        "safety_flags": safety,
    }
    return report


def load_sources(project_root: Path) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for source_id, relative_path, required in INPUT_SOURCES:
        path = project_root / relative_path
        exists = path.is_file()
        payload: dict[str, Any] = {}
        load_error: str | None = None
        if exists:
            try:
                parsed = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                load_error = f"invalid_json:{exc.__class__.__name__}"
            else:
                if isinstance(parsed, dict):
                    payload = parsed
                else:
                    load_error = "json_root_not_object"
        records.append(
            SourceRecord(
                source_id=source_id,
                relative_path=relative_path.as_posix(),
                path=path.resolve(),
                required=required,
                exists=exists,
                sha256=file_sha256(path) if exists else None,
                load_error=load_error,
                payload=payload,
            )
        )
    return records


def build_calibration_rows(payloads: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    target_rows = list_of_mappings(payloads.get("target_store", {}).get("target_records"))
    shadow_scores = shadow_score_by_order_id(payloads.get("ai_shadow_quality_veto", {}))
    warnings: list[str] = []
    if not target_rows:
        return [], ["target_store_has_no_target_records"]
    if not shadow_scores:
        warnings.append("ai_shadow_per_row_score_unavailable_using_row_or_neutral_scores")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(target_rows):
        pnl = first_float(
            row.get("target_expected_value_component"),
            row.get("target_net_pnl"),
            row.get("net_pnl"),
            row.get("pnl"),
        )
        if pnl is None:
            continue
        order_id = str(row.get("order_id") or row.get("event_id") or row.get("trade_id") or f"row-{index}")
        ai_score = first_float(
            shadow_scores.get(order_id),
            row.get("probability_quality"),
            row.get("ai_shadow_score"),
            row.get("shadow_score"),
            0.5,
        )
        qlib_score = first_float(
            row.get("qlib_score"),
            row.get("qlib_probability"),
            row.get("prediction_score"),
            row.get("model_score"),
            0.5,
        )
        assert ai_score is not None
        assert qlib_score is not None
        ensemble = clamp01((clamp01(qlib_score) + clamp01(ai_score)) / 2.0)
        rows.append(
            {
                "row_id": order_id,
                "symbol": str(row.get("symbol_norm") or row.get("symbol") or "unknown").upper(),
                "side": str(row.get("side") or "unknown").lower(),
                "pnl": round(float(pnl), 10),
                "is_win": bool(float(pnl) > 0),
                "qlib_score": round(clamp01(qlib_score), 10),
                "ai_shadow_score": round(clamp01(ai_score), 10),
                "ensemble_score": round(ensemble, 10),
            }
        )
    return rows, warnings


def shadow_score_by_order_id(payload: Mapping[str, Any]) -> dict[str, float]:
    output: dict[str, float] = {}
    for row in list_of_mappings(payload.get("decision_sample")):
        order_id = row.get("order_id") or row.get("event_id") or row.get("trade_id")
        score = first_float(row.get("probability_quality"), row.get("ai_shadow_score"))
        if order_id is not None and score is not None:
            output[str(order_id)] = clamp01(score)
    return output


def evaluate_threshold_grid(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    positive_total = sum(1 for row in rows if bool(row.get("is_win")))
    output: list[dict[str, Any]] = []
    for threshold in THRESHOLD_GRID:
        selected = [row for row in rows if to_float(row.get("ensemble_score")) >= threshold]
        rejected = [row for row in rows if to_float(row.get("ensemble_score")) < threshold]
        selected_wins = sum(1 for row in selected if bool(row.get("is_win")))
        pnl_selected = round(sum(to_float(row.get("pnl")) for row in selected), 10)
        pnl_rejected = round(sum(to_float(row.get("pnl")) for row in rejected), 10)
        selected_count = len(selected)
        rejected_count = len(rejected)
        precision = round(selected_wins / selected_count, 10) if selected_count else 0.0
        recall = round(selected_wins / positive_total, 10) if positive_total else 0.0
        avg_ev = round(pnl_selected / selected_count, 10) if selected_count else 0.0
        output.append(
            {
                "threshold": threshold,
                "selected_count": selected_count,
                "accepted_count": selected_count,
                "rejected_count": rejected_count,
                "pnl_selected": pnl_selected,
                "pnl_rejected": pnl_rejected,
                "precision_proxy": precision,
                "recall_proxy": recall,
                "average_expected_value": avg_ev,
                "research_only": True,
                "thresholds_applied": False,
            }
        )
    return output


def select_recommended_candidate(threshold_grid: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    if not threshold_grid:
        return None
    candidates = [row for row in threshold_grid if to_int(row.get("selected_count")) > 0]
    if not candidates:
        return None
    best = max(
        candidates,
        key=lambda row: (
            to_float(row.get("pnl_selected")),
            to_float(row.get("average_expected_value")),
            to_float(row.get("precision_proxy")),
            to_float(row.get("recall_proxy")),
            -abs(to_float(row.get("threshold")) - 0.5),
        ),
    )
    return {
        **dict(best),
        "candidate_decision": DECISION_RESEARCH,
        "recommended_for_runtime": False,
        "thresholds_applied": False,
        "research_only": True,
    }


def calibration_blockers(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    if not rows:
        return ["no_valid_calibration_rows"]
    if len(rows) < 2:
        return ["insufficient_calibration_rows"]
    return []


def decide_status(blockers: Sequence[str], warnings: Sequence[str]) -> tuple[str, str]:
    if blockers:
        return "blocked", "ensemble_threshold_calibration_blocked"
    if warnings:
        return "warning", "ensemble_threshold_calibration_warnings_research_only"
    return "ok", "ensemble_threshold_calibration_completed_research_only"


def render_markdown(report: Mapping[str, Any]) -> str:
    candidate = report.get("recommended_candidate") if isinstance(report.get("recommended_candidate"), Mapping) else {}
    return "\n".join(
        [
            "# Qlib Shadow Ensemble Threshold Calibration V1",
            "",
            "## Executive Summary",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Calibration rows: `{report.get('calibration_row_count')}`",
            f"- Thresholds applied: `{report.get('thresholds_applied')}`",
            f"- Recommended threshold: `{candidate.get('threshold')}`",
            f"- Recommended pnl selected: `{candidate.get('pnl_selected')}`",
            "",
            "## Threshold Grid",
            "",
            *markdown_threshold_rows(report.get("threshold_grid", [])),
            "",
            "## Safety Invariants",
            "",
            "- `operational_authority=false`",
            "- `release_allowed=false`",
            "- `updates_ai_shadow_thresholds=false`",
            "- `updates_qlib_runtime=false`",
            "- `writes_registry=false`",
            "- `runs_training=false`",
            "- `promotes_model=false`",
            "- `writes_runtime=false`",
            "- `writes_sqlite=false`",
            "- `writes_parquet=false`",
            "",
            "This report suggests candidates only. It does not apply thresholds or change runtime behavior.",
            "",
        ]
    )


def markdown_threshold_rows(rows: Any) -> list[str]:
    records = list_of_mappings(rows)
    if not records:
        return ["- No threshold rows available."]
    return [
        (
            f"- `{row.get('threshold')}`: selected=`{row.get('selected_count')}`, "
            f"pnl_selected=`{row.get('pnl_selected')}`, precision=`{row.get('precision_proxy')}`, "
            f"avg_ev=`{row.get('average_expected_value')}`"
        )
        for row in records[:10]
    ]


def write_reports(report: Mapping[str, Any], report_json: Path, report_md: Path) -> None:
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    write_json(report_json, report)
    report_md.write_text(render_markdown(report), encoding="utf-8")


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n",
        encoding="utf-8",
    )


def build_lineage_hashes(payloads: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for payload in payloads.values():
        for key in (
            "dataset_hash",
            "feature_contract_hash",
            "target_store_hash",
            "split_engine_hash",
            "walkforward_split_engine_hash",
        ):
            if payload.get(key):
                output[key] = payload[key]
        nested = payload.get("lineage_hashes")
        if isinstance(nested, dict):
            output.update({str(key): value for key, value in nested.items() if value})
    return output


def safety_flags() -> dict[str, bool]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "read_only": True,
        "operational_authority": False,
        "release_allowed": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "ai_shadow_runtime_updated": False,
        "updates_ai_shadow_thresholds": False,
        "qlib_runtime_updated": False,
        "updates_qlib_runtime": False,
        "writes_registry": False,
        "registry_write_performed": False,
        "runs_training": False,
        "promotes_model": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "changes_risk": False,
        "updates_risk_manager": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else root / path


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def first_float(*values: Any) -> float | None:
    for value in values:
        try:
            if value is None:
                continue
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def to_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def to_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def json_safe(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value
