from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.paper_shadow_observation_daily_impact_report import (
    SCHEMA_VERSION,
    build_paper_shadow_observation_daily_impact_report,
    compute_impact_report,
)


def _rows() -> list[dict[str, object]]:
    return [
        {
            "trade_id": "t1",
            "order_id": "o1",
            "symbol": "BTCUSDT",
            "side": "long",
            "open_time": "2026-06-01T10:00:00Z",
            "close_time": "2026-06-01T10:10:00Z",
            "pnl": 10.0,
            "would_allow": True,
            "would_block": False,
            "matched_survivor_rule_id": "survivor_good",
            "expected_value_delta": 0.4,
        },
        {
            "trade_id": "t2",
            "order_id": "o2",
            "symbol": "BTCUSDT",
            "side": "long",
            "open_time": "2026-06-01T11:00:00Z",
            "close_time": "2026-06-01T11:10:00Z",
            "pnl": -3.0,
            "would_allow": True,
            "would_block": False,
            "matched_survivor_rule_id": "survivor_bad",
            "expected_value_delta": -0.2,
        },
        {
            "trade_id": "t3",
            "order_id": "o3",
            "symbol": "ETHUSDT",
            "side": "short",
            "open_time": "2026-06-02T10:00:00Z",
            "close_time": "2026-06-02T10:10:00Z",
            "pnl": -2.0,
            "would_allow": False,
            "would_block": True,
            "matched_survivor_rule_id": None,
            "expected_value_delta": None,
        },
        {
            "trade_id": "t4",
            "order_id": "o4",
            "symbol": "ETHUSDT",
            "side": "short",
            "open_time": "2026-06-02T11:00:00Z",
            "close_time": "2026-06-02T11:10:00Z",
            "pnl": 5.0,
            "would_allow": False,
            "would_block": True,
            "matched_survivor_rule_id": None,
            "expected_value_delta": None,
        },
        {
            "trade_id": "t5",
            "order_id": "o5",
            "symbol": "BTCUSDT",
            "side": "short",
            "open_time": "2026-06-02T12:00:00Z",
            "close_time": "2026-06-02T12:10:00Z",
            "pnl": -4.0,
            "would_allow": True,
            "would_block": False,
            "matched_survivor_rule_id": "survivor_bad",
            "expected_value_delta": -0.2,
        },
    ]


def _attribution_payload(rows: list[dict[str, object]] | None = None) -> dict[str, object]:
    data = rows or _rows()
    return {
        "schema_version": "paper_closed_trades_shadow_rule_attribution_v1",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "closed_trade_count": len(data),
        "attributed_trade_count": len(data),
        "unattributed_trade_count": 0,
        "would_allow_count": sum(1 for row in data if row["would_allow"]),
        "would_block_count": sum(1 for row in data if row["would_block"]),
        "join_key_used": "order_id",
        "attribution_table": data,
        "paper_observation_allowed": False,
        "ready_for_shadow_observation": False,
        "operational_authority": False,
    }


def _replay_payload(rows: list[dict[str, object]] | None = None) -> dict[str, object]:
    data = rows or _rows()
    return {
        "schema_version": "ocr_master_candle_shadow_observation_replay_v1",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "replay_metrics": {
            "replay_trade_count": len(data),
            "baseline_net_pnl": sum(float(row["pnl"]) for row in data),
            "replay_rows": data,
        },
    }


def test_no_runtime_read_by_default_blocks_safely(tmp_path: Path) -> None:
    report = build_paper_shadow_observation_daily_impact_report(project_root=tmp_path)

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["status"] == "blocked"
    assert report["reason"] == "daily_impact_report_requires_explicit_runtime_read_or_in_memory_inputs"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["paper_observation_allowed"] is False
    assert report["ready_for_shadow_observation"] is False
    assert report["write_performed"] is False


def test_missing_attribution_report_blocks_structurally(tmp_path: Path) -> None:
    report = build_paper_shadow_observation_daily_impact_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        paper_attribution_report="data/reports/missing_attribution.json",
    )

    assert report["status"] == "blocked"
    assert report["source_status"] == "blocked"
    assert report["reason"] == "missing_paper_attribution_report"
    assert report["impact_report_status"] == "blocked"


def test_impact_report_computes_allowed_and_blocked_pnl() -> None:
    impact = compute_impact_report(attribution_report=_attribution_payload(), replay_report=_replay_payload())

    assert impact["total_closed_trades"] == 5
    assert impact["attributed_trade_count"] == 5
    assert impact["would_allow_count"] == 3
    assert impact["would_block_count"] == 2
    assert impact["impact_summary"]["allowed_net_pnl"] == 3.0
    assert impact["impact_summary"]["blocked_net_pnl"] == 3.0
    assert impact["impact_summary"]["baseline_net_pnl"] == 6.0


