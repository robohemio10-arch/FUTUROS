from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "audit_current_dev_dev27_findings_replay_v1.py"


def load_module():
    spec = importlib.util.spec_from_file_location("dev27_audit", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_payload_has_audit_only_safety_flags(tmp_path: Path):
    module = load_module()
    payload = module.build_replay_audit(tmp_path)

    assert payload["schema_version"] == "current_dev_audit_replay_dev27_findings_v1"
    assert payload["decision"] == "AUDIT_ONLY_NO_CODE_FIX"
    assert payload["research_only"] is True
    assert payload["read_only"] is True
    assert payload["paper_only"] is True
    assert payload["shadow_only"] is True
    assert payload["operational_authority"] is False
    assert payload["fixes_applied"] is False
    assert payload["write_performed"] is False


def test_safe_empty_project_does_not_require_branch_00b(tmp_path: Path):
    module = load_module()
    payload = module.build_replay_audit(tmp_path)

    assert payload["branch_00b_required"] is False
    assert payload["dashboard_notification_findings"]["critical_count"] == 0
    assert payload["cli_standalone_findings"]["finding_count"] == 0


def test_dashboard_requests_post_is_blocked_and_requires_00b(tmp_path: Path):
    write_file(
        tmp_path / "smartcrypto/dashboard/pages/alerts.py",
        "import requests\n\ndef render():\n    requests.post('https://example.invalid')\n",
    )
    module = load_module()
    payload = module.build_replay_audit(tmp_path)

    assert payload["status"] == "blocked"
    assert payload["branch_00b_required"] is True
    assert payload["dashboard_notification_findings"]["critical_count"] == 1
    assert payload["dashboard_notification_findings"]["findings"][0]["finding_type"] == "requests_post"


def test_dashboard_httpx_post_is_detected(tmp_path: Path):
    write_file(
        tmp_path / "smartcrypto/dashboard/components/notify.py",
        "import httpx\n\ndef render():\n    return httpx.post('https://example.invalid')\n",
    )
    module = load_module()
    findings = module.audit_dashboard_notifications(tmp_path)

    assert findings["status"] == "blocked"
    assert findings["critical_count"] == 1
    assert findings["branch_00b_required"] is True


def test_dashboard_dry_run_false_is_high_warning(tmp_path: Path):
    write_file(
        tmp_path / "smartcrypto/dashboard/pages/notify.py",
        "def render():\n    dry_run=False\n    return dry_run\n",
    )
    module = load_module()
    findings = module.audit_dashboard_notifications(tmp_path)

    assert findings["status"] == "warning"
    assert findings["high_count"] == 1
    assert findings["branch_00b_required"] is True


def test_dashboard_readonly_stub_is_safe(tmp_path: Path):
    write_file(
        tmp_path / "smartcrypto/dashboard/pages/readonly.py",
        "def render():\n    return {'dry_run': True, 'send': False}\n",
    )
    module = load_module()
    findings = module.audit_dashboard_notifications(tmp_path)

    assert findings["status"] == "ok"
    assert findings["finding_count"] == 0


def test_cli_missing_bootstrap_medium_for_generic_script(tmp_path: Path):
    write_file(
        tmp_path / "scripts/build_report.py",
        "from smartcrypto.research.x import y\n\nif __name__ == '__main__':\n    print(y)\n",
    )
    module = load_module()
    findings = module.audit_cli_standalone(tmp_path)

    assert findings["status"] == "warning"
    assert findings["medium_count"] == 1
    assert findings["branch_00b_required"] is False


def test_cli_selector_missing_bootstrap_requires_00b(tmp_path: Path):
    write_file(
        tmp_path / "scripts/run_selector.py",
        "from smartcrypto.research.x import y\n\nif __name__ == '__main__':\n    print(y)\n",
    )
    module = load_module()
    findings = module.audit_cli_standalone(tmp_path)

    assert findings["status"] == "blocked"
    assert findings["high_count"] == 1
    assert findings["branch_00b_required"] is True


def test_cli_with_bootstrap_is_not_flagged(tmp_path: Path):
    write_file(
        tmp_path / "scripts/run_selector.py",
        "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
        "from smartcrypto.research.x import y\n\nif __name__ == '__main__':\n    print(y)\n",
    )
    module = load_module()
    findings = module.audit_cli_standalone(tmp_path)

    assert findings["finding_count"] == 0
    assert findings["branch_00b_required"] is False


def test_runtime_safety_presence_detects_artifacts(tmp_path: Path):
    write_file(tmp_path / "scripts/validate_runtime_safety_config.py", "print('ok')\n")
    module = load_module()
    findings = module.audit_runtime_safety_presence(tmp_path)

    assert findings["status"] == "ok"
    assert findings["executed"] is False
    assert findings["present_artifacts"] == ["scripts/validate_runtime_safety_config.py"]


def test_payload_combines_branch_00b_reason(tmp_path: Path):
    write_file(
        tmp_path / "scripts/run_selector.py",
        "from smartcrypto.research.x import y\nif __name__ == '__main__': print(y)\n",
    )
    write_file(
        tmp_path / "smartcrypto/dashboard/pages/a.py",
        "import requests\nrequests.post('https://example.invalid')\n",
    )
    module = load_module()
    payload = module.build_replay_audit(tmp_path)

    assert payload["branch_00b_required"] is True
    assert "cli_standalone_high_priority_findings" in payload["branch_00b_reason"]
    assert "dashboard_notification_side_effect_risk" in payload["branch_00b_reason"]


def test_no_write_does_not_create_output(tmp_path: Path):
    module = load_module()
    payload = module.build_replay_audit(tmp_path)
    output = tmp_path / "audit.json"
    updated = module.write_payload_if_requested(payload, str(output), tmp_path, no_write=True)

    assert updated["write_requested"] is True
    assert updated["write_performed"] is False
    assert not output.exists()


def test_forbidden_output_path_is_blocked(tmp_path: Path):
    module = load_module()
    payload = module.build_replay_audit(tmp_path)
    output = tmp_path / "data" / "reports" / "audit.json"
    updated = module.write_payload_if_requested(payload, str(output), tmp_path, no_write=False)

    assert updated["status"] == "blocked"
    assert updated["write_performed"] is False
    assert "output_path_under_forbidden_runtime_or_data_directory" in updated["validation_errors"]


def test_explicit_safe_output_path_writes_file(tmp_path: Path):
    module = load_module()
    payload = module.build_replay_audit(tmp_path)
    output = tmp_path.parent / f"{tmp_path.name}_audit.json"
    try:
        updated = module.write_payload_if_requested(payload, str(output), tmp_path, no_write=False)
        assert updated["write_performed"] is True
        assert output.exists()
        loaded = json.loads(output.read_text(encoding="utf-8"))
        assert loaded["schema_version"] == "current_dev_audit_replay_dev27_findings_v1"
    finally:
        output.unlink(missing_ok=True)


def test_cli_json_no_write_subprocess(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--project-root",
            str(tmp_path),
            "--no-write",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["schema_version"] == "current_dev_audit_replay_dev27_findings_v1"
    assert payload["write_performed"] is False
    assert payload["decision"] == "AUDIT_ONLY_NO_CODE_FIX"
