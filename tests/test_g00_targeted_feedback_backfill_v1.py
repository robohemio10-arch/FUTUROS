from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import scripts.run_g00_targeted_feedback_backfill_v1 as cli
from smartcrypto.learning.g00_targeted_feedback_backfill import (
    CONFIRMATION_TEXT,
    run_g00_targeted_feedback_backfill_v1,
)
from smartcrypto.learning.g00_targeted_feedback_backfill.orchestrator import (
    event_target,
)
from smartcrypto.learning.paper_feedback_autotrain_e2e_closeout.controlled_backfill import (
    canonical_sha256,
    load_jsonl,
)


def missing_record(trade_id: int) -> dict[str, Any]:
    order_id = f"freqtrade-paper-{trade_id}"
    return {
        "dedup_key": f"order_close:{order_id}",
        "native_key": f"trade_close:{trade_id}",
        "closed_trades_csv_order_id": order_id,
        "paper_db_trade_id": str(trade_id),
        "symbol": "BTCUSDT" if trade_id != 600 else "ETHUSDT",
        "side": "long" if trade_id != 600 else "short",
        "open_time_utc": "2026-07-30T12:00:00+00:00",
        "close_time_utc": f"2026-07-30T12:{trade_id % 60:02d}:00+00:00",
        "net_pnl": float(trade_id) / 100.0,
        "profit_ratio": 0.01,
        "source_presence": ["closed_trades_csv", "paper_db"],
        "source_keys": {
            "closed_trades_csv": [order_id],
            "paper_db": [str(trade_id)],
        },
        "validation_status": {"would_pass_both_stages": True},
        "classification": "missing_in_feedback",
        "db_csv_match_status": "match",
    }


def diagnostics(
    trade_ids: tuple[int, ...] = (599, 600, 601),
) -> dict[str, Any]:
    records = [missing_record(trade_id) for trade_id in trade_ids]
    return {
        "schema_version": "paper_autotrain_feedback_gap_diagnostics_v1",
        "status": "ok",
        "reason": "diagnostics_completed",
        "paper_db_authority_status": "snapshot_db_fresh_against_csv",
        "missing_in_feedback_count": len(records),
        "missing_in_feedback_records": records,
        "conflicting_group_count": 0,
        "validation_rejection_status": {"rejected_count": 0},
        "feedback_events_normalized_record_count": 0,
        "closed_trades_csv_normalized_record_count": len(records),
        "paper_db_normalized_record_count": len(records),
        "unexpected_writer_count": 0,
        "warnings": [],
    }


def prepare_root(
    root: Path,
    existing: list[dict[str, Any]] | None = None,
) -> Path:
    feedback = (
        root
        / "data/feedback/"
        "paper_autotrain_daily_quarantine_feedback_events_v1.jsonl"
    )
    feedback.parent.mkdir(parents=True, exist_ok=True)
    feedback.write_text(
        "".join(
            json.dumps(row, sort_keys=True) + "\n"
            for row in (existing or [])
        ),
        encoding="utf-8",
    )
    csv_path = (
        root / "data/trades/inbox/freqtrade_paper_closed_trades.csv"
    )
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(
        "order_id\nfreqtrade-paper-599\nfreqtrade-paper-600\n",
        encoding="utf-8",
    )
    return feedback


def probe(
    root: Path,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return run_g00_targeted_feedback_backfill_v1(
        project_root=root,
        diagnostics_override=source or diagnostics(),
    )


def authorized(
    root: Path,
    source: dict[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    source_report = source or diagnostics()
    initial = probe(root, source_report)
    arguments: dict[str, Any] = {
        "project_root": root,
        "execute_targeted_backfill": True,
        "expected_plan_hash": initial["plan_hash"],
        "expected_dryrun_hash": initial["dryrun_hash"],
        "expected_target_batch_hash": initial["target_batch_hash"],
        "expected_source_fingerprint_hash": (
            initial["source_fingerprint_hash"]
        ),
        "authorization_reference": "G00-2026-TEST",
        "confirmation_text": CONFIRMATION_TEXT,
        "diagnostics_override": source_report,
    }
    arguments.update(overrides)
    return run_g00_targeted_feedback_backfill_v1(**arguments)


def test_default_is_no_write_and_excludes_other_events(
    tmp_path: Path,
) -> None:
    feedback = prepare_root(tmp_path)
    before = feedback.read_bytes()
    report = probe(tmp_path)
    assert report["status"] == "blocked"
    assert report["decision"] == (
        "NO_TARGETED_BACKFILL_WITHOUT_EXPLICIT_AUTHORIZATION"
    )
    assert report["full_planned_event_count"] == 3
    assert report["target_planned_event_count"] == 2
    assert report["other_planned_event_count"] == 1
    assert report["write_performed"] is False
    assert feedback.read_bytes() == before


@pytest.mark.parametrize(
    ("field", "expected_reason"),
    [
        ("expected_plan_hash", "expected_plan_hash_mismatch"),
        ("expected_dryrun_hash", "expected_dryrun_hash_mismatch"),
        (
            "expected_target_batch_hash",
            "expected_target_batch_hash_mismatch",
        ),
        (
            "expected_source_fingerprint_hash",
            "expected_source_fingerprint_hash_mismatch",
        ),
    ],
)
def test_hash_authorization_is_fail_closed(
    tmp_path: Path,
    field: str,
    expected_reason: str,
) -> None:
    prepare_root(tmp_path)
    report = authorized(tmp_path, **{field: "0" * 64})
    assert report["reason"] == expected_reason
    assert report["write_performed"] is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"execute_targeted_backfill": False},
        {"authorization_reference": None},
        {"confirmation_text": "INCORRETO"},
    ],
)
def test_non_hash_authorization_is_fail_closed(
    tmp_path: Path,
    overrides: dict[str, Any],
) -> None:
    prepare_root(tmp_path)
    report = authorized(tmp_path, **overrides)
    assert report["status"] == "blocked"
    assert report["write_performed"] is False


