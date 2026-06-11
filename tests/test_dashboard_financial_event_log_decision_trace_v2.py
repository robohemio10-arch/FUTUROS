from __future__ import annotations

from smartcrypto.dashboard.components.decision_trace import (
    build_decision_trace_unknown_state,
    extract_decision_trace_rows,
)


def test_missing_decision_trace_is_optional_unknown() -> None:
    assert build_decision_trace_unknown_state()["status"] == "MISSING_OPTIONAL"
    assert extract_decision_trace_rows({}) == []


def test_decision_trace_supports_institutional_identifiers() -> None:
    rows = extract_decision_trace_rows(
        {"sections": {"events": {"events": [{
            "event_id": "evt-1", "correlation_id": "corr-1",
            "reconciliation_status": "OK", "symbol": "BTCUSDT",
        }]}}}
    )
    assert rows[0]["event_id"] == "evt-1"
    assert rows[0]["correlation_id"] == "corr-1"
    assert rows[0]["reconciliation_status"] == "OK"


def test_decision_trace_has_no_raw_database_or_writer_access() -> None:
    from smartcrypto.dashboard.components import decision_trace

    text = open(decision_trace.__file__, encoding="utf-8").read().lower()
    for token in ("sqlite", "jsonl", "session_state", "write_text", "freqtrade"):
        assert token not in text
