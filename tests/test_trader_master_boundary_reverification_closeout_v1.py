from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from smartcrypto.data.trader_master_fingerprint_v2.legacy_master_governance import (
    build_legacy_master_boundary_report,
)
from smartcrypto.data.trader_master_fingerprint_v2.legacy_master_remediation_plan import (
    build_remediation_plan_report,
)


ROOT = Path(__file__).resolve().parents[1]
PROTECTED = {
    "data/trades/trades_master.parquet": (
        "24e049b3ca7a72dbde071a056548035fed87651d48959cd0cf4c6c8b0dac7295"
    ),
    "smartcrypto/data/trader_master_fingerprint_v2/fingerprint_spec.py": (
        "7efee2c2ac682242796ac9954ddea525cd34c4a69ab985cdefcdb4e5fe223147"
    ),
    "config/trader_master_legacy_research_only_policy_v1.json": (
        "b9d19a863132008c61221ade0fdf8726ef5c194f7d4ffb55552f33d26f3bd7b1"
    ),
}


@pytest.fixture(scope="module")
def boundary() -> dict[str, object]:
    return build_legacy_master_boundary_report(project_root=ROOT, write_report=False)


def test_final_boundary_has_no_unresolved_violation(boundary: dict[str, object]) -> None:
    for field in (
        "high_count",
        "critical_count",
        "dynamic_reference_unresolved_count",
        "direct_import_count",
        "direct_write_count",
        "legacy_writer_callsite_count",
        "operational_consumer_count",
        "unregistered_consumer_count",
    ):
        assert boundary[field] == 0
    assert boundary["consumer_inventory_complete"] is True
    assert boundary["decision"] == "LEGACY_MASTER_SEGREGATED_RESEARCH_ONLY"
    assert boundary["segregation_enforced"] is True


def test_final_boundary_preserves_all_safety_denials(
    boundary: dict[str, object],
) -> None:
    for field in (
        "writes_trader_master",
        "import_authorized",
        "write_authorized",
        "fingerprint_generation_allowed",
        "operational_training_authorized",
        "paper_signal_selection_authorized",
        "live_signal_selection_authorized",
        "risk_decision_authorized",
        "order_execution_authorized",
        "operational_authority",
        "sends_orders",
        "exchange_private_access",
    ):
        assert boundary[field] is False


def test_legacy_writer_implementation_remains_quarantined_without_callsites(
    boundary: dict[str, object],
) -> None:
    assert boundary["legacy_writer_implementation_count"] == 1
    assert boundary["legacy_writer_callsite_count"] == 0
    assert boundary["direct_import_count"] == 0
    assert boundary["direct_write_count"] == 0


def test_planner_requires_no_follow_up_package_or_policy_change() -> None:
    report = build_remediation_plan_report(project_root=ROOT, write_report=False)

    assert report["status"] == "ok"
    assert report["decision"] == "LEGACY_BOUNDARY_REMEDIATION_NOT_REQUIRED"
    assert report["reason"] == "legacy_boundary_already_segregated"
    assert report["branch_package_count"] == 0
    assert report["readonly_consumer_registration_count"] == 0
    assert report["planned_policy_change_count"] == 0


def test_protected_artifacts_remain_byte_identical() -> None:
    for relative_path, expected_hash in PROTECTED.items():
        actual = hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
        assert actual == expected_hash
