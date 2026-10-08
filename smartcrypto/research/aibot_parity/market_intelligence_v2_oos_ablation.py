"""PIT flow, basis and funding ablation on the frozen BR10 OOS folds."""

from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import ValidationError

from smartcrypto.research.aibot_parity.market_intelligence_pnl_ablation import (
    AblationError,
    EXPECTED_FEATURES,
    ModelFactory,
    SAFETY_FLAGS,
    TARGET_COLUMN,
    _calibrate_threshold,
    _default_model_factory,
    _economic_metrics,
    _fit_predict,
    _load_bundle,
)
from smartcrypto.research.aibot_parity.market_intelligence_pit_source_foundation import (
    FEATURES as HISTORICAL_FEATURES,
    SCHEMA as PIT_SCHEMA,
    PITObservation,
    align_observations,
)
from smartcrypto.research.market_intelligence.contracts import (
    MarketEvent,
    MarketIntelligenceConfig,
)
from smartcrypto.research.market_intelligence.funding_basis import (
    build_basis_funding_features,
)
from smartcrypto.research.market_intelligence.orderflow import build_orderflow_features

SCHEMA_VERSION = "market_intelligence_v2_flow_basis_funding_oos_ablation_v1"
FEATURES = {
    "FLOW": "mi_flow_imbalance_60s",
    "BASIS": "mi_mark_index_basis_bps",
    "FUNDING": "mi_funding_rate_predicted",
}
VARIANTS = {
    "CONTROL": (),
    "FLOW_ONLY": ("FLOW",),
    "BASIS_ONLY": ("BASIS",),
    "FUNDING_ONLY": ("FUNDING",),
    "FLOW_BASIS": ("FLOW", "BASIS"),
    "FLOW_FUNDING": ("FLOW", "FUNDING"),
    "BASIS_FUNDING": ("BASIS", "FUNDING"),
    "FLOW_BASIS_FUNDING": ("FLOW", "BASIS", "FUNDING"),
}


class MarketIntelligenceV2Error(RuntimeError):
    """No certified point-in-time economic comparison can be produced."""


def _source_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_events(path: Path) -> tuple[list[MarketEvent] | list[PITObservation], str]:
    if path.is_symlink() or not path.is_file():
        raise MarketIntelligenceV2Error("pit_event_source_missing_or_not_regular")
    config = MarketIntelligenceConfig()
    allowed = set(config.allowed_public_sources) - {"offline_fixture"}
    events: list[MarketEvent] = []
    observations: list[PITObservation] = []
    seen: set[str] = set()
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise MarketIntelligenceV2Error("pit_event_source_invalid:record_not_object")
                if payload.get("schema_version") == PIT_SCHEMA:
                    observation = PITObservation.model_validate(payload)
                    if observation.observation_id in seen:
                        raise MarketIntelligenceV2Error(
                            f"pit_event_identity_collision:line={line_number}"
                        )
                    seen.add(observation.observation_id)
                    observations.append(observation)
                    continue
                event = MarketEvent.model_validate(payload)
                if event.source_id not in allowed:
                    raise MarketIntelligenceV2Error(f"pit_source_not_authorized:line={line_number}")
                if event.event_id in seen:
                    raise MarketIntelligenceV2Error(
                        f"pit_event_identity_collision:line={line_number}"
                    )
                seen.add(event.event_id)
                if event.event_type in {"agg_trade", "mark_price"}:
                    events.append(event)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise MarketIntelligenceV2Error(f"pit_event_source_invalid:{type(exc).__name__}") from exc
    if observations and events:
        raise MarketIntelligenceV2Error("mixed_pit_source_semantics")
    if not events and not observations:
        raise MarketIntelligenceV2Error("pit_event_source_empty")
    return observations or events, _source_hash(path)


