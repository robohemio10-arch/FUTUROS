from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.paper_shadow_survivor_remediation_research import (
    SCHEMA_VERSION,
    build_paper_shadow_survivor_remediation_research_report,
    compute_survivor_remediation,
)


BAD_LONG = "include__symbol_norm_ETHUSDT__side_norm_long"
BAD_SHORT = "include__symbol_norm_ETHUSDT__side_norm_short"
GOOD_RULE = "include__symbol_norm_BTCUSDT__side_norm_long"


def _rows() -> list[dict[str, object]]:
    return [
        {
            "trade_id": "t1",
            "order_id": "o1",
            "symbol": "ETHUSDT",
            "side": "long",
            "pnl": -10.0,
            "would_allow": True,
            "would_block": False,
            "matched_survivor_rule_id": BAD_LONG,
        },
        {
            "trade_id": "t2",
            "order_id": "o2",
            "symbol": "ETHUSDT",
            "side": "long",
            "pnl": -5.0,
            "would_allow": True,
            "would_block": False,
            "matched_survivor_rule_id": BAD_LONG,
        },
        {
            "trade_id": "t3",
            "order_id": "o3",
            "symbol": "ETHUSDT",
            "side": "long",
            "pnl": 3.0,
            "would_allow": True,
            "would_block": False,
            "matched_survivor_rule_id": BAD_LONG,
        },
        {
            "trade_id": "t4",
            "order_id": "o4",
            "symbol": "ETHUSDT",
            "side": "short",
            "pnl": -2.0,
            "would_allow": True,
            "would_block": False,
            "matched_survivor_rule_id": BAD_SHORT,
        },
        {
            "trade_id": "t5",
            "order_id": "o5",
            "symbol": "BTCUSDT",
            "side": "long",
            "pnl": 8.0,
            "would_allow": True,
            "would_block": False,
            "matched_survivor_rule_id": GOOD_RULE,
        },
        {
            "trade_id": "t6",
            "order_id": "o6",
            "symbol": "BTCUSDT",
            "side": "short",
            "pnl": -1.0,
            "would_allow": False,
            "would_block": True,
            "matched_survivor_rule_id": None,
        },
        {
            "trade_id": "t7",
            "order_id": "o7",
            "symbol": "BTCUSDT",
            "side": "short",
            "pnl": 4.0,
            "would_allow": False,
            "would_block": True,
            "matched_survivor_rule_id": None,
        },
    ]


def _impact_payload() -> dict[str, object]:
    return {
        "schema_version": "paper_shadow_observation_daily_impact_report_v1",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "total_closed_trades": 7,
        "attributed_trade_count": 7,
        "unattributed_trade_count": 0,
        "would_allow_count": 5,
        "would_block_count": 2,
        "impact_summary": {
            "allowed_net_pnl": -6.0,
            "blocked_net_pnl": 3.0,
            "false_positive_count": 3,
            "preserved_loss_count": 1,
            "missed_opportunity_count": 1,
        },
        "survivor_rule_breakdown": [
            {
                "survivor_rule_id": BAD_LONG,
                "trades": 3,
                "net_pnl": -12.0,
                "false_positive_count": 2,
                "recommendation": "DISCARD_RESEARCH_ONLY",
            },
            {
                "survivor_rule_id": BAD_SHORT,
                "trades": 1,
                "net_pnl": -2.0,
                "false_positive_count": 1,
                "recommendation": "DISCARD_RESEARCH_ONLY",
            },
            {
                "survivor_rule_id": GOOD_RULE,
                "trades": 1,
                "net_pnl": 8.0,
                "false_positive_count": 0,
                "recommendation": "KEEP_PASSIVE_OBSERVATION_ONLY",
            },
        ],
    }


def _attribution_payload() -> dict[str, object]:
    rows = _rows()
    return {
        "schema_version": "paper_closed_trades_shadow_rule_attribution_v1",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "closed_trade_count": len(rows),
        "attributed_trade_count": len(rows),
        "unattributed_trade_count": 0,
        "attribution_table": rows,
    }


def test_no_runtime_read_by_default_blocks_safely(tmp_path: Path) -> None:
    report = build_paper_shadow_survivor_remediation_research_report(project_root=tmp_path)

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["status"] == "blocked"
    assert report["reason"] == "remediation_requires_explicit_runtime_read_or_in_memory_inputs"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["paper_observation_allowed"] is False
    assert report["write_performed"] is False


def test_missing_impact_report_blocks_structurally(tmp_path: Path) -> None:
    report = build_paper_shadow_survivor_remediation_research_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        impact_report="data/reports/missing_impact.json",
    )

    assert report["status"] == "blocked"
    assert report["source_status"] == "blocked"
    assert report["reason"] == "missing_impact_report"
    assert report["remediation_status"] == "blocked"


