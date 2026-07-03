"""Static environment lock audit for the Qlib research backend."""

from __future__ import annotations

import json
import platform
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, Callable

from smartcrypto.learning.paper_autolearning.outcome_schema import SAFETY_FLAGS, utc_now_iso
from smartcrypto.learning.qlib_backend_gate.backend_probe import REQUIRED_MODULES, probe_qlib_backend

SCHEMA_VERSION = "qlib_research_backend_environment_lock_v1"
DEFAULT_REPORT_JSON = Path("data/reports/qlib_research_backend_environment_lock_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/qlib_research_backend_environment_lock_v1.md")
QLIB_PACKAGE_NAMES = ("pyqlib", "qlib")
QLIB_LOCKFILE_NAMES = (
    "requirements-qlib.lock",
    "requirements-dev.lock",
    "requirements-runtime.lock",
)
DEPENDENCY_GLOBS = ("requirements*.lock", "requirements*.txt")
PINNED_Q_LIB_RE = re.compile(
    r"^(?P<package>pyqlib|qlib)(\[[^\]]+\])?==(?P<version>[^=<>!~;\s]+)",
    flags=re.IGNORECASE,
)


def build_qlib_environment_lock_report(
    *,
    project_root: str | Path,
    write: bool = False,
    report_json_path: str | Path | None = None,
    report_markdown_path: str | Path | None = None,
    probe_func: Callable[[list[str] | None], dict[str, Any]] | None = None,
    required_modules: list[str] | None = None,
) -> dict[str, Any]:
    """Audit Qlib research dependency declaration and importability without installing packages."""

    root = Path(project_root).resolve()
    modules = required_modules or list(REQUIRED_MODULES)
    declaration = inspect_dependency_declaration(root)
    probe = (probe_func or probe_qlib_backend)(modules)
    qlib_status = str(probe.get("qlib_backend_status", "unavailable"))
    qlib_importable = bool(probe.get("qlib_importable", False))
    dependency_declared = bool(declaration["qlib_dependency_declared"])
    dependency_pinned = bool(declaration["qlib_dependency_pinned"])
    environment_lock_status = determine_environment_lock_status(
        dependency_declared=dependency_declared,
        dependency_pinned=dependency_pinned,
        qlib_status=qlib_status,
        qlib_importable=qlib_importable,
    )
    compatibility_status = determine_compatibility_status(qlib_status, dependency_declared)
    status, reason = determine_status_reason(
        qlib_status=qlib_status,
        dependency_declared=dependency_declared,
    )
    output_paths = {
        "report_json": str(resolve(root, report_json_path, DEFAULT_REPORT_JSON)),
        "report_markdown": str(resolve(root, report_markdown_path, DEFAULT_REPORT_MD)),
    }
    report: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now_iso(),
        "project_root": str(root),
        "qlib_dependency_declared": dependency_declared,
        "qlib_dependency_pinned": dependency_pinned,
        "qlib_dependency_hash_locked": bool(declaration["qlib_dependency_hash_locked"]),
        "dependency_sources": declaration["dependency_sources"],
        "dependency_specifiers": declaration["dependency_specifiers"],
        "lockfile_entries": declaration["lockfile_entries"],
        "accepted_lockfiles": list(QLIB_LOCKFILE_NAMES),
        "qlib_backend_status": qlib_status,
        "qlib_importable": qlib_importable,
        "qlib_version": probe.get("qlib_version"),
        "qlib_package_path": probe.get("qlib_package_path"),
        "required_modules": modules,
        "required_modules_status": required_modules_status(
            probe.get("module_probe_results", {}),
            modules,
        ),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "environment_lock_status": environment_lock_status,
        "compatibility_status": compatibility_status,
        "recommended_action": recommended_action(
            qlib_status=qlib_status,
            dependency_declared=dependency_declared,
            dependency_pinned=dependency_pinned,
        ),
        "write_requested": bool(write),
        "write_performed": False,
        "output_paths": output_paths,
        **safety_flags(),
        "safety_flags": safety_flags(),
        "validation_errors": validation_errors(
            qlib_status=qlib_status,
            dependency_declared=dependency_declared,
        ),
    }
    if write:
        report["write_performed"] = True
        write_reports(
            report=report,
            output_json=Path(output_paths["report_json"]),
            output_md=Path(output_paths["report_markdown"]),
        )
    return report


