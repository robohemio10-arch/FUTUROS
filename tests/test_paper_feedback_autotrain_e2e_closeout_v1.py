from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from scripts.run_paper_feedback_autotrain_e2e_closeout_v1 import main as cli_main
from smartcrypto.learning.paper_feedback_autotrain_e2e_closeout import CONFIRMATION_TEXT
from smartcrypto.learning.paper_feedback_autotrain_e2e_closeout.controlled_backfill import (
    BackfillRequest,
    canonical_sha256,
    execute_controlled_backfill,
    file_sha256,
    load_jsonl,
    source_fingerprint,
)
from smartcrypto.learning.paper_feedback_autotrain_e2e_closeout.orchestrator import (
    run_paper_feedback_autotrain_e2e_closeout_v1,
)

PACKAGE = Path("smartcrypto/learning/paper_feedback_autotrain_e2e_closeout")


def candidate(index: int = 1) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event_type": "paper_autotrain_feedback_gap_backfill_candidate",
        "schema_version": "paper_autotrain_feedback_gap_backfill_candidate_v1",
        "idempotency_key": f"feedback-gap:key-{index}",
        "source_plan_id": "feedback-gap-plan-test",
        "source_plan_hash": "a" * 64,
        "dedup_key": f"order_id:{index}",
        "native_key": f"order_id:{index}",
        "closed_trades_csv_order_id": str(index),
        "paper_db_trade_id": str(index),
        "symbol": "BTCUSDT",
        "side": "long",
        "open_time_utc": f"2026-07-0{index}T00:00:00+00:00",
        "close_time_utc": f"2026-07-0{index}T00:05:00+00:00",
        "net_pnl": float(index),
        "profit_ratio": 0.01,
        "source_presence": ["closed_trades_csv", "paper_db"],
        "source_keys": {"closed_trades_csv": [str(index)], "paper_db": [str(index)]},
        "validation_status": {"would_pass_both_stages": True},
        "simulation_status": "SIMULATED_ONLY_NOT_WRITTEN",
    }
    payload["event_hash"] = canonical_sha256(payload)
    return payload


def diagnostics(rows: int = 1, *, unexpected_writer_count: int = 0) -> dict[str, Any]:
    missing = []
    for index in range(1, rows + 1):
        event = candidate(index)
        missing.append(
            {
                key: event[key]
                for key in (
                    "dedup_key",
                    "native_key",
                    "closed_trades_csv_order_id",
                    "paper_db_trade_id",
                    "symbol",
                    "side",
                    "open_time_utc",
                    "close_time_utc",
                    "net_pnl",
                    "profit_ratio",
                    "source_presence",
                    "source_keys",
                    "validation_status",
                )
            }
            | {"classification": "missing_in_feedback", "db_csv_match_status": "match"}
        )
    return {
        "schema_version": "paper_autotrain_feedback_gap_diagnostics_v1",
        "status": "ok",
        "reason": "diagnostics_completed",
        "paper_db_authority_status": "snapshot_db_fresh_against_csv",
        "missing_in_feedback_count": rows,
        "missing_in_feedback_records": missing,
        "conflicting_group_count": 0,
        "validation_rejection_status": {"rejected_count": 0},
        "feedback_events_normalized_record_count": 0,
        "closed_trades_csv_normalized_record_count": rows,
        "paper_db_normalized_record_count": rows,
        "unexpected_writer_count": unexpected_writer_count,
        "warnings": [],
    }


def readiness_overrides(**changes: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {
        "watermark": {"status": "ok", "watermark_status": "ok"},
        "freshness": {"status": "ok"},
        "microbatch": {"status": "ok"},
        "qlib": {"status": "ok", "qlib_backend_status": "available"},
        "walkforward": {
            "status": "ok",
            "leakage_status": "ok",
            "future_columns_in_features_count": 0,
            "target_columns_in_features_count": 0,
            "outcome_columns_in_features_count": 0,
        },
        "execution_cost": {"status": "ok"},
        "drift": {"status": "ok"},
        "registry": {"status": "ok", "registry_gate_status": "ok"},
    }
    values.update(changes)
    return values


def prepare_root(root: Path, existing: list[dict[str, Any]] | None = None) -> Path:
    feedback = root / "data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl"
    feedback.parent.mkdir(parents=True, exist_ok=True)
    feedback.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in (existing or [])),
        encoding="utf-8",
    )
    csv = root / "data/trades/inbox/freqtrade_paper_closed_trades.csv"
    csv.parent.mkdir(parents=True, exist_ok=True)
    csv.write_text("order_id\n1\n", encoding="utf-8")
    return feedback


