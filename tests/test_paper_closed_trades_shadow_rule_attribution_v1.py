from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.paper_closed_trades_shadow_rule_attribution import (
    SCHEMA_VERSION,
    attribute_closed_trades_to_shadow_replay,
    build_paper_closed_trades_shadow_rule_attribution_report,
    compute_attribution_metrics,
)


def _closed_trades() -> list[dict[str, object]]:
    return [
        {"trade_id": "t1", "symbol": "BTCUSDT", "side": "long", "pnl_fechado": 10.0},
        {"trade_id": "t2", "symbol": "BTC/USDT", "side": "buy", "pnl_fechado": -3.0},
        {"trade_id": "t3", "symbol": "ETHUSDT", "side": "short", "pnl_fechado": 5.0},
        {"trade_id": "t4", "symbol": "ETHUSDT", "side": "long", "pnl_fechado": -2.0},
    ]


def _replay_rows() -> list[dict[str, object]]:
    return [
        {
            "trade_id": "t1",
            "would_allow": True,
            "would_block": False,
            "matched_survivor_rule_id": "survivor_btc_long",
            "expected_value_delta": 0.2,
        },
        {
            "trade_id": "t2",
            "would_allow": True,
            "would_block": False,
            "matched_survivor_rule_id": "survivor_btc_long",
            "expected_value_delta": 0.2,
        },
        {
            "trade_id": "t3",
            "would_allow": False,
            "would_block": True,
            "matched_survivor_rule_id": "survivor_btc_long",
            "expected_value_delta": 0.2,
        },
        {
            "trade_id": "t4",
            "would_allow": False,
            "would_block": True,
            "matched_survivor_rule_id": "survivor_btc_long",
            "expected_value_delta": 0.2,
        },
    ]


def test_no_runtime_read_by_default_blocks_safely(tmp_path: Path) -> None:
    report = build_paper_closed_trades_shadow_rule_attribution_report(project_root=tmp_path)

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["reason"] == "paper_shadow_attribution_requires_explicit_runtime_read_or_in_memory_inputs"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["source_status"] == "blocked"
    assert report["write_performed"] is False
    assert report["closed_trade_count"] == 0


def test_missing_sources_return_structured_blocked(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    report = build_paper_closed_trades_shadow_rule_attribution_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        closed_trades_path=missing,
        shadow_replay_report=missing,
    )

    assert report["status"] == "blocked"
    assert report["input_mode"] == "runtime_read_requested"
    assert report["source_status"] == "blocked"
    assert report["reason"] == "source_path_missing"
    assert report["write_performed"] is False


def test_closed_trades_are_attributed_deterministically_from_fixture() -> None:
    rows_a = attribute_closed_trades_to_shadow_replay(_closed_trades(), _replay_rows())
    rows_b = attribute_closed_trades_to_shadow_replay(_closed_trades(), _replay_rows())

    assert rows_a == rows_b
    assert [row["trade_id"] for row in rows_a] == ["t1", "t2", "t3", "t4"]
    assert [row["attributed"] for row in rows_a] == [True, True, True, True]
    assert rows_a[0]["attribution_method"] == "shadow_replay_trade_id"
    assert rows_a[0]["operational_action_allowed"] is False
    assert rows_a[0]["can_be_used_as_signal"] is False
    assert rows_a[0]["can_be_used_as_veto"] is False


def test_would_allow_and_would_block_counts_are_consistent(tmp_path: Path) -> None:
    report = build_paper_closed_trades_shadow_rule_attribution_report(
        project_root=tmp_path,
        closed_trades=_closed_trades(),
        replay_rows=_replay_rows(),
    )

    assert report["closed_trade_count"] == 4
    assert report["attributed_trade_count"] == 4
    assert report["unattributed_trade_count"] == 0
    assert report["would_allow_count"] == 2
    assert report["would_block_count"] == 2


