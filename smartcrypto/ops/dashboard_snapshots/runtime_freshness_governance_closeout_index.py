
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from smartcrypto.ops.dashboard_snapshots.builder_common import iso_utc, json_safe
from smartcrypto.ops.dashboard_snapshots.runtime_blockers_remediation import SAFETY_FLAGS

SCHEMA_VERSION = "runtime_freshness_governance_closeout_index_v1"
REPORT_PATH = Path("data/reports/runtime_freshness_governance_closeout_index_v1.json")

StageState = Literal["OK", "WARNING", "BLOCKED"]


@dataclass(frozen=True)
class GovernanceStage:
    chain_id: str
    title: str
    payload_key: str
    operator_action: str
    closeout_condition: str


STAGES: tuple[GovernanceStage, ...] = (
    GovernanceStage(
        chain_id="source_health",
        title="Source health autoritativo",
        payload_key="source_closeout",
        operator_action="Revisar source_health_matrix e freshness critical/stale.",
        closeout_condition="Nenhum global_blocking_reasons crítico permanece.",
    ),
    GovernanceStage(
        chain_id="runtime_evidence",
        title="Runtime evidence/readiness/soak",
        payload_key="runtime_evidence_view",
        operator_action="Revisar runtime evidence, readiness e soak/gap accounting.",
        closeout_condition="Nenhum runtime_evidence_blocking_reasons permanece.",
    ),
    GovernanceStage(
        chain_id="runtime_blockers_remediation",
        title="Runbook de remediação",
        payload_key="runtime_blockers_remediation",
        operator_action="Usar runbook somente como orientação manual read-only.",
        closeout_condition="Todos blockers críticos têm mapeamento e critério de fechamento.",
    ),
    GovernanceStage(
        chain_id="runtime_blockers_operator_pack",
        title="Operator pack",
        payload_key="runtime_blockers_operator_pack",
        operator_action="Seguir checklist manual fora do dashboard.",
        closeout_condition="Checklist manual completo sem violar safety.",
    ),
    GovernanceStage(
        chain_id="runtime_blockers_closeout_evidence",
        title="Closeout evidence",
        payload_key="runtime_blockers_closeout_evidence",
        operator_action="Validar fechamento por evidência materializada, não por bypass visual.",
        closeout_condition="closeout_allowed=true apenas sem blockers críticos e sem bypass.",
    ),
    GovernanceStage(
        chain_id="freshness_remediation_producers",
        title="Producers externos requeridos",
        payload_key="runtime_evidence_freshness_remediation_producers",
        operator_action="Identificar producers manuais necessários para freshness.",
        closeout_condition="Nenhum producer obrigatório pendente para source stale crítico.",
    ),
    GovernanceStage(
        chain_id="producer_contracts",
        title="Contratos manuais dos producers",
        payload_key="runtime_freshness_producer_contracts",
        operator_action="Executar producers manualmente fora do dashboard quando autorizado pelo operador.",
        closeout_condition="Contratos completos, artefatos esperados definidos e closeout manual claro.",
    ),
    GovernanceStage(
        chain_id="entrypoint_static_safety",
        title="Static safety dos entrypoints",
        payload_key="runtime_freshness_producer_entrypoint_static_safety",
        operator_action="Confirmar que entrypoints manuais existem e não violam safety.",
        closeout_condition="Todos entrypoints OK, sem imports proibidos, ordens, rede indevida ou unsafe writes.",
    ),
    GovernanceStage(
        chain_id="post_refresh_evidence_gate",
        title="Gate pós-refresh",
        payload_key="runtime_freshness_post_refresh_evidence_gate",
        operator_action="Após refresh externo, validar timestamps, health e ausência de blockers.",
        closeout_condition="gate_allowed=true e zero bypass_indicators.",
    ),
)

UNSAFE_TRUE_FLAGS = {
    "live_trading_enabled",
    "live_release_allowed",
    "canary_release_allowed",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
}


