
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.ops.dashboard_snapshots.runtime_freshness_governance_closeout_index import (
    build_runtime_freshness_governance_closeout_index,
    load_runtime_freshness_governance_closeout_index_inputs,
)

NOW = datetime(2026, 6, 16, 0, 0, tzinfo=timezone.utc)


def test_governance_index_blocks_with_authoritative_blockers() -> None:
    payload = build_runtime_freshness_governance_closeout_index(
        now_utc=NOW,
        source_closeout={
            "dashboard_status": "BLOCKED",
            "global_source_health_status": "BLOCKED",
            "global_blocking_reasons": ["src_data_runtime_kill_switch_json:STALE"],
        },
        runtime_evidence_view={
            "runtime_evidence_status": "BLOCKED",
            "blocking_evidence_sources": ["readiness:BLOCKED"],
        },
        runtime_blockers_remediation={"status": "warning", "combined_blocking_reasons": []},
        runtime_blockers_operator_pack={"status": "warning"},
        runtime_blockers_closeout_evidence={"status": "warning", "closeout_allowed": False},
        runtime_evidence_freshness_remediation_producers={"status": "warning"},
        runtime_freshness_producer_contracts={"status": "warning"},
        runtime_freshness_producer_entrypoint_static_safety={"status": "ok"},
        runtime_freshness_post_refresh_evidence_gate={"status": "warning", "gate_allowed": False},
    )

    assert payload["status"] == "blocked"
    assert payload["closeout_ready"] is False
    assert payload["execution_allowed"] is False
    assert payload["safe_to_execute_from_dashboard"] is False
    assert payload["open_blockers_total"] == 2
    assert payload["chain_rows_total"] == 9


def test_governance_index_can_be_ok_only_when_chain_is_clean() -> None:
    ok_stage = {"status": "ok", "reason": "ok"}
    payload = build_runtime_freshness_governance_closeout_index(
        now_utc=NOW,
        source_closeout={
            "dashboard_status": "OK",
            "global_source_health_status": "OK",
            "global_blocking_reasons": [],
        },
        runtime_evidence_view={
            "runtime_evidence_status": "OK",
            "blocking_evidence_sources": [],
        },
        runtime_blockers_remediation=ok_stage,
        runtime_blockers_operator_pack=ok_stage,
        runtime_blockers_closeout_evidence={"status": "ok", "closeout_allowed": True},
        runtime_evidence_freshness_remediation_producers=ok_stage,
        runtime_freshness_producer_contracts=ok_stage,
        runtime_freshness_producer_entrypoint_static_safety=ok_stage,
        runtime_freshness_post_refresh_evidence_gate={"status": "ok", "gate_allowed": True},
    )

    assert payload["status"] == "ok"
    assert payload["closeout_ready"] is True
    assert payload["manual_closeout_allowed"] is True
    assert payload["open_blockers"] == []


def test_governance_index_blocks_unsafe_safety_flags() -> None:
    payload = build_runtime_freshness_governance_closeout_index(
        now_utc=NOW,
        source_closeout={
            "dashboard_status": "OK",
            "global_source_health_status": "OK",
            "global_blocking_reasons": [],
            "safety_flags": {"live_trading_enabled": True},
        },
        runtime_evidence_view={
            "runtime_evidence_status": "OK",
            "blocking_evidence_sources": [],
        },
        runtime_blockers_remediation={"status": "ok"},
        runtime_blockers_operator_pack={"status": "ok"},
        runtime_blockers_closeout_evidence={"status": "ok", "closeout_allowed": True},
        runtime_evidence_freshness_remediation_producers={"status": "ok"},
        runtime_freshness_producer_contracts={"status": "ok"},
        runtime_freshness_producer_entrypoint_static_safety={"status": "ok"},
        runtime_freshness_post_refresh_evidence_gate={"status": "ok", "gate_allowed": True},
    )

    assert payload["status"] == "blocked"
    assert "live_trading_enabled" in payload["safety_violations"]
    assert payload["closeout_ready"] is False


def test_loader_fails_closed_without_snapshot_reports(tmp_path: Path) -> None:
    inputs = load_runtime_freshness_governance_closeout_index_inputs(tmp_path)
    payload = build_runtime_freshness_governance_closeout_index(
        now_utc=NOW,
        **inputs,
    )

    assert payload["status"] == "blocked"
    assert payload["input_errors"] == [
        "missing_or_invalid:data/reports/dashboard_global_status_snapshot.json",
        "missing_or_invalid:data/reports/dashboard_snapshot_build_summary.json",
    ]


def test_component_module_is_read_only() -> None:
    component = Path(
        "smartcrypto/dashboard/components/runtime_freshness_governance_closeout_index.py"
    ).read_text(encoding="utf-8")

    forbidden = ("ccxt", "requests", "httpx", "aiohttp", "subprocess", "yaml")
    for token in forbidden:
        assert token not in component
    assert "write_text" not in component
    assert "open(" not in component
