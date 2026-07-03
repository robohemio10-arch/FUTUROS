from __future__ import annotations

from pathlib import Path
from typing import Any

from smartcrypto.learning.qlib_backend_gate import build_qlib_research_backend_gate_report
from smartcrypto.learning.qlib_backend_gate.backend_probe import REQUIRED_MODULES


def write_pyproject(root: Path, specifier: str = "pyqlib==0.9.7") -> None:
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


def test_gate_treats_pyproject_pin_as_declaration_only(tmp_path: Path) -> None:
    write_pyproject(tmp_path, "pyqlib==0.9.7")
    report = build_qlib_research_backend_gate_report(
        project_root=tmp_path,
        probe_func=lambda _modules: fake_probe("available"),
    )

    assert report["qlib_dependency_declared"] is True
    assert report["qlib_dependency_pinned"] is False
    assert report["environment_audit"]["qlib_dependency_pinned"] is False
    assert report["dependency_contract"]["qlib_dependency_pinned"] is False
    assert report["environment_lock_status"] == "declared_not_locked"
    assert report["dependency_contract"]["environment_lock_status"] == "declared_not_locked"


def test_gate_detects_dedicated_qlib_lock(tmp_path: Path) -> None:
    write_pyproject(tmp_path, "pyqlib==0.9.7")
    (tmp_path / "requirements-qlib.lock").write_text("pyqlib==0.9.7\n", encoding="utf-8")
    report = build_qlib_research_backend_gate_report(
        project_root=tmp_path,
        probe_func=lambda _modules: fake_probe("available"),
    )

    assert report["qlib_dependency_declared"] is True
    assert report["qlib_dependency_pinned"] is True
    assert report["environment_audit"]["qlib_dependency_pinned"] is True
    assert report["dependency_contract"]["qlib_dependency_pinned"] is True
    assert report["lockfile_entries"][0]["path"] == "requirements-qlib.lock"
    assert report["environment_lock_status"] == "locked"
    assert report["dependency_contract"]["environment_lock_status"] == "locked"
    assert report["backend_capabilities"]["can_train_ranker"] is True
    assert report["backend_capabilities"]["can_promote_model"] is False
    assert report["backend_capabilities"]["can_send_orders"] is False


def test_gate_reports_locked_but_unavailable_without_side_effects(tmp_path: Path) -> None:
    write_pyproject(tmp_path, "pyqlib==0.9.7")
    (tmp_path / "requirements-qlib.lock").write_text("pyqlib==0.9.7\n", encoding="utf-8")
    report = build_qlib_research_backend_gate_report(
        project_root=tmp_path,
        probe_func=lambda _modules: fake_probe("unavailable"),
    )

    assert report["status"] == "ok"
    assert report["reason"] == "qlib_backend_unavailable"
    assert report["qlib_dependency_pinned"] is True
    assert report["qlib_importable"] is False
    assert report["qlib_backend_status"] == "unavailable"
    assert report["environment_lock_status"] == "locked_not_importable"
    assert report["dependency_contract"]["environment_lock_status"] == "locked_not_importable"
    assert report["model_promotion_performed"] is False
    assert report["registry_write_performed"] is False
    assert report["active_model_changed"] is False
    assert report["qlib_runtime_updated"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
