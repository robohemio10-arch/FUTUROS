"""Immutable epoch identity and microsecond-precise causal contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


class EvidenceError(ValueError):
    """Stable fail-closed reason, never containing raw source data."""


def digest(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False) + "\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc(value: object) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value
    ):
        raise EvidenceError("timestamp_requires_aware_iso8601_microsecond_precision")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as exc:
        raise EvidenceError("timestamp_invalid") from exc


@dataclass(frozen=True)
class Identity:
    epoch_id: str
    freeze_v3_sha256: str
    activation_sha256: str
    prospective_start_utc: str
    git_commit_sha: str
    git_tree_sha: str
    model_artifact_sha256: str
    model_semantic_fingerprint: str
    dataset_fingerprint: str

    def mapping(self) -> dict[str, str]:
        return asdict(self)


CANONICAL = Identity(
    "qlib-v3-b0b722c25fd961938943af8c",
    "87331e2fec0aed73f4fdea57c00a8590f968cda67cbf1d9dc6839e10bd0bc03b",
    "a1756853239b2f52f5604b44ddfb15e6d1355fbfc50533ad046d5f04a5ce9fa9",
    "2026-09-12T13:17:28.798985Z",
    "30719913438b610d330da7cc457a3e429f0f245e",
    "cf933281c90378d59238bdf0968be015a78a9a8a",
    "76de6399ee9373bb786869e708f0f56d832897b50f0af0c39c199d1c3593405d",
    "e0b232ae874d4f187956b913504442ecb4371ac309abb8cfe7e80680ccee74ce",
    "4dd9eb5190125e016a512f689dcbb33592b7c79adc6825123e8c292ecfa54307",
)

SAFETY = dict.fromkeys((
    "historical_backfill_allowed", "nearest_timestamp_matching_allowed",
    "fuzzy_identity_matching_allowed", "model_promotion_performed", "active_model_changed",
    "writes_runtime", "writes_sqlite", "sends_orders", "exchange_private_access",
    "operational_authority", "changes_risk", "changes_leverage", "changes_stake",
    "changes_strategy", "live", "canary", "v2_evidence_imported", "runs_training",
), False) | {"research_only": True, "paper_only": True, "shadow_only": True}


def check_identity(value: object, expected: Identity) -> None:
    if not isinstance(value, dict):
        raise EvidenceError("v3_identity_missing")
    for key, required in expected.mapping().items():
        actual = value.get(key)
        if key == "prospective_start_utc":
            if utc(actual) != utc(required):
                raise EvidenceError("identity_mismatch:prospective_start_utc")
        elif actual != required:
            raise EvidenceError(f"identity_mismatch:{key}")


def check_origin(row: dict[str, Any]) -> None:
    if row.get("epoch_version") != "v3" or row.get("origin") != "natural_paper_runtime":
        raise EvidenceError("not_natural_v3_evidence")
    if any(row.get(key) is not False for key in ("replayed", "backfilled", "synthetic")):
        raise EvidenceError("unproven_natural_origin")
