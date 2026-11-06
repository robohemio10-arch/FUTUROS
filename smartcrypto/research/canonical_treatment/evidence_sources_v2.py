"""Resolve one explicit offline evidence binding without discovery or runtime access."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smartcrypto.learning.qlib_v3_prospective.activation import parse_json, safe_path
from smartcrypto.learning.qlib_v3_prospective.contracts import EvidenceError
from .storage_cohort_contract_v2 import (
    ACCESS_ROLE, BINDING_FIELDS, COMPONENTS, ContractV2Error, ValidatedDescriptor,
    canonical_bytes, read_snapshot,
)


@dataclass(frozen=True)
class SourceResolution:
    status: str
    reason: str
    internal_path: str | None = None
    source_identity: str | None = None
    epoch_namespace: str | None = None
    mount_type: str | None = None
    access: str | None = None
    expected_sha256: str | None = None
    local_path: str | None = None
    raw: bytes | None = None

    def metadata(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "raw"}


def resolve_source(
    descriptor: ValidatedDescriptor, *, component: str, access_role: str,
    logical_source: str, known_fingerprints: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
) -> SourceResolution:
    metadata: dict[str, Any] = {}
    try:
        if not isinstance(descriptor, ValidatedDescriptor):
            raise ContractV2Error("validated_descriptor_required")
        contract = descriptor.revalidate(known_fingerprints)
        if component not in COMPONENTS or component in {"publisher_F02", "consumer_F02"}:
            raise ContractV2Error("offline_component_required")
        if access_role != ACCESS_ROLE:
            raise ContractV2Error("research_read_role_required")
        matches = [b for b in contract["source_bindings"] if b["logical_source"] == logical_source]
        if len(matches) != 1:
            raise ContractV2Error("source_binding_missing_or_ambiguous")
        binding = matches[0]
        metadata = {
            "internal_path": binding["internal_path"], "source_identity": binding["source_identity"],
            "epoch_namespace": binding["epoch_namespace"], "mount_type": binding["mount_type"],
            "access": binding["access"], "expected_sha256": binding["artifact_sha256"],
            "local_path": binding["local_path"],
        }
        if component not in binding["components"]:
            raise ContractV2Error("component_not_authorized_for_source")
        if binding["scope"] == "successor":
            raise ContractV2Error("successor_unregistered_no_t0")
        if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
            raise ContractV2Error("explicit_source_observations_required")
        observed = [o for o in observations if isinstance(o, Mapping) and o.get("logical_source") == logical_source]
        if len(observed) != 1:
            raise ContractV2Error("source_missing_or_ambiguous")
        if set(observed[0]) != BINDING_FIELDS or canonical_bytes(observed[0]) != canonical_bytes(binding):
            raise ContractV2Error("observed_source_fingerprint_mismatch")
        path = Path(binding["local_path"])
        if ".." in path.parts:
            raise ContractV2Error("source_path_traversal_forbidden")
        # Explicit research roots cannot turn a live runtime path into an export.
        forbidden = {"runtime", "userdata", "user_data"}
        if any(part.lower() in forbidden for part in path.parts) or path.suffix.lower() in {
            ".db", ".sqlite", ".sqlite3",
        }:
            raise ContractV2Error("operational_source_forbidden")
        safe_path(path)
        resolved_path = path.resolve(strict=True)
        roots = [
            safe_path(Path(p)).resolve(strict=True)
            for p in known_fingerprints["authorized_local_roots"]
        ]
        if not any(resolved_path.is_relative_to(root) for root in roots):
            raise ContractV2Error("source_outside_authorized_research_roots")
        raw = read_snapshot(path)
        if hashlib.sha256(raw).hexdigest() != binding["artifact_sha256"]:
            raise ContractV2Error("source_artifact_hash_mismatch")
        if binding["scope"] == "historical":
            try:
                payload = parse_json(raw)
            except RecursionError as exc:
                raise ContractV2Error("source_json_nesting_invalid") from exc
            if not isinstance(payload, dict) or "epoch_id" not in payload:
                raise ContractV2Error("historical_source_epoch_link_missing")
            if payload["epoch_id"] != binding["epoch_namespace"]:
                raise ContractV2Error("historical_source_epoch_mismatch")
            if (
                "experiment_id" in payload
                and payload["experiment_id"] != "canonical-treatment-paper-v1"
            ):
                raise ContractV2Error("historical_source_experiment_mismatch")
        return SourceResolution("ok", "exact_offline_source_verified", **metadata, raw=raw)
    except (ContractV2Error, EvidenceError, OSError, KeyError, TypeError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, (ContractV2Error, EvidenceError)) else (
            "offline_source_failed:" + type(exc).__name__
        )
        return SourceResolution("blocked", reason, **metadata)
