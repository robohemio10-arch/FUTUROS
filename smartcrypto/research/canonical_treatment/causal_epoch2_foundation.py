"""Create-once causal Epoch 2 foundation for Paper B after proven runtime drift."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.learning.qlib_v3_prospective.activation import read_object, safe_path
from smartcrypto.learning.qlib_v3_prospective.contracts import (
    CANONICAL,
    EvidenceError,
    check_identity,
    digest,
    utc,
)
from smartcrypto.learning.qlib_v3_prospective.store import exclusive
from smartcrypto.research.canonical_treatment import causal_governance as governance
from smartcrypto.research.canonical_treatment.postfix_causal_soak_foundation import (
    BASELINE_SCHEMA_VERSION as EPOCH1_BASELINE_SCHEMA_VERSION,
    SAFETY_FLAGS as EPOCH1_SAFETY_FLAGS,
    PostfixSoakFoundationError,
    validate_baseline as validate_epoch1_baseline,
)
from smartcrypto.research.canonical_treatment.publisher import LEDGER_SCHEMA_VERSION
from smartcrypto.runtime.integrity_traceability_v2 import (
    AtomicWritePolicy,
    atomic_write_json,
)


SCHEMA_VERSION = "paper_b_causal_epoch2_foundation_v1"
REGISTRATION_SCHEMA_VERSION = "paper_b_causal_epoch2_registration_v1"
EPOCH_MANIFEST_SCHEMA_VERSION = "paper_b_causal_epoch_manifest_v1"
EPOCH_BASELINE_SCHEMA_VERSION = "paper_b_causal_epoch_baseline_v1"

EPOCH1_ID = "paper-b-epoch-1"
EPOCH2_ID = "paper-b-epoch-2"

DEFAULT_REGISTRATION_RELATIVE_PATH = Path(
    "paper_b_epoch_2/epoch_registration_v1.json"
)
DEFAULT_REPORT_RELATIVE_PATH = Path(
    "paper_b_epoch_2/epoch2_foundation_report_v1.json"
)

EPOCH2_SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "runtime_read_only": True,
    "operational_authority": False,
    "historical_backfill_allowed": False,
    "nearest_timestamp_matching_allowed": False,
    "fuzzy_identity_matching_allowed": False,
    "changes_model": False,
    "changes_strategy": False,
    "changes_risk": False,
    "changes_leverage": False,
    "changes_stake": False,
    "changes_threshold": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "runs_training": False,
    "promotion_allowed": False,
    "economic_edge_certified": False,
}


class CausalEpoch2FoundationError(RuntimeError):
    """Stable fail-closed error for Paper B Epoch 2 foundation."""


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _canonical_timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise CausalEpoch2FoundationError("timestamp_not_string")
    match = re.fullmatch(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})",
        value,
    )
    if match is None:
        raise CausalEpoch2FoundationError("timestamp_invalid")
    fraction = (match.group(2) or "")[:6].rstrip("0")
    canonical = match.group(1)
    if fraction:
        canonical += "." + fraction
    canonical += match.group(3)
    return _iso(utc(canonical))


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_sha256(value: object, reason: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise CausalEpoch2FoundationError(reason)
    return value


def _resolve_under(root: Path, value: str | Path) -> Path:
    base = safe_path(root.resolve(strict=False))
    logical = Path(value)
    candidate = safe_path(
        logical.resolve(strict=False)
        if logical.is_absolute()
        else (base / logical).resolve(strict=False)
    )
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise CausalEpoch2FoundationError("artifact_path_outside_evidence_root") from exc
    return candidate


def _flatten(value: object, prefix: str = "") -> dict[str, object]:
    if isinstance(value, Mapping) and value:
        result: dict[str, object] = {}
        for key, child in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten(child, name))
        return result
    return {prefix: value}


def fingerprint_differences(
    registered: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[dict[str, Any]]:
    left = _flatten(registered)
    right = _flatten(current)
    missing = "<MISSING>"
    return [
        {"path": key, "registered": left.get(key, missing), "current": right.get(key, missing)}
        for key in sorted(set(left) | set(right))
        if left.get(key, missing) != right.get(key, missing)
    ]


def drift_categories(differences: list[Mapping[str, Any]]) -> dict[str, bool]:
    paths = {str(item.get("path", "")) for item in differences}
    return {
        "selector_source_changed": any(
            path.startswith("publisher.selector.source_sha256.") for path in paths
        ),
        "selector_container_changed": "publisher.selector.container_id" in paths,
        "selector_restart_observed": "publisher.selector.started_at" in paths,
        "container_restart_observed": any(path.endswith(".started_at") for path in paths),
        "treatment_host_config_changed": (
            "treatment.live_container_spec_snapshot.host_config_sha256" in paths
        ),
        "treatment_live_spec_changed": "treatment.live_container_spec_sha256" in paths,
    }


def _validate_epoch1_linkage(
    baseline: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    if baseline.get("schema_version") != EPOCH1_BASELINE_SCHEMA_VERSION:
        raise CausalEpoch2FoundationError("epoch1_baseline_schema_invalid")
    if baseline.get("safety") != EPOCH1_SAFETY_FLAGS:
        raise CausalEpoch2FoundationError("epoch1_baseline_safety_invalid")
    try:
        validate_epoch1_baseline(baseline)
    except PostfixSoakFoundationError as exc:
        raise CausalEpoch2FoundationError("epoch1_baseline_invalid:" + str(exc)) from exc

    registration = manifest.get("registration_audit")
    if not isinstance(registration, Mapping):
        raise CausalEpoch2FoundationError("epoch1_registration_audit_missing")
    activation = manifest.get("formal_activation_utc")
    try:
        activation_dt = utc(activation)
        governance.validate_manifest(manifest, registration, activation_dt)
    except EvidenceError as exc:
        raise CausalEpoch2FoundationError("epoch1_manifest_invalid:" + str(exc)) from exc

    if baseline.get("causal_manifest_sha256") != manifest.get("manifest_sha256"):
        raise CausalEpoch2FoundationError("epoch1_baseline_manifest_identity_mismatch")
    if utc(baseline.get("formal_activation_utc")) != activation_dt:
        raise CausalEpoch2FoundationError("epoch1_activation_identity_mismatch")


def _audit_current(project_root: Path, runtime_root: Path) -> dict[str, Any]:
    snapshots = governance.collect_runtime(runtime_root)
    report = governance.audit_snapshots(snapshots, git=governance._git(project_root))
    governance.validate_audit(report, governance.now_utc())
    return report


def _require_predecessor_drift(
    epoch1_manifest: Mapping[str, Any],
    current_audit: Mapping[str, Any],
) -> None:
    try:
        governance.validate_manifest(epoch1_manifest, current_audit, governance.now_utc())
    except EvidenceError as exc:
        if str(exc) != "causal_runtime_drift":
            raise CausalEpoch2FoundationError(
                "epoch1_current_validation_blocked:" + str(exc)
            ) from exc
        return
    raise CausalEpoch2FoundationError("epoch1_runtime_still_causally_valid")


def _validated_jsonl_row_count(raw: bytes) -> int:
    row_count = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CausalEpoch2FoundationError("operational_ledger_json_invalid") from exc
        if not isinstance(row, Mapping):
            raise CausalEpoch2FoundationError("operational_ledger_row_not_object")
        row_count += 1
    return row_count


def _opening_snapshot() -> dict[str, Any]:
    publisher = governance.CONTAINERS["publisher"]
    operational_raw = governance.container_read(
        publisher,
        "/app/data/runtime/decision_ledger_paper_v1/decision_ledger_v4_2.jsonl",
    )
    operational_row_count = _validated_jsonl_row_count(operational_raw)

    evidence = governance.container_json(
        publisher,
        "/app/data/research/qlib_v3/prospective_evidence/"
        + CANONICAL.epoch_id
        + "/"
        + CANONICAL.activation_sha256
        + "/evidence.json",
    )
    check_identity(evidence.get("identity"), CANONICAL)
    if evidence.get("store_sha256") != digest(
        {key: value for key, value in evidence.items() if key != "store_sha256"}
    ):
        raise CausalEpoch2FoundationError("v3_store_hash_invalid")
    signals = evidence.get("signals")
    outcomes = evidence.get("outcomes")
    if not isinstance(signals, list) or not isinstance(outcomes, list):
        raise CausalEpoch2FoundationError("v3_store_records_invalid")

    treatment = governance.container_json(
        publisher,
        "/app/data/research/canonical_treatment/decision_ledger_v1.json",
    )
    if treatment.get("schema_version") != LEDGER_SCHEMA_VERSION:
        raise CausalEpoch2FoundationError("treatment_ledger_schema_invalid")
    treatment_rows = treatment.get("rows")
    if not isinstance(treatment_rows, list):
        raise CausalEpoch2FoundationError("treatment_ledger_rows_invalid")

    return {
        "operational_decision_ledger": {
            "row_count": operational_row_count,
            "sha256": _sha256_bytes(operational_raw),
        },
        "treatment_decision_ledger": {
            "row_count": len(treatment_rows),
            "sha256": digest(treatment),
        },
        "qlib_v3_store": {
            "signal_count": len(signals),
            "outcome_count": len(outcomes),
            "store_sha256": str(evidence["store_sha256"]),
        },
    }


def _first_proven_incompatible_runtime_utc(
    current_audit: Mapping[str, Any],
    categories: Mapping[str, bool],
) -> str:
    if categories.get("selector_source_changed") is True:
        selector = (
            current_audit.get("fingerprints", {}).get("publisher", {}).get("selector", {})
        )
        return _canonical_timestamp(selector.get("started_at"))
    return _canonical_timestamp(current_audit.get("generated_at_utc"))


def _manifest_body(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "epoch_manifest_sha256"}


def _baseline_body(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "epoch_baseline_sha256"}


def _registration_body(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "registration_sha256"}


def build_registration(
    *,
    epoch1_baseline: Mapping[str, Any],
    epoch1_manifest: Mapping[str, Any],
    current_audit: Mapping[str, Any],
    opening_snapshot: Mapping[str, Any],
    differences: list[Mapping[str, Any]],
) -> dict[str, Any]:
    categories = drift_categories(differences)
    if not differences:
        raise CausalEpoch2FoundationError("causal_drift_without_fingerprint_difference")
    if categories["selector_source_changed"] is not True:
        raise CausalEpoch2FoundationError("epoch2_requires_proven_selector_source_change")

    activation = _canonical_timestamp(current_audit.get("generated_at_utc"))
    predecessor_activation = _canonical_timestamp(epoch1_manifest.get("formal_activation_utc"))
    first_incompatible = _first_proven_incompatible_runtime_utc(current_audit, categories)
    if not utc(predecessor_activation) < utc(first_incompatible) <= utc(activation):
        raise CausalEpoch2FoundationError("epoch2_drift_boundary_invalid")

    epoch_manifest: dict[str, Any] = {
        "schema_version": EPOCH_MANIFEST_SCHEMA_VERSION,
        "epoch_id": EPOCH2_ID,
        "formal_activation_utc": activation,
        "predecessor_epoch_id": EPOCH1_ID,
        "predecessor_causal_manifest_sha256": epoch1_manifest["manifest_sha256"],
        "predecessor_baseline_sha256": epoch1_baseline["baseline_sha256"],
        "qlib_v3_identity": CANONICAL.mapping(),
        "git": dict(current_audit["git"]),
        "policy_version": current_audit["policy_version"],
        "join_contract": dict(current_audit["join_contract"]),
        "registration_audit_sha256": current_audit["audit_sha256"],
        "registration_status": current_audit["status"],
        "registration_parity_status": current_audit["parity_status"],
        "fingerprints": dict(current_audit["fingerprints"]),
        "fingerprints_sha256": current_audit["fingerprints_sha256"],
        "safety": dict(EPOCH2_SAFETY_FLAGS),
    }
    epoch_manifest["epoch_manifest_sha256"] = digest(epoch_manifest)

    baseline: dict[str, Any] = {
        "schema_version": EPOCH_BASELINE_SCHEMA_VERSION,
        "epoch_id": EPOCH2_ID,
        "formal_activation_utc": activation,
        "epoch_manifest_sha256": epoch_manifest["epoch_manifest_sha256"],
        "predecessor_epoch_id": EPOCH1_ID,
        "predecessor_baseline_sha256": epoch1_baseline["baseline_sha256"],
        "opening_snapshot": dict(opening_snapshot),
        "population_policy": {
            "predecessor_population_carryover_allowed": False,
            "pre_activation_rows_eligible": False,
            "historical_backfill_allowed": False,
            "boundary_rule": "decision_timestamp >= formal_activation_utc",
            "cross_epoch_counter_aggregation_allowed": False,
        },
        "safety": dict(EPOCH2_SAFETY_FLAGS),
    }
    baseline["epoch_baseline_sha256"] = digest(baseline)

    registration: dict[str, Any] = {
        "schema_version": REGISTRATION_SCHEMA_VERSION,
        "created_at_utc": activation,
        "epoch_id": EPOCH2_ID,
        "predecessor_epoch_id": EPOCH1_ID,
        "predecessor_closeout": {
            "reason": "causal_runtime_drift",
            "formal_activation_utc": predecessor_activation,
            "fix_deployed_at_utc": epoch1_baseline["fix_deployed_at_utc"],
            "causal_manifest_sha256": epoch1_manifest["manifest_sha256"],
            "baseline_sha256": epoch1_baseline["baseline_sha256"],
            "first_proven_incompatible_runtime_utc": first_incompatible,
            "drift_detected_at_utc": activation,
            "fingerprint_difference_count": len(differences),
            "fingerprint_differences": [dict(item) for item in differences],
            "categories": dict(categories),
            "historical_evidence_remains_immutable": True,
            "historical_population_carryover_allowed": False,
        },
        "epoch_manifest": epoch_manifest,
        "epoch_baseline": baseline,
        "safety": dict(EPOCH2_SAFETY_FLAGS),
    }
    registration["registration_sha256"] = digest(registration)
    validate_registration(registration)
    return registration


def validate_registration(registration: Mapping[str, Any]) -> None:
    if (
        registration.get("schema_version") != REGISTRATION_SCHEMA_VERSION
        or registration.get("epoch_id") != EPOCH2_ID
        or registration.get("predecessor_epoch_id") != EPOCH1_ID
    ):
        raise CausalEpoch2FoundationError("epoch2_registration_identity_invalid")
    if registration.get("registration_sha256") != digest(_registration_body(registration)):
        raise CausalEpoch2FoundationError("epoch2_registration_hash_invalid")
    if registration.get("safety") != EPOCH2_SAFETY_FLAGS:
        raise CausalEpoch2FoundationError("epoch2_registration_safety_invalid")

    closeout = registration.get("predecessor_closeout")
    if not isinstance(closeout, Mapping):
        raise CausalEpoch2FoundationError("epoch1_closeout_missing")
    if (
        closeout.get("reason") != "causal_runtime_drift"
        or closeout.get("historical_evidence_remains_immutable") is not True
        or closeout.get("historical_population_carryover_allowed") is not False
    ):
        raise CausalEpoch2FoundationError("epoch1_closeout_policy_invalid")
    categories = closeout.get("categories")
    if not isinstance(categories, Mapping) or categories.get("selector_source_changed") is not True:
        raise CausalEpoch2FoundationError("epoch1_closeout_selector_drift_unproven")
    differences = closeout.get("fingerprint_differences")
    if (
        not isinstance(differences, list)
        or len(differences) != closeout.get("fingerprint_difference_count")
        or not differences
    ):
        raise CausalEpoch2FoundationError("epoch1_closeout_differences_invalid")

    manifest = registration.get("epoch_manifest")
    baseline = registration.get("epoch_baseline")
    if not isinstance(manifest, Mapping) or not isinstance(baseline, Mapping):
        raise CausalEpoch2FoundationError("epoch2_manifest_or_baseline_missing")

    if (
        manifest.get("schema_version") != EPOCH_MANIFEST_SCHEMA_VERSION
        or manifest.get("epoch_id") != EPOCH2_ID
        or manifest.get("predecessor_epoch_id") != EPOCH1_ID
    ):
        raise CausalEpoch2FoundationError("epoch2_manifest_identity_invalid")
    if manifest.get("epoch_manifest_sha256") != digest(_manifest_body(manifest)):
        raise CausalEpoch2FoundationError("epoch2_manifest_hash_invalid")
    if manifest.get("fingerprints_sha256") != digest(manifest.get("fingerprints", {})):
        raise CausalEpoch2FoundationError("epoch2_manifest_fingerprint_hash_invalid")
    if (
        manifest.get("registration_status") != "ok"
        or manifest.get("registration_parity_status") != "PASS"
        or manifest.get("safety") != EPOCH2_SAFETY_FLAGS
    ):
        raise CausalEpoch2FoundationError("epoch2_manifest_registration_gate_invalid")
    if manifest.get("qlib_v3_identity") != CANONICAL.mapping():
        raise CausalEpoch2FoundationError("epoch2_qlib_v3_identity_changed")

    if (
        baseline.get("schema_version") != EPOCH_BASELINE_SCHEMA_VERSION
        or baseline.get("epoch_id") != EPOCH2_ID
        or baseline.get("predecessor_epoch_id") != EPOCH1_ID
    ):
        raise CausalEpoch2FoundationError("epoch2_baseline_identity_invalid")
    if baseline.get("epoch_baseline_sha256") != digest(_baseline_body(baseline)):
        raise CausalEpoch2FoundationError("epoch2_baseline_hash_invalid")
    if (
        baseline.get("epoch_manifest_sha256") != manifest.get("epoch_manifest_sha256")
        or baseline.get("formal_activation_utc") != manifest.get("formal_activation_utc")
        or baseline.get("safety") != EPOCH2_SAFETY_FLAGS
    ):
        raise CausalEpoch2FoundationError("epoch2_baseline_manifest_link_invalid")

    policy = baseline.get("population_policy")
    if not isinstance(policy, Mapping) or policy != {
        "predecessor_population_carryover_allowed": False,
        "pre_activation_rows_eligible": False,
        "historical_backfill_allowed": False,
        "boundary_rule": "decision_timestamp >= formal_activation_utc",
        "cross_epoch_counter_aggregation_allowed": False,
    }:
        raise CausalEpoch2FoundationError("epoch2_population_policy_invalid")

    opening = baseline.get("opening_snapshot")
    if not isinstance(opening, Mapping):
        raise CausalEpoch2FoundationError("epoch2_opening_snapshot_missing")
    operational = opening.get("operational_decision_ledger")
    treatment = opening.get("treatment_decision_ledger")
    store = opening.get("qlib_v3_store")
    if not all(isinstance(value, Mapping) for value in (operational, treatment, store)):
        raise CausalEpoch2FoundationError("epoch2_opening_snapshot_invalid")
    for record in (operational, treatment):
        if type(record.get("row_count")) is not int or record["row_count"] < 0:
            raise CausalEpoch2FoundationError("epoch2_opening_count_invalid")
        _require_sha256(record.get("sha256"), "epoch2_opening_sha_invalid")
    for field in ("signal_count", "outcome_count"):
        if type(store.get(field)) is not int or store[field] < 0:
            raise CausalEpoch2FoundationError("epoch2_store_count_invalid")
    _require_sha256(store.get("store_sha256"), "epoch2_store_sha_invalid")

    predecessor_activation = _canonical_timestamp(closeout.get("formal_activation_utc"))
    incompatible = _canonical_timestamp(closeout.get("first_proven_incompatible_runtime_utc"))
    activation = _canonical_timestamp(manifest.get("formal_activation_utc"))
    if not utc(predecessor_activation) < utc(incompatible) <= utc(activation):
        raise CausalEpoch2FoundationError("epoch2_temporal_order_invalid")


def validate_current_epoch2_runtime(
    registration: Mapping[str, Any],
    current_audit: Mapping[str, Any],
) -> None:
    validate_registration(registration)
    governance.validate_audit(current_audit, governance.now_utc())
    manifest = registration["epoch_manifest"]
    if not governance._same_runtime_after_selector_restart(
        manifest["fingerprints"],
        current_audit["fingerprints"],
        utc(manifest["formal_activation_utc"]),
        utc(current_audit["generated_at_utc"]),
    ):
        raise CausalEpoch2FoundationError("epoch2_runtime_drift")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        path,
        dict(payload),
        allow_nan=False,
        policy=AtomicWritePolicy.restricted([path.parent]),
    )


def _persist_report(path: Path, report: Mapping[str, Any]) -> dict[str, Any]:
    persisted = {
        **dict(report),
        "write_performed": True,
        "report_path": str(path),
    }
    persisted["report_sha256"] = digest(persisted)
    _write_json(path, persisted)
    return persisted


def _register_once(path: Path, registration: Mapping[str, Any]) -> bool:
    path = safe_path(path)
    with exclusive(path):
        if path.exists():
            existing = read_object(path)
            validate_registration(existing)
            if existing.get("registration_sha256") != registration.get("registration_sha256"):
                raise CausalEpoch2FoundationError(
                    "epoch2_registration_already_exists_with_different_identity"
                )
            return False
        _write_json(path, registration)
    return True


def build_blocked_report(
    reason: str,
    *,
    register_requested: bool = False,
    write_requested: bool = False,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked",
        "reason": reason,
        "decision": "BLOCKED_EPOCH2_FOUNDATION",
        "next_action": "FIX_EVIDENCE_INTEGRITY_BEFORE_EPOCH2_REGISTRATION",
        "epoch_id": EPOCH2_ID,
        "predecessor_epoch_id": EPOCH1_ID,
        "registration_requested": register_requested,
        "registration_performed": False,
        "write_requested": write_requested,
        "write_performed": False,
        "operational_authority": False,
        "promotion_allowed": False,
        "economic_edge_certified": False,
        "safety": dict(EPOCH2_SAFETY_FLAGS),
    }
    report["report_sha256"] = digest(report)
    return report


def run_foundation(
    *,
    project_root: str | Path,
    runtime_root: str | Path,
    epoch1_project_root: str | Path,
    epoch1_baseline_path: str | Path,
    evidence_root: str | Path,
    registration_path: str | Path = DEFAULT_REGISTRATION_RELATIVE_PATH,
    report_path: str | Path = DEFAULT_REPORT_RELATIVE_PATH,
    register_epoch2: bool = False,
    write_report: bool = False,
) -> dict[str, Any]:
    project = Path(project_root).resolve()
    runtime = Path(runtime_root).resolve()
    epoch1_project = Path(epoch1_project_root).resolve()
    baseline_path = Path(epoch1_baseline_path).resolve()
    evidence = safe_path(Path(evidence_root).resolve(strict=False))
    registration_file = _resolve_under(evidence, registration_path)
    report_file = _resolve_under(evidence, report_path)

    try:
        if write_report and registration_file == report_file:
            raise CausalEpoch2FoundationError("registration_report_path_collision")

        epoch1_baseline = read_object(baseline_path)
        epoch1_manifest = read_object(epoch1_project / governance.MANIFEST_PATH)
        _validate_epoch1_linkage(epoch1_baseline, epoch1_manifest)

        if registration_file.is_file():
            registration = read_object(registration_file)
            current = _audit_current(project, runtime)
            validate_current_epoch2_runtime(registration, current)
            report = {
                "schema_version": SCHEMA_VERSION,
                "status": "ok",
                "reason": "epoch2_registration_exists_and_runtime_is_causally_current",
                "decision": "EPOCH2_ALREADY_REGISTERED_AND_CURRENT",
                "next_action": "CONTINUE_EPOCH2_NATURAL_SOAK",
                "epoch_id": EPOCH2_ID,
                "predecessor_epoch_id": EPOCH1_ID,
                "registration_path": str(registration_file),
                "registration_sha256": registration["registration_sha256"],
                "formal_activation_utc": registration["epoch_manifest"]["formal_activation_utc"],
                "registration_requested": register_epoch2,
                "registration_performed": False,
                "write_requested": write_report,
                "write_performed": False,
                "operational_authority": False,
                "promotion_allowed": False,
                "economic_edge_certified": False,
                "safety": dict(EPOCH2_SAFETY_FLAGS),
            }
        else:
            current_before = _audit_current(project, runtime)
            _require_predecessor_drift(epoch1_manifest, current_before)
            opening = _opening_snapshot()
            current_after = _audit_current(project, runtime)
            if current_before["fingerprints_sha256"] != current_after["fingerprints_sha256"]:
                raise CausalEpoch2FoundationError(
                    "runtime_changed_during_epoch2_registration_audit"
                )
            _require_predecessor_drift(epoch1_manifest, current_after)

            differences = fingerprint_differences(
                epoch1_manifest["fingerprints"],
                current_after["fingerprints"],
            )
            registration = build_registration(
                epoch1_baseline=epoch1_baseline,
                epoch1_manifest=epoch1_manifest,
                current_audit=current_after,
                opening_snapshot=opening,
                differences=differences,
            )

            performed = False
            if register_epoch2:
                performed = _register_once(registration_file, registration)

            report = {
                "schema_version": SCHEMA_VERSION,
                "status": "ok",
                "reason": (
                    "epoch2_registered"
                    if performed
                    else "proven_epoch1_drift_epoch2_registration_ready"
                ),
                "decision": "EPOCH2_REGISTERED" if performed else "READY_TO_REGISTER_EPOCH2",
                "next_action": (
                    "CONTINUE_EPOCH2_NATURAL_SOAK"
                    if performed
                    else "REGISTER_EPOCH2_FROM_CANONICAL_DEV_AFTER_MERGE"
                ),
                "epoch_id": EPOCH2_ID,
                "predecessor_epoch_id": EPOCH1_ID,
                "registration_path": str(registration_file),
                "registration_sha256": registration["registration_sha256"],
                "formal_activation_utc": registration["epoch_manifest"]["formal_activation_utc"],
                "first_proven_incompatible_runtime_utc": registration[
                    "predecessor_closeout"
                ]["first_proven_incompatible_runtime_utc"],
                "fingerprint_difference_count": len(differences),
                "drift_categories": drift_categories(differences),
                "opening_snapshot": opening,
                "registration_requested": register_epoch2,
                "registration_performed": performed,
                "write_requested": write_report,
                "write_performed": False,
                "operational_authority": False,
                "promotion_allowed": False,
                "economic_edge_certified": False,
                "safety": dict(EPOCH2_SAFETY_FLAGS),
            }

        if write_report:
            return _persist_report(report_file, report)

        report["report_sha256"] = digest(report)
        return report
    except (
        CausalEpoch2FoundationError,
        EvidenceError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        reason = (
            str(exc)
            if isinstance(exc, (CausalEpoch2FoundationError, EvidenceError))
            else "epoch2_foundation_failed:" + type(exc).__name__
        )
        return build_blocked_report(
            reason,
            register_requested=register_epoch2,
            write_requested=write_report,
        )
