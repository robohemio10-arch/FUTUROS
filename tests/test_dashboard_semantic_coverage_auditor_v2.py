from __future__ import annotations

from pathlib import Path

from smartcrypto.ops.dashboard_semantic_audit import audit_dashboard_semantic_coverage


ROOT = Path(__file__).resolve().parents[1]


def test_semantic_audit_passes_on_current_dashboard() -> None:
    report = audit_dashboard_semantic_coverage(ROOT)
    payload = report.to_dict()
    assert payload["status"] == "ok", payload
    assert payload["summary"]["page_count"] == 8
    assert payload["summary"]["failed_count"] == 0
    assert payload["safety"]["paper_only"] is True
    assert payload["safety"]["shadow_only"] is True
    assert payload["safety"]["sends_orders"] is False
    assert payload["safety"]["exchange_private_access"] is False


def test_semantic_audit_finds_required_closeout_items() -> None:
    report = audit_dashboard_semantic_coverage(ROOT).to_dict()
    requirement_ids = {finding["requirement_id"] for finding in report["findings"]}
    assert "active_controls_readiness_gates" in requirement_ids
    assert "quant_reports_decision_trace_dataset_pipeline" in requirement_ids
    assert "alerts_messaging_stub_only" in requirement_ids
    assert "css_local_only" in requirement_ids
