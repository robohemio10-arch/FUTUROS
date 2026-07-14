"""Daily foundation runner for paper/shadow auto-learning."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .challenger_training_smoke import run_challenger_training_smoke
from .feedback_store import (
    build_feedback_events,
    read_existing_outcome_events,
    write_feedback_outputs,
)
from .microbatch_builder import build_daily_microbatch
from .outcome_schema import (
    DEFAULT_CLOSED_TRADES_CSV,
    DEFAULT_FEEDBACK_STORE,
    DEFAULT_MICROBATCH_DIR,
    DEFAULT_OUTCOME_EVENTS,
    DEFAULT_REPORT_JSON,
    DEFAULT_REPORT_MD,
    DEFAULT_SOURCE_CONTRACT,
    FUTURES_COVERAGE_FIELDS,
    SCHEMA_VERSION,
    safety_payload,
    utc_now_iso,
)


def build_paper_autolearning_foundation_report(
    *,
    project_root: str | Path,
    source_path: str | Path | None = None,
    feedback_store_path: str | Path | None = None,
    outcome_events_path: str | Path | None = None,
    microbatch_dir: str | Path | None = None,
    report_path: str | Path | None = None,
    markdown_report_path: str | Path | None = None,
    closed_trade_rows: Sequence[Mapping[str, Any]] | None = None,
    write_feedback: bool = False,
    train_smoke: bool = False,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    feedback_path = _resolve(root, feedback_store_path, DEFAULT_FEEDBACK_STORE)
    outcome_path = _resolve(root, outcome_events_path, DEFAULT_OUTCOME_EVENTS)
    microbatch_output_dir = _resolve(root, microbatch_dir, DEFAULT_MICROBATCH_DIR)
    json_report_path = _resolve(root, report_path, DEFAULT_REPORT_JSON)
    md_report_path = _resolve(root, markdown_report_path, DEFAULT_REPORT_MD)
    if write_feedback:
        _validate_write_path(root, feedback_path, root / "data" / "feedback")
        _validate_write_path(root, outcome_path, root / "data" / "feedback")
        _validate_write_path(root, microbatch_output_dir, root / "data" / "feedback")
        _validate_write_path(root, json_report_path, root / "data" / "reports")
        _validate_write_path(root, md_report_path, root / "data" / "reports")
    feedback_result = build_feedback_events(
        project_root=root,
        source_path=source_path,
        existing_outcome_path=outcome_path,
        closed_trade_rows=closed_trade_rows,
    )
    microbatch = build_daily_microbatch(
        feedback_result.valid_events,
        output_dir=microbatch_output_dir,
        write=write_feedback,
    )
    smoke = run_challenger_training_smoke(microbatch["microbatch"], enabled=train_smoke)
    existing_events = read_existing_outcome_events(outcome_path)
    write_performed = False
    if write_feedback:
        write_feedback_outputs(
            feedback_store_path=feedback_path,
            outcome_events_path=outcome_path,
            existing_events=existing_events,
            new_events=feedback_result.new_events,
        )
        json_report_path.parent.mkdir(parents=True, exist_ok=True)
        md_report_path.parent.mkdir(parents=True, exist_ok=True)
        write_performed = True
    positive = sum(1 for event in feedback_result.valid_events if event.get("label_sign") == 1)
    negative = sum(1 for event in feedback_result.valid_events if event.get("label_sign") == -1)
    breakeven = sum(1 for event in feedback_result.valid_events if event.get("label_sign") == 0)
    validation_errors = sorted(set([*microbatch.get("validation_errors", [])]))
    status = (
        "blocked"
        if microbatch["status"] == "blocked"
        or not feedback_result.closed_rows
        or not feedback_result.valid_events
        or microbatch["microbatch_rows"] == 0
        else "ok"
    )
    reason = _reason(status, feedback_result.source_reason, microbatch)
    report: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now_iso(),
        "input_mode": feedback_result.input_mode,
        "source_path": str(feedback_result.source_path) if feedback_result.source_path is not None else None,
        "source_sha256": feedback_result.source_sha256,
        "closed_trades_loaded_count": len(feedback_result.closed_rows),
        "closed_trades_valid_count": len(feedback_result.valid_events),
        "closed_trades_rejected_count": len(feedback_result.rejected_rows),
        "new_feedback_events_count": len(feedback_result.new_events),
        "duplicate_feedback_events_count": len(feedback_result.duplicate_events),
        "positive_trade_count": positive,
        "negative_trade_count": negative,
        "breakeven_trade_count": breakeven,
        "futures_fields_coverage": feedback_result.futures_fields_coverage,
        "funding_fee_available": feedback_result.futures_fields_coverage.get("funding_fee", 0.0) > 0,
        "trading_fee_available": feedback_result.futures_fields_coverage.get("trading_fee", 0.0) > 0,
        "leverage_available": feedback_result.futures_fields_coverage.get("leverage", 0.0) > 0,
        "margin_mode_available": feedback_result.futures_fields_coverage.get("margin_mode", 0.0) > 0,
        "liquidation_price_available": feedback_result.futures_fields_coverage.get("liquidation_price", 0.0) > 0,
        "microbatch_rows": microbatch["microbatch_rows"],
        "microbatch_output_path": _project_relative(Path(microbatch["microbatch_output_path"]), root)
        if microbatch.get("microbatch_output_path")
        else None,
        "microbatch_feature_columns": microbatch["feature_columns"],
        "microbatch_label_columns": microbatch["label_columns"],
        "lookahead_columns": microbatch["lookahead_columns"],
        **smoke,
        "master_update_requested": False,
        "master_update_performed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        **safety_payload(writes_parquet=write_performed),
        "safety_flags": safety_payload(writes_parquet=write_performed),
        "validation_errors": validation_errors,
        "write_performed": write_performed,
        "output_paths": {
            "feedback_store": _project_relative(feedback_path, root) if write_feedback else None,
            "outcome_events": _project_relative(outcome_path, root) if write_feedback else None,
            "microbatch_dir": _project_relative(microbatch_output_dir, root) if write_feedback else None,
            "report": _project_relative(json_report_path, root) if write_feedback else None,
            "markdown_report": _project_relative(md_report_path, root) if write_feedback else None,
        },
        "rejected_rows_sample": feedback_result.rejected_rows[:20],
        "source_contract_path": str((root / DEFAULT_SOURCE_CONTRACT).resolve()),
        "default_closed_trades_path": str((root / DEFAULT_CLOSED_TRADES_CSV).resolve()),
    }
    report["validation_errors"] = validate_report(report)
    if write_feedback:
        json_report_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        md_report_path.write_text(render_markdown_report(report), encoding="utf-8")
    return report


def validate_report(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    for field in FUTURES_COVERAGE_FIELDS:
        if field not in report.get("futures_fields_coverage", {}):
            errors.append(f"missing_futures_coverage:{field}")
    for key, expected in safety_payload(writes_parquet=bool(report.get("write_performed"))).items():
        if report.get(key) is not expected:
            errors.append(f"{key}_must_be_{str(expected).lower()}")
        safety = report.get("safety_flags")
        if not isinstance(safety, Mapping) or safety.get(key) is not expected:
            errors.append(f"safety_flags.{key}_must_be_{str(expected).lower()}")
    return sorted(set([*errors, *list(report.get("validation_errors", []))]))


def render_markdown_report(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Paper Auto-learning Foundation V1",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Closed trades loaded: `{report.get('closed_trades_loaded_count')}`",
            f"- Valid closed trades: `{report.get('closed_trades_valid_count')}`",
            f"- New feedback events: `{report.get('new_feedback_events_count')}`",
            f"- Duplicate feedback events: `{report.get('duplicate_feedback_events_count')}`",
            f"- Microbatch rows: `{report.get('microbatch_rows')}`",
            f"- Qlib smoke ran: `{report.get('qlib_challenger_smoke_ran')}`",
            f"- IA Shadow smoke ran: `{report.get('ai_shadow_challenger_smoke_ran')}`",
            f"- Master update performed: `{report.get('master_update_performed')}`",
            f"- Model promotion performed: `{report.get('model_promotion_performed')}`",
            "",
            "This foundation loop is paper/shadow only. It does not update legacy datasets, train a production model, promote a champion, alter runtime, send orders or access private exchange APIs.",
            "",
        ]
    )


def _reason(status: str, source_reason: str, microbatch: Mapping[str, Any]) -> str:
    if status == "blocked":
        if microbatch.get("status") == "blocked":
            return str(microbatch.get("reason"))
        if microbatch.get("microbatch_rows", 0) == 0:
            return "no_valid_closed_trades_for_microbatch"
        return source_reason
    if microbatch.get("microbatch_rows", 0) == 0:
        return "no_microbatch_rows"
    return "paper_autolearning_foundation_loop_closed"


def _validate_write_path(root: Path, path: Path, allowed_root: Path) -> None:
    try:
        path.resolve().relative_to(allowed_root.resolve())
    except ValueError as exc:
        raise ValueError(f"write_path_outside_allowed_root:{path}") from exc


def _resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _project_relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)
