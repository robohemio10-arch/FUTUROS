from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_state_execution_ledger_boundary.py"
POLICY = ROOT / "docs" / "STATE_EXECUTION_LEDGER_BOUNDARY_AUDIT_V1.md"

SCOPED_AUTHORITY_CASES = (
    (
        "scripts/validate_decision_ledger_payload_v4_2.py",
        "_atomic_write_json",
        "sandbox_validation_artifact_writer",
        "decision_ledger_payload_validation_artifact_writer",
    ),
    (
        "scripts/validate_decision_ledger_runtime_integration_v1.py",
        "write_json",
        "sandbox_validation_artifact_writer",
        "decision_ledger_runtime_integration_validation_artifact_writer",
    ),
    (
        "scripts/validate_decision_ledger_runtime_profile_v1.py",
        "atomic_write_json",
        "sandbox_validation_artifact_writer",
        "decision_ledger_runtime_profile_validation_artifact_writer",
    ),
    (
        "smartcrypto/execution/decision_ledger_runtime_profile_v1/schema.py",
        "write_runtime_profile_schema",
        "design_schema_artifact_writer",
        "decision_ledger_runtime_profile_schema_writer",
    ),
    (
        "smartcrypto/execution/decision_ledger_v4_2/schema.py",
        "write_payload_json_schema",
        "design_schema_artifact_writer",
        "decision_ledger_payload_schema_writer",
    ),
)


def load_auditor() -> Any:
    spec = importlib.util.spec_from_file_location("state_execution_ledger_boundary_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_python(root: Path, relative: str, source: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def write_policy(root: Path) -> None:
    target = root / "docs" / "STATE_EXECUTION_LEDGER_BOUNDARY_AUDIT_V1.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "policy_status: active\n"
        "paper_only: true\nshadow_only: true\n"
        "live_trading_enabled: false\norder_submission_enabled: false\n"
        "real_order_submission_enabled: false\nexchange_private_access: false\n"
        "sends_orders: false\nchanges_risk: false\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("relative_path", "function_name", "classification", "authority_id"),
    SCOPED_AUTHORITY_CASES,
)
def test_exact_scoped_decision_ledger_authority_is_ok(
    tmp_path: Path,
    relative_path: str,
    function_name: str,
    classification: str,
    authority_id: str,
) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_python(
        tmp_path,
        relative_path,
        "from pathlib import Path\n"
        f"def {function_name}():\n"
        "    Path('sandbox_artifact.json').write_text('{}', encoding='utf-8')\n",
    )

    report = module.audit_project(tmp_path)

    assert report["status"] == "ok"
    assert report["high_count"] == 0
    writer = report["writer_targets"][0]
    assert writer["severity"] == "ok"
    assert writer["classification"] == classification
    assert writer["authority"] == authority_id
    assert writer["boundary"] == "sandbox_design_only"
    assert writer["allowed_operations"] == ["write_text"]
    assert writer["runtime_authority"] is False
    assert writer["operational_state_authority"] is False
    assert writer["financial_ledger_authority"] is False
    assert writer["paper_restart_authority"] is False


