from __future__ import annotations

from copy import deepcopy

import pytest

from smartcrypto.research.portfolio_of_alphas.oos_economic_selection import (
    EVIDENCE_SCHEMA_VERSION,
    PortfolioAlphaSelectionError,
    build_portfolio_of_alphas_oos_economic_selection_from_scorecard,
)


def _scorecard(*, candidate_ids: set[str] | None = None) -> dict[str, object]:
    candidates = candidate_ids or set()
    rows = []
    for experiment_id in ("trend-exp", "breakout-exp", "counter-exp"):
        decision = "CANDIDATE" if experiment_id in candidates else "RECALIBRATE"
        rows.append(
            {
                "experiment_id": experiment_id,
                "research_decision": decision,
                "decision_blockers": [] if decision == "CANDIDATE" else ["test"],
                "operational_authority": False,
                "promotion_allowed": False,
            }
        )
    return {
        "schema_version": "economic_control_treatment_scorecard_v1",
        "status": "ok",
        "decision": "SCORECARD_READY_RESEARCH_ONLY",
        "scorecard_evidence_sha256": "a" * 64,
        "scorecards": rows,
        "source_of_financial_truth": {
            "new_financial_source_created": False,
        },
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "promotion_allowed": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "writes_runtime": False,
        "writes_data": False,
    }


def _sleeve(
    sleeve_id: str,
    source: str,
    values: list[float],
    *,
    strategy_id: str | None = None,
) -> dict[str, object]:
    return {
        "sleeve_id": sleeve_id,
        "hypothesis": f"{sleeve_id} economic hypothesis",
        "source_experiment_id": source,
        "strategy_ids": [strategy_id or f"{sleeve_id}-v1"],
        "capacity_usdt": 1000.0,
        "observations": [
            {
                "oos_key": f"fold-{index + 1}",
                "net_pnl": value,
                "capital_hours": 100.0,
            }
            for index, value in enumerate(values)
        ],
    }


def _evidence(*sleeves: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "sleeves": list(sleeves),
    }


def test_missing_oos_sleeve_evidence_waits_without_inventing_sleeves() -> None:
    report = build_portfolio_of_alphas_oos_economic_selection_from_scorecard(
        scorecard_report=_scorecard(candidate_ids={"trend-exp"}),
        sleeve_evidence=None,
    )

    assert report["status"] == "waiting"
    assert report["selected_sleeve_count"] == 0
    assert report["decision"] == "WAITING_FOR_EXISTING_SLEEVE_OOS_EVIDENCE"
    assert len(report["sleeve_inventory"]) == 7
    assert report["capital_increase_allowed"] is False


def test_non_candidate_scorecard_source_excludes_profitable_sleeve() -> None:
    evidence = _evidence(
        _sleeve("trend", "trend-exp", [4.0, -1.0, 5.0, 2.0]),
    )
    report = build_portfolio_of_alphas_oos_economic_selection_from_scorecard(
        scorecard_report=_scorecard(candidate_ids=set()),
        sleeve_evidence=evidence,
    )

    assert report["status"] == "ok"
    assert report["selected_sleeve_count"] == 0
    row = next(item for item in report["sleeve_inventory"] if item["sleeve_id"] == "trend")
    assert "source_scorecard_not_candidate:RECALIBRATE" in row["exclusion_reasons"]


def test_low_correlation_positive_sleeves_are_selected_with_positive_marginal_pnl() -> None:
    evidence = _evidence(
        _sleeve("trend", "trend-exp", [5.0, -1.0, 6.0, 2.0, 4.0]),
        _sleeve("breakout", "breakout-exp", [1.0, 2.0, -8.0, 4.0, 3.0]),
    )
    report = build_portfolio_of_alphas_oos_economic_selection_from_scorecard(
        scorecard_report=_scorecard(candidate_ids={"trend-exp", "breakout-exp"}),
        sleeve_evidence=evidence,
    )

    assert report["selected_sleeve_count"] == 2
    assert set(report["selected_sleeves"]) == {"trend", "breakout"}
    assert report["portfolio_metrics"]["net_pnl"] > 0.0
    assert report["portfolio_metrics"]["profit_factor"] > 1.0
    selected_marginal = [item for item in report["marginal_contributions"] if item["selected"]]
    assert all(item["marginal_net_pnl"] > 0.0 for item in selected_marginal)
    assert report["scaleout_allowed"] is False


def test_highly_correlated_redundant_sleeve_is_eliminated() -> None:
    evidence = _evidence(
        _sleeve("trend", "trend-exp", [5.0, -1.0, 6.0, 2.0, 4.0]),
        _sleeve("breakout", "breakout-exp", [10.0, -2.0, 12.0, 4.0, 8.0]),
    )
    report = build_portfolio_of_alphas_oos_economic_selection_from_scorecard(
        scorecard_report=_scorecard(candidate_ids={"trend-exp", "breakout-exp"}),
        sleeve_evidence=evidence,
    )

    assert report["selected_sleeve_count"] == 1
    excluded = [
        item
        for item in report["sleeve_inventory"]
        if item.get("evidence_status") == "excluded"
        and item["sleeve_id"] in {"trend", "breakout"}
    ]
    assert len(excluded) == 1
    assert any(
        reason.startswith("redundant_correlation:")
        for reason in excluded[0]["exclusion_reasons"]
    )


def test_negative_edge_sleeve_is_explicitly_excluded() -> None:
    evidence = _evidence(
        _sleeve("counter", "counter-exp", [-3.0, 1.0, -2.0, -1.0]),
    )
    report = build_portfolio_of_alphas_oos_economic_selection_from_scorecard(
        scorecard_report=_scorecard(candidate_ids={"counter-exp"}),
        sleeve_evidence=evidence,
    )

    assert report["selected_sleeve_count"] == 0
    row = next(item for item in report["sleeve_inventory"] if item["sleeve_id"] == "counter")
    assert "standalone_net_pnl_not_positive" in row["exclusion_reasons"]


def test_strategy_membership_cannot_be_duplicated_across_sleeves() -> None:
    evidence = _evidence(
        _sleeve("trend", "trend-exp", [5.0, -1.0, 6.0], strategy_id="shared-v1"),
        _sleeve("breakout", "breakout-exp", [1.0, 4.0, -1.0], strategy_id="shared-v1"),
    )

    with pytest.raises(
        PortfolioAlphaSelectionError,
        match="strategy_assigned_to_multiple_sleeves:shared-v1",
    ):
        build_portfolio_of_alphas_oos_economic_selection_from_scorecard(
            scorecard_report=_scorecard(candidate_ids={"trend-exp", "breakout-exp"}),
            sleeve_evidence=evidence,
        )


def test_unsafe_scorecard_fails_closed() -> None:
    scorecard = deepcopy(_scorecard(candidate_ids={"trend-exp"}))
    scorecard["promotion_allowed"] = True

    with pytest.raises(
        PortfolioAlphaSelectionError,
        match="scorecard_safety_false_mismatch:promotion_allowed",
    ):
        build_portfolio_of_alphas_oos_economic_selection_from_scorecard(
            scorecard_report=scorecard,
            sleeve_evidence=None,
        )
