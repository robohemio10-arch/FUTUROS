"""Research-only economic attribution ledger for Branches 10-12.

The ledger preserves each upstream estimand and population. It never sums
heterogeneous contributions into a synthetic total and never upgrades
conditional ablation evidence into a causal claim.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "component_economic_attribution_ledger_v1"

EXPECTED_SCHEMAS = {
    "branch10": "market_intelligence_pnl_ablation_v1",
    "branch11": "execution_intelligence_net_pnl_attribution_v1",
    "branch12": "opportunity_allocator_capital_hour_uplift_v1",
}

EXPECTED_FEATURE_BLOCKS = (
    "structural_context",
    "momentum_trend",
    "volatility_range",
    "volume_activity",
)

REQUIRED_FALSE_SAFETY = (
    "operational_authority",
    "sends_orders",
    "exchange_private_access",
    "changes_risk",
    "writes_runtime",
)

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
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


class ComponentAttributionLedgerError(RuntimeError):
    """Fail-closed validation error for the component ledger."""


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ComponentAttributionLedgerError(f"invalid_numeric_field:{field}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ComponentAttributionLedgerError(
            f"invalid_numeric_field:{field}"
        ) from exc
    if not math.isfinite(number):
        raise ComponentAttributionLedgerError(f"non_finite_field:{field}")
    return number


def _optional_number(value: Any, field: str) -> float | None:
    if value is None:
        return None
    return _finite_number(value, field)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ComponentAttributionLedgerError(f"mapping_required:{field}")
    return value


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_upstream(
    report: Mapping[str, Any],
    *,
    branch: str,
) -> None:
    expected_schema = EXPECTED_SCHEMAS[branch]
    if report.get("schema_version") != expected_schema:
        raise ComponentAttributionLedgerError(
            f"{branch}_schema_mismatch:"
            f"{report.get('schema_version')}!={expected_schema}"
        )
    if report.get("status") != "ok":
        raise ComponentAttributionLedgerError(
            f"{branch}_status_not_ok:{report.get('status')}"
        )
    for key in ("paper_only", "shadow_only", "research_only"):
        if report.get(key) is not True:
            raise ComponentAttributionLedgerError(
                f"{branch}_safety_true_mismatch:{key}"
            )
    for key in REQUIRED_FALSE_SAFETY:
        if report.get(key) is not False:
            raise ComponentAttributionLedgerError(
                f"{branch}_safety_false_mismatch:{key}"
            )


def _entry(
    *,
    component_id: str,
    branch: str,
    evidence_type: str,
    population_scope: str,
    metric_family: str,
    control_value: float | None,
    treatment_value: float | None,
    delta_value: float | None,
    unit: str,
    interpretation: str,
    conditional: bool,
    context_only: bool = False,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "component_id": component_id,
        "source_branch": branch,
        "evidence_type": evidence_type,
        "population_scope": population_scope,
        "metric_family": metric_family,
        "unit": unit,
        "control_value": control_value,
        "treatment_value": treatment_value,
        "delta_value": delta_value,
        "interpretation": interpretation,
        "conditional": conditional,
        "context_only": context_only,
        "causal_claim_allowed": False,
        "additive_to_other_components": False,
        "operational_authority": False,
        **dict(extra or {}),
    }


def _branch10_entries(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    full = _mapping(report.get("full_model"), "branch10.full_model")
    control = _mapping(full.get("control"), "branch10.full_model.control")
    treatment = _mapping(
        full.get("treatment"),
        "branch10.full_model.treatment",
    )

    control_net = _finite_number(
        control.get("net_pnl"),
        "branch10.control.net_pnl",
    )
    treatment_net = _finite_number(
        treatment.get("net_pnl"),
        "branch10.treatment.net_pnl",
    )
    reported_delta = _finite_number(
        full.get("delta_net_pnl_vs_control"),
        "branch10.delta_net_pnl_vs_control",
    )
    calculated_delta = treatment_net - control_net
    if abs(reported_delta - calculated_delta) > 1e-9:
        raise ComponentAttributionLedgerError(
            "branch10_full_model_delta_identity_mismatch"
        )

    entries = [
        _entry(
            component_id="market_intelligence_full_model",
            branch="branch10",
            evidence_type="predictive_selection_oos",
            population_scope="branch10_frozen_wq6_oos",
            metric_family="absolute_net_pnl",
            control_value=control_net,
            treatment_value=treatment_net,
            delta_value=reported_delta,
            unit="USDT",
            interpretation=(
                "full_model_absolute_net_pnl_improved"
                if reported_delta > 0.0
                else "full_model_absolute_net_pnl_not_improved"
            ),
            conditional=True,
            extra={
                "coverage": _finite_number(
                    full.get("coverage"),
                    "branch10.full_model.coverage",
                ),
                "control_expectancy": _optional_number(
                    control.get("expectancy"),
                    "branch10.control.expectancy",
                ),
                "treatment_expectancy": _optional_number(
                    treatment.get("expectancy"),
                    "branch10.treatment.expectancy",
                ),
                "control_profit_factor": _optional_number(
                    control.get("profit_factor"),
                    "branch10.control.profit_factor",
                ),
                "treatment_profit_factor": _optional_number(
                    treatment.get("profit_factor"),
                    "branch10.treatment.profit_factor",
                ),
            },
        )
    ]

    contributions = report.get("feature_block_contributions")
    if not isinstance(contributions, list):
        raise ComponentAttributionLedgerError(
            "branch10_feature_block_contributions_missing"
        )

    seen: set[str] = set()
    for raw in contributions:
        row = _mapping(raw, "branch10.feature_block_contribution")
        block = str(row.get("feature_block", "")).strip()
        if block not in EXPECTED_FEATURE_BLOCKS:
            raise ComponentAttributionLedgerError(
                f"branch10_unexpected_feature_block:{block}"
            )
        if block in seen:
            raise ComponentAttributionLedgerError(
                f"branch10_duplicate_feature_block:{block}"
            )
        seen.add(block)

        contribution = _finite_number(
            row.get("net_pnl_contribution"),
            f"branch10.{block}.net_pnl_contribution",
        )
        reported_class = str(row.get("classification", "")).strip()
        calculated_class = (
            "positive"
            if contribution > 1e-9
            else "negative"
            if contribution < -1e-9
            else "neutral"
        )
        if reported_class != calculated_class:
            raise ComponentAttributionLedgerError(
                f"branch10_feature_classification_mismatch:{block}"
            )

        entries.append(
            _entry(
                component_id=f"market_intelligence_block:{block}",
                branch="branch10",
                evidence_type="leave_one_block_out_oos",
                population_scope="branch10_frozen_wq6_oos",
                metric_family="conditional_net_pnl_contribution",
                control_value=None,
                treatment_value=None,
                delta_value=contribution,
                unit="USDT",
                interpretation=f"conditional_{calculated_class}_contribution",
                conditional=True,
                extra={
                    "feature_block": block,
                    "classification": calculated_class,
                    "expectancy_contribution": _optional_number(
                        row.get("expectancy_contribution"),
                        f"branch10.{block}.expectancy_contribution",
                    ),
                },
            )
        )

    if seen != set(EXPECTED_FEATURE_BLOCKS):
        raise ComponentAttributionLedgerError(
            "branch10_feature_block_set_incomplete"
        )

    return entries


def _branch11_entries(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    gate = _mapping(
        report.get("frozen_branch08_baseline_gate"),
        "branch11.frozen_branch08_baseline_gate",
    )
    if gate.get("status") != "PASS":
        raise ComponentAttributionLedgerError(
            "branch11_frozen_branch08_baseline_not_pass"
        )

    common = _mapping(
        report.get("branch08_common_window"),
        "branch11.branch08_common_window",
    )
    full = _mapping(report.get("full_master"), "branch11.full_master")

    common_reported = _finite_number(
        common.get("reported_pnl"),
        "branch11.common.reported_pnl",
    )
    common_net = _finite_number(
        common.get("economic_net_pnl"),
        "branch11.common.economic_net_pnl",
    )
    common_adjustment = _finite_number(
        common.get("economic_fee_adjustment"),
        "branch11.common.economic_fee_adjustment",
    )
    if abs((common_reported + common_adjustment) - common_net) > 1e-8:
        raise ComponentAttributionLedgerError(
            "branch11_common_economic_identity_mismatch"
        )

    full_reported = _finite_number(
        full.get("reported_pnl"),
        "branch11.full.reported_pnl",
    )
    full_net = _finite_number(
        full.get("economic_net_pnl"),
        "branch11.full.economic_net_pnl",
    )
    full_adjustment = _finite_number(
        full.get("economic_fee_adjustment"),
        "branch11.full.economic_fee_adjustment",
    )
    if abs((full_reported + full_adjustment) - full_net) > 1e-8:
        raise ComponentAttributionLedgerError(
            "branch11_full_economic_identity_mismatch"
        )

    return [
        _entry(
            component_id="execution_observable_fee_common_window",
            branch="branch11",
            evidence_type="observed_fee_adjustment",
            population_scope="branch08_common_window",
            metric_family="economic_fee_adjustment",
            control_value=common_reported,
            treatment_value=common_net,
            delta_value=common_adjustment,
            unit="USDT",
            interpretation=(
                "observable_fee_adjustment_zero"
                if abs(common_adjustment) <= 1e-12
                else "observable_fee_adjustment_nonzero"
            ),
            conditional=False,
            extra={
                "trade_count": int(
                    _finite_number(
                        common.get("trade_count"),
                        "branch11.common.trade_count",
                    )
                ),
                "observable_fee_impact_usdt": _finite_number(
                    common.get("observable_fee_impact_usdt"),
                    "branch11.common.observable_fee_impact_usdt",
                ),
            },
        ),
        _entry(
            component_id="execution_legacy_fee_full_master",
            branch="branch11",
            evidence_type="historical_context_observed_fee_adjustment",
            population_scope="full_master_including_legacy",
            metric_family="economic_fee_adjustment",
            control_value=full_reported,
            treatment_value=full_net,
            delta_value=full_adjustment,
            unit="USDT",
            interpretation="historical_fee_semantics_context_only",
            conditional=False,
            context_only=True,
            extra={
                "trade_count": int(
                    _finite_number(
                        full.get("trade_count"),
                        "branch11.full.trade_count",
                    )
                ),
                "observable_fee_impact_usdt": _finite_number(
                    full.get("observable_fee_impact_usdt"),
                    "branch11.full.observable_fee_impact_usdt",
                ),
            },
        ),
    ]


def _branch12_entries(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    combined = _mapping(report.get("combined_oos"), "branch12.combined_oos")
    control = _mapping(combined.get("control"), "branch12.control")
    treatment = _mapping(combined.get("treatment"), "branch12.treatment")

    control_efficiency = _finite_number(
        control.get("net_pnl_per_capital_hour"),
        "branch12.control.net_pnl_per_capital_hour",
    )
    treatment_efficiency = _finite_number(
        treatment.get("net_pnl_per_capital_hour"),
        "branch12.treatment.net_pnl_per_capital_hour",
    )
    uplift = _finite_number(
        combined.get("net_pnl_per_capital_hour_uplift"),
        "branch12.net_pnl_per_capital_hour_uplift",
    )
    if abs(uplift - (treatment_efficiency - control_efficiency)) > 1e-12:
        raise ComponentAttributionLedgerError(
            "branch12_capital_hour_uplift_identity_mismatch"
        )

    positive_folds = int(
        _finite_number(
            combined.get("positive_uplift_fold_count"),
            "branch12.positive_uplift_fold_count",
        )
    )
    evaluated_folds = int(
        _finite_number(
            combined.get("evaluated_uplift_fold_count"),
            "branch12.evaluated_uplift_fold_count",
        )
    )
    if evaluated_folds <= 0 or not 0 <= positive_folds <= evaluated_folds:
        raise ComponentAttributionLedgerError(
            "branch12_fold_stability_counts_invalid"
        )

    if report.get("selection_uses_current_trade_realized_duration") is not False:
        raise ComponentAttributionLedgerError(
            "branch12_current_trade_duration_selection_violation"
        )
    if report.get("selection_uses_current_trade_outcome_fields") is not False:
        raise ComponentAttributionLedgerError(
            "branch12_current_trade_outcome_selection_violation"
        )
    if report.get("selection_uses_test_period_metrics") is not False:
        raise ComponentAttributionLedgerError(
            "branch12_test_metric_selection_violation"
        )

    relative_uplift = (
        uplift / control_efficiency
        if control_efficiency != 0.0
        else None
    )
    control_net = _finite_number(
        control.get("net_pnl"),
        "branch12.control.net_pnl",
    )
    treatment_net = _finite_number(
        treatment.get("net_pnl"),
        "branch12.treatment.net_pnl",
    )

    return [
        _entry(
            component_id="opportunity_allocator_capital_hour",
            branch="branch12",
            evidence_type="purged_expanding_oos_allocator",
            population_scope="branch12_expanding_wq2_oos",
            metric_family="net_pnl_per_capital_hour",
            control_value=control_efficiency,
            treatment_value=treatment_efficiency,
            delta_value=uplift,
            unit="USDT_per_capital_hour",
            interpretation=(
                "aggregate_efficiency_uplift_observed"
                if uplift > 0.0
                else "aggregate_efficiency_uplift_not_observed"
            ),
            conditional=True,
            extra={
                "relative_uplift": relative_uplift,
                "positive_uplift_fold_count": positive_folds,
                "evaluated_uplift_fold_count": evaluated_folds,
                "positive_uplift_fold_rate": positive_folds / evaluated_folds,
                "control_net_pnl": control_net,
                "treatment_net_pnl": treatment_net,
                "absolute_net_pnl_delta": treatment_net - control_net,
                "treatment_trade_coverage_rate": _finite_number(
                    combined.get("treatment_trade_coverage_rate"),
                    "branch12.treatment_trade_coverage_rate",
                ),
                "control_profit_factor": _optional_number(
                    control.get("profit_factor"),
                    "branch12.control.profit_factor",
                ),
                "treatment_profit_factor": _optional_number(
                    treatment.get("profit_factor"),
                    "branch12.treatment.profit_factor",
                ),
                "control_max_drawdown": _optional_number(
                    control.get("max_drawdown"),
                    "branch12.control.max_drawdown",
                ),
                "treatment_max_drawdown": _optional_number(
                    treatment.get("max_drawdown"),
                    "branch12.treatment.max_drawdown",
                ),
            },
        )
    ]


def _branch10_lineage(report: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(report.get("source"), "branch10.source")
    keys = (
        "dataset_sha256",
        "dataset_hash",
        "feature_contract_hash",
        "split_manifest_hash",
        "row_count",
        "split_count",
        "embargo_seconds",
    )
    return {key: source.get(key) for key in keys}


def build_component_economic_attribution_ledger_from_reports(
    *,
    branch10_report: Mapping[str, Any],
    branch11_report: Mapping[str, Any],
    branch12_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and consolidate Branches 10-12 without synthetic summation."""

    reports = {
        "branch10": branch10_report,
        "branch11": branch11_report,
        "branch12": branch12_report,
    }
    for branch, report in reports.items():
        _validate_upstream(report, branch=branch)

    branch11_source = _mapping(branch11_report.get("source"), "branch11.source")
    branch12_source = _mapping(branch12_report.get("source"), "branch12.source")
    branch11_master = str(branch11_source.get("master_sha256", ""))
    branch12_master = str(branch12_source.get("master_sha256", ""))
    if not branch11_master or branch11_master != branch12_master:
        raise ComponentAttributionLedgerError(
            "branch11_branch12_master_sha_mismatch"
        )

    entries = [
        *_branch10_entries(branch10_report),
        *_branch11_entries(branch11_report),
        *_branch12_entries(branch12_report),
    ]

    branch10_full = next(
        item
        for item in entries
        if item["component_id"] == "market_intelligence_full_model"
    )
    branch11_common = next(
        item
        for item in entries
        if item["component_id"] == "execution_observable_fee_common_window"
    )
    branch12_allocator = next(
        item
        for item in entries
        if item["component_id"] == "opportunity_allocator_capital_hour"
    )

    positive_blocks = [
        item["feature_block"]
        for item in entries
        if item["component_id"].startswith("market_intelligence_block:")
        and item["classification"] == "positive"
    ]

    ledger_core = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "reason": "component_economic_attribution_ledger_complete",
        "decision": "COMPONENT_LEDGER_READY_RESEARCH_ONLY",
        "aggregation_policy": {
            "additive_total_allowed": False,
            "cross_component_ranking_allowed": False,
            "causal_reinterpretation_allowed": False,
            "reason": "branches_use_different_estimands_populations_and_methods",
        },
        "source_lineage": {
            "branch10_schema": branch10_report["schema_version"],
            "branch11_schema": branch11_report["schema_version"],
            "branch12_schema": branch12_report["schema_version"],
            "branch10": _branch10_lineage(branch10_report),
            "branch11_branch12_master_sha256": branch11_master,
        },
        "component_count": len(entries),
        "components": entries,
        "evidence_summary": {
            "market_intelligence_full_model_net_pnl_delta": (
                branch10_full["delta_value"]
            ),
            "market_intelligence_positive_conditional_blocks": positive_blocks,
            "observable_execution_fee_adjustment_common_window_usdt": (
                branch11_common["delta_value"]
            ),
            "allocator_net_pnl_per_capital_hour_uplift": (
                branch12_allocator["delta_value"]
            ),
            "allocator_relative_capital_hour_uplift": (
                branch12_allocator["relative_uplift"]
            ),
            "allocator_positive_uplift_fold_rate": (
                branch12_allocator["positive_uplift_fold_rate"]
            ),
            "operational_action": "NONE",
        },
        **SAFETY_FLAGS,
        "safety_flags": dict(SAFETY_FLAGS),
    }
    ledger_core["ledger_evidence_sha256"] = _canonical_hash(ledger_core)
    json.dumps(ledger_core, sort_keys=True, allow_nan=False, default=str)
    return ledger_core


