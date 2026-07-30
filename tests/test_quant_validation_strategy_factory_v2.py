from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from smartcrypto.research.quant_validation_strategy_factory_v2 import (
    SAFETY_FLAGS,
    AcceptanceGates,
    DatasetAuthority,
    RobustnessContract,
    SplitMode,
    TemporalSplitContract,
    ValidationProtocol,
    build_quant_validation_strategy_factory_report,
    build_synthetic_candidate_fixture,
    generate_candidates,
)
from smartcrypto.research.quant_validation_strategy_factory_v2.data_quality import validate_dataset
from smartcrypto.research.quant_validation_strategy_factory_v2.factory import parameter_space_hash
from smartcrypto.research.quant_validation_strategy_factory_v2.robustness import (
    cpcv_probability_of_backtest_overfitting,
    run_monte_carlo_suite,
)
from smartcrypto.research.quant_validation_strategy_factory_v2.splits import build_temporal_splits
from smartcrypto.research.quant_validation_strategy_factory_v2.stability import analyze_parameter_stability

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_quant_validation_strategy_factory_v2.py"


def small_config() -> dict[str, object]:
    return {
        "schema_version": "quant_validation_strategy_factory_config_v2",
        "strategy_version": "test-v2",
        "dataset_authority": "paper_outcome_reconciled",
        "feature_columns": ["feature_momentum"],
        "feature_availability": {},
        "methodology": {
            "normalization_scope": "fold_local",
            "feature_selection_scope": "fold_local",
            "threshold_calibration_scope": "validation_only",
            "regime_construction": "point_in_time",
        },
        "family_spaces": {
            "baseline_no_change": {},
            "fixed_tp_sl": {
                "take_profit_bps": [10, 20, 30],
                "stop_loss_bps": [20],
            },
        },
        "split": {
            "mode": "expanding",
            "fold_count": 3,
            "validation_rows": 10,
            "test_rows": 20,
            "minimum_train_rows": 40,
            "rolling_train_rows": 80,
            "purge_seconds": 0,
            "embargo_seconds": 1,
            "feature_lookback_seconds": 0,
            "label_horizon_seconds": 0,
        },
        "robustness": {
            "monte_carlo_simulations": 100,
            "block_bootstrap_size": 5,
            "cpcv_group_count": 5,
            "cpcv_test_group_count": 2,
            "ruin_threshold_fraction": 0.30,
            "max_risk_of_ruin": 1.0,
            "max_pbo": 1.0,
            "significance_level": 1.0e-8,
            "annualization_factor": 365,
            "seed": 17,
        },
        "gates": {
            "minimum_total_trades": 100,
            "minimum_trades_per_fold": 20,
            "minimum_trades_per_segment": 10,
            "minimum_oos_profit_factor": 1.0,
            "minimum_oos_expectancy": 0.0,
            "minimum_deflated_sharpe_probability": 0.0,
            "maximum_white_reality_check_pvalue": 1.0,
            "minimum_parameter_stability": 0.0,
            "maximum_cost_drag_ratio": 1.0,
            "material_negative_segment_expectancy": -1.0,
        },
    }


def write_config(root: Path, payload: dict[str, object] | None = None) -> Path:
    path = root / "config.json"
    path.write_text(json.dumps(payload or small_config()), encoding="utf-8")
    return path


def protocol_for_tests(mode: SplitMode = SplitMode.EXPANDING) -> ValidationProtocol:
    return ValidationProtocol(
        split=TemporalSplitContract(
            mode=mode,
            fold_count=3,
            validation_rows=10,
            test_rows=20,
            minimum_train_rows=40,
            rolling_train_rows=80,
            embargo_seconds=1,
        ),
        robustness=RobustnessContract(
            monte_carlo_simulations=100,
            block_bootstrap_size=5,
            cpcv_group_count=5,
            cpcv_test_group_count=2,
            max_risk_of_ruin=1.0,
            max_pbo=1.0,
            significance_level=0.05,
            seed=17,
        ),
        gates=AcceptanceGates(
            minimum_total_trades=100,
            minimum_trades_per_fold=20,
            minimum_trades_per_segment=10,
            minimum_deflated_sharpe_probability=0.0,
            maximum_white_reality_check_pvalue=1.0,
            minimum_parameter_stability=0.0,
            maximum_cost_drag_ratio=1.0,
            material_negative_segment_expectancy=-1.0,
        ),
    )


def test_protocol_hash_is_deterministic() -> None:
    first = protocol_for_tests()
    second = protocol_for_tests()
    assert first.protocol_hash == second.protocol_hash
    assert first.validate() == []
    assert first.to_dict()["safety_flags"] == SAFETY_FLAGS


def test_candidate_ids_and_parameter_space_are_deterministic() -> None:
    spaces = small_config()["family_spaces"]
    first = generate_candidates(spaces, strategy_version="test-v2")
    second = generate_candidates(spaces, strategy_version="test-v2")
    assert [item.candidate_id for item in first] == [item.candidate_id for item in second]
    assert len(first) == 4
    assert parameter_space_hash(spaces) == parameter_space_hash(spaces)