def _event_index(
    events: Sequence[MarketEvent],
) -> dict[tuple[str, str], tuple[list[datetime], list[MarketEvent]]]:
    grouped: dict[tuple[str, str], list[MarketEvent]] = defaultdict(list)
    exchanges = {event.exchange for event in events}
    if len(exchanges) != 1:
        raise MarketIntelligenceV2Error("pit_exchange_ambiguous")
    for event in events:
        grouped[(event.symbol, event.event_type)].append(event)
    index: dict[tuple[str, str], tuple[list[datetime], list[MarketEvent]]] = {}
    for key, values in grouped.items():
        ordered = sorted(values, key=lambda event: (event.event_time_utc, event.event_id))
        index[key] = ([event.event_time_utc for event in ordered], ordered)
    return index


def _aligned_features(
    *,
    symbol: str,
    decision: datetime,
    index: Mapping[tuple[str, str], tuple[list[datetime], list[MarketEvent]]],
    config: MarketIntelligenceConfig,
) -> tuple[dict[str, float | None], dict[str, str]]:
    flow_times, flow_events = index.get((symbol, "agg_trade"), ([], []))
    flow_end = bisect_right(flow_times, decision)
    flow_start = bisect_right(flow_times, decision - pd.Timedelta(seconds=60))
    flow = [
        event for event in flow_events[flow_start:flow_end] if event.available_at_utc <= decision
    ]
    mark_times, mark_events = index.get((symbol, "mark_price"), ([], []))
    mark_end = bisect_right(mark_times, decision)
    marks: list[MarketEvent] = []
    for position in range(mark_end - 1, -1, -1):
        event = mark_events[position]
        if event.available_at_utc <= decision:
            marks.append(event)
            if len(marks) >= config.funding_extremeness_min_observations:
                break
    marks.reverse()
    values: dict[str, float | None] = {}
    status: dict[str, str] = {}
    flow_max_age = config.freshness_thresholds_seconds["flow"]
    mark_max_age = config.freshness_thresholds_seconds["basis_funding"]
    if not flow:
        status["FLOW"] = "UNAVAILABLE"
        values[FEATURES["FLOW"]] = None
    elif (decision - flow[-1].event_time_utc).total_seconds() > flow_max_age:
        status["FLOW"] = "STALE"
        values[FEATURES["FLOW"]] = None
    else:
        output = build_orderflow_features(
            flow,
            decision_time_utc=decision,
            windows_seconds=(60,),
            large_trade_quantile=config.large_trade_quantile,
        )
        number = output.get("flow_imbalance_60s")
        values[FEATURES["FLOW"]] = (
            float(number) if isinstance(number, (int, float)) and math.isfinite(number) else None
        )
        status["FLOW"] = "AVAILABLE" if values[FEATURES["FLOW"]] is not None else "INVALID"
    if not marks:
        mark_status = "UNAVAILABLE"
        basis_funding: dict[str, Any] = {}
    elif (decision - marks[-1].event_time_utc).total_seconds() > mark_max_age:
        mark_status = "STALE"
        basis_funding = {}
    else:
        mark_status = "AVAILABLE"
        basis_funding = build_basis_funding_features(
            marks,
            decision_time_utc=decision,
            extremeness_min_observations=config.funding_extremeness_min_observations,
        )
    for family, name in (("BASIS", "mark_index_basis_bps"), ("FUNDING", "funding_rate_predicted")):
        number = basis_funding.get(name)
        value = float(number) if isinstance(number, (int, float)) else None
        values[FEATURES[family]] = value if value is not None and math.isfinite(value) else None
        status[family] = (
            mark_status
            if mark_status != "AVAILABLE"
            else "AVAILABLE"
            if values[FEATURES[family]] is not None
            else "INVALID"
        )
    return values, status


