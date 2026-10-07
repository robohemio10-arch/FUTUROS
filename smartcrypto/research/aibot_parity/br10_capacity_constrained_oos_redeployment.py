"""Chronological, capacity-constrained BR10 OOS research replay."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from pydantic import ValidationError

from smartcrypto.research.aibot_parity.market_intelligence_pnl_ablation import (
    AblationError,
    ModelFactory,
    SAFETY_FLAGS,
    TARGET_COLUMN,
    _calibrate_threshold,
    _default_model_factory,
    _fit_predict,
    _load_bundle,
    _metrics,
)
from smartcrypto.research.portfolio_intelligence.capacity import (
    build_virtual_state,
    capacity_reasons,
)
from smartcrypto.research.portfolio_intelligence.contracts import PortfolioAllocatorConfig

SCHEMA_VERSION = "br10_capacity_constrained_oos_redeployment_v1"
DEFAULT_CONFIG = "config/research/portfolio_allocator.yaml"


class ReplayError(RuntimeError):
    """Fail-closed replay contract error."""


@dataclass(frozen=True)
class Candidate:
    trade_sequence: int
    symbol: str
    decision_time: pd.Timestamp
    br10_selected: bool
    capital_required_usdt: float


@dataclass(frozen=True)
class ObservedOutcome:
    close_time: pd.Timestamp
    net_pnl_usdt: float


@dataclass(frozen=True)
class OpenPosition:
    trade_sequence: int
    symbol: str
    capital_usdt: float
    close_time: pd.Timestamp


@dataclass
class ArmState:
    open_positions: list[OpenPosition] = field(default_factory=list)
    accepted: list[int] = field(default_factory=list)
    capacity_rejected: list[int] = field(default_factory=list)
    br10_rejected: list[int] = field(default_factory=list)
    redeployment_proofs: list[dict[str, Any]] = field(default_factory=list)

    def release_closed(self, decision_time: pd.Timestamp) -> None:
        self.open_positions = [
            position
            for position in self.open_positions
            if position.close_time > decision_time
        ]

    def consider(
        self,
        candidate: Candidate,
        config: PortfolioAllocatorConfig,
    ) -> tuple[bool, tuple[str, ...]]:
        state = build_virtual_state(
            [(position.symbol, position.capital_usdt) for position in self.open_positions]
        )
        reasons = capacity_reasons(
            state=state,
            candidate_symbol=candidate.symbol,
            candidate_capital_usdt=candidate.capital_required_usdt,
            config=config,
        )
        if reasons:
            self.capacity_rejected.append(candidate.trade_sequence)
            return False, reasons
        self.accepted.append(candidate.trade_sequence)
        return True, ()

    def reserve_until_close(self, candidate: Candidate, close_time: pd.Timestamp) -> None:
        self.open_positions.append(
            OpenPosition(
                trade_sequence=candidate.trade_sequence,
                symbol=candidate.symbol,
                capital_usdt=candidate.capital_required_usdt,
                close_time=close_time,
            )
        )


def _oos_candidates(
    dataset: pd.DataFrame,
    features: tuple[str, ...],
    splits: list[dict[str, Any]],
    model_factory: ModelFactory,
    capital_per_candidate_usdt: float,
) -> tuple[list[Candidate], dict[int, ObservedOutcome]]:
    candidates: list[Candidate] = []
    outcomes: dict[int, ObservedOutcome] = {}
    for split in splits:
        train = dataset.iloc[split["_train_indices"]]
        validation = dataset.iloc[split["_validation_indices"]]
        test = dataset.iloc[split["_test_indices"]]
        if train["close_time_utc"].max() > validation["open_time_utc"].min():
            raise ReplayError("training_outcome_after_validation_start")
        if validation["close_time_utc"].max() > test["open_time_utc"].min():
            raise ReplayError("validation_outcome_after_test_start")
        validation_scores, test_scores = _fit_predict(
            train=train,
            validation=validation,
            test=test,
            features=features,
            model_factory=model_factory,
        )
        threshold = float(
            _calibrate_threshold(
                validation_scores,
                validation[TARGET_COLUMN].to_numpy(dtype=float),
            )["threshold"]
        )
        for row, score in zip(test.itertuples(index=False), test_scores, strict=True):
            trade_sequence = int(row.trade_sequence)
            decision_time = pd.Timestamp(row.open_time_utc)
            close_time = pd.Timestamp(row.close_time_utc)
            if trade_sequence in outcomes:
                raise ReplayError("duplicate_oos_trade_sequence")
            if pd.Timestamp(row.feature_cutoff_utc) > decision_time:
                raise ReplayError("feature_available_after_decision")
            if close_time < decision_time:
                raise ReplayError("outcome_close_before_decision")
            candidates.append(
                Candidate(
                    trade_sequence=trade_sequence,
                    symbol=str(row.symbol),
                    decision_time=decision_time,
                    br10_selected=bool(score >= threshold),
                    capital_required_usdt=capital_per_candidate_usdt,
                )
            )
            outcomes[trade_sequence] = ObservedOutcome(
                close_time=close_time,
                net_pnl_usdt=float(getattr(row, TARGET_COLUMN)),
            )
    if not candidates:
        raise ReplayError("oos_candidates_empty")
    candidates.sort(key=lambda candidate: (candidate.decision_time, candidate.trade_sequence))
    return candidates, outcomes


def _replay(
    candidates: list[Candidate],
    outcomes: dict[int, ObservedOutcome],
    config: PortfolioAllocatorConfig,
) -> tuple[ArmState, ArmState, list[int]]:
    control = ArmState()
    treatment = ArmState()
    reused: list[int] = []
    for candidate in candidates:
        control.release_closed(candidate.decision_time)
        treatment.release_closed(candidate.decision_time)
        control_occupying = tuple(control.open_positions)
        treatment_before = build_virtual_state(
            [(position.symbol, position.capital_usdt) for position in treatment.open_positions]
        )
        control_accepted, control_reasons = control.consider(candidate, config)
        treatment_accepted = False
        if not candidate.br10_selected:
            treatment.br10_rejected.append(candidate.trade_sequence)
        else:
            treatment_accepted, _ = treatment.consider(candidate, config)
        if treatment_accepted and not control_accepted:
            if not control_reasons or not control_occupying:
                raise ReplayError("redeployment_without_control_occupancy")
            reused.append(candidate.trade_sequence)
            treatment.redeployment_proofs.append(
                {
                    "candidate_identifier": f"trade_sequence:{candidate.trade_sequence}",
                    "decision_time_utc": candidate.decision_time.isoformat(),
                    "control_rejection_reasons": list(control_reasons),
                    "control_occupying_positions": [
                        {
                            "candidate_identifier": f"trade_sequence:{position.trade_sequence}",
                            "symbol": position.symbol,
                            "reserved_capital_usdt": position.capital_usdt,
                        }
                        for position in control_occupying
                    ],
                    "treatment_capacity_available_usdt": (
                        config.shadow_capital_budget_usdt
                        - treatment_before.total_capital_usdt
                    ),
                    "treatment_open_position_count_before": treatment_before.position_count,
                    "treatment_capacity_check_reasons": [],
                    "post_decision_outcome_used_for_selection": False,
                }
            )
        if control_accepted:
            control.reserve_until_close(candidate, outcomes[candidate.trade_sequence].close_time)
        if treatment_accepted:
            treatment.reserve_until_close(candidate, outcomes[candidate.trade_sequence].close_time)
    return control, treatment, reused


def _arm_metrics(
    arm: ArmState,
    candidates: list[Candidate],
    outcomes: dict[int, ObservedOutcome],
    *,
    budget_usdt: float,
    window_hours: float,
) -> dict[str, Any]:
    by_id = {candidate.trade_sequence: candidate for candidate in candidates}
    accepted = sorted(arm.accepted, key=lambda key: (outcomes[key].close_time, key))
    metrics = _metrics([outcomes[key].net_pnl_usdt for key in accepted])
    used = sum(
        by_id[key].capital_required_usdt
        * (outcomes[key].close_time - by_id[key].decision_time).total_seconds()
        / 3600.0
        for key in accepted
    )
    available = budget_usdt * window_hours
    idle = available - used
    if idle < -1e-6:
        raise ReplayError("capacity_accounting_exceeds_budget")
    return {
        "net_pnl_usdt": metrics["net_pnl"],
        "trade_count": metrics["trade_count"],
        "capital_hours_used": used,
        "net_pnl_per_capital_hour": metrics["net_pnl"] / used if used > 0 else None,
        "capital_utilization": used / available,
        "idle_capital_hours": max(0.0, idle),
        "capacity_rejected_trade_count": len(arm.capacity_rejected),
        "capacity_rejected_observed_pnl_usdt": sum(
            outcomes[key].net_pnl_usdt for key in arm.capacity_rejected
        ),
        "br10_rejected_trade_count": len(arm.br10_rejected),
        "max_drawdown": metrics["max_drawdown"],
        "profit_factor": metrics["profit_factor"],
        "expectancy": metrics["expectancy"],
    }


def _summarize_capacity_scenario(
    candidates: list[Candidate],
    outcomes: dict[int, ObservedOutcome],
    config: PortfolioAllocatorConfig,
    capital_per_candidate_usdt: float,
) -> dict[str, Any]:
    control, treatment, reused = _replay(candidates, outcomes, config)
    window_end = max(outcome.close_time for outcome in outcomes.values())
    window_hours = (
        window_end - candidates[0].decision_time
    ).total_seconds() / 3600.0
    if window_hours <= 0:
        raise ReplayError("oos_observation_window_invalid")
    budget = config.shadow_capital_budget_usdt
    control_metrics = _arm_metrics(
        control, candidates, outcomes, budget_usdt=budget, window_hours=window_hours
    )
    treatment_metrics = _arm_metrics(
        treatment, candidates, outcomes, budget_usdt=budget, window_hours=window_hours
    )
    by_id = {candidate.trade_sequence: candidate for candidate in candidates}
    return {
        "anti_leakage_status": "PASS",
        "oos_candidate_count": len(candidates),
        "budget_usdt": budget,
        "capital_per_candidate_usdt": capital_per_candidate_usdt,
        "capital_source": "explicit_fixed_research_reservation",
        "historical_capital_proxy_used_for_selection": False,
        "capacity": {
            "max_positions": config.max_positions,
            "max_positions_per_symbol": config.max_positions_per_symbol,
            "max_symbol_concentration_fraction": config.max_symbol_concentration_fraction,
        },
        "window_start_utc": candidates[0].decision_time.isoformat(),
        "window_end_utc": window_end.isoformat(),
        "control": control_metrics,
        "treatment": treatment_metrics,
        "treatment_minus_control_net_pnl": (
            treatment_metrics["net_pnl_usdt"] - control_metrics["net_pnl_usdt"]
        ),
        "treatment_minus_control_net_pnl_per_capital_hour": (
            None
            if control_metrics["net_pnl_per_capital_hour"] is None
            or treatment_metrics["net_pnl_per_capital_hour"] is None
            else treatment_metrics["net_pnl_per_capital_hour"]
            - control_metrics["net_pnl_per_capital_hour"]
        ),
        "released_capacity_reused_count": len(reused),
        "released_capacity_reused_trade_sequences": reused,
        "released_capacity_reused_proofs": treatment.redeployment_proofs,
        "released_capacity_reused_capital_hours": sum(
            by_id[key].capital_required_usdt
            * (outcomes[key].close_time - by_id[key].decision_time).total_seconds()
            / 3600.0
            for key in reused
        ),
        "incremental_pnl_from_redeployment_usdt": sum(
            outcomes[key].net_pnl_usdt for key in reused
        ),
    }


def replay_capacity_constrained_oos(
    *,
    dataset: pd.DataFrame,
    features: tuple[str, ...],
    splits: list[dict[str, Any]],
    config: PortfolioAllocatorConfig,
    capital_per_candidate_usdt: float,
    model_factory: ModelFactory,
) -> dict[str, Any]:
    if not math.isfinite(capital_per_candidate_usdt) or capital_per_candidate_usdt <= 0:
        raise ReplayError("ex_ante_capital_reservation_invalid")
    candidates, outcomes = _oos_candidates(
        dataset, features, splits, model_factory, capital_per_candidate_usdt
    )
    return _summarize_capacity_scenario(
        candidates, outcomes, config, capital_per_candidate_usdt
    )


def replay_capacity_scenarios_oos(
    *,
    dataset: pd.DataFrame,
    features: tuple[str, ...],
    splits: list[dict[str, Any]],
    config: PortfolioAllocatorConfig,
    capital_per_candidate_usdt: float,
    model_factory: ModelFactory,
) -> dict[str, dict[str, Any]]:
    if (
        config.shadow_capital_budget_usdt != 1000.0
        or config.max_positions != 2
        or capital_per_candidate_usdt != 500.0
    ):
        raise ReplayError("capacity_scenario_contract_drift")
    candidates, outcomes = _oos_candidates(
        dataset, features, splits, model_factory, capital_per_candidate_usdt
    )
    stress_config = PortfolioAllocatorConfig.model_validate(
        {**config.model_dump(mode="python"), "max_positions": 1}
    )
    return {
        "BASELINE_CAPACITY_2": _summarize_capacity_scenario(
            candidates, outcomes, config, capital_per_candidate_usdt
        ),
        "CAPACITY_1_GLOBAL": _summarize_capacity_scenario(
            candidates, outcomes, stress_config, capital_per_candidate_usdt
        ),
    }


def build_br10_capacity_constrained_oos_redeployment_v1(
    *,
    project_root: str | Path,
    capital_per_candidate_usdt: float | None = None,
    config_path: str | Path = DEFAULT_CONFIG,
    dataset_path: str | Path | None = None,
    feature_contract_path: str | Path | None = None,
    dataset_manifest_path: str | Path | None = None,
    split_manifest_path: str | Path | None = None,
    model_factory: ModelFactory | None = None,
) -> dict[str, Any]:
    """Build no-write OOS replay; require an explicit ex-ante capital scenario."""

    try:
        if capital_per_candidate_usdt is None:
            raise ReplayError("ex_ante_capital_reservation_required")
        root = Path(project_root).resolve()
        config_file = Path(config_path)
        if not config_file.is_absolute():
            config_file = root / config_file
        config_payload = yaml.safe_load(config_file.read_text(encoding="utf-8-sig"))
        config = PortfolioAllocatorConfig.model_validate(config_payload)
        dataset, features, splits, source = _load_bundle(
            project_root=root,
            dataset_path=dataset_path,
            feature_contract_path=feature_contract_path,
            dataset_manifest_path=dataset_manifest_path,
            split_manifest_path=split_manifest_path,
        )
        scenarios = replay_capacity_scenarios_oos(
            dataset=dataset,
            features=features,
            splits=splits,
            config=config,
            capital_per_candidate_usdt=capital_per_candidate_usdt,
            model_factory=model_factory or _default_model_factory,
        )
        baseline = scenarios["BASELINE_CAPACITY_2"]
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok",
            "reason": "capacity_constrained_oos_replay_complete",
            "decision": "RESEARCH_ONLY",
            "source": {**source, "config_path": str(config_file)},
            **baseline,
            "scenarios": scenarios,
            "write_performed": False,
            **SAFETY_FLAGS,
            "safety_flags": dict(SAFETY_FLAGS),
        }
    except (ReplayError, AblationError, OSError, ValueError, TypeError, ValidationError, yaml.YAMLError) as exc:
        reason = str(exc) if isinstance(exc, (ReplayError, AblationError)) else (
            f"capacity_replay_failed:{type(exc).__name__}"
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "reason": reason,
            "decision": "REPLAY_BLOCKED",
            "anti_leakage_status": "BLOCKED",
            "write_performed": False,
            **SAFETY_FLAGS,
            "safety_flags": dict(SAFETY_FLAGS),
        }
