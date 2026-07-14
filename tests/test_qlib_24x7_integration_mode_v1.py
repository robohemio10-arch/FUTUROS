from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from smartcrypto.learning.qlib_backend_environment_lock.integration_mode import (
    MODE_NATIVE_PROVIDER,
    TRANSPORT_NATIVE_PROVIDER,
    build_qlib_24x7_integration_mode_report,
    validate_qlib_integration_mode_contract,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "config/qlib_integration_mode_v1.json"
CLI = ROOT / "scripts/audit_qlib_24x7_integration_mode_v1.py"
MODULE = (
    ROOT
    / "smartcrypto/learning/qlib_backend_environment_lock/integration_mode.py"
)


def official_contract() -> dict[str, Any]:
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def write_contract(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def run_cli(
    project_root: Path,
    contract: Path,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--project-root",
            str(project_root),
            "--contract",
            str(contract),
            "--json",
            *extra,
        ],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_official_contract_is_valid_and_approved() -> None:
    report = build_qlib_24x7_integration_mode_report(project_root=ROOT)

    assert report["status"] == "ok"
    assert report["reason"] == "qlib_24x7_integration_mode_contract_valid"
    assert report["decision"] == "QLIB_MODE_A_APPROVED_RESEARCH_ONLY"
    assert report["qlib_adr_status"] == "approved"
    assert report["contract_valid"] is True
    assert report["validation_errors"] == []


def test_mode_a_contract_is_consistent() -> None:
    contract = official_contract()

    assert validate_qlib_integration_mode_contract(contract) == []
    assert contract["selected_mode"] == "model_zoo_versioned_parquet"
    assert contract["calendar_mode"] == "continuous_crypto_24x7_utc"
    assert contract["dataset_transport"] == "versioned_parquet"
    assert contract["provider_runtime_authority"] is False


def test_unknown_mode_is_blocked() -> None:
    contract = official_contract()
    contract["selected_mode"] = "unknown"

    assert "unknown_selected_mode" in validate_qlib_integration_mode_contract(contract)


def test_non_utc_timezone_is_blocked() -> None:
    contract = official_contract()
    contract["timezone"] = "America/Sao_Paulo"

    assert "timezone_must_be_utc" in validate_qlib_integration_mode_contract(contract)


def test_non_24x7_calendar_is_blocked() -> None:
    contract = official_contract()
    contract["calendar_mode"] = "weekday_sessions"

    assert "calendar_must_be_continuous_crypto_24x7_utc" in (
        validate_qlib_integration_mode_contract(contract)
    )


@pytest.mark.parametrize(
    "field",
    [
        "dataset_manifest_required",
        "feature_contract_required",
        "label_contract_required",
        "cost_model_required",
        "anti_leakage_contract_required",
    ],
)
def test_required_learning_contracts_cannot_be_absent(field: str) -> None:
    contract = official_contract()
    del contract[field]

    errors = validate_qlib_integration_mode_contract(contract)

    assert f"missing_required_field:{field}" in errors
    assert f"{field}_must_be_true" in errors


@pytest.mark.parametrize(
    "field",
    [
        "training_authorized",
        "model_promotion_authorized",
        "operational_authority",
        "provider_runtime_authority",
        "writes_runtime",
        "sends_orders",
        "exchange_private_access",
    ],
)
def test_operational_authority_flags_are_blocked(field: str) -> None:
    contract = official_contract()
    contract[field] = True

    assert f"{field}_must_be_false" in (
        validate_qlib_integration_mode_contract(contract)
    )


def test_mode_b_requires_complete_reproducible_evidence() -> None:
    contract = official_contract()
    contract["selected_mode"] = MODE_NATIVE_PROVIDER
    contract["dataset_transport"] = TRANSPORT_NATIVE_PROVIDER

    errors = validate_qlib_integration_mode_contract(contract)

    assert any(error.startswith("mode_b_evidence_gate_not_satisfied:") for error in errors)


def test_mode_b_is_only_valid_with_all_evidence_gates() -> None:
    contract = official_contract()
    contract["selected_mode"] = MODE_NATIVE_PROVIDER
    contract["dataset_transport"] = TRANSPORT_NATIVE_PROVIDER
    gates = contract["validation_gates"]
    assert isinstance(gates, dict)
    for gate in (
        "native_provider_24x7_evidence",
        "cross_platform_equivalence_evidence",
        "dataset_manifest_preservation_evidence",
        "feature_contract_preservation_evidence",
        "timezone_determinism_evidence",
        "provider_runtime_independence_evidence",
        "anti_leakage_calendar_equivalence_evidence",
    ):
        gates[gate] = True

    assert validate_qlib_integration_mode_contract(contract) == []


def test_cli_runs_without_pythonpath(tmp_path: Path) -> None:
    contract = tmp_path / "config/contract.json"
    write_contract(contract, official_contract())

    completed = run_cli(tmp_path, contract)
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["status"] == "ok"
    assert payload["contract_valid"] is True


def test_cli_creates_no_files(tmp_path: Path) -> None:
    contract = tmp_path / "config/contract.json"
    write_contract(contract, official_contract())
    sentinel = tmp_path / "data/sentinel.bin"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(b"unchanged")
    before = tree_hashes(tmp_path)

    completed = run_cli(tmp_path, contract)

    assert completed.returncode == 0
    assert tree_hashes(tmp_path) == before


def test_cli_rejects_write_flag(tmp_path: Path) -> None:
    contract = tmp_path / "contract.json"
    write_contract(contract, official_contract())

    completed = run_cli(tmp_path, contract, "--write")

    assert completed.returncode == 2
    assert "unrecognized arguments: --write" in completed.stderr


def test_report_is_idempotent() -> None:
    first = build_qlib_24x7_integration_mode_report(project_root=ROOT)
    second = build_qlib_24x7_integration_mode_report(project_root=ROOT)

    assert first == second


def test_official_contract_remains_byte_identical() -> None:
    before = CONTRACT.read_bytes()

    build_qlib_24x7_integration_mode_report(project_root=ROOT)

    assert CONTRACT.read_bytes() == before


def test_missing_contract_returns_controlled_blocked(tmp_path: Path) -> None:
    report = build_qlib_24x7_integration_mode_report(
        project_root=tmp_path,
        contract_path="missing.json",
    )

    assert report["status"] == "blocked"
    assert report["contract_valid"] is False
    assert report["validation_errors"] == ["contract_file_missing"]
    assert report["write_performed"] is False


def test_report_preserves_all_no_authority_gates() -> None:
    report = build_qlib_24x7_integration_mode_report(project_root=ROOT)

    for field in (
        "operational_authority",
        "training_authorized",
        "model_promotion_authorized",
        "provider_runtime_authority",
        "qlib_runtime_update_authorized",
        "ai_shadow_runtime_update_authorized",
        "freqtrade_update_authorized",
        "risk_manager_update_authorized",
        "writes_runtime",
        "writes_active_model",
        "sends_orders",
        "exchange_private_access",
        "write_performed",
        "qlib_initialized",
        "models_loaded",
        "datasets_loaded",
    ):
        assert report[field] is False


def test_validator_module_has_no_qlib_or_dataset_runtime_imports() -> None:
    source = MODULE.read_text(encoding="utf-8")

    for forbidden in (
        "import qlib",
        "from qlib",
        "import pandas",
        "read_parquet",
        "joblib.load",
    ):
        assert forbidden not in source


def test_report_is_json_serializable() -> None:
    report = build_qlib_24x7_integration_mode_report(project_root=ROOT)

    assert json.loads(json.dumps(report, sort_keys=True)) == report


def test_contract_sha256_matches_file() -> None:
    report = build_qlib_24x7_integration_mode_report(project_root=ROOT)

    assert report["contract_sha256"] == hashlib.sha256(CONTRACT.read_bytes()).hexdigest()


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
