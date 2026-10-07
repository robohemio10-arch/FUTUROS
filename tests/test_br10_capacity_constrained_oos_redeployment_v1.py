from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from smartcrypto.research.aibot_parity import (
    br10_capacity_constrained_oos_redeployment as replay,
)
from smartcrypto.research.portfolio_intelligence.contracts import PortfolioAllocatorConfig


def _config() -> PortfolioAllocatorConfig:
    return PortfolioAllocatorConfig(
        max_positions=1,
        max_positions_per_symbol=1,
        shadow_capital_budget_usdt=500.0,
        max_symbol_concentration_fraction=1.0,
    )


def _candidate(
    number: int, minute: int, *, selected: bool, symbol: str = "BTCUSDT"
) -> replay.Candidate:
    return replay.Candidate(
        trade_sequence=number,
        symbol=symbol,
        decision_time=pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=minute),
        br10_selected=selected,
        capital_required_usdt=500.0,
    )


def _outcome(minute: int, pnl: float) -> replay.ObservedOutcome:
    return replay.ObservedOutcome(
        close_time=pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=minute),
        net_pnl_usdt=pnl,
    )


class _Model:
    def fit(self, x: np.ndarray, y: np.ndarray) -> _Model:
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        return x[:, 0]


def _model_factory() -> _Model:
    return _Model()


def test_released_capacity_reused_requires_same_candidate_control_capacity_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [
        _candidate(1, 0, selected=False),
        _candidate(2, 1, selected=True),
        _candidate(3, 3, selected=True),
    ]
    outcomes = {1: _outcome(3, -10.0), 2: _outcome(4, 7.0), 3: _outcome(5, 5.0)}

    monkeypatch.setattr(replay, "_oos_candidates", lambda *args: (candidates, outcomes))
    result = replay.replay_capacity_constrained_oos(
        dataset=pd.DataFrame(),
        features=(),
        splits=[],
        config=_config(),
        capital_per_candidate_usdt=500.0,
        model_factory=_model_factory,
    )
    control, treatment, reused = replay._replay(candidates, outcomes, _config())

    assert result["anti_leakage_status"] == "PASS"
    assert result["control"]["net_pnl_usdt"] == -5.0
    assert result["treatment"]["net_pnl_usdt"] == 7.0
    assert result["treatment_minus_control_net_pnl"] == 12.0
    assert result["released_capacity_reused_count"] == 1
    assert result["released_capacity_reused_capital_hours"] == 25.0
    assert result["incremental_pnl_from_redeployment_usdt"] == 7.0
    proof = result["released_capacity_reused_proofs"][0]
    assert proof["candidate_identifier"] == "trade_sequence:2"
    assert proof["decision_time_utc"] == "2026-01-01T00:01:00+00:00"
    assert "global_position_capacity_full" in proof["control_rejection_reasons"]
    assert proof["control_occupying_positions"][0]["candidate_identifier"] == "trade_sequence:1"
    assert proof["treatment_capacity_available_usdt"] == 500.0
    assert proof["treatment_capacity_check_reasons"] == []
    assert proof["post_decision_outcome_used_for_selection"] is False
    assert "net_pnl_usdt" not in proof
    assert "close_time" not in str(proof)
    assert control.accepted == [1, 3]
    assert control.capacity_rejected == [2]
    assert treatment.accepted == [2]
    assert treatment.br10_rejected == [1]
    assert treatment.capacity_rejected == [3]
    assert reused == [2]
    assert replay._arm_metrics(
        treatment, candidates, outcomes, budget_usdt=500.0, window_hours=5 / 60
    )["capacity_rejected_observed_pnl_usdt"] == 5.0


def test_close_at_decision_time_releases_capacity_before_candidate() -> None:
    candidates = [_candidate(1, 0, selected=True), _candidate(2, 3, selected=True)]
    outcomes = {1: _outcome(3, -1.0), 2: _outcome(4, 2.0)}

    control, treatment, reused = replay._replay(candidates, outcomes, _config())

    assert control.accepted == [1, 2]
    assert treatment.accepted == [1, 2]
    assert reused == []