def build_runtime_freshness_governance_closeout_index(
    *,
    now_utc: datetime,
    source_closeout: Mapping[str, Any],
    runtime_evidence_view: Mapping[str, Any],
    runtime_blockers_remediation: Mapping[str, Any],
    runtime_blockers_operator_pack: Mapping[str, Any],
    runtime_blockers_closeout_evidence: Mapping[str, Any],
    runtime_evidence_freshness_remediation_producers: Mapping[str, Any],
    runtime_freshness_producer_contracts: Mapping[str, Any],
    runtime_freshness_producer_entrypoint_static_safety: Mapping[str, Any],
    runtime_freshness_post_refresh_evidence_gate: Mapping[str, Any],
    input_errors: Sequence[str] = (),
) -> dict[str, Any]:
    current = _ensure_utc(now_utc)
    payloads: dict[str, Mapping[str, Any]] = {
        "source_closeout": source_closeout,
        "runtime_evidence_view": runtime_evidence_view,
        "runtime_blockers_remediation": runtime_blockers_remediation,
        "runtime_blockers_operator_pack": runtime_blockers_operator_pack,
        "runtime_blockers_closeout_evidence": runtime_blockers_closeout_evidence,
        "runtime_evidence_freshness_remediation_producers": (
            runtime_evidence_freshness_remediation_producers
        ),
        "runtime_freshness_producer_contracts": runtime_freshness_producer_contracts,
        "runtime_freshness_producer_entrypoint_static_safety": (
            runtime_freshness_producer_entrypoint_static_safety
        ),
        "runtime_freshness_post_refresh_evidence_gate": (
            runtime_freshness_post_refresh_evidence_gate
        ),
    }
    safety_flags = _conservative_safety_flags(payloads.values())
    safety_violations = _unsafe_safety_flags(safety_flags)
    global_blocking = _string_list(source_closeout.get("global_blocking_reasons"))
    runtime_blocking = _string_list(
        runtime_evidence_view.get("blocking_evidence_sources")
        or runtime_evidence_view.get("runtime_evidence_blocking_reasons")
    )
    combined_blocking = sorted(
        set(global_blocking)
        | set(runtime_blocking)
        | set(_string_list(runtime_blockers_remediation.get("combined_blocking_reasons")))
    )
    rows = [
        _stage_row(stage=stage, payload=payloads[stage.payload_key])
        for stage in STAGES
    ]
    blocked_rows = [row for row in rows if row["governance_state"] == "BLOCKED"]
    warning_rows = [row for row in rows if row["governance_state"] == "WARNING"]
    normalized_input_errors = sorted({str(error) for error in input_errors if error})
    post_refresh_gate_allowed = bool(
        runtime_freshness_post_refresh_evidence_gate.get("gate_allowed", False)
    )
    closeout_allowed = bool(
        runtime_blockers_closeout_evidence.get("closeout_allowed", False)
    )
    closeout_ready = (
        not combined_blocking
        and not blocked_rows
        and not warning_rows
        and not safety_violations
        and not normalized_input_errors
        and post_refresh_gate_allowed
        and closeout_allowed
    )
    if safety_violations or normalized_input_errors:
        status = "blocked"
        reason = "governance_index_input_or_safety_violation"
    elif combined_blocking:
        status = "blocked"
        reason = "governance_index_blocked_by_authoritative_blockers"
    elif blocked_rows:
        status = "blocked"
        reason = "governance_index_blocked_by_chain_stage"
    elif warning_rows or not closeout_ready:
        status = "warning"
        reason = "governance_index_manual_closeout_not_ready"
    else:
        status = "ok"
        reason = "governance_index_closeout_ready"

    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "generated_at_utc": iso_utc(current),
        "governance_index_status": status.upper(),
        "dashboard_status": str(source_closeout.get("dashboard_status", "UNKNOWN")).upper(),
        "global_source_health_status": str(
            source_closeout.get("global_source_health_status", "UNKNOWN")
        ).upper(),
        "runtime_evidence_integration_status": str(
            runtime_evidence_view.get("runtime_evidence_status", "UNKNOWN")
        ).upper(),
        "closeout_ready": closeout_ready,
        "manual_closeout_allowed": closeout_ready,
        "execution_allowed": False,
        "safe_to_execute_from_dashboard": False,
        "chain_rows_total": len(rows),
        "chain_ok_total": len([row for row in rows if row["governance_state"] == "OK"]),
        "chain_warning_total": len(warning_rows),
        "chain_blocked_total": len(blocked_rows),
        "governance_chain_rows": rows,
        "open_blockers_total": len(combined_blocking),
        "open_blockers": combined_blocking,
        "global_blocking_reasons": global_blocking,
        "runtime_evidence_blocking_reasons": runtime_blocking,
        "combined_blocking_reasons": combined_blocking,
        "manual_next_actions": _manual_next_actions(rows, combined_blocking),
        "closeout_criteria": [stage.closeout_condition for stage in STAGES],
        "input_errors": normalized_input_errors,
        "forbidden_actions": _forbidden_actions(),
        "operator_summary": _operator_summary(status, rows, combined_blocking),
        "safety_flags": safety_flags,
        "safety_violations": safety_violations,
    }
    return json_safe(payload)


