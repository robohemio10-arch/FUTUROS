"""Preflight-bound factory for the certified DecisionLedgerWriter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from smartcrypto.execution.decision_ledger_v4_2 import DecisionLedgerWriter

from .contracts import (
    PaperRuntimeWriterProfileV1,
    WriterFactoryReportV1,
    WriterPreflightReportV1,
)
from .preflight import profile_sha256

WriterConstructor = Callable[..., DecisionLedgerWriter]


@dataclass(frozen=True)
class WriterFactoryOutcomeV1:
    """Factory result keeps a possible writer separate from serializable evidence."""

    report: WriterFactoryReportV1
    writer: DecisionLedgerWriter | None


def create_paper_runtime_writer(
    *,
    profile: PaperRuntimeWriterProfileV1,
    preflight: WriterPreflightReportV1,
    writer_constructor: WriterConstructor = DecisionLedgerWriter,
) -> WriterFactoryOutcomeV1:
    """Create a writer only from a current, successful and explicit preflight."""

    current_hash = profile_sha256(profile)
    if preflight.profile_sha256 != current_hash:
        return _blocked(
            reason="stale_or_mismatched_preflight",
            current_hash=current_hash,
            preflight=preflight,
        )
    if not preflight.writer_creation_allowed or preflight.status != "ready":
        return _blocked(
            reason="writer_preflight_not_ready",
            current_hash=current_hash,
            preflight=preflight,
        )
    if not profile.enabled or not profile.runtime_write_authorized:
        return _blocked(
            reason="profile_not_explicitly_enabled",
            current_hash=current_hash,
            preflight=preflight,
        )

    writer = writer_constructor(
        ledger_path=preflight.path_policy.ledger_path,
        health_path=preflight.path_policy.health_path,
        allowed_root=preflight.path_policy.allowed_root,
        design_only=False,
        lock_timeout_seconds=profile.durability.lock_timeout_seconds,
        fsync_enabled=True,
    )
    report = WriterFactoryReportV1(
        status="created",
        reason="writer_created_after_successful_preflight",
        profile_sha256=current_hash,
        preflight_profile_sha256=preflight.profile_sha256,
        writer_created=True,
        safety_flags=profile.safety_flags,
    )
    return WriterFactoryOutcomeV1(report=report, writer=writer)


def _blocked(
    *,
    reason: str,
    current_hash: str,
    preflight: WriterPreflightReportV1,
) -> WriterFactoryOutcomeV1:
    report = WriterFactoryReportV1(
        status="blocked",
        reason=reason,
        profile_sha256=current_hash,
        preflight_profile_sha256=preflight.profile_sha256,
        writer_created=False,
        safety_flags=preflight.safety_flags,
    )
    return WriterFactoryOutcomeV1(report=report, writer=None)
