"""Static Qlib research backend probes without runtime initialization."""

from __future__ import annotations

import importlib.metadata
import importlib.util
from pathlib import Path
from typing import Any

REQUIRED_MODULES = [
    "qlib",
    "qlib.data",
    "qlib.workflow",
    "qlib.contrib",
    "qlib.contrib.model",
]


def probe_qlib_backend(required_modules: list[str] | None = None) -> dict[str, Any]:
    """Probe Qlib availability with importlib metadata/spec checks only."""

    modules = required_modules or list(REQUIRED_MODULES)
    module_results = {module: probe_module(module) for module in modules}
    qlib_result = module_results.get("qlib", probe_module("qlib"))
    qlib_importable = bool(qlib_result["importable"])
    qlib_version = detect_qlib_version() if qlib_importable else None
    package_path = qlib_result.get("origin")
    missing = [module for module, result in module_results.items() if not result["importable"]]
    unsupported: list[str] = []
    if qlib_importable and not qlib_version:
        unsupported.append("qlib_version_not_detected")
    if qlib_importable and missing:
        unsupported.append(f"missing_required_modules:{','.join(missing)}")
    status = "unavailable"
    if qlib_importable and qlib_version and not missing:
        status = "available"
    elif qlib_importable:
        status = "partial"
    return {
        "qlib_backend_status": status,
        "qlib_importable": qlib_importable,
        "qlib_version": qlib_version,
        "qlib_package_path": package_path,
        "required_modules": modules,
        "module_probe_results": module_results,
        "unsupported_reasons": unsupported,
    }


def probe_module(module_name: str) -> dict[str, Any]:
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, AttributeError, ValueError) as exc:
        return {
            "module": module_name,
            "importable": False,
            "origin": None,
            "reason": f"find_spec_error:{type(exc).__name__}",
        }
    if spec is None:
        return {"module": module_name, "importable": False, "origin": None, "reason": "module_not_found"}
    origin = str(Path(spec.origin).resolve()) if spec.origin and spec.origin not in {"built-in", "namespace"} else spec.origin
    return {"module": module_name, "importable": True, "origin": origin, "reason": "module_spec_found"}


def detect_qlib_version() -> str | None:
    for distribution_name in ("pyqlib", "qlib"):
        try:
            return importlib.metadata.version(distribution_name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None
