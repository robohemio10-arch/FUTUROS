from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.build_paper_autotrain_feedback_gap_remediation_plan_v1 import main
from smartcrypto.learning.paper_autotrain_feedback_gap_remediation_plan import (
    build_paper_autotrain_feedback_gap_remediation_plan_v1,
    build_remediation_plan_from_diagnostics,
)


def missing_record(index: int, *, valid: bool = True, match: bool = True) -> dict[str, Any]:
    trade_id = str(500 + index)
    close_time = f"2026-07-03T12:{index % 60:02d}:00+00:00"
    return {
        "classification": "missing_in_feedback",
        "dedup_key": f"close_id:{trade_id}|{close_time}",
        "native_key": f"trade_close:{trade_id}|{close_time}",
        "closed_trades_csv_order_id": f"freqtrade-paper-{trade_id}",
        "paper_db_trade_id": trade_id,
        "symbol": "BTCUSDT" if index % 2 == 0 else "ETHUSDT",
        "side": "long" if index % 2 == 0 else "short",
        "open_time_utc": "2026-07-03T11:00:00+00:00",
        "close_time_utc": close_time,
        "net_pnl": float(index + 1) / 10,
        "profit_ratio": float(index + 1) / 1000,
        "source_presence": ["closed_trades_csv", "paper_db"],
        "source_keys": {
            "closed_trades_csv": [f"order_close:freqtrade-paper-{trade_id}|{close_time}"],
            "paper_db": [f"trade_close:{trade_id}|{close_time}"],
        },
        "db_csv_match_status": "match" if match else "conflicting",
        "validation_status": {
            "stage1_errors": [] if valid else ["missing_symbol"],
            "stage1_would_pass": valid,
            "stage2_would_pass": valid,
            "would_pass_both_stages": valid,
        },
    }


def diagnostics_payload(
    count: int = 45,
    *,
    conflicts: int = 0,
    stale: bool = False,
    invalid_index: int | None = None,
) -> dict[str, Any]:
    records = [missing_record(index, valid=index != invalid_index) for index in range(count)]
    rejected = 1 if invalid_index is not None else 0
    return {
        "schema_version": "paper_autotrain_feedback_gap_diagnostics_v1",
        "status": "ok",
        "reason": "feedback_gap_diagnostics_completed",
        "paper_db_authority_status": (
            "snapshot_db_stale_against_csv" if stale else "snapshot_db_fresh_against_csv"
        ),
        "closed_trades_csv_normalized_record_count": 543,
        "feedback_events_normalized_record_count": 498,
        "paper_db_normalized_record_count": 543,
        "missing_in_feedback_count": count,
        "conflicting_group_count": conflicts,
        "missing_in_feedback_records": records,
        "validation_rejection_status": {
            "status": "some_rejected" if rejected else "none_rejected",
            "rejected_count": rejected,
        },
        "warnings": [],
    }


def write_diagnostics(root: Path, payload: dict[str, Any] | None = None) -> Path:
    path = root / "data" / "reports" / "paper_autotrain_feedback_gap_diagnostics_v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload or diagnostics_payload()), encoding="utf-8")
    return path


def build_from_payload(payload: dict[str, Any], *, generated_at: str = "2026-07-10T12:00:00+00:00") -> dict[str, Any]:
    return build_remediation_plan_from_diagnostics(
        payload,
        input_report_hash="a" * 64,
        input_report_path="data/reports/paper_autotrain_feedback_gap_diagnostics_v1.json",
        output_paths={
            "json": "data/reports/paper_autotrain_feedback_gap_remediation_plan_v1.json",
            "markdown": "data/reports/paper_autotrain_feedback_gap_remediation_plan_v1.md",
        },
        generated_at_utc=generated_at,
    )


def test_default_no_write_does_not_write_anything(tmp_path: Path) -> None:
    write_diagnostics(tmp_path)
    report = build_paper_autotrain_feedback_gap_remediation_plan_v1(project_root=tmp_path)

    assert report["status"] == "ok"
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "paper_autotrain_feedback_gap_remediation_plan_v1.json").exists()
    assert not (tmp_path / "data" / "feedback").exists()


