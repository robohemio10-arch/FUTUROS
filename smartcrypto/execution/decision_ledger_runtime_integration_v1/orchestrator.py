"""Pure after-RiskManager integration preview for P0.4C."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal, Mapping, Sequence

from smartcrypto.execution.decision_ledger_runtime_profile_v1 import map_runtime_decision

from .contracts import (
    IntegrationPreviewResultV1,
    ProjectionFailureV1,
    SandboxIntegrationConfigV1,
)
from .envelope import attach_decision_envelope
from .source_adapter import SignalSourceValidationError, build_runtime_decision_input


def preview_after_risk_manager(
    *,
    approved_signals: Sequence[Mapping[str, Any]],
    rejected_signals: Sequence[Mapping[str, Any]],
    decision_timestamp: datetime,
    config: SandboxIntegrationConfigV1 | Mapping[str, Any] | None = None,
) -> IntegrationPreviewResultV1:
    resolved = (
        config
        if isinstance(config, SandboxIntegrationConfigV1)
        else SandboxIntegrationConfigV1.model_validate(config or {})
    )
    approved = list(approved_signals)
    rejected = list(rejected_signals)
    source_count = len(approved) + len(rejected)

    if not resolved.enabled:
        return IntegrationPreviewResultV1(
            status="disabled",
            reason="p0_4c_preview_disabled",
            source_signal_count=source_count,
            approved_source_count=len(approved),
            rejected_source_count=len(rejected),
            projected_decision_count=0,
            active_envelope_count=len(approved),
            projection_failure_count=0,
            active_signals=tuple(dict(item) for item in approved),
            decision_projections=(),
            failures=(),
        )

    projections = []
    active_signals: list[dict[str, object]] = []
    failures: list[ProjectionFailureV1] = []
    combined = [(True, item) for item in approved] + [(False, item) for item in rejected]

    for source_index, (expected_approved, signal) in enumerate(combined):
        try:
            if (signal.get("risk_approved") is True) != expected_approved:
                raise ValueError("risk_partition_mismatch")
            runtime_input = build_runtime_decision_input(
                signal,
                decision_timestamp=decision_timestamp,
            )
            projection = map_runtime_decision(runtime_input)
            projections.append(projection)
            if expected_approved:
                active_signals.append(attach_decision_envelope(signal, projection))
        except Exception as exc:
            missing_fields = (
                exc.missing_fields
                if isinstance(exc, SignalSourceValidationError)
                else ()
            )
            failures.append(
                ProjectionFailureV1(
                    source_index=source_index,
                    pair=_optional_text(signal.get("pair")),
                    symbol=_optional_text(signal.get("symbol")),
                    side=_optional_text(signal.get("side")),
                    risk_approved=(
                        signal.get("risk_approved")
                        if type(signal.get("risk_approved")) is bool
                        else None
                    ),
                    error_type=type(exc).__name__,
                    error_message_sha256=hashlib.sha256(
                        str(exc).encode("utf-8")
                    ).hexdigest(),
                    missing_fields=tuple(missing_fields),
                )
            )

    approved_failures = sum(
        1 for failure in failures if failure.source_index < len(approved)
    )
    status: Literal["ok", "blocked"] = "blocked" if approved_failures else "ok"
    reason = "approved_signal_projection_failed" if approved_failures else None

    return IntegrationPreviewResultV1(
        status=status,
        reason=reason,
        source_signal_count=source_count,
        approved_source_count=len(approved),
        rejected_source_count=len(rejected),
        projected_decision_count=len(projections),
        active_envelope_count=len(active_signals),
        projection_failure_count=len(failures),
        active_signals=tuple(active_signals),
        decision_projections=tuple(projections),
        failures=tuple(failures),
    )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
