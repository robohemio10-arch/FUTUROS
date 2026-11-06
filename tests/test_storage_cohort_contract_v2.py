from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from smartcrypto.learning.qlib_v3_prospective.contracts import CANONICAL, digest
from smartcrypto.research.canonical_treatment import evidence_sources_v2 as sources
from smartcrypto.research.canonical_treatment import storage_cohort_contract_v2 as contract
from tests.v2_contract_fixtures import context_for_sources, reseal, validate_synthetic


def state():
    value = {"schema_version": "qlib_v3_prospective_evidence_store_v1",
             "identity": CANONICAL.mapping(), "signals": [], "outcomes": []}
    value["store_sha256"] = digest(value)
    return value


@pytest.fixture
def fixture_context(tmp_path, monkeypatch):
    context = context_for_sources(tmp_path, {"qlib_v3_store": state()})
    validated = validate_synthetic(monkeypatch, context)
    return context, validated


def resolve(context, validated, **updates):
    arguments = dict(component="B09", access_role="research_read",
                     logical_source="qlib_v3_store", known_fingerprints=context[3],
                     observations=context[4])
    arguments.update(updates)
    return sources.resolve_source(validated, **arguments)


def test_valid_explicit_source_and_immutable_context(fixture_context):
    context, validated = fixture_context
    value = resolve(context, validated)
    assert value.status == "ok" and json.loads(value.raw)["identity"] == CANONICAL.mapping()
    copy_of_descriptor = validated.mapping()
    copy_of_descriptor["proposal"]["successor"]["formal_activation_utc"] = "2030-01-01T00:00:00Z"
    assert validated.mapping()["proposal"]["successor"]["formal_activation_utc"] is None


@pytest.mark.parametrize("change", [
    "unknown", "hash", "t0", "v3", "epoch", "rw", "uid", "gid", "volume", "duplicate", "socket",
])
def test_descriptor_negative_controls(fixture_context, change):
    context, _ = fixture_context
    descriptor, predecessor, raw_sha, known, _ = context
    if change == "unknown":
        descriptor["operational_authority"] = True
    elif change == "hash":
        descriptor["descriptor_sha256"] = "0" * 64
    elif change == "t0":
        descriptor["proposal"]["successor"]["formal_activation_utc"] = "2030-01-01T00:00:00Z"
    elif change == "v3":
        descriptor["proposal"]["qlib_v3_identity"]["epoch_id"] = "qlib-v3-other"
    elif change == "epoch":
        descriptor["source_bindings"][0]["epoch_namespace"] = "paper-b-epoch-1"
    elif change == "rw":
        descriptor["source_bindings"][0]["access"] = "RW"
    elif change == "uid":
        descriptor["source_bindings"][0]["uid"] = 0
    elif change == "gid":
        descriptor["source_bindings"][0]["gid"] = 0
    elif change == "volume":
        descriptor["source_bindings"][0]["mount_type"] = "volume"
        descriptor["source_bindings"][0]["mount_identity"] = "unapproved-volume"
    elif change == "socket":
        descriptor["proposal"]["entry_fence"]["socket"] = "/tmp/other.sock"
    else:
        descriptor["source_bindings"].append(copy.deepcopy(descriptor["source_bindings"][0]))
    if change != "hash":
        reseal(descriptor, known)
    with pytest.raises(contract.ContractV2Error):
        contract.validate_descriptor(descriptor, predecessor_registration=predecessor,
                                     predecessor_raw_sha256=raw_sha, known_fingerprints=known)


def test_predecessor_physical_pin_cannot_be_replaced(fixture_context):
    context, _ = fixture_context
    with pytest.raises(contract.ContractV2Error, match="physical_hash"):
        contract.validate_descriptor(context[0], predecessor_registration=context[1],
                                     predecessor_raw_sha256="0" * 64, known_fingerprints=context[3])


def test_official_validator_failure_is_not_bypassed(fixture_context, monkeypatch):
    from smartcrypto.research.canonical_treatment.causal_epoch2_foundation import CausalEpoch2FoundationError
    context, _ = fixture_context

    def reject(_value):
        raise CausalEpoch2FoundationError("invalid fixture")

    monkeypatch.setattr(contract, "validate_registration", reject)
    with pytest.raises(contract.ContractV2Error, match="historical_epoch2_registration_invalid"):
        contract.validate_descriptor(context[0], predecessor_registration=context[1],
                                     predecessor_raw_sha256=context[2], known_fingerprints=context[3])


