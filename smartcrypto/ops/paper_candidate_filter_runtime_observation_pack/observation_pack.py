"""Read-only observation pack for paper-candidate filter runtime evidence."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from smartcrypto.runtime.integrity_traceability_v2 import (
    atomic_write_json,
    atomic_write_text,
)

SCHEMA_VERSION = "paper_candidate_filter_runtime_observation_pack_v1"
DEFAULT_AB_TEST_REPORT = Path("data/reports/paper_only_candidate_strategy_ab_test_v1.json")
DEFAULT_DAILY_IMPACT_REPORT = Path("data/reports/paper_shadow_observation_daily_impact_report_v1.json")
DEFAULT_CLOSED_TRADES_CONTRACT = Path("data/reports/paper_closed_trades_readonly_source_contract_v1.json")
DEFAULT_OUTPUT_REPORT = Path("data/reports/paper_candidate_filter_runtime_observation_pack_v1.json")
DEFAULT_MARKDOWN_REPORT = Path("data/reports/paper_candidate_filter_runtime_observation_pack_v1.md")
DEFAULT_DECISION_EVENT_CANDIDATES = (
    Path("data/reports/paper_candidate_filter_runtime_wiring_v1.json"),
    Path("data/reports/paper_candidate_filter_runtime_wiring_report_v1.json"),
    Path("data/reports/paper_candidate_filter_runtime_observation_events_v1.json"),
    Path("data/reports/phase13_signal_producer_report.json"),
)

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "live_behavior_changed": False,
    "canary_behavior_changed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "changes_model": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_parquet": False,
}

FORBIDDEN_NEXT_ACTIONS = [
    "habilitar live",
    "habilitar canary",
    "enviar ordem",
    "acessar exchange privada",
    "alterar RiskManager",
    "alterar Freqtrade",
    "alterar Qlib runtime",
    "alterar IA Shadow runtime",
    "alterar modelos",
    "alterar active signals",
    "escrever data/runtime",
    "escrever SQLite",
    "escrever Parquet operacional",
]


@dataclass(frozen=True)
class LoadedObservationInputs:
    ab_test_report: dict[str, Any] | None
    daily_impact_report: dict[str, Any] | None
    closed_trades_contract: dict[str, Any] | None
    decision_events: list[dict[str, Any]]
    input_mode: str
    source_status: str
    source_reason: str
    source_paths: dict[str, str | list[str] | None]
    source_sha256: dict[str, str | list[str | None] | None]
    evidence_gaps: list[str]


def build_paper_candidate_filter_runtime_observation_pack(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    ab_test_report: str | Path | None = None,
    daily_impact_report: str | Path | None = None,
    closed_trades_contract: str | Path | None = None,
    decision_event_paths: Sequence[str | Path] | None = None,
    ab_test_payload: Mapping[str, Any] | None = None,
    daily_impact_payload: Mapping[str, Any] | None = None,
    closed_trades_contract_payload: Mapping[str, Any] | None = None,
    decision_event_payloads: Sequence[Mapping[str, Any]] | None = None,
    write: bool = False,
    no_write: bool = True,
    output_report: str | Path | None = None,
    markdown_report: str | Path | None = None,
) -> dict[str, Any]:
    """Build the post-wiring observation pack without operational authority."""

    root = Path(project_root).resolve()
    write_requested = bool(write and not no_write)
    loaded = load_observation_inputs(
        project_root=root,
        allow_runtime_read=allow_runtime_read,
        ab_test_report=ab_test_report,
        daily_impact_report=daily_impact_report,
        closed_trades_contract=closed_trades_contract,
        decision_event_paths=decision_event_paths,
        ab_test_payload=ab_test_payload,
        daily_impact_payload=daily_impact_payload,
        closed_trades_contract_payload=closed_trades_contract_payload,
        decision_event_payloads=decision_event_payloads,
    )
    metrics = compute_observation_metrics(
        ab_test_report=loaded.ab_test_report,
        daily_impact_report=loaded.daily_impact_report,
        closed_trades_contract=loaded.closed_trades_contract,
        decision_events=loaded.decision_events,
    )
    status, reason, observation_status = _status_reason(loaded, metrics)
    report: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _utc_now_iso(),
        "input_mode": loaded.input_mode,
        "observation_status": observation_status,
        "observation_window": metrics["observation_window"],
        "runtime_wiring_status": metrics["runtime_wiring_status"],
        "paper_candidate_filter_called": metrics["paper_candidate_filter_called"],
        "decision_events_loaded": bool(loaded.decision_events),
        "decision_event_count": metrics["decision_event_count"],
        "block_event_count": metrics["block_event_count"],
        "allow_event_count": metrics["allow_event_count"],
        "ethusdt_long_block_event_count": metrics["ethusdt_long_block_event_count"],
        "ethusdt_short_block_event_count": metrics["ethusdt_short_block_event_count"],
        "btcusdt_long_allow_event_count": metrics["btcusdt_long_allow_event_count"],
        "btcusdt_short_allow_event_count": metrics["btcusdt_short_allow_event_count"],
        "post_wiring_closed_trade_count": metrics["post_wiring_closed_trade_count"],
        "post_wiring_ethusdt_trade_count": metrics["post_wiring_ethusdt_trade_count"],
        "post_wiring_btcusdt_trade_count": metrics["post_wiring_btcusdt_trade_count"],
        "baseline_trade_count": metrics["baseline_trade_count"],
        "baseline_blocked_trade_count": metrics["baseline_blocked_trade_count"],
        "baseline_allowed_trade_count": metrics["baseline_allowed_trade_count"],
        "baseline_net_pnl": metrics["baseline_net_pnl"],
        "candidate_expected_net_pnl": metrics["candidate_expected_net_pnl"],
        "candidate_expected_delta": metrics["candidate_expected_delta"],
        "evidence_gaps": sorted(set([*loaded.evidence_gaps, *metrics["evidence_gaps"]])),
        "recommended_next_action": _recommended_next_action(bool(loaded.decision_events), loaded.source_status),
        "forbidden_next_actions": list(FORBIDDEN_NEXT_ACTIONS),
        "source_status": loaded.source_status,
        "source_reason": loaded.source_reason,
        "source_paths": loaded.source_paths,
        "source_sha256": loaded.source_sha256,
        "decision_event_sample": loaded.decision_events[:20],
        "safety_flags": dict(SAFETY_FLAGS),
        "write_requested": write_requested,
        "write_performed": False,
        "output_path": None,
        "markdown_output_path": None,
        "validation_errors": [],
        **SAFETY_FLAGS,
    }
    report["validation_errors"] = validate_observation_pack(report)
    if write_requested:
        output_path = _resolve_output_path(root, output_report, DEFAULT_OUTPUT_REPORT)
        markdown_path = _resolve_output_path(root, markdown_report, DEFAULT_MARKDOWN_REPORT)
        output_error = _validate_output_path(root, output_path, suffix=".json")
        markdown_error = _validate_output_path(root, markdown_path, suffix=".md")
        if output_error is not None or markdown_error is not None:
            report["status"] = "blocked"
            report["reason"] = output_error or markdown_error
            report["validation_errors"] = validate_observation_pack(report)
            return report
        atomic_write_json(output_path, report)
        atomic_write_text(markdown_path, render_markdown_report(report))
        report["write_performed"] = True
        report["output_path"] = _project_relative(output_path, root)
        report["markdown_output_path"] = _project_relative(markdown_path, root)
    return report


def load_observation_inputs(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    ab_test_report: str | Path | None = None,
    daily_impact_report: str | Path | None = None,
    closed_trades_contract: str | Path | None = None,
    decision_event_paths: Sequence[str | Path] | None = None,
    ab_test_payload: Mapping[str, Any] | None = None,
    daily_impact_payload: Mapping[str, Any] | None = None,
    closed_trades_contract_payload: Mapping[str, Any] | None = None,
    decision_event_payloads: Sequence[Mapping[str, Any]] | None = None,
) -> LoadedObservationInputs:
    root = Path(project_root).resolve()
    if ab_test_payload is not None or decision_event_payloads is not None:
        events = _events_from_payloads(decision_event_payloads or [])
        return LoadedObservationInputs(
            ab_test_report=dict(ab_test_payload) if ab_test_payload is not None else None,
            daily_impact_report=dict(daily_impact_payload) if daily_impact_payload is not None else None,
            closed_trades_contract=dict(closed_trades_contract_payload) if closed_trades_contract_payload is not None else None,
            decision_events=events,
            input_mode="in_memory_observation_inputs",
            source_status="ok",
            source_reason="in_memory_inputs_supplied",
            source_paths={"ab_test_report": None, "daily_impact_report": None, "closed_trades_contract": None, "decision_event_paths": []},
            source_sha256={"ab_test_report": None, "daily_impact_report": None, "closed_trades_contract": None, "decision_event_paths": []},
            evidence_gaps=[] if events else ["no_decision_events_loaded"],
        )
    if not allow_runtime_read:
        return LoadedObservationInputs(
            ab_test_report=None,
            daily_impact_report=None,
            closed_trades_contract=None,
            decision_events=[],
            input_mode="no_runtime_rows_loaded",
            source_status="blocked",
            source_reason="runtime_read_not_allowed_by_default",
            source_paths={"ab_test_report": None, "daily_impact_report": None, "closed_trades_contract": None, "decision_event_paths": []},
            source_sha256={"ab_test_report": None, "daily_impact_report": None, "closed_trades_contract": None, "decision_event_paths": []},
            evidence_gaps=["runtime_read_not_allowed"],
        )

    ab_path = _resolve_path(root, ab_test_report, DEFAULT_AB_TEST_REPORT)
    impact_path = _resolve_path(root, daily_impact_report, DEFAULT_DAILY_IMPACT_REPORT)
    contract_path = _resolve_path(root, closed_trades_contract, DEFAULT_CLOSED_TRADES_CONTRACT)
    event_paths = [_resolve_path(root, path, path) for path in (decision_event_paths or DEFAULT_DECISION_EVENT_CANDIDATES)]
    source_paths: dict[str, str | list[str] | None] = {
        "ab_test_report": _project_relative(ab_path, root),
        "daily_impact_report": _project_relative(impact_path, root),
        "closed_trades_contract": _project_relative(contract_path, root),
        "decision_event_paths": [_project_relative(path, root) for path in event_paths],
    }
    source_sha256: dict[str, str | list[str | None] | None] = {
        "ab_test_report": _sha256_file(ab_path),
        "daily_impact_report": _sha256_file(impact_path),
        "closed_trades_contract": _sha256_file(contract_path),
        "decision_event_paths": [_sha256_file(path) for path in event_paths],
    }
    evidence_gaps: list[str] = []
    if not ab_path.exists():
        evidence_gaps.append("missing_ab_test_report")
    ab_payload = _read_optional_json(ab_path)
    impact_payload = _read_optional_json(impact_path)
    contract_payload = _read_optional_json(contract_path)
    decision_events: list[dict[str, Any]] = []
    for path in event_paths:
        payload = _read_optional_json(path)
        if payload is None:
            continue
        decision_events.extend(extract_decision_events(payload))
    if not decision_events:
        evidence_gaps.append("no_post_wiring_runtime_observation_events_found")
    if ab_payload is None:
        return LoadedObservationInputs(
            ab_test_report=None,
            daily_impact_report=impact_payload,
            closed_trades_contract=contract_payload,
            decision_events=decision_events,
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason="missing_or_invalid_ab_test_report",
            source_paths=source_paths,
            source_sha256=source_sha256,
            evidence_gaps=evidence_gaps,
        )
    return LoadedObservationInputs(
        ab_test_report=ab_payload,
        daily_impact_report=impact_payload,
        closed_trades_contract=contract_payload,
        decision_events=decision_events,
        input_mode="runtime_read_requested",
        source_status="ok",
        source_reason="sources_loaded_read_only",
        source_paths=source_paths,
        source_sha256=source_sha256,
        evidence_gaps=evidence_gaps,
    )


def compute_observation_metrics(
    *,
    ab_test_report: Mapping[str, Any] | None,
    daily_impact_report: Mapping[str, Any] | None,
    closed_trades_contract: Mapping[str, Any] | None,
    decision_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    events = [dict(event) for event in decision_events]
    block_events = [event for event in events if _decision(event) == "BLOCK"]
    allow_events = [event for event in events if _decision(event) == "ALLOW"]
    symbols = [_symbol(event) for event in events]
    timestamps = sorted(_event_time(event) for event in events if _event_time(event) is not None)
    baseline = ab_test_report.get("baseline_summary") if isinstance(ab_test_report, Mapping) else {}
    candidate = ab_test_report.get("candidate_summary") if isinstance(ab_test_report, Mapping) else {}
    impact_summary = daily_impact_report.get("impact_summary") if isinstance(daily_impact_report, Mapping) else {}
    closed_count = _safe_int(_first_non_none(
        closed_trades_contract.get("normalized_closed_trade_count") if isinstance(closed_trades_contract, Mapping) else None,
        daily_impact_report.get("total_closed_trades") if isinstance(daily_impact_report, Mapping) else None,
    ))
    evidence_gaps: list[str] = []
    if not events:
        evidence_gaps.append("no_post_wiring_runtime_observation_events_found")
    if not isinstance(ab_test_report, Mapping):
        evidence_gaps.append("missing_ab_test_report")
    return {
        "observation_window": {
            "started_at_utc": timestamps[0] if timestamps else None,
            "ended_at_utc": timestamps[-1] if timestamps else None,
        },
        "runtime_wiring_status": _runtime_wiring_status(events),
        "paper_candidate_filter_called": any(_truthy(event.get("paper_candidate_filter_called")) for event in events) or bool(events),
        "decision_event_count": len(events),
        "block_event_count": len(block_events),
        "allow_event_count": len(allow_events),
        "ethusdt_long_block_event_count": _count_events(block_events, "ETHUSDT", "long"),
        "ethusdt_short_block_event_count": _count_events(block_events, "ETHUSDT", "short"),
        "btcusdt_long_allow_event_count": _count_events(allow_events, "BTCUSDT", "long"),
        "btcusdt_short_allow_event_count": _count_events(allow_events, "BTCUSDT", "short"),
        "post_wiring_closed_trade_count": closed_count if events else 0,
        "post_wiring_ethusdt_trade_count": sum(1 for symbol in symbols if symbol == "ETHUSDT"),
        "post_wiring_btcusdt_trade_count": sum(1 for symbol in symbols if symbol == "BTCUSDT"),
        "baseline_trade_count": _safe_int(_mapping_get(baseline, "baseline_trade_count")),
        "baseline_blocked_trade_count": _safe_int(_mapping_get(candidate, "blocked_trade_count")),
        "baseline_allowed_trade_count": _safe_int(_mapping_get(candidate, "allowed_trade_count")),
        "baseline_net_pnl": _safe_float(_mapping_get(baseline, "baseline_net_pnl")),
        "candidate_expected_net_pnl": _safe_float(_first_non_none(
            _mapping_get(candidate, "candidate_allowed_net_pnl"),
            _mapping_get(impact_summary, "allowed_net_pnl"),
        )),
        "candidate_expected_delta": _safe_float(_mapping_get(candidate, "candidate_vs_baseline_net_pnl_delta")),
        "evidence_gaps": evidence_gaps,
    }


def extract_decision_events(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for key in ("decision_events", "decision_log", "decision_log_sample", "sample_decisions"):
        value = payload.get(key)
        if isinstance(value, list):
            events.extend(dict(item) for item in value if isinstance(item, Mapping) and _has_decision(item))
    nested = payload.get("paper_candidate_filter_runtime_wiring")
    if isinstance(nested, Mapping):
        events.extend(extract_decision_events(nested))
    return [_normalize_event(event) for event in events]


def validate_observation_pack(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    required_fields = (
        "status",
        "reason",
        "schema_version",
        "generated_at_utc",
        "input_mode",
        "observation_status",
        "runtime_wiring_status",
        "paper_candidate_filter_called",
        "decision_events_loaded",
        "decision_event_count",
        "block_event_count",
        "allow_event_count",
        "evidence_gaps",
        "recommended_next_action",
        "forbidden_next_actions",
        "safety_flags",
        "write_performed",
    )
    for field in required_fields:
        if field not in report:
            errors.append(f"missing_required_field:{field}")
    for key, expected in SAFETY_FLAGS.items():
        if report.get(key) is not expected:
            errors.append(f"{key}_must_be_{str(expected).lower()}")
        safety_flags = report.get("safety_flags")
        if not isinstance(safety_flags, Mapping) or safety_flags.get(key) is not expected:
            errors.append(f"safety_flags.{key}_must_be_{str(expected).lower()}")
    return sorted(set(errors))


def render_markdown_report(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Paper Candidate Filter Runtime Observation Pack V1",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Observation status: `{report.get('observation_status')}`",
            f"- Runtime wiring status: `{report.get('runtime_wiring_status')}`",
            f"- Decision events loaded: `{report.get('decision_events_loaded')}`",
            f"- Decision event count: `{report.get('decision_event_count')}`",
            f"- Block events: `{report.get('block_event_count')}`",
            f"- Allow events: `{report.get('allow_event_count')}`",
            f"- ETHUSDT long block events: `{report.get('ethusdt_long_block_event_count')}`",
            f"- ETHUSDT short block events: `{report.get('ethusdt_short_block_event_count')}`",
            f"- BTCUSDT long allow events: `{report.get('btcusdt_long_allow_event_count')}`",
            f"- BTCUSDT short allow events: `{report.get('btcusdt_short_allow_event_count')}`",
            f"- Baseline trades: `{report.get('baseline_trade_count')}`",
            f"- Candidate expected delta: `{report.get('candidate_expected_delta')}`",
            f"- Recommended next action: `{report.get('recommended_next_action')}`",
            "",
            "This pack is read-only and paper-only. It does not change live, canary, risk, models, Qlib, IA Shadow, Freqtrade, SQLite, runtime state or order submission.",
            "",
        ]
    )


def _status_reason(loaded: LoadedObservationInputs, metrics: Mapping[str, Any]) -> tuple[str, str, str]:
    if loaded.input_mode == "no_runtime_rows_loaded":
        return "blocked", "runtime_read_not_allowed_by_default", "blocked"
    if loaded.source_status != "ok":
        return "blocked", loaded.source_reason, "blocked"
    if _safe_int(metrics.get("decision_event_count")) == 0:
        return (
            "blocked",
            "no_post_wiring_runtime_observation_events_found",
            "waiting_for_runtime_evidence",
        )
    return "ok", "paper_candidate_filter_runtime_observation_pack_computed", "runtime_evidence_loaded"


def _recommended_next_action(events_loaded: bool, source_status: str) -> str:
    if source_status != "ok":
        return "corrigir_fontes_readonly_e_reexecutar_observation_pack"
    if not events_loaded:
        return "rodar_paper_candidate_filtrado_e_reexecutar_observation_pack"
    return "comparar_observacao_pos_wiring_com_baseline_e_manter_paper_candidate_sem_live"


def _events_from_payloads(payloads: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for payload in payloads:
        if _has_decision(payload):
            events.append(_normalize_event(payload))
        else:
            events.extend(extract_decision_events(payload))
    return events


def _normalize_event(event: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(event)
    normalized.setdefault("decision", _decision(event))
    normalized.setdefault("symbol_norm", _symbol(event))
    normalized.setdefault("side_norm", _side(event))
    return normalized


def _has_decision(item: Mapping[str, Any]) -> bool:
    return str(item.get("decision") or "").upper() in {"ALLOW", "BLOCK"}


def _decision(event: Mapping[str, Any]) -> str:
    return str(event.get("decision") or event.get("paper_candidate_filter_decision") or "").upper()


def _symbol(event: Mapping[str, Any]) -> str:
    value = event.get("symbol_norm") or event.get("symbol") or event.get("pair")
    return str(value or "").upper().replace("/", "").replace(":USDT", "")


def _side(event: Mapping[str, Any]) -> str:
    return str(event.get("side_norm") or event.get("side") or "").lower()


def _event_time(event: Mapping[str, Any]) -> str | None:
    for field in ("generated_at_utc", "generated_at", "created_at", "event_time_utc", "timestamp"):
        value = event.get(field)
        if value not in (None, ""):
            return str(value)
    return None


def _count_events(events: Sequence[Mapping[str, Any]], symbol: str, side: str) -> int:
    return sum(1 for event in events if _symbol(event) == symbol and _side(event) == side)


def _runtime_wiring_status(events: Sequence[Mapping[str, Any]]) -> str:
    if not events:
        return "unknown"
    if any(str(event.get("runtime_wiring_status") or "").lower() == "enabled" for event in events):
        return "enabled"
    if any(event.get("runtime_wiring_schema_version") for event in events):
        return "enabled"
    return "observed_events_without_wiring_status"


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def _safe_int(value: object, *, default: int = 0) -> int:
    return int(_safe_float(value, default=float(default)))


def _safe_float(value: object, *, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(numeric) or math.isinf(numeric):
        return default
    return round(numeric, 10)


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "sim", "y"}


def _mapping_get(payload: object, key: str) -> Any:
    return payload.get(key) if isinstance(payload, Mapping) else None


def _first_non_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _project_relative(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _resolve_path(root: Path, value: str | Path | None, default: str | Path) -> Path:
    path = Path(value) if value is not None else Path(default)
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()


def _resolve_output_path(root: Path, value: str | Path | None, default: Path) -> Path:
    return _resolve_path(root, value, default)


def _validate_output_path(root: Path, path: Path, *, suffix: str) -> str | None:
    reports_dir = (root / "data" / "reports").resolve()
    try:
        path.relative_to(reports_dir)
    except ValueError:
        return "write_blocked_output_must_be_under_data_reports"
    if path.suffix.lower() != suffix:
        return f"write_blocked_output_must_be_{suffix.removeprefix('.')}_report"
    return None
