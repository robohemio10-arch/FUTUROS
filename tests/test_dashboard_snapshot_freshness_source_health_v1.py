from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.dashboard.components.runtime_source_health import (
    SOURCE_COLUMNS,
    source_health_rows,
)
from smartcrypto.ops.dashboard_snapshots.build_context import create_dashboard_build_context
from smartcrypto.ops.dashboard_snapshots.builder_registry import build_all_dashboard_snapshots
from smartcrypto.ops.dashboard_snapshots.source_closeout import (
    RequiredLevel,
    RuntimeSourceDefinition,
    RuntimeSourceStatus,
    RuntimeSourceType,
    build_page_source_matrix,
    evaluate_source,
)
from smartcrypto.ops.dashboard_snapshots.source_freshness import (
    FreshnessBasis,
    FreshnessPolicy,
    FreshnessStatus,
    SourceHealthStatus,
    TimestampSource,
)


NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def source_definition(
    path: str,
    level: RequiredLevel,
    policy: FreshnessPolicy,
) -> RuntimeSourceDefinition:
    return RuntimeSourceDefinition(
        source_id="src_freshness_test",
        canonical_path=path,
        display_name="Freshness Test",
        owner_domain="infrastructure",
        source_type=RuntimeSourceType.JSON_REPORT,
        required_level=level,
        expected_schema_version=None,
        freshness_policy=policy.to_dict(),
        consumer_snapshots=("data/reports/dashboard_infrastructure_snapshot.json",),
        consumer_pages=("infrastructure",),
        missing_behavior="BLOCK" if level is RequiredLevel.REQUIRED else "DEGRADE",
        stale_behavior="BLOCK" if level is RequiredLevel.REQUIRED else "DEGRADE",
        future_source_pending_behavior="SHOW_PLANNED_PENDING_WITHOUT_FAILURE",
        operator_hint="Refresh the documented source.",
        runbook_hint="Consult the infrastructure runbook.",
        safety_impact="Read-only observability only.",
    )


def freshness_policy(
    basis: FreshnessBasis,
    *,
    max_age_seconds: float = 300.0,
    fallback_to_mtime: bool = False,
) -> FreshnessPolicy:
    return FreshnessPolicy(
        freshness_required=True,
        freshness_basis=basis,
        max_age_seconds=max_age_seconds,
        warning_age_seconds=max_age_seconds * 0.8,
        critical_age_seconds=max_age_seconds,
        fallback_to_mtime=fallback_to_mtime,
        stale_behavior="BLOCK",
        missing_behavior="BLOCK",
        invalid_timestamp_behavior="BLOCK",
        operator_hint="Refresh the documented source.",
        producer_hint="fixture producer",
    )


def write_json(root: Path, relative: str, payload: object) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def test_payload_timestamp_takes_precedence_when_policy_requires_payload(tmp_path: Path) -> None:
    target = write_json(
        tmp_path,
        "data/reports/source.json",
        {"last_updated_utc": "2026-06-15T11:59:00Z"},
    )
    old_mtime = datetime(2026, 6, 14, tzinfo=timezone.utc).timestamp()
    os.utime(target, (old_mtime, old_mtime))

    result = evaluate_source(
        source_definition(
            "data/reports/source.json",
            RequiredLevel.REQUIRED,
            freshness_policy(FreshnessBasis.PAYLOAD_TIMESTAMP),
        ),
        tmp_path,
        NOW,
    )

    assert result["timestamp_source"] == TimestampSource.PAYLOAD.value
    assert result["effective_timestamp_utc"] == "2026-06-15T11:59:00Z"
    assert result["freshness_status"] == FreshnessStatus.FRESH.value
    assert result["status"] == RuntimeSourceStatus.OK.value