def test_missed_opportunity_preserved_loss_false_positive_semantics(tmp_path: Path) -> None:
    report = build_paper_closed_trades_shadow_rule_attribution_report(
        project_root=tmp_path,
        closed_trades=_closed_trades(),
        replay_rows=_replay_rows(),
    )

    assert report["missed_opportunity_count"] == 1
    assert report["preserved_loss_count"] == 1
    assert report["false_positive_observation_count"] == 1
    sample_by_id = {row["trade_id"]: row for row in report["attribution_table_sample"]}
    assert sample_by_id["t3"]["missed_opportunity"] is True
    assert sample_by_id["t4"]["preserved_loss"] is True
    assert sample_by_id["t2"]["false_positive_observation"] is True


def test_expected_value_delta_is_research_only(tmp_path: Path) -> None:
    rows = attribute_closed_trades_to_shadow_replay(_closed_trades(), _replay_rows())
    metrics = compute_attribution_metrics(rows)
    report = build_paper_closed_trades_shadow_rule_attribution_report(
        project_root=tmp_path,
        closed_trades=_closed_trades(),
        replay_rows=_replay_rows(),
    )

    assert metrics["expected_value_delta_total"] == 0.8
    assert metrics["expected_value_delta_mean"] == 0.2
    assert report["expected_value_delta_total"] == 0.8
    assert report["expected_value_delta_mean"] == 0.2
    assert all(row["expected_value_delta_research_only"] is True for row in rows)
    assert report["attribution_semantics"]["operational_use"].startswith("forbidden")


def test_no_operational_authority_flags(tmp_path: Path) -> None:
    report = build_paper_closed_trades_shadow_rule_attribution_report(
        project_root=tmp_path,
        closed_trades=_closed_trades(),
        replay_rows=_replay_rows(),
    )

    assert report["research_only"] is True
    assert report["read_only"] is True
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["operational_authority"] is False
    assert report["ready_for_shadow_observation"] is False
    assert report["can_promote_rules"] is False
    assert report["can_apply_to_freqtrade"] is False
    assert report["can_apply_to_risk_manager"] is False
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["writes_runtime"] is False
    assert report["safety_flags"]["operational_authority"] is False


def test_write_requires_explicit_flag_and_only_writes_research_json(tmp_path: Path) -> None:
    no_write = build_paper_closed_trades_shadow_rule_attribution_report(
        project_root=tmp_path,
        closed_trades=_closed_trades(),
        replay_rows=_replay_rows(),
    )
    assert no_write["write_performed"] is False
    assert not (tmp_path / "data").exists()

    written = build_paper_closed_trades_shadow_rule_attribution_report(
        project_root=tmp_path,
        closed_trades=_closed_trades(),
        replay_rows=_replay_rows(),
        write=True,
        no_write=False,
    )

    output = tmp_path / "data" / "reports" / "paper_closed_trades_shadow_rule_attribution_v1.json"
    assert written["write_requested"] is True
    assert written["write_performed"] is True
    assert written["output_path"] == "data/reports/paper_closed_trades_shadow_rule_attribution_v1.json"
    assert written["writes_runtime"] is False
    assert written["writes_sqlite"] is False
    assert written["writes_parquet"] is False
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert not (tmp_path / "data" / "runtime").exists()


def test_does_not_register_or_apply_shadow_rules(tmp_path: Path) -> None:
    report = build_paper_closed_trades_shadow_rule_attribution_report(
        project_root=tmp_path,
        closed_trades=_closed_trades(),
        replay_rows=_replay_rows(),
    )

    assert report["registers_shadow_rules"] is False
    assert report["applies_shadow_rules"] is False
    assert report["updates_ai_shadow_runtime"] is False
    assert report["updates_freqtrade"] is False
    assert report["updates_risk_manager"] is False
    assert report["gate_summary"]["result_can_be_used_for_operations"] is False


def test_cli_no_runtime_json_executes() -> None:
    script = Path("scripts/build_paper_closed_trades_shadow_rule_attribution_v1.py")
    completed = subprocess.run(
        [sys.executable, str(script), "--project-root", ".", "--no-write", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["status"] == "blocked"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["input_mode"] == "no_runtime_rows_loaded"
    assert payload["write_performed"] is False
