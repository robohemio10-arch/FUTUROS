"""Read-only validation of the certified activation and its immutable freeze."""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .contracts import CANONICAL, EvidenceError, Identity, digest, utc

MAX_JSON_BYTES = 16 * 1024 * 1024


def safe_path(path: Path) -> Path:
    path = path.absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or (part.exists() and getattr(part.lstat(), "st_file_attributes", 0)
                                 & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise EvidenceError("symlink_or_junction_forbidden")
    return path


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("duplicate_json_key")
        result[key] = value
    return result


def parse_json(raw: bytes) -> Any:
    try:
        return json.loads(raw, object_pairs_hook=unique_object,
                          parse_constant=lambda _: invalid_constant())
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("invalid_json") from exc


def invalid_constant() -> None:
    raise EvidenceError("non_finite_json")


def read_bytes(path: Path) -> bytes:
    path = safe_path(path)
    if not path.is_file():
        raise EvidenceError("source_missing_or_not_file")
    with path.open("rb") as handle:
        raw = handle.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise EvidenceError("source_size_limit")
    return raw


def read_object(path: Path) -> dict[str, Any]:
    value = parse_json(read_bytes(path))
    if not isinstance(value, dict):
        raise EvidenceError("json_object_required")
    return value


@dataclass(frozen=True)
class Activation:
    identity: Identity
    boundary: datetime
    activated_at: datetime
    activation_file_sha256: str
    freeze_file_sha256: str


def load_activation(activation_path: Path, freeze_path: Path,
                    expected: Identity = CANONICAL) -> Activation:
    raw = read_bytes(activation_path)
    freeze_raw = read_bytes(freeze_path)
    value, freeze = parse_json(raw), parse_json(freeze_raw)
    if not isinstance(value, dict) or not isinstance(freeze, dict):
        raise EvidenceError("activation_or_freeze_not_object")
    if value.get("schema_version") != "qlib_v3_prospective_activation_v1":
        raise EvidenceError("activation_schema_mismatch")
    if freeze.get("schema_version") != "qlib_v3_complete_freeze_manifest_v2":
        raise EvidenceError("freeze_schema_mismatch")
    if value.get("epoch_version") != "v3" or freeze.get("epoch_version") != "v3":
        raise EvidenceError("epoch_version_mismatch")
    if value.get("activation_sha256") != expected.activation_sha256:
        raise EvidenceError("activation_sha256_mismatch")
    if digest({k: v for k, v in value.items() if k != "activation_sha256"}) != expected.activation_sha256:
        raise EvidenceError("activation_content_hash_mismatch")
    for key in ("epoch_id", "freeze_v3_sha256"):
        if value.get(key) != getattr(expected, key) or freeze.get(key) != getattr(expected, key):
            raise EvidenceError(f"activation_link_mismatch:{key}")
    contract = freeze.get("contract")
    if not isinstance(contract, dict) or digest(
        {k: v for k, v in freeze.items() if k != "freeze_v3_sha256"}
    ) != expected.freeze_v3_sha256:
        raise EvidenceError("freeze_contract_hash_mismatch")
    for key in ("git_commit_sha", "git_tree_sha", "model_artifact_sha256",
                "model_semantic_fingerprint", "dataset_fingerprint"):
        if contract.get(key) != getattr(expected, key):
            raise EvidenceError(f"freeze_identity_mismatch:{key}")
    for payload in (value, freeze):
        for flag in ("historical_backfill_allowed", "model_promotion_performed",
                     "active_model_changed", "writes_runtime", "writes_sqlite",
                     "sends_orders", "exchange_private_access", "operational_authority",
                     "changes_risk", "changes_leverage", "changes_stake", "changes_strategy"):
            if payload.get(flag) is not False:
                raise EvidenceError(f"unsafe_activation_or_freeze:{flag}")
        if any(payload.get(flag) is not True for flag in ("paper_only", "shadow_only", "research_only")):
            raise EvidenceError("paper_shadow_research_required")
    if value.get("backfill_allowed") is not False:
        raise EvidenceError("backfill_forbidden")
    boundary = utc(value.get("prospective_start_utc"))
    activated = utc(value.get("activated_at_utc"))
    cutoff = utc(contract.get("training_cutoff_utc"))
    if boundary != utc(expected.prospective_start_utc):
        raise EvidenceError("activation_boundary_mismatch")
    delay = value.get("activation_delay_seconds")
    if type(delay) is not int or delay != 60 or boundary - activated != timedelta(seconds=60):
        raise EvidenceError("activation_delay_mismatch")
    if not cutoff < activated < boundary:
        raise EvidenceError("activation_temporal_order_invalid")
    return Activation(expected, boundary, activated, hashlib.sha256(raw).hexdigest(),
                      hashlib.sha256(freeze_raw).hexdigest())
