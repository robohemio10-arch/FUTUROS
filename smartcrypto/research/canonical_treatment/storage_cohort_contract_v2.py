"""Explicit, unregistered L3.13 descriptor. This module cannot register an epoch."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from smartcrypto.learning.qlib_v3_prospective.activation import parse_json, safe_path
from smartcrypto.learning.qlib_v3_prospective.contracts import CANONICAL, EvidenceError, digest
from smartcrypto.research.canonical_treatment.causal_epoch2_foundation import (
    CausalEpoch2FoundationError,
    validate_registration,
)

SCHEMA_VERSION = "paper_b_storage_cohort_descriptor_v2"
APPROVED_PROPOSAL_SHA256 = "bf85cc70d46cdcca03df193c214b16884461c02d274d09f294073f2c4975254e"
MAX_SOURCE_BYTES = 32 * 1024 * 1024
ACCESS_ROLE = "research_read"
COMPONENTS = frozenset({
    "B09", "B10", "B14", "B15", "B16", "B17", "economic_monitor",
    "parity_collector", "Epoch2_foundation", "publisher_F02", "consumer_F02",
})
FROZEN_SOURCES = frozenset({
    "qlib_v3_store", "wq6_dataset", "wq6_feature_contract",
    "wq6_dataset_manifest", "wq6_split_manifest",
})
LOGICAL_SOURCES = FROZEN_SOURCES | frozenset({
    "branch09_report", "branch10_report", "branch11_report", "branch12_report",
    "branch13_report", "branch14_report", "branch15_report", "branch16_report",
    "sleeve_evidence", "council_ab_evidence", "monitor_report", "parity_report",
    "treatment_ledger", "operational_ledger_export", "cohort_registration",
    "control_trade_export", "treatment_trade_export",
})
BINDING_FIELDS = frozenset({
    "logical_source", "components", "scope", "epoch_namespace", "source_identity",
    "internal_path", "local_path", "mount_type", "mount_identity", "mount_target",
    "access", "uid", "gid", "acl", "artifact_sha256",
})


class ContractV2Error(ValueError):
    """Stable fail-closed contract rejection."""


def canonical_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(dict(value), sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    except (TypeError, ValueError, RecursionError) as exc:
        raise ContractV2Error("contract_not_canonical_json") from exc


def _exact_fields(value: object, fields: set[str] | frozenset[str], reason: str) -> Mapping:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ContractV2Error(reason)
    return value


def _sha(value: object, reason: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ContractV2Error(reason)
    return value


def read_snapshot(path: str | Path, *, limit: int = MAX_SOURCE_BYTES) -> bytes:
    """Bounded stable read, with no writes and rejection of reparse aliases."""
    source = Path(path)
    if not source.is_absolute() or ".." in source.parts:
        raise ContractV2Error("source_absolute_path_required")
    safe_path(source)
    before = source.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise ContractV2Error("source_regular_file_required")
    if before.st_size > limit:
        raise ContractV2Error("source_size_limit")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(source, flags)
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        raw = handle.read(limit + 1)
        ended = os.fstat(handle.fileno())
    safe_path(source)
    after = source.stat(follow_symlinks=False)

    def identity(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

    if len(raw) > limit:
        raise ContractV2Error("source_size_limit")
    if (
        not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(after.st_mode)
        or identity(before) != identity(opened) or identity(opened) != identity(ended)
        or identity(ended) != identity(after) or len(raw) != before.st_size
        or opened.st_ctime_ns != ended.st_ctime_ns or before.st_ctime_ns != after.st_ctime_ns
    ):
        raise ContractV2Error("source_changed_during_read")
    return raw


def approved_proposal() -> dict[str, Any]:
    path = Path(__file__).with_name("contracts_v2") / "approved_l313_proposal.json"
    raw = read_snapshot(path.absolute(), limit=256 * 1024)
    if hashlib.sha256(raw).hexdigest() != "6859474cff49a1d034d3c076773918a2fcb9af0f29add0d50f298e8288aae82f":
        raise ContractV2Error("packaged_proposal_hash_mismatch")
    proposal = parse_json(raw)
    if not isinstance(proposal, dict) or proposal.get("proposal_sha256") != APPROVED_PROPOSAL_SHA256:
        raise ContractV2Error("approved_proposal_identity_mismatch")
    if digest({k: v for k, v in proposal.items() if k != "proposal_sha256"}) != APPROVED_PROPOSAL_SHA256:
        raise ContractV2Error("approved_proposal_seal_invalid")
    return proposal


def binding_fingerprint(binding: Mapping[str, Any]) -> str:
    return digest(dict(binding))


def _absolute_internal(value: object) -> str:
    if not isinstance(value, str) or "\\" in value or "\0" in value:
        raise ContractV2Error("internal_path_invalid")
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ContractV2Error("internal_path_invalid")
    return value


def _validate_binding(binding: object, proposal: Mapping[str, Any]) -> Mapping:
    row = _exact_fields(binding, BINDING_FIELDS, "source_binding_fields_invalid")
    ref = row["logical_source"]
    if not isinstance(ref, str) or ref not in LOGICAL_SOURCES:
        raise ContractV2Error("logical_source_unknown")
    components = row["components"]
    if (
        not isinstance(components, list) or not components
        or any(not isinstance(c, str) or c not in COMPONENTS for c in components)
        or len(set(components)) != len(components)
    ):
        raise ContractV2Error("source_components_invalid")
    scope = row["scope"]
    namespaces = {
        "frozen": CANONICAL.epoch_id if ref == "qlib_v3_store" else "wq6-frozen-v1",
        "historical": proposal["predecessor"]["epoch_id"],
        "successor": "planned:" + proposal["proposal_id"],
    }
    if not isinstance(scope, str) or scope not in namespaces or row["epoch_namespace"] != namespaces[scope]:
        raise ContractV2Error("source_epoch_namespace_mismatch")
    if (scope == "frozen") != (ref in FROZEN_SOURCES):
        raise ContractV2Error("source_scope_invalid")
    _absolute_internal(row["internal_path"])
    target = _absolute_internal(row["mount_target"])
    try:
        PurePosixPath(row["internal_path"]).relative_to(target)
    except ValueError as exc:
        raise ContractV2Error("source_outside_mount") from exc
    if (
        not isinstance(row["local_path"], str)
        or not Path(row["local_path"]).is_absolute()
        or ".." in Path(row["local_path"]).parts
    ):
        raise ContractV2Error("source_local_path_invalid")
    if (
        row["mount_type"] not in {"bind", "volume"} or row["access"] != "RO"
        or type(row["uid"]) is not int or row["uid"] != 10001
        or type(row["gid"]) is not int or row["gid"] != 10001
        or not isinstance(row["acl"], str) or not row["acl"]
        or not isinstance(row["source_identity"], str) or not row["source_identity"]
        or not isinstance(row["mount_identity"], str) or not row["mount_identity"]
    ):
        raise ContractV2Error("source_mount_or_permission_invalid")
    _sha(row["artifact_sha256"], "source_artifact_sha256_invalid")
    if ref == "cohort_registration" and (
        scope != "historical"
        or row["artifact_sha256"] != proposal["predecessor"]["physical_sha256"]
    ):
        raise ContractV2Error("historical_registration_source_mismatch")
    if row["mount_type"] == "volume":
        roles = {v["docker_name"]: v for v in proposal["volume_roles"]}
        if row["mount_identity"] not in roles:
            raise ContractV2Error("source_volume_unknown")
        role = roles[row["mount_identity"]]
        expected_role = {
            "treatment_ledger": "treatment_ledger",
            "monitor_report": "monitor_reports",
        }.get(ref)
        if scope != "successor" or role["role"] != expected_role:
            raise ContractV2Error("source_volume_role_or_scope_mismatch")
        if not any(m["target"] == target for m in role["mounts"]):
            raise ContractV2Error("source_volume_target_mismatch")
        if row["acl"] != role["acl"]:
            raise ContractV2Error("source_volume_acl_mismatch")
    if ref in FROZEN_SOURCES:
        filenames = {
            "wq6_dataset": "official_trades_master_qlib_dataset_v1.parquet",
            "wq6_feature_contract": "official_trades_master_qlib_feature_contract_v1.json",
            "wq6_dataset_manifest": "official_trades_master_qlib_dataset_manifest_v1.json",
            "wq6_split_manifest": "official_trades_master_qlib_split_manifest_v1.json",
        }
        canonical_path = (
            "/app/data/research/qlib_v3/prospective_evidence/"
            + CANONICAL.epoch_id + "/" + CANONICAL.activation_sha256 + "/evidence.json"
            if ref == "qlib_v3_store"
            else "/app/data/research/frozen_wq6/" + filenames[ref]
        )
        if row["mount_type"] != "bind" or row["internal_path"] != canonical_path:
            raise ContractV2Error("frozen_source_canonical_path_required")
        if target in {"/", "/app", "/app/data", "/app/data/research"}:
            raise ContractV2Error("frozen_source_mount_too_broad")
    return row


def _validate(
    descriptor: Mapping[str, Any], predecessor: Mapping[str, Any], predecessor_sha: str,
    known_fingerprints: Mapping[str, Any],
) -> None:
    _exact_fields(descriptor, frozenset({
        "schema_version", "proposal", "fingerprints", "source_bindings", "descriptor_sha256",
    }), "descriptor_fields_invalid")
    if descriptor["schema_version"] != SCHEMA_VERSION:
        raise ContractV2Error("descriptor_schema_invalid")
    _sha(descriptor["descriptor_sha256"], "descriptor_sha256_invalid")
    if descriptor["descriptor_sha256"] != digest({
        k: v for k, v in descriptor.items() if k != "descriptor_sha256"
    }):
        raise ContractV2Error("descriptor_hash_mismatch")
    proposal = approved_proposal()
    if canonical_bytes(descriptor["proposal"]) != canonical_bytes(proposal):
        raise ContractV2Error("approved_proposal_changed")
    expected = proposal["predecessor"]
    if predecessor_sha != expected["physical_sha256"]:
        raise ContractV2Error("predecessor_physical_hash_mismatch")
    try:
        validate_registration(predecessor)
    except (CausalEpoch2FoundationError, EvidenceError, ValueError, KeyError, TypeError) as exc:
        raise ContractV2Error("historical_epoch2_registration_invalid") from exc
    if (
        predecessor.get("epoch_id") != expected["epoch_id"]
        or predecessor.get("registration_sha256") != expected["registration_sha256"]
        or predecessor.get("epoch_manifest", {}).get("epoch_manifest_sha256") != expected["epoch_manifest_sha256"]
        or predecessor.get("epoch_baseline", {}).get("epoch_baseline_sha256") != expected["epoch_baseline_sha256"]
        or predecessor.get("epoch_manifest", {}).get("qlib_v3_identity") != CANONICAL.mapping()
    ):
        raise ContractV2Error("historical_epoch2_reference_mismatch")
    bindings = descriptor["source_bindings"]
    if not isinstance(bindings, list) or not bindings:
        raise ContractV2Error("source_bindings_required")
    refs = []
    for binding in bindings:
        refs.append(_validate_binding(binding, proposal)["logical_source"])
    if len(set(refs)) != len(refs):
        raise ContractV2Error("source_binding_ambiguous")
    for field in ("local_path", "internal_path", "source_identity"):
        values = [
            os.path.normcase(b[field]) if field == "local_path" else b[field]
            for b in bindings
        ]
        if len(set(values)) != len(values):
            raise ContractV2Error("source_binding_alias_ambiguous")
    fingerprint_keys = frozenset({
        "compose_sha256", "images", "artifacts", "mount_roles_sha256", "sources",
    })
    fingerprints = _exact_fields(descriptor["fingerprints"], fingerprint_keys, "fingerprints_fields_invalid")
    expected_known = {k: v for k, v in known_fingerprints.items() if k != "authorized_local_roots"}
    if canonical_bytes(fingerprints) != canonical_bytes(expected_known):
        raise ContractV2Error("known_fingerprints_mismatch")
    _sha(fingerprints["compose_sha256"], "compose_sha256_invalid")
    if fingerprints["mount_roles_sha256"] != digest(proposal["volume_roles"]):
        raise ContractV2Error("mount_roles_fingerprint_mismatch")
    images = _exact_fields(fingerprints["images"], {"publisher", "treatment"}, "image_roles_invalid")
    if any(not isinstance(v, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", v) is None for v in images.values()):
        raise ContractV2Error("image_fingerprint_invalid")
    if fingerprints["artifacts"] != {
        "publisher": proposal["entry_fence"]["publisher_sha256"],
        "strategy": proposal["entry_fence"]["strategy_sha256"],
    }:
        raise ContractV2Error("implementation_fingerprint_mismatch")
    if fingerprints["sources"] != {b["logical_source"]: binding_fingerprint(b) for b in bindings}:
        raise ContractV2Error("source_fingerprint_mismatch")
    roots = known_fingerprints.get("authorized_local_roots")
    if not isinstance(roots, list) or not roots or any(
        not isinstance(root, str) or not Path(root).is_absolute() or ".." in Path(root).parts
        for root in roots
    ):
        raise ContractV2Error("authorized_local_roots_required")


@dataclass(frozen=True)
class ValidatedDescriptor:
    descriptor_json: bytes
    predecessor_json: bytes
    predecessor_raw_sha256: str
    known_fingerprints_json: bytes

    def mapping(self) -> dict[str, Any]:
        return parse_json(self.descriptor_json)

    def revalidate(self, known_fingerprints: Mapping[str, Any]) -> dict[str, Any]:
        if canonical_bytes(known_fingerprints) != self.known_fingerprints_json:
            raise ContractV2Error("validation_context_changed")
        value = self.mapping()
        _validate(value, parse_json(self.predecessor_json), self.predecessor_raw_sha256, known_fingerprints)
        return value


def validate_descriptor(
    descriptor: Mapping[str, Any], *, predecessor_registration: Mapping[str, Any],
    predecessor_raw_sha256: str, known_fingerprints: Mapping[str, Any],
) -> ValidatedDescriptor:
    # Canonical snapshots sever all mutable references supplied by the caller.
    descriptor_raw = canonical_bytes(descriptor)
    predecessor_raw = canonical_bytes(predecessor_registration)
    known_raw = canonical_bytes(known_fingerprints)
    _validate(parse_json(descriptor_raw), parse_json(predecessor_raw), predecessor_raw_sha256, parse_json(known_raw))
    return ValidatedDescriptor(descriptor_raw, predecessor_raw, predecessor_raw_sha256, known_raw)


def load_descriptor(
    path: str | Path, *, predecessor_path: str | Path,
    known_fingerprints: Mapping[str, Any],
) -> ValidatedDescriptor:
    raw = read_snapshot(path)
    predecessor_raw = read_snapshot(predecessor_path)
    try:
        descriptor, predecessor = parse_json(raw), parse_json(predecessor_raw)
    except RecursionError as exc:
        raise ContractV2Error("descriptor_json_nesting_invalid") from exc
    if not isinstance(descriptor, dict) or not isinstance(predecessor, dict):
        raise ContractV2Error("descriptor_or_predecessor_object_required")
    return validate_descriptor(
        descriptor, predecessor_registration=predecessor,
        predecessor_raw_sha256=hashlib.sha256(predecessor_raw).hexdigest(),
        known_fingerprints=known_fingerprints,
    )