def load_runtime_freshness_governance_closeout_index_inputs(
    project_root: Path,
) -> dict[str, Any]:
    reports = project_root / "data/reports"
    summary = _load_mapping(reports / "dashboard_snapshot_build_summary.json")
    global_snapshot = _load_mapping(reports / "dashboard_global_status_snapshot.json")
    source_closeout = {
        "dashboard_status": global_snapshot.get("dashboard_status")
        or summary.get("dashboard_status"),
        "global_source_health_status": global_snapshot.get("global_source_health_status")
        or summary.get("global_source_health_status"),
        "global_blocking_reasons": global_snapshot.get("global_blocking_reasons")
        or summary.get("global_blocking_reasons"),
        "source_health_matrix": global_snapshot.get("source_health_matrix")
        or summary.get("source_health_matrix"),
    }
    runtime_evidence = _embedded_payload(
        global_snapshot, summary, "runtime_evidence_view"
    )
    input_errors: list[str] = []
    if not summary:
        input_errors.append(
            "missing_or_invalid:data/reports/dashboard_snapshot_build_summary.json"
        )
    if not global_snapshot:
        input_errors.append(
            "missing_or_invalid:data/reports/dashboard_global_status_snapshot.json"
        )
    return {
        "source_closeout": source_closeout,
        "runtime_evidence_view": runtime_evidence,
        "runtime_blockers_remediation": _embedded_payload(
            global_snapshot, summary, "runtime_blockers_remediation"
        ),
        "runtime_blockers_operator_pack": _embedded_payload(
            global_snapshot, summary, "runtime_blockers_operator_pack"
        ),
        "runtime_blockers_closeout_evidence": _embedded_payload(
            global_snapshot, summary, "runtime_blockers_closeout_evidence"
        ),
        "runtime_evidence_freshness_remediation_producers": _embedded_payload(
            global_snapshot,
            summary,
            "runtime_evidence_freshness_remediation_producers",
        ),
        "runtime_freshness_producer_contracts": _embedded_payload(
            global_snapshot, summary, "runtime_freshness_producer_contracts"
        ),
        "runtime_freshness_producer_entrypoint_static_safety": _embedded_payload(
            global_snapshot,
            summary,
            "runtime_freshness_producer_entrypoint_static_safety",
        ),
        "runtime_freshness_post_refresh_evidence_gate": _embedded_payload(
            global_snapshot, summary, "runtime_freshness_post_refresh_evidence_gate"
        ),
        "input_errors": input_errors,
    }


def _stage_row(*, stage: GovernanceStage, payload: Mapping[str, Any]) -> dict[str, Any]:
    raw_status = _normalized_status(payload.get("status"))
    blockers = _blockers_for_payload(payload)
    warnings = _warnings_for_payload(payload)
    if not payload:
        state: StageState = "BLOCKED"
        reason = "missing_stage_payload"
    elif raw_status in {"ERROR", "BLOCKED"}:
        state = "BLOCKED"
        reason = str(payload.get("reason", "stage_blocked"))
    elif blockers:
        state = "BLOCKED"
        reason = "authoritative_blockers_present"
    elif raw_status in {"WARNING", "DEGRADED", "STALE"} or warnings:
        state = "WARNING"
        reason = str(payload.get("reason", "stage_warning"))
    else:
        state = "OK"
        reason = str(payload.get("reason", "stage_ok"))
    return {
        "chain_id": stage.chain_id,
        "title": stage.title,
        "payload_key": stage.payload_key,
        "stage_status": raw_status,
        "governance_state": state,
        "reason": reason,
        "blockers_count": len(blockers),
        "blockers": blockers,
        "warnings_count": len(warnings),
        "warnings": warnings,
        "operator_action": stage.operator_action,
        "closeout_condition": stage.closeout_condition,
        "requires_manual_operator": True,
        "execution_allowed": False,
        "safe_to_execute_from_dashboard": False,
        "changes_runtime": False,
        "changes_risk": False,
        "changes_model": False,
        "sends_orders": False,
        "sends_notifications": False,
    }


def _blockers_for_payload(payload: Mapping[str, Any]) -> list[str]:
    keys = (
        "combined_blocking_reasons",
        "global_blocking_reasons",
        "runtime_evidence_blocking_reasons",
        "blocking_evidence_sources",
        "bypass_indicators",
        "safety_violations",
        "missing_required_contracts",
        "missing_entrypoints",
        "forbidden_findings",
        "unsafe_write_findings",
    )
    blockers: list[str] = []
    for key in keys:
        blockers.extend(f"{key}:{item}" for item in _string_list(payload.get(key)))
    if payload.get("gate_allowed") is False and payload.get("schema_version"):
        blockers.append("gate_allowed:false")
    if payload.get("closeout_allowed") is False and payload.get("schema_version"):
        blockers.append("closeout_allowed:false")
    return sorted(set(blockers))


