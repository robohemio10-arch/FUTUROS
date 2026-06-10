from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from smartcrypto.dashboard.trade_event_notifications_runtime_panel import (
    collect_runtime_alerts,
    collect_safety_alerts,
    parse_utc_datetime,
    summarize_trade_event_notifications_runtime,
)


def valid_report(created_at: str) -> dict[str, object]:
    return {
        "created_at": created_at,
        "daemon": True,
        "daemon_iteration": 4,
        "dry_run": False,
        "channels": "all",
        "events_detected": 598,
        "events_pending": 0,
        "events_dispatched": 0,
        "events_marked_sent": 0,
        "reason": "no_pending_events",
        "status": "ok",
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }


def test_parse_utc_datetime_accepts_z_suffix() -> None:
    parsed = parse_utc_datetime("2026-06-10T19:05:21Z")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.isoformat() == "2026-06-10T19:05:21+00:00"


def test_runtime_summary_ok_for_fresh_report(tmp_path: Path) -> None:
    now = datetime(2026, 6, 10, 19, 6, 0, tzinfo=timezone.utc)
    report_path = tmp_path / "trade_event_notifications_report.json"
    report_path.write_text(
        __import__("json").dumps(valid_report("2026-06-10T19:05:21+00:00")),
        encoding="utf-8",
    )

    state = summarize_trade_event_notifications_runtime(
        report_path=report_path,
        now=now,
        stale_after_seconds=120,
    )

    assert state["status"] == "ok"
    assert state["reason"] == "ok"
    assert state["metrics"]["daemon"] is True
    assert state["metrics"]["dry_run"] is False
    assert state["metrics"]["channels"] == "all"
    assert state["metrics"]["events_pending"] == 0
    assert state["sends_orders"] is False
    assert state["changes_risk"] is False
    assert state["exchange_private_access"] is False


def test_runtime_summary_degraded_when_report_stale(tmp_path: Path) -> None:
    now = datetime(2026, 6, 10, 19, 10, 0, tzinfo=timezone.utc)
    report_path = tmp_path / "trade_event_notifications_report.json"
    report_path.write_text(
        __import__("json").dumps(valid_report("2026-06-10T19:05:21+00:00")),
        encoding="utf-8",
    )

    state = summarize_trade_event_notifications_runtime(
        report_path=report_path,
        now=now,
        stale_after_seconds=120,
    )

    assert state["status"] == "degraded"
    assert "report_stale" in state["alerts"]


def test_runtime_summary_blocked_when_safety_flag_unsafe(tmp_path: Path) -> None:
    now = datetime(2026, 6, 10, 19, 6, 0, tzinfo=timezone.utc)
    payload = valid_report("2026-06-10T19:05:21+00:00")
    payload["sends_orders"] = True

    report_path = tmp_path / "trade_event_notifications_report.json"
    report_path.write_text(__import__("json").dumps(payload), encoding="utf-8")

    state = summarize_trade_event_notifications_runtime(
        report_path=report_path,
        now=now,
        stale_after_seconds=120,
    )

    assert state["status"] == "blocked"
    assert "unsafe_flag:sends_orders_not_false" in state["alerts"]


def test_collect_runtime_alerts_detects_daemon_dry_run_and_pending() -> None:
    now = datetime(2026, 6, 10, 19, 6, 0, tzinfo=timezone.utc)
    payload = valid_report("2026-06-10T19:05:21+00:00")
    payload["daemon"] = False
    payload["dry_run"] = True
    payload["events_pending"] = 2

    alerts, metrics = collect_runtime_alerts(payload, now=now, stale_after_seconds=120)

    assert "daemon_not_true" in alerts
    assert "dry_run_not_false" in alerts
    assert "events_pending_positive" in alerts
    assert metrics["events_pending"] == 2


def test_collect_safety_alerts_requires_paper_shadow_true() -> None:
    payload = valid_report("2026-06-10T19:05:21+00:00")
    payload["paper_only"] = False
    payload["shadow_only"] = False

    alerts = collect_safety_alerts(payload)

    assert "unsafe_flag:paper_only_not_true" in alerts
    assert "unsafe_flag:shadow_only_not_true" in alerts


def test_dashboard_app_registers_trade_notifications_page() -> None:
    payload = Path("smartcrypto/dashboard/app.py").read_text(encoding="utf-8")

    assert "render_trade_event_notifications_runtime_panel" in payload
    assert '"Trade notifications"' in payload
    assert 'elif page == "Trade notifications":' in payload
