from __future__ import annotations

import json
import os
import socket
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts.validate_credential_rotation_attestation_v1 import main
from smartcrypto.security.credential_rotation_attestation import loader as loader_module
from smartcrypto.security.credential_rotation_attestation import validator as validator_module
from smartcrypto.security.credential_rotation_attestation.validator import (
    validate_credential_rotation_attestation_v1,
)

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
RESULT_FIELDS = {
    "credential_id",
    "credential_category",
    "provider",
    "required_action",
    "rotation_status",
    "completed_at_utc",
    "verified_at_utc",
    "operator_role",
    "reviewer_role",
    "verification_method",
    "sanitized_evidence_reference",
    "status",
    "reason",
}


def inventory_payload() -> dict[str, Any]:
    return {
        "schema_version": "credential_rotation_required_inventory_v1",
        "incident_reference": "SEC-2026-101",
        "generated_at_utc": "2026-07-09T10:00:00Z",
        "required_credentials": [
            {
                "credential_id": "provider-primary",
                "credential_category": "notification-provider",
                "provider": "synthetic-provider",
                "affected_scope": "paper-alerting",
                "required_action": "revoke_or_rotate",
            }
        ],
    }


def attestation_item(status: str = "revoked") -> dict[str, Any]:
    return {
        "credential_id": "provider-primary",
        "credential_category": "notification-provider",
        "provider": "synthetic-provider",
        "rotation_status": status,
        "completed_at_utc": "2026-07-08T10:00:00Z",
        "verified_at_utc": "2026-07-09T10:00:00Z",
        "operator_role": "security_operator",
        "reviewer_role": "security_reviewer",
        "verification_method": "provider_console",
        "sanitized_evidence_reference": "SEC-2026-101-ITEM-1",
        "sanitized_notes": "Synthetic sanitized operational statement.",
    }