def probe(root: Path, source: dict[str, Any] | None = None) -> dict[str, Any]:
    return run_paper_feedback_autotrain_e2e_closeout_v1(
        project_root=root,
        diagnostics_override=source or diagnostics(),
    )


def authorized(root: Path, **kwargs: Any) -> dict[str, Any]:
    source = kwargs.pop("diagnostics_override", diagnostics())
    initial = probe(root, source)
    return run_paper_feedback_autotrain_e2e_closeout_v1(
        project_root=root,
        execute_backfill=True,
        expected_plan_hash=initial["plan_hash"],
        expected_dryrun_hash=initial["dryrun_hash"],
        authorization_reference="SEC-2026-TEST",
        confirmation_text=CONFIRMATION_TEXT,
        diagnostics_override=source,
        readiness_overrides=readiness_overrides(),
        **kwargs,
    )


def direct_request(root: Path, events: tuple[dict[str, Any], ...] | None = None) -> BackfillRequest:
    feedback = prepare_root(root)
    csv = root / "data/trades/inbox/freqtrade_paper_closed_trades.csv"
    return BackfillRequest(
        feedback_path=feedback,
        backup_dir=root / "data/backups/closeout",
        lock_path=feedback.with_suffix(".jsonl.lock"),
        operation_id="operation-test",
        authorization_reference="SEC-2026-TEST",
        candidate_events=events or (candidate(),),
        external_source_paths=(csv,),
        source_fingerprint_hash=source_fingerprint((csv,)),
    )


def test_default_is_no_write_and_requires_explicit_authorization(tmp_path: Path) -> None:
    feedback = prepare_root(tmp_path)
    before = feedback.read_bytes()
    report = probe(tmp_path)
    assert report["status"] == "blocked"
    assert report["reason"] == "explicit_backfill_authorization_required"
    assert report["decision"] == "NO_BACKFILL_WITHOUT_EXPLICIT_AUTHORIZATION"
    assert report["write_performed"] is False
    assert feedback.read_bytes() == before


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        ({"confirmation_text": None}, "explicit_backfill_authorization_required"),
        ({"confirmation_text": "INCORRETO"}, "explicit_backfill_authorization_required"),
        ({"authorization_reference": None}, "explicit_backfill_authorization_required"),
        ({"expected_plan_hash": "0" * 64}, "expected_plan_hash_mismatch"),
        ({"expected_dryrun_hash": "0" * 64}, "expected_dryrun_hash_mismatch"),
    ],
)
def test_authorization_contract_is_fail_closed(
    tmp_path: Path,
    overrides: dict[str, Any],
    expected_reason: str,
) -> None:
    prepare_root(tmp_path)
    initial = probe(tmp_path)
    arguments = {
        "project_root": tmp_path,
        "execute_backfill": True,
        "expected_plan_hash": initial["plan_hash"],
        "expected_dryrun_hash": initial["dryrun_hash"],
        "authorization_reference": "SEC-2026-TEST",
        "confirmation_text": CONFIRMATION_TEXT,
        "diagnostics_override": diagnostics(),
    }
    arguments.update(overrides)
    report = run_paper_feedback_autotrain_e2e_closeout_v1(**arguments)
    assert report["reason"] == expected_reason
    assert report["write_performed"] is False


def test_unexpected_writer_blocks_before_mutation(tmp_path: Path) -> None:
    feedback = prepare_root(tmp_path)
    report = authorized(tmp_path, diagnostics_override=diagnostics(unexpected_writer_count=1))
    assert report["reason"] == "unexpected_feedback_writer_detected"
    assert feedback.read_text(encoding="utf-8") == ""


