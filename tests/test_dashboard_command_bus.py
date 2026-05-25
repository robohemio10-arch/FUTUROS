from __future__ import annotations

from pathlib import Path

import pytest

from smartcrypto.dashboard.command_bus import (
    ACCEPTED,
    READONLY_BLOCKED,
    REJECTED,
    DashboardCommandValidationError,
    DashboardReadonlyCommandBus,
)


def build_bus(tmp_path: Path) -> DashboardReadonlyCommandBus:
    return DashboardReadonlyCommandBus(
        event_log_path=tmp_path / "dashboard_command_bus.jsonl",
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
