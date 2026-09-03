from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.ops.dashboard_snapshots.aibot_parity_integration import (
    AIBOT_PARITY_REPORT_PATH,
    build_aibot_parity_dashboard_section,
)
from smartcrypto.ops.dashboard_snapshots.contracts import DashboardPageId
from smartcrypto.ops.dashboard_snapshots.source_catalog import sources_for_page


UTC = timezone.utc


def _sources_with_projection(key: str, projection: dict[str, Any]) -> dict[str, Any]:
    stamp = datetime(2026, 9, 3, 17, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
    payload = {
        "schema_version": "aibot_parity_e2e_snapshot_v1",
        "cycle_id": "aibot-parity-cycle-1",
        "status": "ABSTAIN",
        "reason": "riskmanager_shadow_allow_not_proven",
        "qlib_status": "BLOCKED_EXTERNAL",
        "decision_time_utc": stamp,
        "dashboard": {key: projection},
    }
    return {"payloads": {"aibot_parity_e2e_snapshot_v1": [payload]}}


def test_source_catalog_registers_e2e_report_for_pages_04_05_07() -> None:
    for page_id in (
        DashboardPageId.opportunity_scanner,
        DashboardPageId.ai_governance,
        DashboardPageId.quantitative_reports,
    ):
        paths = {source.path for source in sources_for_page(page_id)}
        assert AIBOT_PARITY_REPORT_PATH in paths


def test_projection_missing_is_unknown_and_fail_closed() -> None:
    section = build_aibot_parity_dashboard_section({}, "opportunity_scanner")

    assert section["status"] == "UNKNOWN"
    assert section["operational_authority"] is False
    assert section["writes_active_signals"] is False
    assert section["signal_published"] is False


def test_page_04_projection_is_read_only() -> None:
    section = build_aibot_parity_dashboard_section(
        _sources_with_projection(
            "opportunity_scanner",
            {
                "status": "ABSTAIN",
                "final_action": "ABSTAIN",
                "selected_candidate_count": 0,
                "selected_candidate_ids": [],
                "would_signal": False,
            },
        ),
        "opportunity_scanner",
    )

    assert section["status"] == "WARNING"
    assert section["cycle_id"] == "aibot-parity-cycle-1"
    assert section["would_signal"] is False
    assert section["writes_active_signals"] is False


def test_page_05_projection_keeps_qlib_external_block_isolated() -> None:
    section = build_aibot_parity_dashboard_section(
        _sources_with_projection(
            "ai_governance",
            {
                "status": "ABSTAIN",
                "ensemble_action": "PROCEED_RESEARCH",
                "riskmanager_shadow_decision": "NOT_EVALUATED",
                "would_signal": False,
                "model_promotion_allowed": False,
            },
        ),
        "ai_governance",
    )

    assert section["qlib_status"] == "BLOCKED_EXTERNAL"
    assert section["operational_authority"] is False
    assert section["model_promotion_allowed"] is False
    assert section["riskmanager_final_authority"] is True


def test_page_07_projection_exposes_e2e_coverage_without_authority() -> None:
    section = build_aibot_parity_dashboard_section(
        _sources_with_projection(
            "quantitative_reports",
            {
                "status": "READY_SHADOW",
                "required_source_count": 5,
                "required_sources_present_count": 5,
                "point_in_time_valid_required_count": 5,
                "execution_intelligence_status": "SUCCESS",
                "risk_budget_status": "SUCCESS",
                "treasury_status": "SUCCESS",
                "final_action": "WOULD_SIGNAL",
            },
        ),
        "quantitative_reports",
    )

    assert section["status"] == "OK"
    assert section["final_action"] == "WOULD_SIGNAL"
    assert section["writes_active_signals"] is False
    assert section["signal_published"] is False
    assert section["operational_authority"] is False


def test_pages_declare_aibot_parity_section() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = (
        root / "smartcrypto/dashboard/pages/04_opportunity_scanner.py",
        root / "smartcrypto/dashboard/pages/05_ai_governance.py",
        root / "smartcrypto/dashboard/pages/07_quantitative_reports.py",
    )
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert '"aibot_parity"' in source
        assert "writes_active_signals" in source
