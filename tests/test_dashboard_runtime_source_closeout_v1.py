from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.ops.dashboard_snapshots.build_context import create_dashboard_build_context
from smartcrypto.ops.dashboard_snapshots.builder_registry import build_all_dashboard_snapshots
from smartcrypto.ops.dashboard_snapshots.source_closeout import (
    RequiredLevel,
    RuntimeSourceDefinition,
    RuntimeSourceStatus,
    RuntimeSourceType,
    build_runtime_source_closeout,
    evaluate_source,
    page_status_from_sources,
)


NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def definition(
    path: str,
    level: RequiredLevel,
    *,
    schema: str | None = None,
    max_age_seconds: float | None = None,
) -> RuntimeSourceDefinition:
    return RuntimeSourceDefinition(
        source_id="src_test",
        canonical_path=path,
        display_name="Test Source",
        owner_domain="infrastructure",
        source_type=RuntimeSourceType.JSON_REPORT,
        required_level=level,
        expected_schema_version=schema,
        freshness_policy=(
            {"basis": "payload_timestamp_or_file_mtime", "max_age_seconds": max_age_seconds}
            if max_age_seconds is not None
            else None
        ),
        consumer_snapshots=("data/reports/dashboard_infrastructure_snapshot.json",),
        consumer_pages=("infrastructure",),
        missing_behavior="BLOCK_CONSUMER_PAGE" if level is RequiredLevel.REQUIRED else "DEGRADE",
        stale_behavior="BLOCK" if level is RequiredLevel.REQUIRED else "DEGRADE",
        future_source_pending_behavior="SHOW_PLANNED_PENDING_WITHOUT_FAILURE",
        operator_hint="Run the documented producer, then rebuild snapshots.",
        runbook_hint="Consult the infrastructure runbook.",
        safety_impact="Observability only; no trading authority.",
    )


def write_json(root: Path, relative: str, payload: object) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def test_required_source_missing_blocks_consumer_page(tmp_path: Path) -> None:
    result = evaluate_source(
        definition("data/reports/required.json", RequiredLevel.REQUIRED),
        tmp_path,
        NOW,
    )

    assert result["status"] == RuntimeSourceStatus.MISSING_REQUIRED.value
    assert result["blocks_page_operational_view"] is True
    assert result["blocks_dashboard_readiness"] is True


def test_optional_source_missing_does_not_block_dashboard(tmp_path: Path) -> None:
    result = evaluate_source(
        definition("data/reports/optional.json", RequiredLevel.OPTIONAL),
        tmp_path,
        NOW,
    )

    assert result["status"] == RuntimeSourceStatus.MISSING_OPTIONAL.value
    assert result["blocks_page_operational_view"] is False
    assert result["blocks_dashboard_readiness"] is False


def test_future_source_pending_is_explicit_not_failure(tmp_path: Path) -> None:
    result = evaluate_source(
        definition("data/reports/future.json", RequiredLevel.FUTURE_SOURCE_PENDING),
        tmp_path,
        NOW,
    )

    assert result["status"] == RuntimeSourceStatus.FUTURE_SOURCE_PENDING.value
    assert result["severity"] == "INFO"
    assert result["blocks_dashboard_readiness"] is False


def test_stale_required_source_degrades_or_blocks_page(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/stale.json",
        {"status": "ok", "last_updated_utc": "2026-06-15T11:00:00Z"},
    )
    result = evaluate_source(
        definition("data/reports/stale.json", RequiredLevel.REQUIRED, max_age_seconds=300),
        tmp_path,
        NOW,
    )

    assert result["status"] == RuntimeSourceStatus.STALE.value
    assert result["stale"] is True
    assert result["blocks_page_operational_view"] is True
    assert result["blocks_dashboard_readiness"] is True
    assert result["status"] != RuntimeSourceStatus.OK.value


def test_invalid_json_source_is_not_ok(tmp_path: Path) -> None:
    target = tmp_path / "data/reports/invalid.json"
    target.parent.mkdir(parents=True)
    target.write_text("{not-json", encoding="utf-8")

    result = evaluate_source(
        definition("data/reports/invalid.json", RequiredLevel.REQUIRED),
        tmp_path,
        NOW,
    )

    assert result["status"] == RuntimeSourceStatus.INVALID_JSON.value
    assert result["status"] != RuntimeSourceStatus.OK.value


def test_optional_invalid_json_degrades_consumer_page(tmp_path: Path) -> None:
    target = tmp_path / "data/reports/optional-invalid.json"
    target.parent.mkdir(parents=True)
    target.write_text("{not-json", encoding="utf-8")

    result = evaluate_source(
        definition("data/reports/optional-invalid.json", RequiredLevel.OPTIONAL),
        tmp_path,
        NOW,
    )

    assert result["blocks_page_operational_view"] is False
    assert page_status_from_sources([result], "OK") == "DEGRADED"


def test_invalid_schema_source_is_not_ok(tmp_path: Path) -> None:
    write_json(tmp_path, "data/reports/schema.json", {"schema_version": "wrong", "status": "ok"})

    result = evaluate_source(
        definition("data/reports/schema.json", RequiredLevel.REQUIRED, schema="expected_v1"),
        tmp_path,
        NOW,
    )

    assert result["status"] == RuntimeSourceStatus.INVALID_SCHEMA.value
    assert result["blocks_page_operational_view"] is True


def test_source_matrix_lists_all_eight_pages(tmp_path: Path) -> None:
    closeout = build_runtime_source_closeout(tmp_path, NOW)

    assert len(closeout["page_source_matrix"]) == 8
    assert {item["page_id"] for item in closeout["page_source_matrix"]} == {
        "infrastructure",
        "portfolio_risk",
        "grid_monitor",
        "opportunity_scanner",
        "ai_governance",
        "active_controls",
        "quantitative_reports",
        "alerts_messaging",
    }


def test_source_matrix_links_sources_to_snapshots(tmp_path: Path) -> None:
    closeout = build_runtime_source_closeout(tmp_path, NOW)

    assert closeout["source_matrix"]
    assert all(item["consumer_pages"] for item in closeout["source_matrix"])
    assert all(item["consumer_snapshots"] for item in closeout["source_matrix"])
    assert all(path.startswith("data/reports/dashboard_") for item in closeout["source_matrix"] for path in item["consumer_snapshots"])


def test_build_dashboard_snapshots_includes_runtime_source_closeout(tmp_path: Path) -> None:
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

    assert summary["dashboard_status"] in {"BLOCKED", "DEGRADED", "UNKNOWN", "OK"}
    assert summary["pages_total"] == 8
    assert summary["source_matrix"]
    assert len(summary["page_source_matrix"]) == 8
    assert summary["required_sources_missing"] > 0
    assert not (tmp_path / "output").exists()


def test_dashboard_runtime_source_closeout_static_safety() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = (
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
        "yaml.dump",
        "shell=true",
    ):
        assert prohibited not in source


def test_dashboard_source_closeout_does_not_create_fake_runtime_data(tmp_path: Path) -> None:
    before = list(tmp_path.rglob("*"))

    closeout = build_runtime_source_closeout(tmp_path, NOW)

    assert closeout["required_sources_missing"] > 0
    assert list(tmp_path.rglob("*")) == before
