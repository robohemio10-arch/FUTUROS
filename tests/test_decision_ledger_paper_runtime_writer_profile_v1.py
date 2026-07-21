from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts import build_runtime_interruption_quarantine_v1_1 as quarantine_cli
from scripts import validate_decision_ledger_paper_runtime_writer_profile_v1 as validator_cli
from smartcrypto.execution.decision_ledger_paper_runtime_writer_v1 import (
    CANONICAL_ALLOWED_ROOT,
    PaperRuntimeWriterProfileV1,
    RuntimeIdentityEvidenceV1,
    build_runtime_interruption_quarantine,
    create_paper_runtime_writer,
    evaluate_path_policy,
    profile_sha256,
    run_writer_preflight,
)


def _non_root_identity() -> RuntimeIdentityEvidenceV1:
    return RuntimeIdentityEvidenceV1(
        source="test",
        verified=True,
        elevated=False,
        effective_uid=1000,
        reason="non_root_identity_verified",
    )


def _root_identity() -> RuntimeIdentityEvidenceV1:
    return RuntimeIdentityEvidenceV1(
        source="test",
        verified=True,
        elevated=True,
        effective_uid=0,
        reason="root_identity_detected",
    )


def _enabled_profile(**updates: object) -> PaperRuntimeWriterProfileV1:
    payload: dict[str, object] = {
        "activation_state": "preflight_only",
        "enabled": True,
        "runtime_write_authorized": True,
    }
    payload.update(updates)
    return PaperRuntimeWriterProfileV1.model_validate(payload)


def _prepare_allowed_root(project_root: Path) -> Path:
    allowed_root = project_root / Path(*CANONICAL_ALLOWED_ROOT.split("/"))
    allowed_root.mkdir(parents=True)
    return allowed_root


def test_default_profile_is_immutable_disabled_and_paper_only() -> None:
    profile = PaperRuntimeWriterProfileV1()

    assert profile.enabled is False
    assert profile.activation_state == "disabled"
    assert profile.runtime_write_authorized is False
    assert profile.safety_flags.paper_only is True
    assert profile.safety_flags.shadow_only is True
    assert profile.safety_flags.operational_authority is False
    assert profile.safety_flags.sends_orders is False
    assert profile.safety_flags.exchange_private_access is False
    assert profile.safety_flags.changes_risk is False
    with pytest.raises(ValidationError):
        profile.enabled = True  # type: ignore[misc]


def test_profile_rejects_inconsistent_enablement() -> None:
    with pytest.raises(ValidationError, match="enabled_must_match"):
        PaperRuntimeWriterProfileV1(enabled=True)
    with pytest.raises(ValidationError, match="runtime_write_authorized_must_match"):
        PaperRuntimeWriterProfileV1(runtime_write_authorized=True)


def test_durability_and_health_contracts_are_fail_visible() -> None:
    profile = PaperRuntimeWriterProfileV1()

    assert profile.durability.lock_mode == "exclusive_create"
    assert profile.durability.append_mode == "append_only"
    assert profile.durability.file_fsync_required is True
    assert profile.durability.health_fsync_required is True
    assert profile.durability.parent_directory_fsync_required is True
    assert profile.health.health_required is True
    assert profile.health.failure_counter_monotonic is True
    assert profile.health.raw_error_message_persistence_allowed is False
    assert profile.health.automatic_recovery_allowed is False


def test_safe_path_policy_accepts_only_canonical_runtime_scope(tmp_path: Path) -> None:
    _prepare_allowed_root(tmp_path)
    report = evaluate_path_policy(
        project_root=tmp_path,
        profile=_enabled_profile(),
    )

    assert report.status == "ok"
    assert report.paths_are_canonical is True
    assert report.paths_within_allowed_root is True
    assert report.symlink_detected is False


def test_path_policy_blocks_traversal(tmp_path: Path) -> None:
    _prepare_allowed_root(tmp_path)
    profile = _enabled_profile(ledger_path="../escaped.jsonl")

    report = evaluate_path_policy(project_root=tmp_path, profile=profile)

    assert report.status == "blocked"
    assert report.reason == "unsafe_relative_path"