def inspect_dependency_declaration(project_root: Path) -> dict[str, Any]:
    """Return Qlib declaration and lock status using one institutional rule.

    ``qlib_dependency_pinned`` is true only when a version-exact ``pyqlib==X.Y.Z``
    or ``qlib==X.Y.Z`` entry exists in a versioned lockfile. A pyproject optional
    dependency, even when exact-pinned, is a declaration source but not a lock.
    """

    pyproject = project_root / "pyproject.toml"
    requirements = discover_dependency_files(project_root)
    dependency_sources: list[dict[str, Any]] = []
    specifiers: list[str] = []
    lockfile_entries: list[dict[str, Any]] = []
    if pyproject.exists():
        parsed = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        optional = parsed.get("project", {}).get("optional-dependencies", {})
        for group_name, entries in optional.items():
            for entry in entries:
                entry_text = str(entry).strip()
                if is_qlib_requirement(entry_text):
                    specifiers.append(entry_text)
                    dependency_sources.append(
                        {
                            "path": "pyproject.toml",
                            "group": str(group_name),
                            "specifier": entry_text,
                            "source_type": "declaration",
                        }
                    )
    for path in requirements:
        relative = path.relative_to(project_root).as_posix()
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8", errors="ignore").splitlines(),
            start=1,
        ):
            stripped = normalize_requirement_line(line)
            if stripped and is_qlib_requirement(stripped):
                pinned = is_pinned_requirement(stripped)
                hash_locked = "--hash=sha256:" in stripped.lower()
                entry = {
                    "path": relative,
                    "specifier": stripped,
                    "line_number": line_number,
                    "pinned": pinned,
                    "hash_locked": hash_locked,
                }
                lockfile_entries.append(entry)
                specifiers.append(stripped)
                dependency_sources.append(
                    {
                        "path": relative,
                        "group": "lockfile",
                        "specifier": stripped,
                        "source_type": "lockfile",
                    }
                )
    return {
        "qlib_dependency_declared": bool(dependency_sources),
        "qlib_dependency_pinned": any(entry["pinned"] for entry in lockfile_entries),
        "qlib_dependency_hash_locked": any(
            entry["pinned"] and entry["hash_locked"] for entry in lockfile_entries
        ),
        "dependency_sources": dependency_sources,
        "dependency_specifiers": sorted(set(specifiers)),
        "lockfile_entries": lockfile_entries,
    }


def discover_dependency_files(project_root: Path) -> list[Path]:
    """Discover dependency files, prioritising the dedicated research Qlib lock."""

    files: list[Path] = []
    for name in QLIB_LOCKFILE_NAMES:
        path = project_root / name
        if path.is_file():
            files.append(path.resolve())
    for pattern in DEPENDENCY_GLOBS:
        files.extend(path.resolve() for path in project_root.glob(pattern) if path.is_file())
    return sorted(set(files), key=lambda path: path.relative_to(project_root).as_posix())


def normalize_requirement_line(value: str) -> str:
    stripped = value.strip()
    if not stripped or stripped.startswith("#"):
        return ""
    if " #" in stripped:
        stripped = stripped.split(" #", 1)[0].strip()
    return stripped


def is_qlib_requirement(value: str) -> bool:
    normalized = value.strip().lower()
    return any(
        normalized == name
        or normalized.startswith(f"{name}=")
        or normalized.startswith(f"{name}<")
        or normalized.startswith(f"{name}>")
        or normalized.startswith(f"{name}[")
        for name in QLIB_PACKAGE_NAMES
    )


def is_pinned_requirement(value: str) -> bool:
    return bool(PINNED_Q_LIB_RE.match(value.strip()))


def required_modules_status(
    module_probe_results: dict[str, Any],
    required_modules: list[str],
) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for module in required_modules:
        result = module_probe_results.get(module, {})
        rows[module] = {
            "importable": bool(result.get("importable", False)),
            "origin": result.get("origin"),
            "reason": result.get("reason", "not_probed"),
        }
    missing = [module for module, result in rows.items() if not result["importable"]]
    return {
        "modules": rows,
        "missing_modules": missing,
        "all_required_modules_importable": not missing,
    }


