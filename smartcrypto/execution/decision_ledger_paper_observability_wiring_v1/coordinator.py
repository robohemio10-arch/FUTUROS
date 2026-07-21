"""Shared producer coordinator for post-RiskManager paper observability."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from smartcrypto.execution.decision_ledger_paper_runtime_writer_v1 import (
    RuntimeIdentityEvidenceV1,
    create_paper_runtime_writer,
    run_writer_preflight,
)
from smartcrypto.execution.decision_ledger_runtime_integration_v1 import (
    SandboxIntegrationConfigV1,
    preview_after_risk_manager,
)
from smartcrypto.execution.signal_risk_gate import RiskGateResult

from .config import load_observability_config
from .contracts import (
    DEFAULT_CONFIG_PATH,
    PreparedSignalBatchV1,
    WiringReportV1,
)
from .lineage import complete_after_risk_manager, prepare_signal_batch
from .sink import IdempotentDecisionLedgerRuntimeSink


@dataclass(frozen=True)
class PaperObservabilityOutcomeV1:
    active_signals: list[dict[str, Any]]
    report: WiringReportV1


def prepare_before_risk_manager(
    candidate_signals: Sequence[Mapping[str, Any]],
    *,
    producer_id: str,
    config_source: str | Path | Mapping[str, Any] | None = DEFAULT_CONFIG_PATH,
) -> PreparedSignalBatchV1:
    """Build deterministic lineage before RiskManager, or preserve disabled input."""

    config = load_observability_config(config_source)
    return prepare_signal_batch(
        candidate_signals,
        producer_id=producer_id,
        config=config,
    )


def finalize_after_risk_manager(
    prepared: PreparedSignalBatchV1,
    *,
    risk_gate: RiskGateResult,
    decision_timestamp: datetime | None = None,
    project_root: str | Path = ".",
    identity: RuntimeIdentityEvidenceV1 | None = None,
) -> PaperObservabilityOutcomeV1:
    """Project, persist and envelope before publication when explicitly enabled."""

    config = prepared.config
    approved = [dict(item) for item in risk_gate.approved_signals]
    rejected = [dict(item) for item in risk_gate.rejected_signals]
    if not config.enabled:
        return PaperObservabilityOutcomeV1(
            active_signals=approved,
            report=_disabled_report(prepared, approved, rejected),
        )

    completed_approved = [
        complete_after_risk_manager(item, expected_approved=True) for item in approved
    ]
    completed_rejected = [
        complete_after_risk_manager(item, expected_approved=False) for item in rejected
    ]
    resolved_timestamp = decision_timestamp or datetime.now(timezone.utc)
    preview = preview_after_risk_manager(
        approved_signals=completed_approved,
        rejected_signals=completed_rejected,
        decision_timestamp=resolved_timestamp,
        config=SandboxIntegrationConfigV1(mode="preview", enabled=True),
    )
    failures = tuple(item.model_dump(mode="json") for item in preview.failures)
    if preview.status != "ok":
        return PaperObservabilityOutcomeV1(
            active_signals=[],
            report=WiringReportV1(
                status="blocked",
                reason=preview.reason or "decision_projection_failed",
                producer_id=prepared.producer_id,
                enabled=True,
                writer_enabled=config.writer_enabled,
                trade_link_enabled=config.trade_link_enabled,
                source_signal_count=prepared.source_signal_count,
                approved_signal_count=len(approved),
                rejected_signal_count=len(rejected),
                projected_decision_count=preview.projected_decision_count,
                persisted_decision_count=0,
                duplicate_decision_count=0,
                active_envelope_count=0,
                projection_failure_count=preview.projection_failure_count,
                publication_blocked=True,
                writer_invoked=False,
                writes_runtime=False,
                runtime_integration_executed=False,
                preflight_status=None,
                factory_status=None,
                failures=failures,
                safety_flags=config.safety_flags,
            ),
        )

    root = Path(project_root).expanduser().resolve(strict=False)
    preflight = run_writer_preflight(
        project_root=root,
        profile=config.writer_profile,
        identity=identity,
    )
    factory = create_paper_runtime_writer(
        profile=config.writer_profile,
        preflight=preflight,
    )
    if factory.writer is None:
        return PaperObservabilityOutcomeV1(
            active_signals=[],
            report=_factory_blocked_report(
                prepared=prepared,
                approved=approved,
                rejected=rejected,
                preview_count=preview.projected_decision_count,
                failure_count=preview.projection_failure_count,
                failures=failures,
                preflight_status=preflight.status,
                factory_status=factory.report.status,
                reason=factory.report.reason,
            ),
        )

    index_path = _resolve_project_relative(root, config.index_path)
    sink = IdempotentDecisionLedgerRuntimeSink(
        writer=factory.writer,
        index_path=index_path,
        lock_timeout_seconds=config.writer_profile.durability.lock_timeout_seconds,
    )
    receipts = []
    try:
        for projection in preview.decision_projections:
            receipts.append(sink.append(projection))
    except Exception as exc:  # noqa: BLE001 - fail-closed publication boundary.
        failure = {
            "error_type": type(exc).__name__,
            "error_message_sha256": hashlib.sha256(
                str(exc).encode("utf-8")
            ).hexdigest(),
        }
        return PaperObservabilityOutcomeV1(
            active_signals=[],
            report=WiringReportV1(
                status="blocked",
                reason=f"decision_persistence_failed:{type(exc).__name__}",
                producer_id=prepared.producer_id,
                enabled=True,
                writer_enabled=config.writer_enabled,
                trade_link_enabled=config.trade_link_enabled,
                source_signal_count=prepared.source_signal_count,
                approved_signal_count=len(approved),
                rejected_signal_count=len(rejected),
                projected_decision_count=preview.projected_decision_count,
                persisted_decision_count=sum(
                    receipt.append_performed for receipt in receipts
                ),
                duplicate_decision_count=sum(receipt.duplicate for receipt in receipts),
                active_envelope_count=0,
                projection_failure_count=preview.projection_failure_count,
                publication_blocked=True,
                writer_invoked=True,
                writes_runtime=any(receipt.append_performed for receipt in receipts),
                runtime_integration_executed=False,
                preflight_status=preflight.status,
                factory_status=factory.report.status,
                failures=(*failures, failure),
                receipts=tuple(receipts),
                safety_flags=config.safety_flags,
            ),
        )

    return PaperObservabilityOutcomeV1(
        active_signals=[dict(item) for item in preview.active_signals],
        report=WiringReportV1(
            status="ok",
            reason=None,
            producer_id=prepared.producer_id,
            enabled=True,
            writer_enabled=config.writer_enabled,
            trade_link_enabled=config.trade_link_enabled,
            source_signal_count=prepared.source_signal_count,
            approved_signal_count=len(approved),
            rejected_signal_count=len(rejected),
            projected_decision_count=preview.projected_decision_count,
            persisted_decision_count=sum(
                receipt.append_performed for receipt in receipts
            ),
            duplicate_decision_count=sum(receipt.duplicate for receipt in receipts),
            active_envelope_count=len(preview.active_signals),
            projection_failure_count=preview.projection_failure_count,
            publication_blocked=False,
            writer_invoked=bool(receipts),
            writes_runtime=any(receipt.append_performed for receipt in receipts),
            runtime_integration_executed=False,
            preflight_status=preflight.status,
            factory_status=factory.report.status,
            failures=failures,
            receipts=tuple(receipts),
            safety_flags=config.safety_flags,
        ),
    )


def _disabled_report(
    prepared: PreparedSignalBatchV1,
    approved: Sequence[Mapping[str, Any]],
    rejected: Sequence[Mapping[str, Any]],
) -> WiringReportV1:
    return WiringReportV1(
        status="disabled",
        reason="paper_observability_wiring_disabled_by_default",
        producer_id=prepared.producer_id,
        enabled=False,
        writer_enabled=False,
        trade_link_enabled=False,
        source_signal_count=prepared.source_signal_count,
        approved_signal_count=len(approved),
        rejected_signal_count=len(rejected),
        projected_decision_count=0,
        persisted_decision_count=0,
        duplicate_decision_count=0,
        active_envelope_count=0,
        projection_failure_count=0,
        publication_blocked=False,
        writer_invoked=False,
        writes_runtime=False,
        runtime_integration_executed=False,
        preflight_status=None,
        factory_status=None,
        safety_flags=prepared.config.safety_flags,
    )


def _factory_blocked_report(
    *,
    prepared: PreparedSignalBatchV1,
    approved: Sequence[Mapping[str, Any]],
    rejected: Sequence[Mapping[str, Any]],
    preview_count: int,
    failure_count: int,
    failures: tuple[dict[str, object], ...],
    preflight_status: str,
    factory_status: str,
    reason: str,
) -> WiringReportV1:
    config = prepared.config
    return WiringReportV1(
        status="blocked",
        reason=reason,
        producer_id=prepared.producer_id,
        enabled=True,
        writer_enabled=config.writer_enabled,
        trade_link_enabled=config.trade_link_enabled,
        source_signal_count=prepared.source_signal_count,
        approved_signal_count=len(approved),
        rejected_signal_count=len(rejected),
        projected_decision_count=preview_count,
        persisted_decision_count=0,
        duplicate_decision_count=0,
        active_envelope_count=0,
        projection_failure_count=failure_count,
        publication_blocked=True,
        writer_invoked=False,
        writes_runtime=False,
        runtime_integration_executed=False,
        preflight_status=preflight_status,
        factory_status=factory_status,
        failures=failures,
        safety_flags=config.safety_flags,
    )


def _resolve_project_relative(root: Path, value: str) -> Path:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("observability_index_path_unsafe")
    return (root / Path(*path.parts)).resolve(strict=False)
