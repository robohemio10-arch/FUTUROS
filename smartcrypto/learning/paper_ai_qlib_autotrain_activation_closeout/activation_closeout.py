"""Research-only closeout for paper AI/Qlib/autotrain activation.

The closeout consolidates evidence from the registry gate, signal candidate
producer, and selector dry-run. It proves whether the activation path is still
blocked, but it never activates runtime components or writes operational state.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "paper_ai_qlib_autotrain_activation_closeout_v1"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"

DEFAULT_REPORT_JSON = Path("data/reports/paper_ai_qlib_autotrain_activation_closeout_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/paper_ai_qlib_autotrain_activation_closeout_v1.md")

SOURCE_SPECS: tuple[tuple[str, Path, bool], ...] = (
    ("registry_gate", Path("data/reports/paper_model_candidate_registry_gate_v1.json"), True),
    ("signal_producer", Path("data/reports/paper_ai_signal_candidate_producer_v1.json"), True),
    (
        "selector_dryrun",
        Path("data/reports/freqtrade_paper_ai_selector_e2e_dryrun_v1.json"),
        True,
    ),
    ("autotrain_feedback_loop", Path("data/reports/paper_autotrain_feedback_loop_v1.json"), False),
    ("qlib_trainer", Path("data/reports/qlib_institutional_ranking_trainer_v1.json"), False),
    ("ai_shadow_quality_veto", Path("data/reports/ai_shadow_quality_veto_trainer_v1.json"), False),
    ("drift_monitor", Path("data/reports/ai_qlib_drift_regime_monitor_v1.json"), False),
    (
        "execution_cost_gate",
        Path("data/reports/event_driven_backtest_execution_cost_gate_v1.json"),
        False,
    ),
    (
        "monte_carlo_gate",
        Path("data/reports/monte_carlo_risk_ruin_stress_gate_v1.json"),
        False,
    ),
    ("readiness_snapshot", Path("data/reports/readiness_snapshot_v2.json"), False),
)


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


def build_paper_ai_qlib_autotrain_activation_closeout_v1(
    *,
    project_root: str | Path,
    evidence_payloads: Mapping[str, Mapping[str, Any]] | None = None,
    write: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the activation closeout report in memory."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    if evidence_payloads is None:
        sources = load_sources(root)
        payloads = {source.source_id: source.payload for source in sources if source.payload}
    else:
        payloads = {str(key): dict(value) for key, value in evidence_payloads.items()}
        sources = sources_from_payloads(root, payloads)

    registry = payloads.get("registry_gate", {})
    producer = payloads.get("signal_producer", {})
    selector = payloads.get("selector_dryrun", {})
    summaries = build_summaries(payloads, sources)
    blockers = build_blockers(sources=sources, payloads=payloads, summaries=summaries)
    warnings = build_warnings(sources=sources, payloads=payloads)
    status, reason = decide_status(blockers)
    activation_status = reason
    safety = safety_flags()
    output_json = resolve(root, output_json_path, DEFAULT_REPORT_JSON)
    output_md = resolve(root, output_markdown_path, DEFAULT_REPORT_MD)
    activation_decision = build_activation_decision(reason=reason, blockers=blockers, warnings=warnings)

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "decision": DECISION_RESEARCH,
        "activation_closeout_status": activation_status,
        "research_only": True,
        "paper_only": True,
        "shadow_only": True,
        "dry_run_only": True,
        "read_only": True,
        "input_sources": [source.public_record() for source in sources],
        "lineage_hashes": build_lineage_hashes(payloads),
        "evidence_summary": build_evidence_summary(sources=sources, blockers=blockers, warnings=warnings),
        **summaries,
        "registry_gate_status": str(registry.get("registry_gate_status") or "missing"),
        "signal_producer_status": str(producer.get("status") or "missing"),
        "selector_dryrun_status": str(selector.get("selector_dryrun_status") or "missing"),
        "model_candidate_eligible_count": to_int(registry.get("eligible_candidate_count")),
        "actionable_signal_candidate_count": to_int(producer.get("actionable_signal_candidate_count")),
        "selected_signal_count": to_int(selector.get("selected_signal_count")),
        "activation_decision": activation_decision,
        **activation_decision,
        "blockers": blockers,
        "warnings": warnings,
        "non_goals": build_non_goals(),
        "output_paths": {"json": str(output_json), "markdown": str(output_md)},
        "write_requested": bool(write),
        "write_performed": False,
        **safety,
        "safety_flags": safety,
    }
    return report


def load_sources(project_root: Path) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for source_id, relative_path, required in SOURCE_SPECS:
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


def sources_from_payloads(project_root: Path, payloads: Mapping[str, Mapping[str, Any]]) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for source_id, relative_path, required in SOURCE_SPECS:
        payload = dict(payloads.get(source_id, {}))
        records.append(
            SourceRecord(
                source_id=source_id,
                relative_path=relative_path.as_posix(),
                path=(project_root / relative_path).resolve(),
                required=required,
                exists=bool(payload),
                sha256=stable_payload_hash(payload) if payload else None,
                load_error=None,
                payload=payload,
            )
        )
    return records


def build_summaries(
    payloads: Mapping[str, Mapping[str, Any]],
    sources: Sequence[SourceRecord],
) -> dict[str, dict[str, Any]]:
    source_map = {source.source_id: source for source in sources}
    registry = payloads.get("registry_gate", {})
    producer = payloads.get("signal_producer", {})
    selector = payloads.get("selector_dryrun", {})
    autotrain = payloads.get("autotrain_feedback_loop", {})
    qlib = payloads.get("qlib_trainer", {})
    ai_shadow = payloads.get("ai_shadow_quality_veto", {})
    drift = payloads.get("drift_monitor", {})
    execution_cost = payloads.get("execution_cost_gate", {})
    monte_carlo = payloads.get("monte_carlo_gate", {})
    readiness = payloads.get("readiness_snapshot", {})

    return {
        "registry_gate_summary": {
            "source_status": source_status(source_map, "registry_gate"),
            "status": registry.get("status", "missing"),
            "registry_gate_status": registry.get("registry_gate_status", "missing"),
            "candidate_count": to_int(registry.get("candidate_count")),
            "eligible_candidate_count": to_int(registry.get("eligible_candidate_count")),
            "blockers": list_of_strings(registry.get("blockers")),
        },
        "signal_producer_summary": {
            "source_status": source_status(source_map, "signal_producer"),
            "status": producer.get("status", "missing"),
            "reason": producer.get("reason", "missing"),
            "registry_gate_status": producer.get("registry_gate_status", "missing"),
            "signal_candidate_count": to_int(producer.get("signal_candidate_count")),
            "actionable_signal_candidate_count": to_int(producer.get("actionable_signal_candidate_count")),
            "blocked_signal_candidate_count": to_int(producer.get("blocked_signal_candidate_count")),
            "blockers": list_of_strings(producer.get("blockers")),
        },
        "selector_dryrun_summary": {
            "source_status": source_status(source_map, "selector_dryrun"),
            "status": selector.get("status", "missing"),
            "reason": selector.get("reason", "missing"),
            "selector_dryrun_status": selector.get("selector_dryrun_status", "missing"),
            "selected_signal_count": to_int(selector.get("selected_signal_count")),
            "rejected_signal_count": to_int(selector.get("rejected_signal_count")),
            "blockers": list_of_strings(selector.get("blockers")),
        },
        "autotrain_summary": generic_summary(source_map, "autotrain_feedback_loop", autotrain),
        "qlib_summary": generic_summary(source_map, "qlib_trainer", qlib),
        "ai_shadow_summary": generic_summary(source_map, "ai_shadow_quality_veto", ai_shadow),
        "drift_summary": generic_summary(source_map, "drift_monitor", drift),
        "execution_cost_summary": generic_summary(source_map, "execution_cost_gate", execution_cost),
        "monte_carlo_summary": generic_summary(source_map, "monte_carlo_gate", monte_carlo),
        "readiness_summary": generic_summary(source_map, "readiness_snapshot", readiness),
    }


def generic_summary(
    source_map: Mapping[str, SourceRecord],
    source_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "source_status": source_status(source_map, source_id),
        "status": payload.get("status", "missing"),
        "reason": payload.get("reason", "missing"),
        "decision": payload.get("decision"),
        "blockers": list_of_strings(payload.get("blockers")),
        "warnings": list_of_strings(payload.get("warnings")),
    }


def source_status(source_map: Mapping[str, SourceRecord], source_id: str) -> str:
    source = source_map.get(source_id)
    if source is None or not source.exists:
        return "missing"
    if source.load_error is not None:
        return "invalid"
    return "available"


def build_blockers(
    *,
    sources: Sequence[SourceRecord],
    payloads: Mapping[str, Mapping[str, Any]],
    summaries: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    blockers: list[str] = []
    for source in sources:
        if source.required and not source.exists:
            blockers.append(f"missing_required_source:{source.relative_path}")
        if source.required and source.load_error is not None:
            blockers.append(f"invalid_required_source:{source.relative_path}:{source.load_error}")

    registry_summary = summaries["registry_gate_summary"]
    producer_summary = summaries["signal_producer_summary"]
    selector_summary = summaries["selector_dryrun_summary"]

    if registry_summary.get("registry_gate_status") != "ok_research_review_only":
        blockers.append("registry_gate_not_ok_research_review_only")
    if to_int(registry_summary.get("eligible_candidate_count")) == 0:
        blockers.append("no_model_candidate_eligible")
    if producer_summary.get("status") != "ok":
        blockers.append(f"signal_producer_status_not_ok:{producer_summary.get('status')}")
    if to_int(producer_summary.get("actionable_signal_candidate_count")) == 0:
        blockers.append("no_actionable_signal_candidates")
    if selector_summary.get("selector_dryrun_status") not in {
        "ok_dryrun_observation_only",
        "observe_only",
    }:
        blockers.append(f"selector_dryrun_not_ok:{selector_summary.get('selector_dryrun_status')}")
    if to_int(selector_summary.get("selected_signal_count")) == 0:
        blockers.append("no_selected_signals")
    if any_payload_has_blocker(payloads, {"drift_gate_blocked", "blocked_drift_gate"}):
        blockers.append("drift_gate_blocked")
    if any_payload_has_blocker(payloads, {"execution_cost_gate_blocked", "blocked_execution_cost_gate"}):
        blockers.append("execution_cost_gate_blocked")
    if not payloads.get("monte_carlo_gate"):
        blockers.append("missing_monte_carlo_gate")
    if not payloads.get("readiness_snapshot"):
        blockers.append("missing_readiness_snapshot")
    return sorted_unique(blockers)


def build_warnings(
    *,
    sources: Sequence[SourceRecord],
    payloads: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    for source in sources:
        if not source.required and not source.exists:
            warnings.append(f"missing_optional_source:{source.relative_path}")
        if not source.required and source.load_error is not None:
            warnings.append(f"invalid_optional_source:{source.relative_path}:{source.load_error}")
    if not payloads.get("monte_carlo_gate"):
        warnings.append("monte_carlo_gate_missing_activation_not_allowed")
    if not payloads.get("readiness_snapshot"):
        warnings.append("readiness_snapshot_missing_activation_not_allowed")
    warnings.append("activation_closeout_only_no_runtime_activation")
    return sorted_unique(warnings)


def decide_status(blockers: Sequence[str]) -> tuple[str, str]:
    if any(blocker.startswith("missing_required_source:") for blocker in blockers):
        return "blocked", "missing_required_activation_sources"
    if any(blocker.startswith("invalid_required_source:") for blocker in blockers):
        return "blocked", "invalid_required_activation_sources"
    actionable_path_blockers = {
        "registry_gate_not_ok_research_review_only",
        "no_model_candidate_eligible",
        "no_actionable_signal_candidates",
        "no_selected_signals",
        "drift_gate_blocked",
        "execution_cost_gate_blocked",
    }
    if actionable_path_blockers.intersection(blockers):
        return "blocked", "blocked_no_actionable_ai_signal_path"
    if blockers:
        return "blocked", "blocked_missing_safety_evidence"
    return "ok", "activation_closeout_research_ready_only"


def build_activation_decision(
    *,
    reason: str,
    blockers: Sequence[str],
    warnings: Sequence[str],
) -> dict[str, Any]:
    return {
        "activation_allowed": False,
        "activation_level": "none",
        "activation_reason": reason,
        "can_enable_autotrain_runtime": False,
        "can_enable_paper_selector_runtime": False,
        "can_write_active_freqtrade_signals": False,
        "can_update_qlib_runtime": False,
        "can_update_ai_shadow_runtime": False,
        "can_promote_model": False,
        "can_promote_thresholds": False,
        "can_change_risk": False,
        "can_send_orders": False,
        "required_before_activation": [
            "registry_gate_status must be ok_research_review_only",
            "eligible_candidate_count must be greater than zero",
            "signal producer must have actionable signal candidates",
            "selector dry-run must select at least one observe-only signal",
            "drift and execution cost gates must be clear",
            "Monte Carlo and readiness evidence must be available and acceptable",
            "manual go/no-go must remain separate from this research closeout",
        ],
        "next_branch_recommendation": (
            "Keep activation blocked; remediate registry/drift/execution-cost blockers before any "
            "paper selector runtime adapter branch."
        ),
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
    }


def build_evidence_summary(
    *,
    sources: Sequence[SourceRecord],
    blockers: Sequence[str],
    warnings: Sequence[str],
) -> dict[str, Any]:
    return {
        "source_count": len(sources),
        "available_source_count": sum(1 for source in sources if source.exists and source.load_error is None),
        "missing_required_source_count": sum(1 for source in sources if source.required and not source.exists),
        "missing_optional_source_count": sum(1 for source in sources if not source.required and not source.exists),
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
    }


def build_lineage_hashes(payloads: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for source_id, payload in payloads.items():
        output[f"{source_id}_sha256"] = stable_payload_hash(payload)
        nested = payload.get("lineage_hashes")
        if isinstance(nested, Mapping):
            output.update({str(key): value for key, value in nested.items() if value})
    return output


def build_non_goals() -> list[str]:
    return [
        "No operational autotrain activation",
        "No paper selector runtime enablement",
        "No scheduler, cron, systemd timer, Windows task, or service creation",
        "No active_freqtrade_signals.json write",
        "No Freqtrade strategy or config mutation",
        "No RiskManager invocation",
        "No Qlib runtime update",
        "No IA Shadow runtime update",
        "No threshold application",
        "No registry write",
        "No model promotion",
        "No training execution",
        "No order submission",
        "No private exchange access",
        "No SQLite, parquet, model artifact, or runtime writes",
    ]


def safety_flags() -> dict[str, bool]:
    return {
        "research_only": True,
        "paper_only": True,
        "shadow_only": True,
        "dry_run_only": True,
        "read_only": True,
        "active_model_changed": False,
        "model_promotion_performed": False,
        "promotes_model": False,
        "runs_training": False,
        "runs_autotrain": False,
        "scheduler_registered": False,
        "creates_cron": False,
        "creates_systemd_timer": False,
        "creates_windows_task": False,
        "creates_service": False,
        "starts_service": False,
        "registry_write_performed": False,
        "model_registry_write_performed": False,
        "runtime_registry_write_performed": False,
        "candidate_registry_write_performed": False,
        "signal_runtime_write_performed": False,
        "active_signal_file_written": False,
        "qlib_runtime_updated": False,
        "ai_shadow_runtime_updated": False,
        "updates_ai_shadow_thresholds": False,
        "updates_qlib_runtime": False,
        "updates_freqtrade": False,
        "updates_freqtrade_strategy": False,
        "updates_freqtrade_config": False,
        "updates_risk_manager": False,
        "changes_risk": False,
        "changes_model": False,
        "changes_feature_contract": False,
        "changes_dataset_manifest": False,
        "writes_runtime": False,
        "writes_registry": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "writes_model_artifact": False,
        "writes_signal_file": False,
        "writes_active_freqtrade_signals": False,
        "writes_freqtrade_user_data": False,
        "operational_authority": False,
        "selector_operational_authority": False,
        "autotrain_operational_authority": False,
        "paper_selector_runtime_enabled": False,
        "autotrain_operational_activation": False,
        "freqtrade_strategy_changed": False,
        "freqtrade_config_changed": False,
        "release_allowed": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "exchange_private_access": False,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Paper AI/Qlib Autotrain Activation Closeout V1",
            "",
            "## Summary",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Activation closeout status: `{report.get('activation_closeout_status')}`",
            f"- Registry gate status: `{report.get('registry_gate_status')}`",
            f"- Model candidates eligible: `{report.get('model_candidate_eligible_count')}`",
            f"- Signal producer status: `{report.get('signal_producer_status')}`",
            f"- Actionable signal candidates: `{report.get('actionable_signal_candidate_count')}`",
            f"- Selector dry-run status: `{report.get('selector_dryrun_status')}`",
            f"- Selected signals: `{report.get('selected_signal_count')}`",
            "",
            "## Activation Decision",
            "",
            f"- Activation allowed: `{report.get('activation_allowed')}`",
            f"- Activation level: `{report.get('activation_level')}`",
            f"- Activation reason: `{report.get('activation_reason')}`",
            "",
            "## Blockers",
            "",
            *markdown_list(report.get("blockers", [])),
            "",
            "## Warnings",
            "",
            *markdown_list(report.get("warnings", [])),
            "",
            "## Safety Invariants",
            "",
            "- `decision=MANTER_EM_RESEARCH`",
            "- `autotrain_operational_activation=false`",
            "- `paper_selector_runtime_enabled=false`",
            "- `qlib_runtime_updated=false`",
            "- `ai_shadow_runtime_updated=false`",
            "- `active_signal_file_written=false`",
            "- `writes_active_freqtrade_signals=false`",
            "- `writes_signal_file=false`",
            "- `writes_runtime=false`",
            "- `sends_orders=false`",
            "",
            "## Non-Goals",
            "",
            *[f"- {item}" for item in list_of_strings(report.get("non_goals"))],
            "",
        ]
    )


def markdown_list(value: Any) -> list[str]:
    rows = list_of_strings(value)
    return [f"- `{row}`" for row in rows] if rows else ["- None"]


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n",
        encoding="utf-8",
    )


def any_payload_has_blocker(payloads: Mapping[str, Mapping[str, Any]], blocker_values: set[str]) -> bool:
    for payload in payloads.values():
        if payload_contains_any(payload, blocker_values):
            return True
    return False


def payload_contains_any(value: Any, needles: set[str]) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key) in needles:
                return True
            if payload_contains_any(nested, needles):
                return True
    elif isinstance(value, list):
        for item in value:
            if str(item) in needles:
                return True
            if payload_contains_any(item, needles):
                return True
    return False


def stable_payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=json_safe).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else root / path


def to_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def sorted_unique(values: Sequence[Any]) -> list[str]:
    return sorted({str(value) for value in values if str(value).strip()})


def list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def json_safe(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value
