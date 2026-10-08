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


def _load_events(path: Path) -> tuple[list[MarketEvent], str]:
    if path.is_symlink() or not path.is_file():
        raise MarketIntelligenceV2Error("pit_event_source_missing_or_not_regular")
    config = MarketIntelligenceConfig()
    allowed = set(config.allowed_public_sources) - {"offline_fixture"}
    events: list[MarketEvent] = []
    seen: set[str] = set()
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                payload = json.loads(line)
                event = MarketEvent.model_validate(payload)
                if event.source_id not in allowed:
                    raise MarketIntelligenceV2Error(
                        f"pit_source_not_authorized:line={line_number}"
                    )
                if event.event_id in seen:
                    raise MarketIntelligenceV2Error(
                        f"pit_event_identity_collision:line={line_number}"
                    )
                seen.add(event.event_id)
                if event.event_type in {"agg_trade", "mark_price"}:
                    events.append(event)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise MarketIntelligenceV2Error(
            f"pit_event_source_invalid:{type(exc).__name__}"
        ) from exc
    if not events:
        raise MarketIntelligenceV2Error("pit_event_source_empty")
    return events, _source_hash(path)


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
        event for event in flow_events[flow_start:flow_end]
        if event.available_at_utc <= decision
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
            float(number) if isinstance(number, (int, float)) and math.isfinite(number)
            else None
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
            mark_status if mark_status != "AVAILABLE" else
            "AVAILABLE" if values[FEATURES[family]] is not None else "INVALID"
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
        family: {"AVAILABLE": 0, "STALE": 0, "UNAVAILABLE": 0, "INVALID": 0}
        for family in FEATURES
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


