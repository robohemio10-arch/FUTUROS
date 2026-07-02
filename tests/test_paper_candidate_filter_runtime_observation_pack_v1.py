from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.ops.paper_candidate_filter_runtime_observation_pack import (
    SAFETY_FLAGS,
    build_paper_candidate_filter_runtime_observation_pack,
)


def _ab_test_report() -> dict[str, object]:
    return {
        "schema_version": "paper_only_candidate_strategy_ab_test_v1",
        "status": "ok",
        "baseline_summary": {
            "baseline_trade_count": 484,
            "baseline_net_pnl": -68.41923069,
        },
        "candidate_summary": {
            "blocked_trade_count": 290,
            "allowed_trade_count": 194,
            "blocked_eth_long_count": 219,
            "blocked_eth_short_count": 71,
            "candidate_allowed_net_pnl": -17.1575597,
            "candidate_vs_baseline_net_pnl_delta": 51.26167099,
        },
    }


def _daily_impact_report() -> dict[str, object]:
    return {
        "schema_version": "paper_shadow_observation_daily_impact_report_v1",
        "total_closed_trades": 484,
        "impact_summary": {
            "allowed_net_pnl": -17.1575597,
            "baseline_net_pnl": -68.41923069,
        },
    }


def _closed_trades_contract() -> dict[str, object]:
    return {
        "schema_version": "paper_closed_trades_readonly_source_contract_v1",
        "normalized_closed_trade_count": 484,
    }


def _decision_events() -> list[dict[str, object]]:
    return [
        {
            "runtime_wiring_schema_version": "paper_candidate_filter_runtime_wiring_v1",
            "generated_at_utc": "2026-07-02T12:00:00+00:00",
            "decision": "BLOCK",
            "symbol_norm": "ETHUSDT",
            "side_norm": "long",
            "paper_candidate_filter_called": True,
            "runtime_wiring_status": "enabled",
        },
        {
            "runtime_wiring_schema_version": "paper_candidate_filter_runtime_wiring_v1",
            "generated_at_utc": "2026-07-02T12:01:00+00:00",
            "decision": "BLOCK",
            "symbol_norm": "ETHUSDT",
            "side_norm": "short",
            "paper_candidate_filter_called": True,
            "runtime_wiring_status": "enabled",
        },
        {
            "runtime_wiring_schema_version": "paper_candidate_filter_runtime_wiring_v1",
            "generated_at_utc": "2026-07-02T12:02:00+00:00",
            "decision": "ALLOW",
            "symbol_norm": "BTCUSDT",
            "side_norm": "long",
            "paper_candidate_filter_called": True,
            "runtime_wiring_status": "enabled",
        },
        {
            "runtime_wiring_schema_version": "paper_candidate_filter_runtime_wiring_v1",
            "generated_at_utc": "2026-07-02T12:03:00+00:00",
            "decision": "ALLOW",
            "symbol_norm": "BTCUSDT",
            "side_norm": "short",
            "paper_candidate_filter_called": True,
            "runtime_wiring_status": "enabled",
        },
    ]


def _write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_fixture_reports(root: Path, *, with_events: bool = False) -> dict[str, Path]:
    reports = root / "data" / "reports"
    paths = {
        "ab": _write_json(reports / "paper_only_candidate_strategy_ab_test_v1.json", _ab_test_report()),
        "impact": _write_json(reports / "paper_shadow_observation_daily_impact_report_v1.json", _daily_impact_report()),
        "contract": _write_json(reports / "paper_closed_trades_readonly_source_contract_v1.json", _closed_trades_contract()),
    }
    if with_events:
        paths["events"] = _write_json(reports / "paper_candidate_filter_runtime_wiring_v1.json", {"decision_events": _decision_events()})
    return paths