def test_known_fingerprint_is_independent(fixture_context):
    context, _ = fixture_context
    known = copy.deepcopy(context[3])
    known["images"]["treatment"] = "sha256:" + "0" * 64
    with pytest.raises(contract.ContractV2Error, match="known_fingerprints"):
        contract.validate_descriptor(context[0], predecessor_registration=context[1],
                                     predecessor_raw_sha256=context[2], known_fingerprints=known)


@pytest.mark.parametrize("kind", ["missing", "duplicate", "path_copy", "rw", "namespace"])
def test_observation_and_source_failures(fixture_context, tmp_path, kind):
    context, validated = fixture_context
    observations = copy.deepcopy(context[4])
    if kind == "missing":
        observations.clear()
    elif kind == "duplicate":
        observations.append(copy.deepcopy(observations[0]))
    elif kind == "path_copy":
        copy_path = tmp_path / "unapproved-copy.json"
        copy_path.write_bytes(Path(observations[0]["local_path"]).read_bytes())
        observations[0]["local_path"] = str(copy_path)
    elif kind == "rw":
        observations[0]["access"] = "RW"
    else:
        observations[0]["epoch_namespace"] = "paper-b-epoch-1"
    value = resolve(context, validated, observations=observations)
    assert value.status == "blocked" and value.raw is None


def test_content_hash_divergence_blocks(fixture_context):
    context, validated = fixture_context
    Path(context[4][0]["local_path"]).write_text("{}")
    result = resolve(context, validated)
    assert result.reason == "source_artifact_hash_mismatch"


def test_missing_file_never_becomes_empty_data(fixture_context):
    context, validated = fixture_context
    Path(context[4][0]["local_path"]).unlink()
    result = resolve(context, validated)
    assert result.status == "blocked" and result.raw is None


def test_unregistered_volume_source_is_blocked_before_open(tmp_path, monkeypatch):
    context = context_for_sources(tmp_path, {"treatment_ledger": {"rows": []}})
    descriptor, _predecessor, _sha, known, observations = context
    role = next(v for v in descriptor["proposal"]["volume_roles"] if v["role"] == "treatment_ledger")
    binding = descriptor["source_bindings"][0]
    binding.update(scope="successor", epoch_namespace="planned:" + descriptor["proposal"]["proposal_id"],
                   mount_type="volume", mount_identity=role["docker_name"],
                   internal_path="/app/data/research/canonical_treatment/decision_ledger_v1.json",
                   mount_target="/app/data/research/canonical_treatment", acl=role["acl"])
    reseal(descriptor, known)
    observations[:] = copy.deepcopy(descriptor["source_bindings"])
    validated = validate_synthetic(monkeypatch, context)
    value = sources.resolve_source(validated, component="B17", access_role="research_read",
                                   logical_source="treatment_ledger",
                                   known_fingerprints=known, observations=observations)
    assert value.reason == "successor_unregistered_no_t0"


def test_research_resolver_cannot_run_operational_roles(fixture_context):
    context, validated = fixture_context
    assert resolve(context, validated, component="publisher_F02").status == "blocked"
    assert resolve(context, validated, access_role="RW").status == "blocked"


def test_traversal_outside_root_is_rejected(fixture_context):
    context, _ = fixture_context
    descriptor, predecessor, raw_sha, known, _ = context
    descriptor["source_bindings"][0]["local_path"] += "/../secret.json"
    reseal(descriptor, known)
    with pytest.raises(contract.ContractV2Error, match="local_path"):
        contract.validate_descriptor(descriptor, predecessor_registration=predecessor,
                                     predecessor_raw_sha256=raw_sha, known_fingerprints=known)


def test_symlink_source_is_rejected(fixture_context, tmp_path):
    context, validated = fixture_context
    path = Path(context[4][0]["local_path"])
    target = tmp_path / "real-source.json"
    path.replace(target)
    try:
        path.symlink_to(target)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("file symlinks require Windows privilege")
        raise
    assert resolve(context, validated).reason == "symlink_or_junction_forbidden"