def test_existing_lock_blocks(tmp_path: Path) -> None:
    request = direct_request(tmp_path)
    request.lock_path.write_text("owned elsewhere", encoding="utf-8")
    report = execute_controlled_backfill(request)
    assert report["reason"] == "backfill_lock_already_exists"
    assert request.lock_path.exists()


def test_existing_backup_blocks(tmp_path: Path) -> None:
    request = direct_request(tmp_path)
    request.backup_dir.mkdir(parents=True)
    (request.backup_dir / f"{request.feedback_path.name}.{request.operation_id}.bak").write_bytes(b"existing")
    report = execute_controlled_backfill(request)
    assert report["reason"] == "backup_already_exists"
    assert request.feedback_path.read_text(encoding="utf-8") == ""


def test_backup_is_byte_exact_and_hash_matches(tmp_path: Path) -> None:
    request = direct_request(tmp_path)
    original = request.feedback_path.read_bytes()
    report = execute_controlled_backfill(request)
    backup = next(request.backup_dir.glob("*.bak"))
    assert report["backup_created"] is True
    assert backup.read_bytes() == original
    assert file_sha256(backup) == hashlib.sha256(original).hexdigest()


def test_atomic_replace_is_used(tmp_path: Path) -> None:
    request = direct_request(tmp_path)
    calls: list[tuple[Path, Path]] = []

    def replace(source: Any, target: Any) -> None:
        calls.append((Path(source), Path(target)))
        os.replace(source, target)

    report = execute_controlled_backfill(request, replace_function=replace)
    assert report["backfill_performed"] is True
    assert calls and calls[0][1] == request.feedback_path


def test_invalid_jsonl_blocks(tmp_path: Path) -> None:
    request = direct_request(tmp_path)
    request.feedback_path.write_text("{invalid\n", encoding="utf-8")
    report = execute_controlled_backfill(request)
    assert report["reason"] == "invalid_feedback_jsonl"


def test_duplicate_event_in_batch_blocks(tmp_path: Path) -> None:
    item = candidate()
    request = direct_request(tmp_path, (item, dict(item)))
    report = execute_controlled_backfill(request)
    assert report["duplicate_count"] == 1
    assert report["write_performed"] is False


def test_existing_duplicate_feedback_blocks(tmp_path: Path) -> None:
    item = candidate()
    feedback = prepare_root(tmp_path, [item, item])
    csv = tmp_path / "data/trades/inbox/freqtrade_paper_closed_trades.csv"
    request = BackfillRequest(
        feedback_path=feedback,
        backup_dir=tmp_path / "data/backups/closeout",
        lock_path=feedback.with_suffix(".jsonl.lock"),
        operation_id="operation-test",
        authorization_reference="SEC-2026-TEST",
        candidate_events=(candidate(2),),
        external_source_paths=(csv,),
        source_fingerprint_hash=source_fingerprint((csv,)),
    )
    report = execute_controlled_backfill(request)
    assert report["reason"] == "existing_feedback_duplicates_detected"


def test_initial_source_fingerprint_mismatch_blocks_before_lock(tmp_path: Path) -> None:
    request = direct_request(tmp_path)
    invalid = BackfillRequest(**{**request.__dict__, "source_fingerprint_hash": "0" * 64})
    report = execute_controlled_backfill(invalid)
    assert report["reason"] == "source_fingerprint_mismatch"
    assert not request.lock_path.exists()


def test_event_already_existing_is_idempotent(tmp_path: Path) -> None:
    item = candidate()
    feedback = prepare_root(tmp_path, [item])
    csv = tmp_path / "data/trades/inbox/freqtrade_paper_closed_trades.csv"
    request = BackfillRequest(
        feedback_path=feedback,
        backup_dir=tmp_path / "data/backups/closeout",
        lock_path=feedback.with_suffix(".jsonl.lock"),
        operation_id="operation-test",
        authorization_reference="SEC-2026-TEST",
        candidate_events=(item,),
        external_source_paths=(csv,),
        source_fingerprint_hash=source_fingerprint((csv,)),
    )
    report = execute_controlled_backfill(request)
    assert report["reason"] == "authorized_backfill_already_applied"
    assert report["already_applied"] is True
    assert not request.backup_dir.exists()


