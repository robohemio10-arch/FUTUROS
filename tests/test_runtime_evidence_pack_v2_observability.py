from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.ops.runtime_evidence_pack import (
    collect_compose_service_catalog,
    collect_runtime_observability_sources,
    runtime_observability_rollup,
    summarize_runtime_component,
    build_runtime_evidence_pack_and_readiness_snapshot_v2,
)


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def valid_trade_event_report() -> dict[str, object]:
    return {
        "created_at": "2026-06-10T19:29:23+00:00",
        "daemon": True,
        "daemon_iteration": 299,
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


def test_trade_event_runtime_component_ok() -> None:
    now = datetime(2026, 6, 10, 19, 30, 0, tzinfo=timezone.utc)

    summary = summarize_runtime_component(
        "trade_event_notifications_report",
        valid_trade_event_report(),
        now=now,
    )

    assert summary["status"] == "ok"
    assert summary["reason"] == "ok"
    assert summary["metrics"]["daemon"] is True
    assert summary["metrics"]["dry_run"] is False
    assert summary["metrics"]["channels"] == "all"
    assert summary["metrics"]["events_pending"] == 0


def test_trade_event_runtime_component_degraded_on_pending_and_dry_run() -> None:
    now = datetime(2026, 6, 10, 19, 30, 0, tzinfo=timezone.utc)
    payload = valid_trade_event_report()
    payload["dry_run"] = True
    payload["events_pending"] = 2

    summary = summarize_runtime_component(
        "trade_event_notifications_report",
        payload,
        now=now,
    )

    assert summary["status"] == "degraded"
    assert "dry_run_not_false" in summary["alerts"]
    assert "events_pending_positive" in summary["alerts"]


def test_trade_event_runtime_component_blocks_on_unsafe_flag() -> None:
    now = datetime(2026, 6, 10, 19, 30, 0, tzinfo=timezone.utc)
    payload = valid_trade_event_report()
    payload["sends_orders"] = True

    summary = summarize_runtime_component(
        "trade_event_notifications_report",
        payload,
        now=now,
    )

    assert summary["status"] == "blocked"
    assert "unsafe_flag:sends_orders" in summary["alerts"]


def test_collect_runtime_observability_sources_includes_trade_event_report(tmp_path: Path) -> None:
    report = tmp_path / "data/reports/trade_event_notifications_report.json"
    write_json(report, valid_trade_event_report())

    sources = collect_runtime_observability_sources(
        tmp_path,
        now=datetime(2026, 6, 10, 19, 30, 0, tzinfo=timezone.utc),
    )

    assert sources["trade_event_notifications_report"]["runtime_status"] == "ok"
    assert sources["trade_event_notifications_report"]["component_summary"]["status"] == "ok"


def test_runtime_rollup_degraded_when_optional_runtime_missing(tmp_path: Path) -> None:
    sources = collect_runtime_observability_sources(
        tmp_path,
        now=datetime(2026, 6, 10, 19, 30, 0, tzinfo=timezone.utc),
    )

    rollup = runtime_observability_rollup(sources)

    assert rollup["status"] == "degraded"
    assert "trade_event_notifications_report" in rollup["missing_optional_sources"]


def test_compose_service_catalog_detects_expected_services(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.paper.yml"
    compose.write_text(
        """
services:
  freqtrade-paper:
    image: freqtrade
  phase14-feedback-sync-paper:
    image: phase14
  qlib-refresh-supervisor-paper:
    image: qlib
  smartcrypto-bot-paper:
    image: bot
  smartcrypto-dashboard-paper:
    image: dash
  trade-event-notifications-paper:
    image: notifications
volumes:
  freqtrade_paper_db:
""",
        encoding="utf-8",
    )

    catalog = collect_compose_service_catalog(tmp_path)

    assert catalog["status"] == "ok"
    assert catalog["missing_expected_services"] == []


def test_build_runtime_evidence_pack_exposes_runtime_observability(tmp_path: Path) -> None:
    write_json(tmp_path / "data/reports/trade_event_notifications_report.json", valid_trade_event_report())

    result = build_runtime_evidence_pack_and_readiness_snapshot_v2(
        project_root=tmp_path,
        output_dir=tmp_path / "out",
        no_write=True,
        now=datetime(2026, 6, 10, 19, 30, 0, tzinfo=timezone.utc),
        include_containers=False,
    )

    pack = result.evidence_pack

    assert pack["schema_version"] == "runtime_evidence_pack_v2"
    assert "runtime_observability" in pack
    assert "runtime_sources" in pack
    assert pack["runtime_sources"]["trade_event_notifications_report"]["runtime_status"] == "ok"
    assert pack["container_snapshot"]["status"] == "disabled"
    assert result.write_performed is False

def test_missing_manual_dispatch_report_is_neutral_runtime_source(tmp_path: Path) -> None:
    sources = collect_runtime_observability_sources(
        tmp_path,
        now=datetime(2026, 6, 10, 19, 30, 0, tzinfo=timezone.utc),
    )

    manual = sources["manual_notification_test_dispatch_report"]

    assert manual["runtime_status"] == "neutral"
    assert manual["component_summary"]["status"] == "neutral"
    assert manual["component_summary"]["alerts"] == []


def test_runtime_rollup_ignores_neutral_optional_sources() -> None:
    rollup = runtime_observability_rollup(
        {
            "trade_event_notifications_report": {"runtime_status": "ok"},
            "manual_notification_test_dispatch_report": {"runtime_status": "neutral"},
        }
    )

    assert rollup["status"] == "ok"
    assert rollup["reason"] == "ok"
    assert rollup["missing_optional_sources"] == []
    assert rollup["neutral_optional_sources"] == ["manual_notification_test_dispatch_report"]
