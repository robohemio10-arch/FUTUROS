"""Atomic, lock-serialized persistence for AIBOT-Parity W13 snapshots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import (
    AtomicWriteError,
    AtomicWritePolicy,
    atomic_write_json,
    resolve_authorized_target,
)

from .contracts import AibotParityPipelineSnapshot

DEFAULT_OUTPUT = Path("data/reports/aibot_parity/aibot_parity_e2e_snapshot_v1.json")


class AibotParityPipelinePersistenceError(RuntimeError):
    def __init__(self, reason: str, *, write_performed: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.write_performed = write_performed


def persist_pipeline_snapshot(
    *,
    project_root: str | Path,
    snapshot: AibotParityPipelineSnapshot,
    output_json: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    report_root = (root / "data" / "reports" / "aibot_parity").resolve(strict=False)
    policy = AtomicWritePolicy.restricted((report_root,), working_directory=root)
    target_input = DEFAULT_OUTPUT if output_json is None else Path(output_json)
    try:
        target = resolve_authorized_target(target_input, policy=policy)
    except AtomicWriteError as exc:
        raise AibotParityPipelinePersistenceError(exc.reason) from exc

    payload = snapshot.model_dump(mode="json")
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise AibotParityPipelinePersistenceError("existing_output_not_regular_file")
        try:
            existing = json.loads(target.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AibotParityPipelinePersistenceError("existing_output_unreadable") from exc
        if existing == payload:
            return {
                "write_performed": False,
                "lock_serialized": True,
                "output_path": target.relative_to(root).as_posix(),
            }
    try:
        result = atomic_write_json(
            target,
            payload,
            policy=policy,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
    except (AtomicWriteError, OSError, ValueError) as exc:
        reason = exc.reason if isinstance(exc, AtomicWriteError) else "pipeline_write_failed"
        raise AibotParityPipelinePersistenceError(reason) from exc
    return {
        "write_performed": result.write_performed,
        "lock_serialized": result.lock_serialized,
        "output_path": target.relative_to(root).as_posix(),
    }