def align_pit_features(
    dataset: pd.DataFrame,
    events: Sequence[MarketEvent],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Align only original public observations available by each trade's open time."""
    index = _event_index(events)
    config = MarketIntelligenceConfig()
    enriched = dataset.copy()
    components: dict[str, dict[str, int]] = {
        family: {"AVAILABLE": 0, "STALE": 0, "UNAVAILABLE": 0, "INVALID": 0} for family in FEATURES
    }
    collected: dict[str, list[float | None]] = {name: [] for name in FEATURES.values()}
    for row in dataset.itertuples(index=False):
        decision = pd.Timestamp(row.open_time_utc).to_pydatetime()
        if pd.Timestamp(row.feature_cutoff_utc) > pd.Timestamp(row.open_time_utc):
            raise MarketIntelligenceV2Error("baseline_feature_after_decision")
        values, statuses = _aligned_features(
            symbol=str(row.symbol), decision=decision, index=index, config=config
        )
        for family, status in statuses.items():
            components[family][status] += 1
        for name in collected:
            collected[name].append(values[name])
    for name, aligned_values in collected.items():
        enriched[name] = aligned_values
    count = len(dataset)
    coverage = {
        family: {
            "available_count": counters["AVAILABLE"],
            "feature_coverage": counters["AVAILABLE"] / count if count else 0.0,
            "stale_or_unavailable_count": count - counters["AVAILABLE"],
            "statuses": counters,
        }
        for family, counters in components.items()
    }
    return enriched, coverage


def _metrics(rows: pd.DataFrame) -> dict[str, Any]:
    economic = _economic_metrics(rows)
    return {
        "trade_count": economic["trade_count"],
        "net_pnl_usdt": economic["net_pnl"],
        "profit_factor": economic["profit_factor"],
        "expectancy": economic["expectancy"],
        "max_drawdown": economic["max_drawdown"],
        "win_rate": economic["win_rate"],
        "capital_hours": economic["capital_hours_total"],
        "net_pnl_per_capital_hour": economic["net_pnl_per_capital_hour"],
    }


def _selection_hash(rows: pd.DataFrame) -> str:
    identifiers = [int(value) for value in rows["trade_sequence"].tolist()]
    encoded = json.dumps(identifiers, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _evaluate_arm(
    *,
    dataset: pd.DataFrame,
    features: Sequence[str],
    splits: Sequence[Mapping[str, Any]],
    model_factory: ModelFactory,
    covered: pd.Series,
    frozen_thresholds: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    all_indices = [int(i) for split in splits for i in split["_test_indices"]]
    universe = dataset.iloc[all_indices].loc[covered.iloc[all_indices]]
    selected_frames: list[pd.DataFrame] = []
    fold_reports: list[dict[str, Any]] = []
    for split in splits:
        partitions = [
            dataset.iloc[list(split[f"_{name}_indices"])]
            for name in ("train", "validation", "test")
        ]
        train, validation, test = [frame.loc[covered.loc[frame.index]] for frame in partitions]
        if train.empty or validation.empty or test.empty:
            raise MarketIntelligenceV2Error("covered_fold_partition_empty")
        validation_scores, test_scores = _fit_predict(
            train=train,
            validation=validation,
            test=test,
            features=features,
            model_factory=model_factory,
        )
        if frozen_thresholds is None:
            threshold = float(
                _calibrate_threshold(
                    validation_scores, validation[TARGET_COLUMN].to_numpy(dtype=float)
                )["threshold"]
            )
        else:
            threshold = float(frozen_thresholds.get(str(split["split_id"]), float("nan")))
            if not math.isfinite(threshold):
                raise MarketIntelligenceV2Error("frozen_threshold_missing_or_invalid")
        chosen = test.loc[test_scores >= threshold].copy()
        selected_frames.append(chosen)
        fold_reports.append(
            {
                "split_id": split["split_id"],
                "oos_universe_count": len(test),
                "cohort_identity_sha256": _selection_hash(test),
                "train_count": len(train),
                "validation_count": len(validation),
                "threshold": threshold,
                "threshold_source": "past_validation_only"
                if frozen_thresholds is None
                else "previous_train_calibration_frozen",
                **_metrics(chosen),
                "selection_identity_sha256": _selection_hash(chosen),
                "by_symbol": {
                    str(value): _metrics(chosen.loc[chosen["symbol"].eq(value)])
                    for value in sorted(test["symbol"].astype(str).unique())
                },
                "by_side": {
                    str(value): _metrics(chosen.loc[chosen["side"].eq(value)])
                    for value in sorted(test["side"].astype(str).unique())
                },
            }
        )
    selected_oos = pd.concat(selected_frames).sort_values(
        ["open_time_utc", "trade_sequence"], kind="stable"
    )
    return {
        "status": "ok",
        **_metrics(selected_oos),
        "cohort_identity_sha256": _selection_hash(universe),
        "oos_universe_count": len(universe),
        "selection_identity_sha256": _selection_hash(selected_oos),
        "selection_trade_sequences": [int(value) for value in selected_oos["trade_sequence"]],
        "folds": fold_reports,
        "by_symbol": {
            str(value): _metrics(selected_oos.loc[selected_oos["symbol"].eq(value)])
            for value in sorted(universe["symbol"].astype(str).unique())
        },
        "by_side": {
            str(value): _metrics(selected_oos.loc[selected_oos["side"].eq(value)])
            for value in sorted(universe["side"].astype(str).unique())
        },
    }


def _no_br10_control(dataset: pd.DataFrame, splits: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    indices = [int(i) for split in splits for i in split["_test_indices"]]
    universe = dataset.iloc[indices].sort_values(["open_time_utc", "trade_sequence"], kind="stable")

    def segments(rows: pd.DataFrame, name: str) -> dict[str, Any]:
        return {
            str(value): _metrics(rows.loc[rows[name].eq(value)])
            for value in sorted(rows[name].astype(str).unique())
        }

    return {
        "status": "ok",
        **_metrics(universe),
        "oos_universe_count": len(universe),
        "cohort_identity_sha256": _selection_hash(universe),
        "selection_identity_sha256": _selection_hash(universe),
        "selection_trade_sequences": [int(value) for value in universe["trade_sequence"]],
        "selection_rule": "all_frozen_oos_candidates_no_br10_abstention",
        "feature_coverage": 1.0,
        "economic_candidate": False,
        "folds": [
            {
                "split_id": split["split_id"],
                "oos_universe_count": len(split["_test_indices"]),
                "cohort_identity_sha256": _selection_hash(
                    dataset.iloc[list(split["_test_indices"])]
                ),
                **_metrics(dataset.iloc[list(split["_test_indices"])]),
            }
            for split in splits
        ],
        "by_symbol": segments(universe, "symbol"),
        "by_side": segments(universe, "side"),
    }


def _compare_no_br10(
    report: dict[str, Any],
    baseline: Mapping[str, Any],
    dataset: pd.DataFrame,
    *,
    future_join_count: int,
    anti_leakage_status: str,
) -> None:
    if report["status"] != "ok":
        return
    if report.get("cohort_identity_sha256") != baseline["cohort_identity_sha256"]:
        report.update(
            {
                "no_br10_comparison_status": "blocked",
                "no_br10_comparison_reason": "no_br10_cohort_identity_mismatch",
                "delta_net_pnl_vs_no_br10_control": None,
                "incremental_expectancy_vs_no_br10_control": None,
                "fold_positive_count_vs_no_br10_control": 0,
                "economic_candidate_vs_no_br10_control": False,
            }
        )
        return
    report["no_br10_comparison_status"] = "ok"
    report["delta_net_pnl_vs_no_br10_control"] = report["net_pnl_usdt"] - baseline["net_pnl_usdt"]
    report["incremental_expectancy_vs_no_br10_control"] = (
        None
        if report["expectancy"] is None or baseline["expectancy"] is None
        else report["expectancy"] - baseline["expectancy"]
    )
    for dimension in ("by_symbol", "by_side"):
        for name, segment in report[dimension].items():
            segment["delta_net_pnl_vs_no_br10_control"] = (
                segment["net_pnl_usdt"] - baseline[dimension][name]["net_pnl_usdt"]
            )
    for fold, base in zip(report["folds"], baseline["folds"], strict=True):
        fold["delta_net_pnl_vs_no_br10_control"] = fold["net_pnl_usdt"] - base["net_pnl_usdt"]
    report["fold_positive_count_vs_no_br10_control"] = sum(
        fold["delta_net_pnl_vs_no_br10_control"] > 0 for fold in report["folds"]
    )
    removed_ids = set(baseline["selection_trade_sequences"]) - set(
        report["selection_trade_sequences"]
    )
    removed = dataset.loc[dataset["trade_sequence"].isin(removed_ids)].sort_values(
        ["open_time_utc", "trade_sequence"], kind="stable"
    )
    report["filtered_vs_no_br10_control"] = {
        "count": len(removed),
        "observed_net_pnl_usdt": float(removed[TARGET_COLUMN].sum()),
        "outcomes_used_for_decision": False,
        "analysis_timing": "ex_post_after_frozen_selection",
        "candidates": [
            {
                "trade_sequence": int(row.trade_sequence),
                "order_id": str(row.order_id) if hasattr(row, "order_id") else None,
                "symbol": str(row.symbol),
                "side": str(row.side),
                "decision_time_utc": pd.Timestamp(row.open_time_utc).isoformat(),
                "observed_net_pnl_usdt": float(getattr(row, TARGET_COLUMN)),
            }
            for row in removed.itertuples(index=False)
        ],
    }
    report["economic_candidate_vs_no_br10_control"] = (
        report["delta_net_pnl_vs_no_br10_control"] > 0
        and report["incremental_expectancy_vs_no_br10_control"] is not None
        and report["incremental_expectancy_vs_no_br10_control"] > 0
        and report["fold_positive_count_vs_no_br10_control"] >= 2
        and future_join_count == 0
        and anti_leakage_status == "PASS"
    )


def _evaluate_oos_variants(
    *,
    dataset: pd.DataFrame,
    baseline_features: Sequence[str],
    splits: Sequence[Mapping[str, Any]],
    model_factory: ModelFactory,
    frozen_thresholds: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, Any]:
    """Compare each variant against Control on exactly its PIT-covered frozen folds."""
    if len(splits) != 3:
        raise MarketIntelligenceV2Error("expected_three_frozen_folds")
    if not baseline_features or len(set(baseline_features)) != len(baseline_features):
        raise MarketIntelligenceV2Error("baseline_feature_set_invalid")
    if set(baseline_features) - set(EXPECTED_FEATURES):
        raise MarketIntelligenceV2Error("baseline_feature_outside_frozen_contract")
    all_indices = [int(i) for split in splits for i in split["_test_indices"]]
    if len(set(all_indices)) != len(all_indices):
        raise MarketIntelligenceV2Error("oos_fold_overlap")
    for split in splits:
        train_ids, validation_ids, test_ids = [
            set(split[f"_{name}_indices"]) for name in ("train", "validation", "test")
        ]
        if train_ids & validation_ids or train_ids & test_ids or validation_ids & test_ids:
            raise MarketIntelligenceV2Error("fold_partition_overlap")
        train, validation, test = [
            dataset.iloc[list(split[f"_{name}_indices"])]
            for name in ("train", "validation", "test")
        ]
        if train.empty or validation.empty or test.empty:
            raise MarketIntelligenceV2Error("empty_frozen_fold_partition")
        if train["close_time_utc"].max() > validation["open_time_utc"].min():
            raise MarketIntelligenceV2Error("train_outcome_not_available_at_calibration")
        if validation["close_time_utc"].max() > test["open_time_utc"].min():
            raise MarketIntelligenceV2Error("calibration_outcome_not_available_at_oos")
    feature_names = {
        family: HISTORICAL_FEATURES.get(family, legacy)
        if HISTORICAL_FEATURES.get(family) in dataset.columns
        else legacy
        for family, legacy in FEATURES.items()
    }
    reports: dict[str, dict[str, Any]] = {}
    controls: dict[tuple[bool, ...], dict[str, Any]] = {}
    for variant, families in VARIANTS.items():
        covered = pd.Series(True, index=dataset.index)
        for family in families:
            name = feature_names[family]
            covered &= (
                dataset[name].map(lambda value: pd.notna(value) and math.isfinite(value))
                if name in dataset
                else False
            )
        count = int(covered.iloc[all_indices].sum())
        report: dict[str, Any] = {
            "feature_coverage": count / len(all_indices),
            "stale_or_unavailable_count": len(all_indices) - count,
            "economic_candidate": False,
        }
        if not count:
            reports[variant] = {
                **report,
                "status": "blocked",
                "reason": "SOURCE_UNAVAILABLE",
                "net_pnl_usdt": None,
                "folds": [],
            }
            continue
        try:
            if frozen_thresholds is not None and (
                variant not in frozen_thresholds or "CONTROL" not in frozen_thresholds
            ):
                raise MarketIntelligenceV2Error("frozen_variant_thresholds_missing")
            key = tuple(bool(value) for value in covered)
            if key not in controls:
                controls[key] = _evaluate_arm(
                    dataset=dataset,
                    features=baseline_features,
                    splits=splits,
                    model_factory=model_factory,
                    covered=covered,
                    frozen_thresholds=frozen_thresholds["CONTROL"]
                    if frozen_thresholds is not None
                    else None,
                )
            control = controls[key]
            report.update(
                _evaluate_arm(
                    dataset=dataset,
                    features=[*baseline_features, *(feature_names[family] for family in families)],
                    splits=splits,
                    model_factory=model_factory,
                    covered=covered,
                    frozen_thresholds=frozen_thresholds[variant]
                    if frozen_thresholds is not None
                    else None,
                )
            )
        except MarketIntelligenceV2Error as exc:
            reports[variant] = {
                **report,
                "status": "blocked",
                "reason": str(exc),
                "net_pnl_usdt": None,
                "folds": [],
            }
            continue
        report["matched_control"] = control
        report["same_candidate_subset_as_control"] = (
            report["cohort_identity_sha256"] == control["cohort_identity_sha256"]
        )
        delta = report["net_pnl_usdt"] - control["net_pnl_usdt"]
        report["delta_net_pnl_vs_control"] = delta
        report["incremental_expectancy"] = (
            None
            if report["expectancy"] is None or control["expectancy"] is None
            else report["expectancy"] - control["expectancy"]
        )
        positive = sum(
            fold["net_pnl_usdt"] > base["net_pnl_usdt"]
            for fold, base in zip(report["folds"], control["folds"], strict=True)
        )
        report["fold_positive_count"] = positive
        report["economic_candidate"] = variant != "CONTROL" and (
            delta > 0
            and report["incremental_expectancy"] is not None
            and report["incremental_expectancy"] > 0
            and positive >= 2
        )
        for dimension in ("by_symbol", "by_side"):
            for value, segment in report[dimension].items():
                baseline_segment = control[dimension].get(value)
                segment["delta_net_pnl_vs_control"] = (
                    segment["net_pnl_usdt"] - baseline_segment["net_pnl_usdt"]
                    if baseline_segment is not None
                    else None
                )
        for fold, base in zip(report["folds"], control["folds"], strict=True):
            fold["delta_net_pnl_vs_control"] = fold["net_pnl_usdt"] - base["net_pnl_usdt"]
            for dimension in ("by_symbol", "by_side"):
                for value, segment in fold[dimension].items():
                    segment["delta_net_pnl_vs_control"] = (
                        segment["net_pnl_usdt"] - base[dimension][value]["net_pnl_usdt"]
                    )
        reports[variant] = report
    no_br10 = _no_br10_control(dataset, splits)
    for variant, report in reports.items():
        _compare_no_br10(report, no_br10, dataset, future_join_count=0, anti_leakage_status="PASS")
        if report["status"] == "ok":
            report["economic_candidate_vs_br10_current"] = report["economic_candidate"]
            report["economic_candidate"] = (
                variant != "CONTROL" and report["economic_candidate_vs_no_br10_control"]
            )
    candidates = [name for name, report in reports.items() if report["economic_candidate"]]
    _compare_no_br10(no_br10, no_br10, dataset, future_join_count=0, anti_leakage_status="PASS")
    reports["NO_BR10_CONTROL"] = no_br10
    reports["BR10_CURRENT"] = reports["CONTROL"]
    return {
        "status": "ok",
        "decision": "ECONOMIC_CANDIDATE" if candidates else "MARKET_INTELLIGENCE_V2_NO_OOS_UPLIFT",
        "best_variant": None,
        "candidate_variants": candidates,
        "oos_used_for_variant_choice": False,
        "primary_economic_baseline": "NO_BR10_CONTROL",
        "thresholds_recalibrated": frozen_thresholds is None,
        "variants": reports,
        "folds": [split["split_id"] for split in splits],
        "oos_universe_count": len(all_indices),
        "anti_leakage_status": "PASS",
        "point_in_time_contract": "PASS",
        "same_oos_universe_and_costs": True,
        "comparison_scope": "variant_specific_PIT_covered_cohort_and_matched_control",
        "outcome_used_for_selection": False,
        "future_join_count": 0,
    }


def build_market_intelligence_v2_oos_ablation_v1(
    *,
    project_root: str | Path,
    market_events_path: str | Path | None,
    market_events_sha256: str | None = None,
    dataset_path: str | Path | None = None,
    feature_contract_path: str | Path | None = None,
    dataset_manifest_path: str | Path | None = None,
    split_manifest_path: str | Path | None = None,
    model_factory: ModelFactory | None = None,
    frozen_thresholds: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, Any]:
    """Validate source lineage and run a no-write comparison, or block explicitly."""
    source: dict[str, Any] = {}
    coverage: dict[str, Any] | None = None
    fold_ids: list[str] = []
    try:
        dataset, baseline, splits, source = _load_bundle(
            project_root=project_root,
            dataset_path=dataset_path,
            feature_contract_path=feature_contract_path,
            dataset_manifest_path=dataset_manifest_path,
            split_manifest_path=split_manifest_path,
        )
        fold_ids = [str(split["split_id"]) for split in splits]
        coverage = {
            family: {
                "available_count": 0,
                "feature_coverage": 0.0,
                "stale_or_unavailable_count": len(dataset),
                "source_status": "SOURCE_UNAVAILABLE",
            }
            for family in FEATURES
        }
        if market_events_path is None:
            raise MarketIntelligenceV2Error("pit_event_source_required")
        if (
            market_events_sha256 is None
            or len(market_events_sha256) != 64
            or any(character not in "0123456789abcdef" for character in market_events_sha256)
        ):
            raise MarketIntelligenceV2Error("pit_event_source_sha256_required")
        candidate = Path(market_events_path)
        events_path = candidate if candidate.is_absolute() else Path(project_root) / candidate
        events, digest = _load_events(events_path)
        if digest != market_events_sha256:
            raise MarketIntelligenceV2Error("pit_event_source_sha256_mismatch")
        source["market_events_path"] = str(events_path.resolve())
        source["market_events_sha256"] = digest
        source["market_event_count"] = len(events)
        if isinstance(events[0], PITObservation):
            aligned, coverage = align_observations(
                dataset, [item for item in events if isinstance(item, PITObservation)]
            )
            source["availability_is_modeled"] = True
            source["historical_reception_proven"] = False
            source["feature_semantics"] = HISTORICAL_FEATURES
        else:
            aligned, coverage = align_pit_features(
                dataset, [item for item in events if isinstance(item, MarketEvent)]
            )
        result = _evaluate_oos_variants(
            dataset=aligned,
            baseline_features=baseline,
            splits=splits,
            model_factory=model_factory or _default_model_factory,
            frozen_thresholds=frozen_thresholds,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "source": source,
            "coverage": coverage,
            "write_performed": False,
            **SAFETY_FLAGS,
            **result,
        }
    except (
        AblationError,
        MarketIntelligenceV2Error,
        OSError,
        ValueError,
        TypeError,
        KeyError,
    ) as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "reason": str(exc),
            "decision": "MARKET_INTELLIGENCE_V2_PIT_BLOCKED",
            "source": source,
            "coverage": coverage,
            "folds": fold_ids,
            "anti_leakage_status": "BLOCKED",
            "point_in_time_contract": "BLOCKED",
            "write_performed": False,
            **SAFETY_FLAGS,
        }
