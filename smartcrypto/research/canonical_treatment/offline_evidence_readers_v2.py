"""Read-only adapters for explicit V2 evidence sources.

These entry points reuse existing pure research evaluators. They do not collect
runtime state, read operational databases, train, register or write reports.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from smartcrypto.learning.qlib_v3_prospective.activation import parse_json
from smartcrypto.learning.qlib_v3_prospective.contracts import (
    CANONICAL,
    EvidenceError,
    check_identity,
    digest,
)
from smartcrypto.learning.qlib_v3_prospective.store import SCHEMA as V3_STORE_SCHEMA
from smartcrypto.research.canonical_treatment.evidence_sources_v2 import (
    SourceResolution,
    resolve_source,
)
from smartcrypto.research.canonical_treatment.storage_cohort_contract_v2 import (
    ContractV2Error,
    FROZEN_SOURCES,
    ValidatedDescriptor,
)

SCHEMA_VERSION = "paper_b_offline_evidence_readers_v2"
READERS = ("B09", "B10", "B14", "B15", "B16", "economic_monitor", "parity_collector")

SAFETY = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "changes_model": False,
    "changes_strategy": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_data": False,
    "training_performed": False,
    "training_executed_by_adapter": False,
    "epoch_registered": False,
    "economic_edge_certified": False,
}


class OfflineEvidenceReaderError(ValueError):
    """Stable fail-closed reason from an offline evidence adapter."""


def _metadata(value: SourceResolution) -> dict[str, Any]:
    return {
        "status": value.status,
        "reason": value.reason,
        "internal_path": value.internal_path,
        "source_identity": value.source_identity,
        "epoch_namespace": value.epoch_namespace,
        "mount_type": value.mount_type,
        "access": value.access,
        "expected_sha256": value.expected_sha256,
    }


def _object(raw: bytes) -> dict[str, Any]:
    value = parse_json(raw)
    if not isinstance(value, dict):
        raise OfflineEvidenceReaderError("source_json_object_required")
    return value


def _validate_materialized_source(logical_source: str, report: dict[str, Any]) -> None:
    """Apply source semantics in addition to the resolver's physical hash."""
    if logical_source in {"sleeve_evidence", "council_ab_evidence"}:
        # The historical schemas permit omission, but an explicit non-ok state
        # must stop V2 before the evaluator can select a research candidate.
        if "status" in report and report["status"] != "ok":
            raise OfflineEvidenceReaderError("source_status_not_admissible:" + logical_source)
        return
    if logical_source == "branch09_report":
        check_identity(report.get("identity"), CANONICAL)
        return
    if logical_source == "branch13_report":
        from smartcrypto.research.aibot_parity.component_economic_attribution_ledger import (
            SCHEMA_VERSION as expected_schema,
            _canonical_hash as report_hash,
        )

        seal = "ledger_evidence_sha256"
    elif logical_source == "branch14_report":
        from smartcrypto.research.aibot_parity.economic_control_treatment_scorecard import (
            SCHEMA_VERSION as expected_schema,
            _hash as report_hash,
        )

        seal = "scorecard_evidence_sha256"
    else:
        return
    if report.get("schema_version") != expected_schema:
        raise OfflineEvidenceReaderError(logical_source + "_schema_invalid")
    actual = report.get(seal)
    # B13 and B14 seal every producer field except the seal itself. Unlike the
    # separate B15/B16 contract, neither excludes optional source metadata.
    if (
        not isinstance(actual, str)
        or re.fullmatch(r"[0-9a-f]{64}", actual) is None
        or actual != report_hash({key: value for key, value in report.items() if key != seal})
    ):
        raise OfflineEvidenceReaderError(logical_source + "_semantic_seal_invalid")