def test_path_policy_blocks_noncanonical_root(tmp_path: Path) -> None:
    (tmp_path / "other").mkdir()
    profile = _enabled_profile(
        allowed_root="other",
        ledger_path="other/ledger.jsonl",
        health_path="other/health.json",
    )

    report = evaluate_path_policy(project_root=tmp_path, profile=profile)

    assert report.status == "blocked"
    assert report.reason == "allowed_root_not_canonical"


def test_preflight_blocks_default_profile_without_constructing_writer(tmp_path: Path) -> None:
    report = run_writer_preflight(
        project_root=tmp_path,
        profile=PaperRuntimeWriterProfileV1(),
        identity=_non_root_identity(),
    )

    assert report.status == "blocked"
    assert report.reason == "profile_disabled_by_default"
    assert report.writer_creation_allowed is False
    assert report.writer_factory_invoked is False
    assert report.write_performed is False


def test_preflight_blocks_root_even_when_profile_enabled(tmp_path: Path) -> None:
    _prepare_allowed_root(tmp_path)
    report = run_writer_preflight(
        project_root=tmp_path,
        profile=_enabled_profile(),
        identity=_root_identity(),
    )

    assert report.status == "blocked"
    assert report.writer_creation_allowed is False
    assert next(check for check in report.checks if check.check_id == "non_root_identity").reason == (
        "root_identity_detected"
    )


def test_preflight_blocks_unverifiable_identity(tmp_path: Path) -> None:
    _prepare_allowed_root(tmp_path)
    identity = RuntimeIdentityEvidenceV1(
        source="test",
        verified=False,
        elevated=None,
        effective_uid=None,
        reason="non_root_identity_unverifiable",
    )

    report = run_writer_preflight(
        project_root=tmp_path,
        profile=_enabled_profile(),
        identity=identity,
    )

    assert report.status == "blocked"
    assert report.writer_creation_allowed is False


def test_preflight_blocks_missing_runtime_root_without_creating_it(tmp_path: Path) -> None:
    report = run_writer_preflight(
        project_root=tmp_path,
        profile=_enabled_profile(),
        identity=_non_root_identity(),
    )

    assert report.status == "blocked"
    assert report.path_policy.allowed_root_exists is False
    assert not (tmp_path / "data").exists()


def test_preflight_ready_binds_profile_hash(tmp_path: Path) -> None:
    _prepare_allowed_root(tmp_path)
    profile = _enabled_profile()

    report = run_writer_preflight(
        project_root=tmp_path,
        profile=profile,
        identity=_non_root_identity(),
    )

    assert report.status == "ready"
    assert report.writer_creation_allowed is True
    assert report.profile_sha256 == profile_sha256(profile)


def test_factory_does_not_call_constructor_when_preflight_is_blocked(tmp_path: Path) -> None:
    profile = PaperRuntimeWriterProfileV1()
    preflight = run_writer_preflight(
        project_root=tmp_path,
        profile=profile,
        identity=_non_root_identity(),
    )

    def forbidden_constructor(**_kwargs: object):
        raise AssertionError("constructor_must_not_be_called")

    outcome = create_paper_runtime_writer(
        profile=profile,
        preflight=preflight,
        writer_constructor=forbidden_constructor,  # type: ignore[arg-type]
    )

    assert outcome.writer is None
    assert outcome.report.status == "blocked"
    assert outcome.report.writer_created is False


def test_factory_creates_writer_only_after_ready_preflight(tmp_path: Path) -> None:
    allowed_root = _prepare_allowed_root(tmp_path)
    profile = _enabled_profile()
    preflight = run_writer_preflight(
        project_root=tmp_path,
        profile=profile,
        identity=_non_root_identity(),
    )

    outcome = create_paper_runtime_writer(profile=profile, preflight=preflight)

    assert outcome.report.status == "created"
    assert outcome.report.writer_created is True
    assert outcome.report.runtime_wiring_performed is False
    assert outcome.report.write_performed is False
    assert outcome.writer is not None
    assert outcome.writer.allowed_root == allowed_root.resolve()
    assert outcome.writer.design_only is False
    assert not outcome.writer.ledger_path.exists()
    assert not outcome.writer.health_path.exists()


