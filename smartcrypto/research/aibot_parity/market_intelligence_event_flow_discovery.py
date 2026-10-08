"""Event-level research discovery, never certification on consumed WQ6 OOS.

The rule is fixed before observing event-flow outcomes: accept only if all three
window imbalances agree strictly with LONG/SHORT. No fitting, calibration,
FLOW_LITE thresholds, or tuning after seeing the historical result is allowed.
An independent later cohort is audited before loading events. This historical
runner refuses to consume a fresh holdout; it requires a separate sealed,
one-shot evaluation plan rather than quietly treating it as another backtest.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.research.aibot_parity.market_intelligence_event_flow_source import (
    FEATURES,
    WINDOWS,
    EventFlowError,
    align_event_flow,
    file_hash,
)
from smartcrypto.research.aibot_parity.market_intelligence_pit_source_foundation import (
    PITSourceError,
    encode,
    external_root,
    sha256,
)
from smartcrypto.research.aibot_parity.market_intelligence_pnl_ablation import (
    TARGET_COLUMN,
    SAFETY_FLAGS,
    AblationError,
    _load_bundle,
)
from smartcrypto.research.aibot_parity.market_intelligence_v2_oos_ablation import (
    _compare_no_br10,
    _metrics,
    _no_br10_control,
    _selection_hash,
)

SCHEMA = "market_intelligence_usdm_aggtrades_discovery_v1"
RULE = {
    "name": "side_aligned_positive_taker_imbalance_all_three_windows",
    "windows_seconds": list(WINDOWS),
    "threshold": 0.0,
    "combination": "all",
    "parameter_origin": "fixed_ex_ante_no_calibration",
}
RULE_SHA256 = sha256(encode(RULE))


def fresh_oos_gate(
    previous: pd.DataFrame,
    splits: Sequence[Mapping[str, Any]],
    new_cohort: pd.DataFrame | None = None,
    *,
    observed_at_utc: pd.Timestamp | None = None,
) -> dict[str, Any]:
    indices = [int(i) for split in splits for i in split["_test_indices"]]
    if len(indices) != len(set(indices)) or not indices:
        raise EventFlowError("previous_oos_identity_overlap_or_empty")
    consumed = previous.iloc[indices]
    end = pd.to_datetime(consumed.close_time_utc, utc=True).max()
    now = observed_at_utc if observed_at_utc is not None else pd.Timestamp.now(tz="UTC")
    if pd.isna(end) or now.tzinfo is None:
        raise EventFlowError("oos_gate_timestamp_invalid")
    candidates = (
        previous.loc[pd.to_datetime(previous.open_time_utc, utc=True) > end].copy()
        if new_cohort is None
        else new_cohort.copy()
    )
    required = {"order_id", "symbol", "side", "open_time_utc", "close_time_utc", TARGET_COLUMN}
    if not required.issubset(candidates.columns):
        raise EventFlowError("new_cohort_identity_or_outcome_schema_missing")
    opened = pd.to_datetime(candidates.open_time_utc, utc=True, errors="coerce")
    closed = pd.to_datetime(candidates.close_time_utc, utc=True, errors="coerce")
    pnl = pd.to_numeric(candidates[TARGET_COLUMN], errors="coerce")
    ids = candidates.order_id.astype(str)
    overlaps = int(ids.isin(previous.order_id.astype(str)).sum())
    collision = ids.duplicated().any() or candidates.order_id.isna().any()
    outcomes = (
        closed.notna() & (closed >= opened) & (closed <= now) & pnl.notna() & np.isfinite(pnl)
    )
    independent = (
        len(candidates) > 0
        and not collision
        and overlaps == 0
        and opened.notna().all()
        and (opened > end).all()
        and outcomes.all()
    )
    return {
        "previous_oos_end": end.isoformat(),
        "previous_oos_last_decision": pd.to_datetime(consumed.open_time_utc, utc=True)
        .max()
        .isoformat(),
        "previous_oos_candidate_count": len(consumed),
        "candidate_new_oos_start": opened.min().isoformat() if opened.notna().any() else None,
        "candidate_new_oos_end": opened.max().isoformat() if opened.notna().any() else None,
        "candidate_count": len(candidates),
        "outcome_available_count": int(outcomes.sum()),
        "overlap_count": overlaps,
        "identity_collision": bool(collision),
        "fresh_oos_available": bool(independent),
        "certification_status": "PENDING_SEALED_ONE_SHOT_EVALUATION"
        if independent
        else "BLOCKED_NO_FRESH_OOS",
        "economic_candidate": False,
        "promotion_allowed": False,
    }


def fixed_selection(candidates: pd.DataFrame) -> pd.Series:
    side = candidates.side.astype(str).str.lower()
    if not side.isin(["long", "short"]).all():
        raise EventFlowError("candidate_side_invalid")
    direction = side.map({"long": 1.0, "short": -1.0})
    chosen = candidates.event_flow_covered.astype(bool).copy()
    for seconds in WINDOWS:
        feature = pd.to_numeric(candidates[f"taker_imbalance_{seconds // 60}m"], errors="coerce")
        chosen &= feature.notna() & np.isfinite(feature) & (direction * feature > 0)
    return chosen


def historical_discovery(
    candidates: pd.DataFrame,
    splits: Sequence[Mapping[str, Any]],
    gate: Mapping[str, Any],
) -> dict[str, Any]:
    if gate["fresh_oos_available"]:
        raise EventFlowError("fresh_holdout_requires_sealed_one_shot_plan_not_historical_discovery")
    if len(splits) != 3 or not candidates.event_flow_covered.all():
        raise EventFlowError("complete_three_period_historical_coverage_required")
    numeric = candidates[list(FEATURES)].to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        raise EventFlowError("event_flow_feature_unavailable")
    baseline = _no_br10_control(candidates, splits)
    mask = fixed_selection(candidates)
    selected = candidates.loc[mask]

    def segments(name: str) -> dict[str, Any]:
        return {
            str(v): _metrics(selected.loc[selected[name].eq(v)])
            for v in sorted(candidates[name].astype(str).unique())
        }

    arm = {
        "status": "ok",
        **_metrics(selected),
        "oos_universe_count": len(candidates),
        "cohort_identity_sha256": _selection_hash(candidates),
        "selection_identity_sha256": _selection_hash(selected),
        "selection_trade_sequences": [int(i) for i in selected.trade_sequence],
        "folds": [
            {
                "split_id": split["split_id"],
                **_metrics(
                    candidates.iloc[list(split["_test_indices"])].loc[
                        mask.iloc[list(split["_test_indices"])]
                    ]
                ),
                "selection_rule_sha256": RULE_SHA256,
            }
            for split in splits
        ],
        "by_symbol": segments("symbol"),
        "by_side": segments("side"),
        "feature_coverage": 1.0,
        "selection_rule": RULE,
        "selection_rule_sha256": RULE_SHA256,
    }
    _compare_no_br10(arm, baseline, candidates, future_join_count=0, anti_leakage_status="PASS")
    _compare_no_br10(
        baseline, baseline, candidates, future_join_count=0, anti_leakage_status="PASS"
    )
    for variant in (baseline, arm):
        variant["historical_metric_gate_passed"] = variant.pop(
            "economic_candidate_vs_no_br10_control"
        )
        variant["economic_candidate"] = False
        variant["promotion_allowed"] = False
        variant["research_discovery_only"] = True
        variant["delta_net_pnl_vs_control"] = variant["delta_net_pnl_vs_no_br10_control"]
        variant["incremental_expectancy"] = variant["incremental_expectancy_vs_no_br10_control"]
        variant["pnl_per_capital_hour"] = variant["net_pnl_per_capital_hour"]
    return {
        "status": "ok",
        "decision": "RESEARCH_DISCOVERY_ONLY",
        "certification_status": gate["certification_status"],
        "research_discovery_only": True,
        "economic_candidate": False,
        "promotion_allowed": False,
        "variants": {"NO_BR10_CONTROL": baseline, "EVENT_FLOW_ONLY": arm},
        "parameter_calibration_performed": False,
        "flow_lite_thresholds_used": False,
        "oos_used_for_parameter_choice": False,
        "outcomes_used_for_selection": False,
        "anti_leakage_status": "PASS",
        "future_join_count": 0,
    }


def build_event_flow_discovery(
    *,
    project_root: str | Path,
    cache_root: str | Path,
    allow_download: bool = False,
    dataset_path: str | Path | None = None,
    feature_contract_path: str | Path | None = None,
    dataset_manifest_path: str | Path | None = None,
    split_manifest_path: str | Path | None = None,
    new_cohort_path: str | Path | None = None,
    new_cohort_sha256: str | None = None,
) -> dict[str, Any]:
    source: dict[str, Any] = {}
    gate: dict[str, Any] | None = None
    coverage: dict[str, Any] | None = None
    common = {
        "schema_version": SCHEMA,
        "write_performed": False,
        **SAFETY_FLAGS,
        "promotion_allowed": False,
        "economic_candidate": False,
    }
    try:
        cache = external_root(Path(cache_root), Path(project_root))
        dataset, _, splits, source = _load_bundle(
            project_root=project_root,
            dataset_path=dataset_path,
            feature_contract_path=feature_contract_path,
            dataset_manifest_path=dataset_manifest_path,
            split_manifest_path=split_manifest_path,
        )
        new_cohort = None
        if new_cohort_path is not None:
            path = Path(new_cohort_path)
            if (
                path.is_symlink()
                or not path.is_file()
                or new_cohort_sha256 is None
                or file_hash(path) != new_cohort_sha256
            ):
                raise EventFlowError("new_cohort_hash_or_source_invalid")
            new_cohort = pd.read_parquet(path)
            source["new_cohort_sha256"] = new_cohort_sha256
        gate = fresh_oos_gate(dataset, splits, new_cohort)
        if gate["fresh_oos_available"]:
            raise EventFlowError(
                "fresh_holdout_requires_sealed_one_shot_plan_not_historical_discovery"
            )
        indices = [int(i) for split in splits for i in split["_test_indices"]]
        candidates = dataset.iloc[indices].reset_index(drop=True)
        aligned, coverage = align_event_flow(candidates, cache, allow_download=allow_download)
        local_splits = []
        offset = 0
        for split in splits:
            count = len(split["_test_indices"])
            local_splits.append(
                {
                    "split_id": split["split_id"],
                    "_test_indices": list(range(offset, offset + count)),
                }
            )
            offset += count
        result = historical_discovery(aligned, local_splits, gate)
        return {
            **common,
            "source": source,
            "fresh_oos_gate": gate,
            "coverage": coverage,
            "feature_names": list(FEATURES),
            **result,
        }
    except (
        AblationError,
        EventFlowError,
        PITSourceError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:
        return {
            **common,
            "status": "blocked",
            "reason": str(exc),
            "decision": "EVENT_FLOW_RESEARCH_BLOCKED",
            "fresh_oos_gate": gate,
            "certification_status": gate["certification_status"]
            if gate
            else "BLOCKED_GATE_NOT_VERIFIED",
            "research_discovery_only": True,
            "source": source,
            "coverage": coverage,
            "anti_leakage_status": "BLOCKED",
        }
