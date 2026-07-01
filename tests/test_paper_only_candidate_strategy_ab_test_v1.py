from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.paper_only_candidate_strategy_ab_test import (
    SCHEMA_VERSION,
    PaperOnlyCandidateDecisionFilter,
    build_paper_only_candidate_strategy_ab_test_report,
    compute_ab_test,
)


def _rows() -> list[dict[str, object]]:
    return [
        {"trade_id": "t1", "symbol": "ETHUSDT", "side": "long", "pnl": -10.0},
        {"trade_id": "t2", "symbol": "ETHUSDT", "side": "short", "pnl": -2.0},
        {"trade_id": "t3", "symbol": "BTCUSDT", "side": "long", "pnl": 8.0},
        {"trade_id": "t4", "symbol": "BTCUSDT", "side": "short", "pnl": -1.0},
        {"trade_id": "t5", "symbol": "SOLUSDT", "side": "long", "pnl": 3.0},
    ]


def _attribution_payload() -> dict[str, object]:
    rows = _rows()
    return {
        "schema_version": "paper_closed_trades_shadow_rule_attribution_v1",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "closed_trade_count": len(rows),
        "attributed_trade_count": len(rows),
        "attribution_table": rows,
    }


def _impact_payload() -> dict[str, object]:
    return {
        "schema_version": "paper_shadow_observation_daily_impact_report_v1",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "attributed_trade_count": 484,
        "would_allow_count": 290,
        "would_block_count": 194,
        "impact_summary": {
            "baseline_net_pnl": -68.41923069,
            "allowed_net_pnl": -51.26167099,
            "blocked_net_pnl": -17.1575597,
            "false_positive_count": 185,
        },
        "survivor_rule_breakdown": [
            {
                "survivor_rule_id": "include__symbol_norm_ETHUSDT__side_norm_long",
                "trades": 219,
                "net_pnl": -45.73341105,
                "false_positive_count": 145,
                "recommendation": "DISCARD_RESEARCH_ONLY",
            },
            {
                "survivor_rule_id": "include__symbol_norm_ETHUSDT__side_norm_short",
                "trades": 71,
                "net_pnl": -5.52825994,
                "false_positive_count": 40,
                "recommendation": "DISCARD_RESEARCH_ONLY",
            },
        ],
    }


def _remediation_payload() -> dict[str, object]:
    return {
        "schema_version": "paper_shadow_survivor_remediation_research_v1",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "remediation_summary": {
            "remediated_would_allow_count": 0,
            "remediated_would_block_count": 484,
            "remediated_allowed_net_pnl": 0.0,
            "false_positive_reduction": 185,
            "missed_opportunity_delta": 105,
            "discarded_survivor_count": 2,
        },
    }


def test_no_runtime_read_by_default_blocks_safely(tmp_path: Path) -> None:
    report = build_paper_only_candidate_strategy_ab_test_report(project_root=tmp_path)

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["status"] == "blocked"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["candidate_filter_active"] is False
    assert report["write_performed"] is False


def test_filter_blocks_ethusdt_long() -> None:
    decision = PaperOnlyCandidateDecisionFilter().evaluate({"symbol": "ETHUSDT", "side": "long"})

    assert decision.decision == "BLOCK"
    assert decision.reason == "discarded_negative_survivor_ethusdt_long"


def test_filter_blocks_ethusdt_short() -> None:
    decision = PaperOnlyCandidateDecisionFilter().evaluate({"symbol": "ETHUSDT", "side": "short"})

    assert decision.decision == "BLOCK"
    assert decision.reason == "discarded_negative_survivor_ethusdt_short"


def test_filter_allows_btc_long_short() -> None:
    filter_ = PaperOnlyCandidateDecisionFilter()

    assert filter_.evaluate({"symbol": "BTCUSDT", "side": "long"}).decision == "ALLOW"
    assert filter_.evaluate({"symbol": "BTCUSDT", "side": "short"}).decision == "ALLOW"


def test_filter_is_case_insensitive() -> None:
    decision = PaperOnlyCandidateDecisionFilter().evaluate({"symbol": "eth/usdt", "side": "SHORT"})

    assert decision.decision == "BLOCK"
    assert decision.symbol_norm == "ETHUSDT"
    assert decision.side_norm == "short"


