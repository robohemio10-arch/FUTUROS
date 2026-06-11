from __future__ import annotations

import pytest

from smartcrypto.ops.dashboard_snapshots.ai_governance_snapshot_builder import (
    REQUIRED_SECTIONS,
    build_ai_governance_snapshot,
    calculate_brier_score,
    calculate_psi,
    classification_metrics,
    classify_drift,
    expected_trade_value,
    promotion_gate,
)
from smartcrypto.ops.dashboard_snapshots.contracts import DASHBOARD_AI_GOVERNANCE_SCHEMA_VERSION
from tests.dashboard_builder_test_support import assert_safe_snapshot, context, write_json, write_jsonl


def test_ai_builder_contract_rates_and_governance(tmp_path) -> None:
    write_json(tmp_path, "data/contracts/phase23_feature_contract.json", {"status": "ok"})
    write_jsonl(tmp_path, "data/ai/model_decisions.jsonl", [{"decision": "AI_ACCEPT"}, {"decision": "AI_REJECT"}, {"decision": "AI_ACCEPT"}])
    snapshot = build_ai_governance_snapshot(context(tmp_path))
    assert_safe_snapshot(snapshot, DASHBOARD_AI_GOVERNANCE_SCHEMA_VERSION, REQUIRED_SECTIONS)
    veto = snapshot["sections"]["shadow_veto"]
    assert veto["ai_accept_count"] == 2
    assert veto["ai_reject_count"] == 1
    governance = snapshot["sections"]["model_governance"]
    assert governance["auto_promotion_allowed"] is False
    assert governance["model_promotion_allowed_from_dashboard"] is False


def test_ai_formulas_and_manual_promotion_gate() -> None:
    value = expected_trade_value(0.10, 0.80, 0.50, estimated_fee=0.01, drift_penalty=0.005)
    assert value == pytest.approx(0.025)
    metrics = classification_metrics(8, 2, 9, 1)
    assert metrics["precision"] == 0.8
    assert metrics["recall"] == pytest.approx(8 / 9)
    assert calculate_brier_score([0.9, 0.2], [1, 0]) == pytest.approx(0.025)
    psi = calculate_psi([0.5, 0.5], [0.6, 0.4])
    assert psi > 0
    assert classify_drift(0.09) == "OK"
    assert classify_drift(0.20) == "WARNING"
    assert classify_drift(0.30) == "BLOCKED"
    common = dict(feature_contract_ok=True, dataset_manifest_ok=True, anti_leakage_ok=True, walkforward_ok=True, financial_metrics_ok=True, drawdown_ok=True, drift_status="OK", event_driven_backtest_ok=True, monte_carlo_ok=True, rollback_pointer_exists=True)
    assert promotion_gate(**common, manual_approval_present=False) is False
    assert promotion_gate(**common, manual_approval_present=True) is True
