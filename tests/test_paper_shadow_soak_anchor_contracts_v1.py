from __future__ import annotations

from pathlib import Path

from smartcrypto.ops.paper_shadow_soak_anchor.catalog import iter_soak_evidence_sources
from smartcrypto.ops.paper_shadow_soak_anchor.contracts import (
    DEFAULT_DIAGNOSTIC_SOAK_DAYS,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_REQUIRED_SOAK_DAYS,
    SCHEMA_VERSION,
    SoakAnchorConfig,
    SoakAnchorStatus,
    SoakGateStatus,
)


def test_contract_constants_are_canonical() -> None:
    assert SCHEMA_VERSION == "paper_shadow_soak_anchor_continuity_pack_v1"
    assert DEFAULT_DIAGNOSTIC_SOAK_DAYS == 7
    assert DEFAULT_REQUIRED_SOAK_DAYS == 30
    assert DEFAULT_OUTPUT_PATH == Path("data/reports/paper_shadow_soak_anchor_continuity_pack.json")


def test_status_enums_are_string_compatible() -> None:
    assert SoakAnchorStatus.OK.value == "ok"
    assert SoakAnchorStatus.BLOCKED.value == "blocked"
    assert SoakAnchorStatus.EVIDENCE_MISSING.value == "evidence_missing"
    assert SoakGateStatus.REACHED.value == "reached"
    assert SoakGateStatus.NOT_REACHED.value == "not_reached"


def test_config_defaults_are_safe_readonly() -> None:
    config = SoakAnchorConfig()
    assert config.write is False
    assert config.diagnostic_soak_days == 7
    assert config.required_soak_days == 30


def test_evidence_catalog_contains_core_anchor_sources() -> None:
    names = {source.name for source in iter_soak_evidence_sources()}
    assert "paper_shadow_soak_report" in names
    assert "paper_shadow_soak_continuity_audit" in names
    assert "runtime_evidence_pack_v2" in names
    assert "readiness_snapshot_v2" in names
    assert "dashboard_semantic_coverage_audit_cli" in names
    assert "dashboard_semantic_coverage_audit_doc" in names
