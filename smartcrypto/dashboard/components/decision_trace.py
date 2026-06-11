from __future__ import annotations

from collections.abc import Mapping
from typing import Any


DECISION_TRACE_FIELDS = (
    "timestamp", "event_id", "correlation_id", "symbol", "strategy_id", "model_version",
    "config_version", "risk_mode", "decision_reason", "risk_checks_passed",
    "risk_checks_failed", "order_intent_id", "clientOrderId", "state_before", "state_after",
    "reconciliation_status",
)


def build_decision_trace_unknown_state() -> dict[str, Any]:
    return {
        "status": "MISSING_OPTIONAL",
        "reason": "decision_trace_not_available_in_snapshot",
    }


def extract_decision_trace_rows(snapshot: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    _collect_rows(snapshot if isinstance(snapshot, Mapping) else {}, rows)
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        signature = tuple(str(row.get(field, "")) for field in DECISION_TRACE_FIELDS)
        if signature not in seen:
            seen.add(signature)
            unique.append(row)
    return unique[:100]


def render_financial_event_log_decision_trace(snapshot: Mapping[str, Any], *, ui: Any) -> None:
    ui.subheader("Financial Event Log / Decision Trace")
    rows = extract_decision_trace_rows(snapshot)
    if not rows:
        ui.info(build_decision_trace_unknown_state())
        return
    ui.dataframe(rows, use_container_width=True, hide_index=True)


def _collect_rows(value: Any, output: list[dict[str, Any]]) -> None:
    if isinstance(value, Mapping):
        if any(field in value for field in DECISION_TRACE_FIELDS):
            output.append({field: value.get(field) for field in DECISION_TRACE_FIELDS})
        for child in value.values():
            _collect_rows(child, output)
    elif isinstance(value, list):
        for child in value:
            _collect_rows(child, output)
