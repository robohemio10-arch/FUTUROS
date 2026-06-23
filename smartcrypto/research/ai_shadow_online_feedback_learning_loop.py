"""Offline, record-only feedback evidence loop for AI Shadow research."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOOP_VERSION = "1.0"

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "runs_training": False,
    "updates_freqtrade": False,
    "updates_qlib_runtime": False,
    "updates_risk_manager": False,
    "updates_ai_shadow_runtime": False,
    "runs_ai_shadow_incremental": False,
    "cleans_sqlite": False,
    "writes_sqlite": False,
    "registers_model": False,
    "auto_promote": False,
    "production_enabled": False,
}

UNSAFE_TRUE_FLAGS = (
    "live_trading_enabled",
    "live_release_allowed",
    "canary_release_allowed",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
    "changes_model",
    "runs_training",
    "updates_freqtrade",
    "updates_qlib_runtime",
    "updates_risk_manager",
    "updates_ai_shadow_runtime",
    "runs_ai_shadow_incremental",
    "cleans_sqlite",
    "writes_sqlite",
    "registers_model",
    "auto_promote",
    "production_enabled",
)

SOURCE_EVENT_TYPES = {
    "branch04_training_summary": "branch04_supervised_result_observed",
    "branch05_executive_pack": "branch05_executive_pack_observed",
    "branch06_candidate_report": "branch06_shadow_candidate_registered",
    "outcome_attribution_report": "ai_shadow_outcome_attribution_observed",
    "financial_threshold_report": "ai_shadow_financial_thresholds_observed",
    "drift_monitor_report": "ai_shadow_drift_monitor_observed",
    "incremental_trainer_report": "ai_shadow_incremental_trainer_observed",
}

OPTIONAL_SOURCE_BLOCKERS = {
    "threshold_readiness_report": "missing_ai_shadow_threshold_readiness_report",
    "decision_logger_report": "missing_ai_shadow_decision_logger_report",
    "outcome_tracker_report": "missing_ai_shadow_outcome_tracker_report",
}

CRITICAL_SOURCES = {
    "branch04_training_summary",
    "branch05_executive_pack",
    "branch06_candidate_report",
}


@dataclass(frozen=True)
class AIShadowFeedbackLoopPaths:
    project_root: Path
    training_summary_path: Path
    executive_pack_path: Path
    shadow_candidate_report_path: Path
    shadow_candidate_registry_path: Path
    outcome_attribution_report_path: Path
    financial_threshold_report_path: Path
    threshold_readiness_report_path: Path
    drift_monitor_report_path: Path
    decision_logger_report_path: Path
    outcome_tracker_report_path: Path
    incremental_trainer_report_path: Path
    report_output_path: Path
    events_output_path: Path


@dataclass(frozen=True)
class AIShadowFeedbackLoopConfig:
    strict: bool = False
    loop_version: str = LOOP_VERSION


@dataclass(frozen=True)
class AIShadowFeedbackLoopResult:
    report: dict[str, Any]
    events: list[dict[str, Any]]


def _resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    if value is None:
        return default.resolve()
    path = Path(value).expanduser()
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def resolve_paths(
    project_root: str | Path,
    *,
    training_summary: str | Path | None = None,
    executive_pack: str | Path | None = None,
    shadow_candidate_report: str | Path | None = None,
    shadow_candidate_registry: str | Path | None = None,
    outcome_attribution_report: str | Path | None = None,
    financial_threshold_report: str | Path | None = None,
    threshold_readiness_report: str | Path | None = None,
    drift_monitor_report: str | Path | None = None,
    decision_logger_report: str | Path | None = None,
    outcome_tracker_report: str | Path | None = None,
    incremental_trainer_report: str | Path | None = None,
    report_output: str | Path | None = None,
    events_output: str | Path | None = None,
) -> AIShadowFeedbackLoopPaths:
    root = Path(project_root).expanduser().resolve()
    reports = root / "data" / "reports"
    return AIShadowFeedbackLoopPaths(
        project_root=root,
        training_summary_path=_resolve(
            root,
            training_summary,
            reports / "qlib_ocr_v11_supervised_training_summary.json",
        ),
        executive_pack_path=_resolve(
            root,
            executive_pack,
            reports / "training_reports" / "smart_futuros_training_executive_pack.json",
        ),
        shadow_candidate_report_path=_resolve(
            root,
            shadow_candidate_report,
            reports / "qlib_ocr_v11_shadow_model_candidate_registry_report.json",
        ),
        shadow_candidate_registry_path=_resolve(
            root,
            shadow_candidate_registry,
            root
            / "data"
            / "models"
            / "qlib_ocr_v11"
            / "research"
            / "qlib_ocr_v11_shadow_candidate_registry.json",
        ),
        outcome_attribution_report_path=_resolve(
            root,
            outcome_attribution_report,
            reports / "ai_shadow_outcome_attribution_report.json",
        ),
        financial_threshold_report_path=_resolve(
            root,
            financial_threshold_report,
            reports / "ai_shadow_financial_threshold_evaluation_report.json",
        ),
        threshold_readiness_report_path=_resolve(
            root,
            threshold_readiness_report,
            reports / "ai_shadow_threshold_readiness_report.json",
        ),
        drift_monitor_report_path=_resolve(
            root,
            drift_monitor_report,
            reports / "ai_shadow_drift_monitor_report.json",
        ),
        decision_logger_report_path=_resolve(
            root,
            decision_logger_report,
            reports / "ai_shadow_model_decision_logger_report.json",
        ),
        outcome_tracker_report_path=_resolve(
            root,
            outcome_tracker_report,
            reports / "ai_shadow_outcome_tracker_report.json",
        ),
        incremental_trainer_report_path=_resolve(
            root,
            incremental_trainer_report,
            reports / "ai_shadow_incremental_trainer_report.json",
        ),
        report_output_path=_resolve(
            root,
            report_output,
            reports / "ai_shadow_online_feedback_learning_loop_report.json",
        ),
        events_output_path=_resolve(
            root,
            events_output,
            reports / "ai_shadow_online_feedback_learning_loop_events.jsonl",
        ),
    )


def load_json_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_report_must_be_object:{path}")
    return payload


def _source_paths(paths: AIShadowFeedbackLoopPaths) -> dict[str, Path]:
    return {
        "branch04_training_summary": paths.training_summary_path,
        "branch05_executive_pack": paths.executive_pack_path,
        "branch06_candidate_report": paths.shadow_candidate_report_path,
        "branch06_candidate_registry": paths.shadow_candidate_registry_path,
        "outcome_attribution_report": paths.outcome_attribution_report_path,
        "financial_threshold_report": paths.financial_threshold_report_path,
        "threshold_readiness_report": paths.threshold_readiness_report_path,
        "drift_monitor_report": paths.drift_monitor_report_path,
        "decision_logger_report": paths.decision_logger_report_path,
        "outcome_tracker_report": paths.outcome_tracker_report_path,
        "incremental_trainer_report": paths.incremental_trainer_report_path,
    }


def collect_feedback_evidence(paths: AIShadowFeedbackLoopPaths) -> dict[str, Any]:
    sources: dict[str, dict[str, Any]] = {}
    missing_sources: list[str] = []
    warnings: list[str] = []
    load_errors: list[str] = []
    for name, path in _source_paths(paths).items():
        if not path.exists():
            missing_sources.append(name)
            warnings.append(f"missing_source:{name}")
            sources[name] = {
                "available": False,
                "path": str(path),
                "payload": {},
                "load_error": None,
            }
            continue
        try:
            payload = load_json_report(path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            error = f"{name}:{type(exc).__name__}"
            load_errors.append(error)
            warnings.append(f"invalid_source:{error}")
            sources[name] = {
                "available": False,
                "path": str(path),
                "payload": {},
                "load_error": error,
            }
            continue
        sources[name] = {
            "available": True,
            "path": str(path),
            "payload": payload,
            "load_error": None,
        }
    return {
        "sources": sources,
        "missing_sources": sorted(missing_sources),
        "warnings": sorted(set(warnings)),
        "load_errors": sorted(load_errors),
    }


def _payload(evidence: dict[str, Any], source: str) -> dict[str, Any]:
    return evidence.get("sources", {}).get(source, {}).get("payload", {})


def _value(payload: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = payload
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if current is not None:
            return current
    return None


def _number(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _unsafe_flags(evidence: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for source, entry in evidence.get("sources", {}).items():
        if not entry.get("available"):
            continue
        payload = entry.get("payload", {})
        for flag in ("paper_only", "shadow_only"):
            if flag in payload and payload.get(flag) is not True:
                blockers.append(f"unsafe_safety_flag:{source}:{flag}={payload.get(flag)!r}")
        for flag in UNSAFE_TRUE_FLAGS:
            if payload.get(flag) is True:
                blockers.append(f"unsafe_safety_flag:{source}:{flag}=true")
    return blockers


def evaluate_learning_gate(
    evidence: dict[str, Any],
    config: AIShadowFeedbackLoopConfig,
) -> dict[str, Any]:
    blockers: list[str] = []
    branch04 = _payload(evidence, "branch04_training_summary")
    branch05 = _payload(evidence, "branch05_executive_pack")
    branch06 = _payload(evidence, "branch06_candidate_report")
    trainer = _payload(evidence, "incremental_trainer_report")
    if branch04.get("decision") == "MANTER_EM_RESEARCH":
        blockers.append("branch04_kept_in_research")
    selected = _number(
        _value(branch04, "aggregate_metrics.selected_net_pnl", "selected_net_pnl")
    )
    all_test = _number(
        _value(branch04, "aggregate_metrics.all_test_net_pnl", "all_test_net_pnl")
    )
    if selected is not None and all_test is not None and float(selected) <= float(all_test):
        blockers.append("branch04_selected_not_above_all_test")
    if branch05.get("decision") == "MANTER_EM_RESEARCH":
        blockers.append("branch05_kept_in_research")
    if branch06.get("promotion_status") == "blocked":
        blockers.append("branch06_promotion_blocked")
    if branch06.get("promotion_eligible") is False:
        blockers.append("branch06_not_promotion_eligible")
    if trainer.get("promotion_status") == "pending":
        blockers.append("ai_shadow_trainer_pending_not_approved")
    missing_sources = set(evidence.get("missing_sources", []))
    unavailable_sources = {
        name
        for name, entry in evidence.get("sources", {}).items()
        if not entry.get("available")
    }
    for source, blocker in OPTIONAL_SOURCE_BLOCKERS.items():
        if source in missing_sources or source in unavailable_sources:
            blockers.append(blocker)
    blockers.extend(_unsafe_flags(evidence))
    blockers.append("research_feedback_scope_forbids_training")
    critical_missing = sorted(
        source
        for source in CRITICAL_SOURCES
        if source in missing_sources or source in unavailable_sources
    )
    return {
        "status": "blocked",
        "learning_action": "record_only",
        "training_allowed": False,
        "promotion_status": "blocked",
        "promotion_allowed": False,
        "learning_blockers": list(dict.fromkeys(blockers)),
        "critical_missing_sources": critical_missing,
        "strict": config.strict,
        "recommended_next_actions": [
            "materialize_missing_shadow_readiness_and_observation_reports",
            "collect_more_paper_shadow_outcomes_without_runtime_changes",
            "require_selected_pnl_above_all_test_baseline_before_reconsideration",
            "keep_candidate_out_of_production_registry_and_qlib_runtime",
        ],
    }


def _stable_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _event(
    *,
    event_type: str,
    source: str,
    status: str,
    decision: str,
    summary: str,
    analysis_date_utc: str,
    identity_payload: dict[str, Any],
) -> dict[str, Any]:
    identity = f"{event_type}|{source}|{_stable_payload_hash(identity_payload)}"
    return {
        "event_id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32],
        "event_type": event_type,
        "analysis_date_utc": analysis_date_utc,
        "source": source,
        "status": status,
        "decision": decision,
        "summary": summary,
        "action_taken": "record_only",
        "sends_orders": False,
        "changes_risk": False,
        "runs_training": False,
        "registers_model": False,
        "updates_runtime": False,
    }


def build_feedback_events(
    evidence: dict[str, Any],
    gate: dict[str, Any],
    analysis_date_utc: str,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for source, event_type in SOURCE_EVENT_TYPES.items():
        entry = evidence.get("sources", {}).get(source, {})
        if not entry.get("available"):
            continue
        payload = entry.get("payload", {})
        status = str(payload.get("status") or "observed")
        decision = str(
            payload.get("decision")
            or payload.get("promotion_status")
            or payload.get("recommendation_reason")
            or "OBSERVED"
        )
        summary = str(
            payload.get("reason")
            or payload.get("recommendation")
            or f"{source}_evidence_observed"
        )
        events.append(
            _event(
                event_type=event_type,
                source=str(entry.get("path") or source),
                status=status,
                decision=decision,
                summary=summary,
                analysis_date_utc=analysis_date_utc,
                identity_payload=payload,
            )
        )
    events.append(
        _event(
            event_type="learning_gate_blocked",
            source="offline_feedback_learning_gate",
            status="blocked",
            decision="MANTER_EM_RESEARCH",
            summary=";".join(gate["learning_blockers"]),
            analysis_date_utc=analysis_date_utc,
            identity_payload={"learning_blockers": gate["learning_blockers"]},
        )
    )
    events.append(
        _event(
            event_type="recommended_next_actions_recorded",
            source="offline_feedback_learning_gate",
            status="warning",
            decision="RECORD_ONLY",
            summary=";".join(gate["recommended_next_actions"]),
            analysis_date_utc=analysis_date_utc,
            identity_payload={
                "recommended_next_actions": gate["recommended_next_actions"]
            },
        )
    )
    return sorted(events, key=lambda event: (event["event_type"], event["event_id"]))


def build_feedback_report(
    evidence: dict[str, Any],
    gate: dict[str, Any],
    events: list[dict[str, Any]],
    config: AIShadowFeedbackLoopConfig,
    analysis_date_utc: str,
) -> dict[str, Any]:
    critical_missing = list(gate.get("critical_missing_sources", []))
    status = "blocked" if config.strict or critical_missing else "warning"
    return {
        "loop_version": config.loop_version,
        "status": status,
        "reason": "feedback_recorded_without_training",
        "loop_status": "research_feedback_only",
        "learning_action": "record_only",
        "decision": "MANTER_EM_RESEARCH",
        "promotion_status": "blocked",
        "training_allowed": False,
        "promotion_allowed": False,
        "analysis_date_utc": analysis_date_utc,
        "learning_blockers": list(gate["learning_blockers"]),
        "critical_missing_sources": critical_missing,
        "recommended_next_actions": list(gate["recommended_next_actions"]),
        "missing_sources": list(evidence.get("missing_sources", [])),
        "warnings": list(evidence.get("warnings", [])),
        "load_errors": list(evidence.get("load_errors", [])),
        "source_status": {
            name: {
                "available": bool(entry.get("available")),
                "path": entry.get("path"),
                "status": entry.get("payload", {}).get("status"),
                "reason": entry.get("payload", {}).get("reason"),
                "decision": entry.get("payload", {}).get("decision"),
                "load_error": entry.get("load_error"),
            }
            for name, entry in evidence.get("sources", {}).items()
        },
        "event_count": len(events),
        "event_types": [event["event_type"] for event in events],
        "strict": config.strict,
        "write_requested": False,
        "write_performed": False,
        "new_events_written": 0,
        **SAFETY_FLAGS,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(
        _json_safe(payload),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    _atomic_write_text(path, content + "\n")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"jsonl_row_must_be_object:{path}:{line_number}")
        rows.append(payload)
    return rows


def _atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    content = "\n".join(
        json.dumps(
            _json_safe(row),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        for row in rows
    )
    _atomic_write_text(path, content + ("\n" if content else ""))


def run_ai_shadow_online_feedback_learning_loop(
    paths: AIShadowFeedbackLoopPaths,
    config: AIShadowFeedbackLoopConfig,
    *,
    write: bool = False,
    analysis_date_utc: str | None = None,
) -> AIShadowFeedbackLoopResult:
    analysis_date = analysis_date_utc or (
        datetime.now(timezone.utc).isoformat() if write else "not_recorded_no_write"
    )
    evidence = collect_feedback_evidence(paths)
    gate = evaluate_learning_gate(evidence, config)
    events = build_feedback_events(evidence, gate, analysis_date)
    report = build_feedback_report(evidence, gate, events, config, analysis_date)
    report["write_requested"] = write
    report["report_output_path"] = str(paths.report_output_path)
    report["events_output_path"] = str(paths.events_output_path)
    existing_events = _load_jsonl(paths.events_output_path)
    existing_ids = {str(event.get("event_id")) for event in existing_events}
    new_events = [event for event in events if event["event_id"] not in existing_ids]
    report["new_events_written"] = len(new_events) if write else 0
    report["existing_event_count"] = len(existing_events)
    if write:
        _atomic_write_jsonl(paths.events_output_path, [*existing_events, *new_events])
        report["write_performed"] = True
        _atomic_write_json(paths.report_output_path, report)
    return AIShadowFeedbackLoopResult(report=report, events=events)
