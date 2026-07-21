"""Fail-closed, read-only preflight for the paper writer profile."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path

from .contracts import (
    PaperRuntimeWriterProfileV1,
    PreflightCheckV1,
    RuntimeIdentityEvidenceV1,
    WriterPreflightReportV1,
)
from .path_policy import evaluate_path_policy


def profile_sha256(profile: PaperRuntimeWriterProfileV1) -> str:
    """Hash canonical profile content for preflight/factory binding."""

    payload = json.dumps(
        profile.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def inspect_current_identity() -> RuntimeIdentityEvidenceV1:
    """Inspect effective privilege without mutating the host."""

    get_effective_uid = getattr(os, "geteuid", None)
    if callable(get_effective_uid):
        effective_uid = int(get_effective_uid())
        return RuntimeIdentityEvidenceV1(
            source="posix_geteuid",
            verified=True,
            elevated=effective_uid == 0,
            effective_uid=effective_uid,
            reason="root_identity_detected" if effective_uid == 0 else "non_root_identity_verified",
        )

    if os.name == "nt":
        try:
            is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            return RuntimeIdentityEvidenceV1(
                source="unavailable",
                verified=False,
                elevated=None,
                effective_uid=None,
                reason="windows_elevation_status_unavailable",
            )
        return RuntimeIdentityEvidenceV1(
            source="windows_admin_api",
            verified=True,
            elevated=is_admin,
            effective_uid=None,
            reason="elevated_identity_detected" if is_admin else "non_root_identity_verified",
        )

    return RuntimeIdentityEvidenceV1(
        source="unavailable",
        verified=False,
        elevated=None,
        effective_uid=None,
        reason="non_root_identity_unverifiable",
    )


def run_writer_preflight(
    *,
    project_root: Path,
    profile: PaperRuntimeWriterProfileV1,
    identity: RuntimeIdentityEvidenceV1 | None = None,
) -> WriterPreflightReportV1:
    """Evaluate all gates without constructing a writer or touching storage."""

    path_policy = evaluate_path_policy(project_root=project_root, profile=profile)
    identity_evidence = identity or inspect_current_identity()
    checks = (
        _check(
            "profile_enabled",
            profile.enabled,
            "profile_explicitly_enabled",
            "profile_disabled_by_default",
        ),
        _check(
            "runtime_write_authorized",
            profile.runtime_write_authorized,
            "runtime_write_scope_explicitly_authorized",
            "runtime_write_scope_not_authorized",
        ),
        _check(
            "path_policy",
            path_policy.status == "ok",
            "path_policy_ok",
            path_policy.reason,
        ),
        _check(
            "non_root_identity",
            identity_evidence.verified and identity_evidence.elevated is False,
            "non_root_identity_verified",
            identity_evidence.reason,
        ),
        _check(
            "exclusive_lock",
            profile.durability.lock_required
            and profile.durability.lock_mode == "exclusive_create",
            "exclusive_lock_contract_ok",
            "exclusive_lock_contract_invalid",
        ),
        _check(
            "append_only",
            profile.durability.append_mode == "append_only",
            "append_only_contract_ok",
            "append_only_contract_invalid",
        ),
        _check(
            "fsync",
            profile.durability.file_fsync_required
            and profile.durability.health_fsync_required
            and profile.durability.parent_directory_fsync_required,
            "fsync_contract_ok",
            "fsync_contract_invalid",
        ),
        _check(
            "health",
            profile.health.health_required
            and profile.health.failure_counter_monotonic
            and profile.health.consecutive_failure_counter_required
            and profile.health.error_message_sha256_required
            and not profile.health.raw_error_message_persistence_allowed,
            "health_contract_ok",
            "health_contract_invalid",
        ),
        _check(
            "safety_boundary",
            profile.safety_flags.operational_authority is False
            and profile.safety_flags.runtime_integration_allowed is False
            and profile.safety_flags.sends_orders is False
            and profile.safety_flags.exchange_private_access is False
            and profile.safety_flags.changes_risk is False,
            "safety_boundary_ok",
            "safety_boundary_invalid",
        ),
    )
    writer_creation_allowed = all(check.status == "ok" for check in checks)
    first_blocker = next(
        (check.reason for check in checks if check.status == "blocked"),
        "writer_preflight_ready",
    )
    return WriterPreflightReportV1(
        status="ready" if writer_creation_allowed else "blocked",
        reason=first_blocker,
        profile_sha256=profile_sha256(profile),
        profile_enabled=profile.enabled,
        runtime_write_authorized=profile.runtime_write_authorized,
        path_policy=path_policy,
        identity=identity_evidence,
        checks=checks,
        writer_creation_allowed=writer_creation_allowed,
        safety_flags=profile.safety_flags,
    )


def _check(
    check_id: str,
    condition: bool,
    success_reason: str,
    blocked_reason: str,
) -> PreflightCheckV1:
    return PreflightCheckV1(
        check_id=check_id,
        status="ok" if condition else "blocked",
        reason=success_reason if condition else blocked_reason,
    )
