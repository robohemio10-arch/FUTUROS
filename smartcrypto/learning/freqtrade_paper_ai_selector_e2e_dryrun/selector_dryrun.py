"""Research-only dry-run selector for paper AI signal candidates.

This module consumes already-materialized evidence from the paper AI signal
candidate producer and simulates selector decisions without importing or
touching Freqtrade, RiskManager, Qlib runtime, IA Shadow runtime, or any active
signal file.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "freqtrade_paper_ai_selector_e2e_dryrun_v1"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"

DEFAULT_REPORT_JSON = Path("data/reports/freqtrade_paper_ai_selector_e2e_dryrun_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/freqtrade_paper_ai_selector_e2e_dryrun_v1.md")

PRIMARY_SOURCE_ID = "signal_candidate_report"
PRIMARY_SOURCE_PATH = Path("data/reports/paper_ai_signal_candidate_producer_v1.json")

SOURCE_SPECS: tuple[tuple[str, Path, bool], ...] = (
    (PRIMARY_SOURCE_ID, PRIMARY_SOURCE_PATH, True),
    ("registry_gate", Path("data/reports/paper_model_candidate_registry_gate_v1.json"), False),
    (
        "selector_integration",
        Path("data/reports/freqtrade_paper_ai_selector_integration_v1.json"),
        False,
    ),
    ("runtime_safety", Path("data/reports/runtime_safety_audit_config.json"), False),
    ("readiness_snapshot", Path("data/reports/readiness_snapshot_v2.json"), False),
)

FORBIDDEN_OPERATIONAL_FIELDS = (
    "label",
    "target",
    "outcome",
    "pnl",
    "profit",
    "win_loss",
    "future_return",
    "future_ret",
    "expected_value",
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


def build_freqtrade_paper_ai_selector_e2e_dryrun_v1(
    *,
    project_root: str | Path,
    evidence_payloads: Mapping[str, Mapping[str, Any]] | None = None,
    write: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the selector dry-run report in memory."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    if evidence_payloads is None:
        sources = load_sources(root)
        payloads = {source.source_id: source.payload for source in sources if source.payload}
    else:
        payloads = {str(key): dict(value) for key, value in evidence_payloads.items()}
        sources = sources_from_payloads(root, payloads)

    producer = payloads.get(PRIMARY_SOURCE_ID, {})
    source_blockers = input_source_blockers(sources)
    source_warnings = input_source_warnings(sources)
    signal_candidates = list_of_mappings(producer.get("signal_candidates"))
    selector_decisions = build_selector_decisions(signal_candidates)
    selected_signal_count = sum(
        1 for decision in selector_decisions if decision["selector_action"] == "dryrun_observe_only"
    )
    rejected_signal_count = sum(1 for decision in selector_decisions if decision["selector_action"] == "reject")
    producer_status = str(producer.get("status") or "missing")
    producer_reason = str(producer.get("reason") or "missing_signal_candidate_report")
    signal_candidate_count = to_int(producer.get("signal_candidate_count"))
    if signal_candidate_count == 0 and signal_candidates:
        signal_candidate_count = len(signal_candidates)
    actionable_signal_candidate_count = to_int(producer.get("actionable_signal_candidate_count"))
    blocked_signal_candidate_count = to_int(producer.get("blocked_signal_candidate_count"))
    if blocked_signal_candidate_count == 0 and signal_candidates:
        blocked_signal_candidate_count = len(signal_candidates) - actionable_signal_candidate_count

    context_blockers = build_context_blockers(
        producer=producer,
        producer_status=producer_status,
        producer_reason=producer_reason,
        actionable_signal_candidate_count=actionable_signal_candidate_count,
        selector_decisions=selector_decisions,
    )
    blockers = sorted_unique(source_blockers + context_blockers)
    status, reason, selector_status = decide_status(
        blockers=blockers,
        producer_status=producer_status,
        producer_reason=producer_reason,
        actionable_signal_candidate_count=actionable_signal_candidate_count,
    )
    safety = safety_flags()
    output_json = resolve(root, output_json_path, DEFAULT_REPORT_JSON)
    output_md = resolve(root, output_markdown_path, DEFAULT_REPORT_MD)

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "decision": DECISION_RESEARCH,
        "research_only": True,
        "paper_only": True,
        "shadow_only": True,
        "dry_run_only": True,
        "read_only": True,
        "input_sources": [source.public_record() for source in sources],
        "lineage_hashes": build_lineage_hashes(payloads),
        "producer_status": producer_status,
        "producer_reason": producer_reason,
        "signal_candidate_count": signal_candidate_count,
        "actionable_signal_candidate_count": actionable_signal_candidate_count,
        "blocked_signal_candidate_count": blocked_signal_candidate_count,
        "selected_signal_count": selected_signal_count,
        "rejected_signal_count": rejected_signal_count,
        "selector_dryrun_status": selector_status,
        "selector_decisions": selector_decisions,
        "blockers": blockers,
        "warnings": sorted_unique(source_warnings + build_warnings(selector_decisions, producer)),
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


def input_source_blockers(sources: Sequence[SourceRecord]) -> list[str]:
    blockers: list[str] = []
    for source in sources:
        if source.required and not source.exists:
            blockers.append("missing_signal_candidate_report")
        if source.required and source.load_error is not None:
            blockers.append(f"invalid_signal_candidate_report:{source.load_error}")
    return blockers


def input_source_warnings(sources: Sequence[SourceRecord]) -> list[str]:
    warnings: list[str] = []
    for source in sources:
        if not source.required and not source.exists:
            warnings.append(f"missing_optional_source:{source.relative_path}")
        if not source.required and source.load_error is not None:
            warnings.append(f"invalid_optional_source:{source.relative_path}:{source.load_error}")
    return warnings


def build_context_blockers(
    *,
    producer: Mapping[str, Any],
    producer_status: str,
    producer_reason: str,
    actionable_signal_candidate_count: int,
    selector_decisions: Sequence[Mapping[str, Any]],
) -> list[str]:
    blockers: list[str] = []
    if producer:
        if producer_status != "ok":
            blockers.append(f"producer_status_not_ok:{producer_status}")
        if producer_reason == "no_registry_eligible_candidates":
            blockers.append("producer_no_registry_eligible_candidates")
        if actionable_signal_candidate_count == 0:
            blockers.append("no_actionable_signal_candidates")
        if has_blocker(producer, "drift_gate_blocked") or has_blocker(producer, "blocked_drift_gate"):
            blockers.append("drift_gate_blocked")
        if has_blocker(producer, "execution_cost_gate_blocked") or has_blocker(producer, "blocked_execution_cost_gate"):
            blockers.append("execution_cost_gate_blocked")
    if selector_decisions and all(decision.get("selector_action") == "reject" for decision in selector_decisions):
        blockers.append("all_selector_decisions_rejected")
    return sorted_unique(blockers)


def build_selector_decisions(signal_candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for index, candidate in enumerate(signal_candidates):
        source_candidate_id = str(candidate.get("source_candidate_id") or "")
        signal_candidate_id = str(candidate.get("signal_candidate_id") or f"signal-candidate-{index}")
        blocked_reasons = selector_blocked_reasons(candidate)
        selector_action = "reject" if blocked_reasons else "dryrun_observe_only"
        selector_reason = "candidate_not_eligible_for_freqtrade" if blocked_reasons else "dryrun_observe_only"
        decisions.append(
            {
                "selector_decision_id": deterministic_id(
                    ["selector", signal_candidate_id, source_candidate_id, selector_action]
                ),
                "source_signal_candidate_id": signal_candidate_id,
                "source_candidate_id": source_candidate_id or None,
                "symbol_scope": list_of_strings(candidate.get("symbol_scope")),
                "side_scope": list_of_strings(candidate.get("side_scope")),
                "regime_scope": list_of_strings(candidate.get("regime_scope")),
                "signal_direction": str(candidate.get("signal_direction") or "unknown"),
                "signal_confidence": safe_signal_confidence(candidate.get("signal_confidence")),
                "source_signal_actionability": str(candidate.get("signal_actionability") or "unknown"),
                "selector_action": selector_action,
                "selector_reason": selector_reason,
                "blocked_reasons": sorted_unique(blocked_reasons),
                "eligible_for_paper_selector": False,
                "eligible_for_freqtrade": False,
                "would_write_active_signal": False,
                "active_signal_payload": None,
                "sends_orders": False,
                "writes_runtime": False,
                "updates_freqtrade": False,
                "changes_risk": False,
            }
        )
    return sorted(decisions, key=lambda row: str(row["selector_decision_id"]))


def selector_blocked_reasons(candidate: Mapping[str, Any]) -> list[str]:
    reasons = list_of_strings(candidate.get("blocked_reasons"))
    actionability = str(candidate.get("signal_actionability") or "unknown")
    if actionability == "blocked":
        reasons.append("source_signal_actionability_blocked")
    if not bool(candidate.get("eligible_for_paper_selector")):
        reasons.append("not_eligible_for_paper_selector")
    if not bool(candidate.get("eligible_for_freqtrade")):
        reasons.append("not_eligible_for_freqtrade")
    if has_forbidden_operational_field(candidate):
        reasons.append("forbidden_operational_field_present")
    if "drift_gate_blocked" in reasons or "blocked_drift_gate" in reasons:
        reasons.append("drift_gate_blocked")
    if "execution_cost_gate_blocked" in reasons or "blocked_execution_cost_gate" in reasons:
        reasons.append("execution_cost_gate_blocked")
    return sorted_unique(reasons)


def has_forbidden_operational_field(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized_key = str(key).strip().lower()
            if any(pattern in normalized_key for pattern in FORBIDDEN_OPERATIONAL_FIELDS):
                return True
            if has_forbidden_operational_field(nested):
                return True
    elif isinstance(value, list):
        return any(has_forbidden_operational_field(item) for item in value)
    return False


def decide_status(
    *,
    blockers: Sequence[str],
    producer_status: str,
    producer_reason: str,
    actionable_signal_candidate_count: int,
) -> tuple[str, str, str]:
    if "missing_signal_candidate_report" in blockers:
        return "blocked", "missing_signal_candidate_report", "blocked_missing_signal_candidate_report"
    if actionable_signal_candidate_count == 0 or "no_actionable_signal_candidates" in blockers:
        return "blocked", "no_actionable_signal_candidates", "blocked_no_actionable_candidates"
    if producer_status != "ok" or producer_reason == "no_registry_eligible_candidates":
        return "blocked", "producer_not_actionable", "blocked_no_actionable_candidates"
    if blockers:
        return "blocked", "selector_dryrun_blocked", "blocked_no_actionable_candidates"
    return "ok", "selector_dryrun_observe_only", "observe_only"


def build_warnings(selector_decisions: Sequence[Mapping[str, Any]], producer: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    if selector_decisions and all(decision.get("selector_action") == "reject" for decision in selector_decisions):
        warnings.append("all_signal_candidates_rejected")
    if not selector_decisions and producer:
        warnings.append("no_selector_decisions_materialized")
    warnings.append("dry_run_only_no_runtime_integration")
    return warnings


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
        "No paper selector runtime activation",
        "No Freqtrade strategy or config mutation",
        "No active signal file generation",
        "No active_freqtrade_signals.json write",
        "No RiskManager invocation",
        "No Qlib runtime update",
        "No IA Shadow runtime update",
        "No registry write",
        "No model promotion",
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
        "operational_authority": False,
        "selector_operational_authority": False,
        "paper_selector_runtime_enabled": False,
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
            "# Freqtrade Paper AI Selector E2E Dry-Run V1",
            "",
            "## Summary",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Producer status: `{report.get('producer_status')}`",
            f"- Producer reason: `{report.get('producer_reason')}`",
            f"- Signal candidates: `{report.get('signal_candidate_count')}`",
            f"- Actionable signal candidates: `{report.get('actionable_signal_candidate_count')}`",
            f"- Selected signals: `{report.get('selected_signal_count')}`",
            f"- Rejected signals: `{report.get('rejected_signal_count')}`",
            "",
            "## Selector Decisions",
            "",
            *markdown_selector_decisions(report.get("selector_decisions", [])),
            "",
            "## Blockers",
            "",
            *markdown_list(report.get("blockers", [])),
            "",
            "## Safety Invariants",
            "",
            "- `decision=MANTER_EM_RESEARCH`",
            "- `paper_selector_runtime_enabled=false`",
            "- `freqtrade_strategy_changed=false`",
            "- `freqtrade_config_changed=false`",
            "- `active_signal_file_written=false`",
            "- `writes_active_freqtrade_signals=false`",
            "- `writes_signal_file=false`",
            "- `sends_orders=false`",
            "- `exchange_private_access=false`",
            "",
            "## Non-Goals",
            "",
            *[f"- {item}" for item in list_of_strings(report.get("non_goals"))],
            "",
        ]
    )


def markdown_selector_decisions(value: Any) -> list[str]:
    rows = list_of_mappings(value)
    if not rows:
        return ["- No selector decisions materialized."]
    return [
        (
            f"- `{row.get('selector_decision_id')}`: action=`{row.get('selector_action')}`, "
            f"reason=`{row.get('selector_reason')}`, "
            f"eligible_for_freqtrade=`{row.get('eligible_for_freqtrade')}`"
        )
        for row in rows
    ]


def markdown_list(value: Any) -> list[str]:
    rows = list_of_strings(value)
    return [f"- `{row}`" for row in rows] if rows else ["- None"]


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n",
        encoding="utf-8",
    )


def has_blocker(payload: Mapping[str, Any], blocker: str) -> bool:
    values: list[str] = []
    for key in ("blockers", "blocked_reasons"):
        values.extend(list_of_strings(payload.get(key)))
    for candidate in list_of_mappings(payload.get("signal_candidates")):
        values.extend(list_of_strings(candidate.get("blocked_reasons")))
    return blocker in set(values)


def deterministic_id(parts: Sequence[Any]) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return f"selector_decision_{digest[:16]}"


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


def safe_signal_confidence(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


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
