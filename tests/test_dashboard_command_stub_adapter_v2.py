from __future__ import annotations

import pytest

from smartcrypto.dashboard.controls.command_stub_adapter import evaluate_dashboard_command_intent
from smartcrypto.dashboard.controls.contracts import DashboardCommandIntent, DashboardCommandStatus


def evaluate(command: str, payload: dict | None = None):
    return evaluate_dashboard_command_intent(
        DashboardCommandIntent(command_id=f"test-{command}", command_name=command, payload=payload or {})
    )


def test_n1_and_valid_n2_are_accepted_without_execution() -> None:
    assert evaluate("REFRESH_VIEW").status is DashboardCommandStatus.DRY_RUN_ACCEPTED
    result = evaluate("REQUEST_ALERT_TEST_DRY_RUN", {"severity": "WARNING", "channel": "TELEGRAM"})
    assert result.accepted is True
    assert result.executed is False
    assert result.dry_run is True


def test_invalid_n2_and_n3_are_rejected() -> None:
    assert evaluate("REQUEST_ALERT_TEST_DRY_RUN").status is DashboardCommandStatus.DRY_RUN_REJECTED
    assert evaluate("PAPER_KILL_SWITCH").status is DashboardCommandStatus.DRY_RUN_REJECTED


def test_unknown_command_is_disabled_without_execution() -> None:
    result = evaluate("UNREGISTERED_COMMAND")
    assert result.status is DashboardCommandStatus.DISABLED
    assert result.accepted is False
    assert result.executed is False


def test_valid_n3_requires_manual_confirmation() -> None:
    result = evaluate("PAPER_KILL_SWITCH", {"scope": "paper", "reason": "test"})
    assert result.accepted is True
    assert result.simulated_effect["manual_confirmation_required"] is True
    assert result.executed is False


@pytest.mark.parametrize(
    "command",
    ["LIVE_ORDER", "ENABLE_LIVE_TRADING", "PROMOTE_MODEL_TO_PRODUCTION",
     "EXECUTE_OCR_IMPORT_OFFICIAL", "FORCE_READINESS_APPROVAL", "ENABLE_CANARY_RELEASE"],
)
def test_n4_is_always_hard_blocked(command: str) -> None:
    result = evaluate(command, {"anything": True})
    assert result.status is DashboardCommandStatus.HARD_BLOCKED
    assert result.accepted is False
    assert result.executed is False
    assert result.hard_blocked is True
    assert result.audit["sends_orders"] is False
    assert result.audit["changes_risk"] is False
    assert result.audit["uses_command_bus"] is False