def test_file_mtime_fallback_when_policy_allows_it(tmp_path: Path) -> None:
    target = write_json(tmp_path, "data/reports/source.json", {"status": "ok"})
    recent_mtime = datetime(2026, 6, 15, 11, 59, tzinfo=timezone.utc).timestamp()
    os.utime(target, (recent_mtime, recent_mtime))

    result = evaluate_source(
        source_definition(
            "data/reports/source.json",
            RequiredLevel.REQUIRED,
            freshness_policy(
                FreshnessBasis.PAYLOAD_TIMESTAMP_OR_FILE_MTIME,
                fallback_to_mtime=True,
            ),
        ),
        tmp_path,
        NOW,
    )

    assert result["timestamp_source"] == TimestampSource.FILE_MTIME.value
    assert result["freshness_status"] == FreshnessStatus.FRESH.value
    assert result["reason"] == "source_available_and_valid"


def test_invalid_timestamp_never_returns_ok(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/source.json",
        {"last_updated_utc": "not-a-timestamp"},
    )

    result = evaluate_source(
        source_definition(
            "data/reports/source.json",
            RequiredLevel.REQUIRED,
            freshness_policy(
                FreshnessBasis.PAYLOAD_TIMESTAMP_OR_FILE_MTIME,
                fallback_to_mtime=True,
            ),
        ),
        tmp_path,
        NOW,
    )

    assert result["status"] == RuntimeSourceStatus.INVALID_TIMESTAMP.value
    assert result["invalid_timestamp"] is True
    assert result["health_status"] == SourceHealthStatus.BLOCKED.value


def test_required_stale_source_blocks_consumer_pages(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/source.json",
        {"last_updated_utc": "2026-06-15T11:00:00Z"},
    )
    result = evaluate_source(
        source_definition(
            "data/reports/source.json",
            RequiredLevel.REQUIRED,
            freshness_policy(FreshnessBasis.PAYLOAD_TIMESTAMP),
        ),
        tmp_path,
        NOW,
    )

    assert result["freshness_status"] == FreshnessStatus.CRITICAL_STALE.value
    assert result["health_status"] == SourceHealthStatus.BLOCKED.value
    assert result["blocks_page_operational_view"] is True
    assert result["blocks_dashboard_readiness"] is True


def test_optional_stale_source_degrades_without_global_block(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/source.json",
        {"last_updated_utc": "2026-06-15T11:00:00Z"},
    )
    result = evaluate_source(
        source_definition(
            "data/reports/source.json",
            RequiredLevel.OPTIONAL,
            freshness_policy(FreshnessBasis.PAYLOAD_TIMESTAMP),
        ),
        tmp_path,
        NOW,
    )

    assert result["health_status"] == SourceHealthStatus.DEGRADED.value
    assert result["blocks_page_operational_view"] is False
    assert result["blocks_dashboard_readiness"] is False


def test_future_source_pending_has_planned_health(tmp_path: Path) -> None:
    result = evaluate_source(
        source_definition(
            "data/reports/future.json",
            RequiredLevel.FUTURE_SOURCE_PENDING,
            FreshnessPolicy(False, FreshnessBasis.NOT_APPLICABLE),
        ),
        tmp_path,
        NOW,
    )

    assert result["health_status"] == SourceHealthStatus.PLANNED.value
    assert result["freshness_status"] == FreshnessStatus.NOT_APPLICABLE.value
    assert result["blocks_dashboard_readiness"] is False


def test_generated_snapshot_has_generated_health_without_operational_authority(
    tmp_path: Path,
) -> None:
    result = evaluate_source(
        source_definition(
            "data/reports/dashboard_infrastructure_snapshot.json",
            RequiredLevel.GENERATED,
            FreshnessPolicy(False, FreshnessBasis.NOT_APPLICABLE),
        ),
        tmp_path,
        NOW,
    )

    assert result["health_status"] == SourceHealthStatus.HEALTHY.value
    assert result["freshness_status"] == FreshnessStatus.NOT_APPLICABLE.value
    assert result["blocks_page_operational_view"] is False
    assert result["safety_impact"] == "Read-only observability only."