def test_ab_test_computes_blocked_and_allowed_counts() -> None:
    result = compute_ab_test(attribution_report=_attribution_payload())

    candidate = result["candidate_summary"]
    assert candidate["blocked_trade_count"] == 2
    assert candidate["allowed_trade_count"] == 3
    assert candidate["blocked_eth_long_count"] == 1
    assert candidate["blocked_eth_short_count"] == 1
    assert candidate["candidate_allowed_net_pnl"] == 10.0


def test_ab_test_candidate_changes_paper_behavior() -> None:
    result = compute_ab_test(attribution_report=_attribution_payload())

    assert result["candidate_summary"]["paper_behavior_changed"] is True
    assert result["candidate_summary"]["live_behavior_changed"] is False
    assert result["ab_test_summary"]["paper_behavior_changed"] is True


def test_ab_test_preserves_live_safety_flags(tmp_path: Path) -> None:
    report = build_paper_only_candidate_strategy_ab_test_report(
        project_root=tmp_path,
        attribution_payload=_attribution_payload(),
    )

    assert report["paper_only"] is True
    assert report["candidate_only"] is True
    assert report["live_behavior_changed"] is False
    assert report["live_release_allowed"] is False
    assert report["canary_release_allowed"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False


def test_ab_test_never_sends_orders(tmp_path: Path) -> None:
    report = build_paper_only_candidate_strategy_ab_test_report(
        project_root=tmp_path,
        attribution_payload=_attribution_payload(),
    )

    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["changes_risk"] is False
    assert report["updates_freqtrade"] is False


def test_cli_no_runtime_json_executes() -> None:
    script = Path("scripts/run_paper_only_candidate_strategy_ab_test_v1.py")
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
    assert payload["candidate_filter_active"] is False
    assert payload["write_performed"] is False


def test_cli_runtime_read_write_json_executes(tmp_path: Path) -> None:
    attribution = tmp_path / "data" / "reports" / "paper_closed_trades_shadow_rule_attribution_v1.json"
    impact = tmp_path / "data" / "reports" / "paper_shadow_observation_daily_impact_report_v1.json"
    remediation = tmp_path / "data" / "reports" / "paper_shadow_survivor_remediation_research_v1.json"
    attribution.parent.mkdir(parents=True)
    attribution.write_text(json.dumps(_attribution_payload()), encoding="utf-8")
    impact.write_text(json.dumps(_impact_payload()), encoding="utf-8")
    remediation.write_text(json.dumps(_remediation_payload()), encoding="utf-8")

    script = Path.cwd() / "scripts" / "run_paper_only_candidate_strategy_ab_test_v1.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(tmp_path),
            "--allow-runtime-read",
            "--write",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "ok"
    assert payload["candidate_summary"]["paper_behavior_changed"] is True
    assert payload["candidate_summary"]["live_behavior_changed"] is False
    assert payload["write_performed"] is True


def test_write_only_outputs_data_reports(tmp_path: Path) -> None:
    report = build_paper_only_candidate_strategy_ab_test_report(
        project_root=tmp_path,
        attribution_payload=_attribution_payload(),
        write=True,
        no_write=False,
    )

    assert report["write_performed"] is True
    assert (tmp_path / "data" / "reports" / "paper_only_candidate_strategy_ab_test_v1.json").exists()
    assert (tmp_path / "data" / "reports" / "paper_only_candidate_strategy_ab_test_v1.md").exists()
    assert not (tmp_path / "data" / "runtime").exists()
    assert not list(tmp_path.rglob("*.sqlite"))
    assert not list(tmp_path.rglob("*.parquet"))


def test_manifest_safe_paths_only(tmp_path: Path) -> None:
    report = build_paper_only_candidate_strategy_ab_test_report(
        project_root=tmp_path,
        attribution_payload=_attribution_payload(),
        write=True,
        no_write=False,
    )

    assert str(report["output_path"]).startswith("data/reports/")
    assert str(report["markdown_output_path"]).startswith("data/reports/")
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["writes_parquet"] is False