def determine_environment_lock_status(
    *,
    dependency_declared: bool,
    dependency_pinned: bool,
    qlib_status: str,
    qlib_importable: bool,
) -> str:
    if not dependency_declared:
        return "missing_dependency_declaration"
    if not dependency_pinned:
        return "declared_not_locked"
    if qlib_status == "blocked":
        return "locked_with_documented_backend_blocker"
    if not qlib_importable or qlib_status == "unavailable":
        return "locked_not_importable"
    return "locked"


def determine_compatibility_status(qlib_status: str, dependency_declared: bool) -> str:
    if not dependency_declared:
        return "blocked_missing_dependency_contract"
    if qlib_status == "available":
        return "compatible"
    if qlib_status == "partial":
        return "partial_backend"
    if qlib_status == "blocked":
        return "blocked_backend"
    return "backend_unavailable"


def determine_status_reason(*, qlib_status: str, dependency_declared: bool) -> tuple[str, str]:
    if not dependency_declared:
        return "blocked", "qlib_dependency_not_declared"
    if qlib_status == "available":
        return "ok", "qlib_backend_available"
    if qlib_status == "partial":
        return "warning", "qlib_backend_partial"
    if qlib_status == "blocked":
        return "blocked", "qlib_backend_blocked"
    return "warning", "qlib_backend_unavailable"


def validation_errors(*, qlib_status: str, dependency_declared: bool) -> list[str]:
    errors: list[str] = []
    if not dependency_declared:
        errors.append("qlib_dependency_not_declared")
    if qlib_status == "blocked":
        errors.append("qlib_backend_blocked")
    return errors


def recommended_action(
    *,
    qlib_status: str,
    dependency_declared: bool,
    dependency_pinned: bool,
) -> str:
    if not dependency_declared:
        return "Declare pyqlib in a research-only optional dependency group before research training."
    if qlib_status == "available" and dependency_pinned:
        return "Environment is importable and locked for research-only Qlib challenger work."
    if qlib_status == "available":
        return "Qlib is importable; add a pinned research lock before declaring readiness."
    if qlib_status == "partial":
        return "Qlib is partially importable; align required modules and version metadata."
    if qlib_status == "blocked":
        return "Resolve backend isolation or environment blockers before any research training."
    if dependency_pinned:
        return "Install the pinned research dependency set; this auditor does not install packages."
    return "Create a pinned research lock for pyqlib; this auditor does not install packages."


def write_reports(*, report: dict[str, Any], output_json: Path, output_md: Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(stable_pretty_json(report), encoding="utf-8")
    output_md.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Qlib Research Backend Environment Lock V1",
            "",
            f"- Status: `{report['status']}`",
            f"- Reason: `{report['reason']}`",
            f"- Dependency declared: `{report['qlib_dependency_declared']}`",
            f"- Dependency pinned: `{report['qlib_dependency_pinned']}`",
            f"- Dependency hash locked: `{report['qlib_dependency_hash_locked']}`",
            f"- Qlib importable: `{report['qlib_importable']}`",
            f"- Qlib version: `{report['qlib_version']}`",
            f"- Environment lock status: `{report['environment_lock_status']}`",
            f"- Compatibility status: `{report['compatibility_status']}`",
            f"- Recommended action: {report['recommended_action']}",
            "",
            "This audit is static and research-only. It does not install packages, train models, update runtime, write registry, promote models, access exchange, or send orders.",
            "",
        ]
    )


def stable_pretty_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n"


def resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else root / path


def safety_flags() -> dict[str, bool]:
    return {
        **SAFETY_FLAGS,
        "training_requested": False,
        "qlib_challenger_training_performed": False,
        "qlib_training_performed": False,
        "qlib_runtime_updated": False,
        "ai_shadow_training_performed": False,
        "ai_shadow_runtime_updated": False,
        "registry_write_requested": False,
        "registry_write_performed": False,
        "model_promotion_requested": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
    }
