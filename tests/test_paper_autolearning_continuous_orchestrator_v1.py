from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.learning.paper_autolearning.continuous_orchestrator import (
    build_quarantine_microbatch,
    run_paper_autolearning_continuous_orchestrator_v1,
    run_paper_autolearning_unattended_service_v2,
)


class FakeLock:
    def __init__(self, path: Path, *, busy: bool = False) -> None:
        self.path = path
        self.busy = busy
        self.acquired = False
        self.released = False

    def acquire(self) -> None:
        if self.busy:
            raise RuntimeError("paper_autolearning_unattended_lock_busy")
        self.acquired = True

    def release(self) -> None:
        self.released = True


def _feedback_report(path: Path | None, *, new_outcomes: int = 2, rows: int = 2) -> dict[str, Any]:
    return {
        "status": "ok",
        "reason": "incremental_feedback_materialized",
        "new_outcome_event_count": new_outcomes,
        "duplicate_outcome_event_count": 0,
        "duplicate_or_reprocessed_row_count": 0,
        "microbatch_rows": rows,
        "microbatch_output_path": str(path) if path is not None else None,
        "close_to_feedback_latency_seconds_latest": 1.5 if new_outcomes else None,
        "close_to_feedback_latency_seconds_p50": 2.0 if new_outcomes else None,
        "close_to_feedback_latency_seconds_max": 3.0 if new_outcomes else None,
        "sends_orders": False,
        "changes_risk": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
    }


def _source_microbatch() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_id": ["e1", "e2"],
            "label_sign": [1, -1],
            "feature_entry_price": [100.0, 101.0],
            "feature_quantity": [1.0, 1.5],
            "feature_side_long": [1, 0],
            "feature_side_short": [0, 1],
            "feature_symbol_btcusdt": [1, 1],
        }
    )


def _touch_microbatch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test-microbatch-placeholder")


def test_dry_run_stops_before_quarantine(tmp_path: Path) -> None:
    calls = {"quarantine": 0}

    def live_feedback_runner(**_: Any) -> dict[str, Any]:
        return _feedback_report(None)

    def quarantine_runner(**_: Any) -> dict[str, Any]:
        calls["quarantine"] += 1
        return {}

    report = run_paper_autolearning_continuous_orchestrator_v1(
        project_root=tmp_path,
        write_feedback=False,
        train_challenger=True,
        live_feedback_runner=live_feedback_runner,
        quarantine_runner=quarantine_runner,
    )

    assert report["status"] == "ok"
    assert report["reason"] == "dry_run_new_training_evidence_detected"
    assert calls["quarantine"] == 0
    assert report["training_performed"] is False
    assert report["operational_authority"] is False
    assert report["sends_orders"] is False


def test_materialized_cycle_bridges_binary_target_and_evaluates(tmp_path: Path) -> None:
    microbatch_path = tmp_path / "data" / "feedback" / "training_microbatches" / "2026-09-05.parquet"
    _touch_microbatch(microbatch_path)
    captured: dict[str, Any] = {}

    def live_feedback_runner(**_: Any) -> dict[str, Any]:
        return _feedback_report(microbatch_path)

    def quarantine_runner(**kwargs: Any) -> dict[str, Any]:
        frame = kwargs["microbatch_frame"]
        captured["targets"] = frame["target_profitable"].tolist()
        captured["train"] = kwargs["train_challenger"]
        return {
            "status": "ok",
            "reason": "quarantine_cycle_executed",
            "quarantine_candidate_count": 2,
            "train_challenger_requested": True,
            "qlib_challenger_train_status": "trained_quarantine_only",
            "ai_shadow_challenger_train_status": "trained_quarantine_only",
            "training_prevented_by_watermark": False,
            "blockers": [],
            "sends_orders": False,
            "changes_risk": False,
            "runtime_updated": False,
            "model_promotion_performed": False,
            "active_model_changed": False,
        }

    def candidate_evaluator(**_: Any) -> dict[str, Any]:
        captured["evaluated"] = True
        return {
            "status": "blocked",
            "reason": "external_research_gates_blocked",
            "decision": "MANTER_EM_QUARENTENA",
            "sends_orders": False,
            "changes_risk": False,
            "writes_runtime": False,
            "model_promotion_performed": False,
            "active_model_changed": False,
        }

    report = run_paper_autolearning_continuous_orchestrator_v1(
        project_root=tmp_path,
        write_feedback=True,
        train_challenger=True,
        write_quarantine_artifacts=True,
        evaluate_candidates=True,
        live_feedback_runner=live_feedback_runner,
        quarantine_runner=quarantine_runner,
        candidate_evaluator=candidate_evaluator,
        microbatch_loader=lambda _: _source_microbatch(),
    )

    assert report["status"] == "ok"
    assert captured["targets"] == [1, 0]
    assert captured["train"] is True
    assert captured["evaluated"] is True
    assert report["training_performed"] is True
    assert report["new_outcomes"] == 2
    assert report["candidate_count"] == 2
    assert report["candidate_evaluation_decision"] == "MANTER_EM_QUARENTENA"
    assert report["close_to_feedback_latency_seconds_latest"] == 1.5
    assert report["feedback_to_challenger_latency_seconds"] is not None
    assert report["feedback_to_challenger_latency_seconds"] >= 0.0
    assert report["model_promotion_performed"] is False
    assert report["qlib_operational_authority"] is False


