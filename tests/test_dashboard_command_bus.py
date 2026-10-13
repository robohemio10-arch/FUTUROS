from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from smartcrypto.dashboard.command_bus import (
    ACCEPTED,
    READONLY_BLOCKED,
    REJECTED,
    DashboardCommandValidationError,
    DashboardReadonlyCommandBus,
)
from smartcrypto.risk.kill_switch_guard import (
    DEFAULT_KILL_SWITCH_PATH,
    disabled_entry,
    enabled_entry,
    utc_timestamp,
)


def build_bus(tmp_path: Path) -> DashboardReadonlyCommandBus:
    timestamp = utc_timestamp()
    authority_path = tmp_path / "kill_switch.json"
    authority_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runtime_mode": "paper",
                "global": disabled_entry("explicit_fixture_clear", "pytest", timestamp),
                "symbols": {},
                "updated_at": timestamp,
            }
        ),
        encoding="utf-8",
    )
    return DashboardReadonlyCommandBus(
        event_log_path=tmp_path / "dashboard_command_bus.jsonl",
        kill_switch_path=authority_path,
        runtime_mode="paper",
        readonly=True,
    )


def test_accepts_allowed_readonly_command_and_records_events(tmp_path: Path) -> None:
    bus = build_bus(tmp_path)

    result = bus.submit(
        "refresh_metrics",
        correlation_id="corr-1",
        operator="analyst",
        source="dashboard",
        payload={"view": "overview"},
    )

    assert result.status == ACCEPTED
    assert result.reasons == []
    events = bus.event_logger.read_events()
    assert [event["event_type"] for event in events] == [
        "dashboard_command_received",
        "dashboard_command_accepted",
    ]


def test_requires_correlation_id_operator_and_source(tmp_path: Path) -> None:
    bus = build_bus(tmp_path)

    with pytest.raises(DashboardCommandValidationError, match="correlation_id is required"):
        bus.submit("refresh_metrics", correlation_id="", operator="ops", source="dashboard")

    with pytest.raises(DashboardCommandValidationError, match="operator is required"):
        bus.submit("refresh_metrics", correlation_id="corr-1", operator="", source="dashboard")

    with pytest.raises(DashboardCommandValidationError, match="source is required"):
        bus.submit("refresh_metrics", correlation_id="corr-1", operator="ops", source="")


def test_blocks_real_order_commands_as_readonly_blocked(tmp_path: Path) -> None:
    bus = build_bus(tmp_path)

    result = bus.submit(
        "submit_order",
        correlation_id="corr-1",
        operator="analyst",
        source="dashboard",
    )

    assert result.status == READONLY_BLOCKED
    assert "dashboard_readonly" in result.reasons
    assert "prohibited_command:submit_order" in result.reasons
    assert bus.event_logger.read_events()[-1]["event_type"] == "dashboard_readonly_blocked"


def test_rejects_unknown_command_without_executing_anything(tmp_path: Path) -> None:
    bus = build_bus(tmp_path)

    result = bus.submit(
        "restart_exchange_connector",
        correlation_id="corr-1",
        operator="analyst",
        source="dashboard",
    )

    assert result.status == REJECTED
    assert "command_not_allowed:restart_exchange_connector" in result.reasons
    assert bus.event_logger.read_events()[-1]["event_type"] == "dashboard_command_rejected"


def test_blocks_every_command_when_live_or_order_flags_are_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = build_bus(tmp_path)
    monkeypatch.setenv("LIVE_ENABLED", "true")

    result = bus.submit(
        "refresh_metrics",
        correlation_id="corr-1",
        operator="analyst",
        source="dashboard",
    )

    assert result.status == REJECTED
    assert "LIVE_ENABLED=true" in result.reasons

    monkeypatch.delenv("LIVE_ENABLED")
    monkeypatch.setenv("ORDER_SUBMISSION_ENABLED", "true")
    result = bus.submit(
        "request_paper_snapshot",
        correlation_id="corr-2",
        operator="analyst",
        source="dashboard",
    )
    assert result.status == REJECTED
    assert "ORDER_SUBMISSION_ENABLED=true" in result.reasons

    monkeypatch.delenv("ORDER_SUBMISSION_ENABLED")
    monkeypatch.setenv("REAL_ORDER_SUBMISSION_ENABLED", "true")
    result = bus.submit(
        "request_reconciliation_check",
        correlation_id="corr-3",
        operator="analyst",
        source="dashboard",
    )
    assert result.status == REJECTED
    assert "REAL_ORDER_SUBMISSION_ENABLED=true" in result.reasons


def test_all_initial_allowed_commands_are_accepted(tmp_path: Path) -> None:
    bus = build_bus(tmp_path)

    for command in (
        "refresh_metrics",
        "request_paper_snapshot",
        "request_reconciliation_check",
        "request_market_health_check",
    ):
        result = bus.submit(
            command,
            correlation_id=f"corr-{command}",
            operator="analyst",
            source="dashboard",
        )
        assert result.status == ACCEPTED


def test_command_bus_default_uses_canonical_authority() -> None:
    assert (
        inspect.signature(DashboardReadonlyCommandBus).parameters["kill_switch_path"].default
        == DEFAULT_KILL_SWITCH_PATH
    )


@pytest.mark.parametrize(
    "command",
    [
        "refresh_metrics",
        "request_paper_snapshot",
        "request_reconciliation_check",
        "request_market_health_check",
    ],
)
@pytest.mark.parametrize("authority", ["missing", "global", "symbol", "corrupt", "legacy_clear"])
def test_readonly_commands_never_bypass_missing_or_blocked_authority(
    tmp_path: Path, command: str, authority: str
) -> None:
    bus = build_bus(tmp_path)
    path = bus.kill_switch_guard.state_path
    state = json.loads(path.read_text(encoding="utf-8"))
    if authority == "missing":
        path.unlink()
    elif authority == "corrupt":
        path.write_text("{broken-json", encoding="utf-8")
    elif authority == "legacy_clear":
        path.write_text(json.dumps({"enabled": False, "runtime_mode": "paper"}), encoding="utf-8")
    else:
        blocked = enabled_entry("explicit_fixture_halt", "pytest", state["updated_at"])
        if authority == "global":
            state["global"] = blocked
        else:
            state["symbols"] = {"BTCUSDT": blocked}
        path.write_text(json.dumps(state), encoding="utf-8")
    before = path.read_bytes() if path.exists() else None
    result = bus.submit(
        command,
        correlation_id="corr-blocked",
        operator="analyst",
        source="dashboard",
        payload={"symbol": "BTCUSDT"},
    )
    expected_reason = (
        "kill_switch_global_blocked"
        if authority == "global"
        else "kill_switch_symbol_blocked"
        if authority == "symbol"
        else "kill_switch_corrupted"
    )
    assert result.status == REJECTED
    assert result.reasons == [expected_reason]
    event_types = [event["event_type"] for event in bus.event_logger.read_events()]
    assert event_types[-1] == "dashboard_command_rejected"
    assert "dashboard_command_received" not in event_types
    assert "dashboard_command_accepted" not in event_types
    assert (path.read_bytes() if path.exists() else None) == before
