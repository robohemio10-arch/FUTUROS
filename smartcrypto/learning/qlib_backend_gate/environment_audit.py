"""Environment audit for the Qlib research backend dependency gate."""

from __future__ import annotations

import os
import platform
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

RELEVANT_ENV_VARS = (
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "PYTHONPATH",
    "SMARTCRYPTO_RUNTIME_MODE",
    "LIVE_ENABLED",
    "ORDER_SUBMISSION_ENABLED",
    "REAL_ORDER_SUBMISSION_ENABLED",
)
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


def build_environment_audit(project_root: str | Path) -> dict[str, Any]:
    """Audit Qlib dependency files and execution context without side effects.

    This gate intentionally mirrors the dependency semantics used by the
    environment-lock auditor. ``qlib_dependency_pinned`` is true only when a
    version-exact Qlib package exists in a versioned lockfile.
    """

    root = Path(project_root).resolve()
    validation_errors: list[str] = []
    if not root.exists() or not root.is_dir():
        validation_errors.append("project_root_missing_or_not_directory")
        dependency_files: list[Path] = []
        declaration = empty_declaration()
    else:
        dependency_files = discover_dependency_files(root)
        declaration = inspect_dependency_declaration(root)
    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "executable": sys.executable,
        "cwd": os.getcwd(),
        "project_root": str(root),
        "virtualenv_detected": bool(os.environ.get("VIRTUAL_ENV") or os.environ.get("CONDA_PREFIX")),
        "relevant_env_vars_present": {name: name in os.environ for name in RELEVANT_ENV_VARS},
        "dependency_files_detected": [str(path) for path in dependency_files],
        "qlib_dependency_declared": bool(declaration["qlib_dependency_declared"]),
        "qlib_dependency_pinned": bool(declaration["qlib_dependency_pinned"]),
        "qlib_dependency_hash_locked": bool(declaration["qlib_dependency_hash_locked"]),
        "dependency_sources": declaration["dependency_sources"],
        "dependency_specifiers": declaration["dependency_specifiers"],
        "lockfile_entries": declaration["lockfile_entries"],
        "qlib_backend_available": False,
        "environment_audit_status": "blocked" if validation_errors else "ok",
        "validation_errors": validation_errors,
    }


def empty_declaration() -> dict[str, Any]:
    return {
        "qlib_dependency_declared": False,
        "qlib_dependency_pinned": False,
        "qlib_dependency_hash_locked": False,
        "dependency_sources": [],
        "dependency_specifiers": [],
        "lockfile_entries": [],
    }


def inspect_dependency_declaration(project_root: Path) -> dict[str, Any]:
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