def test_classifies_false_positive_preserved_loss_and_missed_opportunity() -> None:
    impact = compute_impact_report(attribution_report=_attribution_payload(), replay_report=_replay_payload())
    summary = impact["impact_summary"]

    assert summary["false_positive_count"] == 2
    assert summary["false_positive_net_pnl"] == -7.0
    assert summary["preserved_loss_count"] == 1
    assert summary["preserved_loss_net_pnl"] == -2.0
    assert summary["missed_opportunity_count"] == 1
    assert summary["missed_opportunity_net_pnl"] == 5.0


def test_daily_breakdown_is_deterministic() -> None:
    first = compute_impact_report(attribution_report=_attribution_payload(), replay_report=_replay_payload())
    second = compute_impact_report(attribution_report=_attribution_payload(), replay_report=_replay_payload())

    assert first["daily_breakdown"] == second["daily_breakdown"]
    assert [row["impact_day"] for row in first["daily_breakdown"]] == ["2026-06-01", "2026-06-02"]


def test_symbol_side_breakdown_is_deterministic() -> None:
    impact = compute_impact_report(attribution_report=_attribution_payload(), replay_report=_replay_payload())

    keys = [(row["symbol"], row["side"]) for row in impact["symbol_side_breakdown"]]
    assert keys == [("BTCUSDT", "long"), ("BTCUSDT", "short"), ("ETHUSDT", "short")]


def test_survivor_recommendations_are_research_only() -> None:
    impact = compute_impact_report(attribution_report=_attribution_payload(), replay_report=_replay_payload())

    assert impact["survivor_recommendations"]
    assert all(item["research_only"] is True for item in impact["survivor_recommendations"])
    assert all(item["can_activate_observer"] is False for item in impact["survivor_recommendations"])
    assert all(item["can_promote_rules"] is False for item in impact["survivor_recommendations"])


def test_negative_survivor_is_discard_research_only() -> None:
    impact = compute_impact_report(attribution_report=_attribution_payload(), replay_report=_replay_payload())
    by_id = {item["survivor_rule_id"]: item for item in impact["survivor_recommendations"]}

    assert by_id["survivor_bad"]["recommendation"] == "DISCARD_RESEARCH_ONLY"


def test_report_never_allows_paper_observation(tmp_path: Path) -> None:
    report = build_paper_shadow_observation_daily_impact_report(
        project_root=tmp_path,
        attribution_payload=_attribution_payload(),
        replay_payload=_replay_payload(),
    )

    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["impact_report_decision"] == "MANTER_EM_RESEARCH"
    assert report["would_allow_count"] == 3
    assert report["would_block_count"] == 2
    assert report["daily_breakdown_count"] == 2
    assert report["paper_observation_allowed"] is False
    assert report["ready_for_shadow_observation"] is False
    assert report["operational_authority"] is False


def test_write_requires_explicit_flag_and_only_writes_reports(tmp_path: Path) -> None:
    no_write = build_paper_shadow_observation_daily_impact_report(
        project_root=tmp_path,
        attribution_payload=_attribution_payload(),
        replay_payload=_replay_payload(),
    )
    assert no_write["write_performed"] is False
    assert not (tmp_path / "data").exists()

    written = build_paper_shadow_observation_daily_impact_report(
        project_root=tmp_path,
        attribution_payload=_attribution_payload(),
        replay_payload=_replay_payload(),
        write=True,
        no_write=False,
    )

    json_report = tmp_path / "data" / "reports" / "paper_shadow_observation_daily_impact_report_v1.json"
    markdown_report = tmp_path / "data" / "reports" / "paper_shadow_observation_daily_impact_report_v1.md"
    assert written["write_performed"] is True
    assert json_report.exists()
    assert markdown_report.exists()
    assert not (tmp_path / "data" / "runtime").exists()
    assert not list(tmp_path.rglob("*.sqlite"))
    assert not list(tmp_path.rglob("*.parquet"))


def test_no_operational_authority_flags(tmp_path: Path) -> None:
    report = build_paper_shadow_observation_daily_impact_report(
        project_root=tmp_path,
        attribution_payload=_attribution_payload(),
        replay_payload=_replay_payload(),
    )

    assert report["research_only"] is True
    assert report["read_only"] is True
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["can_apply_to_freqtrade"] is False
    assert report["can_apply_to_risk_manager"] is False
    assert report["can_promote_rules"] is False
    assert report["can_promote_model"] is False
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["writes_parquet"] is False


def test_cli_no_runtime_json_executes() -> None:
    script = Path("scripts/build_paper_shadow_observation_daily_impact_report_v1.py")
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
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["paper_observation_allowed"] is False
    assert payload["ready_for_shadow_observation"] is False
    assert payload["write_performed"] is False
