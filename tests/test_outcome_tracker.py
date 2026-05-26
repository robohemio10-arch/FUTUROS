from __future__ import annotations

from smartcrypto.ml.outcome_tracker import OutcomeTracker


def test_outcome_tracker_associates_outcome_to_decision_id(tmp_path) -> None:
    tracker = OutcomeTracker(tmp_path / "outcomes.json")

    outcome = tracker.record_outcome(
        decision_id="decision-1",
        trade_id="trade-1",
        target_win=True,
        return_pct=0.03,
        pnl=12.5,
    )

    assert outcome.decision_id == "decision-1"
    assert outcome.trade_id == "trade-1"
    assert tracker.list_outcomes()[0].pnl == 12.5


def test_outcome_tracker_calculates_simple_metrics(tmp_path) -> None:
    tracker = OutcomeTracker(tmp_path / "outcomes.json")
    tracker.record_outcome(decision_id="decision-1", target_win=True, return_pct=0.04)
    tracker.record_outcome(decision_id="decision-2", target_win=False, return_pct=-0.02)

    metrics = tracker.metrics()

    assert metrics["total_decisions"] == 2
    assert metrics["resolved_decisions"] == 2
    assert metrics["win_rate"] == 0.5
    assert metrics["average_return_pct"] == 0.01
