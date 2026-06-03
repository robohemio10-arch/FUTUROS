from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.ml.ai_shadow_financial_evaluation import (
    compute_financial_metrics,
    evaluate_ai_shadow_financial_thresholds,
    evaluate_ai_shadow_financial_thresholds_frame,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def outcome_rows() -> list[dict]:
    return [
        {
            "decision_id": "d1",
            "matched": True,
            "action_shadow": "AI_ACCEPT",
            "probability": 0.82,
            "target_return": 0.05,
        },
        {
            "decision_id": "d2",
            "matched": True,
            "action_shadow": "AI_ACCEPT",
            "probability": 0.76,
            "target_return": 0.03,
        },
        {
            "decision_id": "d3",
            "matched": True,
            "action_shadow": "AI_ACCEPT",
            "probability": 0.68,
            "target_return": -0.01,
        },
        {
            "decision_id": "d4",
            "matched": True,
            "action_shadow": "AI_REJECT",
            "probability": 0.44,
            "target_return": -0.04,
        },
        {
            "decision_id": "d5",
            "matched": True,
            "action_shadow": "SHADOW_ENTRY",
            "probability": 0.62,
            "target_return": 0.02,
        },
        {
            "decision_id": "d6",
            "matched": True,
            "action_shadow": "SHADOW_SKIP",
            "probability": 0.48,
            "target_return": -0.03,
        },
        {
            "decision_id": "d7",
            "matched": False,
            "action_shadow": "AI_ACCEPT",
            "probability": 0.91,
            "target_return": 0.99,
        },
    ]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True))
            handle.write("\n")


