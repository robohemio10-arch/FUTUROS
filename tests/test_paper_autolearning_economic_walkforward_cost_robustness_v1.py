from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from smartcrypto.learning.paper_autolearning.economic_walkforward_cost_robustness import (
    build_paper_autolearning_economic_walkforward_cost_robustness_v1,
)


def make_rows(
    *,
    weak_folds: set[int] | None = None,
    missing_leverage_every: int | None = None,
) -> list[dict[str, object]]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    weak_folds = weak_folds or set()
    evaluation_start = 144
    fold_size = 72

    for index in range(360):
        is_evaluation = index >= evaluation_start
        evaluation_offset = index - evaluation_start
        fold_number = evaluation_offset // fold_size + 1 if is_evaluation else 0
        fold_offset = evaluation_offset % fold_size if is_evaluation else 0
        selected = is_evaluation and fold_offset < 50

        if selected:
            net_pnl = -0.5 if fold_offset % 10 == 0 else 1.0
        elif is_evaluation:
            net_pnl = 1.0 if fold_number in weak_folds else -1.0
        else:
            net_pnl = 0.1 if index % 2 == 0 else -0.1

        leverage: float | None = 10.0
        if missing_leverage_every and index % missing_leverage_every == 0:
            leverage = None

        close_time = start + timedelta(hours=index)
        rows.append(
            {
                "is_closed": True,
                "open_time_utc": (close_time - timedelta(hours=1)).isoformat(),
                "close_time_utc": close_time.isoformat(),
                "duration_seconds": 3600,
                "net_pnl": net_pnl,
                "notional": 1000.0,
                "leverage": leverage,
                "trading_fee": 0.4,
                "funding_fee": 0.0,
                "paper_candidate_filter_decision": (
                    "allowed" if selected else "blocked"
                ),
            }
        )

    return rows


def test_walkforward_cost_robust_edge_passes(tmp_path: Path) -> None:
    report = build_paper_autolearning_economic_walkforward_cost_robustness_v1(
        project_root=tmp_path,
        rows=make_rows(),
    )

    assert report["status"] == "ok"
    assert report["decision"] == "WALKFORWARD_COST_ROBUST_RESEARCH_ONLY"
    assert report["fold_count"] == 3
    assert report["positive_delta_net_pnl_fold_count"] == 3
    assert report["positive_expectancy_uplift_fold_count"] == 3
    assert report["aggregate_delta_stressed_net_pnl"] > 0
    assert report["aggregate_delta_stressed_expectancy"] > 0
    assert report["aggregate_delta_stressed_net_pnl_per_capital_hour"] > 0
    assert report["aggregate_treatment_metrics"]["stressed_net_pnl_total"] > 0
    assert report["fees_or_funding_double_counted"] is False
    assert report["blockers"] == []


def test_walkforward_requires_repeatable_fold_uplift(tmp_path: Path) -> None:
    report = build_paper_autolearning_economic_walkforward_cost_robustness_v1(
        project_root=tmp_path,
        rows=make_rows(weak_folds={2, 3}),
    )

    assert report["status"] == "blocked"
    assert report["positive_delta_net_pnl_fold_count"] == 1
    assert "positive_delta_net_pnl_fold_count_not_met" in report["blockers"]
    assert "positive_expectancy_uplift_fold_count_not_met" in report["blockers"]


def test_walkforward_blocks_when_execution_stress_destroys_edge(tmp_path: Path) -> None:
    report = build_paper_autolearning_economic_walkforward_cost_robustness_v1(
        project_root=tmp_path,
        rows=make_rows(),
        additional_execution_stress_bps=20.0,
    )

    assert report["status"] == "blocked"
    assert "aggregate_positive_treatment_net_pnl_not_met" in report["blockers"]
    assert report["aggregate_treatment_metrics"]["stressed_net_pnl_total"] < 0


def test_walkforward_requires_capital_hour_coverage(tmp_path: Path) -> None:
    report = build_paper_autolearning_economic_walkforward_cost_robustness_v1(
        project_root=tmp_path,
        rows=make_rows(missing_leverage_every=2),
    )

    assert report["status"] == "blocked"
    assert any("capital_hour_coverage_not_met" in item for item in report["blockers"])


def test_walkforward_fails_closed_on_invalid_time_and_preserves_safety(
    tmp_path: Path,
) -> None:
    rows = make_rows()
    rows[10]["close_time_utc"] = "not-a-time"

    report = build_paper_autolearning_economic_walkforward_cost_robustness_v1(
        project_root=tmp_path,
        rows=rows,
    )

    assert report["status"] == "blocked"
    assert "unparseable_close_time_detected" in report["blockers"]
    assert report["anti_leakage"] is True
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["research_only"] is True
    assert report["operational_authority"] is False
    assert report["promotion_allowed"] is False
    assert report["changes_risk"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["writes_runtime"] is False
