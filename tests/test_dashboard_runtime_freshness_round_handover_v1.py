from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

SCRIPT = Path("scripts/build_dashboard_runtime_freshness_round_handover_v1.py")
SPEC = importlib.util.spec_from_file_location("round_handover", SCRIPT)
assert SPEC is not None
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

NOW = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)


def test_handover_fails_closed_without_dashboard_snapshots(tmp_path: Path) -> None:
    payload = MODULE.build_handover(tmp_path, NOW)

    assert payload["status"] == "blocked"
    assert payload["closeout_state"]["dashboard_round_closed"] is True
    assert payload["closeout_state"]["runtime_closeout_ready"] is False
    assert payload["input_errors"] == [
        "missing_or_invalid:data/reports/dashboard_snapshot_build_summary.json",
        "missing_or_invalid:data/reports/dashboard_global_status_snapshot.json",
    ]


def test_handover_records_blocked_state_without_release(tmp_path: Path) -> None:
    reports = tmp_path / "data/reports"
    reports.mkdir(parents=True)
    (reports / "dashboard_snapshot_build_summary.json").write_text(
        '{"dashboard_status":"BLOCKED","combined_blocking_reasons":["readiness:BLOCKED"]}',
        encoding="utf-8",
    )
    (reports / "dashboard_global_status_snapshot.json").write_text(
        '{"dashboard_status":"BLOCKED","global_source_health_status":"BLOCKED",'
        '"global_blocking_reasons":["src_data_runtime_kill_switch_json:STALE"],'
        '"runtime_evidence_blocking_reasons":["readiness:BLOCKED"]}',
        encoding="utf-8",
    )

    payload = MODULE.build_handover(tmp_path, NOW)

    assert payload["status"] == "blocked"
    assert payload["open_blockers_total"] == 2
    assert payload["closeout_state"]["live_release_allowed"] is False
    assert payload["closeout_state"]["canary_release_allowed"] is False
    assert payload["closeout_state"]["order_submission_enabled"] is False


def test_handover_detects_unsafe_flags(tmp_path: Path) -> None:
    reports = tmp_path / "data/reports"
    reports.mkdir(parents=True)
    (reports / "dashboard_snapshot_build_summary.json").write_text(
        '{"dashboard_status":"OK","safety_flags":{"live_trading_enabled":true}}',
        encoding="utf-8",
    )
    (reports / "dashboard_global_status_snapshot.json").write_text(
        '{"dashboard_status":"OK","global_source_health_status":"OK",'
        '"global_blocking_reasons":[],"runtime_evidence_blocking_reasons":[]}',
        encoding="utf-8",
    )

    payload = MODULE.build_handover(tmp_path, NOW)

    assert payload["status"] == "blocked"
    assert "live_trading_enabled" in payload["safety_violations"]


def test_branch_inventory_is_complete(tmp_path: Path) -> None:
    payload = MODULE.build_handover(tmp_path, NOW)

    assert payload["round_branch_count"] == 10
    assert payload["round_branches"][-1] == "dashboard-runtime-freshness-round-handover-v1"
    assert payload["module_rows_total"] == 9


def test_script_is_read_only_boundary() -> None:
    content = SCRIPT.read_text(encoding="utf-8")

    forbidden = ("ccxt", "requests", "httpx", "aiohttp", "docker compose")
    for token in forbidden:
        assert token not in content

    assert "order_submission_enabled" in content
    assert "real_order_submission_enabled" in content
    assert "exchange_private_access" in content