def test_safe_32_mib_read_remains_separate_from_global_reader(tmp_path):
    from smartcrypto.learning.qlib_v3_prospective.activation import MAX_JSON_BYTES, read_bytes
    path = tmp_path / "large.json"
    raw = b"{}" + b" " * MAX_JSON_BYTES
    path.write_bytes(raw)
    assert contract.read_snapshot(path) == raw
    with pytest.raises(Exception, match="source_size_limit"):
        read_bytes(path)
    with path.open("wb") as handle:
        handle.truncate(contract.MAX_SOURCE_BYTES + 1)
    with pytest.raises(contract.ContractV2Error, match="source_size_limit"):
        contract.read_snapshot(path)


def test_loader_rejects_duplicate_json_keys(tmp_path):
    path = tmp_path / "descriptor.json"
    path.write_text('{"x":1,"x":2}')
    predecessor = tmp_path / "predecessor.json"
    predecessor.write_text("{}")
    with pytest.raises(Exception, match="duplicate_json_key"):
        contract.load_descriptor(path, predecessor_path=predecessor, known_fingerprints={})


@pytest.mark.parametrize(("payload", "reason"), [
    ({"status": "ok"}, "historical_source_epoch_link_missing"),
    ({"epoch_id": "paper-b-epoch-1"}, "historical_source_epoch_mismatch"),
    ({"epoch_id": "paper-b-epoch-2", "experiment_id": "other"}, "historical_source_experiment_mismatch"),
])
def test_payload_cohort_cannot_be_relabelled_by_valid_binding(tmp_path, monkeypatch, payload, reason):
    context = context_for_sources(tmp_path, {"branch10_report": payload})
    validated = validate_synthetic(monkeypatch, context)
    result = sources.resolve_source(
        validated, component="B10", access_role="research_read", logical_source="branch10_report",
        known_fingerprints=context[3], observations=context[4],
    )
    assert result.status == "blocked" and result.reason == reason and result.raw is None


def test_explicit_matching_historical_payload_can_resolve(tmp_path, monkeypatch):
    context = context_for_sources(tmp_path, {"branch10_report": {
        "epoch_id": "paper-b-epoch-2", "experiment_id": "canonical-treatment-paper-v1"
    }})
    validated = validate_synthetic(monkeypatch, context)
    result = sources.resolve_source(
        validated, component="B10", access_role="research_read", logical_source="branch10_report",
        known_fingerprints=context[3], observations=context[4],
    )
    assert result.status == "ok"


def test_deep_historical_json_returns_structured_block(tmp_path, monkeypatch):
    raw = (
        b'{"epoch_id":"paper-b-epoch-2","nested":'
        + b'{"x":[' * 10000 + b'0' + b']}' * 10000 + b'}'
    )
    context = context_for_sources(tmp_path, {"branch10_report": raw})
    validated = validate_synthetic(monkeypatch, context)
    result = sources.resolve_source(
        validated, component="B10", access_role="research_read", logical_source="branch10_report",
        known_fingerprints=context[3], observations=context[4],
    )
    assert result.status == "blocked" and result.reason == "source_json_nesting_invalid"
    assert result.raw is None


def test_replacement_during_snapshot_read_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "source.json"
    path.write_text("{}")
    replacement = tmp_path / "replacement.json"
    replacement.write_text("{}")
    original = contract.safe_path
    calls = 0

    def substitute(source):
        nonlocal calls
        calls += 1
        if calls == 2:
            replacement.replace(source)
        return original(source)

    monkeypatch.setattr(contract, "safe_path", substitute)
    with pytest.raises(contract.ContractV2Error, match="source_changed_during_read"):
        contract.read_snapshot(path)


@pytest.mark.parametrize("field", ["local_path", "internal_path", "source_identity"])
def test_different_roles_cannot_share_ambiguous_authority_alias(tmp_path, monkeypatch, field):
    context = context_for_sources(tmp_path, {
        "branch09_report": {"epoch_id": "paper-b-epoch-2"},
        "branch10_report": {"epoch_id": "paper-b-epoch-2"},
    })
    monkeypatch.setattr(contract, "validate_registration", lambda _value: None)
    descriptor, predecessor, raw_sha, known, _ = context
    descriptor["source_bindings"][1][field] = descriptor["source_bindings"][0][field]
    reseal(descriptor, known)
    with pytest.raises(contract.ContractV2Error, match="alias_ambiguous"):
        contract.validate_descriptor(descriptor, predecessor_registration=predecessor,
                                     predecessor_raw_sha256=raw_sha, known_fingerprints=known)