def test_no_runtime_read_by_default_blocks_safely(tmp_path: Path) -> None:
    report = build_paper_candidate_filter_runtime_observation_pack(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["write_performed"] is False
    assert report["sends_orders"] is False


def test_observation_pack_loads_baseline_reports_from_fixtures(tmp_path: Path) -> None:
    _write_fixture_reports(tmp_path)
    report = build_paper_candidate_filter_runtime_observation_pack(project_root=tmp_path, allow_runtime_read=True)

    assert report["baseline_trade_count"] == 484
    assert report["baseline_blocked_trade_count"] == 290
    assert report["baseline_allowed_trade_count"] == 194
    assert report["baseline_net_pnl"] == -68.41923069
    assert report["candidate_expected_net_pnl"] == -17.1575597
    assert report["candidate_expected_delta"] == 51.26167099


def test_observation_pack_detects_no_runtime_events_as_waiting(tmp_path: Path) -> None:
    _write_fixture_reports(tmp_path)
    report = build_paper_candidate_filter_runtime_observation_pack(project_root=tmp_path, allow_runtime_read=True)

    assert report["status"] == "blocked"
    assert report["reason"] == "no_post_wiring_runtime_observation_events_found"
    assert report["observation_status"] == "waiting_for_runtime_evidence"
    assert report["post_wiring_closed_trade_count"] == 0
    assert report["decision_events_loaded"] is False
    assert report["recommended_next_action"] == "rodar_paper_candidate_filtrado_e_reexecutar_observation_pack"


def test_observation_pack_counts_block_and_allow_events(tmp_path: Path) -> None:
    _write_fixture_reports(tmp_path, with_events=True)
    report = build_paper_candidate_filter_runtime_observation_pack(project_root=tmp_path, allow_runtime_read=True)

    assert report["status"] == "ok"
    assert report["decision_events_loaded"] is True
    assert report["paper_candidate_filter_called"] is True
    assert report["decision_event_count"] == 4
    assert report["block_event_count"] == 2
    assert report["allow_event_count"] == 2


def test_observation_pack_counts_eth_block_events(tmp_path: Path) -> None:
    _write_fixture_reports(tmp_path, with_events=True)
    report = build_paper_candidate_filter_runtime_observation_pack(project_root=tmp_path, allow_runtime_read=True)

    assert report["ethusdt_long_block_event_count"] == 1
    assert report["ethusdt_short_block_event_count"] == 1
    assert report["post_wiring_ethusdt_trade_count"] == 2


def test_observation_pack_counts_btc_allow_events(tmp_path: Path) -> None:
    _write_fixture_reports(tmp_path, with_events=True)
    report = build_paper_candidate_filter_runtime_observation_pack(project_root=tmp_path, allow_runtime_read=True)

    assert report["btcusdt_long_allow_event_count"] == 1
    assert report["btcusdt_short_allow_event_count"] == 1
    assert report["post_wiring_btcusdt_trade_count"] == 2


def test_observation_pack_preserves_safety_flags(tmp_path: Path) -> None:
    report = build_paper_candidate_filter_runtime_observation_pack(
        project_root=tmp_path,
        ab_test_payload=_ab_test_report(),
        decision_event_payloads=_decision_events(),
    )

    for key, expected in SAFETY_FLAGS.items():
        assert report[key] is expected
        assert report["safety_flags"][key] is expected


def test_observation_pack_never_sends_orders(tmp_path: Path) -> None:
    report = build_paper_candidate_filter_runtime_observation_pack(
        project_root=tmp_path,
        ab_test_payload=_ab_test_report(),
        decision_event_payloads=_decision_events(),
    )

    assert report["sends_orders"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False
    assert report["changes_risk"] is False


def test_observation_pack_write_only_data_reports(tmp_path: Path) -> None:
    _write_fixture_reports(tmp_path, with_events=True)
    report = build_paper_candidate_filter_runtime_observation_pack(
        project_root=tmp_path,
        allow_runtime_read=True,
        write=True,
        no_write=False,
    )

    assert report["write_performed"] is True
    assert report["output_path"] == "data/reports/paper_candidate_filter_runtime_observation_pack_v1.json"
    assert report["markdown_output_path"] == "data/reports/paper_candidate_filter_runtime_observation_pack_v1.md"
    assert (tmp_path / report["output_path"]).exists()
    assert (tmp_path / report["markdown_output_path"]).exists()


def test_cli_no_runtime_json_executes() -> None:
    script = Path("scripts/build_paper_candidate_filter_runtime_observation_pack_v1.py")
    completed = subprocess.run(
        [sys.executable, str(script), "--project-root", ".", "--no-write", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "blocked"
    assert payload["input_mode"] == "no_runtime_rows_loaded"
    assert payload["write_performed"] is False
    assert payload["sends_orders"] is False


def test_cli_runtime_read_write_json_executes(tmp_path: Path) -> None:
    paths = _write_fixture_reports(tmp_path, with_events=True)
    script = Path("scripts/build_paper_candidate_filter_runtime_observation_pack_v1.py")
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(tmp_path),
            "--allow-runtime-read",
            "--ab-test-report",
            str(paths["ab"]),
            "--daily-impact-report",
            str(paths["impact"]),
            "--closed-trades-contract",
            str(paths["contract"]),
            "--decision-events",
            str(paths["events"]),
            "--write",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "ok"
    assert payload["decision_events_loaded"] is True
    assert payload["write_performed"] is True
    assert payload["block_event_count"] == 2
    assert payload["allow_event_count"] == 2