def _validate_materialized_b10(
    report: dict[str, Any],
    metadata: Mapping[str, Mapping[str, Any]],
    contract: Mapping[str, Any],
    manifest: Mapping[str, Any],
    splits: Mapping[str, Any],
) -> None:
    from smartcrypto.research.aibot_parity.economic_control_treatment_scorecard import _validate

    _validate("branch10", report)
    source = report.get("source")
    if not isinstance(source, Mapping):
        raise OfflineEvidenceReaderError("branch10_source_identity_missing")
    names = {
        "dataset_sha256": "wq6_dataset",
        "feature_contract_sha256": "wq6_feature_contract",
        "dataset_manifest_sha256": "wq6_dataset_manifest",
        "split_manifest_sha256": "wq6_split_manifest",
    }
    if any(source.get(field) != metadata[logical]["expected_sha256"] for field, logical in names.items()):
        raise OfflineEvidenceReaderError("branch10_explicit_source_hash_mismatch")
    if any(item.get("validation_status") != "ok" for item in (contract, manifest, splits)):
        raise OfflineEvidenceReaderError("branch10_frozen_bundle_not_validated")
    if splits.get("leakage_status") != "ok":
        raise OfflineEvidenceReaderError("branch10_frozen_split_leakage_blocked")
    if any(
        not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
        for value in (
            contract.get("contract_hash"), manifest.get("dataset_hash"),
            splits.get("split_manifest_hash"),
        )
    ):
        raise OfflineEvidenceReaderError("branch10_frozen_semantic_hash_invalid")
    if (
        not isinstance(contract.get("contract_hash"), str)
        or manifest.get("feature_contract_hash") != contract["contract_hash"]
        or splits.get("feature_contract_hash") != contract["contract_hash"]
        or source.get("feature_contract_hash") != contract["contract_hash"]
        or source.get("dataset_hash") != manifest.get("dataset_hash")
        or splits.get("dataset_hash") != manifest.get("dataset_hash")
        or source.get("split_manifest_hash") != splits.get("split_manifest_hash")
    ):
        raise OfflineEvidenceReaderError("branch10_frozen_bundle_identity_mismatch")
    materialized_hash = manifest.get("materialized_dataset_sha256")
    if materialized_hash is not None and materialized_hash != metadata["wq6_dataset"]["expected_sha256"]:
        raise OfflineEvidenceReaderError("branch10_materialized_dataset_hash_mismatch")


