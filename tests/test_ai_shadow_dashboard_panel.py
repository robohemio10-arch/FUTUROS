from __future__ import annotations

import json
from pathlib import Path

from smartcrypto.dashboard.ai_shadow_panel import (
    build_decision_table,
    load_ai_shadow_panel_state,
    recommended_command,
    validate_safety_status,
)


def report_payload(**overrides):
    payload = {
        "status": "OK",
        "rows_observed": 2,
        "shadow_entry_count": 1,
        "shadow_skip_count": 1,
        "blocked_count": 0,
        "probability_threshold": 0.6,
        "model_name": "random_forest_shadow_observer",
        "model_version": "random_forest_in_memory_research_v1",
        "model_source": "model_vs_baseline_financial_evaluation:random_forest",
        "leakage_status": "OK",
        "shadow_only": True,
        "dry_run": True,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "safety_status": {
            "shadow_only": True,
            "dry_run": True,
            "live_trading_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
        },
    }
    payload.update(overrides)
    return payload


def decision_payload(**overrides):
    payload = {
        "created_at": "2026-05-27T23:33:02Z",
        "symbol": "BTCUSDT",
        "open_1m_ts": "2026-04-29T22:10:00",
        "probability_win": 0.6738005317600624,
        "probability_threshold": 0.6,
        "decision": "SHADOW_ENTRY",
        "decision_reason": "probability_above_or_equal_threshold",
        "model_name": "random_forest_shadow_observer",
        "blocked_reason": None,
    }
    payload.update(overrides)
    return payload


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_parser_reads_valid_json_report(tmp_path) -> None:
    report_path = tmp_path / "report.json"
    decisions_path = tmp_path / "decisions.jsonl"
    write_json(report_path, report_payload())

    state = load_ai_shadow_panel_state(report_path, decisions_path)

    assert state["status"] == "OK"
    assert state["report"]["model_name"] == "random_forest_shadow_observer"
    assert state["files_present"]["report"] is True
    assert state["files_present"]["decisions"] is False


def test_parser_reads_valid_jsonl_decisions(tmp_path) -> None:
    report_path = tmp_path / "report.json"
    decisions_path = tmp_path / "decisions.jsonl"
    write_json(report_path, report_payload())
    write_jsonl(
        decisions_path,
        [
            decision_payload(decision="SHADOW_SKIP", probability_win=0.41),
            decision_payload(decision="SHADOW_ENTRY", probability_win=0.75),
        ],
    )

    state = load_ai_shadow_panel_state(report_path, decisions_path)

    assert len(state["decisions"]) == 2
    assert state["decision_table"][0]["probability_win"] == 0.41
    assert state["decision_table"][1]["decision"] == "SHADOW_ENTRY"


def test_missing_files_return_empty_state_without_exception(tmp_path) -> None:
    state = load_ai_shadow_panel_state(tmp_path / "missing.json", tmp_path / "missing.jsonl")

    assert state["status"] == "EMPTY"
    assert state["is_empty"] is True
    assert state["report"] == {}
    assert state["decisions"] == []
    assert "run_ai_shadow_entry_observer.py" in state["recommended_command"]


def test_loader_validates_safety_status() -> None:
    assert validate_safety_status(report_payload()) == []


def test_loader_alerts_when_order_submission_is_enabled(tmp_path) -> None:
    report_path = tmp_path / "report.json"
    decisions_path = tmp_path / "decisions.jsonl"
    unsafe = report_payload(
        order_submission_enabled=True,
        safety_status={
            "shadow_only": True,
            "dry_run": True,
            "live_trading_enabled": False,
            "order_submission_enabled": True,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
        },
    )
    write_json(report_path, unsafe)

    state = load_ai_shadow_panel_state(report_path, decisions_path)

    assert state["status"] == "SAFETY_ALERT"
    assert "order_submission_enabled_true" in state["safety_alerts"]


def test_decision_table_is_json_serializable() -> None:
    table = build_decision_table([decision_payload()])

    assert json.dumps(table, sort_keys=True)


def test_runner_command_is_read_only_instruction() -> None:
    command = recommended_command()

    assert "run_ai_shadow_entry_observer.py" in command
    assert "--dry-run true" in command
    assert "--shadow-only true" in command


def test_panel_module_does_not_reference_exchange_or_mutating_runtime_targets() -> None:
    text = Path("smartcrypto/dashboard/ai_shadow_panel.py").read_text(encoding="utf-8")
    forbidden = [
        "ccxt",
        "create_order",
        "cancel_order",
        "fetch_balance",
        ".env",
        "docker-compose",
        "START_PAPER_24H",
        "to_parquet",
        "to_csv",
    ]
    assert all(token not in text for token in forbidden)
