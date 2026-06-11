from __future__ import annotations

from smartcrypto.ops.paper_shadow_soak_gap_accounting import (
    DEFAULT_REQUIRED_SOAK_DAYS,
    PROJECT_NAME,
    SCHEMA_VERSION,
    SoakEvidenceSource,
    iter_soak_gap_accounting_sources,
)
from smartcrypto.ops.paper_shadow_soak_gap_accounting.contracts import SAFE_FALSE_FLAGS, SAFE_TRUE_FLAGS


def test_contracts_expose_schema_and_project_identity() -> None:
    assert SCHEMA_VERSION == "paper_shadow_soak_continuity_gap_accounting_v1"
    assert PROJECT_NAME == "SMART FUTUROS"
    assert DEFAULT_REQUIRED_SOAK_DAYS == 30


def test_catalog_contains_required_anchor_family_sources() -> None:
    sources = list(iter_soak_gap_accounting_sources())
    names = {source.name for source in sources}

    assert all(isinstance(source, SoakEvidenceSource) for source in sources)
    assert "paper_shadow_soak_report" in names
    assert "paper_shadow_soak_continuity_audit" in names
    assert "paper_shadow_soak_anchor_continuity_pack" in names
    assert "runtime_evidence_pack_v2" in names
    assert "readiness_snapshot_v2" in names
    assert any(source.required_for_accounting for source in sources)
    assert any(source.required_for_readiness for source in sources)


def test_safety_contract_is_hard_locked() -> None:
    assert SAFE_TRUE_FLAGS["paper_only"] is True
    assert SAFE_TRUE_FLAGS["shadow_only"] is True
    assert SAFE_TRUE_FLAGS["dashboard_readonly"] is True
    assert SAFE_TRUE_FLAGS["live_locked"] is True
    assert SAFE_FALSE_FLAGS["live_trading_enabled"] is False
    assert SAFE_FALSE_FLAGS["order_submission_enabled"] is False
    assert SAFE_FALSE_FLAGS["real_order_submission_enabled"] is False
    assert SAFE_FALSE_FLAGS["exchange_private_access"] is False
    assert SAFE_FALSE_FLAGS["sends_orders"] is False
    assert SAFE_FALSE_FLAGS["changes_risk"] is False
    assert SAFE_FALSE_FLAGS["live_release_allowed"] is False
    assert SAFE_FALSE_FLAGS["canary_release_allowed"] is False
