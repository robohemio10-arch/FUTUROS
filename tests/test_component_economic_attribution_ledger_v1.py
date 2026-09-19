from __future__ import annotations

from copy import deepcopy

import pytest

from smartcrypto.research.aibot_parity.component_economic_attribution_ledger import (
    ComponentAttributionLedgerError,
    build_component_economic_attribution_ledger_from_reports,
)

MASTER_SHA = "a" * 64


def _safety() -> dict[str, object]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "writes_runtime": False,
    }


def _branch10() -> dict[str, object]:
    return {
        "schema_version": "market_intelligence_pnl_ablation_v1",
        "status": "ok",
        **_safety(),
        "source": {
            "dataset_sha256": "b" * 64,
            "dataset_hash": "c" * 64,
            "feature_contract_hash": "d" * 64,
            "split_manifest_hash": "e" * 64,
            "row_count": 100,
            "split_count": 3,
            "embargo_seconds": 86400,
        },
        "full_model": {
            "control": {
                "net_pnl": 100.0,
                "expectancy": 1.0,
                "profit_factor": 1.5,
            },
            "treatment": {
                "net_pnl": 98.0,
                "expectancy": 1.2,
                "profit_factor": 1.6,
            },
            "coverage": 0.8,
            "delta_net_pnl_vs_control": -2.0,
        },
        "feature_block_contributions": [
            {
                "feature_block": "structural_context",
                "net_pnl_contribution": -3.0,
                "expectancy_contribution": -0.1,
                "classification": "negative",
            },
            {
                "feature_block": "momentum_trend",
                "net_pnl_contribution": 4.0,
                "expectancy_contribution": 0.1,
                "classification": "positive",
            },
            {
                "feature_block": "volatility_range",
                "net_pnl_contribution": -5.0,
                "expectancy_contribution": -0.2,
                "classification": "negative",
            },
            {
                "feature_block": "volume_activity",
                "net_pnl_contribution": -1.0,
                "expectancy_contribution": 0.0,
                "classification": "negative",
            },
        ],
    }


def _branch11() -> dict[str, object]:
    return {
        "schema_version": "execution_intelligence_net_pnl_attribution_v1",
        "status": "ok",
        **_safety(),
        "source": {"master_sha256": MASTER_SHA},
        "frozen_branch08_baseline_gate": {"status": "PASS"},
        "branch08_common_window": {
            "trade_count": 100,
            "reported_pnl": 50.0,
            "economic_fee_adjustment": 0.0,
            "economic_net_pnl": 50.0,
            "observable_fee_impact_usdt": 0.0,
        },
        "full_master": {
            "trade_count": 120,
            "reported_pnl": 60.0,
            "economic_fee_adjustment": -10.0,
            "economic_net_pnl": 50.0,
            "observable_fee_impact_usdt": 10.0,
        },
    }


def _branch12() -> dict[str, object]:
    return {
        "schema_version": "opportunity_allocator_capital_hour_uplift_v1",
        "status": "ok",
        **_safety(),
        "source": {"master_sha256": MASTER_SHA},
        "selection_uses_current_trade_realized_duration": False,
        "selection_uses_current_trade_outcome_fields": False,
        "selection_uses_test_period_metrics": False,
        "combined_oos": {
            "control": {
                "net_pnl": 200.0,
                "net_pnl_per_capital_hour": 0.005,
                "profit_factor": 1.5,
                "max_drawdown": 20.0,
            },
            "treatment": {
                "net_pnl": 120.0,
                "net_pnl_per_capital_hour": 0.006,
                "profit_factor": 1.7,
                "max_drawdown": 25.0,
            },
            "net_pnl_per_capital_hour_uplift": 0.001,
            "positive_uplift_fold_count": 3,
            "evaluated_uplift_fold_count": 6,
            "treatment_trade_coverage_rate": 0.5,
        },
    }


def test_builds_non_additive_ledger_with_preserved_estimands() -> None:
    report = build_component_economic_attribution_ledger_from_reports(
        branch10_report=_branch10(),
        branch11_report=_branch11(),
        branch12_report=_branch12(),
    )

    assert report["status"] == "ok"
    assert report["aggregation_policy"]["additive_total_allowed"] is False
    assert report["aggregation_policy"]["causal_reinterpretation_allowed"] is False
    assert report["component_count"] == 8
    assert (
        report["evidence_summary"][
            "market_intelligence_positive_conditional_blocks"
        ]
        == ["momentum_trend"]
    )
    assert (
        report["evidence_summary"][
            "observable_execution_fee_adjustment_common_window_usdt"
        ]
        == 0.0
    )
    assert report["evidence_summary"]["allocator_positive_uplift_fold_rate"] == 0.5
    assert report["operational_authority"] is False
    assert report["writes_data"] is False


def test_allocator_absolute_pnl_tradeoff_is_preserved() -> None:
    report = build_component_economic_attribution_ledger_from_reports(
        branch10_report=_branch10(),
        branch11_report=_branch11(),
        branch12_report=_branch12(),
    )
    allocator = next(
        row
        for row in report["components"]
        if row["component_id"] == "opportunity_allocator_capital_hour"
    )

    assert allocator["delta_value"] == pytest.approx(0.001)
    assert allocator["relative_uplift"] == pytest.approx(0.2)
    assert allocator["absolute_net_pnl_delta"] == pytest.approx(-80.0)
    assert allocator["additive_to_other_components"] is False


def test_upstream_safety_violation_fails_closed() -> None:
    unsafe = deepcopy(_branch12())
    unsafe["sends_orders"] = True

    with pytest.raises(
        ComponentAttributionLedgerError,
        match="branch12_safety_false_mismatch:sends_orders",
    ):
        build_component_economic_attribution_ledger_from_reports(
            branch10_report=_branch10(),
            branch11_report=_branch11(),
            branch12_report=unsafe,
        )


def test_master_lineage_divergence_fails_closed() -> None:
    divergent = deepcopy(_branch12())
    divergent["source"]["master_sha256"] = "c" * 64

    with pytest.raises(
        ComponentAttributionLedgerError,
        match="branch11_branch12_master_sha_mismatch",
    ):
        build_component_economic_attribution_ledger_from_reports(
            branch10_report=_branch10(),
            branch11_report=_branch11(),
            branch12_report=divergent,
        )


def test_branch10_feature_classification_drift_fails_closed() -> None:
    drift = deepcopy(_branch10())
    drift["feature_block_contributions"][1]["classification"] = "negative"

    with pytest.raises(
        ComponentAttributionLedgerError,
        match="branch10_feature_classification_mismatch:momentum_trend",
    ):
        build_component_economic_attribution_ledger_from_reports(
            branch10_report=drift,
            branch11_report=_branch11(),
            branch12_report=_branch12(),
        )
