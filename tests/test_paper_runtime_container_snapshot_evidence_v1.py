from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from smartcrypto.ops.dashboard_snapshots.active_controls_snapshot_builder import (
    build_active_controls_snapshot,
)
from smartcrypto.ops.dashboard_snapshots.infrastructure_snapshot_builder import (
    build_infrastructure_snapshot,
)
from smartcrypto.ops.paper_runtime_health_and_freshness import (
    audit_paper_runtime_health_and_freshness,
)
from smartcrypto.ops.paper_runtime_health_and_freshness.contracts import (
    CRITICAL_PAPER_SERVICES,
    EXPECTED_PAPER_SERVICES,
)
from smartcrypto.ops.runtime_evidence_pack import (
    build_runtime_evidence_pack_and_readiness_snapshot_v2,
)
from tests.dashboard_builder_test_support import context, write_json as write_dashboard_json
from tests.test_paper_runtime_health_and_freshness_evidence_v1 import (
    FIXED_NOW,
    complete_health_project,
    safe_report,
)
from scripts.audit_paper_runtime_health_and_freshness import parse_args as parse_health_args
from scripts.build_runtime_evidence_pack_and_readiness_snapshot_v2 import (
    parse_args as parse_evidence_args,
)


def compose_ps_result(
    *,
    missing: set[str] | None = None,
    unhealthy: set[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    missing = missing or set()
    unhealthy = unhealthy or set()
    rows = []
    for service in EXPECTED_PAPER_SERVICES:
        if service in missing:
            continue
        is_unhealthy = service in unhealthy
        rows.append(
            {
                "Service": service,
                "Name": f"futuros-{service}-1",
                "Image": "smart-futuros:test",
                "State": "running",
                "Status": "Up 5 minutes (unhealthy)" if is_unhealthy else "Up 5 minutes",
                "Health": "unhealthy" if is_unhealthy else "healthy",
            }
        )
    return subprocess.CompletedProcess(
        args=["docker", "compose", "ps"],
        returncode=0,
        stdout=json.dumps(rows),
        stderr="",
    )


def test_default_without_collection_keeps_disabled_unknown(tmp_path: Path) -> None:
    root = complete_health_project(tmp_path)

    report = audit_paper_runtime_health_and_freshness(project_root=root, now=FIXED_NOW)

    assert report["container_collection_requested"] is False
    assert report["container_snapshot_status"] == "disabled"
    assert report["docker_services_status"] == "disabled"
    assert report["paper_runtime_alive"] is False
    assert report["freqtrade_paper_status"] == "unknown"
    assert report["smartcrypto_bot_status"] == "unknown"


def test_collection_all_services_running_marks_runtime_alive(tmp_path: Path) -> None:
    root = complete_health_project(tmp_path)

    with patch(
        "smartcrypto.ops.paper_runtime_health_and_freshness.auditor.subprocess.run",
        return_value=compose_ps_result(),
    ) as mocked_run:
        report = audit_paper_runtime_health_and_freshness(
            project_root=root,
            collect_containers=True,
            now=FIXED_NOW,
        )

    assert report["status"] == "ok"
    assert report["docker_services_status"] == "ok"
    assert report["paper_runtime_alive"] is True
    assert report["freqtrade_paper_status"] == "ok"
    assert report["smartcrypto_bot_status"] == "ok"
    assert report["container_snapshot"]["missing_expected_services"] == []
    command = mocked_run.call_args.args[0]
    assert command[:3] == ["docker", "compose", "-f"]
    assert command[-3:] == ["ps", "--format", "json"]


def test_missing_or_unhealthy_critical_service_blocks_liveness(tmp_path: Path) -> None:
    root = complete_health_project(tmp_path)
    missing_service = "smartcrypto-bot-paper"

    with patch(
        "smartcrypto.ops.paper_runtime_health_and_freshness.auditor.subprocess.run",
        return_value=compose_ps_result(missing={missing_service}),
    ):
        missing_report = audit_paper_runtime_health_and_freshness(
            project_root=root,
            collect_containers=True,
            now=FIXED_NOW,
        )

    assert missing_report["status"] == "blocked"
    assert missing_report["paper_runtime_alive"] is False
    assert missing_service in missing_report["container_snapshot"]["missing_expected_services"]

    unhealthy_service = "freqtrade-paper"
    with patch(
        "smartcrypto.ops.paper_runtime_health_and_freshness.auditor.subprocess.run",
        return_value=compose_ps_result(unhealthy={unhealthy_service}),
    ):
        unhealthy_report = audit_paper_runtime_health_and_freshness(
            project_root=root,
            collect_containers=True,
            now=FIXED_NOW,
        )

    assert unhealthy_report["status"] == "blocked"
    assert unhealthy_report["paper_runtime_alive"] is False
    assert unhealthy_service in unhealthy_report["container_snapshot"]["unhealthy_services"]


def test_docker_unavailable_is_controlled_and_safe(tmp_path: Path) -> None:
    root = complete_health_project(tmp_path)

    with patch(
        "smartcrypto.ops.paper_runtime_health_and_freshness.auditor.subprocess.run",
        side_effect=FileNotFoundError("docker"),
    ):
        report = audit_paper_runtime_health_and_freshness(
            project_root=root,
            collect_containers=True,
            now=FIXED_NOW,
        )

    assert report["status"] == "degraded"
    assert report["container_snapshot_status"] == "unknown"
    assert report["container_snapshot"]["reason"] == "docker_unavailable"
    assert report["paper_runtime_alive"] is False
    assert report["paper_only"] is True
    for flag in (
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "canary_release_allowed",
        "live_release_allowed",
    ):
        assert report[flag] is False


def test_docker_compose_failure_is_controlled(tmp_path: Path) -> None:
    root = complete_health_project(tmp_path)
    failed = subprocess.CompletedProcess(
        args=["docker", "compose", "ps"],
        returncode=1,
        stdout="",
        stderr="Docker daemon unavailable",
    )

    with patch(
        "smartcrypto.ops.paper_runtime_health_and_freshness.auditor.subprocess.run",
        return_value=failed,
    ):
        report = audit_paper_runtime_health_and_freshness(
            project_root=root,
            collect_containers=True,
            now=FIXED_NOW,
        )

    assert report["status"] == "degraded"
    assert report["container_snapshot_status"] == "degraded"
    assert report["container_snapshot"]["reason"] == "docker_compose_ps_failed"
    assert report["paper_runtime_alive"] is False


def test_collect_containers_flag_is_available_on_both_clis() -> None:
    assert parse_health_args(["--collect-containers"]).collect_containers is True
    assert parse_evidence_args(["--collect-containers"]).collect_containers is True


def test_runtime_evidence_propagates_single_collected_snapshot(tmp_path: Path) -> None:
    root = complete_health_project(tmp_path)

    with patch(
        "smartcrypto.ops.paper_runtime_health_and_freshness.auditor.subprocess.run",
        return_value=compose_ps_result(),
    ) as mocked_run:
        result = build_runtime_evidence_pack_and_readiness_snapshot_v2(
            project_root=root,
            output_dir=root / "data" / "reports",
            no_write=True,
            collect_containers=True,
            now=FIXED_NOW,
        )

    summary = result.readiness_snapshot["paper_runtime_health_and_freshness"]
    assert summary["paper_runtime_alive"] is True
    assert summary["container_snapshot_status"] == "ok"
    assert result.evidence_pack["container_snapshot"]["status"] == "ok"
    assert result.readiness_snapshot["canary_release_allowed"] is False
    assert result.readiness_snapshot["live_release_allowed"] is False
    assert mocked_run.call_count == 1


def test_dashboard_consumes_container_fields_read_only(tmp_path: Path) -> None:
    root = complete_health_project(tmp_path)
    with patch(
        "smartcrypto.ops.paper_runtime_health_and_freshness.auditor.subprocess.run",
        return_value=compose_ps_result(),
    ):
        audit_paper_runtime_health_and_freshness(
            project_root=root,
            collect_containers=True,
            write=True,
            now=FIXED_NOW,
        )
    write_dashboard_json(root, "data/reports/system_healthcheck_report.json", safe_report())
    write_dashboard_json(root, "data/reports/market_data_health_audit_report.json", safe_report())
    write_dashboard_json(
        root,
        "data/reports/market_data_health_runtime_sources_report.json",
        safe_report(last_candle_timestamp_utc="2026-06-11T11:59:00Z"),
    )
    write_dashboard_json(root, "data/runtime/runtime_safety_audit_config.json", {"riskmanager_approval": True})
    write_dashboard_json(root, "data/runtime/kill_switch.json", {"active": False})
    write_dashboard_json(root, "data/reports/risk_recovery_mode_audit_report.json", safe_report())
    write_dashboard_json(root, "data/reports/state_reconciliation_audit_report.json", safe_report())

    infrastructure = build_infrastructure_snapshot(context(root))
    controls = build_active_controls_snapshot(context(root))

    assert infrastructure["sections"]["docker"]["status"] == "OK"
    assert infrastructure["sections"]["paper_runtime_health"]["paper_runtime_alive"] is True
    assert controls["sections"]["paper_runtime_health"]["freqtrade_paper_status"] == "ok"
    assert controls["sections"]["paper_runtime_health"]["canary_release_allowed"] is False
    assert controls["sections"]["paper_runtime_health"]["live_release_allowed"] is False


def test_critical_service_contract_is_explicit() -> None:
    assert set(CRITICAL_PAPER_SERVICES) < set(EXPECTED_PAPER_SERVICES)
    assert "trade-event-notifications-paper" not in CRITICAL_PAPER_SERVICES
