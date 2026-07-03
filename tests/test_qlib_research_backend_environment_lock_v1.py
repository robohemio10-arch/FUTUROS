from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from smartcrypto.learning.qlib_backend_environment_lock import build_qlib_environment_lock_report
from smartcrypto.learning.qlib_backend_gate.backend_probe import REQUIRED_MODULES

REPO_ROOT = Path(__file__).resolve().parents[1]


def write_pyproject(root: Path, specifier: str = "pyqlib>=0.9,<1") -> None:
    (root / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "fixture"',
                'version = "0.0.0"',
                "",
                "[project.optional-dependencies]",
                "qlib = [",
                f'  "{specifier}"',
                "]",
                "",
            ]
        ),
        encoding="utf-8",
    )


def fake_probe(status: str) -> dict[str, Any]:
    importable = status in {"available", "partial"}
    module_results: dict[str, dict[str, Any]] = {}
    for module in REQUIRED_MODULES:
        module_importable = importable and not (status == "partial" and module == "qlib.contrib.model")
        module_results[module] = {
            "module": module,
            "importable": module_importable,
            "origin": f"/fake/{module.replace('.', '/')}.py" if module_importable else None,
            "reason": "module_spec_found" if module_importable else "module_not_found",
        }
    return {
        "qlib_backend_status": status,
        "qlib_importable": importable,
        "qlib_version": "0.9.7" if status == "available" else None,
        "qlib_package_path": "/fake/qlib/__init__.py" if importable else None,
        "required_modules": list(REQUIRED_MODULES),
        "module_probe_results": module_results,
        "unsupported_reasons": [] if status == "available" else ["fixture_unavailable"],
    }


def test_declared_dependency_is_detected(tmp_path: Path) -> None:
    write_pyproject(tmp_path)
    report = build_qlib_environment_lock_report(project_root=tmp_path, probe_func=lambda _modules: fake_probe("unavailable"))

    assert report["qlib_dependency_declared"] is True
    assert report["dependency_specifiers"] == ["pyqlib>=0.9,<1"]
    assert report["status"] == "warning"
    assert report["reason"] == "qlib_backend_unavailable"


def test_missing_dependency_declaration_blocks(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0.0.0'\n", encoding="utf-8")
    report = build_qlib_environment_lock_report(project_root=tmp_path, probe_func=lambda _modules: fake_probe("unavailable"))

    assert report["status"] == "blocked"
    assert report["reason"] == "qlib_dependency_not_declared"
    assert "qlib_dependency_not_declared" in report["validation_errors"]


def test_exact_pin_is_detected(tmp_path: Path) -> None:
    write_pyproject(tmp_path, "pyqlib==0.9.7")
    report = build_qlib_environment_lock_report(project_root=tmp_path, probe_func=lambda _modules: fake_probe("available"))

    assert report["qlib_dependency_pinned"] is True
    assert report["environment_lock_status"] == "locked"
    assert report["status"] == "ok"


def test_hash_locked_lockfile_is_detected(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project.optional-dependencies]\n", encoding="utf-8")
    (tmp_path / "requirements-dev.lock").write_text(
        "pyqlib==0.9.7 --hash=sha256:" + ("a" * 64) + "\n",
        encoding="utf-8",
    )
    report = build_qlib_environment_lock_report(project_root=tmp_path, probe_func=lambda _modules: fake_probe("available"))

    assert report["qlib_dependency_declared"] is True
    assert report["qlib_dependency_pinned"] is True
    assert report["lockfile_entries"]


def test_available_backend_reports_compatibility(tmp_path: Path) -> None:
    write_pyproject(tmp_path)
    report = build_qlib_environment_lock_report(project_root=tmp_path, probe_func=lambda _modules: fake_probe("available"))

    assert report["status"] == "ok"
    assert report["reason"] == "qlib_backend_available"
    assert report["qlib_importable"] is True
    assert report["qlib_version"] == "0.9.7"
    assert report["compatibility_status"] == "compatible"
    assert report["required_modules_status"]["all_required_modules_importable"] is True


def test_partial_backend_is_warning(tmp_path: Path) -> None:
    write_pyproject(tmp_path)
    report = build_qlib_environment_lock_report(project_root=tmp_path, probe_func=lambda _modules: fake_probe("partial"))

    assert report["status"] == "warning"
    assert report["reason"] == "qlib_backend_partial"
    assert report["compatibility_status"] == "partial_backend"
    assert "qlib.contrib.model" in report["required_modules_status"]["missing_modules"]


def test_write_outputs_only_reports(tmp_path: Path) -> None:
    write_pyproject(tmp_path)
    report = build_qlib_environment_lock_report(project_root=tmp_path, write=True, probe_func=lambda _modules: fake_probe("available"))

    assert report["write_performed"] is True
    assert (tmp_path / "data/reports/qlib_research_backend_environment_lock_v1.json").exists()
    assert (tmp_path / "data/reports/qlib_research_backend_environment_lock_v1.md").exists()
    assert not (tmp_path / "data/models").exists()


def test_safety_flags_preserved(tmp_path: Path) -> None:
    write_pyproject(tmp_path)
    report = build_qlib_environment_lock_report(project_root=tmp_path, probe_func=lambda _modules: fake_probe("available"))

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["training_requested"] is False
    assert report["qlib_training_performed"] is False
    assert report["qlib_runtime_updated"] is False
    assert report["ai_shadow_runtime_updated"] is False
    assert report["registry_write_performed"] is False
    assert report["model_promotion_performed"] is False
    assert report["active_model_changed"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["writes_parquet"] is False


def test_cli_json_executes() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/audit_qlib_research_backend_environment_lock_v1.py"),
            "--project-root",
            str(REPO_ROOT),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["schema_version"] == "qlib_research_backend_environment_lock_v1"
    assert payload["qlib_dependency_declared"] is True
    assert payload["write_performed"] is False
    assert payload["sends_orders"] is False
