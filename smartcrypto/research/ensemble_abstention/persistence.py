"""Restricted persistence for W4 research-only ensemble decisions."""

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

from .contracts import EnsembleAbstentionDecision


class EnsemblePersistenceError(RuntimeError):
    def __init__(self, reason: str, *, write_performed: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.write_performed = write_performed


def persist_decision(
    *,
    project_root: str | Path,
    decision: EnsembleAbstentionDecision,
    output_json: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    research_root = root / "data" / "research" / "aibot_parity" / "ensemble_abstention"
    report_root = root / "data" / "reports" / "aibot_parity" / "ensemble_abstention"
    research_policy = AtomicWritePolicy.restricted((research_root,), working_directory=root)
    report_policy = AtomicWritePolicy.restricted((report_root,), working_directory=root)

    try:
        decision_target = (
            research_root / decision.decision_id / "decision.json"
            if output_json is None
            else resolve_authorized_target(output_json, policy=research_policy)
        )
    except AtomicWriteError as exc:
        raise EnsemblePersistenceError(exc.reason) from exc

    audit_target = report_root / decision.decision_id / "decision_audit.json"
    decision_payload = decision.model_dump(mode="json")
    audit_payload = {
        "schema_version": "ensemble_abstention_decision_audit_v1",
        "decision_id": decision.decision_id,
        "status": decision.status.value,
        "research_action": decision.research_action.value,
        "reasons": list(decision.reasons),
        "regime_label": decision.regime_route.regime_label.value,
        "regime_confidence": decision.regime_route.regime_confidence,
        "regime_alignment": decision.regime_alignment.value,
        "disagreement_score": decision.disagreement_score,
        "uncertainty_score": decision.uncertainty_score,
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "changes_model": False,
        "writes_active_signals": False,
    }

    decision_written = _write_once(decision_target, decision_payload, policy=research_policy)
    try:
        audit_written = _write_once(audit_target, audit_payload, policy=report_policy)
    except EnsemblePersistenceError as exc:
        raise EnsemblePersistenceError(
            exc.reason,
            write_performed=decision_written or exc.write_performed,
        ) from exc
    return {
        "write_performed": decision_written or audit_written,
        "output_paths": {
            "decision": decision_target.relative_to(root).as_posix(),
            "audit": audit_target.relative_to(root).as_posix(),
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
            raise EnsemblePersistenceError("existing_output_not_regular_file")
        try:
            existing = json.loads(target.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EnsemblePersistenceError("existing_output_unreadable") from exc
        if existing == dict(payload):
            return False
        raise EnsemblePersistenceError("deterministic_output_conflict")
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
        raise EnsemblePersistenceError("ensemble_research_write_failed") from exc
    return result.write_performed
