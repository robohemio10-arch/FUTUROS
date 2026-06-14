from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_bitradex_dependency_boundary.py"
POLICY = ROOT / "docs" / "BITRADEX_DEPENDENCY_BOUNDARY_CLEANUP_V1.md"


def load_auditor() -> Any:
    spec = importlib.util.spec_from_file_location("bitradex_dependency_boundary_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_file(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def write_policy(root: Path) -> None:
    write_file(
        root,
        "docs/BITRADEX_DEPENDENCY_BOUNDARY_CLEANUP_V1.md",
        "policy_status: active\npaper_only: true\nshadow_only: true\n"
        "live_trading_enabled: false\norder_submission_enabled: false\n"
        "real_order_submission_enabled: false\nexchange_private_access: false\n"
        "sends_orders: false\nchanges_risk: false\nruns_ocr: false\nimports_trades: false\n",
    )


def test_detects_prohibited_execution_order_or_private_exchange_import(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_file(
        tmp_path,
        "scripts/bitradex_ocr_stage.py",
        "from smartcrypto.execution.order_manager import OrderManager\n",
    )

    report = module.audit_project(tmp_path)

    assert report["status"] == "blocked"
    finding = report["dependency_findings"][0]
    assert finding["severity"] == "critical"
    assert finding["pattern"] == "bitradex_or_ocr_imports_trading_runtime"


def test_detects_unauthorized_official_dataset_writers(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_file(
        tmp_path,
        "scripts/bitradex_quick_import.py",
        "from pathlib import Path\n"
        "def write():\n"
        "    Path('data/trades/trades_master.xlsx').write_bytes(b'x')\n"
        "    Path('data/features/training_dataset.parquet').write_bytes(b'x')\n",
    )

    report = module.audit_project(tmp_path)

    assert report["status"] == "blocked"
    assert {item["pattern"] for item in report["write_findings"]} == {
        "unauthorized_trades_master_writer",
        "unauthorized_training_dataset_writer",
    }
    assert all(item["severity"] == "critical" for item in report["write_findings"])


def test_dashboard_read_only_snapshot_consumer_is_allowed(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_file(
        tmp_path,
        "smartcrypto/dashboard/bitradex_snapshot.py",
        "import json\nfrom pathlib import Path\n"
        "def load():\n    return json.loads(Path('data/reports/bitradex_status.json').read_text())\n",
    )

    report = module.audit_project(tmp_path)

    assert report["status"] == "ok"
    assert report["write_findings"] == []


def test_report_writer_is_controlled_warning(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_file(
        tmp_path,
        "scripts/bitradex_stage.py",
        "from pathlib import Path\n"
        "def report():\n    Path('data/reports/bitradex_stage.json').write_text('{}')\n",
    )

    report = module.audit_project(tmp_path)

    assert report["status"] == "warning"
    assert report["write_findings"][0]["pattern"] == "report_writer_policy_review"
    assert report["write_findings"][0]["severity"] == "medium"


def test_report_is_deterministic(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_file(tmp_path, "scripts/bitradex_reader.py", "def read(value):\n    return value\n")

    assert module.audit_project(tmp_path) == module.audit_project(tmp_path)


def test_preserves_all_safety_flags(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_file(tmp_path, "scripts/bitradex_reader.py", "PAPER_ONLY = True\n")

    report = module.audit_project(tmp_path)

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    for key in (
        "live_trading_enabled", "live_release_allowed", "canary_release_allowed",
        "order_submission_enabled", "real_order_submission_enabled",
        "exchange_private_access", "sends_orders", "changes_risk", "runs_ocr",
        "imports_trades", "writes_trades_master", "writes_official_trades_master",
        "changes_training_dataset", "changes_model",
    ):
        assert report[key] is False


def test_does_not_import_or_execute_audited_modules(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    marker = tmp_path / "executed.txt"
    write_file(
        tmp_path,
        "scripts/bitradex_dangerous.py",
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nraise RuntimeError('no')\n",
    )

    module.audit_project(tmp_path)

    assert not marker.exists()


def test_source_has_no_docker_network_exchange_or_notification_dispatch() -> None:
    source = SCRIPT.read_text(encoding="utf-8").lower()

    assert "import requests" not in source
    assert "urllib.request" not in source
    assert "import ccxt" not in source
    assert "import docker" not in source
    assert "notificationdispatcher" not in source
    assert "shell=true" not in source


def test_cli_returns_deterministic_json(tmp_path: Path) -> None:
    write_policy(tmp_path)
    write_file(tmp_path, "scripts/bitradex_reader.py", "PAPER_ONLY = True\n")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--json"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["schema_version"] == "bitradex_dependency_boundary_cleanup_v1"
    assert payload["status"] == "ok"


def test_real_repository_is_not_blocked_without_high_or_critical() -> None:
    module = load_auditor()

    report = module.audit_project(ROOT)

    assert report["status"] in {"ok", "warning"}
    assert report["critical_count"] == 0
    assert report["high_count"] == 0
    assert report["policy_documented"] is True


def test_policy_documents_authority_and_paper_shadow_safety() -> None:
    text = POLICY.read_text(encoding="utf-8").lower()

    assert "policy_status: active" in text
    assert "official trades master writers" in text
    assert "dashboard: read-only" in text
    assert "paper_only: true" in text
    assert "live_trading_enabled: false" in text
