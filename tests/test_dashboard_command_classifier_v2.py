from __future__ import annotations

import pytest

from smartcrypto.dashboard.controls.command_classifier import (
    classify_dashboard_command,
    is_n4_hard_blocked,
    validate_command_payload,
)
from smartcrypto.dashboard.controls.contracts import DashboardCommandIntent, DashboardCommandLevel


@pytest.mark.parametrize(
    ("command", "level"),
    [
        ("REFRESH_VIEW", DashboardCommandLevel.N1_LOCAL_INFO),
        ("REQUEST_SNAPSHOT_REFRESH", DashboardCommandLevel.N2_DRY_RUN_STUB),
        ("CHANGE_GRID_PARAMETERS_PAPER", DashboardCommandLevel.N3_DRY_RUN_STUB_SENSITIVE),
        ("LIVE_ORDER", DashboardCommandLevel.N4_HARD_BLOCKED),
        ("ENABLE_LIVE_TRADING", DashboardCommandLevel.N4_HARD_BLOCKED),
        ("PROMOTE_MODEL_TO_PRODUCTION", DashboardCommandLevel.N4_HARD_BLOCKED),
        ("EXECUTE_OCR_IMPORT_OFFICIAL", DashboardCommandLevel.N4_HARD_BLOCKED),
        ("FORCE_READINESS_APPROVAL", DashboardCommandLevel.N4_HARD_BLOCKED),
        ("ENABLE_CANARY_RELEASE", DashboardCommandLevel.N4_HARD_BLOCKED),
    ],
)
def test_command_classification(command: str, level: DashboardCommandLevel) -> None:
    policy = classify_dashboard_command(command)
    assert policy.level is level
    assert policy.hard_blocked is (level is DashboardCommandLevel.N4_HARD_BLOCKED)


def test_unknown_command_is_conservatively_blocked() -> None:
    policy = classify_dashboard_command("SOMETHING_NEW")
    assert policy.enabled is False
    assert policy.hard_blocked is True


def test_payload_validation() -> None:
    policy = classify_dashboard_command("CHANGE_GRID_PARAMETERS_PAPER")
    invalid = DashboardCommandIntent(command_id="x", command_name=policy.command_name)
    valid = DashboardCommandIntent(
        command_id="y", command_name=policy.command_name,
        payload={"symbol": "BTCUSDT", "proposed_parameters": {"levels": 5}},
    )
    assert validate_command_payload(invalid, policy)[0] is False
    assert validate_command_payload(valid, policy)[0] is True
    assert is_n4_hard_blocked("MARKET_SELL_ALL_REAL") is True