def test_partially_existing_batch_adds_only_missing(tmp_path: Path) -> None:
    first, second = candidate(1), candidate(2)
    feedback = prepare_root(tmp_path, [first])
    csv = tmp_path / "data/trades/inbox/freqtrade_paper_closed_trades.csv"
    request = BackfillRequest(
        feedback_path=feedback,
        backup_dir=tmp_path / "data/backups/closeout",
        lock_path=feedback.with_suffix(".jsonl.lock"),
        operation_id="operation-test",
        authorization_reference="SEC-2026-TEST",
        candidate_events=(first, second),
        external_source_paths=(csv,),
        source_fingerprint_hash=source_fingerprint((csv,)),
    )
    report = execute_controlled_backfill(request)
    rows, errors = load_jsonl(feedback)
    assert not errors
    assert len(rows) == 2
    assert report["applied_event_count"] == 1
    assert report["already_existing_count"] == 1


def test_source_change_between_preflight_and_write_blocks(tmp_path: Path) -> None:
    request = direct_request(tmp_path)
    source = request.external_source_paths[0]
    report = execute_controlled_backfill(
        request,
        source_change_hook=lambda: source.write_text("changed", encoding="utf-8"),
    )
    assert report["reason"] == "source_fingerprint_changed_before_write"
    assert report["write_performed"] is False


def test_valid_post_write_audit_completes(tmp_path: Path) -> None:
    request = direct_request(tmp_path)
    report = execute_controlled_backfill(request)
    assert report["status"] == "ok"
    assert report["missing_after_count"] == 0
    assert report["duplicate_count"] == 0


def test_post_write_failure_rolls_back(tmp_path: Path) -> None:
    request = direct_request(tmp_path)
    original = request.feedback_path.read_bytes()
    report = execute_controlled_backfill(request, post_write_validator=lambda _rows: False)
    assert report["reason"] == "post_write_validation_failed_rollback_completed"
    assert report["rollback_performed"] is True
    assert request.feedback_path.read_bytes() == original


def test_post_write_rollback_failure_requires_manual_intervention(tmp_path: Path) -> None:
    request = direct_request(tmp_path)

    def fail_rollback(_source: Any, _target: Any) -> None:
        raise OSError("synthetic rollback failure")

    report = execute_controlled_backfill(
        request,
        post_write_validator=lambda _rows: False,
        rollback_replace_function=fail_rollback,
    )
    assert report["decision"] == "MANUAL_INTERVENTION_REQUIRED"
    assert report["manual_intervention_required"] is True
    assert request.lock_path.exists()


def test_authorized_orchestrator_reaches_ready_only_when_all_gates_pass(tmp_path: Path) -> None:
    prepare_root(tmp_path)
    report = authorized(tmp_path)
    assert report["decision"] == "READY_FOR_PAPER_OBSERVATION"
    assert report["write_performed"] is True
    assert report["runs_training"] is False
    assert report["promotes_model"] is False


def test_orchestrator_second_run_is_idempotent_without_new_backup(tmp_path: Path) -> None:
    prepare_root(tmp_path)
    initial = probe(tmp_path)
    arguments = {
        "project_root": tmp_path,
        "execute_backfill": True,
        "expected_plan_hash": initial["plan_hash"],
        "expected_dryrun_hash": initial["dryrun_hash"],
        "authorization_reference": "SEC-2026-TEST",
        "confirmation_text": CONFIRMATION_TEXT,
        "diagnostics_override": diagnostics(),
        "readiness_overrides": readiness_overrides(),
    }
    first = run_paper_feedback_autotrain_e2e_closeout_v1(**arguments)
    backups_before = list((tmp_path / "data/backups/paper_feedback_autotrain_e2e_closeout").glob("*.bak"))
    second = run_paper_feedback_autotrain_e2e_closeout_v1(**arguments)
    backups_after = list((tmp_path / "data/backups/paper_feedback_autotrain_e2e_closeout").glob("*.bak"))
    assert first["backfill_performed"] is True
    assert second["reason"] == "authorized_backfill_already_applied"
    assert second["already_applied"] is True
    assert len(backups_after) == len(backups_before) == 1


