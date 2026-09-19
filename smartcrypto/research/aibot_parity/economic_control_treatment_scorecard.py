"""Canonical research-only Control x Treatment economic scorecard.

Consumes merged Branch 09-13 evidence, exposes the six canonical KPI slots,
and never invents missing financial denominators or execution costs.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

SCHEMA_VERSION = "economic_control_treatment_scorecard_v1"
CANONICAL_KPIS = (
    "net_pnl",
    "roi",
    "expectancy",
    "profit_factor",
    "max_drawdown",
    "net_pnl_per_capital_hour",
)
MIN_TREATMENT_TRADES = 30
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 42
MAX_DRAWDOWN_RATIO_FOR_CANDIDATE = 1.05

EXPECTED_SCHEMAS = {
    "branch09": "ai_shadow_economic_challenger_sync_v1",
    "branch10": "market_intelligence_pnl_ablation_v1",
    "branch11": "execution_intelligence_net_pnl_attribution_v1",
    "branch12": "opportunity_allocator_capital_hour_uplift_v1",
    "ledger": "component_economic_attribution_ledger_v1",
}

SAFETY_FLAGS = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "promotion_allowed": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "changes_model": False,
    "changes_strategy": False,
    "changes_leverage": False,
    "changes_stake": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_data": False,
}


class EconomicScorecardError(RuntimeError):
    """Fail-closed Branch 14 validation error."""


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EconomicScorecardError(f"mapping_required:{field}")
    return value


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise EconomicScorecardError(f"invalid_numeric:{field}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EconomicScorecardError(f"invalid_numeric:{field}") from exc
    if not math.isfinite(number):
        raise EconomicScorecardError(f"non_finite:{field}")
    return number


def _optional(value: Any, field: str) -> float | None:
    return None if value is None else _number(value, field)


def _hash(payload: Any) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _validate(name: str, report: Mapping[str, Any]) -> None:
    if report.get("schema_version") != EXPECTED_SCHEMAS[name]:
        raise EconomicScorecardError(f"{name}_schema_mismatch")
    allowed = {"ok"} if name != "branch09" else {"ok", "waiting"}
    if report.get("status") not in allowed:
        raise EconomicScorecardError(f"{name}_status_invalid:{report.get('status')}")
    for key in ("paper_only", "shadow_only", "research_only"):
        if report.get(key) is not True:
            raise EconomicScorecardError(f"{name}_safety_true_mismatch:{key}")
    for key in (
        "operational_authority",
        "sends_orders",
        "exchange_private_access",
        "changes_risk",
        "writes_runtime",
    ):
        if report.get(key) is not False:
            raise EconomicScorecardError(f"{name}_safety_false_mismatch:{key}")


def _metric(
    control: float | None,
    treatment: float | None,
    unit: str,
    *,
    available: bool = True,
    reason: str | None = None,
) -> dict[str, Any]:
    delta = None
    relative = None
    if available and control is not None and treatment is not None:
        delta = float(treatment - control)
        if control != 0.0:
            relative = float(delta / abs(control))
    return {
        "control": control,
        "treatment": treatment,
        "delta": delta,
        "relative_delta": relative,
        "unit": unit,
        "available": available,
        "unavailable_reason": reason,
    }


def _empty_kpis(reason: str) -> dict[str, dict[str, Any]]:
    units = {
        "net_pnl": "USDT",
        "roi": "ratio",
        "expectancy": "USDT_per_trade",
        "profit_factor": "ratio",
        "max_drawdown": "USDT",
        "net_pnl_per_capital_hour": "USDT_per_capital_hour",
    }
    return {
        name: _metric(None, None, units[name], available=False, reason=reason)
        for name in CANONICAL_KPIS
    }


def _decision(
    kpis: Mapping[str, Mapping[str, Any]],
    treatment_count: int,
    sample_sufficient: bool,
) -> tuple[str, list[str]]:
    if treatment_count < MIN_TREATMENT_TRADES or not sample_sufficient:
        return "COLLECT/INSUFFICIENT", ["treatment_sample_insufficient"]

    net = kpis["net_pnl"]
    exp = kpis["expectancy"]
    pf = kpis["profit_factor"]
    if (
        net.get("treatment") is not None
        and exp.get("treatment") is not None
        and pf.get("treatment") is not None
        and float(net["treatment"]) <= 0.0
        and float(exp["treatment"]) <= 0.0
        and float(pf["treatment"]) < 1.0
    ):
        return "DISCARD", ["treatment_economics_negative"]

    blockers: list[str] = []
    for name in ("net_pnl", "roi", "expectancy", "net_pnl_per_capital_hour"):
        metric = kpis[name]
        if metric.get("available") is not True:
            blockers.append(f"{name}_unavailable")
        elif metric.get("delta") is None or float(metric["delta"]) <= 0.0:
            blockers.append(f"{name}_uplift_not_positive")

    if pf.get("available") is not True:
        blockers.append("profit_factor_unavailable")
    elif (
        pf.get("control") is None
        or pf.get("treatment") is None
        or float(pf["treatment"]) <= 1.0
        or float(pf["treatment"]) <= float(pf["control"])
    ):
        blockers.append("profit_factor_not_improved")

    dd = kpis["max_drawdown"]
    if dd.get("available") is not True:
        blockers.append("max_drawdown_unavailable")
    else:
        control_dd = float(dd.get("control") or 0.0)
        treatment_dd = float(dd.get("treatment") or 0.0)
        if control_dd == 0.0 and treatment_dd > 0.0:
            blockers.append("max_drawdown_worse_than_zero_baseline")
        elif control_dd > 0.0:
            if treatment_dd / control_dd > MAX_DRAWDOWN_RATIO_FOR_CANDIDATE:
                blockers.append("max_drawdown_ratio_exceeded")

    return ("CANDIDATE", []) if not blockers else ("RECALIBRATE", sorted(set(blockers)))


def _row(
    experiment_id: str,
    source: str,
    population: str,
    control_count: int,
    treatment_count: int,
    sample_sufficient: bool,
    kpis: Mapping[str, Mapping[str, Any]],
    cost_basis: str,
    stress_basis: str,
    ci_delta: Mapping[str, Any],
    dimensions: Mapping[str, Any],
) -> dict[str, Any]:
    if tuple(kpis.keys()) != CANONICAL_KPIS:
        raise EconomicScorecardError(f"{experiment_id}_canonical_kpis_incomplete")
    decision, blockers = _decision(kpis, treatment_count, sample_sufficient)
    return {
        "experiment_id": experiment_id,
        "source": source,
        "population_scope": population,
        "control_trade_count": control_count,
        "treatment_trade_count": treatment_count,
        "sample_sufficient": sample_sufficient,
        "minimum_treatment_trades": MIN_TREATMENT_TRADES,
        "kpis": dict(kpis),
        "ci_delta": dict(ci_delta),
        "cost_basis": cost_basis,
        "stress_basis": stress_basis,
        "dimensions": dict(dimensions),
        "research_decision": decision,
        "decision_blockers": blockers,
        "operational_authority": False,
        "promotion_allowed": False,
    }


def _branch10(report: Mapping[str, Any]) -> dict[str, Any]:
    full = _mapping(report.get("full_model"), "branch10.full_model")
    control = _mapping(full.get("control"), "branch10.control")
    treatment = _mapping(full.get("treatment"), "branch10.treatment")
    kpis = {
        "net_pnl": _metric(
            _number(control.get("net_pnl"), "branch10.control.net_pnl"),
            _number(treatment.get("net_pnl"), "branch10.treatment.net_pnl"),
            "USDT",
        ),
        "roi": _metric(
            None,
            None,
            "ratio",
            available=False,
            reason="capital_denominator_not_materialized_in_branch10_report",
        ),
        "expectancy": _metric(
            _optional(control.get("expectancy"), "branch10.control.expectancy"),
            _optional(treatment.get("expectancy"), "branch10.treatment.expectancy"),
            "USDT_per_trade",
        ),
        "profit_factor": _metric(
            _optional(control.get("profit_factor"), "branch10.control.profit_factor"),
            _optional(treatment.get("profit_factor"), "branch10.treatment.profit_factor"),
            "ratio",
        ),
        "max_drawdown": _metric(
            _optional(control.get("max_drawdown"), "branch10.control.max_drawdown"),
            _optional(treatment.get("max_drawdown"), "branch10.treatment.max_drawdown"),
            "USDT",
        ),
        "net_pnl_per_capital_hour": _metric(
            None,
            None,
            "USDT_per_capital_hour",
            available=False,
            reason="capital_hour_denominator_not_materialized_in_branch10_report",
        ),
    }
    return _row(
        "market_intelligence_oos",
        "branch10",
        "branch10_frozen_wq6_oos",
        int(_number(control.get("trade_count"), "branch10.control.trade_count")),
        int(_number(treatment.get("trade_count"), "branch10.treatment.trade_count")),
        True,
        kpis,
        "label_economic_net_pnl_shared_control_treatment",
        "same_wq6_target_no_extra_hypothetical_stress",
        {
            "available": False,
            "reason": "row_level_selected_oos_not_exposed_by_branch10_report",
        },
        {
            "model": "market_intelligence_full_model",
            "regime": {"available": False, "reason": "common_pit_regime_not_materialized"},
            "symbol_side": {"available": False, "reason": "not_exposed_by_branch10_report"},
            "capital_hour": {"available": False, "reason": "not_exposed_by_branch10_report"},
        },
    )


def _branch12(report: Mapping[str, Any]) -> dict[str, Any]:
    combined = _mapping(report.get("combined_oos"), "branch12.combined_oos")
    control = _mapping(combined.get("control"), "branch12.control")
    treatment = _mapping(combined.get("treatment"), "branch12.treatment")
    kpis = {
        "net_pnl": _metric(
            _number(control.get("net_pnl"), "branch12.control.net_pnl"),
            _number(treatment.get("net_pnl"), "branch12.treatment.net_pnl"),
            "USDT",
        ),
        "roi": _metric(
            None,
            None,
            "ratio",
            available=False,
            reason="capital_total_not_materialized_in_branch12_report",
        ),
        "expectancy": _metric(
            _optional(control.get("expectancy"), "branch12.control.expectancy"),
            _optional(treatment.get("expectancy"), "branch12.treatment.expectancy"),
            "USDT_per_trade",
        ),
        "profit_factor": _metric(
            _optional(control.get("profit_factor"), "branch12.control.profit_factor"),
            _optional(treatment.get("profit_factor"), "branch12.treatment.profit_factor"),
            "ratio",
        ),
        "max_drawdown": _metric(
            _optional(control.get("max_drawdown"), "branch12.control.max_drawdown"),
            _optional(treatment.get("max_drawdown"), "branch12.treatment.max_drawdown"),
            "USDT",
        ),
        "net_pnl_per_capital_hour": _metric(
            _number(
                control.get("net_pnl_per_capital_hour"),
                "branch12.control.net_pnl_per_capital_hour",
            ),
            _number(
                treatment.get("net_pnl_per_capital_hour"),
                "branch12.treatment.net_pnl_per_capital_hour",
            ),
            "USDT_per_capital_hour",
        ),
    }
    folds = report.get("folds")
    selections = []
    if isinstance(folds, list):
        selections = [
            {
                "test_period": item.get("test_period"),
                "selected_opportunity_keys": item.get("selected_opportunity_keys"),
            }
            for item in folds
            if isinstance(item, Mapping)
        ]
    return _row(
        "opportunity_allocator_oos",
        "branch12",
        "branch12_expanding_wq2_oos",
        int(_number(control.get("trade_count"), "branch12.control.trade_count")),
        int(_number(treatment.get("trade_count"), "branch12.treatment.trade_count")),
        True,
        kpis,
        "economic_net_pnl_shared_control_treatment",
        "same_official_master_net_pnl_no_extra_hypothetical_stress",
        {
            "available": False,
            "reason": "row_level_selected_oos_not_exposed_by_branch12_report",
        },
        {
            "model": "opportunity_allocator_symbol_side_top2",
            "regime": {"available": False, "reason": "common_pit_regime_not_materialized"},
            "symbol_side": {"available": bool(selections), "selected_by_fold": selections},
            "capital_hour": {"available": True},
        },
    )


def _bootstrap_shadow(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    treatment_count = sum(item.get("shadow_selected") is True for item in rows)
    if len(rows) < MIN_TREATMENT_TRADES or treatment_count < MIN_TREATMENT_TRADES:
        return {
            "available": False,
            "reason": "minimum_resolved_or_treatment_sample_not_met",
            "resamples": 0,
        }
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    deltas: list[float] = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        indices = rng.integers(0, len(rows), size=len(rows))
        sample = [rows[int(index)] for index in indices]
        control = [float(item["net_pnl"]) for item in sample]
        treatment = [
            float(item["net_pnl"])
            for item in sample
            if item.get("shadow_selected") is True
        ]
        if not treatment:
            continue
        deltas.append(float(np.mean(treatment) - np.mean(control)))
    if len(deltas) < BOOTSTRAP_RESAMPLES // 2:
        return {
            "available": False,
            "reason": "bootstrap_valid_resamples_insufficient",
            "resamples": len(deltas),
        }
    values = np.asarray(deltas, dtype=float)
    return {
        "available": True,
        "metric": "expectancy_delta",
        "confidence_level": 0.95,
        "lower": float(np.quantile(values, 0.025)),
        "median": float(np.quantile(values, 0.5)),
        "upper": float(np.quantile(values, 0.975)),
        "resamples": len(deltas),
        "seed": BOOTSTRAP_SEED,
    }


def _branch09(report: Mapping[str, Any]) -> dict[str, Any]:
    comparison = _mapping(report.get("global_comparison"), "branch09.global_comparison")
    control = _mapping(comparison.get("control"), "branch09.control")
    treatment = _mapping(comparison.get("treatment"), "branch09.treatment")
    control_count = int(_number(control.get("trade_count"), "branch09.control.trade_count"))
    treatment_count = int(
        _number(comparison.get("selected_trade_count"), "branch09.selected_trade_count")
    )
    sufficient = bool(report.get("sample_sufficient_for_diagnostic_comparison"))
    sufficient = sufficient and treatment_count >= MIN_TREATMENT_TRADES
    rows_raw = report.get("resolved_rows")
    rows = (
        [item for item in rows_raw if isinstance(item, Mapping)]
        if isinstance(rows_raw, list)
        else []
    )

    if not sufficient:
        kpis = _empty_kpis("forward_treatment_sample_insufficient")
    else:
        kpis = {
            "net_pnl": _metric(
                _number(control.get("net_pnl"), "branch09.control.net_pnl"),
                _number(treatment.get("net_pnl"), "branch09.treatment.net_pnl"),
                "USDT",
            ),
            "roi": _metric(
                None,
                None,
                "ratio",
                available=False,
                reason="capital_denominator_not_in_v3_outcome_store",
            ),
            "expectancy": _metric(
                _optional(control.get("expectancy"), "branch09.control.expectancy"),
                _optional(treatment.get("expectancy"), "branch09.treatment.expectancy"),
                "USDT_per_trade",
            ),
            "profit_factor": _metric(
                _optional(control.get("profit_factor"), "branch09.control.profit_factor"),
                _optional(treatment.get("profit_factor"), "branch09.treatment.profit_factor"),
                "ratio",
            ),
            "max_drawdown": _metric(
                _optional(control.get("max_drawdown"), "branch09.control.max_drawdown"),
                _optional(treatment.get("max_drawdown"), "branch09.treatment.max_drawdown"),
                "USDT",
            ),
            "net_pnl_per_capital_hour": _metric(
                None,
                None,
                "USDT_per_capital_hour",
                available=False,
                reason="capital_denominator_not_in_v3_outcome_store",
            ),
        }

    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in rows:
        groups[f"{item.get('symbol')}|{item.get('side')}"] .append(item)
    segments = [
        {
            "symbol_side": key,
            "resolved_trade_count": len(group),
            "selected_trade_count": sum(
                item.get("shadow_selected") is True for item in group
            ),
        }
        for key, group in sorted(groups.items())
    ]
    return _row(
        "ai_shadow_v3_forward",
        "branch09",
        "exact_v3_prospective_epoch",
        control_count,
        treatment_count,
        sufficient,
        kpis,
        "same_resolved_paper_net_pnl_rows_control_treatment",
        "no_extra_stress_without_observed_execution_telemetry",
        _bootstrap_shadow(rows),
        {
            "model": "qlib_v3_ai_shadow",
            "epoch": report.get("identity"),
            "regime": {"available": False, "reason": "pit_regime_not_in_v3_outcomes"},
            "symbol_side": {"available": bool(segments), "segments": segments},
            "capital_hour": {
                "available": False,
                "reason": "capital_denominator_not_in_v3_outcome_store",
            },
        },
    )


def _check_ledger(
    ledger: Mapping[str, Any],
    branch10: Mapping[str, Any],
    branch11: Mapping[str, Any],
    branch12: Mapping[str, Any],
) -> None:
    summary = _mapping(ledger.get("evidence_summary"), "ledger.evidence_summary")
    full = _mapping(branch10.get("full_model"), "branch10.full_model")
    expected_mi = _number(full.get("delta_net_pnl_vs_control"), "branch10.delta")
    actual_mi = _number(
        summary.get("market_intelligence_full_model_net_pnl_delta"),
        "ledger.market_delta",
    )
    if abs(expected_mi - actual_mi) > 1e-9:
        raise EconomicScorecardError("ledger_branch10_delta_mismatch")

    common = _mapping(branch11.get("branch08_common_window"), "branch11.common")
    expected_fee = _number(common.get("economic_fee_adjustment"), "branch11.common.fee")
    actual_fee = _number(
        summary.get("observable_execution_fee_adjustment_common_window_usdt"),
        "ledger.common_fee",
    )
    if abs(expected_fee - actual_fee) > 1e-9:
        raise EconomicScorecardError("ledger_branch11_fee_mismatch")

    combined = _mapping(branch12.get("combined_oos"), "branch12.combined")
    expected_uplift = _number(
        combined.get("net_pnl_per_capital_hour_uplift"),
        "branch12.uplift",
    )
    actual_uplift = _number(
        summary.get("allocator_net_pnl_per_capital_hour_uplift"),
        "ledger.allocator_uplift",
    )
    if abs(expected_uplift - actual_uplift) > 1e-12:
        raise EconomicScorecardError("ledger_branch12_uplift_mismatch")


def build_economic_control_treatment_scorecard_from_reports(
    *,
    branch09_report: Mapping[str, Any],
    branch10_report: Mapping[str, Any],
    branch11_report: Mapping[str, Any],
    branch12_report: Mapping[str, Any],
    ledger_report: Mapping[str, Any],
) -> dict[str, Any]:
    reports = {
        "branch09": branch09_report,
        "branch10": branch10_report,
        "branch11": branch11_report,
        "branch12": branch12_report,
        "ledger": ledger_report,
    }
    for name, report in reports.items():
        _validate(name, report)
    _check_ledger(ledger_report, branch10_report, branch11_report, branch12_report)

    scorecards = [
        _branch09(branch09_report),
        _branch10(branch10_report),
        _branch12(branch12_report),
    ]
    counts: dict[str, int] = defaultdict(int)
    for item in scorecards:
        counts[item["research_decision"]] += 1

    common = _mapping(branch11_report.get("branch08_common_window"), "branch11.common")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "reason": "canonical_control_treatment_scorecard_complete",
        "decision": "SCORECARD_READY_RESEARCH_ONLY",
        "canonical_kpis": list(CANONICAL_KPIS),
        "scorecard_count": len(scorecards),
        "scorecards": scorecards,
        "decision_counts": dict(sorted(counts.items())),
        "cost_stress_policy": {
            "same_cost_basis_required": True,
            "control_treatment_cost_basis_aligned": True,
            "observable_execution_fee_adjustment_common_window_usdt": _number(
                common.get("economic_fee_adjustment"),
                "branch11.common.economic_fee_adjustment",
            ),
            "unobservable_actual_execution_components": branch11_report.get(
                "unobservable_actual_execution_components"
            ),
            "hypothetical_execution_costs_invented": False,
            "additional_common_stress_applied": False,
            "additional_stress_reason": (
                "no_common_observed_spread_slippage_latency_impact_path"
            ),
        },
        "source_of_financial_truth": {
            "ledger_schema": ledger_report.get("schema_version"),
            "ledger_evidence_sha256": ledger_report.get("ledger_evidence_sha256"),
            "new_financial_source_created": False,
        },
        "dimension_coverage": {
            "model": True,
            "regime": False,
            "regime_reason": "common_pit_regime_not_materialized_across_experiments",
            "symbol_side": any(
                item["dimensions"]["symbol_side"].get("available") is True
                for item in scorecards
            ),
            "capital_hour": any(
                item["kpis"]["net_pnl_per_capital_hour"]["available"] is True
                for item in scorecards
            ),
        },
        "research_decisions_allowed": [
            "COLLECT/INSUFFICIENT",
            "DISCARD",
            "RECALIBRATE",
            "CANDIDATE",
        ],
        **SAFETY_FLAGS,
        "safety_flags": dict(SAFETY_FLAGS),
    }
    payload["scorecard_evidence_sha256"] = _hash(payload)
    json.dumps(payload, sort_keys=True, allow_nan=False, default=str)
    return payload


def build_economic_control_treatment_scorecard_v1(
    *,
    project_root: str | Path,
    master_path: str | Path,
    dataset_path: str | Path,
    feature_contract_path: str | Path,
    dataset_manifest_path: str | Path,
    split_manifest_path: str | Path,
    shadow_evidence_path: str | Path | None = None,
) -> dict[str, Any]:
    try:
        from smartcrypto.research.aibot_parity.ai_shadow_economic_challenger import (
            build_ai_shadow_economic_challenger_sync_v1,
        )
        from smartcrypto.research.aibot_parity.component_economic_attribution_ledger import (
            build_component_economic_attribution_ledger_from_reports,
        )
        from smartcrypto.research.aibot_parity.execution_intelligence_net_pnl_attribution import (
            build_execution_intelligence_net_pnl_attribution_v1,
        )
        from smartcrypto.research.aibot_parity.market_intelligence_pnl_ablation import (
            build_market_intelligence_pnl_ablation_v1,
        )
        from smartcrypto.research.aibot_parity.opportunity_allocator_capital_hour_uplift import (
            build_opportunity_allocator_capital_hour_uplift_v1,
        )

        branch09 = build_ai_shadow_economic_challenger_sync_v1(
            project_root=project_root,
            evidence_path=shadow_evidence_path,
        )
        branch10 = build_market_intelligence_pnl_ablation_v1(
            project_root=project_root,
            dataset_path=dataset_path,
            feature_contract_path=feature_contract_path,
            dataset_manifest_path=dataset_manifest_path,
            split_manifest_path=split_manifest_path,
        )
        branch11 = build_execution_intelligence_net_pnl_attribution_v1(
            master_path=master_path,
        )
        branch12 = build_opportunity_allocator_capital_hour_uplift_v1(
            master_path=master_path,
        )
        ledger = build_component_economic_attribution_ledger_from_reports(
            branch10_report=branch10,
            branch11_report=branch11,
            branch12_report=branch12,
        )
        return build_economic_control_treatment_scorecard_from_reports(
            branch09_report=branch09,
            branch10_report=branch10,
            branch11_report=branch11,
            branch12_report=branch12,
            ledger_report=ledger,
        )
    except (EconomicScorecardError, OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        reason = str(exc) if isinstance(exc, EconomicScorecardError) else (
            f"economic_scorecard_failed:{type(exc).__name__}"
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "reason": reason,
            "decision": "SCORECARD_BLOCKED",
            **SAFETY_FLAGS,
            "safety_flags": dict(SAFETY_FLAGS),
        }


__all__ = [
    "BOOTSTRAP_RESAMPLES",
    "CANONICAL_KPIS",
    "EconomicScorecardError",
    "MIN_TREATMENT_TRADES",
    "SCHEMA_VERSION",
    "build_economic_control_treatment_scorecard_from_reports",
    "build_economic_control_treatment_scorecard_v1",
]