def test_financial_evaluation_blocks_missing_input(tmp_path: Path) -> None:
    report = evaluate_ai_shadow_financial_thresholds(
        input_path=tmp_path / "missing.jsonl",
        report_path=tmp_path / "report.json",
        strict=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_input"


def test_financial_evaluation_blocks_missing_required_columns(tmp_path: Path) -> None:
    source = tmp_path / "bad.jsonl"
    write_jsonl(source, [{"matched": True, "probability": 0.7}])

    report = evaluate_ai_shadow_financial_thresholds(
        input_path=source,
        report_path=tmp_path / "report.json",
        strict=True,
    )

    assert report["status"] == "blocked"
    assert "missing_required_columns" in report["reason"]
    assert "target_return_or_pnl" in report["missing_required_columns"]


def test_financial_evaluation_blocks_no_matched_outcomes(tmp_path: Path) -> None:
    source = tmp_path / "outcomes.jsonl"
    rows = [{**row, "matched": False} for row in outcome_rows()]
    write_jsonl(source, rows)

    report = evaluate_ai_shadow_financial_thresholds(input_path=source, report_path=None)

    assert report["status"] == "blocked"
    assert report["reason"] == "no_matched_outcomes"
    assert report["matched_outcomes"] == 0


def test_financial_evaluation_blocks_unsafe_safety_flags() -> None:
    report = evaluate_ai_shadow_financial_thresholds_frame(
        frame=pd.DataFrame(outcome_rows()),
        report_path=None,
        strict=True,
        safety_overrides={"live_trading_enabled": True, "sends_orders": True},
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "unsafe_safety_flags"
    assert sorted(report["blocking_errors"]) == ["live_trading_enabled", "sends_orders"]


def test_financial_evaluation_calculates_expectancy() -> None:
    report = evaluate_ai_shadow_financial_thresholds_frame(
        frame=pd.DataFrame(outcome_rows()),
        report_path=None,
        min_samples=1,
    )

    expected = (0.05 + 0.03 - 0.01 - 0.04 + 0.02 - 0.03) / 6
    assert report["status"] == "ok"
    assert report["global_metrics"]["expectancy"] == expected


def test_financial_evaluation_calculates_profit_factor_safely() -> None:
    metrics = compute_financial_metrics([0.01, 0.02, 0.03])

    assert metrics["gross_loss"] == 0.0
    assert metrics["profit_factor"] is None
    assert metrics["profit_factor_note"] == "gross_loss_zero"


def test_financial_evaluation_calculates_drawdown_approx() -> None:
    metrics = compute_financial_metrics([0.10, -0.05, -0.10, 0.02])

    assert metrics["max_drawdown_approx"] == 0.15000000000000002


def test_financial_evaluation_compares_ai_accept_vs_reject() -> None:
    report = evaluate_ai_shadow_financial_thresholds_frame(
        frame=pd.DataFrame(outcome_rows()),
        report_path=None,
        min_samples=1,
    )

    accept = report["group_results"]["AI_ACCEPT"]
    reject = report["group_results"]["AI_REJECT"]
    assert accept["rows"] == 3
    assert reject["rows"] == 1
    assert accept["expectancy"] > reject["expectancy"]


def test_financial_evaluation_selects_recommended_threshold() -> None:
    report = evaluate_ai_shadow_financial_thresholds_frame(
        frame=pd.DataFrame(outcome_rows()),
        report_path=None,
        thresholds=[0.50, 0.60, 0.75],
        min_samples=1,
    )

    assert report["recommended_threshold"] == 0.75
    assert report["best_threshold"]["expectancy"] == 0.04
    assert report["promotion_allowed"] is False


def test_financial_evaluation_marks_small_sample_warning() -> None:
    report = evaluate_ai_shadow_financial_thresholds_frame(
        frame=pd.DataFrame(outcome_rows()),
        report_path=None,
        min_samples=30,
    )

    assert report["status"] == "insufficient_data"
    assert report["sample_warning"] is True
    assert report["recommendation_confidence"] == "low"


def test_financial_evaluation_never_allows_auto_promotion() -> None:
    report = evaluate_ai_shadow_financial_thresholds_frame(
        frame=pd.DataFrame(outcome_rows()),
        report_path=None,
        min_samples=1,
    )

    assert report["promotion_allowed"] is False
    assert report["auto_promote"] is False
    assert report["registry_updated"] is False
    assert report["model_promoted"] is False


def test_cli_evaluate_financial_thresholds_runs_successfully(tmp_path: Path) -> None:
    source = tmp_path / "outcomes.jsonl"
    report_path = tmp_path / "report.json"
    write_jsonl(source, outcome_rows())

    result = subprocess.run(
        [
            sys.executable,
            "scripts/evaluate_ai_shadow_financial_thresholds.py",
            "--input",
            str(source),
            "--report",
            str(report_path),
            "--thresholds",
            "0.5,0.6,0.75",
            "--min-samples",
            "1",
            "--strict",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["recommended_threshold"] == 0.75
    assert report_path.exists()


def test_does_not_touch_training_dataset_or_trades_master(tmp_path: Path) -> None:
    training_dataset = tmp_path / "training_dataset.parquet"
    trades_master = tmp_path / "trades_master.xlsx"
    source = tmp_path / "outcomes.jsonl"
    training_dataset.write_bytes(b"training")
    trades_master.write_bytes(b"master")
    write_jsonl(source, outcome_rows())

    report = evaluate_ai_shadow_financial_thresholds(
        input_path=source,
        report_path=tmp_path / "report.json",
        min_samples=1,
        strict=True,
    )

    assert report["status"] == "ok"
    assert training_dataset.read_bytes() == b"training"
    assert trades_master.read_bytes() == b"master"


def test_does_not_touch_registry_or_signal_producer(tmp_path: Path) -> None:
    registry = tmp_path / "model_registry.json"
    signal_producer = tmp_path / "active_freqtrade_signals.json"
    source = tmp_path / "outcomes.jsonl"
    registry.write_text('{"champion_model_id":"keep"}', encoding="utf-8")
    signal_producer.write_text('{"signals":[]}', encoding="utf-8")
    registry_before = registry.read_text(encoding="utf-8")
    signal_before = signal_producer.read_text(encoding="utf-8")
    write_jsonl(source, outcome_rows())

    report = evaluate_ai_shadow_financial_thresholds(
        input_path=source,
        report_path=tmp_path / "report.json",
        min_samples=1,
        strict=True,
    )

    assert report["registry_updated"] is False
    assert report["signal_producer_updated"] is False
    assert registry.read_text(encoding="utf-8") == registry_before
    assert signal_producer.read_text(encoding="utf-8") == signal_before