def test_write_report_writes_only_data_reports(tmp_path: Path) -> None:
    write_diagnostics(tmp_path)
    report = build_paper_autotrain_feedback_gap_remediation_plan_v1(
        project_root=tmp_path,
        write_report=True,
    )

    assert report["write_performed"] is True
    assert (tmp_path / report["output_paths"]["json"]).is_file()
    assert (tmp_path / report["output_paths"]["markdown"]).is_file()
    assert not (tmp_path / "data" / "feedback").exists()
    assert not (tmp_path / "data" / "models").exists()
    assert not (tmp_path / "data" / "registries").exists()
    assert not (tmp_path / "data" / "runtime").exists()


def test_45_missing_records_plan_45_future_feedback_events(tmp_path: Path, capsys: Any) -> None:
    source = write_diagnostics(tmp_path, diagnostics_payload(45))
    exit_code = main(
        [
            "--project-root",
            str(tmp_path),
            "--diagnostics-report",
            str(source),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["decision"] == "PLAN_ONLY_NO_BACKFILL"
    assert report["missing_in_feedback_count"] == 45
    assert report["planned_feedback_event_count"] == 45
    assert report["blocked_feedback_event_count"] == 0
    assert len(report["eligible_missing_records"]) == 45
    assert all(row["planned_action"] == "WOULD_CREATE_FEEDBACK_EVENT_IN_FUTURE_BRANCH" for row in report["eligible_missing_records"])


def test_conflict_blocks_decision() -> None:
    report = build_from_payload(diagnostics_payload(2, conflicts=1))

    assert report["status"] == "blocked"
    assert report["decision"] == "BLOCKED_CONFLICTS_REQUIRE_RECONCILIATION"
    assert report["planned_feedback_event_count"] == 0
    assert report["blocked_feedback_event_count"] == 2


def test_validation_rejection_blocks_record() -> None:
    report = build_from_payload(diagnostics_payload(2, invalid_index=1))

    assert report["status"] == "blocked"
    assert report["decision"] == "BLOCKED_VALIDATION_REJECTION_REQUIRES_REVIEW"
    assert report["planned_feedback_event_count"] == 1
    assert report["blocked_feedback_event_count"] == 1
    assert report["blocked_missing_records"][0]["blocked_reason"] == "validation_would_not_pass_both_stages"


def test_stale_db_source_blocks_plan() -> None:
    report = build_from_payload(diagnostics_payload(2, stale=True))

    assert report["status"] == "blocked"
    assert report["decision"] == "BLOCKED_SOURCE_NOT_FRESH"
    assert report["planned_feedback_event_count"] == 0
    assert report["blocked_feedback_event_count"] == 2


def test_safety_flags_remain_fail_closed() -> None:
    report = build_from_payload(diagnostics_payload(1))

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
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
        "would_create_microbatch",
        "would_run_training",
        "would_promote_model",
        "backfill_performed",
    ):
        assert report[field] is False


def test_idempotency_key_is_deterministic() -> None:
    first = build_from_payload(diagnostics_payload(1), generated_at="2026-07-10T12:00:00+00:00")
    second = build_from_payload(diagnostics_payload(1), generated_at="2026-07-11T12:00:00+00:00")

    assert first["eligible_missing_records"][0]["idempotency_key"] == second["eligible_missing_records"][0]["idempotency_key"]


def test_plan_hash_and_plan_id_are_deterministic() -> None:
    first = build_from_payload(diagnostics_payload(3), generated_at="2026-07-10T12:00:00+00:00")
    second = build_from_payload(diagnostics_payload(3), generated_at="2026-07-11T12:00:00+00:00")

    assert first["plan_hash"] == second["plan_hash"]
    assert first["plan_id"] == second["plan_id"]


def test_relative_versionable_paths_use_posix_separators(tmp_path: Path) -> None:
    write_diagnostics(tmp_path)
    report = build_paper_autotrain_feedback_gap_remediation_plan_v1(project_root=tmp_path)

    relative_paths = [report["input_report_path"], *report["output_paths"].values()]
    assert all("\\" not in path for path in relative_paths)
