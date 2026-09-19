from __future__ import annotations

from copy import deepcopy

import pytest

from smartcrypto.research.aibot_parity.economic_control_treatment_scorecard import (
    CANONICAL_KPIS,
    EconomicScorecardError,
    build_economic_control_treatment_scorecard_from_reports,
)


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


def _branch09() -> dict[str, object]:
    return {
        "schema_version": "ai_shadow_economic_challenger_sync_v1",
        "status": "waiting",
        **_safety(),
        "identity": {"epoch_id": "qlib-v3-test"},
        "sample_sufficient_for_diagnostic_comparison": False,
        "resolved_rows": [],
        "global_comparison": {
            "control": {
                "trade_count": 0,
                "net_pnl": 0.0,
                "expectancy": None,
                "profit_factor": None,
                "max_drawdown": None,
            },
            "treatment": {
                "trade_count": 0,
                "net_pnl": 0.0,
                "expectancy": None,
                "profit_factor": None,
                "max_drawdown": None,
            },
            "selected_trade_count": 0,
        },
    }


def _branch10() -> dict[str, object]:
    return {
        "schema_version": "market_intelligence_pnl_ablation_v1",
        "status": "ok",
        **_safety(),
        "full_model": {
            "control": {
                "trade_count": 100,
                "net_pnl": 100.0,
                "expectancy": 1.0,
                "profit_factor": 1.5,
                "max_drawdown": 10.0,
            },
            "treatment": {
                "trade_count": 80,
                "net_pnl": 98.0,
                "expectancy": 1.225,
                "profit_factor": 1.7,
                "max_drawdown": 9.0,
            },
            "delta_net_pnl_vs_control": -2.0,
        },
    }


def _branch11() -> dict[str, object]:
    return {
        "schema_version": "execution_intelligence_net_pnl_attribution_v1",
        "status": "ok",
        **_safety(),
        "branch08_common_window": {"economic_fee_adjustment": 0.0},
        "unobservable_actual_execution_components": {
            "spread": "unavailable",
            "slippage": "unavailable",
        },
    }


def _branch12() -> dict[str, object]:
    return {
        "schema_version": "opportunity_allocator_capital_hour_uplift_v1",
        "status": "ok",
        **_safety(),
        "combined_oos": {
            "control": {
                "trade_count": 200,
                "net_pnl": 200.0,
                "expectancy": 1.0,
                "profit_factor": 1.5,
                "max_drawdown": 20.0,
                "net_pnl_per_capital_hour": 0.005,
            },
            "treatment": {
                "trade_count": 100,
                "net_pnl": 120.0,
                "expectancy": 1.2,
                "profit_factor": 1.7,
                "max_drawdown": 25.0,
                "net_pnl_per_capital_hour": 0.006,
            },
            "net_pnl_per_capital_hour_uplift": 0.001,
        },
        "folds": [
            {
                "test_period": "2026-07",
                "selected_opportunity_keys": ["ETHUSDT|long", "ETHUSDT|short"],
            }
        ],
    }


def _ledger() -> dict[str, object]:
    return {
        "schema_version": "component_economic_attribution_ledger_v1",
        "status": "ok",
        **_safety(),
        "ledger_evidence_sha256": "b" * 64,
        "evidence_summary": {
            "market_intelligence_full_model_net_pnl_delta": -2.0,
            "observable_execution_fee_adjustment_common_window_usdt": 0.0,
            "allocator_net_pnl_per_capital_hour_uplift": 0.001,
        },
    }


def _build() -> dict[str, object]:
    return build_economic_control_treatment_scorecard_from_reports(
        branch09_report=_branch09(),
        branch10_report=_branch10(),
        branch11_report=_branch11(),
        branch12_report=_branch12(),
        ledger_report=_ledger(),
    )


def test_six_kpi_slots_and_safety() -> None:
    report = _build()
    assert report["status"] == "ok"
    assert report["scorecard_count"] == 3
    assert report["canonical_kpis"] == list(CANONICAL_KPIS)
    for row in report["scorecards"]:
        assert tuple(row["kpis"].keys()) == CANONICAL_KPIS
        assert row["operational_authority"] is False
        assert row["promotion_allowed"] is False


def test_shadow_insufficient_stays_collect() -> None:
    report = _build()
    shadow = next(
        row for row in report["scorecards"]
        if row["experiment_id"] == "ai_shadow_v3_forward"
    )
    assert shadow["research_decision"] == "COLLECT/INSUFFICIENT"
    assert all(metric["available"] is False for metric in shadow["kpis"].values())


def test_market_intelligence_no_metric_cherry_pick() -> None:
    report = _build()
    market = next(
        row for row in report["scorecards"]
        if row["experiment_id"] == "market_intelligence_oos"
    )
    assert market["kpis"]["net_pnl"]["delta"] == pytest.approx(-2.0)
    assert market["kpis"]["expectancy"]["delta"] > 0.0
    assert market["kpis"]["profit_factor"]["delta"] > 0.0
    assert market["research_decision"] == "RECALIBRATE"
    assert "net_pnl_uplift_not_positive" in market["decision_blockers"]


def test_allocator_efficiency_alone_not_candidate() -> None:
    report = _build()
    allocator = next(
        row for row in report["scorecards"]
        if row["experiment_id"] == "opportunity_allocator_oos"
    )
    assert allocator["kpis"]["net_pnl_per_capital_hour"]["delta"] == pytest.approx(0.001)
    assert allocator["kpis"]["net_pnl"]["delta"] == pytest.approx(-80.0)
    assert allocator["research_decision"] == "RECALIBRATE"
    assert "roi_unavailable" in allocator["decision_blockers"]
    assert "max_drawdown_ratio_exceeded" in allocator["decision_blockers"]


def test_ledger_divergence_fails_closed() -> None:
    ledger = deepcopy(_ledger())
    ledger["evidence_summary"]["allocator_net_pnl_per_capital_hour_uplift"] = 0.123
    with pytest.raises(EconomicScorecardError, match="ledger_branch12_uplift_mismatch"):
        build_economic_control_treatment_scorecard_from_reports(
            branch09_report=_branch09(),
            branch10_report=_branch10(),
            branch11_report=_branch11(),
            branch12_report=_branch12(),
            ledger_report=ledger,
        )


def test_no_new_financial_source() -> None:
    report = _build()
    assert report["source_of_financial_truth"]["new_financial_source_created"] is False
    assert report["cost_stress_policy"]["hypothetical_execution_costs_invented"] is False
    assert report["operational_authority"] is False
    assert report["promotion_allowed"] is False
    assert report["writes_data"] is False
