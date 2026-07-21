"""Pure builder for sanitized runtime-interruption quarantine evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

from .contracts import RuntimeInterruptionQuarantineV11

InterruptionStage = Literal[
    "preflight",
    "lock_acquisition",
    "append",
    "file_fsync",
    "health_update",
    "parent_directory_fsync",
]


def build_runtime_interruption_quarantine(
    *,
    event_id: str,
    interrupted_at_utc: datetime,
    interruption_stage: InterruptionStage,
    error_type: str,
    error_message_sha256: str,
    payload_sha256: str | None = None,
) -> RuntimeInterruptionQuarantineV11:
    """Build deterministic in-memory evidence from already-sanitized fields."""

    identity_payload = {
        "event_id": event_id,
        "interrupted_at_utc": interrupted_at_utc.isoformat(),
        "interruption_stage": interruption_stage,
        "error_type": error_type,
        "error_message_sha256": error_message_sha256,
        "payload_sha256": payload_sha256,
    }
    encoded = json.dumps(
        identity_payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    quarantine_id = f"quarantine-{hashlib.sha256(encoded).hexdigest()[:24]}"
    return RuntimeInterruptionQuarantineV11(
        quarantine_id=quarantine_id,
        event_id=event_id,
        interrupted_at_utc=interrupted_at_utc,
        interruption_stage=interruption_stage,
        error_type=error_type,
        error_message_sha256=error_message_sha256,
        payload_sha256=payload_sha256,
    )