def test_discards_negative_survivors_research_only() -> None:
    remediation = compute_survivor_remediation(
        impact_report=_impact_payload(),
        attribution_report=_attribution_payload(),
    )

    summary = remediation["remediation_summary"]
    assert summary["discarded_survivor_count"] == 2
    assert summary["retained_survivor_count"] == 1
    assert summary["remediation_decision_research_only"] == "DISCARD_SURVIVOR_RESEARCH_ONLY"


def test_remediation_reduces_false_positive_count() -> None:
    remediation = compute_survivor_remediation(
        impact_report=_impact_payload(),
        attribution_report=_attribution_payload(),
    )

    summary = remediation["remediation_summary"]
    assert summary["remediated_false_positive_count"] == 0
    assert summary["false_positive_reduction"] == 3


def test_remediation_computes_allowed_net_pnl_delta() -> None:
    remediation = compute_survivor_remediation(
        impact_report=_impact_payload(),
        attribution_report=_attribution_payload(),
    )

    summary = remediation["remediation_summary"]
    assert summary["remediated_allowed_net_pnl"] == 8.0
    assert summary["allowed_net_pnl_delta"] == 14.0
    assert summary["missed_opportunity_delta"] == 1


def test_no_subfilter_without_available_fields() -> None:
    remediation = compute_survivor_remediation(
        impact_report=_impact_payload(),
        attribution_report=_attribution_payload(),
    )

    candidates = remediation["candidate_subfilters"]
    assert candidates
    assert all(item["status"] == "blocked" for item in candidates)
    assert all(item["reason"] == "insufficient_non_outcome_feature_fields" for item in candidates)


def test_candidate_subfilter_is_never_operational() -> None:
    remediation = compute_survivor_remediation(
        impact_report=_impact_payload(),
        attribution_report=_attribution_payload(),
    )

    for item in remediation["candidate_subfilters"]:
        assert item["research_only"] is True
        assert item["operational_authority"] is False
        assert item["can_activate_observer"] is False
        assert item["can_promote_rules"] is False


def test_recommendations_remain_research_only() -> None:
    remediation = compute_survivor_remediation(
        impact_report=_impact_payload(),
        attribution_report=_attribution_payload(),
    )

    recommendations = remediation["remediation_recommendations"]
    assert recommendations
    assert all(item["research_only"] is True for item in recommendations)
    assert all(item["operational_authority"] is False for item in recommendations)
    assert all(item["can_activate_observer"] is False for item in recommendations)


def test_report_never_allows_paper_observation(tmp_path: Path) -> None:
    report = build_paper_shadow_survivor_remediation_research_report(
        project_root=tmp_path,
        impact_payload=_impact_payload(),
        attribution_payload=_attribution_payload(),
    )

    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["remediation_decision"] == "MANTER_EM_RESEARCH"
    assert report["paper_observation_allowed"] is False
    assert report["ready_for_shadow_observation"] is False
    assert report["operational_authority"] is False
    assert report["remediation_summary"]["discarded_survivor_count"] == 2


def test_write_requires_explicit_flag_and_only_writes_reports(tmp_path: Path) -> None:
    no_write = build_paper_shadow_survivor_remediation_research_report(
        project_root=tmp_path,
        impact_payload=_impact_payload(),
        attribution_payload=_attribution_payload(),
    )
    assert no_write["write_performed"] is False
    assert not (tmp_path / "data").exists()

    written = build_paper_shadow_survivor_remediation_research_report(
        project_root=tmp_path,
        impact_payload=_impact_payload(),
        attribution_payload=_attribution_payload(),
        write=True,
        no_write=False,
    )

    json_report = tmp_path / "data" / "reports" / "paper_shadow_survivor_remediation_research_v1.json"
    markdown_report = tmp_path / "data" / "reports" / "paper_shadow_survivor_remediation_research_v1.md"
    assert written["write_performed"] is True
    assert json_report.exists()
    assert markdown_report.exists()
    assert not (tmp_path / "data" / "runtime").exists()
    assert not list(tmp_path.rglob("*.sqlite"))
    assert not list(tmp_path.rglob("*.parquet"))


def test_no_operational_authority_flags(tmp_path: Path) -> None:
    report = build_paper_shadow_survivor_remediation_research_report(
        project_root=tmp_path,
        impact_payload=_impact_payload(),
        attribution_payload=_attribution_payload(),
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
    script = Path("scripts/build_paper_shadow_survivor_remediation_research_v1.py")
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