def test_factory_rejects_preflight_from_another_profile(tmp_path: Path) -> None:
    _prepare_allowed_root(tmp_path)
    first = _enabled_profile()
    preflight = run_writer_preflight(
        project_root=tmp_path,
        profile=first,
        identity=_non_root_identity(),
    )
    second = _enabled_profile(
        durability={"lock_timeout_seconds": 3.0},
    )

    outcome = create_paper_runtime_writer(profile=second, preflight=preflight)

    assert outcome.writer is None
    assert outcome.report.reason == "stale_or_mismatched_preflight"


def test_quarantine_contract_is_deterministic_immutable_and_non_operational() -> None:
    kwargs = {
        "event_id": "event-001",
        "interrupted_at_utc": datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc),
        "interruption_stage": "append",
        "error_type": "LedgerWriteError",
        "error_message_sha256": "a" * 64,
        "payload_sha256": "b" * 64,
    }

    first = build_runtime_interruption_quarantine(**kwargs)  # type: ignore[arg-type]
    second = build_runtime_interruption_quarantine(**kwargs)  # type: ignore[arg-type]

    assert first == second
    assert first.schema_version == "runtime_interruption_quarantine_v1_1"
    assert first.quarantine_status == "quarantined"
    assert first.automatic_replay_allowed is False
    assert first.writer_resume_authorized is False
    assert first.runtime_integration_allowed is False
    assert first.operational_authority is False
    assert first.sends_orders is False
    assert first.changes_risk is False
    with pytest.raises(ValidationError):
        first.quarantine_status = "released"  # type: ignore[misc]


def test_quarantine_rejects_non_utc_and_invalid_hash() -> None:
    with pytest.raises(ValidationError, match="offset_zero"):
        build_runtime_interruption_quarantine(
            event_id="event-001",
            interrupted_at_utc=datetime.fromisoformat("2026-07-21T09:00:00-03:00"),
            interruption_stage="append",
            error_type="LedgerWriteError",
            error_message_sha256="a" * 64,
        )
    with pytest.raises(ValidationError):
        build_runtime_interruption_quarantine(
            event_id="event-001",
            interrupted_at_utc=datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc),
            interruption_stage="append",
            error_type="LedgerWriteError",
            error_message_sha256="not-a-hash",
        )


def test_validator_cli_is_no_write_and_keeps_default_disabled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = tuple(tmp_path.rglob("*"))

    exit_code = validator_cli.main(["--project-root", str(tmp_path), "--json"])
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["status"] == "ok"
    assert report["decision"] == "KEEP_WRITER_DISABLED"
    assert report["profile_enabled"] is False
    assert report["writer_factory_invoked"] is False
    assert report["writer_created"] is False
    assert report["writes_runtime"] is False
    assert tuple(tmp_path.rglob("*")) == before


def test_quarantine_cli_builds_in_memory_only(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = quarantine_cli.main(
        [
            "--event-id",
            "event-001",
            "--interrupted-at-utc",
            "2026-07-21T12:00:00Z",
            "--interruption-stage",
            "health_update",
            "--error-type",
            "LedgerHealthError",
            "--error-message-sha256",
            "a" * 64,
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["status"] == "ok"
    assert report["quarantine"]["schema_version"] == "runtime_interruption_quarantine_v1_1"
    assert report["write_performed"] is False
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["sends_orders"] is False


def test_new_python_surface_has_no_runtime_or_exchange_integration_imports() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = [
        *(
            root / "smartcrypto/execution/decision_ledger_paper_runtime_writer_v1"
        ).glob("*.py"),
        root / "scripts/validate_decision_ledger_paper_runtime_writer_profile_v1.py",
        root / "scripts/build_runtime_interruption_quarantine_v1_1.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths).casefold()

    for forbidden in (
        "import ccxt",
        "from ccxt",
        "import freqtrade",
        "from freqtrade",
        "import docker",
        "from docker",
        "import subprocess",
        "from subprocess",
        "riskmanager",
        "signal_producer",
    ):
        assert forbidden not in source
