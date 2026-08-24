from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.research.paper_capacity_scaleout import (
    CapacityScaleoutConfig,
    evaluate_capacity_scenarios,
)
from smartcrypto.research.paper_capacity_scaleout.engine import (
    _read_outcome_rows,
)


def _config() -> CapacityScaleoutConfig:
    return CapacityScaleoutConfig(
        baseline_commit=(
            "2c2c5a2ea147d24207e6d6a5c1a1b4ee5bbc06ba"
        ),
        baseline_capacity=2,
        minimum_opportunity_coverage=0.50,
        minimum_marginal_outcomes=1,
        bootstrap_iterations=200,
        monte_carlo_iterations=200,
    )


def _closed() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"close_profit_abs": 1.0},
            {"close_profit_abs": -0.5},
        ]
    )


def _branch4() -> dict:
    return {
        "__source_status": "OK",
        "software_dod": {"status": "PASS"},
        "financial_evidence": {
            "status": "EVIDENCE_BLOCKED"
        },
        "decision": "MANTER_BASELINE",
    }


def _opportunity() -> dict:
    return {
        "__source_status": "OK",
        "opportunity_cost": {
            "candidate_ev_coverage_rate": 1.0,
            "ledger": [
                {
                    "candidate_id": "candidate-1",
                    "observed_at_utc": (
                        "2026-08-21T10:00:00Z"
                    ),
                    "candidate_symbol": "BTCUSDT",
                    "candidate_side": "LONG",
                    "candidate_regime": "TREND",
                    "candidate_actionable_shadow": True,
                    "capacity_blocked": True,
                    "missed_due_to_global_capacity": True,
                    "missed_due_to_pair_occupancy": False,
                    "candidate_ev": 1.0,
                    "capital_hours": 2.0,
                }
            ],
        },
    }


def test_branch5_remains_research_only() -> None:
    report, rows = evaluate_capacity_scenarios(
        closed_trades=_closed(),
        branch4_report=_branch4(),
        opportunity_report=_opportunity(),
        outcome_rows=[
            {
                "candidate_id": "candidate-1",
                "realized_net_pnl_usdt": 2.0,
                "outcome_available_at_utc": (
                    "2026-08-21T11:00:00Z"
                ),
            }
        ],
        config=_config(),
    )

    assert (
        report["simulation_mode"]
        == "RESEARCH_SIMULATION_ONLY"
    )
    assert report["capacity_activation_allowed"] is False
    assert report["changes_max_open_trades"] is False
    assert report["changes_risk"] is False
    assert report["changes_strategy"] is False
    assert report["sends_orders"] is False
    assert rows[0]["linkage_method"] == "EXACT_CANDIDATE_ID"


def test_c4_is_fail_closed() -> None:
    report, _ = evaluate_capacity_scenarios(
        closed_trades=_closed(),
        branch4_report=_branch4(),
        opportunity_report={
            "__source_status": "OK",
            "opportunity_cost": {
                "ledger": [],
                "candidate_ev_coverage_rate": 0.0,
            },
        },
        outcome_rows=[],
        config=_config(),
    )

    assert report["scenarios"]["C4"]["fail_closed"] is True
    assert report["scenarios"]["C4"]["recovered_count"] == 0


def test_pair_occupancy_is_not_recovered() -> None:
    opportunity = _opportunity()
    opportunity["opportunity_cost"]["ledger"][0][
        "missed_due_to_pair_occupancy"
    ] = True

    report, _ = evaluate_capacity_scenarios(
        closed_trades=_closed(),
        branch4_report=_branch4(),
        opportunity_report=opportunity,
        outcome_rows=[],
        config=_config(),
    )

    assert report["scenarios"]["C1"]["recovered_count"] == 0


def test_no_outcome_source_is_explicitly_insufficient(
    tmp_path: Path,
) -> None:
    rows, source = _read_outcome_rows(
        tmp_path,
        None,
    )

    assert rows == []
    assert source["status"] == "NOT_PROVIDED"


def test_branch4_assignment_row_is_not_an_outcome(
    tmp_path: Path,
) -> None:
    target = tmp_path / "assignments.jsonl"
    target.write_text(
        json.dumps(
            {
                "assignment_id": "assignment-1",
                "candidate_id": "candidate-1",
                "observed_at_utc": (
                    "2026-08-21T10:00:00Z"
                ),
                "arm": "TREATMENT",
                "status": "ASSIGNED",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    rows, source = _read_outcome_rows(
        tmp_path,
        target,
    )

    assert rows == []
    assert (
        source["status"]
        == "SOURCE_CONTRACT_MISMATCH"
    )
    assert source["valid_outcome_row_count"] == 0


def test_explicit_candidate_outcome_is_accepted(
    tmp_path: Path,
) -> None:
    target = tmp_path / "outcomes.jsonl"
    target.write_text(
        json.dumps(
            {
                "candidate_id": "candidate-1",
                "realized_net_pnl_usdt": 1.25,
                "outcome_available_at_utc": (
                    "2026-08-21T11:00:00Z"
                ),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    rows, source = _read_outcome_rows(
        tmp_path,
        target,
    )

    assert len(rows) == 1
    assert source["status"] == "OK"


def test_candidate_ev_is_not_used_as_realized_outcome() -> None:
    report, rows = evaluate_capacity_scenarios(
        closed_trades=_closed(),
        branch4_report=_branch4(),
        opportunity_report=_opportunity(),
        outcome_rows=[],
        config=_config(),
    )

    assert rows == []
    assert (
        report["scenarios"]["C1"][
            "financially_linked_count"
        ]
        == 0
    )
    assert (
        report["capacity_evidence"]["status"]
        == "INSUFFICIENT"
    )


def test_branch4_runtime_report_is_required() -> None:
    branch4 = {
        "__source_status": "SOURCE_MISSING",
    }

    report, _ = evaluate_capacity_scenarios(
        closed_trades=_closed(),
        branch4_report=branch4,
        opportunity_report=_opportunity(),
        outcome_rows=[],
        config=_config(),
    )

    assert report["status"] == "blocked"
    assert (
        "BRANCH4_RUNTIME_REPORT_SOFTWARE_DOD_NOT_VERIFIED"
        in report["capacity_evidence"]["blockers"]
    )


def test_output_linkage_never_uses_trade_id_as_candidate_id() -> None:
    report, rows = evaluate_capacity_scenarios(
        closed_trades=_closed(),
        branch4_report=_branch4(),
        opportunity_report=_opportunity(),
        outcome_rows=[
            {
                "candidate_id": "candidate-1",
                "trade_id": 123,
                "realized_net_pnl_usdt": 2.0,
                "outcome_available_at_utc": (
                    "2026-08-21T11:00:00Z"
                ),
            }
        ],
        config=_config(),
    )

    assert rows[0]["candidate_id"] == "candidate-1"
    assert rows[0]["trade_id"] == 123
    assert rows[0]["linkage_method"] == "EXACT_CANDIDATE_ID"
    assert report["historical_backfill"] is False
    assert report["fuzzy_matching"] is False
    assert report["timestamp_nearest_matching"] is False


def test_invalid_baseline_capacity_fails() -> None:
    with pytest.raises(
        ValueError,
        match="baseline_capacity_must_be_positive",
    ):
        CapacityScaleoutConfig(
            baseline_commit="abcdef1",
            baseline_capacity=0,
        )
