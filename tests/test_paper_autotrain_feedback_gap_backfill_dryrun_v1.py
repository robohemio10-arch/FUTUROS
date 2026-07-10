from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from scripts.build_paper_autotrain_feedback_gap_backfill_dryrun_v1 import main
from smartcrypto.learning.paper_autotrain_feedback_gap_backfill_dryrun import (
    DEFAULT_EXPECTED_PLAN_HASH,
    build_dryrun_from_plan,
    build_paper_autotrain_feedback_gap_backfill_dryrun_v1,
)


def eligible_record(index: int) -> dict[str, Any]:
    trade_id = str(511 + index)
    close_time = f"2026-07-{3 + index // 24:02d}T12:{index % 60:02d}:00+00:00"
    return {
        "dedup_key": f"close_id:{trade_id}|{close_time}",
        "native_key": f"trade_close:{trade_id}|{close_time}",
        "closed_trades_csv_order_id": f"freqtrade-paper-{trade_id}",
        "paper_db_trade_id": trade_id,
        "symbol": "BTCUSDT" if index % 2 == 0 else "ETHUSDT",
        "side": "long" if index % 2 == 0 else "short",
        "open_time_utc": "2026-07-03T11:00:00+00:00",
        "close_time_utc": close_time,
        "net_pnl": (index + 1) / 10,
        "profit_ratio": (index + 1) / 1000,
        "source_presence": ["closed_trades_csv", "paper_db"],
        "source_keys": {
            "closed_trades_csv": [f"order_close:freqtrade-paper-{trade_id}|{close_time}"],
            "paper_db": [f"trade_close:{trade_id}|{close_time}"],
        },
        "validation_status": {
            "stage1_errors": [],
            "stage1_would_pass": True,
            "stage2_would_pass": True,
            "would_pass_both_stages": True,
        },
        "planned_action": "WOULD_CREATE_FEEDBACK_EVENT_IN_FUTURE_BRANCH",
        "blocked_reason": None,
        "idempotency_key": f"feedback-gap:{index:064x}",
    }


def plan_payload(count: int = 45) -> dict[str, Any]:
    return {
        "schema_version": "paper_autotrain_feedback_gap_remediation_plan_v1",
        "status": "ok",
        "reason": "remediation_plan_built_without_backfill",
        "decision": "PLAN_ONLY_NO_BACKFILL",
        "plan_id": "feedback-gap-plan-7a566e9359c55c42",
        "plan_hash": DEFAULT_EXPECTED_PLAN_HASH,
        "planned_feedback_event_count": count,
        "blocked_feedback_event_count": 0,
        "eligible_missing_records": [eligible_record(index) for index in range(count)],
    }


def write_inputs(root: Path, plan: dict[str, Any] | None = None, existing: list[dict[str, Any]] | None = None) -> tuple[Path, Path]:
    plan_path = root / "data" / "reports" / "paper_autotrain_feedback_gap_remediation_plan_v1.json"
    feedback_path = root / "data" / "feedback" / "paper_autotrain_daily_quarantine_feedback_events_v1.jsonl"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan or plan_payload()), encoding="utf-8")
    feedback_path.write_text(
        "".join(json.dumps(event) + "\n" for event in (existing or [])),
        encoding="utf-8",
    )
    return plan_path, feedback_path


def build_from_payload(
    plan: dict[str, Any],
    *,
    existing: list[dict[str, Any]] | None = None,
    expected_hash: str = DEFAULT_EXPECTED_PLAN_HASH,
    generated_at: str = "2026-07-10T12:00:00+00:00",
) -> dict[str, Any]:
    return build_dryrun_from_plan(
        plan,
        existing_feedback_events=existing or [],
        expected_plan_hash=expected_hash,
        input_plan_hash="b" * 64,
        input_plan_path="data/reports/paper_autotrain_feedback_gap_remediation_plan_v1.json",
        feedback_events_path="data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl",
        output_paths={
            "json": "data/reports/paper_autotrain_feedback_gap_backfill_dryrun_v1.json",
            "markdown": "data/reports/paper_autotrain_feedback_gap_backfill_dryrun_v1.md",
        },
        generated_at_utc=generated_at,
    )


def test_default_no_write_does_not_write_anything(tmp_path: Path) -> None:
    write_inputs(tmp_path)
    report = build_paper_autotrain_feedback_gap_backfill_dryrun_v1(project_root=tmp_path)

    assert report["status"] == "ok"
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "paper_autotrain_feedback_gap_backfill_dryrun_v1.json").exists()
    assert not (tmp_path / "data" / "runtime").exists()


