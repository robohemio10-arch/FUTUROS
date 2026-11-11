"""Synthetic unit fixtures. Historical validation is tested separately against V1."""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from smartcrypto.learning.qlib_v3_prospective.contracts import CANONICAL, digest
from smartcrypto.research.canonical_treatment import storage_cohort_contract_v2 as contract


def encode_source(value: object) -> bytes:
    return value if isinstance(value, bytes) else json.dumps(value, allow_nan=False).encode()


def materialized_b10_sources() -> dict[str, Any]:
    from tests import test_economic_control_treatment_scorecard_v1 as scorecard_fixtures

    dataset = b"synthetic materialized data bytes, never parsed or trained"
    dataset_sha256 = hashlib.sha256(dataset).hexdigest()
    feature_contract = {"validation_status": "ok", "contract_hash": "a" * 64}
    manifest = {
        "validation_status": "ok", "feature_contract_hash": "a" * 64,
        "dataset_hash": "b" * 64, "materialized_dataset_sha256": dataset_sha256,
    }
    splits = {
        "validation_status": "ok", "leakage_status": "ok",
        "feature_contract_hash": "a" * 64, "dataset_hash": "b" * 64,
        "split_manifest_hash": "c" * 64,
    }
    report = scorecard_fixtures._branch10()
    report["source"] = {
        "dataset_sha256": dataset_sha256,
        "feature_contract_sha256": hashlib.sha256(encode_source(feature_contract)).hexdigest(),
        "dataset_manifest_sha256": hashlib.sha256(encode_source(manifest)).hexdigest(),
        "split_manifest_sha256": hashlib.sha256(encode_source(splits)).hexdigest(),
        "dataset_hash": "b" * 64, "feature_contract_hash": "a" * 64,
        "split_manifest_hash": "c" * 64,
    }
    return {
        "wq6_dataset": dataset,
        "wq6_feature_contract": feature_contract,
        "wq6_dataset_manifest": manifest,
        "wq6_split_manifest": splits,
        "branch10_report": report,
    }


def predecessor_fixture() -> dict:
    expected = contract.approved_proposal()["predecessor"]
    return {
        "epoch_id": expected["epoch_id"],
        "registration_sha256": expected["registration_sha256"],
        "epoch_manifest": {
            "epoch_manifest_sha256": expected["epoch_manifest_sha256"],
            "qlib_v3_identity": CANONICAL.mapping(),
        },
        "epoch_baseline": {"epoch_baseline_sha256": expected["epoch_baseline_sha256"]},
    }


def reseal(descriptor: dict, known: dict, *, sync_known: bool = True) -> None:
    descriptor["fingerprints"]["sources"] = {
        binding["logical_source"]: contract.binding_fingerprint(binding)
        for binding in descriptor["source_bindings"]
    }
    descriptor["descriptor_sha256"] = digest({
        key: value for key, value in descriptor.items() if key != "descriptor_sha256"
    })
    if sync_known:
        roots = known["authorized_local_roots"]
        known.clear()
        known.update(copy.deepcopy(descriptor["fingerprints"]))
        known["authorized_local_roots"] = roots


def context_for_sources(tmp_path: Path, sources: Mapping[str, object], *, components=None):
    proposal = contract.approved_proposal()
    canonical_names = {
        "wq6_dataset": "official_trades_master_qlib_dataset_v1.parquet",
        "wq6_feature_contract": "official_trades_master_qlib_feature_contract_v1.json",
        "wq6_dataset_manifest": "official_trades_master_qlib_dataset_manifest_v1.json",
        "wq6_split_manifest": "official_trades_master_qlib_split_manifest_v1.json",
    }
    bindings = []
    for logical, value in sources.items():
        raw = encode_source(value)
        extension = ".parquet" if logical == "wq6_dataset" else ".json"
        path = tmp_path / (logical + extension)
        path.write_bytes(raw)
        scope = "frozen" if logical in contract.FROZEN_SOURCES else "historical"
        namespace = (
            CANONICAL.epoch_id if logical == "qlib_v3_store"
            else "wq6-frozen-v1" if scope == "frozen"
            else proposal["predecessor"]["epoch_id"]
        )
        if logical == "qlib_v3_store":
            internal = (
                "/app/data/research/qlib_v3/prospective_evidence/"
                + CANONICAL.epoch_id + "/" + CANONICAL.activation_sha256 + "/evidence.json"
            )
        elif logical in canonical_names:
            internal = "/app/data/research/frozen_wq6/" + canonical_names[logical]
        else:
            internal = "/app/data/reports/canonical_economic_plane/" + logical + ".json"
        bindings.append({
            "logical_source": logical,
            "components": list(components or ["B09", "B10", "B14", "B15", "B16", "B17"]),
            "scope": scope, "epoch_namespace": namespace,
            "source_identity": "synthetic-explicit:" + logical,
            "internal_path": internal, "local_path": str(path.absolute()),
            "mount_type": "bind", "mount_identity": "synthetic-source-" + logical,
            "mount_target": str(Path(internal).parent).replace("\\", "/"),
            "access": "RO", "uid": 10001, "gid": 10001,
            "acl": "u::r--,u:10001:r--,g::---,m::r--,o::---",
            "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        })
    fingerprints = {
        "compose_sha256": "c" * 64,
        "images": {"publisher": "sha256:" + "d" * 64, "treatment": "sha256:" + "e" * 64},
        "artifacts": {
            "publisher": proposal["entry_fence"]["publisher_sha256"],
            "strategy": proposal["entry_fence"]["strategy_sha256"],
        },
        "mount_roles_sha256": digest(proposal["volume_roles"]),
        "sources": {},
    }
    descriptor = {
        "schema_version": contract.SCHEMA_VERSION,
        "proposal": proposal,
        "fingerprints": fingerprints,
        "source_bindings": bindings,
    }
    known = {"authorized_local_roots": [str(tmp_path.absolute())]}
    reseal(descriptor, known)
    return descriptor, predecessor_fixture(), proposal["predecessor"]["physical_sha256"], known, copy.deepcopy(bindings)


def validate_synthetic(monkeypatch, context):
    """Patch only the official historical validator for synthetic unit headers."""
    monkeypatch.setattr(contract, "validate_registration", lambda _value: None)
    descriptor, predecessor, raw_sha, known, _observations = context
    return contract.validate_descriptor(
        descriptor, predecessor_registration=predecessor,
        predecessor_raw_sha256=raw_sha, known_fingerprints=known,
    )