def test_future_pnl_cannot_change_admission_or_redeployment_identity() -> None:
    candidates = [_candidate(1, 0, selected=False), _candidate(2, 1, selected=True)]
    first = {1: _outcome(3, -10.0), 2: _outcome(4, 7.0)}
    changed = {1: _outcome(8, 1000.0), 2: _outcome(9, -2000.0)}

    control_a, treatment_a, reused_a = replay._replay(candidates, first, _config())
    control_b, treatment_b, reused_b = replay._replay(candidates, changed, _config())

    assert control_a.accepted == control_b.accepted == [1]
    assert treatment_a.accepted == treatment_b.accepted == [2]
    assert reused_a == reused_b == [2]
    assert treatment_a.redeployment_proofs == treatment_b.redeployment_proofs


def test_global_one_slot_stress_reuses_capacity_without_changing_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [
        _candidate(1, 0, selected=False),
        _candidate(2, 1, selected=True, symbol="ETHUSDT"),
        _candidate(3, 3, selected=True),
    ]
    outcomes = {1: _outcome(3, -10.0), 2: _outcome(4, 7.0), 3: _outcome(5, 5.0)}
    calls = 0

    def candidates_once(*args: object) -> tuple[list[replay.Candidate], dict[int, replay.ObservedOutcome]]:
        nonlocal calls
        calls += 1
        return candidates, outcomes

    monkeypatch.setattr(replay, "_oos_candidates", candidates_once)
    baseline_config = PortfolioAllocatorConfig(
        max_positions=2,
        max_positions_per_symbol=1,
        shadow_capital_budget_usdt=1000.0,
        max_symbol_concentration_fraction=0.60,
    )

    scenarios = replay.replay_capacity_scenarios_oos(
        dataset=pd.DataFrame(),
        features=(),
        splits=[],
        config=baseline_config,
        capital_per_candidate_usdt=500.0,
        model_factory=_model_factory,
    )

    assert calls == 1
    baseline = scenarios["BASELINE_CAPACITY_2"]
    stress = scenarios["CAPACITY_1_GLOBAL"]
    assert baseline["control"]["trade_count"] == 3
    assert baseline["treatment"]["trade_count"] == 2
    assert baseline["released_capacity_reused_count"] == 0
    assert stress["budget_usdt"] == baseline["budget_usdt"] == 1000.0
    assert stress["capital_per_candidate_usdt"] == baseline["capital_per_candidate_usdt"] == 500.0
    assert stress["capacity"]["max_positions"] == 1
    assert baseline["capacity"]["max_positions"] == 2
    assert stress["capacity"]["max_positions_per_symbol"] == baseline["capacity"]["max_positions_per_symbol"] == 1
    assert stress["capacity"]["max_symbol_concentration_fraction"] == baseline["capacity"]["max_symbol_concentration_fraction"] == 0.60
    assert stress["control"]["net_pnl_usdt"] == -5.0
    assert stress["treatment"]["net_pnl_usdt"] == 7.0
    assert stress["released_capacity_reused_count"] == 1
    assert stress["released_capacity_reused_capital_hours"] == 25.0
    assert stress["incremental_pnl_from_redeployment_usdt"] == 7.0
    proof = stress["released_capacity_reused_proofs"][0]
    assert proof["candidate_identifier"] == "trade_sequence:2"
    assert proof["control_rejection_reasons"] == ["global_position_capacity_full"]
    assert proof["control_occupying_positions"] == [
        {
            "candidate_identifier": "trade_sequence:1",
            "symbol": "BTCUSDT",
            "reserved_capital_usdt": 500.0,
        }
    ]
    assert proof["treatment_capacity_available_usdt"] == 1000.0
    assert proof["treatment_open_position_count_before"] == 0
    assert proof["post_decision_outcome_used_for_selection"] is False


@pytest.mark.parametrize(
    ("budget", "positions", "capital"),
    [(999.0, 2, 500.0), (1000.0, 1, 500.0), (1000.0, 2, 499.0)],
)
def test_capacity_scenario_contract_drift_blocks(
    budget: float, positions: int, capital: float,
) -> None:
    config = PortfolioAllocatorConfig(
        shadow_capital_budget_usdt=budget,
        max_positions=positions,
    )
    with pytest.raises(replay.ReplayError, match="capacity_scenario_contract_drift"):
        replay.replay_capacity_scenarios_oos(
            dataset=pd.DataFrame(),
            features=(),
            splits=[],
            config=config,
            capital_per_candidate_usdt=capital,
            model_factory=_model_factory,
        )