def _evaluate_oos_variants(
    *,
    dataset: pd.DataFrame,
    baseline_features: Sequence[str],
    splits: Sequence[Mapping[str, Any]],
    model_factory: ModelFactory,
) -> dict[str, Any]:
    """Freeze every fold's calibration before reading its held-out outcomes."""
    if len(splits) != 3:
        raise MarketIntelligenceV2Error("expected_three_frozen_folds")
    if not baseline_features or len(set(baseline_features)) != len(baseline_features):
        raise MarketIntelligenceV2Error("baseline_feature_set_invalid")
    if set(baseline_features) - set(EXPECTED_FEATURES):
        raise MarketIntelligenceV2Error("baseline_feature_outside_frozen_contract")
    all_indices = [int(i) for split in splits for i in split["_test_indices"]]
    if len(set(all_indices)) != len(all_indices):
        raise MarketIntelligenceV2Error("oos_fold_overlap")
    universe = dataset.iloc[all_indices]
    reports: dict[str, dict[str, Any]] = {}
    for variant, families in VARIANTS.items():
        features = [*baseline_features, *(FEATURES[family] for family in families)]
        selected_frames: list[pd.DataFrame] = []
        fold_reports: list[dict[str, Any]] = []
        for split in splits:
            train_ids = set(split["_train_indices"])
            validation_ids = set(split["_validation_indices"])
            test_ids = set(split["_test_indices"])
            if (train_ids & validation_ids) or (train_ids & test_ids) or (validation_ids & test_ids):
                raise MarketIntelligenceV2Error("fold_partition_overlap")
            train = dataset.iloc[list(split["_train_indices"])]
            validation = dataset.iloc[list(split["_validation_indices"])]
            test = dataset.iloc[list(split["_test_indices"])]
            if train.empty or validation.empty or test.empty:
                raise MarketIntelligenceV2Error("empty_frozen_fold_partition")
            if train["close_time_utc"].max() > validation["open_time_utc"].min():
                raise MarketIntelligenceV2Error("train_outcome_not_available_at_calibration")
            if validation["close_time_utc"].max() > test["open_time_utc"].min():
                raise MarketIntelligenceV2Error("calibration_outcome_not_available_at_oos")
            validation_scores, test_scores = _fit_predict(
                train=train,
                validation=validation,
                test=test,
                features=features,
                model_factory=model_factory,
            )
            threshold = float(_calibrate_threshold(
                validation_scores, validation[TARGET_COLUMN].to_numpy(dtype=float)
            )["threshold"])
            selected = test_scores >= threshold
            chosen = test.loc[selected].copy()
            selected_frames.append(chosen)
            fold_reports.append({
                "split_id": split["split_id"],
                "oos_universe_count": len(test),
                "threshold": threshold,
                "threshold_source": "past_validation_only",
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
            })
        selected_oos = pd.concat(selected_frames).sort_values(
            ["open_time_utc", "trade_sequence"], kind="stable"
        )
        reports[variant] = {
            **_metrics(selected_oos),
            "selection_identity_sha256": _selection_hash(selected_oos),
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
    control = reports["CONTROL"]
    for variant, report in reports.items():
        delta = report["net_pnl_usdt"] - control["net_pnl_usdt"]
        report["delta_net_pnl_vs_control"] = delta
        report["incremental_expectancy"] = (
            None if report["expectancy"] is None or control["expectancy"] is None
            else report["expectancy"] - control["expectancy"]
        )
        positive = sum(
            fold["net_pnl_usdt"] > base["net_pnl_usdt"]
            for fold, base in zip(report["folds"], control["folds"], strict=True)
        )
        report["fold_positive_count"] = positive
        report["economic_candidate"] = variant != "CONTROL" and (
            delta > 0 and report["incremental_expectancy"] is not None
            and report["incremental_expectancy"] > 0 and positive >= 2
        )
        for dimension in ("by_symbol", "by_side"):
            for value, segment in report[dimension].items():
                baseline_segment = control[dimension].get(value)
                segment["delta_net_pnl_vs_control"] = (
                    segment["net_pnl_usdt"] - baseline_segment["net_pnl_usdt"]
                    if baseline_segment is not None else None
                )
        for fold, base in zip(report["folds"], control["folds"], strict=True):
            fold["delta_net_pnl_vs_control"] = (
                fold["net_pnl_usdt"] - base["net_pnl_usdt"]
            )
            for dimension in ("by_symbol", "by_side"):
                for value, segment in fold[dimension].items():
                    segment["delta_net_pnl_vs_control"] = (
                        segment["net_pnl_usdt"] - base[dimension][value]["net_pnl_usdt"]
                    )
    candidates = [name for name, report in reports.items() if report["economic_candidate"]]
    best = max(candidates, key=lambda name: reports[name]["delta_net_pnl_vs_control"]) if candidates else None
    return {
        "status": "ok",
        "decision": "ECONOMIC_CANDIDATE" if best else "MARKET_INTELLIGENCE_V2_NO_OOS_UPLIFT",
        "best_variant": best,
        "variants": reports,
        "folds": [split["split_id"] for split in splits],
        "oos_universe_count": len(all_indices),
        "anti_leakage_status": "PASS",
        "point_in_time_contract": "PASS",
        "same_oos_universe_and_costs": True,
        "outcome_used_for_selection": False,
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
        if market_events_sha256 is None or len(market_events_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in market_events_sha256
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
        aligned, coverage = align_pit_features(dataset, events)
        missing = [family for family, item in coverage.items() if item["feature_coverage"] != 1.0]
        if missing:
            raise MarketIntelligenceV2Error("pit_feature_coverage_incomplete:" + ",".join(missing))
        result = _evaluate_oos_variants(
            dataset=aligned,
            baseline_features=baseline,
            splits=splits,
            model_factory=model_factory or _default_model_factory,
        )
        for name, report in result["variants"].items():
            families = VARIANTS[name]
            report["feature_coverage"] = (
                min(coverage[family]["feature_coverage"] for family in families)
                if families else 1.0
            )
            report["stale_or_unavailable_count"] = 0
        return {
            "schema_version": SCHEMA_VERSION,
            "source": source,
            "coverage": coverage,
            "write_performed": False,
            **SAFETY_FLAGS,
            **result,
        }
    except (AblationError, MarketIntelligenceV2Error, OSError, ValueError, TypeError, KeyError) as exc:
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
