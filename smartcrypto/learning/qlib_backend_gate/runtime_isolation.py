"""Runtime isolation checks for Qlib research backend probing."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import Any


def snapshot_runtime_state() -> dict[str, Any]:
    """Capture low-risk process state before static dependency probing."""

    return {
        "sys_path": list(sys.path),
        "cwd": os.getcwd(),
        "env_keys": sorted(os.environ),
    }


def audit_runtime_isolation(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Validate that probing did not mutate process-level runtime state."""

    side_effects: list[str] = []
    if list(before.get("sys_path", [])) != list(after.get("sys_path", [])):
        side_effects.append("sys_path_changed")
    if before.get("cwd") != after.get("cwd"):
        side_effects.append("cwd_changed")
    if list(before.get("env_keys", [])) != list(after.get("env_keys", [])):
        side_effects.append("env_keys_changed")
    return {
        "runtime_isolation_status": "blocked" if side_effects else "ok",
        "side_effects_detected": side_effects,
        "qlib_runtime_initialized": False,
        "qlib_runtime_updated": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
    }
