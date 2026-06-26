from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.paper_master_divergence_remediation import (
    SCHEMA_VERSION,
    build_paper_master_divergence_remediation_report,
    build_remediation_hypotheses,
    calculate_trade_kpis,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "build_paper_master_divergence_remediation_research_v1.py"
MODULE_PATH = (
    PROJECT_ROOT
    / "smartcrypto"
    / "research"
    / "paper_master_divergence_remediation"
    / "remediation.py"
)


def test_default_report_blocks_operation_and_confirms_divergence() -> None:
    report = build_paper_master_divergence_remediation_report()
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["divergence_confirmed"] is True
    assert report["paper_replicates_master_edge"] is False
    assert report["remediation_hypotheses_created"] is True
    assert report["gate_summary"]["critical_failed_gate_ids"] == []


def test_safety_flags_remain_non_operational() -> None:
    report = build_paper_master_divergence_remediation_report()
    expected_true = ["research_only", "read_only", "paper_only", "shadow_only"]
    expected_false = [
        "operational_authority",
        "release_authority",
        "can_apply_to_freqtrade",
        "can_apply_to_risk_manager",
        "can_promote_rules",
        "can_promote_model",
        "applies_remediation",
        "updates_freqtrade",
        "updates_risk_manager",
        "updates_qlib_runtime",
        "updates_ai_shadow_runtime",
        "sends_orders",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "live_release_allowed",
        "canary_release_allowed",
        "writes_data",
        "writes_runtime",
        "writes_reports",
        "writes_sqlite",
        "writes_parquet",
    ]
    for key in expected_true:
        assert report[key] is True, key
    for key in expected_false:
        assert report[key] is False, key


def test_canonical_baseline_metrics_are_preserved() -> None:
    report = build_paper_master_divergence_remediation_report()
    assert report["paper_kpis"]["trade_count"] == 239
    assert report["paper_kpis"]["net_pnl"] == -21.35477552
    assert report["master_kpis"]["trade_count"] == 243
    assert report["master_kpis"]["net_pnl"] == 143.166332
    assert report["divergence_metrics"]["paper_minus_master_net_pnl"] == -164.52110752
    assert report["canonical_cluster_evidence"]["eth_long_stop_loss_cluster"] == "critical"


def test_hypotheses_cover_required_problem_space() -> None:
    hypotheses = build_remediation_hypotheses()
    assert [item["hypothesis_id"] for item in hypotheses] == [f"H{number}" for number in range(1, 9)]
    problem_areas = {item["problem_area"] for item in hypotheses}
    assert "exit_risk_stop_loss" in problem_areas
    assert "symbol_side_cluster" in problem_areas
    assert "late_entry" in problem_areas
    assert "missed_master_winners" in problem_areas
    assert "opposite_side" in problem_areas
    assert "filter_quality" in problem_areas
    assert "data_coverage" in problem_areas
    assert "selector_expected_value" in problem_areas
    for hypothesis in hypotheses:
        assert hypothesis["operational_authority"] is False
        assert hypothesis["can_apply_to_freqtrade"] is False
        assert hypothesis["can_promote_rules"] is False


def test_in_memory_kpis_and_clusters_are_research_only() -> None:
    paper_rows = [
        {"symbol": "ETH/USDT:USDT", "side": "long", "exit_reason": "stop_loss", "net_pnl": -10.0, "duration_minutes": 12},
        {"symbol": "ETH/USDT:USDT", "side": "long", "exit_reason": "stop_loss", "net_pnl": -8.0, "duration_minutes": 18},
        {"symbol": "BTC/USDT:USDT", "side": "short", "exit_reason": "roi", "net_pnl": 5.0, "duration_minutes": 95},
    ]
    master_rows = [
        {"symbol": "ETH/USDT:USDT", "side": "short", "exit_reason": "roi", "net_pnl": 30.0, "duration_minutes": 70},
        {"symbol": "BTC/USDT:USDT", "side": "short", "exit_reason": "roi", "net_pnl": 15.0, "duration_minutes": 50},
    ]
    kpis = calculate_trade_kpis(paper_rows)
    assert kpis["trade_count"] == 3
    assert kpis["net_pnl"] == -13.0
    assert kpis["win_rate"] == 0.3333333333
    report = build_paper_master_divergence_remediation_report(paper_rows, master_rows)
    assert report["input_mode"] == "in_memory_rows_only"
    assert report["paper_replicates_master_edge"] is False
    assert report["cluster_summary"]
    assert report["operational_authority"] is False


def test_cli_no_write_outputs_expected_json_without_pythonpath() -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--project-root", str(PROJECT_ROOT), "--no-write", "--json"],
        cwd=Path(os.environ.get("TMP", os.environ.get("TEMP", "."))),
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["write_performed"] is False
    assert payload["input_mode"] == "no_runtime_rows_loaded"
    assert payload["branch_ready_for_operational_release"] is not True if "branch_ready_for_operational_release" in payload else True


def test_cli_write_request_is_blocked_by_readonly_contract() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--write", "--json"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["write_requested"] is True
    assert payload["write_performed"] is False
    assert payload["write_blocked_reason"] == "read_only_research_contract"


def test_source_does_not_contain_operational_execution_tokens() -> None:
    forbidden_tokens = [
        "create_order(",
        "fetch_balance(",
        "cancel_order(",
        "requests.post(",
        "httpx.post(",
        "aiohttp.ClientSession(",
        "dry_run=False",
        "live_trading_enabled=True",
        "canary_release_allowed=True",
    ]
    combined = MODULE_PATH.read_text(encoding="utf-8") + SCRIPT_PATH.read_text(encoding="utf-8")
    for token in forbidden_tokens:
        assert token not in combined


def test_cli_has_explicit_standalone_bootstrap_before_smartcrypto_import() -> None:
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "Path(__file__).resolve().parents[1]" in text
    assert "sys.path.insert(0, str(PROJECT_ROOT))" in text
    assert text.index("sys.path.insert(0, str(PROJECT_ROOT))") < text.index("smartcrypto.research")
