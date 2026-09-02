"""W4 regime + abstention service with fail-closed research-only boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .contracts import (
    AibotParityResearchConfig,
    EnsembleAbstentionRequest,
    EnsembleRunReport,
    EnsembleStatus,
)
from .ensemble import evaluate_ensemble_abstention
from .persistence import EnsemblePersistenceError, persist_decision

MAX_CONFIG_BYTES = 256 * 1024


def load_aibot_parity_config(
    project_root: str | Path,
    source: str | Path | dict[str, Any] | AibotParityResearchConfig,
) -> AibotParityResearchConfig:
    if isinstance(source, AibotParityResearchConfig):
        return source
    if isinstance(source, dict):
        return AibotParityResearchConfig.model_validate(source)

    root = Path(project_root).resolve()
    path = Path(source)
    path = path if path.is_absolute() else root / path
    path = path.resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("aibot_parity_config_outside_project") from exc
    if path.is_symlink():
        raise ValueError("aibot_parity_config_symlink_forbidden")
    if not path.is_file():
        raise ValueError("aibot_parity_config_missing")
    if path.suffix.casefold() not in {".yml", ".yaml"}:
        raise ValueError("aibot_parity_config_extension_invalid")
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError("aibot_parity_config_too_large")

    payload = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("aibot_parity_config_must_be_mapping")
    return AibotParityResearchConfig.model_validate(payload)


def run_ensemble_abstention(
    *,
    project_root: str | Path,
    config: AibotParityResearchConfig,
    request_payload: dict[str, Any] | EnsembleAbstentionRequest,
    write_report: bool = False,
    output_json: str | Path | None = None,
) -> EnsembleRunReport:
    try:
        request = (
            request_payload
            if isinstance(request_payload, EnsembleAbstentionRequest)
            else EnsembleAbstentionRequest.model_validate(request_payload)
        )
    except ValidationError as exc:
        return blocked_report(
            reason=f"request_validation_failed:{_compact_validation_errors(exc)}",
            write_requested=write_report,
        )

    decision = evaluate_ensemble_abstention(request, config.ensemble_abstention)
    write_performed = False
    output_paths: dict[str, str] = {}
    if write_report:
        try:
            persisted = persist_decision(
                project_root=project_root,
                decision=decision,
                output_json=output_json,
            )
        except EnsemblePersistenceError as exc:
            return blocked_report(
                reason=f"persistence_failed:{exc.reason}",
                request_id=request.request_id,
                decision=decision,
                write_requested=True,
                write_performed=exc.write_performed,
            )
        write_performed = bool(persisted["write_performed"])
        output_paths = dict(persisted["output_paths"])

    return EnsembleRunReport(
        status=decision.status,
        reason=(decision.reasons[0] if decision.reasons else None),
        request_id=request.request_id,
        decision=decision,
        write_requested=write_report,
        write_performed=write_performed,
        output_paths=output_paths,
    )


def blocked_report(
    *,
    reason: str,
    request_id: str | None = None,
    decision: object | None = None,
    write_requested: bool = False,
    write_performed: bool = False,
) -> EnsembleRunReport:
    from .contracts import EnsembleAbstentionDecision

    typed_decision = (
        None
        if decision is None
        else EnsembleAbstentionDecision.model_validate(decision)
    )
    return EnsembleRunReport(
        status=EnsembleStatus.BLOCKED,
        reason=reason,
        request_id=request_id,
        decision=typed_decision,
        write_requested=write_requested,
        write_performed=write_performed,
    )


def _compact_validation_errors(exc: ValidationError) -> str:
    parts: list[str] = []
    for item in exc.errors(include_url=False):
        location = ".".join(str(part) for part in item.get("loc", ()))
        message = str(item.get("msg", "validation_error")).replace(";", ",")
        parts.append(f"{location}:{message}")
    return "|".join(parts[:8])