def test_authorized_write_applies_only_599_and_600(
    tmp_path: Path,
) -> None:
    feedback = prepare_root(tmp_path)
    report = authorized(tmp_path)
    rows, errors = load_jsonl(feedback)
    assert not errors
    assert report["status"] == "ok"
    assert report["decision"] == (
        "TARGETED_G00_FEEDBACK_BACKFILL_APPLIED"
    )
    assert report["applied_event_count"] == 2
    assert {event_target(row) for row in rows} == {"599", "600"}
    assert all(
        str(row["paper_db_trade_id"]) != "601"
        for row in rows
    )


def test_authorized_write_does_not_create_downstream_artifacts(
    tmp_path: Path,
) -> None:
    prepare_root(tmp_path)
    report = authorized(tmp_path)
    assert report["creates_microbatch"] is False
    assert report["advances_watermark"] is False
    assert report["runs_training"] is False
    assert not (
        tmp_path
        / "data/research/paper_autotrain_daily_quarantine"
    ).exists()
    assert not (
        tmp_path
        / "data/research/"
        "paper_autotrain_daily_quarantine_watermark"
    ).exists()


def test_idempotent_authorized_rerun_does_not_duplicate(
    tmp_path: Path,
) -> None:
    feedback = prepare_root(tmp_path)
    first_probe = probe(tmp_path)
    first = authorized(tmp_path)
    second = run_g00_targeted_feedback_backfill_v1(
        project_root=tmp_path,
        execute_targeted_backfill=True,
        expected_plan_hash=first_probe["plan_hash"],
        expected_dryrun_hash=first_probe["dryrun_hash"],
        expected_target_batch_hash=first_probe["target_batch_hash"],
        expected_source_fingerprint_hash=(
            first_probe["source_fingerprint_hash"]
        ),
        authorization_reference="G00-2026-TEST",
        confirmation_text=CONFIRMATION_TEXT,
        diagnostics_override=diagnostics(),
    )
    rows, errors = load_jsonl(feedback)
    assert not errors
    assert first["applied_event_count"] == 2
    assert second["decision"] == "TARGETED_BACKFILL_ALREADY_APPLIED"
    assert second["write_performed"] is False
    assert len(rows) == 2


def test_partial_existing_target_state_is_blocked(
    tmp_path: Path,
) -> None:
    prepare_root(tmp_path)
    initial = probe(tmp_path)
    source = diagnostics()
    plan_hash = str(initial["plan_hash"])
    event = {
        "event_type": "paper_autotrain_feedback_gap_backfill_candidate",
        "schema_version": (
            "paper_autotrain_feedback_gap_backfill_candidate_v1"
        ),
        "idempotency_key": "feedback-gap:partial-599",
        "source_plan_id": "partial-plan",
        "source_plan_hash": plan_hash,
        "dedup_key": "order_close:freqtrade-paper-599",
        "native_key": "trade_close:599",
        "closed_trades_csv_order_id": "freqtrade-paper-599",
        "paper_db_trade_id": "599",
        "symbol": "BTCUSDT",
        "side": "long",
        "open_time_utc": "2026-07-30T12:00:00+00:00",
        "close_time_utc": "2026-07-30T12:59:00+00:00",
        "net_pnl": 5.99,
        "profit_ratio": 0.01,
        "source_presence": ["closed_trades_csv", "paper_db"],
        "source_keys": {
            "closed_trades_csv": ["freqtrade-paper-599"],
            "paper_db": ["599"],
        },
        "validation_status": {"would_pass_both_stages": True},
        "simulation_status": "SIMULATED_ONLY_NOT_WRITTEN",
    }
    event["event_hash"] = canonical_sha256(event)
    prepare_root(tmp_path, [event])
    report = probe(tmp_path, source)
    assert "partial_target_materialization_detected" in (
        report["blockers"]
    )


