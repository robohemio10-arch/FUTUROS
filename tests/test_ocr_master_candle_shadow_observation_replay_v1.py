from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.ocr_master_candle_shadow_observation_replay import (
    SCHEMA_VERSION,
    build_shadow_observation_replay_report,
    compute_replay_metrics,
    replay_survivors_on_trades,
    survivor_matches_trade,
)


def _survivor() -> dict[str, object]:
    return {
        "survivor_rule_id": "survivor_btc_long",
        "survivor_expression": "symbol_norm == 'BTCUSDT' AND side_norm == 'long'",
        "dimensions": ["symbol_norm", "side_norm"],
        "values": ["BTCUSDT", "long"],
        "would_allow": True,
        "would_block": False,
        "opportunity_score": 0.72,
        "expected_value_delta": 0.2,
        "operational_authority": False,
        "paper_observation_allowed": False,
        "can_promote_rules": False,
    }


def _trades() -> list[dict[str, object]]:
    return [
        {"trade_id": "t1", "symbol": "BTCUSDT", "side": "long", "net_pnl": 10.0},
        {"trade_id": "t2", "symbol": "BTC/USDT", "side": "buy", "net_pnl": -3.0},
        {"trade_id": "t3", "symbol": "ETHUSDT", "side": "short", "net_pnl": 5.0},
        {"trade_id": "t4", "symbol": "ETHUSDT", "side": "long", "net_pnl": -2.0},
    ]


def test_no_runtime_read_by_default_blocks_safely(tmp_path: Path) -> None:
    report = build_shadow_observation_replay_report(project_root=tmp_path)

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["reason"] == "shadow_observation_replay_requires_explicit_runtime_read_or_in_memory_inputs"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["source_status"] == "blocked"
    assert report["write_performed"] is False
    assert report["replay_metrics"]["replay_trade_count"] == 0


def test_replay_contract_contains_required_fields(tmp_path: Path) -> None:
    report = build_shadow_observation_replay_report(
        project_root=tmp_path,
        survivor_records=[_survivor()],
        closed_trades=_trades(),
    )

    required = set(report["required_replay_fields"])
    assert required <= set(report["replay_metrics"])
    assert report["replay_semantics"]["operational_use"].startswith("forbidden")
    assert report["validation_errors"] == []


def test_replay_materializes_would_allow_and_would_block_from_fixture() -> None:
    rows = replay_survivors_on_trades([_survivor()], _trades())

    assert [row["would_allow"] for row in rows] == [True, True, False, False]
    assert [row["would_block"] for row in rows] == [False, False, True, True]
    assert rows[0]["matched_survivor_rule_id"] == "survivor_btc_long"
    assert rows[2]["matched_survivor_rule_id"] is None
    assert survivor_matches_trade(_survivor(), _trades()[0]) is True
    assert survivor_matches_trade(_survivor(), _trades()[2]) is False


def test_replay_metrics_are_deterministic() -> None:
    metrics_a = compute_replay_metrics([_survivor()], _trades())
    metrics_b = compute_replay_metrics([_survivor()], _trades())

    assert metrics_a == metrics_b
    assert metrics_a["replay_trade_count"] == 4
    assert metrics_a["would_allow_count"] == 2
    assert metrics_a["would_block_count"] == 2
    assert metrics_a["would_allow_net_pnl"] == 7.0
    assert metrics_a["baseline_net_pnl"] == 10.0
    assert metrics_a["expected_value_delta_total"] == 0.4
    assert metrics_a["expected_value_delta_mean"] == 0.2
    assert metrics_a["missed_opportunity_count"] == 1
    assert metrics_a["preserved_loss_count"] == 1
    assert metrics_a["false_positive_observation_count"] == 1
    assert metrics_a["survivor_attribution_table"][0]["would_allow_count"] == 2


def test_replay_preserves_research_only_safety_flags(tmp_path: Path) -> None:
    report = build_shadow_observation_replay_report(
        project_root=tmp_path,
        survivor_records=[_survivor()],
        closed_trades=_trades(),
    )

    assert report["research_only"] is True
    assert report["read_only"] is True
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["operational_authority"] is False
    assert report["paper_observation_allowed"] is False
    assert report["ready_for_shadow_observation"] is False
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["exchange_private_access"] is False


def test_replay_does_not_apply_or_register_shadow_rules(tmp_path: Path) -> None:
    report = build_shadow_observation_replay_report(
        project_root=tmp_path,
        survivor_records=[_survivor()],
        closed_trades=_trades(),
    )

    assert report["registers_shadow_rules"] is False
    assert report["applies_shadow_rules"] is False
    assert report["can_apply_to_freqtrade"] is False
    assert report["can_apply_to_risk_manager"] is False
    assert report["can_promote_rules"] is False
    assert report["updates_ai_shadow_runtime"] is False
    assert report["updates_freqtrade"] is False


def test_write_requires_explicit_flag_and_only_writes_research_report(tmp_path: Path) -> None:
    no_write = build_shadow_observation_replay_report(
        project_root=tmp_path,
        survivor_records=[_survivor()],
        closed_trades=_trades(),
    )
    assert no_write["write_performed"] is False
    assert not (tmp_path / "data").exists()

    written = build_shadow_observation_replay_report(
        project_root=tmp_path,
        survivor_records=[_survivor()],
        closed_trades=_trades(),
        write=True,
        no_write=False,
    )

    output = tmp_path / "data" / "reports" / "ocr_master_candle_shadow_observation_replay_v1.json"
    assert written["write_requested"] is True
    assert written["write_performed"] is True
    assert written["output_path"] == "data/reports/ocr_master_candle_shadow_observation_replay_v1.json"
    assert written["writes_runtime"] is False
    assert written["writes_sqlite"] is False
    assert written["writes_parquet"] is False
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"] == "MANTER_EM_RESEARCH"


def test_missing_sources_return_structured_blocked(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    report = build_shadow_observation_replay_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        observation_design_report=missing,
        legacy_trade_dataset=missing,
    )

    assert report["status"] == "blocked"
    assert report["input_mode"] == "runtime_read_requested"
    assert report["source_status"] == "blocked"
    assert report["reason"] == "source_path_missing"
    assert report["write_performed"] is False


def test_cli_no_runtime_json_executes() -> None:
    script = Path("scripts/build_ocr_master_candle_shadow_observation_replay_v1.py")
    completed = subprocess.run(
        [sys.executable, str(script), "--project-root", ".", "--no-write", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["status"] == "blocked"
    assert payload["input_mode"] == "no_runtime_rows_loaded"
    assert payload["write_performed"] is False