def test_unsafe_quarantine_contract_fails_closed(tmp_path: Path) -> None:
    microbatch_path = tmp_path / "microbatch.parquet"
    _touch_microbatch(microbatch_path)

    def live_feedback_runner(**_: Any) -> dict[str, Any]:
        return _feedback_report(microbatch_path)

    def quarantine_runner(**_: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "reason": "unsafe",
            "quarantine_candidate_count": 0,
            "sends_orders": True,
            "blockers": [],
        }

    report = run_paper_autolearning_continuous_orchestrator_v1(
        project_root=tmp_path,
        write_feedback=True,
        live_feedback_runner=live_feedback_runner,
        quarantine_runner=quarantine_runner,
        microbatch_loader=lambda _: _source_microbatch(),
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "unsafe_quarantine_contract"
    assert "quarantine:sends_orders" in report["blockers"]
    assert report["training_performed"] is False
    assert report["sends_orders"] is False


def test_no_new_evidence_is_idempotent_noop(tmp_path: Path) -> None:
    calls = {"quarantine": 0}

    def live_feedback_runner(**_: Any) -> dict[str, Any]:
        return _feedback_report(None, new_outcomes=0, rows=0)

    def quarantine_runner(**_: Any) -> dict[str, Any]:
        calls["quarantine"] += 1
        return {}

    report = run_paper_autolearning_continuous_orchestrator_v1(
        project_root=tmp_path,
        write_feedback=True,
        live_feedback_runner=live_feedback_runner,
        quarantine_runner=quarantine_runner,
    )

    assert report["status"] == "ok"
    assert report["reason"] == "no_new_training_evidence"
    assert report["training_performed"] is False
    assert calls["quarantine"] == 0


def test_historical_dedupe_count_does_not_become_reprocessing(tmp_path: Path) -> None:
    def live_feedback_runner(**_: Any) -> dict[str, Any]:
        feedback = _feedback_report(None, new_outcomes=0, rows=0)
        feedback["duplicate_outcome_event_count"] = 905
        feedback["already_known_outcome_event_count"] = 905
        feedback["duplicate_or_reprocessed_row_count"] = 0
        return feedback

    report = run_paper_autolearning_continuous_orchestrator_v1(
        project_root=tmp_path,
        write_feedback=True,
        train_challenger=True,
        live_feedback_runner=live_feedback_runner,
    )

    assert report["status"] == "ok"
    assert report["new_outcomes"] == 0
    assert report["microbatch_rows"] == 0
    assert report["duplicate_or_reprocessed_row_count"] == 0
    assert report["training_performed"] is False


def test_bridge_blocks_lookahead_columns() -> None:
    frame = _source_microbatch()
    frame["future_ret_5m"] = [0.01, -0.02]

    bridged, errors = build_quarantine_microbatch(frame)

    assert bridged.empty
    assert errors == ["lookahead_columns_detected:future_ret_5m"]


def test_unattended_service_runs_repeated_cycles_and_counts_training(tmp_path: Path) -> None:
    responses = [
        {
            "status": "ok",
            "reason": "no_new_training_evidence",
            "new_outcome_event_count": 0,
            "microbatch_rows": 0,
            "duplicate_or_reprocessed_row_count": 0,
            "quarantine_candidate_count": 0,
            "training_performed": False,
        },
        {
            "status": "ok",
            "reason": "quarantine_training_cycle_completed",
            "new_outcome_event_count": 3,
            "microbatch_rows": 3,
            "duplicate_or_reprocessed_row_count": 1,
            "quarantine_candidate_count": 2,
            "training_performed": True,
        },
        {
            "status": "ok",
            "reason": "no_new_training_evidence",
            "new_outcome_event_count": 0,
            "microbatch_rows": 0,
            "duplicate_or_reprocessed_row_count": 0,
            "quarantine_candidate_count": 0,
            "training_performed": False,
        },
    ]
    observed: list[dict[str, Any]] = []
    sleeps: list[float] = []
    lock = FakeLock(tmp_path / "autolearning.lock")

    def cycle_runner(**_: Any) -> dict[str, Any]:
        return responses.pop(0)

    summary = run_paper_autolearning_unattended_service_v2(
        project_root=tmp_path,
        interval_seconds=5,
        max_cycles=3,
        cycle_runner=cycle_runner,
        cycle_observer=observed.append,
        sleep_fn=sleeps.append,
        lock=lock,
    )

    assert summary["status"] == "ok"
    assert summary["reason"] == "max_cycles_reached"
    assert summary["cycles_executed"] == 3
    assert summary["cycles_with_new_outcomes"] == 1
    assert summary["training_cycle_count"] == 1
    assert summary["new_outcome_event_count"] == 3
    assert summary["microbatch_rows"] == 3
    assert summary["candidate_count"] == 2
    assert summary["duplicate_or_reprocessed_row_count"] == 1
    assert sleeps == [5, 5]
    assert [item["cycle_index"] for item in observed] == [1, 2, 3]
    assert lock.acquired is True
    assert lock.released is True


def test_unattended_service_isolates_cycle_exception_and_continues(tmp_path: Path) -> None:
    calls = 0
    observed: list[dict[str, Any]] = []

    def cycle_runner(**_: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("transient source error")
        return {
            "status": "ok",
            "reason": "no_new_training_evidence",
            "new_outcome_event_count": 0,
            "microbatch_rows": 0,
            "duplicate_or_reprocessed_row_count": 0,
            "quarantine_candidate_count": 0,
            "training_performed": False,
        }

    summary = run_paper_autolearning_unattended_service_v2(
        project_root=tmp_path,
        interval_seconds=1,
        max_cycles=2,
        cycle_runner=cycle_runner,
        cycle_observer=observed.append,
        sleep_fn=lambda _: None,
        lock=FakeLock(tmp_path / "autolearning.lock"),
    )

    assert summary["status"] == "warning"
    assert summary["cycles_executed"] == 2
    assert summary["exception_cycle_count"] == 1
    assert summary["blocked_cycle_count"] == 1
    assert observed[0]["reason"] == "unattended_cycle_exception"
    assert observed[0]["exception_type"] == "OSError"
    assert observed[1]["status"] == "ok"


def test_unattended_service_blocks_second_daemon_lock(tmp_path: Path) -> None:
    lock = FakeLock(tmp_path / "autolearning.lock", busy=True)

    summary = run_paper_autolearning_unattended_service_v2(
        project_root=tmp_path,
        interval_seconds=5,
        max_cycles=1,
        lock=lock,
    )

    assert summary["status"] == "blocked"
    assert summary["reason"] == "paper_autolearning_unattended_lock_busy"
    assert summary["cycles_executed"] == 0
    assert lock.released is False
    assert summary["sends_orders"] is False
    assert summary["changes_risk"] is False
