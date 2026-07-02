"""Environment audit for the Qlib research backend dependency gate."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from typing import Any

DEPENDENCY_PATTERNS = ("pyproject.toml", "requirements*.txt", "requirements*.lock")
RELEVANT_ENV_VARS = (
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "PYTHONPATH",
    "SMARTCRYPTO_RUNTIME_MODE",
    "LIVE_ENABLED",
    "ORDER_SUBMISSION_ENABLED",
    "REAL_ORDER_SUBMISSION_ENABLED",
)


def build_environment_audit(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    validation_errors: list[str] = []
    if not root.exists() or not root.is_dir():
        validation_errors.append("project_root_missing_or_not_directory")
    dependency_files = discover_dependency_files(root)
    qlib_dependency_declared = False
    qlib_dependency_pinned = False
    for path in dependency_files:
        text = safe_read(path).lower()
        if "pyqlib" in text or "\nqlib" in text or " qlib" in text:
            qlib_dependency_declared = True
            if "pyqlib==" in text or "qlib==" in text or "pyqlib>=" in text or "qlib>=" in text:
                qlib_dependency_pinned = True
    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "executable": sys.executable,
        "cwd": os.getcwd(),
        "project_root": str(root),
        "virtualenv_detected": bool(os.environ.get("VIRTUAL_ENV") or os.environ.get("CONDA_PREFIX")),
        "relevant_env_vars_present": {name: name in os.environ for name in RELEVANT_ENV_VARS},
        "dependency_files_detected": [str(path) for path in dependency_files],
        "qlib_dependency_declared": qlib_dependency_declared,
        "qlib_dependency_pinned": qlib_dependency_pinned,
        "qlib_backend_available": False,
        "environment_audit_status": "blocked" if validation_errors else "ok",
        "validation_errors": validation_errors,
    }


def discover_dependency_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in DEPENDENCY_PATTERNS:
        files.extend(path.resolve() for path in root.glob(pattern) if path.is_file())
    return sorted(set(files))


def safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