@pytest.mark.parametrize("mode", list(SplitMode))
def test_temporal_split_modes_are_deterministic_and_disjoint(mode: SplitMode) -> None:
    protocol = protocol_for_tests(mode)
    candidates = generate_candidates({"baseline_no_change": {}}, strategy_version="test-v2")
    frame = build_synthetic_candidate_fixture(candidates, protocol=protocol)
    first = build_temporal_splits(frame, protocol.split)
    second = build_temporal_splits(frame, protocol.split)
    assert first.evidence.status.value == "PASS"
    assert first.split_hash == second.split_hash
    assert len(first.folds) == 3
    for fold in first.folds:
        assert not set(fold.train_indices) & set(fold.validation_indices)
        assert not set(fold.train_indices) & set(fold.test_indices)
        assert not set(fold.validation_indices) & set(fold.test_indices)


def test_quarantined_input_is_fail_closed() -> None:
    protocol = protocol_for_tests()
    candidate = generate_candidates({"baseline_no_change": {}}, strategy_version="test-v2")[0]
    frame = build_synthetic_candidate_fixture((candidate,), protocol=protocol)
    result = validate_dataset(
        frame,
        dataset_authority=DatasetAuthority.PERMANENT_QUARANTINE,
        feature_columns=("feature_momentum",),
    )
    assert result.evidence.status.value == "BLOCKED"
    assert "input_not_authoritative" in result.evidence.blockers


def test_target_feature_and_future_feature_are_blocked() -> None:
    protocol = protocol_for_tests()
    candidate = generate_candidates({"baseline_no_change": {}}, strategy_version="test-v2")[0]
    frame = build_synthetic_candidate_fixture((candidate,), protocol=protocol)
    frame["target_profit"] = 1.0
    frame["future_return"] = 1.0
    result = validate_dataset(
        frame,
        dataset_authority=DatasetAuthority.PAPER_OUTCOME_RECONCILED,
        feature_columns=("target_profit", "future_return"),
    )
    assert result.leakage_evidence.status.value == "BLOCKED"
    assert any("forbidden_feature" in item for item in result.leakage_evidence.blockers)


def test_cost_reconciliation_mismatch_is_blocked() -> None:
    protocol = protocol_for_tests()
    candidate = generate_candidates({"baseline_no_change": {}}, strategy_version="test-v2")[0]
    frame = build_synthetic_candidate_fixture((candidate,), protocol=protocol)
    frame.loc[0, "net_pnl"] += 1.0
    result = validate_dataset(
        frame,
        dataset_authority=DatasetAuthority.PAPER_OUTCOME_RECONCILED,
        feature_columns=("feature_momentum",),
    )
    assert "cost_reconciliation_mismatch" in result.evidence.blockers


def test_monte_carlo_is_seed_reproducible() -> None:
    contract = protocol_for_tests().robustness
    returns = np.linspace(-0.1, 0.2, 120)
    first = run_monte_carlo_suite(returns, contract=contract, initial_capital=100.0)
    second = run_monte_carlo_suite(returns, contract=contract, initial_capital=100.0)
    assert first == second
    assert first["status"] == "ok"
    assert {item["method"] for item in first["methods"]} == {
        "trade_permutation",
        "iid_bootstrap",
        "block_bootstrap",
        "cost_stress",
    }


def test_cpcv_pbo_is_reproducible() -> None:
    returns = {
        "candidate_a": np.linspace(-0.1, 0.2, 100),
        "candidate_b": np.linspace(-0.2, 0.1, 100),
        "candidate_c": np.sin(np.arange(100)) / 10.0,
    }
    first = cpcv_probability_of_backtest_overfitting(
        returns, group_count=5, test_group_count=2
    )
    second = cpcv_probability_of_backtest_overfitting(
        returns, group_count=5, test_group_count=2
    )
    assert first == second
    assert first["status"] == "ok"
    assert first["path_count"] == 10
    assert first["valid_path_count"] == 10


def test_parameter_surface_rejects_isolated_spike() -> None:
    candidates = generate_candidates(
        {"entry_threshold": {"entry_threshold": [0.4, 0.5, 0.6]}},
        strategy_version="test-v2",
    )
    scores = {
        candidates[0].candidate_id: 1.0,
        candidates[1].candidate_id: 10.0,
        candidates[2].candidate_id: 1.0,
    }
    folds = {
        candidate.candidate_id: [scores[candidate.candidate_id]] * 3
        for candidate in candidates
    }
    result = analyze_parameter_stability(candidates, candidate_scores=scores, fold_scores=folds)
    middle = result["candidate_stability"][candidates[1].candidate_id]
    assert middle["isolated_spike"] is True
    assert middle["knife_edge_optimum"] is True