def _warnings_for_payload(payload: Mapping[str, Any]) -> list[str]:
    keys = (
        "stale_evidence_sources",
        "stale_or_invalid_artifacts",
        "blocked_until_refreshed_sources",
        "incomplete_contracts",
        "missing_cli_flags",
        "network_findings",
        "subprocess_findings",
    )
    warnings: list[str] = []
    for key in keys:
        warnings.extend(f"{key}:{item}" for item in _string_list(payload.get(key)))
    return sorted(set(warnings))


def _manual_next_actions(rows: Sequence[Mapping[str, Any]], blockers: Sequence[str]) -> list[str]:
    actions = [
        str(row["operator_action"])
        for row in rows
        if row.get("governance_state") in {"BLOCKED", "WARNING"}
    ]
    if blockers:
        actions.insert(0, "Remediar blockers autoritativos antes de qualquer closeout manual.")
    return list(dict.fromkeys(actions))


def _operator_summary(
    status: str,
    rows: Sequence[Mapping[str, Any]],
    blockers: Sequence[str],
) -> str:
    if status == "ok":
        return "Governance index pronto para closeout manual auditado."
    blocked = len([row for row in rows if row.get("governance_state") == "BLOCKED"])
    warning = len([row for row in rows if row.get("governance_state") == "WARNING"])
    return (
        "Closeout manual ainda não permitido: "
        f"{len(blockers)} blockers autoritativos, "
        f"{blocked} estágios bloqueados e {warning} estágios em warning."
    )


def _forbidden_actions() -> list[str]:
    return [
        "Do not execute producers from the dashboard or this governance index CLI.",
        "Do not edit snapshots or blocker lists to simulate closeout.",
        "Do not enable live, canary, private exchange access, or order submission.",
        "Do not change risk, models, datasets, signals, YAML configuration, or notifications.",
        "Do not infer operational release from governance visibility.",
    ]


def _conservative_safety_flags(payloads: Iterable[Mapping[str, Any]]) -> dict[str, bool]:
    flags = dict(SAFETY_FLAGS)
    for payload in payloads:
        nested = payload.get("safety_flags")
        if not isinstance(nested, Mapping):
            nested = payload.get("safety")
        if not isinstance(nested, Mapping):
            continue
        for key, value in nested.items():
            if isinstance(value, bool):
                if key in UNSAFE_TRUE_FLAGS and value:
                    flags[key] = True
                elif key in {"paper_only", "shadow_only"} and not value:
                    flags[key] = False
                else:
                    flags.setdefault(str(key), value)
    flags["dashboard_readonly"] = True
    flags["changes_runtime"] = False
    flags["changes_risk"] = False
    flags["changes_model"] = False
    flags["changes_active_signals"] = False
    flags["sends_notifications"] = False
    return flags


def _unsafe_safety_flags(flags: Mapping[str, bool]) -> list[str]:
    violations = [flag for flag in sorted(UNSAFE_TRUE_FLAGS) if bool(flags.get(flag))]
    if flags.get("paper_only") is not True:
        violations.append("paper_only_not_true")
    if flags.get("shadow_only") is not True:
        violations.append("shadow_only_not_true")
    return sorted(set(violations))


def _normalized_status(value: Any) -> str:
    status = str(value or "UNKNOWN").upper()
    if status == "OK":
        return "OK"
    if status in {"WARNING", "WARN"}:
        return "WARNING"
    if status in {"BLOCKED", "ERROR", "FAILED", "FAIL"}:
        return "BLOCKED"
    if status in {"DEGRADED", "STALE", "MISSING_REQUIRED"}:
        return status
    return status


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _load_mapping(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _embedded_payload(
    global_snapshot: Mapping[str, Any],
    summary: Mapping[str, Any],
    key: str,
) -> dict[str, Any]:
    for source in (global_snapshot, summary):
        direct = source.get(key)
        if isinstance(direct, Mapping):
            return dict(direct)
        sections = source.get("sections")
        if isinstance(sections, Mapping):
            section = sections.get(key)
            if isinstance(section, Mapping) and isinstance(section.get("data"), Mapping):
                return dict(section["data"])
    return {}


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
