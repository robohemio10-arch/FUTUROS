from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path


SCRIPT_NAMES = (
    "audit_binance_vs_bitradex_1m.py",
    "audit_trades_xlsx_vs_binance_1m.py",
    "audit_trades_xlsx_vs_binance_1m_mdy.py",
    "build_trade_quality_gate_binance_1m.py",
    "build_training_dataset_quality_gated_binance_1m.py",
    "diagnose_date_shift_patterns.py",
    "diagnose_inside_trade_price_mismatch.py",
    "diagnose_price_repair_candidates.py",
    "diagnose_trade_binance_coverage.py",
    "diagnose_trade_datetime_parse.py",
    "download_binance_1m_for_trades_range.py",
    "run_paper_risk_sizing_quality_gated.py",
    "run_trade_block_monte_carlo_quality_gated_10_workers.py",
    "run_trade_monte_carlo_quality_gated_10_workers.py",
    "run_v13_quality_gated_independent_baseline.py",
    "run_v13_quality_gated_independent_baseline_strict.py",
    "analyze_extratrees_050_fold_stability.py",
    "analyze_v13_quality_gated_threshold_uplift.py",
    "run_ai_shadow_filter_extratrees_050_contract_test.py",
    "train_ai_shadow_filter_extratrees_050.py",
)


def script_paths() -> list[Path]:
    return [Path("scripts") / name for name in SCRIPT_NAMES]


def test_candidate_scripts_exist_with_docstring_and_argparse_contract() -> None:
    for path in script_paths():
        assert path.exists(), path
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        assert ast.get_docstring(tree), path
        assert "main_for(SPEC)" in text, path


def test_common_runtime_blocks_live_and_order_flags() -> None:
    common = Path("scripts/audit_diagnostic_common.py").read_text(encoding="utf-8")

    assert "LIVE_ENABLED" in common
    assert "ORDER_SUBMISSION_ENABLED" in common
    assert "REAL_ORDER_SUBMISSION_ENABLED" in common
    assert "output_report_must_be_runtime_path" in common


def test_scripts_help_runs_without_touching_runtime_state() -> None:
    for path in script_paths()[:3]:
        result = subprocess.run(
            [sys.executable, str(path), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "--runtime-mode" in result.stdout
        assert "--write-report" in result.stdout