def test_write_report_writes_only_data_reports(tmp_path: Path) -> None:
    _, feedback_path = write_inputs(tmp_path)
    feedback_before = feedback_path.read_bytes()
    report = build_paper_autotrain_feedback_gap_backfill_dryrun_v1(
        project_root=tmp_path,
        write_report=True,
    )

    assert report["write_performed"] is True
    assert (tmp_path / report["output_paths"]["json"]).is_file()
    assert (tmp_path / report["output_paths"]["markdown"]).is_file()
    assert feedback_path.read_bytes() == feedback_before
    assert not (tmp_path / "data" / "runtime").exists()
    assert not (tmp_path / "data" / "models").exists()
    assert not (tmp_path / "data" / "registries").exists()


def test_45_planned_records_generate_45_simulated_events(tmp_path: Path, capsys: Any) -> None:
    plan_path, feedback_path = write_inputs(tmp_path)
    exit_code = main(
        [
            "--project-root",
            str(tmp_path),
            "--plan-report",
            str(plan_path),
            "--feedback-events-path",
            str(feedback_path),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["decision"] == "DRYRUN_READY_NO_BACKFILL"
    assert report["planned_feedback_event_count"] == 45
    assert report["simulated_feedback_event_count"] == 45
    assert report["blocked_event_count"] == 0
    assert all(event["simulation_status"] == "SIMULATED_ONLY_NOT_WRITTEN" for event in report["simulated_feedback_events"])


def test_duplicate_simulated_event_blocks() -> None:
    plan = plan_payload(2)
    plan["eligible_missing_records"][1] = deepcopy(plan["eligible_missing_records"][0])
    report = build_from_payload(plan)

    assert report["decision"] == "BLOCKED_DUPLICATE_SIMULATED_EVENTS"
    assert report["duplicate_simulated_event_count"] == 1
    assert report["blocked_event_count"] == 2


def test_existing_feedback_event_blocks() -> None:
    plan = plan_payload(1)
    existing = [{"order_id": plan["eligible_missing_records"][0]["closed_trades_csv_order_id"]}]
    report = build_from_payload(plan, existing=existing)

    assert report["decision"] == "BLOCKED_EVENT_ALREADY_EXISTS"
    assert report["already_existing_event_count"] == 1


def test_invalid_event_schema_blocks() -> None:
    plan = plan_payload(1)
    plan["eligible_missing_records"][0]["symbol"] = None
    report = build_from_payload(plan)

    assert report["decision"] == "BLOCKED_SCHEMA_VALIDATION_FAILED"
    assert report["schema_validation_error_count"] >= 1
    assert report["blocked_event_count"] == 1


def test_plan_hash_mismatch_blocks() -> None:
    report = build_from_payload(plan_payload(1), expected_hash="f" * 64)

    assert report["decision"] == "BLOCKED_SOURCE_PLAN_HASH_MISMATCH"
    assert report["simulated_feedback_event_count"] == 0


def test_plan_decision_not_ready_blocks() -> None:
    plan = plan_payload(1)
    plan["decision"] = "BLOCKED_SOURCE_NOT_FRESH"
    report = build_from_payload(plan)

    assert report["decision"] == "BLOCKED_PLAN_NOT_READY"
    assert report["simulated_feedback_event_count"] == 0


def test_dryrun_hash_is_deterministic() -> None:
    first = build_from_payload(plan_payload(3), generated_at="2026-07-10T12:00:00+00:00")
    second = build_from_payload(plan_payload(3), generated_at="2026-07-11T12:00:00+00:00")

    assert first["dryrun_hash"] == second["dryrun_hash"]


def test_event_hash_is_deterministic() -> None:
    first = build_from_payload(plan_payload(1), generated_at="2026-07-10T12:00:00+00:00")
    second = build_from_payload(plan_payload(1), generated_at="2026-07-11T12:00:00+00:00")

    assert first["simulated_feedback_events"][0]["event_hash"] == second["simulated_feedback_events"][0]["event_hash"]


def test_event_order_is_deterministic() -> None:
    plan = plan_payload(3)
    plan["eligible_missing_records"] = list(reversed(plan["eligible_missing_records"]))
    report = build_from_payload(plan)
    ordering = [
        (event["close_time_utc"], event["dedup_key"], event["idempotency_key"])
        for event in report["simulated_feedback_events"]
    ]

    assert ordering == sorted(ordering)


def test_relative_paths_use_posix_separators(tmp_path: Path) -> None:
    write_inputs(tmp_path)
    report = build_paper_autotrain_feedback_gap_backfill_dryrun_v1(project_root=tmp_path)
    paths = [report["input_plan_path"], report["feedback_events_path"], *report["output_paths"].values()]

    assert all("\\" not in path for path in paths)


def test_safety_flags_remain_fail_closed() -> None:
    report = build_from_payload(plan_payload(1))

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["dryrun_only"] is True
    for field in (
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "changes_model",
        "writes_feedback",
        "writes_microbatch",
        "writes_runtime",
        "writes_sqlite",
        "writes_parquet",
        "writes_models",
        "writes_registries",
        "would_create_microbatch",
        "would_run_training",
        "would_promote_model",
        "backfill_performed",
    ):
        assert report[field] is False
