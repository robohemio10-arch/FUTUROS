from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_operational_exception_swallowing.py"


def load_auditor() -> Any:
    spec = importlib.util.spec_from_file_location("operational_exception_swallowing_audit_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_python(root: Path, relative_path: str, source: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_auditor_detects_real_except_exception_pass(tmp_path: Path) -> None:
    module = load_auditor()
    write_python(
        tmp_path,
        "scripts/report_parser.py",
        "def parse_report():\n    try:\n        return 1\n    except Exception:\n        pass\n",
    )

    report = module.audit_project(tmp_path)

    assert report["finding_count"] == 1
    assert report["findings"][0]["pattern"] == "broad_exception_pass"
    assert report["findings"][0]["function_or_class"] == "parse_report"


def test_auditor_ignores_custom_exception_class_pass(tmp_path: Path) -> None:
    module = load_auditor()
    write_python(tmp_path, "smartcrypto/errors.py", "class PaperOnlyError(Exception):\n    pass\n")

    report = module.audit_project(tmp_path)

    assert report["finding_count"] == 0
    assert report["ignored_false_positive_count"] == 1


def test_auditor_ignores_protocol_and_abc_contracts(tmp_path: Path) -> None:
    module = load_auditor()
    write_python(
        tmp_path,
        "smartcrypto/contracts.py",
        "from typing import Protocol\n"
        "from abc import ABC, abstractmethod\n"
        "class Reader(Protocol):\n"
        "    def read(self):\n"
        "        pass\n"
        "class Writer(ABC):\n"
        "    @abstractmethod\n"
        "    def write(self):\n"
        "        pass\n",
    )

    report = module.audit_project(tmp_path)

    assert report["finding_count"] == 0
    assert report["ignored_false_positive_count"] == 2


def test_auditor_classifies_operational_severity(tmp_path: Path) -> None:
    module = load_auditor()
    write_python(
        tmp_path,
        "smartcrypto/execution/order_submission_probe.py",
        "def submit_order():\n    try:\n        mutate_order()\n    except Exception:\n        pass\n",
    )
    write_python(
        tmp_path,
        "smartcrypto/ops/runtime_evidence_probe.py",
        "def collect_evidence():\n    try:\n        collect()\n    except Exception:\n        pass\n",
    )
    write_python(
        tmp_path,
        "scripts/report_parser.py",
        "def convert():\n    try:\n        convert_value()\n    except Exception:\n        pass\n",
    )

    report = module.audit_project(tmp_path)
    severities = {finding["file"]: finding["severity"] for finding in report["findings"]}

    assert severities["smartcrypto/execution/order_submission_probe.py"] == "critical"
    assert severities["smartcrypto/ops/runtime_evidence_probe.py"] == "high"
    assert severities["scripts/report_parser.py"] == "medium"
    assert report["status"] == "blocked"


def test_auditor_recognizes_controlled_failure_and_logging(tmp_path: Path) -> None:
    module = load_auditor()
    write_python(
        tmp_path,
        "smartcrypto/ops/controlled.py",
        "def controlled(logger):\n"
        "    try:\n"
        "        operation()\n"
        "    except Exception:\n"
        "        logger.exception('operation failed')\n"
        "        return {'status': 'blocked', 'reason': 'operation_failed'}\n",
    )

    report = module.audit_project(tmp_path)

    assert report["status"] == "ok"
    assert report["finding_count"] == 0


def test_auditor_report_is_deterministic(tmp_path: Path) -> None:
    module = load_auditor()
    write_python(
        tmp_path,
        "scripts/sample.py",
        "def sample():\n    try:\n        work()\n    except Exception:\n        return None\n",
    )

    assert module.audit_project(tmp_path) == module.audit_project(tmp_path)


def test_auditor_preserves_all_safety_flags(tmp_path: Path) -> None:
    module = load_auditor()
    write_python(tmp_path, "scripts/safe.py", "PAPER_ONLY = True\n")

    report = module.audit_project(tmp_path)

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["exchange_private_access"] is False
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["canary_release_allowed"] is False
    assert report["live_release_allowed"] is False


def test_auditor_does_not_import_or_execute_audited_modules(tmp_path: Path) -> None:
    module = load_auditor()
    marker = tmp_path / "executed.txt"
    write_python(
        tmp_path,
        "smartcrypto/dangerous_module.py",
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nraise RuntimeError('must not run')\n",
    )

    report = module.audit_project(tmp_path)

    assert report["scanned_files"] == 1
    assert not marker.exists()


def test_cli_emits_controlled_json_and_fail_on_policy(tmp_path: Path) -> None:
    write_python(
        tmp_path,
        "smartcrypto/ops/runtime_evidence_probe.py",
        "try:\n    collect()\nexcept Exception:\n    pass\n",
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


def test_auditor_source_is_static_and_has_no_external_dispatch() -> None:
    source = SCRIPT.read_text(encoding="utf-8").lower()

    assert "notificationdispatcher" not in source
    assert "import ccxt" not in source
    assert "import docker" not in source
    assert "import requests" not in source
    assert "urllib.request" not in source
    assert "shell=true" not in source


def test_real_repository_has_no_unhandled_high_or_critical_findings() -> None:
    module = load_auditor()

    report = module.audit_project(ROOT)

    assert report["status"] in {"ok", "warning"}
    assert report["critical_count"] == 0
    assert report["high_count"] == 0