def build_offline_evidence_reader_v2(
    *,
    descriptor: ValidatedDescriptor,
    reader: str,
    known_fingerprints: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve declared source bytes and invoke only existing pure evaluators."""
    resolutions: dict[str, dict[str, Any]] = {}
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "reader": reader,
        "status": "blocked",
        "reason": "reader_not_supported",
        "evaluation_scope": "explicit_bound_source_diagnostic_only",
        "new_cohort_certified": False,
        "resolutions": resolutions,
        "result": None,
        **SAFETY,
    }
    if reader not in READERS:
        return report

    def raw_source(logical_source: str) -> bytes:
        resolved = resolve_source(
            descriptor,
            component=reader,
            access_role="research_read",
            logical_source=logical_source,
            known_fingerprints=known_fingerprints,
            observations=observations,
        )
        resolutions[logical_source] = _metadata(resolved)
        if resolved.status != "ok" or resolved.raw is None:
            raise OfflineEvidenceReaderError("source_blocked:" + resolved.reason)
        return resolved.raw

    def object_source(logical_source: str) -> dict[str, Any]:
        value = _object(raw_source(logical_source))
        if logical_source not in FROZEN_SOURCES and "epoch_id" in value:
            if value["epoch_id"] != resolutions[logical_source]["epoch_namespace"]:
                raise OfflineEvidenceReaderError("source_payload_epoch_mismatch")
        _validate_materialized_source(logical_source, value)
        return value

    def materialized_b10_source() -> dict[str, Any]:
        # Each binding must authorize the current reader, including B14.
        raw_source("wq6_dataset")
        contract = object_source("wq6_feature_contract")
        manifest = object_source("wq6_dataset_manifest")
        splits = object_source("wq6_split_manifest")
        materialized = object_source("branch10_report")
        _validate_materialized_b10(materialized, resolutions, contract, manifest, splits)
        return materialized

    try:
        if reader == "B09":
            from smartcrypto.research.aibot_parity.ai_shadow_economic_challenger import (
                evaluate_evidence_state,
            )

            state = object_source("qlib_v3_store")
            if state.get("schema_version") != V3_STORE_SCHEMA:
                raise OfflineEvidenceReaderError("store_not_v3")
            if any(
                not isinstance(state.get(key), list)
                or any(not isinstance(row, dict) for row in state[key])
                for key in ("signals", "outcomes")
            ):
                raise OfflineEvidenceReaderError("store_records_invalid")
            if state.get("store_sha256") != digest({
                key: value for key, value in state.items() if key != "store_sha256"
            }):
                raise OfflineEvidenceReaderError("v3_store_hash_mismatch")
            result = evaluate_evidence_state(state, expected=CANONICAL)
        elif reader == "B10":
            # The pre-existing producer calls model.fit. This adapter accepts only
            # an already-materialized report bound to the four exact frozen inputs.
            result = materialized_b10_source()
            report["evaluation_scope"] = "existing_materialized_report_and_explicit_source_bindings"
        elif reader == "B14":
            from smartcrypto.research.aibot_parity.economic_control_treatment_scorecard import (
                build_economic_control_treatment_scorecard_from_reports,
            )

            result = build_economic_control_treatment_scorecard_from_reports(
                branch09_report=object_source("branch09_report"),
                branch10_report=materialized_b10_source(),
                branch11_report=object_source("branch11_report"),
                branch12_report=object_source("branch12_report"),
                ledger_report=object_source("branch13_report"),
            )
        elif reader == "B15":
            from smartcrypto.research.portfolio_of_alphas.oos_economic_selection import (
                build_portfolio_of_alphas_oos_economic_selection_from_scorecard,
            )

            result = build_portfolio_of_alphas_oos_economic_selection_from_scorecard(
                scorecard_report=object_source("branch14_report"),
                sleeve_evidence=object_source("sleeve_evidence"),
            )
        elif reader == "B16":
            from smartcrypto.research.research_council.alpha_uplift_ab import (
                build_research_council_alpha_uplift_ab_from_scorecard,
            )

            result = build_research_council_alpha_uplift_ab_from_scorecard(
                scorecard_report=object_source("branch14_report"),
                council_ab_evidence=object_source("council_ab_evidence"),
            )
        elif reader == "economic_monitor":
            object_source("monitor_report")
            raise OfflineEvidenceReaderError("monitor_generation_requires_registered_cohort_and_authorized_financial_inputs")
        else:
            object_source("parity_report")
            raise OfflineEvidenceReaderError("runtime_parity_collection_requires_versioned_deployment_attestation")
        report["result"] = result
        report["status"] = result.get("status", "blocked")
        report["reason"] = str(result.get("reason", "pure_reader_completed"))
    except (
        ContractV2Error,
        EvidenceError,
        OfflineEvidenceReaderError,
        OSError,
        ImportError,
        KeyError,
        TypeError,
        ValueError,
        RuntimeError,
    ) as exc:
        # Existing pure evaluators use RuntimeError subclasses for stable validation.
        # Exception messages are not copied if the error is an OS/dependency failure.
        report["status"] = "blocked"
        report["reason"] = (
            str(exc)
            if isinstance(exc, (ContractV2Error, EvidenceError, OfflineEvidenceReaderError, RuntimeError))
            else "offline_reader_failed:" + type(exc).__name__
        )
    return report


__all__ = ["READERS", "SCHEMA_VERSION", "OfflineEvidenceReaderError", "build_offline_evidence_reader_v2"]
