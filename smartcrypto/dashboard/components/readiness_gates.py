from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .snapshot_cards import render_key_value_grid


READINESS_FIELDS = (
    "seven_day_diagnostic_status", "thirty_day_readiness_status", "paper_shadow_soak_days",
    "required_soak_days", "soak_gap_count", "uptime_pct", "p0_incident_count",
    "p1_incident_count", "readiness_gate_status", "monte_carlo_status",
    "no_trade_gate_status", "evidence_pack_status", "sidecar_status",
    "manual_go_no_go_required", "blocking_reasons",
)


def normalize_readiness_bool(value: Any, forced_false_for_release: bool = True) -> bool:
    if forced_false_for_release:
        return False
    return value is True or str(value).strip().lower() in {"true", "1", "yes"}


def build_readiness_unknown_state() -> dict[str, Any]:
    return {
        "status": "MISSING_OPTIONAL",
        "reason": "readiness_fields_not_available_in_snapshot",
        "canary_release_allowed": False,
        "live_release_allowed": False,
        "manual_go_no_go_required": True,
        "blocking_reasons": ["readiness_evidence_missing"],
    }


def extract_readiness_gates(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    source = snapshot if isinstance(snapshot, Mapping) else {}
    result = {field: _find_value(source, field) for field in READINESS_FIELDS}
    if not any(value is not None for value in result.values()):
        return build_readiness_unknown_state()
    result.update(
        {
            "status": "READ_ONLY",
            "reason": "snapshot_view_only",
            "canary_release_allowed": False,
            "live_release_allowed": False,
            "manual_go_no_go_required": True,
        }
    )
    return result


def render_readiness_gates_snapshot_view(snapshot: Mapping[str, Any], *, ui: Any) -> None:
    ui.subheader("Readiness & Gates - Snapshot View")
    ui.warning("7d diagnostic and 30d readiness never auto-release canary or live.")
    render_key_value_grid(extract_readiness_gates(snapshot), ui=ui)


def _find_value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find_value(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_value(child, key)
            if found is not None:
                return found
    return None
