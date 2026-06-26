from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.paper_master_divergence_oos_slice_metrics import (
    SCHEMA_VERSION,
    build_oos_slice_metrics_report,
    compute_slice_metrics,
    run_oos_slice_metrics_research,
)
from smartcrypto.research.paper_master_divergence_oos_slice_metrics.slice_metrics import (
    duration_bucket,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "build_paper_master_divergence_oos_slice_metrics_v1.py"


FIXTURE_ROWS = [
    {
        "trade_id": "t1",
        "day": "2026-06-10",
        "symbol": "ETH/USDT:USDT",
        "side": "long",
        "exit_reason": "stop_loss",
        "duration_minutes": 12,
        "net_pnl": -10.0,
        "covered_feature_subset": True,
        "candidate_rule_triggered": True,
    },
    {
        "trade_id": "t2",
        "day": "2026-06-10",
        "symbol": "ETH/USDT:USDT",
        "side": "long",
        "exit_reason": "roi",
        "duration_minutes": 95,
        "net_pnl": 6.0,
        "covered_feature_subset": True,
        "candidate_rule_triggered": False,
    },
    {
        "trade_id": "t3",
        "day": "2026-06-11",
        "symbol": "BTC/USDT:USDT",
        "side": "short",
        "exit_reason": "stop_loss",
        "duration_minutes": 22,
        "net_pnl": -4.0,
        "covered_feature_subset": False,
        "candidate_rule_triggered": True,
    },
    {
        "trade_id": "t4",
        "day": "2026-06-11",
        "symbol": "BTC/USDT:USDT",
        "side": "long",
        "exit_reason": "roi",
        "duration_minutes": 240,
        "net_pnl": 8.0,
        "covered_feature_subset": True,
        "candidate_rule_triggered": True,
    },
    {
        "trade_id": "t5",
        "day": "2026-06-12",
        "symbol": "ETH/USDT:USDT",
        "side": "short",
        "exit_reason": "stop_loss",
        "duration_minutes": 80,
        "net_pnl": -3.0,
        "covered_feature_subset": True,
        "candidate_rule_triggered": False,
    },
]


def test_no_runtime_report_preserves_research_only_safety_contract() -> None:
    report = build_oos_slice_metrics_report(PROJECT_ROOT)

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["research_only"] is True
    assert report["read_only"] is True
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["operational_authority"] is False
    assert report["can_apply_to_freqtrade"] is False
    assert report["can_apply_to_risk_manager"] is False
    assert report["can_promote_rules"] is False
    assert report["can_promote_model"] is False
    assert report["updates_freqtrade"] is False
    assert report["updates_risk_manager"] is False
    assert report["updates_qlib_runtime"] is False
    assert report["updates_ai_shadow_runtime"] is False
    assert report["sends_orders"] is False
    assert report["live_release_allowed"] is False
    assert report["canary_release_allowed"] is False


def test_no_runtime_report_declares_required_oos_dimensions_and_blockers() -> None:
    report = build_oos_slice_metrics_report(PROJECT_ROOT)

    assert report["hypothesis_scope"] == ["H1", "H2", "H6"]
    assert report["oos_slice_dimensions"] == [
        "day",
        "symbol",
        "side",
        "exit_reason",
        "duration_bucket",
        "covered_vs_uncovered",
    ]
    assert report["oos_slice_metrics_computed"] is False
    assert report["slice_metrics_status"] == "blocked_no_rows_loaded"
    assert report["oos_validation_required"] is True
    assert report["oos_validated"] is False
    assert report["ready_for_candidate_registry"] is False
    assert report["remediation_application_allowed"] is False


def test_compute_slice_metrics_from_fixture_rows() -> None:
    result = compute_slice_metrics(FIXTURE_ROWS)

    assert result["oos_slice_metrics_computed"] is True
    assert result["observation_count"] == 5
    assert result["slice_count"] > 0
    assert result["global_metrics"]["trade_count"] == 5
    assert result["global_metrics"]["net_pnl"] == -3.0
    assert result["global_metrics"]["profit_factor"] == round(14.0 / 17.0, 8)
    assert result["global_metrics"]["win_rate"] == 0.4
    assert result["global_metrics"]["false_positive_count"] == 1
    assert result["global_metrics"]["false_negative_count"] == 1
    assert result["global_metrics"]["precision"] == round(2 / 3, 8)
    assert result["global_metrics"]["recall"] == round(2 / 3, 8)


def test_fixture_report_contains_h1_h2_h6_metrics() -> None:
    report = build_oos_slice_metrics_report(PROJECT_ROOT, rows=FIXTURE_ROWS)

    assert report["input_mode"] == "in_memory_rows_loaded"
    assert report["oos_slice_metrics_computed"] is True
    hypothesis_metrics = {
        item["hypothesis_id"]: item["metrics"] for item in report["hypothesis_slice_metrics"]
    }
    assert set(hypothesis_metrics) == {"H1", "H2", "H6"}
    assert hypothesis_metrics["H1"]["trade_count"] == 2
    assert hypothesis_metrics["H1"]["net_pnl"] == -14.0
    assert hypothesis_metrics["H2"]["trade_count"] == 2
    assert hypothesis_metrics["H2"]["net_pnl"] == -4.0
    assert hypothesis_metrics["H6"]["trade_count"] == 3
    assert hypothesis_metrics["H6"]["simulated_removed_pnl_delta"] == 6.0
    assert all(item["oos_validated"] is False for item in report["hypothesis_slice_metrics"])


def test_duration_bucket_contract() -> None:
    assert duration_bucket(0) == "<15m"
    assert duration_bucket(14.99) == "<15m"
    assert duration_bucket(15) == "15-30m"
    assert duration_bucket(30) == "30-60m"
    assert duration_bucket(60) == "1-3h"
    assert duration_bucket(180) == "3-6h"
    assert duration_bucket(360) == ">6h"


def test_gate_summary_has_no_failed_or_critical_failed_gates() -> None:
    report = build_oos_slice_metrics_report(PROJECT_ROOT, rows=FIXTURE_ROWS)

    assert report["gate_summary"]["gate_count"] == 7
    assert report["gate_summary"]["failed_gate_count"] == 0
    assert report["gate_summary"]["failed_gate_ids"] == []
    assert report["gate_summary"]["critical_failed_gate_ids"] == []


def test_no_write_default_does_not_create_output(tmp_path: Path) -> None:
    output_path = tmp_path / "should_not_exist.json"

    report = run_oos_slice_metrics_research(
        PROJECT_ROOT,
        write=False,
        output_path=output_path,
        rows=FIXTURE_ROWS,
    )

    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert report["writes_reports"] is False
    assert not output_path.exists()


def test_write_mode_is_explicit_and_research_only(tmp_path: Path) -> None:
    output_path = tmp_path / "report.json"

    report = run_oos_slice_metrics_research(
        PROJECT_ROOT,
        write=True,
        output_path=output_path,
        rows=FIXTURE_ROWS,
    )

    assert output_path.exists()
    assert report["write_requested"] is True
    assert report["write_performed"] is True
    assert report["writes_reports"] is True
    assert report["operational_authority"] is False
    assert report["sends_orders"] is False
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["schema_version"] == SCHEMA_VERSION


def test_cli_outputs_valid_no_write_json() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--project-root",
            str(PROJECT_ROOT),
            "--no-write",
            "--json",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["status"] == "blocked"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["write_performed"] is False
    assert payload["oos_slice_metrics_computed"] is False


def test_cli_can_load_explicit_input_json(tmp_path: Path) -> None:
    input_path = tmp_path / "rows.json"
    input_path.write_text(json.dumps({"observations": FIXTURE_ROWS}), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--project-root",
            str(PROJECT_ROOT),
            "--input-json",
            str(input_path),
            "--no-write",
            "--json",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["input_mode"] == "in_memory_rows_loaded"
    assert payload["observation_count"] == 5
    assert payload["global_metrics"]["false_positive_count"] == 1


def test_cli_is_standalone_without_pythonpath() -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--project-root",
            str(PROJECT_ROOT),
            "--no-write",
            "--json",
        ],
        cwd=Path(os.environ.get("TMP", os.environ.get("TEMP", "."))),
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["research_only"] is True
    assert payload["operational_authority"] is False


def test_script_import_via_runpy_does_not_execute_main() -> None:
    namespace = runpy.run_path(str(SCRIPT_PATH), run_name="__not_main__")

    assert "main" in namespace
    assert "parse_args" in namespace
