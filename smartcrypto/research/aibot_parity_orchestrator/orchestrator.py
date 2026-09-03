"""Deterministic snapshot-first AIBOT-Parity W13 orchestration."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from .contracts import (
    ALLOWED_SOURCE_NAMES,
    REQUIRED_SOURCE_NAMES,
    AibotParityPipelineRequest,
    AibotParityPipelineSnapshot,
    PipelineSourceView,
    PipelineStatus,
    PointInTimeStatus,
    canonical_sha256,
    stable_id,
)

_BLOCKING_STATUS_TOKENS = ("BLOCKED", "FAILED", "ERROR", "HARD_BLOCKED")
_TIME_KEYS = (
    "available_at_utc",
    "decision_time_utc",
    "generated_at_utc",
    "created_at_utc",
)


def build_aibot_parity_pipeline(
    request: AibotParityPipelineRequest,
) -> AibotParityPipelineSnapshot:
    views = tuple(
        _source_view(name, request.sources[name], request.decision_time_utc)
        for name in sorted(request.sources)
    )
    view_by_name = {item.source_name: item for item in views}
    missing_required = tuple(
        name for name in REQUIRED_SOURCE_NAMES if name not in request.sources
    )
    required_present = tuple(
        name for name in REQUIRED_SOURCE_NAMES if name in request.sources
    )

    blocking: list[str] = []
    blocking.extend(f"missing_required_source:{name}" for name in missing_required)
    for name in REQUIRED_SOURCE_NAMES:
        view = view_by_name.get(name)
        if view is None:
            continue
        if view.point_in_time_status is not PointInTimeStatus.VALID:
            blocking.append(
                f"required_source_point_in_time_{view.point_in_time_status.value.lower()}:{name}"
            )
        if _status_blocks(view.status):
            blocking.append(f"required_source_blocked:{name}:{view.status}")

    qlib_status = _qlib_status(request.sources.get("qlib_security"))
    ensemble_action = _ensemble_action(request.sources.get("ensemble_abstention"))
    selected_candidate_ids = _selected_candidate_ids(
        request.sources.get("portfolio_allocator")
    )
    risk_budget_allows = _risk_budget_allows(request.sources.get("risk_budget"))
    riskmanager_shadow_decision = _riskmanager_shadow_decision(
        request.sources.get("riskmanager_shadow")
    )

    if ensemble_action == "ABSTAIN":
        final_action = "ABSTAIN"
    elif not selected_candidate_ids:
        final_action = "ABSTAIN"
    elif risk_budget_allows is False:
        final_action = "ABSTAIN"
    elif riskmanager_shadow_decision != "ALLOW":
        final_action = "ABSTAIN"
    else:
        final_action = "WOULD_SIGNAL"

    would_signal = final_action == "WOULD_SIGNAL" and not blocking
    if blocking:
        status = PipelineStatus.BLOCKED
        reason = blocking[0]
        final_action = "ABSTAIN"
        would_signal = False
    elif final_action == "ABSTAIN":
        status = PipelineStatus.ABSTAIN
        reason = _abstain_reason(
            ensemble_action=ensemble_action,
            selected_candidate_ids=selected_candidate_ids,
            risk_budget_allows=risk_budget_allows,
            riskmanager_shadow_decision=riskmanager_shadow_decision,
        )
    else:
        status = PipelineStatus.READY_SHADOW
        reason = "counterfactual_signal_ready_after_shadow_riskmanager_allow"

    semantic_payload = {
        "schema_version": "aibot_parity_e2e_snapshot_v1",
        "request_id": request.request_id,
        "decision_time_utc": request.decision_time_utc,
        "source_hashes": {item.source_name: item.source_hash for item in views},
        "source_statuses": {item.source_name: item.status for item in views},
        "source_point_in_time": {
            item.source_name: item.point_in_time_status.value for item in views
        },
        "missing_required_sources": missing_required,
        "blocking_reasons": tuple(blocking),
        "qlib_status": qlib_status,
        "ensemble_action": ensemble_action,
        "selected_candidate_ids": selected_candidate_ids,
        "risk_budget_allows": risk_budget_allows,
        "riskmanager_shadow_decision": riskmanager_shadow_decision,
        "final_action": final_action,
    }
    cycle_id = stable_id("aibot-parity-cycle", semantic_payload)
    dashboard = _dashboard_projection(
        cycle_id=cycle_id,
        status=status.value,
        final_action=final_action,
        would_signal=would_signal,
        qlib_status=qlib_status,
        ensemble_action=ensemble_action,
        riskmanager_shadow_decision=riskmanager_shadow_decision,
        selected_candidate_ids=selected_candidate_ids,
        required_present=required_present,
        missing_required=missing_required,
        views=view_by_name,
    )
    return AibotParityPipelineSnapshot(
        cycle_id=cycle_id,
        request_id=request.request_id,
        decision_time_utc=request.decision_time_utc,
        created_at_utc=request.decision_time_utc,
        status=status,
        reason=reason,
        final_action=final_action,
        would_signal=would_signal,
        qlib_status=qlib_status,
        qlib_blocked_external=qlib_status == "BLOCKED_EXTERNAL",
        ensemble_action=ensemble_action,
        riskmanager_shadow_decision=riskmanager_shadow_decision,
        selected_candidate_ids=selected_candidate_ids,
        required_sources_present=required_present,
        missing_required_sources=missing_required,
        blocking_reasons=tuple(blocking),
        source_views=views,
        dashboard=dashboard,
    )


def _source_view(
    source_name: str,
    payload: Mapping[str, Any],
    decision_time_utc: datetime,
) -> PipelineSourceView:
    inner = _unwrap(payload)
    evidence_time = _evidence_time(inner)
    if evidence_time is None:
        pit_status = PointInTimeStatus.UNKNOWN
        reason = "point_in_time_evidence_missing"
    elif evidence_time > decision_time_utc:
        pit_status = PointInTimeStatus.INVALID
        reason = "source_evidence_after_pipeline_decision"
    else:
        pit_status = PointInTimeStatus.VALID
        reason = "point_in_time_valid"
    return PipelineSourceView(
        source_name=source_name,
        status=_status(inner),
        point_in_time_status=pit_status,
        source_hash=canonical_sha256(payload),
        evidence_time_utc=evidence_time,
        reason=reason,
    )


def _unwrap(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("snapshot", "decision", "report", "payload"):
        candidate = payload.get(key)
        if isinstance(candidate, Mapping):
            return candidate
    return payload


def _status(payload: Mapping[str, Any]) -> str:
    for key in ("status", "state", "decision_status", "source_status"):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value).upper()
    return "UNKNOWN"


def _status_blocks(status: str) -> bool:
    normalized = status.upper()
    return any(token in normalized for token in _BLOCKING_STATUS_TOKENS)


def _parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _evidence_time(payload: Mapping[str, Any]) -> datetime | None:
    for key in _TIME_KEYS:
        parsed = _parse_utc(payload.get(key))
        if parsed is not None:
            return parsed
    return None


def _ensemble_action(payload: Mapping[str, Any] | None) -> str:
    if payload is None:
        return "UNKNOWN"
    root = _unwrap(payload)
    for key in ("research_action", "action", "final_action"):
        value = root.get(key)
        if value not in (None, ""):
            return str(value).upper()
    return "UNKNOWN"


def _selected_candidate_ids(payload: Mapping[str, Any] | None) -> tuple[str, ...]:
    if payload is None:
        return ()
    root = _unwrap(payload)
    selected = root.get("selected")
    if not isinstance(selected, list | tuple):
        return ()
    ids = {
        str(row.get("candidate_id"))
        for row in selected
        if isinstance(row, Mapping) and row.get("candidate_id") not in (None, "")
    }
    return tuple(sorted(ids))


def _risk_budget_allows(payload: Mapping[str, Any] | None) -> bool | None:
    if payload is None:
        return None
    root = _unwrap(payload)
    candidates: list[Mapping[str, Any]] = [root]
    for key in ("daily_budget", "decision", "budget"):
        nested = root.get(key)
        if isinstance(nested, Mapping):
            candidates.append(nested)
    for candidate in candidates:
        value = candidate.get("new_risk_allowed")
        if isinstance(value, bool):
            return value
    return None


def _riskmanager_shadow_decision(payload: Mapping[str, Any] | None) -> str:
    if payload is None:
        return "NOT_EVALUATED"
    root = _unwrap(payload)
    for key in ("riskmanager_decision", "decision", "action", "result"):
        value = root.get(key)
        if value not in (None, ""):
            normalized = str(value).upper()
            if normalized in {"ALLOW", "ALLOWED", "PASS"}:
                return "ALLOW"
            if normalized in {"BLOCK", "BLOCKED", "DENY", "DENIED", "REJECT"}:
                return "BLOCK"
            return normalized
    return "NOT_EVALUATED"


def _qlib_status(payload: Mapping[str, Any] | None) -> str:
    if payload is None:
        return "BLOCKED_EXTERNAL"
    root = _unwrap(payload)
    status = _status(root)
    reason = str(root.get("reason", "")).lower()
    if _status_blocks(status) or "upstream" in reason or "dependency" in reason:
        return "BLOCKED_EXTERNAL"
    return status


def _abstain_reason(
    *,
    ensemble_action: str,
    selected_candidate_ids: tuple[str, ...],
    risk_budget_allows: bool | None,
    riskmanager_shadow_decision: str,
) -> str:
    if ensemble_action == "ABSTAIN":
        return "ensemble_abstain"
    if not selected_candidate_ids:
        return "no_shadow_candidate_selected"
    if risk_budget_allows is False:
        return "risk_budget_disallows_new_risk"
    if riskmanager_shadow_decision != "ALLOW":
        return "riskmanager_shadow_allow_not_proven"
    return "shadow_abstain"


def _dashboard_projection(
    *,
    cycle_id: str,
    status: str,
    final_action: str,
    would_signal: bool,
    qlib_status: str,
    ensemble_action: str,
    riskmanager_shadow_decision: str,
    selected_candidate_ids: tuple[str, ...],
    required_present: tuple[str, ...],
    missing_required: tuple[str, ...],
    views: Mapping[str, PipelineSourceView],
) -> dict[str, dict[str, Any]]:
    return {
        "opportunity_scanner": {
            "cycle_id": cycle_id,
            "status": status,
            "final_action": final_action,
            "selected_candidate_count": len(selected_candidate_ids),
            "selected_candidate_ids": list(selected_candidate_ids),
            "would_signal": would_signal,
            "writes_active_signals": False,
        },
        "ai_governance": {
            "cycle_id": cycle_id,
            "status": status,
            "ensemble_action": ensemble_action,
            "qlib_status": qlib_status,
            "riskmanager_shadow_decision": riskmanager_shadow_decision,
            "would_signal": would_signal,
            "operational_authority": False,
            "model_promotion_allowed": False,
        },
        "quantitative_reports": {
            "cycle_id": cycle_id,
            "status": status,
            "required_source_count": len(REQUIRED_SOURCE_NAMES),
            "required_sources_present_count": len(required_present),
            "missing_required_sources": list(missing_required),
            "point_in_time_valid_required_count": sum(
                views[name].point_in_time_status is PointInTimeStatus.VALID
                for name in required_present
            ),
            "execution_intelligence_status": _view_status(
                views, "execution_intelligence"
            ),
            "risk_budget_status": _view_status(views, "risk_budget"),
            "treasury_status": _view_status(views, "treasury"),
            "qlib_status": qlib_status,
            "final_action": final_action,
            "writes_active_signals": False,
        },
    }


def _view_status(views: Mapping[str, PipelineSourceView], name: str) -> str:
    if name not in ALLOWED_SOURCE_NAMES:
        return "UNKNOWN"
    view = views.get(name)
    return "MISSING_OPTIONAL" if view is None else view.status