def test_full_pipeline_fixture_is_no_write_and_has_uniform_scorecards(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    first = build_quant_validation_strategy_factory_report(
        project_root=tmp_path,
        config_path=config,
        generated_at_utc="2026-07-30T00:00:00+00:00",
    )
    second = build_quant_validation_strategy_factory_report(
        project_root=tmp_path,
        config_path=config,
        generated_at_utc="2026-07-30T00:00:00+00:00",
    )
    assert first["status"] == "ok"
    assert first["write_performed"] is False
    assert first["fixture_only"] is True
    assert first["authoritative_result"] is False
    assert first["promotion_allowed"] is False
    assert first["operational_authority"] is False
    assert first["candidate_count"] == 4
    assert first["result_hash"] == second["result_hash"]
    required = {
        "candidate_id",
        "candidate_family",
        "protocol_hash",
        "dataset_hash",
        "split_hash",
        "oos_net_pnl",
        "oos_expectancy",
        "oos_profit_factor",
        "deflated_sharpe",
        "white_reality_check",
        "pbo",
        "risk_of_ruin",
        "parameter_stability",
        "final_decision",
        "authoritative_result",
        "safety_flags",
    }
    for scorecard in first["candidate_scorecards"]:
        assert required <= set(scorecard)
        assert scorecard["promotion_allowed"] is False
        assert scorecard["operational_authority"] is False
    assert not (tmp_path / "data").exists()


def test_material_negative_segment_blocks_candidate(tmp_path: Path) -> None:
    config_payload = small_config()
    config_payload["family_spaces"] = {"baseline_no_change": {}}
    config_payload["dataset_authority"] = "paper_outcome_reconciled"
    config_payload["gates"]["material_negative_segment_expectancy"] = -0.01
    config = write_config(tmp_path, config_payload)
    protocol = protocol_for_tests()
    candidate = generate_candidates({"baseline_no_change": {}}, strategy_version="test-v2")[0]
    frame = build_synthetic_candidate_fixture((candidate,), protocol=protocol)
    mask = frame["symbol"] == "ETHUSDT"
    frame.loc[mask, "net_pnl"] = -1.0
    frame.loc[mask, "gross_pnl"] = frame.loc[mask, "net_pnl"] + frame.loc[mask, "total_cost"]
    input_path = tmp_path / "candidate.csv"
    frame.to_csv(input_path, index=False)
    report = build_quant_validation_strategy_factory_report(
        project_root=tmp_path,
        input_path=input_path,
        config_path=config,
        generated_at_utc="2026-07-30T00:00:00+00:00",
    )
    scorecard = report["candidate_scorecards"][0]
    assert scorecard["material_negative_segments"]


def test_cli_returns_single_json_and_no_write(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(tmp_path),
            "--config",
            str(config),
            "--no-write",
            "--json",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "ok"
    assert payload["write_performed"] is False
    assert payload["sends_orders"] is False
    assert payload["exchange_private_access"] is False
    assert not (tmp_path / "data").exists()


def test_cli_conflicting_write_flags_are_blocked(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(tmp_path),
            "--write-report",
            "--no-write",
            "--json",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["reason"] == "conflicting_write_flags"
    assert payload["write_performed"] is False



def test_manifest_content_hash_excludes_volatile_timestamps(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    first = build_quant_validation_strategy_factory_report(
        project_root=tmp_path,
        config_path=config,
        generated_at_utc="2026-07-30T00:00:00+00:00",
    )
    second = build_quant_validation_strategy_factory_report(
        project_root=tmp_path,
        config_path=config,
        generated_at_utc="2026-07-31T00:00:00+00:00",
    )
    assert first["result_hash"] == second["result_hash"]
    assert (
        first["execution_manifest_content_hash"]
        == second["execution_manifest_content_hash"]
    )


def test_unsafe_global_preprocessing_methodology_is_blocked(tmp_path: Path) -> None:
    payload = small_config()
    payload["methodology"]["normalization_scope"] = "global_before_split"
    config = write_config(tmp_path, payload)
    report = build_quant_validation_strategy_factory_report(
        project_root=tmp_path,
        config_path=config,
        generated_at_utc="2026-07-30T00:00:00+00:00",
    )
    assert report["status"] == "blocked"
    assert "unsafe_methodology:normalization_scope" in report["blockers"]


def test_future_regime_classification_is_blocked() -> None:
    protocol = protocol_for_tests()
    candidate = generate_candidates(
        {"baseline_no_change": {}}, strategy_version="test-v2"
    )[0]
    frame = build_synthetic_candidate_fixture((candidate,), protocol=protocol)
    frame["regime_time_utc"] = pd.to_datetime(frame["open_time_utc"], utc=True) + pd.Timedelta(
        seconds=1
    )
    result = validate_dataset(
        frame,
        dataset_authority=DatasetAuthority.PAPER_OUTCOME_RECONCILED,
        feature_columns=("feature_momentum",),
    )
    assert result.leakage_evidence.status.value == "BLOCKED"
    assert "leakage:future_regime_classification" in result.leakage_evidence.blockers

def test_source_has_no_operational_integrations() -> None:
    package = ROOT / "smartcrypto" / "research" / "quant_validation_strategy_factory_v2"
    text = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    forbidden = (
        "freqtrade_client",
        "private_exchange_client",
        "submit_order(",
        "active_freqtrade_signals",
        "model_promotion_performed = True",
        "updates_risk_manager\": True",
    )
    for token in forbidden:
        assert token not in text
