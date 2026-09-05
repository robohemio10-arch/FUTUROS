from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.learning.paper_autolearning.continuous_orchestrator import (
    build_quarantine_microbatch,
    run_paper_autolearning_continuous_orchestrator_v1,
)


def _feedback_report(path: Path | None, *, new_outcomes: int = 2, rows: int = 2) -> dict[str, Any]:
    return {
        "status": "ok",
        "reason": "incremental_feedback_materialized",
        "new_outcome_event_count": new_outcomes,
        "microbatch_rows": rows,
        "microbatch_output_path": str(path) if path is not None else None,
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
    assert report["operational_authority"] is False
    assert report["sends_orders"] is False


def test_materialized_cycle_bridges_binary_target_and_evaluates(tmp_path: Path) -> None:
    microbatch_path = tmp_path / "data" / "feedback" / "training_microbatches" / "2026-09-05.parquet"
    microbatch_path.parent.mkdir(parents=True)
    _source_microbatch().to_parquet(microbatch_path, index=False)
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
    )

    assert report["status"] == "ok"
    assert captured["targets"] == [1, 0]
    assert captured["train"] is True
    assert captured["evaluated"] is True
    assert report["candidate_evaluation_decision"] == "MANTER_EM_QUARENTENA"
    assert report["model_promotion_performed"] is False
    assert report["qlib_operational_authority"] is False


def test_unsafe_quarantine_contract_fails_closed(tmp_path: Path) -> None:
    microbatch_path = tmp_path / "microbatch.parquet"
    _source_microbatch().to_parquet(microbatch_path, index=False)

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
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "unsafe_quarantine_contract"
    assert "quarantine:sends_orders" in report["blockers"]
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
    assert calls["quarantine"] == 0


def test_bridge_blocks_lookahead_columns() -> None:
    frame = _source_microbatch()
    frame["future_ret_5m"] = [0.01, -0.02]

    bridged, errors = build_quarantine_microbatch(frame)

    assert bridged.empty
    assert errors == ["lookahead_columns_detected:future_ret_5m"]
