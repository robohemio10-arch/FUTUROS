"""Restricted atomic persistence for non-versioned AIBOT research reports."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import (
    AtomicWritePolicy,
    atomic_write_json,
)


REPORT_FILENAMES = {
    "source_registry": ("research", "source_registry.json"),
    "trader_master_audit": ("reports", "trader_master_audit.json"),
    "behavior_fingerprint": ("reports", "behavior_fingerprint.json"),
    "rolling_behavior": ("research", "rolling_behavior.json"),
    "performance_reconciliation": ("reports", "performance_reconciliation.json"),
    "benchmark_summary": ("reports", "benchmark_summary.json"),
}


class AibotPersistenceError(RuntimeError):
    """Controlled persistence boundary error."""


def persist_benchmark_reports(
    *,
    project_root: str | Path,
    source_batch_id: str,
    loaded_at_utc: str,
    payloads: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    batch_component = _safe_component(source_batch_id)
    run_component = _safe_component(loaded_at_utc)
    research_root = root / "data" / "research" / "aibot_parity"
    reports_root = root / "data" / "reports" / "aibot_parity"
    policies = {
        "research": AtomicWritePolicy.restricted((research_root,), working_directory=root),
        "reports": AtomicWritePolicy.restricted((reports_root,), working_directory=root),
    }
    base_roots = {"research": research_root, "reports": reports_root}
    output_paths: dict[str, str] = {}
    write_results: dict[str, Any] = {}
    for name, payload in payloads.items():
        if name not in REPORT_FILENAMES:
            raise AibotPersistenceError(f"unsupported_output:{name}")
        boundary, filename = REPORT_FILENAMES[name]
        target = base_roots[boundary] / batch_component / run_component / filename
        if target.exists():
            raise AibotPersistenceError(f"evidence_run_already_exists:{name}")
        result = atomic_write_json(
            target,
            to_json_safe(dict(payload)),
            policy=policies[boundary],
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        output_paths[name] = target.relative_to(root).as_posix()
        write_results[name] = {
            "status": result.status,
            "bytes_written": result.bytes_written,
            "write_performed": result.write_performed,
        }
    return {
        "write_performed": bool(write_results) and all(
            bool(item["write_performed"]) for item in write_results.values()
        ),
        "output_paths": output_paths,
        "write_results": write_results,
    }


def to_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (pd.Timestamp,)):
        return None if pd.isna(value) else value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        return {str(key): to_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json_safe(item) for item in value]
    if pd.isna(value):
        return None
    return str(value)


def _safe_component(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]", "_", str(value).strip())
    if not normalized or normalized in {".", ".."}:
        raise AibotPersistenceError("invalid_output_component")
    return normalized