def build_component_economic_attribution_ledger_v1(
    *,
    project_root: str | Path,
    master_path: str | Path,
    dataset_path: str | Path,
    feature_contract_path: str | Path,
    dataset_manifest_path: str | Path,
    split_manifest_path: str | Path,
) -> dict[str, Any]:
    """Execute the merged upstream builders and consolidate their evidence."""

    try:
        from smartcrypto.research.aibot_parity.execution_intelligence_net_pnl_attribution import (
            build_execution_intelligence_net_pnl_attribution_v1,
        )
        from smartcrypto.research.aibot_parity.market_intelligence_pnl_ablation import (
            build_market_intelligence_pnl_ablation_v1,
        )
        from smartcrypto.research.aibot_parity.opportunity_allocator_capital_hour_uplift import (
            build_opportunity_allocator_capital_hour_uplift_v1,
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

        return build_component_economic_attribution_ledger_from_reports(
            branch10_report=branch10,
            branch11_report=branch11,
            branch12_report=branch12,
        )
    except (
        ComponentAttributionLedgerError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        ImportError,
    ) as exc:
        reason = (
            str(exc)
            if isinstance(exc, ComponentAttributionLedgerError)
            else f"component_ledger_failed:{type(exc).__name__}"
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "reason": reason,
            "decision": "COMPONENT_LEDGER_BLOCKED",
            **SAFETY_FLAGS,
            "safety_flags": dict(SAFETY_FLAGS),
        }


__all__ = [
    "ComponentAttributionLedgerError",
    "SCHEMA_VERSION",
    "build_component_economic_attribution_ledger_from_reports",
    "build_component_economic_attribution_ledger_v1",
]