def test_missing_ex_ante_capital_blocks_before_reading_bundle() -> None:
    report = replay.build_br10_capacity_constrained_oos_redeployment_v1(
        project_root="does-not-exist"
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "ex_ante_capital_reservation_required"
    assert report["anti_leakage_status"] == "BLOCKED"
    assert report["write_performed"] is False


@pytest.mark.parametrize("capital", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_ex_ante_capital_blocks(capital: float) -> None:
    with pytest.raises(replay.ReplayError, match="ex_ante_capital_reservation_invalid"):
        replay.replay_capacity_constrained_oos(
            dataset=pd.DataFrame(),
            features=(),
            splits=[],
            config=_config(),
            capital_per_candidate_usdt=capital,
            model_factory=_model_factory,
        )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("train_close", "training_outcome_after_validation_start"),
        ("validation_close", "validation_outcome_after_test_start"),
        ("test_feature", "feature_available_after_decision"),
    ],
)
def test_score_inputs_must_precede_test_and_feature_cutoff(
    mutation: str, reason: str,
) -> None:
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    frame = pd.DataFrame(
        {
            "trade_sequence": [1, 2, 3],
            "symbol": ["BTCUSDT"] * 3,
            "open_time_utc": [start, start + pd.Timedelta(minutes=1), start + pd.Timedelta(minutes=2)],
            "close_time_utc": [
                start,
                start + pd.Timedelta(minutes=2),
                start + pd.Timedelta(minutes=4),
            ],
            "feature_cutoff_utc": [start] * 3,
            "label_economic_net_pnl": [1.0, 1.0, 1.0],
            "feature_ret_close_1m": [0.0, 0.0, 0.0],
        }
    )
    splits = [{"_train_indices": [0], "_validation_indices": [1], "_test_indices": [2]}]
    if mutation == "train_close":
        frame.loc[0, "close_time_utc"] = start + pd.Timedelta(minutes=2)
    elif mutation == "validation_close":
        frame.loc[1, "close_time_utc"] = start + pd.Timedelta(minutes=3)
    else:
        frame.loc[2, "feature_cutoff_utc"] = start + pd.Timedelta(minutes=3)

    with pytest.raises(replay.ReplayError, match=reason):
        replay._oos_candidates(frame, ("feature_ret_close_1m",), splits, _model_factory, 500.0)


def test_full_builder_uses_past_validation_and_explicit_capital_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    count = 44
    frame = pd.DataFrame(
        {
            "trade_sequence": list(range(1, count + 1)),
            "symbol": ["BTCUSDT"] * count,
            "open_time_utc": [start + pd.Timedelta(hours=index) for index in range(count)],
            "close_time_utc": [
                start + pd.Timedelta(hours=index, minutes=30) for index in range(count)
            ],
            "feature_cutoff_utc": [start + pd.Timedelta(hours=index) for index in range(count)],
            "label_economic_net_pnl": [
                -2.0 if 10 <= index < 20 else 3.0 for index in range(count)
            ],
            "feature_ret_close_1m": [
                float(index - 10) if index < 40 else [-5.0, 5.0, 10.0, -10.0][index - 40]
                for index in range(count)
            ],
        }
    )
    splits = [{
        "_train_indices": list(range(10)),
        "_validation_indices": list(range(10, 40)),
        "_test_indices": list(range(40, 44)),
    }]
    monkeypatch.setattr(
        replay,
        "_load_bundle",
        lambda **kwargs: (frame, ("feature_ret_close_1m",), splits, {"frozen": True}),
    )

    report = replay.build_br10_capacity_constrained_oos_redeployment_v1(
        project_root=Path(__file__).resolve().parents[1],
        capital_per_candidate_usdt=500.0,
        model_factory=_model_factory,
    )

    assert report["status"] == "ok"
    assert report["anti_leakage_status"] == "PASS"
    assert report["control"]["trade_count"] == 4
    assert report["treatment"]["trade_count"] < 4
    assert report["budget_usdt"] == 1000.0
    assert report["capital_source"] == "explicit_fixed_research_reservation"
    assert report["historical_capital_proxy_used_for_selection"] is False
    assert report["write_performed"] is False
    assert set(report["scenarios"]) == {"BASELINE_CAPACITY_2", "CAPACITY_1_GLOBAL"}
    assert report["control"] == report["scenarios"]["BASELINE_CAPACITY_2"]["control"]
    assert report["scenarios"]["CAPACITY_1_GLOBAL"]["capacity"]["max_positions"] == 1