def test_unauthorized_function_in_scoped_file_remains_high(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    relative_path, function_name, _, _ = SCOPED_AUTHORITY_CASES[0]
    write_python(
        tmp_path,
        relative_path,
        "from pathlib import Path\n"
        f"def {function_name}():\n"
        "    Path('sandbox_artifact.json').write_text('{}')\n"
        "def unauthorized_writer():\n"
        "    Path('parallel_ledger.json').write_text('{}')\n",
    )

    report = module.audit_project(tmp_path)

    writers = {item["function_or_class"]: item for item in report["writer_targets"]}
    assert writers[function_name]["severity"] == "ok"
    assert writers["unauthorized_writer"]["severity"] == "high"
    assert writers["unauthorized_writer"]["scoped_authority"] is False


def test_different_operation_in_scoped_function_remains_high(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    relative_path, function_name, _, _ = SCOPED_AUTHORITY_CASES[1]
    write_python(
        tmp_path,
        relative_path,
        "from pathlib import Path\n"
        f"def {function_name}():\n"
        "    Path('parallel_ledger.bin').write_bytes(b'ledger')\n",
    )

    report = module.audit_project(tmp_path)

    writer = report["writer_targets"][0]
    assert writer["operation"] == "write_bytes"
    assert writer["severity"] == "high"
    assert writer["scoped_authority"] is False


def test_similar_sibling_file_remains_high(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_python(
        tmp_path,
        "scripts/validate_decision_ledger_payload_v4_2_copy.py",
        "from pathlib import Path\n"
        "def _atomic_write_json():\n"
        "    Path('parallel_ledger.json').write_text('{}')\n",
    )

    report = module.audit_project(tmp_path)

    assert report["writer_targets"][0]["severity"] == "high"
    assert report["writer_targets"][0]["scoped_authority"] is False


def test_rogue_decision_ledger_writer_remains_high(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_python(
        tmp_path,
        "smartcrypto/execution/rogue_decision_ledger_writer.py",
        "from pathlib import Path\n"
        "def persist():\n"
        "    Path('parallel_ledger.json').write_text('{}')\n",
    )

    report = module.audit_project(tmp_path)

    assert report["status"] == "blocked"
    assert report["writer_targets"][0]["severity"] == "high"


def test_detects_ambiguous_runtime_ledger_writer(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_python(
        tmp_path,
        "smartcrypto/execution/rogue_writer.py",
        "from pathlib import Path\n"
        "def persist():\n"
        "    Path('data/runtime/financial_ledger.json').write_text('{}', encoding='utf-8')\n",
    )

    report = module.audit_project(tmp_path)

    assert report["status"] == "blocked"
    writer = report["writer_targets"][0]
    assert writer["classification"] == "ambiguous_state_or_ledger_writer"
    assert writer["severity"] == "high"
    assert writer["authority"] == "none"


def test_dashboard_snapshot_consumer_is_read_only(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_python(
        tmp_path,
        "smartcrypto/dashboard/snapshot_reader.py",
        "import json\nfrom pathlib import Path\n"
        "def load():\n    return json.loads(Path('data/reports/snapshot.json').read_text())\n",
    )

    report = module.audit_project(tmp_path)

    assert report["status"] == "ok"
    assert report["modules"][0]["role"] == "read_only_consumer"
    assert report["writer_targets"] == []


def test_ops_report_writer_is_allowed(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_python(
        tmp_path,
        "smartcrypto/ops/sample_report.py",
        "from pathlib import Path\n"
        "REPORT_PATH = Path('data/reports/sample.json')\n"
        "def write_report():\n    REPORT_PATH.write_text('{}', encoding='utf-8')\n",
    )

    report = module.audit_project(tmp_path)

    assert report["status"] == "ok"
    assert report["writer_targets"][0]["classification"] == "report_writer"


def test_improper_state_to_execution_import_is_blocked(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_python(
        tmp_path,
        "smartcrypto/state/bad_dependency.py",
        "from smartcrypto.execution.order_manager import OrderManager\n",
    )

    report = module.audit_project(tmp_path)

    assert report["status"] == "blocked"
    finding = report["boundary_findings"][0]
    assert finding["classification"] == "improper_state_to_execution_dependency"
    assert finding["severity"] == "high"


def test_report_is_deterministic(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_python(tmp_path, "smartcrypto/state/reader.py", "def read_state(value):\n    return value\n")

    assert module.audit_project(tmp_path) == module.audit_project(tmp_path)


def test_report_contract_and_safety_flags(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_python(tmp_path, "smartcrypto/ops/reader.py", "def read(value):\n    return value\n")

    report = module.audit_project(tmp_path)

    required = {
        "schema_version", "status", "reason", "scanned_files", "modules",
        "boundary_findings", "writer_targets", "cross_domain_imports",
        "authority_map", "counts", "policy_documented",
    }
    assert required <= report.keys()
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["runtime_integration_allowed"] is False
    assert report["paper_restart_authorized"] is False
    assert report["canary_release_allowed"] is False
    assert report["live_release_allowed"] is False


def test_scoped_authority_registry_contains_only_literal_exact_keys() -> None:
    module = load_auditor()

    expected = {
        (
            "scripts/validate_decision_ledger_payload_v4_2.py",
            "_atomic_write_json",
            "decision_ledger_payload_validation_artifact_writer",
            "sandbox_validation_artifact_writer",
        ),
        (
            "scripts/validate_decision_ledger_runtime_integration_v1.py",
            "write_json",
            "decision_ledger_runtime_integration_validation_artifact_writer",
            "sandbox_validation_artifact_writer",
        ),
        (
            "scripts/validate_decision_ledger_runtime_profile_v1.py",
            "atomic_write_json",
            "decision_ledger_runtime_profile_validation_artifact_writer",
            "sandbox_validation_artifact_writer",
        ),
        (
            "smartcrypto/execution/decision_ledger_runtime_profile_v1/schema.py",
            "write_runtime_profile_schema",
            "decision_ledger_runtime_profile_schema_writer",
            "design_schema_artifact_writer",
        ),
        (
            "smartcrypto/execution/decision_ledger_v4_2/schema.py",
            "write_payload_json_schema",
            "decision_ledger_payload_schema_writer",
            "design_schema_artifact_writer",
        ),
        (
            "scripts/generate_hashed_lock_v1.py",
            "main",
            "hermetic_lock_generation_artifact_writer",
            "sandbox_validation_artifact_writer",
        ),
        (
            "scripts/pip_report_to_lock_v1.py",
            "main",
            "pip_resolution_lock_artifact_writer",
            "sandbox_validation_artifact_writer",
        ),
    }
    observed = {
        (
            authority.path,
            authority.function_or_class,
            authority.authority_id,
            authority.classification,
        )
        for authority in module.SCOPED_WRITER_AUTHORITIES
    }

    assert observed == expected
    for authority in module.SCOPED_WRITER_AUTHORITIES:
        assert not any(marker in authority.path for marker in ("*", "?", "[", "]"))
        assert authority.function_or_class
        assert authority.allowed_operations == frozenset({"write_text"})
        assert authority.boundary == "sandbox_design_only"
        assert authority.runtime_authority is False
        assert authority.operational_state_authority is False
        assert authority.financial_ledger_authority is False
        assert authority.paper_restart_authority is False


def test_auditor_does_not_import_or_execute_scanned_modules(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    marker = tmp_path / "executed.txt"
    write_python(
        tmp_path,
        "smartcrypto/execution/dangerous.py",
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nraise RuntimeError('no')\n",
    )

    module.audit_project(tmp_path)

    assert not marker.exists()


def test_auditor_source_is_static_and_has_no_external_operations() -> None:
    source = SCRIPT.read_text(encoding="utf-8").lower()

    assert "import requests" not in source
    assert "urllib.request" not in source
    assert "import ccxt" not in source
    assert "import docker" not in source
    assert "notificationdispatcher" not in source
    assert "shell=true" not in source


def test_cli_emits_controlled_json_and_fail_on_high(tmp_path: Path) -> None:
    write_policy(tmp_path)
    write_python(
        tmp_path,
        "smartcrypto/state/bad.py",
        "from smartcrypto.execution.order_manager import OrderManager\n",
    )

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--json", "--fail-on", "high"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert payload["status"] == "blocked"
    assert payload["high_count"] == 1


def test_real_repository_is_ok_or_warning_without_high_or_critical() -> None:
    module = load_auditor()

    report = module.audit_project(ROOT)

    assert report["status"] in {"ok", "warning"}
    assert report["high_count"] == 0
    assert report["critical_count"] == 0
    assert report["policy_documented"] is True


def test_policy_exists_and_preserves_paper_shadow_only() -> None:
    text = POLICY.read_text(encoding="utf-8").lower()

    assert "policy_status: active" in text
    assert "paper_only: true" in text
    assert "shadow_only: true" in text
    assert "live_trading_enabled: false" in text
    assert "order_submission_enabled: false" in text
    assert "real_order_submission_enabled: false" in text