def test_source_fingerprint_change_before_write_blocks(
    tmp_path: Path,
) -> None:
    feedback = prepare_root(tmp_path)
    csv_path = (
        tmp_path
        / "data/trades/inbox/freqtrade_paper_closed_trades.csv"
    )
    report = authorized(
        tmp_path,
        source_change_hook=lambda: csv_path.write_text(
            "changed",
            encoding="utf-8",
        ),
    )
    assert report["reason"] == (
        "source_fingerprint_changed_before_write"
    )
    assert report["write_performed"] is False
    assert feedback.read_text(encoding="utf-8") == ""


def test_post_write_validation_failure_rolls_back(
    tmp_path: Path,
) -> None:
    feedback = prepare_root(tmp_path)
    before = feedback.read_bytes()
    report = authorized(
        tmp_path,
        post_write_validator=lambda _rows: False,
    )
    assert report["rollback_performed"] is True
    assert feedback.read_bytes() == before



def test_volatile_diagnostics_metadata_does_not_change_hashes(
    tmp_path: Path,
) -> None:
    prepare_root(tmp_path)
    first_source = diagnostics()
    first_source.update(
        {
            "generated_at_utc": "2026-07-31T12:00:00+00:00",
            "output_paths": {"json": "first.json"},
            "write_report_requested": False,
            "write_performed": False,
            "safety_flags": {"read_only": True},
        }
    )
    second_source = diagnostics()
    second_source.update(
        {
            "generated_at_utc": "2026-07-31T13:00:00+00:00",
            "output_paths": {"json": "second.json"},
            "write_report_requested": True,
            "write_performed": True,
            "safety_flags": {"read_only": False},
        }
    )

    first = probe(tmp_path, first_source)
    second = probe(tmp_path, second_source)

    for field in (
        "diagnostics_identity_hash",
        "plan_hash",
        "dryrun_hash",
        "target_batch_hash",
        "source_fingerprint_hash",
    ):
        assert first[field] == second[field]


def test_semantic_diagnostics_change_changes_identity_and_plan_hash(
    tmp_path: Path,
) -> None:
    prepare_root(tmp_path)
    first_source = diagnostics()
    second_source = diagnostics()
    second_source["missing_in_feedback_records"][0]["net_pnl"] = 999.0

    first = probe(tmp_path, first_source)
    second = probe(tmp_path, second_source)

    assert (
        first["diagnostics_identity_hash"]
        != second["diagnostics_identity_hash"]
    )
    assert first["plan_hash"] != second["plan_hash"]


def test_unsafe_report_path_is_blocked(tmp_path: Path) -> None:
    prepare_root(tmp_path)
    report = run_g00_targeted_feedback_backfill_v1(
        project_root=tmp_path,
        diagnostics_override=diagnostics(),
        write_report=True,
        output_json_path="../unsafe.json",
    )
    assert report["reason"] == "unsafe_path"
    assert report["write_performed"] is False


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"paper_db_trade_id": "599"}, "599"),
        (
            {
                "closed_trades_csv_order_id": (
                    "freqtrade-paper-600"
                )
            },
            "600",
        ),
        (
            {"native_key": "trade_close:599"},
            "599",
        ),
        (
            {
                "dedup_key": (
                    "order_close:freqtrade-paper-600"
                )
            },
            "600",
        ),
    ],
)
def test_event_target_resolves_canonical_aliases(
    payload: dict[str, Any],
    expected: str,
) -> None:
    assert event_target(payload) == expected


def test_cli_default_remains_no_write(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "run_g00_targeted_feedback_backfill_v1",
        lambda **_kwargs: {
            "status": "blocked",
            "reason": (
                "explicit_targeted_backfill_authorization_required"
            ),
            "decision": (
                "NO_TARGETED_BACKFILL_WITHOUT_EXPLICIT_AUTHORIZATION"
            ),
        },
    )
    code = cli.main(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["status"] == "blocked"


def test_confirmation_text_is_exposed_by_cli(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(["--print-confirmation-text"])
    assert code == 0
    assert capsys.readouterr().out.strip() == CONFIRMATION_TEXT
