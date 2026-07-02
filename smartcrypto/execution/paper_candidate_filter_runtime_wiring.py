"""Runtime wiring for the paper-only candidate filter in signal production."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from smartcrypto.execution.paper_candidate_filter_adapter import (
    PAPER_CANDIDATE_MODE,
    SAFETY_FLAGS,
    PaperOnlyCandidateFilterAdapter,
    normalize_mode,
)

SCHEMA_VERSION = "paper_candidate_filter_runtime_wiring_v1"


def apply_paper_candidate_filter_to_signals(
    signals: Sequence[Mapping[str, Any]],
    *,
    runtime_mode: str | None,
    adapter: PaperOnlyCandidateFilterAdapter | None = None,
) -> dict[str, Any]:
    """Filter paper-candidate signals before candidate execution/submission.

    The adapter is called only when ``runtime_mode == "paper_candidate"``.
    Other modes return the input signals unchanged and keep the wiring disabled.
    """

    mode = normalize_mode(runtime_mode)
    original_signals = [dict(signal) for signal in signals]
    if mode != PAPER_CANDIDATE_MODE:
        return {
            "runtime_wiring_status": "disabled",
            "paper_candidate_filter_called": False,
            "paper_candidate_filter_enabled": False,
            "adapter_integration_status": "paper_adapter_available",
            "integration_status": "runtime_wiring_available",
            "filter_applied": False,
            "allowed_signals": original_signals,
            "blocked_signals": [],
            "decision_events": [],
            "blocked_before_execution_count": 0,
            "allowed_to_candidate_count": len(original_signals),
            "decision_event_count": 0,
            "execution_submission_count": len(original_signals),
            "blocked_submission_count": 0,
            "ethusdt_long_blocked_before_execution": False,
            "ethusdt_short_blocked_before_execution": False,
            "btcusdt_long_allowed_to_paper_candidate": any(_is_symbol_side(signal, "BTCUSDT", "long") for signal in original_signals),
            "btcusdt_short_allowed_to_paper_candidate": any(_is_symbol_side(signal, "BTCUSDT", "short") for signal in original_signals),
            "safety_flags": dict(SAFETY_FLAGS),
            **SAFETY_FLAGS,
        }

    filter_adapter = adapter or PaperOnlyCandidateFilterAdapter()
    allowed_signals: list[dict[str, Any]] = []
    blocked_signals: list[dict[str, Any]] = []
    decision_events: list[dict[str, Any]] = []

    for index, signal in enumerate(original_signals, start=1):
        decision = filter_adapter.evaluate(signal, mode=PAPER_CANDIDATE_MODE).to_event()
        event = {
            **decision,
            "runtime_wiring_schema_version": SCHEMA_VERSION,
            "decision_event_id": f"paper_candidate_runtime_filter_{index:06d}",
            "adapter_integration_status": "paper_adapter_available",
            "blocked_before_execution": decision["decision"] == "BLOCK",
            "submitted_to_candidate_executor": decision["decision"] == "ALLOW",
        }
        decision_events.append(event)
        signal_with_decision = {
            **signal,
            "paper_candidate_filter_decision": decision["decision"],
            "paper_candidate_filter_reason": decision["reason"],
        }
        if decision["decision"] == "BLOCK":
            blocked_signals.append(signal_with_decision)
        else:
            allowed_signals.append(signal_with_decision)

    return {
        "runtime_wiring_status": "enabled",
        "paper_candidate_filter_called": True,
        "paper_candidate_filter_enabled": True,
        "adapter_integration_status": "paper_adapter_available",
        "integration_status": "runtime_wiring_available",
        "filter_applied": True,
        "allowed_signals": allowed_signals,
        "blocked_signals": blocked_signals,
        "decision_events": decision_events,
        "blocked_before_execution_count": len(blocked_signals),
        "allowed_to_candidate_count": len(allowed_signals),
        "decision_event_count": len(decision_events),
        "execution_submission_count": len(allowed_signals),
        "blocked_submission_count": len(blocked_signals),
        "ethusdt_long_blocked_before_execution": any(_event_is_block(event, "ETHUSDT", "long") for event in decision_events),
        "ethusdt_short_blocked_before_execution": any(_event_is_block(event, "ETHUSDT", "short") for event in decision_events),
        "btcusdt_long_allowed_to_paper_candidate": any(_event_is_allow(event, "BTCUSDT", "long") for event in decision_events),
        "btcusdt_short_allowed_to_paper_candidate": any(_event_is_allow(event, "BTCUSDT", "short") for event in decision_events),
        "safety_flags": dict(SAFETY_FLAGS),
        **SAFETY_FLAGS,
    }


def summarize_runtime_wiring(wiring: Mapping[str, Any], *, sample_size: int = 20) -> dict[str, Any]:
    events = wiring.get("decision_events", [])
    decision_events = events if isinstance(events, list) else []
    return {
        "schema_version": SCHEMA_VERSION,
        "runtime_wiring_status": wiring.get("runtime_wiring_status"),
        "paper_candidate_filter_called": wiring.get("paper_candidate_filter_called") is True,
        "paper_candidate_filter_enabled": wiring.get("paper_candidate_filter_enabled") is True,
        "adapter_integration_status": wiring.get("adapter_integration_status"),
        "integration_status": wiring.get("integration_status"),
        "filter_applied": wiring.get("filter_applied") is True,
        "ethusdt_long_blocked_before_execution": wiring.get("ethusdt_long_blocked_before_execution") is True,
        "ethusdt_short_blocked_before_execution": wiring.get("ethusdt_short_blocked_before_execution") is True,
        "btcusdt_long_allowed_to_paper_candidate": wiring.get("btcusdt_long_allowed_to_paper_candidate") is True,
        "btcusdt_short_allowed_to_paper_candidate": wiring.get("btcusdt_short_allowed_to_paper_candidate") is True,
        "blocked_before_execution_count": int(wiring.get("blocked_before_execution_count") or 0),
        "allowed_to_candidate_count": int(wiring.get("allowed_to_candidate_count") or 0),
        "decision_event_count": int(wiring.get("decision_event_count") or 0),
        "decision_log_sample": decision_events[:sample_size],
        "execution_submission_count": int(wiring.get("execution_submission_count") or 0),
        "blocked_submission_count": int(wiring.get("blocked_submission_count") or 0),
        "safety_flags": dict(SAFETY_FLAGS),
        **SAFETY_FLAGS,
    }


def _is_symbol_side(signal: Mapping[str, Any], symbol: str, side: str) -> bool:
    signal_symbol = str(signal.get("symbol") or signal.get("pair") or "").upper().replace("/", "").replace(":USDT", "")
    signal_side = str(signal.get("side") or "").lower()
    return signal_symbol == symbol and signal_side == side


def _event_is_block(event: Mapping[str, Any], symbol: str, side: str) -> bool:
    return event.get("decision") == "BLOCK" and event.get("symbol_norm") == symbol and event.get("side_norm") == side


def _event_is_allow(event: Mapping[str, Any], symbol: str, side: str) -> bool:
    return event.get("decision") == "ALLOW" and event.get("symbol_norm") == symbol and event.get("side_norm") == side
