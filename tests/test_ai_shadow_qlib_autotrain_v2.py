from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from smartcrypto.learning.ai_shadow_qlib_autotrain_v2.calibration import (
    build_calibration_suite,
    calibration_report,
    rank_percentile_probabilities,
)
from smartcrypto.learning.ai_shadow_qlib_autotrain_v2.contracts import (
    CadenceContract,
    PipelineConfig,
    StrategyPolicy,
)
from smartcrypto.learning.ai_shadow_qlib_autotrain_v2.counterfactual import (
    build_counterfactual_harness,
    normalize_rows,
)
from smartcrypto.learning.ai_shadow_qlib_autotrain_v2.drift import build_drift_overlay
from smartcrypto.learning.ai_shadow_qlib_autotrain_v2.governance import (
    build_cadence_governance,
    evaluate_training_eligibility,
)
from smartcrypto.learning.ai_shadow_qlib_autotrain_v2.pipeline import (
    build_ai_shadow_qlib_autotrain_v2,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "ai_shadow_qlib_autotrain_v2.json"


def sample_rows(count: int = 12) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(count):
        label = 1 if index % 2 == 0 else 0
        entry = 100.0 + index
        pnl = 2.0 if label else -1.0
        rows.append(
            {
                "event_id": f"event-{index}",
                "candle_time_utc": f"2026-01-01T00:{index:02d}:00+00:00",
                "symbol": "BTCUSDT",
                "side": "long" if index % 2 == 0 else "short",
                "expected_entry": entry,
                "expected_exit": entry + pnl,
                "net_pnl": pnl,
                "label": label,
                "qlib_score": index / max(1, count - 1),
                "ai_shadow_probability": 0.8 if label else 0.2,
            }
        )
    return rows


def training_state(**overrides: object) -> dict[str, object]:
    state: dict[str, object] = {
        "new_unique_trade_count": 10,
        "total_unique_sample_count": 150,
        "previous_watermark": "2026-01-01T00:00:00+00:00",
        "current_watermark": "2026-01-02T00:00:00+00:00",
        "previous_dataset_hash": hashlib.sha256(b"previous").hexdigest(),
        "current_dataset_hash": hashlib.sha256(b"current").hexdigest(),
        "prior_microbatch_hashes": [],
    }
    state.update(overrides)
    return state


def test_config_loads_and_has_three_unique_policies() -> None:
    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
    config = PipelineConfig.from_mapping(payload)
    assert len(config.policies) == 3
    assert len({policy.policy_id for policy in config.policies}) == 3


def test_config_rejects_duplicate_policy_ids() -> None:
    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
    payload["strategy_policies"][1]["policy_id"] = payload["strategy_policies"][0][
        "policy_id"
    ]
    with pytest.raises(ValueError, match="unique"):
        PipelineConfig.from_mapping(payload)


def test_rank_percentiles_are_deterministic_with_ties() -> None:
    assert rank_percentile_probabilities([5.0, 1.0, 5.0]) == [0.75, 0.0, 0.75]


def test_calibration_report_computes_brier_ece_and_buckets() -> None:
    report = calibration_report(
        [0.9, 0.8, 0.2, 0.1],
        [1, 1, 0, 0],
        [2.0, 1.0, -1.0, -2.0],
        bin_count=2,
        min_bucket_rows=1,
        score_semantics="test",
    )
    assert report["brier_score"] == pytest.approx(0.025)
    assert report["expected_calibration_error"] == pytest.approx(0.15)
    assert len(report["reliability_curve"]) == 2
    assert len(report["precision_by_bucket"]) == 2
    assert len(report["expected_value_by_bucket"]) == 2


def test_calibration_suite_contains_all_models() -> None:
    suite = build_calibration_suite(sample_rows(), bin_count=4, min_bucket_rows=1)
    assert suite["row_count"] == 12
    assert set(suite) >= {"qlib_ranker", "ai_shadow_veto", "ensemble"}


def test_normalize_rows_blocks_duplicate_event_id() -> None:
    rows = sample_rows(2)
    rows[1]["event_id"] = rows[0]["event_id"]
    normalized, blockers = normalize_rows(rows)
    assert len(normalized) == 1
    assert any("duplicate_event_id" in blocker for blocker in blockers)


def test_normalize_rows_requires_timezone() -> None:
    rows = sample_rows(1)
    rows[0]["candle_time_utc"] = "2026-01-01T00:00:00"
    _normalized, blockers = normalize_rows(rows)
    assert any("timezone-aware" in blocker for blocker in blockers)


def test_counterfactual_harness_emits_required_fields_for_each_policy() -> None:
    policies = (
        StrategyPolicy("p1", "qlib_rank_probability", 0.4),
        StrategyPolicy("p2", "ensemble_probability", 0.5, 0.3),
    )
    report = build_counterfactual_harness(sample_rows(4), policies)
    assert report["status"] == "ok"
    assert report["decision_count"] == 8
    assert report["required_log_fields_present"] is True
    assert all(decision["sends_orders"] is False for decision in report["decisions"])


def test_counterfactual_harness_blocks_unknown_score_source() -> None:
    policies = (StrategyPolicy("bad", "unknown", 0.5),)
    report = build_counterfactual_harness(sample_rows(2), policies)
    assert report["status"] == "blocked"
    assert report["decision_count"] == 0


def test_training_gate_blocks_no_new_unique_trades() -> None:
    report = evaluate_training_eligibility(
        training_state(new_unique_trade_count=0),
        min_training_sample_rows=100,
    )
    assert report["research_training_eligible"] is False
    assert "new_unique_trade_count_not_positive" in report["blockers"]


def test_training_gate_blocks_watermark_not_advanced() -> None:
    report = evaluate_training_eligibility(
        training_state(current_watermark="2026-01-01T00:00:00+00:00"),
        min_training_sample_rows=100,
    )
    assert "watermark_not_advanced" in report["blockers"]


def test_training_gate_blocks_unchanged_dataset_hash() -> None:
    previous = hashlib.sha256(b"same").hexdigest()
    report = evaluate_training_eligibility(
        training_state(previous_dataset_hash=previous, current_dataset_hash=previous),
        min_training_sample_rows=100,
    )
    assert "dataset_hash_not_changed" in report["blockers"]


def test_training_gate_blocks_duplicate_microbatch_hash() -> None:
    current = hashlib.sha256(b"current").hexdigest()
    report = evaluate_training_eligibility(
        training_state(current_dataset_hash=current, prior_microbatch_hashes=[current]),
        min_training_sample_rows=100,
    )
    assert "duplicate_microbatch_hash_detected" in report["blockers"]


def test_training_gate_blocks_insufficient_sample() -> None:
    report = evaluate_training_eligibility(
        training_state(total_unique_sample_count=99),
        min_training_sample_rows=100,
    )
    assert "minimum_training_sample_not_met" in report["blockers"]


def test_training_gate_can_mark_research_eligibility_without_training() -> None:
    report = evaluate_training_eligibility(
        training_state(),
        min_training_sample_rows=100,
    )
    assert report["status"] == "ok"
    assert report["research_training_eligible"] is True
    assert report["training_performed"] is False
    assert report["promotion_allowed"] is False


def test_cadence_contract_never_registers_scheduler() -> None:
    cadence = CadenceContract(5, "trade_closed", 60, 1, 7, 30)
    report = build_cadence_governance(cadence)
    assert report["cadence"]["creates_cron"] is False
    assert report["scheduler_registered"] is False


def test_drift_overlay_is_insufficient_without_baseline(tmp_path: Path) -> None:
    suite = build_calibration_suite(sample_rows(), bin_count=4, min_bucket_rows=1)
    report = build_drift_overlay(
        suite,
        baseline_calibration=None,
        upstream_drift_report_path=tmp_path / "missing.json",
        max_brier_degradation=0.01,
        max_ece_degradation=0.01,
        max_expected_value_degradation=0.01,
    )
    assert report["status"] == "insufficient_data"
    assert "baseline_calibration_missing" in report["warnings"]


def test_drift_overlay_blocks_degraded_calibration(tmp_path: Path) -> None:
    current = build_calibration_suite(sample_rows(), bin_count=4, min_bucket_rows=1)
    baseline = json.loads(json.dumps(current))
    baseline["ensemble"]["brier_score"] = 0.0
    baseline["ensemble"]["expected_calibration_error"] = 0.0
    baseline["ensemble"]["overall_expected_value"] = 10.0
    report = build_drift_overlay(
        current,
        baseline_calibration=baseline,
        upstream_drift_report_path=tmp_path / "missing.json",
        max_brier_degradation=0.0001,
        max_ece_degradation=0.0001,
        max_expected_value_degradation=0.0001,
    )
    assert report["status"] == "blocked"
    assert any("ensemble" in blocker for blocker in report["blockers"])


def test_pipeline_fixture_is_non_authoritative_and_fail_closed() -> None:
    report = build_ai_shadow_qlib_autotrain_v2(
        project_root=ROOT,
        config_path=CONFIG,
        generated_at_utc="2026-01-01T00:00:00+00:00",
    )
    assert report["fixture_mode"] is True
    assert report["authoritative_result"] is False
    assert report["status"] == "blocked"
    assert report["training_governance"]["research_training_eligible"] is False
    assert report["sends_orders"] is False
    assert report["automatic_promotion"] is False
    assert report["write_performed"] is False


def test_pipeline_result_hash_is_stable_across_timestamps() -> None:
    first = build_ai_shadow_qlib_autotrain_v2(
        project_root=ROOT,
        config_path=CONFIG,
        generated_at_utc="2026-01-01T00:00:00+00:00",
    )
    second = build_ai_shadow_qlib_autotrain_v2(
        project_root=ROOT,
        config_path=CONFIG,
        generated_at_utc="2026-01-02T00:00:00+00:00",
    )
    assert first["result_hash"] == second["result_hash"]


def test_pipeline_explicit_input_is_authoritative_research_only(tmp_path: Path) -> None:
    payload = {"rows": sample_rows(120), "training_state": training_state()}
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    report = build_ai_shadow_qlib_autotrain_v2(
        project_root=ROOT,
        input_path=input_path,
        config_path=CONFIG,
    )
    assert report["authoritative_result"] is True
    assert report["research_only"] is True
    assert report["operational_authority"] is False
    assert report["training_governance"]["research_training_eligible"] is True
    assert report["training_governance"]["training_performed"] is False


def test_cli_no_write_returns_json_and_no_orders() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_ai_shadow_qlib_autotrain_v2.py"),
            "--project-root",
            str(ROOT),
            "--config",
            str(CONFIG),
            "--no-write",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    assert payload["write_performed"] is False
    assert payload["sends_orders"] is False
    assert payload["runtime_activation"] is False