def attestation_payload(status: str = "revoked") -> dict[str, Any]:
    return {
        "schema_version": "credential_rotation_attestation_v1",
        "incident_reference": "SEC-2026-101",
        "generated_at_utc": "2026-07-09T11:00:00Z",
        "attestations": [attestation_item(status)],
    }


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_inputs(
    root: Path,
    *,
    inventory: dict[str, Any] | None = None,
    attestation: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    inventory_path = write_json(root / "inputs" / "inventory.json", inventory or inventory_payload())
    attestation_path = write_json(root / "inputs" / "attestation.json", attestation or attestation_payload())
    return inventory_path, attestation_path


def run_validated(
    root: Path,
    *,
    inventory: dict[str, Any] | None = None,
    attestation: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    inventory_path, attestation_path = write_inputs(root, inventory=inventory, attestation=attestation)
    return validate_credential_rotation_attestation_v1(
        project_root=root,
        required_inventory_path=inventory_path,
        attestation_path=attestation_path,
        now_utc=NOW,
        **kwargs,
    )


def test_default_without_inputs_is_blocked(tmp_path: Path, capsys: Any) -> None:
    exit_code = main(["--project-root", str(tmp_path), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert report["decision"] == "BLOCKED_INPUT_NOT_FOUND"


def test_default_does_not_write(tmp_path: Path) -> None:
    report = validate_credential_rotation_attestation_v1(project_root=tmp_path, now_utc=NOW)
    assert report["write_performed"] is False
    assert not (tmp_path / "data").exists()


def test_missing_inventory_is_blocked(tmp_path: Path) -> None:
    attestation = write_json(tmp_path / "attestation.json", attestation_payload())
    report = validate_credential_rotation_attestation_v1(
        project_root=tmp_path,
        attestation_path=attestation,
        now_utc=NOW,
    )
    assert report["decision"] == "BLOCKED_INPUT_NOT_FOUND"


def test_missing_attestation_is_blocked(tmp_path: Path) -> None:
    inventory = write_json(tmp_path / "inventory.json", inventory_payload())
    report = validate_credential_rotation_attestation_v1(
        project_root=tmp_path,
        required_inventory_path=inventory,
        now_utc=NOW,
    )
    assert report["decision"] == "BLOCKED_INPUT_NOT_FOUND"


def test_invalid_json_is_blocked(tmp_path: Path) -> None:
    inventory = write_text(tmp_path / "inventory.json", "{not-json")
    attestation = write_json(tmp_path / "attestation.json", attestation_payload())
    report = validate_credential_rotation_attestation_v1(
        project_root=tmp_path,
        required_inventory_path=inventory,
        attestation_path=attestation,
        now_utc=NOW,
    )
    assert report["decision"] == "BLOCKED_REQUIRED_INVENTORY_INVALID"


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_invalid_schema_version_is_blocked(tmp_path: Path) -> None:
    inventory = inventory_payload()
    inventory["schema_version"] = "wrong"
    report = run_validated(tmp_path, inventory=inventory)
    assert report["decision"] == "BLOCKED_REQUIRED_INVENTORY_INVALID"


def test_incident_reference_mismatch_is_blocked(tmp_path: Path) -> None:
    attestation = attestation_payload()
    attestation["incident_reference"] = "SEC-2026-999"
    report = run_validated(tmp_path, attestation=attestation)
    assert report["decision"] == "BLOCKED_INCIDENT_REFERENCE_MISMATCH"


def test_missing_required_credential_is_blocked(tmp_path: Path) -> None:
    attestation = attestation_payload()
    attestation["attestations"] = []
    report = run_validated(tmp_path, attestation=attestation)
    assert report["decision"] == "BLOCKED_REQUIRED_CREDENTIAL_MISSING"
    assert report["missing_credential_count"] == 1


def test_unknown_credential_is_blocked(tmp_path: Path) -> None:
    attestation = attestation_payload()
    unknown = deepcopy(attestation_item())
    unknown["credential_id"] = "unknown-provider"
    unknown["credential_category"] = "unknown-category"
    attestation["attestations"].append(unknown)
    report = run_validated(tmp_path, attestation=attestation)
    assert report["decision"] == "BLOCKED_UNKNOWN_CREDENTIAL"
    assert report["unknown_credential_count"] == 1


def test_duplicate_inventory_category_is_blocked(tmp_path: Path) -> None:
    inventory = inventory_payload()
    second = deepcopy(inventory["required_credentials"][0])
    second["credential_id"] = "provider-secondary"
    inventory["required_credentials"].append(second)
    report = run_validated(tmp_path, inventory=inventory)
    assert report["decision"] == "BLOCKED_DUPLICATE_CREDENTIAL"


def test_duplicate_attestation_credential_is_blocked(tmp_path: Path) -> None:
    attestation = attestation_payload()
    attestation["attestations"].append(deepcopy(attestation["attestations"][0]))
    report = run_validated(tmp_path, attestation=attestation)
    assert report["decision"] == "BLOCKED_DUPLICATE_CREDENTIAL"


def test_unverified_is_blocked(tmp_path: Path) -> None:
    report = run_validated(tmp_path, attestation=attestation_payload("unverified"))
    assert report["decision"] == "BLOCKED_UNVERIFIED_CREDENTIAL"
    assert report["unverified_count"] == 1


def test_valid_revoked_is_accepted(tmp_path: Path) -> None:
    report = run_validated(tmp_path, attestation=attestation_payload("revoked"))
    assert report["decision"] == "ROTATION_ATTESTATION_COMPLETE"
    assert report["revoked_count"] == 1


def test_valid_rotated_is_accepted(tmp_path: Path) -> None:
    report = run_validated(tmp_path, attestation=attestation_payload("rotated"))
    assert report["decision"] == "ROTATION_ATTESTATION_COMPLETE"
    assert report["rotated_count"] == 1


def not_applicable_attestation(*, with_notes: bool) -> dict[str, Any]:
    payload = attestation_payload("not_applicable")
    item = payload["attestations"][0]
    item["operator_role"] = None
    item["completed_at_utc"] = None
    item["verified_at_utc"] = None
    item["verification_method"] = "documented_not_applicable"
    item["sanitized_notes"] = "Documented synthetic reason." if with_notes else None
    return payload


def test_not_applicable_without_justification_is_blocked(tmp_path: Path) -> None:
    report = run_validated(tmp_path, attestation=not_applicable_attestation(with_notes=False))
    assert report["decision"] == "BLOCKED_DUAL_CONTROL_INVALID"


def test_valid_not_applicable_is_accepted(tmp_path: Path) -> None:
    report = run_validated(tmp_path, attestation=not_applicable_attestation(with_notes=True))
    assert report["decision"] == "ROTATION_ATTESTATION_COMPLETE"
    assert report["not_applicable_count"] == 1


def test_same_operator_and_reviewer_is_blocked(tmp_path: Path) -> None:
    attestation = attestation_payload()
    attestation["attestations"][0]["reviewer_role"] = "security_operator"
    report = run_validated(tmp_path, attestation=attestation)
    assert report["decision"] == "BLOCKED_DUAL_CONTROL_INVALID"


def test_missing_operator_is_blocked(tmp_path: Path) -> None:
    attestation = attestation_payload()
    attestation["attestations"][0]["operator_role"] = None
    report = run_validated(tmp_path, attestation=attestation)
    assert report["decision"] == "BLOCKED_DUAL_CONTROL_INVALID"


def test_missing_reviewer_is_blocked(tmp_path: Path) -> None:
    attestation = attestation_payload()
    attestation["attestations"][0]["reviewer_role"] = None
    report = run_validated(tmp_path, attestation=attestation)
    assert report["decision"] == "BLOCKED_DUAL_CONTROL_INVALID"


def test_invalid_verification_method_is_blocked(tmp_path: Path) -> None:
    attestation = attestation_payload()
    attestation["attestations"][0]["verification_method"] = "automated_provider_call"
    report = run_validated(tmp_path, attestation=attestation)
    assert report["decision"] == "BLOCKED_ATTESTATION_INVALID"


def test_missing_verification_reference_is_blocked(tmp_path: Path) -> None:
    attestation = attestation_payload()
    attestation["attestations"][0]["sanitized_evidence_reference"] = None
    report = run_validated(tmp_path, attestation=attestation)
    assert report["decision"] == "BLOCKED_DUAL_CONTROL_INVALID"


def test_invalid_timestamp_is_blocked(tmp_path: Path) -> None:
    attestation = attestation_payload()
    attestation["attestations"][0]["completed_at_utc"] = "not-a-time"
    report = run_validated(tmp_path, attestation=attestation)
    assert report["decision"] == "BLOCKED_TIMESTAMP_INVALID"


def test_future_timestamp_is_blocked(tmp_path: Path) -> None:
    attestation = attestation_payload()
    attestation["attestations"][0]["verified_at_utc"] = "2026-07-11T10:00:00Z"
    report = run_validated(tmp_path, attestation=attestation)
    assert report["decision"] == "BLOCKED_TIMESTAMP_INVALID"


def test_verification_before_completion_is_blocked(tmp_path: Path) -> None:
    attestation = attestation_payload()
    attestation["attestations"][0]["completed_at_utc"] = "2026-07-09T11:00:00Z"
    attestation["attestations"][0]["verified_at_utc"] = "2026-07-09T10:00:00Z"
    report = run_validated(tmp_path, attestation=attestation)
    assert report["decision"] == "BLOCKED_TIMESTAMP_INVALID"


def test_stale_attestation_is_blocked(tmp_path: Path) -> None:
    attestation = attestation_payload()
    attestation["generated_at_utc"] = "2026-05-01T10:00:00Z"
    report = run_validated(tmp_path, attestation=attestation)
    assert report["decision"] == "BLOCKED_STALE_ATTESTATION"


def github_pat() -> str:
    return "gh" + "p_" + "A" * 36


def jwt_token() -> str:
    return "eyJ" + "B" * 12 + "." + "C" * 14 + "." + "D" * 14


def telegram_token() -> str:
    return "123456789:" + "E" * 30


@pytest.mark.parametrize(
    "synthetic_secret",
    [github_pat(), jwt_token(), telegram_token()],
    ids=["github-pat", "jwt", "telegram-token"],
)
def test_credential_id_secret_is_blocked(tmp_path: Path, synthetic_secret: str) -> None:
    inventory = inventory_payload()
    inventory["required_credentials"][0]["credential_id"] = synthetic_secret
    report = run_validated(tmp_path, inventory=inventory)
    assert report["decision"] == "BLOCKED_SECRET_MATERIAL_DETECTED"


@pytest.mark.parametrize(
    "synthetic_secret",
    [github_pat(), jwt_token(), telegram_token()],
    ids=["github-pat", "jwt", "telegram-token"],
)
def test_credential_category_secret_is_blocked(tmp_path: Path, synthetic_secret: str) -> None:
    inventory = inventory_payload()
    inventory["required_credentials"][0]["credential_category"] = synthetic_secret
    report = run_validated(tmp_path, inventory=inventory)
    assert report["decision"] == "BLOCKED_SECRET_MATERIAL_DETECTED"


@pytest.mark.parametrize(
    "field_name",
    [
        "credential_value",
        "credential_hash",
        "credential_fingerprint",
        "credential_prefix",
        "credential_suffix",
        "credential_secret",
    ],
)
def test_sensitive_credential_field_name_is_blocked(tmp_path: Path, field_name: str) -> None:
    attestation = attestation_payload()
    attestation["attestations"][0][field_name] = "synthetic-forbidden-metadata"
    report = run_validated(tmp_path, attestation=attestation)
    assert report["decision"] == "BLOCKED_SECRET_MATERIAL_DETECTED"


@pytest.mark.parametrize("secret", [github_pat(), jwt_token(), telegram_token()])
def test_synthetic_token_material_is_blocked(tmp_path: Path, secret: str) -> None:
    attestation = attestation_payload()
    attestation["attestations"][0]["sanitized_notes"] = "unsafe=" + secret
    report = run_validated(tmp_path, attestation=attestation)
    assert report["decision"] == "BLOCKED_SECRET_MATERIAL_DETECTED"
    assert report["secret_finding_count"] >= 1


def test_synthetic_authenticated_url_is_blocked(tmp_path: Path) -> None:
    attestation = attestation_payload()
    attestation["attestations"][0]["sanitized_notes"] = (
        "https://operator:" + "F" * 24 + "@provider.invalid/path"
    )
    report = run_validated(tmp_path, attestation=attestation)
    assert report["decision"] == "BLOCKED_SECRET_MATERIAL_DETECTED"


def test_declared_secret_hash_is_blocked(tmp_path: Path) -> None:
    attestation = attestation_payload()
    attestation["attestations"][0]["fingerprint"] = "a" * 64
    report = run_validated(tmp_path, attestation=attestation)
    assert report["decision"] == "BLOCKED_SECRET_MATERIAL_DETECTED"


def test_symlink_input_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inventory_path, attestation_path = write_inputs(tmp_path)
    original = Path.is_symlink

    def fake_is_symlink(path: Path) -> bool:
        return path == inventory_path or original(path)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    report = validate_credential_rotation_attestation_v1(
        project_root=tmp_path,
        required_inventory_path=inventory_path,
        attestation_path=attestation_path,
        now_utc=NOW,
    )
    assert report["decision"] == "BLOCKED_UNSAFE_INPUT_PATH"


def test_unsupported_extension_is_blocked(tmp_path: Path) -> None:
    inventory = write_text(tmp_path / "inventory.yaml", "safe: true")
    attestation = write_json(tmp_path / "attestation.json", attestation_payload())
    report = validate_credential_rotation_attestation_v1(
        project_root=tmp_path,
        required_inventory_path=inventory,
        attestation_path=attestation,
        now_utc=NOW,
    )
    assert report["decision"] == "BLOCKED_UNSAFE_INPUT_PATH"


def test_file_above_limit_is_blocked(tmp_path: Path) -> None:
    inventory, attestation = write_inputs(tmp_path)
    report = validate_credential_rotation_attestation_v1(
        project_root=tmp_path,
        required_inventory_path=inventory,
        attestation_path=attestation,
        max_file_bytes=10,
        now_utc=NOW,
    )
    assert report["decision"] == "BLOCKED_UNSAFE_INPUT_PATH"


def test_report_never_contains_synthetic_secret(tmp_path: Path) -> None:
    secret = github_pat()
    attestation = attestation_payload()
    attestation["attestations"][0]["sanitized_notes"] = secret
    report = run_validated(tmp_path, attestation=attestation)
    assert secret not in json.dumps(report, sort_keys=True)


def test_report_does_not_contain_raw_input_or_notes(tmp_path: Path) -> None:
    unique_note = "Synthetic statement excluded from safe report 7821."
    attestation = attestation_payload()
    attestation["attestations"][0]["sanitized_notes"] = unique_note
    report = run_validated(tmp_path, attestation=attestation)
    serialized = json.dumps(report, sort_keys=True)
    assert unique_note not in serialized
    assert all(set(item) == RESULT_FIELDS for item in report["credential_results"])


def test_write_report_only_under_data_reports(tmp_path: Path) -> None:
    inventory, attestation = write_inputs(tmp_path)
    report = validate_credential_rotation_attestation_v1(
        project_root=tmp_path,
        required_inventory_path=inventory,
        attestation_path=attestation,
        write_report=True,
        now_utc=NOW,
    )
    assert report["write_performed"] is True
    assert (tmp_path / "data" / "reports" / "credential_rotation_attestation_gate_v1.json").is_file()
    assert not (tmp_path / "data" / "runtime").exists()


def test_write_failure_leaves_no_partial_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inventory, attestation = write_inputs(tmp_path)
    report_path = tmp_path / "data" / "reports" / "credential_rotation_attestation_gate_v1.json"

    def fail_write(path: Path, payload: Any) -> None:
        del path, payload
        raise OSError("synthetic write failure")

    monkeypatch.setattr(validator_module, "write_safe_report", fail_write)
    report = validate_credential_rotation_attestation_v1(
        project_root=tmp_path,
        required_inventory_path=inventory,
        attestation_path=attestation,
        write_report=True,
        now_utc=NOW,
    )
    assert report["write_performed"] is False
    assert not report_path.exists()
    assert not list(report_path.parent.glob(".*.tmp")) if report_path.parent.exists() else True


def test_all_required_items_resolved_is_complete(tmp_path: Path) -> None:
    inventory = inventory_payload()
    second = deepcopy(inventory["required_credentials"][0])
    second.update(
        credential_id="provider-secondary",
        credential_category="data-provider",
        provider="synthetic-provider-two",
    )
    inventory["required_credentials"].append(second)
    attestation = attestation_payload("rotated")
    second_attestation = deepcopy(attestation["attestations"][0])
    second_attestation.update(
        credential_id="provider-secondary",
        credential_category="data-provider",
        provider="synthetic-provider-two",
        rotation_status="revoked",
        sanitized_evidence_reference="SEC-2026-101-ITEM-2",
    )
    attestation["attestations"].append(second_attestation)
    report = run_validated(tmp_path, inventory=inventory, attestation=attestation)
    assert report["decision"] == "ROTATION_ATTESTATION_COMPLETE"
    assert report["all_required_credentials_resolved"] is True
    assert report["rotation_attestation_complete"] is True


def test_safety_flags_are_conservative(tmp_path: Path) -> None:
    report = validate_credential_rotation_attestation_v1(project_root=tmp_path, now_utc=NOW)
    assert report["paper_only"] is True
    assert report["security_only"] is True
    assert report["read_only"] is True
    for field in (
        "live_trading_enabled",
        "canary_release_allowed",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "changes_model",
        "runs_training",
        "writes_runtime",
        "writes_feedback",
        "writes_sqlite",
        "writes_parquet",
        "writes_models",
        "writes_registries",
        "rotates_credentials",
        "revokes_credentials",
        "calls_provider_apis",
        "reads_environment_secrets",
    ):
        assert report[field] is False


def test_no_network_call_is_made(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_network(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket.socket, "connect", fail_network)
    report = run_validated(tmp_path)
    assert report["decision"] == "ROTATION_ATTESTATION_COMPLETE"


def test_no_sensitive_environment_variable_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_getenv(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("environment read attempted")

    monkeypatch.setattr(os, "getenv", fail_getenv)
    report = run_validated(tmp_path)
    assert report["decision"] == "ROTATION_ATTESTATION_COMPLETE"


def test_serialized_paths_use_posix_separator(tmp_path: Path) -> None:
    report = run_validated(tmp_path)
    assert "\\" not in report["required_inventory_path"]
    assert "\\" not in report["attestation_path"]
    assert "\\" not in report["report_path"]


def test_existing_redaction_scanner_is_called_before_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inventory, attestation = write_inputs(tmp_path)
    calls: list[Path] = []
    original = loader_module.scan_source

    def observed_scan(path: Path, **kwargs: Any) -> Any:
        calls.append(path)
        return original(path, **kwargs)

    monkeypatch.setattr(loader_module, "scan_source", observed_scan)
    report = validate_credential_rotation_attestation_v1(
        project_root=tmp_path,
        required_inventory_path=inventory,
        attestation_path=attestation,
        now_utc=NOW,
    )
    assert report["decision"] == "ROTATION_ATTESTATION_COMPLETE"
    assert calls == [inventory, attestation]


def test_write_outside_allowed_root_is_blocked(tmp_path: Path) -> None:
    inventory, attestation = write_inputs(tmp_path)
    report = validate_credential_rotation_attestation_v1(
        project_root=tmp_path,
        required_inventory_path=inventory,
        attestation_path=attestation,
        write_report=True,
        report_path=tmp_path / "outside.json",
        now_utc=NOW,
    )
    assert report["decision"] == "BLOCKED_WRITE_OUTSIDE_ALLOWED_ROOT"
    assert report["write_performed"] is False
