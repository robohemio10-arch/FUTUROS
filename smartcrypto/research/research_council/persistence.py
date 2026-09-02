"""Restricted atomic persistence for Research Council evidence only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import (
    AtomicWriteError,
    AtomicWritePolicy,
    atomic_write_json,
    resolve_authorized_target,
)

from .contracts import ContextIntelligenceSnapshot


class ResearchCouncilPersistenceError(RuntimeError):
    def __init__(self, reason: str, *, write_performed: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.write_performed = write_performed


def persist_snapshot(
    *,
    project_root: str | Path,
    snapshot: ContextIntelligenceSnapshot,
    output_json: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    research_root = root / "data" / "research" / "research_council"
    audit_root = root / "data" / "reports" / "aibot_parity" / "research_council"
    research_policy = AtomicWritePolicy.restricted((research_root,), working_directory=root)
    audit_policy = AtomicWritePolicy.restricted((audit_root,), working_directory=root)
    try:
        snapshot_target = (
            research_root / snapshot.snapshot_id / "context_snapshot.json"
            if output_json is None
            else resolve_authorized_target(output_json, policy=research_policy)
        )
    except AtomicWriteError as exc:
        raise ResearchCouncilPersistenceError(exc.reason) from exc
    audit_target = audit_root / snapshot.snapshot_id / "provider_audit.json"
    snapshot_payload = snapshot.model_dump(mode="json")
    audit_payload = {
        "schema_version": "research_council_provider_audit_v1",
        "snapshot_id": snapshot.snapshot_id,
        "status": snapshot.status,
        "provider_provenance": [
            item.model_dump(mode="json") for item in snapshot.provider_provenance
        ],
        "agent_statuses": {
            key: value.value for key, value in snapshot.agent_statuses.items()
        },
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "sends_orders": False,
        "exchange_private_access": False,
    }

    snapshot_written = _write_once(
        snapshot_target, snapshot_payload, policy=research_policy
    )
    try:
        audit_written = _write_once(audit_target, audit_payload, policy=audit_policy)
    except ResearchCouncilPersistenceError as exc:
        raise ResearchCouncilPersistenceError(
            exc.reason,
            write_performed=snapshot_written or exc.write_performed,
        ) from exc
    return {
        "write_performed": snapshot_written or audit_written,
        "output_paths": {
            "snapshot": snapshot_target.relative_to(root).as_posix(),
            "provider_audit": audit_target.relative_to(root).as_posix(),
        },
    }


def _write_once(
    target: Path,
    payload: Mapping[str, Any],
    *,
    policy: AtomicWritePolicy,
) -> bool:
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise ResearchCouncilPersistenceError("existing_output_not_regular_file")
        try:
            existing = json.loads(target.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ResearchCouncilPersistenceError("existing_output_unreadable") from exc
        if existing == dict(payload):
            return False
        raise ResearchCouncilPersistenceError("deterministic_output_conflict")
    try:
        result = atomic_write_json(
            target,
            dict(payload),
            policy=policy,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
    except (AtomicWriteError, OSError, ValueError) as exc:
        raise ResearchCouncilPersistenceError("research_evidence_write_failed") from exc
    return result.write_performed
