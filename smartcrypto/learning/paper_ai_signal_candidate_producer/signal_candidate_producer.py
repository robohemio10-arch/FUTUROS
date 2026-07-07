"""Research-only paper AI signal candidate producer.

The producer turns registry-gate candidates into observational signal-candidate
evidence. It never creates operational Freqtrade signals, never writes runtime
state, never applies thresholds, and never uses realized outcome fields as
signal inputs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "paper_ai_signal_candidate_producer_v1"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"

DEFAULT_REPORT_JSON = Path("data/reports/paper_ai_signal_candidate_producer_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/paper_ai_signal_candidate_producer_v1.md")

SOURCE_SPECS: tuple[tuple[str, Path, bool], ...] = (
    ("registry_gate", Path("data/reports/paper_model_candidate_registry_gate_v1.json"), True),
    (
        "ensemble_threshold_calibration",
        Path("data/reports/qlib_shadow_ensemble_threshold_calibration_v1.json"),
        True,
    ),
    ("qlib_trainer", Path("data/reports/qlib_institutional_ranking_trainer_v1.json"), True),
    ("ai_shadow_quality_veto", Path("data/reports/ai_shadow_quality_veto_trainer_v1.json"), True),
    ("paper_autotrain_feedback_loop", Path("data/reports/paper_autotrain_feedback_loop_v1.json"), True),
    ("target_store", Path("data/reports/financial_label_target_store_v1.json"), True),
    ("drift_monitor", Path("data/reports/ai_qlib_drift_regime_monitor_v1.json"), True),
    ("execution_cost_gate", Path("data/reports/event_driven_backtest_execution_cost_gate_v1.json"), True),
)

FORBIDDEN_SIGNAL_PATTERNS = (
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


def build_paper_ai_signal_candidate_producer_v1(
    *,
    project_root: str | Path,
    evidence_payloads: Mapping[str, Mapping[str, Any]] | None = None,
    write: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build observational signal-candidate evidence in memory."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    if evidence_payloads is None:
        sources = load_sources(root)
        payloads = {source.source_id: source.payload for source in sources if source.payload}
    else:
        payloads = {str(key): dict(value) for key, value in evidence_payloads.items()}
        sources = sources_from_payloads(root, payloads)

    registry = payloads.get("registry_gate", {})
    registry_gate_status = str(registry.get("registry_gate_status") or "missing_registry_gate")
    registry_candidate_count = to_int(registry.get("candidate_count"))
    registry_eligible_candidate_count = to_int(registry.get("eligible_candidate_count"))
    source_blockers = input_source_blockers(sources)
    context_blockers = build_context_blockers(payloads, registry_gate_status, registry_eligible_candidate_count)
    signal_candidates = build_signal_candidates(registry, source_blockers + context_blockers)
    actionable_count = sum(1 for candidate in signal_candidates if candidate["signal_actionability"] != "blocked")
    blocked_count = len(signal_candidates) - actionable_count
    blockers = sorted_unique(source_blockers + context_blockers)
    warnings = build_warnings(signal_candidates)
    status, reason = decide_status(blockers, registry_gate_status, registry_eligible_candidate_count)
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
        "input_sources": [source.public_record() for source in sources],
        "lineage_hashes": build_lineage_hashes(payloads),
        "registry_gate_status": registry_gate_status,
        "registry_candidate_count": registry_candidate_count,
        "registry_eligible_candidate_count": registry_eligible_candidate_count,
        "signal_candidate_count": len(signal_candidates),
        "actionable_signal_candidate_count": actionable_count,
        "blocked_signal_candidate_count": blocked_count,
        "signal_candidates": signal_candidates,
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


def input_source_blockers(sources: Sequence[SourceRecord]) -> list[str]:
    blockers: list[str] = []
    for source in sources:
        if source.required and not source.exists:
            blockers.append(f"missing_required_source:{source.relative_path}")
        if source.required and source.load_error is not None:
            blockers.append(f"invalid_required_source:{source.relative_path}:{source.load_error}")
    return blockers


def build_context_blockers(
    payloads: Mapping[str, Mapping[str, Any]],
    registry_gate_status: str,
    registry_eligible_candidate_count: int,
) -> list[str]:
    blockers: list[str] = []
    if registry_gate_status != "ok_research_review_only":
        blockers.append("registry_gate_not_ok_research_review_only")
    if registry_eligible_candidate_count == 0:
        blockers.append("no_registry_eligible_candidates")
    drift_monitor = payloads.get("drift_monitor", {})
    if not drift_monitor:
        blockers.append("missing_drift_monitor")
    elif drift_monitor.get("status") == "blocked" or drift_monitor.get("blockers"):
        blockers.append("drift_gate_blocked")
    execution_cost_gate = payloads.get("execution_cost_gate", {})
    if not execution_cost_gate:
        blockers.append("missing_execution_cost_gate")
    elif execution_cost_gate.get("status") == "blocked" or execution_cost_gate.get("blockers"):
        blockers.append("execution_cost_gate_blocked")
    return sorted_unique(blockers)


def build_signal_candidates(registry: Mapping[str, Any], global_blockers: Sequence[str]) -> list[dict[str, Any]]:
    rows = list_of_mappings(registry.get("candidates"))
    candidates: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        source_candidate_id = str(row.get("candidate_id") or f"registry-candidate-{index}")
        blocked_reasons = sorted_unique(list_of_strings(row.get("blocked_reasons")) + list(global_blockers))
        source_gate_status = str(row.get("gate_status") or "unknown")
        if source_gate_status != "eligible_for_research_review":
            blocked_reasons.append(source_gate_status)
        signal_actionability = "research_observation_only" if not blocked_reasons else "blocked"
        signal_candidate_id = deterministic_id(
            [
                source_candidate_id,
                row.get("candidate_type"),
                row.get("source_id"),
                row.get("threshold"),
                signal_actionability,
            ]
        )
        side_scope = list_of_strings(row.get("side_scope"))
        signal_direction = infer_signal_direction(side_scope)
        metric_summary = sanitized_metric_summary(mapping_or_empty(row.get("score_metric_summary")))
        candidates.append(
            {
                "signal_candidate_id": signal_candidate_id,
                "source_candidate_id": source_candidate_id,
                "source_model_candidate_type": row.get("candidate_type"),
                "source_id": row.get("source_id"),
                "symbol_scope": list_of_strings(row.get("symbol_scope")),
                "side_scope": side_scope,
                "regime_scope": list_of_strings(row.get("regime_scope")),
                "threshold": row.get("threshold"),
                "ensemble_score_summary": metric_summary,
                "signal_direction": signal_direction,
                "signal_confidence": infer_signal_confidence(row, metric_summary),
                "evidence_status": row.get("evidence_status", "unknown"),
                "signal_actionability": signal_actionability,
                "blocked_reasons": sorted_unique(blocked_reasons),
                "eligible_for_research_observation": bool(signal_actionability == "research_observation_only"),
                "eligible_for_paper_selector": False,
                "eligible_for_freqtrade": False,
                "operational_authority": False,
                "sends_orders": False,
                "writes_runtime": False,
                "updates_freqtrade": False,
            }
        )
    return sorted(candidates, key=lambda item: str(item["signal_candidate_id"]))


def sanitized_metric_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in summary.items():
        if forbidden_metric_key(key):
            continue
        if isinstance(value, Mapping):
            nested = sanitized_metric_summary(value)
            if nested:
                output[str(key)] = nested
        elif not isinstance(value, list):
            output[str(key)] = value
    return output


def forbidden_metric_key(key: Any) -> bool:
    normalized = str(key).strip().lower()
    return any(pattern in normalized for pattern in FORBIDDEN_SIGNAL_PATTERNS)


def infer_signal_direction(side_scope: Sequence[str]) -> str:
    normalized = {str(side).lower() for side in side_scope}
    if normalized == {"long"}:
        return "long"
    if normalized == {"short"}:
        return "short"
    if normalized == {"long", "short"}:
        return "neutral"
    return "unknown"


def infer_signal_confidence(row: Mapping[str, Any], metric_summary: Mapping[str, Any]) -> float | None:
    threshold = to_float(row.get("threshold"))
    if threshold is not None:
        return threshold
    candidate = metric_summary.get("recommended_candidate")
    if isinstance(candidate, Mapping):
        return to_float(candidate.get("threshold"))
    return None


def decide_status(
    blockers: Sequence[str],
    registry_gate_status: str,
    registry_eligible_candidate_count: int,
) -> tuple[str, str]:
    if "no_registry_eligible_candidates" in blockers or registry_eligible_candidate_count == 0:
        return "blocked", "no_registry_eligible_candidates"
    if registry_gate_status != "ok_research_review_only":
        return "blocked", "registry_gate_not_ok_research_review_only"
    if blockers:
        return "blocked", "signal_candidate_producer_blocked"
    return "ok", "signal_candidates_research_observation_only"


def build_warnings(signal_candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    if signal_candidates and all(candidate.get("signal_actionability") == "blocked" for candidate in signal_candidates):
        return ["all_signal_candidates_blocked"]
    if signal_candidates:
        return ["signal_candidates_are_research_observation_only"]
    return []


def build_lineage_hashes(payloads: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for payload in payloads.values():
        for key in (
            "dataset_hash",
            "feature_contract_hash",
            "target_store_hash",
            "split_engine_hash",
            "walkforward_split_engine_hash",
            "dependency_contract_hash",
        ):
            if payload.get(key):
                output[key] = payload[key]
        nested = payload.get("lineage_hashes")
        if isinstance(nested, Mapping):
            output.update({str(key): value for key, value in nested.items() if value})
    return output


def build_non_goals() -> list[str]:
    return [
        "No operational signal file generation",
        "No Freqtrade signal production",
        "No paper selector activation",
        "No runtime threshold application",
        "No active model promotion",
        "No registry write",
        "No Qlib runtime update",
        "No IA Shadow runtime update",
        "No RiskManager or Freqtrade changes",
        "No order submission",
        "No private exchange access",
        "No SQLite, parquet, model artifact, or runtime writes",
    ]


def safety_flags() -> dict[str, bool]:
    return {
        "research_only": True,
        "paper_only": True,
        "shadow_only": True,
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
        "qlib_runtime_updated": False,
        "ai_shadow_runtime_updated": False,
        "updates_ai_shadow_thresholds": False,
        "updates_qlib_runtime": False,
        "updates_freqtrade": False,
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
        "operational_authority": False,
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
            "# Paper AI Signal Candidate Producer V1",
            "",
            "## Executive Summary",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Registry gate status: `{report.get('registry_gate_status')}`",
            f"- Registry eligible candidates: `{report.get('registry_eligible_candidate_count')}`",
            f"- Signal candidates: `{report.get('signal_candidate_count')}`",
            f"- Actionable signal candidates: `{report.get('actionable_signal_candidate_count')}`",
            "",
            "## Signal Candidates",
            "",
            *markdown_signal_candidates(report.get("signal_candidates", [])),
            "",
            "## Blockers",
            "",
            *markdown_list(report.get("blockers", [])),
            "",
            "## Safety Invariants",
            "",
            "- `decision=MANTER_EM_RESEARCH`",
            "- `eligible_for_paper_selector=false`",
            "- `eligible_for_freqtrade=false`",
            "- `writes_signal_file=false`",
            "- `writes_runtime=false`",
            "- `updates_freqtrade=false`",
            "- `sends_orders=false`",
            "- `exchange_private_access=false`",
            "",
            "## Non-Goals",
            "",
            *[f"- {item}" for item in list_of_strings(report.get("non_goals"))],
            "",
        ]
    )


def markdown_signal_candidates(value: Any) -> list[str]:
    rows = list_of_mappings(value)
    if not rows:
        return ["- No signal candidates produced."]
    return [
        (
            f"- `{row.get('signal_candidate_id')}`: actionability=`{row.get('signal_actionability')}`, "
            f"direction=`{row.get('signal_direction')}`, "
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


def deterministic_id(parts: Sequence[Any]) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return f"signal_candidate_{digest[:16]}"


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


def to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def sorted_unique(values: Sequence[Any]) -> list[str]:
    return sorted({str(value) for value in values if str(value).strip()})


def mapping_or_empty(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


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