def test_source_health_matrix_is_present_in_build_summary(tmp_path: Path) -> None:
    context = create_dashboard_build_context(
        tmp_path,
        output_dir=tmp_path / "output",
        now_utc=NOW,
        runtime_mode="paper",
        strict=False,
        allow_writes_to_output_dir=False,
    )
    result = build_all_dashboard_snapshots(context)
    summary = result["summary"]

    assert summary["source_health_matrix"] == summary["source_matrix"]
    assert summary["source_health_total"] == len(summary["source_health_matrix"])
    assert summary["freshness_policy_coverage"] == 1.0
    assert "global_source_health_status" in summary


def test_page_source_matrix_consistent_with_blocking_sources(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/source.json",
        {"last_updated_utc": "2026-06-15T11:00:00Z"},
    )
    blocked = evaluate_source(
        source_definition(
            "data/reports/source.json",
            RequiredLevel.REQUIRED,
            freshness_policy(FreshnessBasis.PAYLOAD_TIMESTAMP),
        ),
        tmp_path,
        NOW,
    )
    matrix = build_page_source_matrix([blocked], {"infrastructure": "OK"})
    infrastructure = next(row for row in matrix if row["page_id"] == "infrastructure")

    assert infrastructure["current_page_status"] == "BLOCKED"
    assert infrastructure["blocking_sources"] == ["src_freshness_test"]


def test_dashboard_global_status_not_ok_when_required_source_is_critical_stale(
    tmp_path: Path,
) -> None:
    context = create_dashboard_build_context(
        tmp_path,
        output_dir=tmp_path / "output",
        now_utc=NOW,
        runtime_mode="paper",
        strict=False,
        allow_writes_to_output_dir=False,
    )
    result = build_all_dashboard_snapshots(context)
    summary = result["summary"]
    global_snapshot = result["snapshots"]["dashboard_global_status_snapshot.json"]

    assert summary["dashboard_status"] != "OK"
    assert global_snapshot["overall_status"] == summary["dashboard_status"]
    assert global_snapshot["global_blocking_reasons"] == summary["global_blocking_reasons"]


def test_runtime_source_health_component_renders_freshness_fields() -> None:
    rows = source_health_rows(
        {
            "runtime_source_health": [
                {
                    "display_name": "Runtime Safety",
                    "status": "STALE",
                    "health_status": "BLOCKED",
                    "freshness_status": "CRITICAL_STALE",
                    "severity": "CRITICAL",
                    "age_seconds": 901.25,
                    "effective_timestamp_utc": "2026-06-15T11:44:58Z",
                    "timestamp_source": "payload",
                    "freshness_basis": "PAYLOAD_TIMESTAMP_OR_FILE_MTIME",
                    "max_age_seconds": 900,
                    "canonical_path": "data/runtime/runtime_safety_audit_config.json",
                    "consumer_pages": ["active_controls"],
                    "consumer_snapshots": ["dashboard_active_controls_snapshot.json"],
                    "blocks_dashboard_readiness": True,
                    "remediation_action": "Refresh safely.",
                }
            ]
        }
    )

    assert rows[0]["health_status"] == "BLOCKED"
    assert rows[0]["freshness_status"] == "CRITICAL_STALE"
    assert rows[0]["timestamp_source"] == "payload"
    assert rows[0]["remediation_action"] == "Refresh safely."
    for field in (
        "health_status",
        "freshness_status",
        "effective_timestamp_utc",
        "timestamp_source",
        "freshness_basis",
        "max_age_seconds",
        "remediation_action",
    ):
        assert field in SOURCE_COLUMNS


def test_static_safety_no_forbidden_runtime_calls() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = (
        root / "smartcrypto/ops/dashboard_snapshots/source_freshness.py",
        root / "smartcrypto/ops/dashboard_snapshots/source_closeout.py",
        root / "smartcrypto/dashboard/components/runtime_source_health.py",
    )
    source = "\n".join(path.read_text(encoding="utf-8").lower() for path in paths)

    for prohibited in (
        "import ccxt",
        "create_order(",
        "cancel_order(",
        "fetch_balance(",
        "fetch_open_orders(",
        "ordermanager(",
        "exchangegateway(",
        "notificationdispatcher(",
        "requests.post(",
        "shell=true",
    ):
        assert prohibited not in source