@pytest.mark.parametrize(
    ("gate", "payload"),
    [
        ("qlib", {"status": "blocked", "qlib_backend_status": "unavailable"}),
        ("walkforward", {"status": "blocked", "leakage_status": "blocked"}),
        ("execution_cost", {"status": "blocked"}),
        ("drift", {"status": "blocked"}),
        ("microbatch", {"status": "blocked"}),
    ],
)
def test_institutional_gate_blockers_keep_research_decision(
    tmp_path: Path,
    gate: str,
    payload: dict[str, Any],
) -> None:
    prepare_root(tmp_path)
    overrides = readiness_overrides(**{gate: payload})
    initial = probe(tmp_path)
    report = run_paper_feedback_autotrain_e2e_closeout_v1(
        project_root=tmp_path,
        execute_backfill=True,
        expected_plan_hash=initial["plan_hash"],
        expected_dryrun_hash=initial["dryrun_hash"],
        authorization_reference="SEC-2026-TEST",
        confirmation_text=CONFIRMATION_TEXT,
        diagnostics_override=diagnostics(),
        readiness_overrides=overrides,
    )
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["status"] == "blocked"


def test_report_is_sanitized_and_contains_no_events(tmp_path: Path) -> None:
    prepare_root(tmp_path)
    report = probe(tmp_path)
    serialized = json.dumps(report, sort_keys=True)
    assert "simulated_feedback_events" not in report
    assert "eligible_missing_records" not in report
    assert "BTCUSDT" not in serialized
    assert "token" not in serialized.casefold()


def test_write_report_only_writes_json_and_markdown(tmp_path: Path) -> None:
    feedback = prepare_root(tmp_path)
    before = feedback.read_bytes()
    report = run_paper_feedback_autotrain_e2e_closeout_v1(
        project_root=tmp_path,
        diagnostics_override=diagnostics(),
        write_report=True,
    )
    assert report["write_report_performed"] is True
    assert (tmp_path / "data/reports/paper_feedback_autotrain_e2e_closeout_v1.json").is_file()
    assert (tmp_path / "data/reports/paper_feedback_autotrain_e2e_closeout_v1.md").is_file()
    assert feedback.read_bytes() == before


def test_cli_default_executes_without_backfill(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    prepare_root(tmp_path)
    code = cli_main(["--project-root", str(tmp_path), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["reason"] == "explicit_backfill_authorization_required"
    assert report["backfill_performed"] is False


@pytest.mark.parametrize(
    "flag",
    [
        "live_trading_enabled",
        "live_release_allowed",
        "canary_release_allowed",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "runs_training",
        "promotes_model",
        "writes_models",
        "writes_registries",
        "writes_signals",
        "updates_freqtrade",
        "updates_risk_manager",
        "updates_qlib_runtime",
        "updates_ai_shadow_runtime",
        "creates_microbatch",
    ],
)
def test_safety_flags_are_fail_closed(tmp_path: Path, flag: str) -> None:
    prepare_root(tmp_path)
    assert probe(tmp_path)[flag] is False


def test_no_operational_artifacts_are_created(tmp_path: Path) -> None:
    prepare_root(tmp_path)
    protected = [
        tmp_path / "data/runtime",
        tmp_path / "data/registries/active",
        tmp_path / "data/models",
        tmp_path / "data/features/incremental_training_microbatch.parquet",
    ]
    probe(tmp_path)
    assert all(not path.exists() for path in protected)


def test_implementation_has_no_network_env_docker_or_operational_imports() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE.glob("*.py"))
    forbidden = ("import ccxt", "import freqtrade", "import docker", "import requests", "subprocess", "dotenv", ".env")
    assert all(value not in text for value in forbidden)
