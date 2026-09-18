"""Frozen WQ7 post-OCR research hypothesis registry.

The registry consumes the externally frozen WQ7 method contract and candidate
queue V2. It preserves immutable hypothesis identity, WQ2-WQ6 lineage,
pre-registered metrics, acceptance gates and research-only authority.

This module does not train models, evaluate hypotheses, change thresholds,
activate filters, mutate RiskManager/Freqtrade, access private exchange APIs,
send orders, or promote any candidate.

Registry fingerprints are semantic and portable. Local filesystem locator
fields remain available for observability but are excluded from the registry
content fingerprint.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "post_ocr_hypothesis_registry_v1"
REGISTRY_VERSION = "V1"

EXPECTED_BRANCH = "codex/post-ocr-hypothesis-registry-v1"

EXPECTED_BASE_SHA = (
    "91017c145a5f6a46f273fdd1fb9655bb26ff8cbb"
)

EXPECTED_METHOD_HASH = (
    "81cb14553b054a90eca2597073b20c4902cd1672759403ef0b4779eab9707d33"
)

EXPECTED_METHOD_FREEZE_FILE_SHA256 = (
    "b2ef74f1c89c4e9bfd97ec54f979a1a50d8ec2d7aea994ddb24a46dfcb8e560d"
)

EXPECTED_QUEUE_POLICY_HASH = (
    "9a582584d0b67d1e43ef28bad99c090b5775b75754941d46ac41c18313208a4c"
)

EXPECTED_NORMALIZATION_POLICY_HASH = (
    "3927d11728aa0fd8b2e2395424751fbc3c005a02e6c632189797a17092fbe7e2"
)

EXPECTED_QUEUE_V2_HASH = (
    "3ceaa95ea1f262dcf289654e3ed66efd1f0df47764db88fbd3d39660a1f0f956"
)

EXPECTED_QUEUE_V2_FILE_SHA256 = (
    "d3529c5e5faa351314650ebfc61ae4d7284850ade0297434e79cd47dca35aef8"
)

EXPECTED_PORTABLE_REGISTRY_HASH = (
    "79195333de12e1925302082246e3133e2b5d26f4bd4f88250504b10107bc04b4"
)

EXPECTED_MASTER_SHA256 = (
    "a99c03497bb47e2aee2762c182ed45e38060490914d6991d3730d31de649935f"
)

EXPECTED_SOURCE_EVIDENCE_SHA256 = {
    "wq2": (
        "bcf28ef93841a029f23e7003944680ada4f15b25b0269d1ca3834b8f058a1512"
    ),
    "wq3": (
        "4dd3bfe5f5f20d1630787c1bd975f4334ac8a4b5f5f6cc7e9cae66de13778185"
    ),
    "wq4": (
        "9675e5dd7ceeb12175b3a3c8b87fe51f768914dd186e119ef41d862865e012bc"
    ),
    "wq5": (
        "f6191acd51df381116b1cad16da309466f3c07bf19b5a84bd5ba21353635cd2e"
    ),
    "wq6": (
        "2a3ad511cbf77ba44a5bff49fc40fb3c7425a273c0979ffeccf3efd884ad0e35"
    ),
}

EXPECTED_WQ6_DATASET_HASH = (
    "fa9ea5e088e668e5f733cde841da849dc3c16263a672991f1803e609fff14a25"
)

EXPECTED_WQ6_SPLIT_MANIFEST_HASH = (
    "248d53e2481cf107f88a4f7267b9573f118d3fed1afef7906d290e741b3229bc"
)

EXPECTED_QLIB_HYPOTHESIS_ID = (
    "WQ7-H01-qlib_ranking_challenger-fa9ea5e088e668e5"
)

EXPECTED_SEGMENT_HYPOTHESIS_ID = (
    "WQ7-H02-segment_filter-segment_99f7fa60c4edfd02ea462561"
)

EXPECTED_SEGMENT_ID = (
    "segment_99f7fa60c4edfd02ea462561"
)

ALLOWED_STATES = {
    "PROVAR",
    "DESCARTAR",
    "RECALIBRAR",
    "HOLD",
}

REQUIRED_HYPOTHESIS_FIELDS = {
    "hypothesis_id",
    "source_evidence",
    "mechanism",
    "population",
    "candidate_change",
    "pre_registered_metrics",
    "acceptance_gate",
    "experiment_budget",
    "dataset_fingerprint",
    "split_fingerprint",
    "state",
    "authority",
}

REGISTRY_AUTHORITY: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "exchange_private_access": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_active_model_registry": False,
    "updates_operational_thresholds": False,
    "live_trading_enabled": False,
    "canary_enabled": False,
    "training_performed": False,
    "candidate_evaluation_performed": False,
    "independent_oos_evaluation_performed": False,
    "threshold_search_performed": False,
    "model_selection_performed": False,
}

_QUEUE_AUTHORITY_FALSE = (
    "can_apply_to_freqtrade",
    "can_apply_to_risk_manager",
    "updates_active_model_registry",
    "updates_operational_thresholds",
    "live_trading_enabled",
    "canary_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
    "operational_authority",
)

_REGISTRY_HASH_EXCLUDED_LOCATORS = (
    ("method_freeze", "path"),
    ("source_queue", "path"),
)


class OfficialHypothesisRegistryError(ValueError):
    """Fail-closed WQ7 hypothesis-registry validation error."""

    def __init__(
        self,
        code: str,
        **details: Any,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.details = details


def _fail(
    code: str,
    **details: Any,
) -> None:
    raise OfficialHypothesisRegistryError(
        code,
        **details,
    )


def _stable_hash(
    payload: Any,
) -> str:
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    return hashlib.sha256(
        rendered
    ).hexdigest()


def sha256_file(
    path: str | Path,
) -> str:
    target = Path(path)

    if not target.is_file():
        _fail(
            "file_not_found",
            path=str(target),
        )

    digest = hashlib.sha256()

    with target.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def method_freeze_content_sha256(
    payload: Mapping[str, Any],
) -> str:
    normalized = deepcopy(
        dict(payload)
    )

    normalized.pop(
        "method_hash",
        None,
    )

    return _stable_hash(
        normalized
    )


def queue_content_sha256(
    payload: Mapping[str, Any],
) -> str:
    normalized = deepcopy(
        dict(payload)
    )

    normalized.pop(
        "queue_hash",
        None,
    )

    return _stable_hash(
        normalized
    )


def canonical_registry_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the portable semantic payload used for registry hashing.

    Local materialization paths are observability metadata, not economic or
    methodological identity. They are canonicalized to ``None`` before the
    fingerprint is computed. Hashes of the referenced artifacts remain inside
    the semantic payload and therefore continue to anchor lineage exactly.
    """

    normalized = deepcopy(
        dict(payload)
    )

    normalized.pop(
        "registry_hash",
        None,
    )

    for (
        section_name,
        field_name,
    ) in _REGISTRY_HASH_EXCLUDED_LOCATORS:
        section = normalized.get(
            section_name
        )

        if isinstance(
            section,
            Mapping,
        ):
            section_copy = dict(
                section
            )

            section_copy[
                field_name
            ] = None

            normalized[
                section_name
            ] = section_copy

    return normalized


def registry_content_sha256(
    payload: Mapping[str, Any],
) -> str:
    return _stable_hash(
        canonical_registry_payload(
            payload
        )
    )


def _require_mapping(
    value: Any,
    *,
    code: str,
) -> Mapping[str, Any]:
    if not isinstance(
        value,
        Mapping,
    ):
        _fail(
            code
        )

    return value


def _require_list(
    value: Any,
    *,
    code: str,
) -> list[Any]:
    if not isinstance(
        value,
        list,
    ):
        _fail(
            code
        )

    return value


def _load_json_object(
    path: str | Path,
    *,
    code_prefix: str,
) -> dict[str, Any]:
    target = Path(
        path
    )

    if not target.is_file():
        _fail(
            f"{code_prefix}_file_missing",
            path=str(target),
        )

    try:
        payload = json.loads(
            target.read_text(
                encoding="utf-8-sig"
            )
        )

    except (
        OSError,
        json.JSONDecodeError,
    ) as exc:
        raise OfficialHypothesisRegistryError(
            f"{code_prefix}_invalid_json",
            path=str(target),
        ) from exc

    if not isinstance(
        payload,
        dict,
    ):
        _fail(
            f"{code_prefix}_not_object",
            path=str(target),
        )

    return payload


def validate_method_freeze(
    payload: Mapping[str, Any],
) -> None:
    if (
        payload.get(
            "schema_version"
        )
        != "post_ocr_hypothesis_registry_method_freeze_v1"
    ):
        _fail(
            "method_freeze_schema_mismatch"
        )

    if payload.get(
        "freeze_version"
    ) != "V1":
        _fail(
            "method_freeze_version_mismatch"
        )

    if payload.get(
        "branch"
    ) != EXPECTED_BRANCH:
        _fail(
            "method_freeze_branch_mismatch"
        )

    if payload.get(
        "base_sha"
    ) != EXPECTED_BASE_SHA:
        _fail(
            "method_freeze_base_sha_mismatch"
        )

    if payload.get(
        "master_sha256"
    ) != EXPECTED_MASTER_SHA256:
        _fail(
            "method_freeze_master_sha_mismatch"
        )

    if payload.get(
        "method_hash"
    ) != EXPECTED_METHOD_HASH:
        _fail(
            "method_freeze_hash_mismatch"
        )

    recomputed = (
        method_freeze_content_sha256(
            payload
        )
    )

    if recomputed != EXPECTED_METHOD_HASH:
        _fail(
            "method_freeze_content_hash_mismatch",
            expected=EXPECTED_METHOD_HASH,
            actual=recomputed,
        )

    source_evidence = _require_mapping(
        payload.get(
            "source_evidence"
        ),
        code=(
            "method_freeze_source_evidence_missing"
        ),
    )

    if set(
        source_evidence
    ) != set(
        EXPECTED_SOURCE_EVIDENCE_SHA256
    ):
        _fail(
            "method_freeze_source_evidence_set_mismatch"
        )

    for (
        wave,
        expected_sha,
    ) in EXPECTED_SOURCE_EVIDENCE_SHA256.items():
        record = _require_mapping(
            source_evidence.get(
                wave
            ),
            code=(
                "method_freeze_source_"
                f"{wave}_invalid"
            ),
        )

        if (
            record.get(
                "file_sha256"
            )
            != expected_sha
        ):
            _fail(
                "method_freeze_source_sha_mismatch",
                wave=wave,
                expected=expected_sha,
                actual=record.get(
                    "file_sha256"
                ),
            )

    wq6 = _require_mapping(
        payload.get(
            "wq6_fingerprints"
        ),
        code=(
            "method_freeze_wq6_fingerprints_missing"
        ),
    )

    if (
        wq6.get(
            "dataset_hash"
        )
        != EXPECTED_WQ6_DATASET_HASH
    ):
        _fail(
            "method_freeze_wq6_dataset_hash_mismatch"
        )

    if (
        wq6.get(
            "split_manifest_hash"
        )
        != EXPECTED_WQ6_SPLIT_MANIFEST_HASH
    ):
        _fail(
            "method_freeze_wq6_split_hash_mismatch"
        )

    if (
        wq6.get(
            "leakage_status"
        )
        != "ok"
    ):
        _fail(
            "method_freeze_wq6_leakage_not_ok"
        )

    if int(
        wq6.get(
            "true_label_interval_overlap_count",
            -1,
        )
    ) != 0:
        _fail(
            "method_freeze_wq6_label_overlap_nonzero"
        )

    wq5 = _require_mapping(
        payload.get(
            "wq5_inheritance"
        ),
        code=(
            "method_freeze_wq5_inheritance_missing"
        ),
    )

    if (
        wq5.get(
            "quantitative_acceptance"
        )
        != "FAIL"
    ):
        _fail(
            "method_freeze_wq5_acceptance_drift"
        )

    if (
        wq5.get(
            "classification"
        )
        != "DISCARD_OR_RECALIBRATE_RESEARCH_ONLY"
    ):
        _fail(
            "method_freeze_wq5_classification_drift"
        )


def _validate_queue_authority(
    authority: Any,
    *,
    label: str,
) -> None:
    contract = _require_mapping(
        authority,
        code=(
            f"{label}_authority_missing"
        ),
    )

    for field in (
        "paper_only",
        "shadow_only",
        "research_only",
    ):
        if (
            contract.get(
                field
            )
            is not True
        ):
            _fail(
                f"{label}_authority_true_flag_drift",
                field=field,
                value=contract.get(
                    field
                ),
            )

    for field in (
        _QUEUE_AUTHORITY_FALSE
    ):
        if (
            contract.get(
                field
            )
            is not False
        ):
            _fail(
                f"{label}_authority_false_flag_drift",
                field=field,
                value=contract.get(
                    field
                ),
            )


def _candidate_by_id(
    candidates: Sequence[
        Mapping[str, Any]
    ],
    hypothesis_id: str,
) -> Mapping[str, Any]:
    matches = [
        candidate
        for candidate in candidates
        if candidate.get(
            "hypothesis_id"
        )
        == hypothesis_id
    ]

    if len(
        matches
    ) != 1:
        _fail(
            "hypothesis_identity_not_unique",
            hypothesis_id=(
                hypothesis_id
            ),
            count=len(
                matches
            ),
        )

    return matches[
        0
    ]


def validate_candidate_queue_v2(
    payload: Mapping[str, Any],
) -> None:
    if (
        payload.get(
            "schema_version"
        )
        != "wq7_hypothesis_candidate_queue_v2"
    ):
        _fail(
            "queue_schema_mismatch"
        )

    if (
        payload.get(
            "queue_version"
        )
        != "V2"
    ):
        _fail(
            "queue_version_mismatch"
        )

    if (
        payload.get(
            "branch"
        )
        != EXPECTED_BRANCH
    ):
        _fail(
            "queue_branch_mismatch"
        )

    if (
        payload.get(
            "base_sha"
        )
        != EXPECTED_BASE_SHA
    ):
        _fail(
            "queue_base_sha_mismatch"
        )

    if (
        payload.get(
            "parent_method_hash"
        )
        != EXPECTED_METHOD_HASH
    ):
        _fail(
            "queue_parent_method_hash_mismatch"
        )

    if (
        payload.get(
            "queue_policy_hash"
        )
        != EXPECTED_QUEUE_POLICY_HASH
    ):
        _fail(
            "queue_policy_hash_mismatch"
        )

    if (
        payload.get(
            "queue_hash"
        )
        != EXPECTED_QUEUE_V2_HASH
    ):
        _fail(
            "queue_hash_mismatch"
        )

    recomputed = (
        queue_content_sha256(
            payload
        )
    )

    if (
        recomputed
        != EXPECTED_QUEUE_V2_HASH
    ):
        _fail(
            "queue_content_hash_mismatch",
            expected=(
                EXPECTED_QUEUE_V2_HASH
            ),
            actual=recomputed,
        )

    normalization = _require_mapping(
        payload.get(
            "semantic_normalization"
        ),
        code=(
            "queue_normalization_missing"
        ),
    )

    if (
        normalization.get(
            "policy_hash"
        )
        != EXPECTED_NORMALIZATION_POLICY_HASH
    ):
        _fail(
            "queue_normalization_policy_hash_mismatch"
        )

    if (
        normalization.get(
            "applied"
        )
        is not True
    ):
        _fail(
            "queue_normalization_not_applied"
        )

    for field in (
        "ranking_recomputed",
        "candidate_selection_recomputed",
        "segment_id_changed",
        "source_wq3_mutated",
    ):
        if (
            normalization.get(
                field
            )
            is not False
        ):
            _fail(
                "queue_normalization_boundary_drift",
                field=field,
                value=normalization.get(
                    field
                ),
            )

    source_hashes = _require_mapping(
        payload.get(
            "source_report_sha256"
        ),
        code=(
            "queue_source_hashes_missing"
        ),
    )

    expected_source_hashes = {
        "wq3": (
            EXPECTED_SOURCE_EVIDENCE_SHA256[
                "wq3"
            ]
        ),
        "wq4": (
            EXPECTED_SOURCE_EVIDENCE_SHA256[
                "wq4"
            ]
        ),
        "wq6": (
            EXPECTED_SOURCE_EVIDENCE_SHA256[
                "wq6"
            ]
        ),
    }

    if (
        dict(
            source_hashes
        )
        != expected_source_hashes
    ):
        _fail(
            "queue_source_hashes_mismatch"
        )

    evaluation = _require_mapping(
        payload.get(
            "evaluation"
        ),
        code=(
            "queue_evaluation_missing"
        ),
    )

    for field in (
        "candidate_evaluation_performed",
        "training_performed",
        "independent_oos_evaluation_performed",
        "threshold_search_performed",
        "model_selection_performed",
    ):
        if (
            evaluation.get(
                field
            )
            is not False
        ):
            _fail(
                "queue_premature_evaluation",
                field=field,
            )

    raw_candidates = _require_list(
        payload.get(
            "candidates"
        ),
        code=(
            "queue_candidates_invalid"
        ),
    )

    candidates: list[
        Mapping[str, Any]
    ] = []

    for (
        index,
        candidate,
    ) in enumerate(
        raw_candidates
    ):
        if not isinstance(
            candidate,
            Mapping,
        ):
            _fail(
                "queue_candidate_not_object",
                index=index,
            )

        missing = sorted(
            REQUIRED_HYPOTHESIS_FIELDS
            - set(
                candidate
            )
        )

        if missing:
            _fail(
                "queue_candidate_fields_missing",
                index=index,
                missing=missing,
            )

        if (
            candidate.get(
                "state"
            )
            != "HOLD"
        ):
            _fail(
                "queue_candidate_state_not_hold",
                index=index,
                state=candidate.get(
                    "state"
                ),
            )

        _validate_queue_authority(
            candidate.get(
                "authority"
            ),
            label=(
                "candidate_"
                + str(
                    index
                )
            ),
        )

        candidates.append(
            candidate
        )

    if len(
        candidates
    ) != 2:
        _fail(
            "queue_candidate_count_mismatch",
            count=len(
                candidates
            ),
        )

    observed_ids = {
        str(
            candidate.get(
                "hypothesis_id"
            )
        )
        for candidate in candidates
    }

    expected_ids = {
        EXPECTED_QLIB_HYPOTHESIS_ID,
        EXPECTED_SEGMENT_HYPOTHESIS_ID,
    }

    if (
        observed_ids
        != expected_ids
    ):
        _fail(
            "queue_hypothesis_id_set_mismatch"
        )

    qlib = _candidate_by_id(
        candidates,
        EXPECTED_QLIB_HYPOTHESIS_ID,
    )

    if (
        qlib.get(
            "candidate_class"
        )
        != "qlib_ranking_challenger"
    ):
        _fail(
            "qlib_candidate_class_mismatch"
        )

    qlib_source = _require_mapping(
        qlib.get(
            "source_evidence"
        ),
        code=(
            "qlib_source_evidence_missing"
        ),
    )

    if (
        qlib_source.get(
            "dataset_hash"
        )
        != EXPECTED_WQ6_DATASET_HASH
    ):
        _fail(
            "qlib_dataset_hash_mismatch"
        )

    if (
        qlib_source.get(
            "split_manifest_hash"
        )
        != EXPECTED_WQ6_SPLIT_MANIFEST_HASH
    ):
        _fail(
            "qlib_split_hash_mismatch"
        )

    segment = _candidate_by_id(
        candidates,
        EXPECTED_SEGMENT_HYPOTHESIS_ID,
    )

    if (
        segment.get(
            "candidate_class"
        )
        != "segment_filter"
    ):
        _fail(
            "segment_candidate_class_mismatch"
        )

    segment_source = _require_mapping(
        segment.get(
            "source_evidence"
        ),
        code=(
            "segment_source_evidence_missing"
        ),
    )

    if (
        segment_source.get(
            "segment_id"
        )
        != EXPECTED_SEGMENT_ID
    ):
        _fail(
            "segment_id_mismatch"
        )

    change = _require_mapping(
        segment.get(
            "candidate_change"
        ),
        code=(
            "segment_candidate_change_missing"
        ),
    )

    if (
        "segment_values"
        in change
    ):
        _fail(
            "ambiguous_legacy_segment_values_present"
        )

    if (
        change.get(
            "raw_segment_values"
        )
        != {
            "open_hour_utc": "(23,)"
        }
    ):
        _fail(
            "segment_raw_values_mismatch"
        )

    if (
        change.get(
            "semantic_segment_values"
        )
        != {
            "open_hour_utc": 23
        }
    ):
        _fail(
            "segment_semantic_values_mismatch"
        )

    matching = _require_mapping(
        change.get(
            "matching_contract"
        ),
        code=(
            "segment_matching_contract_missing"
        ),
    )

    expected_matching = {
        "mode": (
            "exact_semantic_equality"
        ),
        "field": (
            "open_hour_utc"
        ),
        "semantic_value": 23,
        "raw_source_value": "(23,)",
        "fuzzy_matching_allowed": False,
        "nearest_matching_allowed": False,
        "backfill_matching_allowed": False,
    }

    if (
        dict(
            matching
        )
        != expected_matching
    ):
        _fail(
            "segment_matching_contract_mismatch"
        )

    split_fingerprint = (
        _require_mapping(
            segment.get(
                "split_fingerprint"
            ),
            code=(
                "segment_split_fingerprint_missing"
            ),
        )
    )

    if (
        split_fingerprint.get(
            "independent_oos_split"
        )
        is not None
    ):
        _fail(
            "segment_independent_oos_not_unresolved"
        )

    empty_slots = _require_list(
        payload.get(
            "empty_slots"
        ),
        code=(
            "queue_empty_slots_invalid"
        ),
    )

    if len(
        empty_slots
    ) != 2:
        _fail(
            "queue_empty_slot_count_mismatch"
        )

    expected_empty = {
        3: (
            "regime_abstention",
            "EMPTY_NO_ELIGIBLE_REGIME",
        ),
        4: (
            "exit_time_stop_atr_research",
            "EMPTY_PREREQUISITE_UNAVAILABLE",
        ),
    }

    observed_empty: dict[
        int,
        tuple[str, str],
    ] = {}

    for item in (
        empty_slots
    ):
        if not isinstance(
            item,
            Mapping,
        ):
            _fail(
                "queue_empty_slot_not_object"
            )

        slot = int(
            item.get(
                "slot",
                -1,
            )
        )

        if (
            item.get(
                "replacement_allowed"
            )
            is not False
        ):
            _fail(
                "queue_empty_slot_replacement_allowed",
                slot=slot,
            )

        observed_empty[
            slot
        ] = (
            str(
                item.get(
                    "candidate_class"
                )
            ),
            str(
                item.get(
                    "status"
                )
            ),
        )

    if (
        observed_empty
        != expected_empty
    ):
        _fail(
            "queue_empty_slot_contract_mismatch"
        )

    _validate_queue_authority(
        payload.get(
            "authority"
        ),
        label=(
            "queue"
        ),
    )


def load_method_freeze(
    path: str | Path,
) -> dict[str, Any]:
    target = Path(
        path
    )

    actual_sha = (
        sha256_file(
            target
        )
    )

    if (
        actual_sha
        != EXPECTED_METHOD_FREEZE_FILE_SHA256
    ):
        _fail(
            "method_freeze_file_sha_mismatch",
            expected=(
                EXPECTED_METHOD_FREEZE_FILE_SHA256
            ),
            actual=(
                actual_sha
            ),
        )

    payload = _load_json_object(
        target,
        code_prefix=(
            "method_freeze"
        ),
    )

    validate_method_freeze(
        payload
    )

    return payload


def load_candidate_queue_v2(
    path: str | Path,
) -> dict[str, Any]:
    target = Path(
        path
    )

    actual_sha = (
        sha256_file(
            target
        )
    )

    if (
        actual_sha
        != EXPECTED_QUEUE_V2_FILE_SHA256
    ):
        _fail(
            "queue_v2_file_sha_mismatch",
            expected=(
                EXPECTED_QUEUE_V2_FILE_SHA256
            ),
            actual=(
                actual_sha
            ),
        )

    payload = _load_json_object(
        target,
        code_prefix=(
            "queue_v2"
        ),
    )

    validate_candidate_queue_v2(
        payload
    )

    return payload


def build_post_ocr_hypothesis_registry(
    method_freeze: Mapping[str, Any],
    queue_v2: Mapping[str, Any],
    *,
    method_freeze_path: str | Path | None = None,
    queue_v2_path: str | Path | None = None,
) -> dict[str, Any]:
    validate_method_freeze(
        method_freeze
    )

    validate_candidate_queue_v2(
        queue_v2
    )

    source_evidence = deepcopy(
        dict(
            method_freeze[
                "source_evidence"
            ]
        )
    )

    hypotheses = sorted(
        deepcopy(
            list(
                queue_v2[
                    "candidates"
                ]
            )
        ),
        key=lambda item: int(
            item[
                "slot"
            ]
        ),
    )

    for hypothesis in (
        hypotheses
    ):
        hypothesis[
            "canonical_program_lineage"
        ] = deepcopy(
            source_evidence
        )

        hypothesis[
            "wq5_risk_inheritance"
        ] = deepcopy(
            dict(
                method_freeze[
                    "wq5_inheritance"
                ]
            )
        )

    state_counts = Counter(
        str(
            hypothesis[
                "state"
            ]
        )
        for hypothesis in (
            hypotheses
        )
    )

    class_counts = Counter(
        str(
            hypothesis[
                "candidate_class"
            ]
        )
        for hypothesis in (
            hypotheses
        )
    )

    registry: dict[
        str,
        Any,
    ] = {
        "schema_version": (
            SCHEMA_VERSION
        ),
        "registry_version": (
            REGISTRY_VERSION
        ),
        "status": "ok",
        "engineering_status": (
            "PASS"
        ),
        "wq7_status": (
            "REGISTRY_READY"
        ),
        "quant_edge_status": (
            "NOT_EVALUATED"
        ),
        "decision": (
            "WQ7_REGISTRY_READY_RESEARCH_ONLY"
        ),
        "branch": (
            EXPECTED_BRANCH
        ),
        "base_sha": (
            EXPECTED_BASE_SHA
        ),
        "master_sha256": (
            EXPECTED_MASTER_SHA256
        ),
        "method_freeze": {
            "method_hash": (
                EXPECTED_METHOD_HASH
            ),
            "file_sha256": (
                EXPECTED_METHOD_FREEZE_FILE_SHA256
            ),
            "path": (
                None
                if method_freeze_path
                is None
                else str(
                    Path(
                        method_freeze_path
                    )
                )
            ),
        },
        "source_queue": {
            "queue_hash": (
                EXPECTED_QUEUE_V2_HASH
            ),
            "file_sha256": (
                EXPECTED_QUEUE_V2_FILE_SHA256
            ),
            "queue_policy_hash": (
                EXPECTED_QUEUE_POLICY_HASH
            ),
            "normalization_policy_hash": (
                EXPECTED_NORMALIZATION_POLICY_HASH
            ),
            "path": (
                None
                if queue_v2_path
                is None
                else str(
                    Path(
                        queue_v2_path
                    )
                )
            ),
        },
        "source_evidence": (
            source_evidence
        ),
        "wq6_fingerprints": deepcopy(
            dict(
                method_freeze[
                    "wq6_fingerprints"
                ]
            )
        ),
        "hypothesis_count": len(
            hypotheses
        ),
        "state_counts": dict(
            sorted(
                state_counts.items()
            )
        ),
        "candidate_class_counts": dict(
            sorted(
                class_counts.items()
            )
        ),
        "hypotheses": (
            hypotheses
        ),
        "empty_slots": deepcopy(
            list(
                queue_v2[
                    "empty_slots"
                ]
            )
        ),
        "consumer_contract": {
            "hypothesis_id_is_immutable": True,
            "future_challengers_must_reference_hypothesis_id": True,
            "future_challengers_must_reference_registry_hash": True,
            "future_challengers_must_preserve_dataset_fingerprint": True,
            "future_challengers_must_preserve_split_fingerprint": True,
            "hold_state_is_not_evaluation_authority": True,
            "hold_state_is_not_operational_authority": True,
            "state_transition_requires_separate_evidence": True,
            "surviving_candidate_requires_separate_preregistered_paper_ab_package": True,
        },
        "evaluation": {
            "candidate_evaluation_performed": False,
            "training_performed": False,
            "independent_oos_evaluation_performed": False,
            "threshold_search_performed": False,
            "model_selection_performed": False,
            "paper_ab_performed": False,
        },
        "authority": dict(
            REGISTRY_AUTHORITY
        ),
    }

    registry[
        "registry_hash"
    ] = (
        registry_content_sha256(
            registry
        )
    )

    if (
        registry[
            "registry_hash"
        ]
        != EXPECTED_PORTABLE_REGISTRY_HASH
    ):
        _fail(
            "portable_registry_hash_drift",
            expected=(
                EXPECTED_PORTABLE_REGISTRY_HASH
            ),
            actual=(
                registry[
                    "registry_hash"
                ]
            ),
        )

    validate_post_ocr_hypothesis_registry(
        registry,
        raise_on_error=True,
    )

    return registry


def validate_post_ocr_hypothesis_registry(
    registry: Mapping[str, Any],
    *,
    raise_on_error: bool = False,
) -> list[str]:
    errors: list[str] = []

    def error(
        code: str,
    ) -> None:
        if raise_on_error:
            _fail(
                code
            )

        errors.append(
            code
        )

    if (
        registry.get(
            "schema_version"
        )
        != SCHEMA_VERSION
    ):
        error(
            "registry_schema_mismatch"
        )

    if (
        registry.get(
            "registry_version"
        )
        != REGISTRY_VERSION
    ):
        error(
            "registry_version_mismatch"
        )

    if (
        registry.get(
            "status"
        )
        != "ok"
    ):
        error(
            "registry_status_not_ok"
        )

    if (
        registry.get(
            "engineering_status"
        )
        != "PASS"
    ):
        error(
            "registry_engineering_not_pass"
        )

    if (
        registry.get(
            "wq7_status"
        )
        != "REGISTRY_READY"
    ):
        error(
            "registry_wq7_not_ready"
        )

    if (
        registry.get(
            "quant_edge_status"
        )
        != "NOT_EVALUATED"
    ):
        error(
            "registry_quant_edge_status_drift"
        )

    stored_registry_hash = (
        registry.get(
            "registry_hash"
        )
    )

    recomputed_registry_hash = (
        registry_content_sha256(
            registry
        )
    )

    if (
        stored_registry_hash
        != EXPECTED_PORTABLE_REGISTRY_HASH
    ):
        error(
            "registry_expected_hash_mismatch"
        )

    if (
        recomputed_registry_hash
        != EXPECTED_PORTABLE_REGISTRY_HASH
    ):
        error(
            "registry_content_hash_mismatch"
        )

    if (
        stored_registry_hash
        != recomputed_registry_hash
    ):
        error(
            "registry_stored_vs_recomputed_hash_mismatch"
        )

    source_evidence = (
        registry.get(
            "source_evidence"
        )
    )

    if (
        not isinstance(
            source_evidence,
            Mapping,
        )
        or set(
            source_evidence
        )
        != set(
            EXPECTED_SOURCE_EVIDENCE_SHA256
        )
    ):
        error(
            "registry_source_evidence_invalid"
        )

    hypotheses = (
        registry.get(
            "hypotheses"
        )
    )

    if not isinstance(
        hypotheses,
        list,
    ):
        error(
            "registry_hypotheses_invalid"
        )

        hypotheses = []

    if len(
        hypotheses
    ) != 2:
        error(
            "registry_hypothesis_count_mismatch"
        )

    observed_ids: list[
        str
    ] = []

    for (
        index,
        hypothesis,
    ) in enumerate(
        hypotheses
    ):
        if not isinstance(
            hypothesis,
            Mapping,
        ):
            error(
                f"registry_hypothesis_{index}_invalid"
            )

            continue

        missing = (
            REQUIRED_HYPOTHESIS_FIELDS
            - set(
                hypothesis
            )
        )

        if missing:
            error(
                f"registry_hypothesis_{index}_fields_missing"
            )

        hypothesis_id = str(
            hypothesis.get(
                "hypothesis_id"
            )
        )

        observed_ids.append(
            hypothesis_id
        )

        if (
            hypothesis.get(
                "state"
            )
            != "HOLD"
        ):
            error(
                f"registry_hypothesis_{index}_state_not_hold"
            )

        if (
            hypothesis.get(
                "canonical_program_lineage"
            )
            != source_evidence
        ):
            error(
                f"registry_hypothesis_{index}_lineage_mismatch"
            )

    expected_ids = {
        EXPECTED_QLIB_HYPOTHESIS_ID,
        EXPECTED_SEGMENT_HYPOTHESIS_ID,
    }

    if (
        set(
            observed_ids
        )
        != expected_ids
    ):
        error(
            "registry_hypothesis_id_set_mismatch"
        )

    if len(
        observed_ids
    ) != len(
        set(
            observed_ids
        )
    ):
        error(
            "registry_duplicate_hypothesis_id"
        )

    if (
        registry.get(
            "state_counts"
        )
        != {
            "HOLD": 2
        }
    ):
        error(
            "registry_state_counts_mismatch"
        )

    if (
        registry.get(
            "candidate_class_counts"
        )
        != {
            "qlib_ranking_challenger": 1,
            "segment_filter": 1,
        }
    ):
        error(
            "registry_class_counts_mismatch"
        )

    empty_slots = (
        registry.get(
            "empty_slots"
        )
    )

    if (
        not isinstance(
            empty_slots,
            list,
        )
        or len(
            empty_slots
        )
        != 2
    ):
        error(
            "registry_empty_slots_invalid"
        )

    evaluation = (
        registry.get(
            "evaluation"
        )
    )

    if not isinstance(
        evaluation,
        Mapping,
    ):
        error(
            "registry_evaluation_invalid"
        )

    else:
        for (
            field,
            value,
        ) in evaluation.items():
            if (
                value
                is not False
            ):
                error(
                    f"registry_evaluation_{field}_must_be_false"
                )

    if (
        registry.get(
            "authority"
        )
        != REGISTRY_AUTHORITY
    ):
        error(
            "registry_authority_mismatch"
        )

    return errors


def list_registered_hypotheses(
    registry: Mapping[str, Any],
    *,
    state: str | None = None,
    candidate_class: str | None = None,
) -> list[dict[str, Any]]:
    errors = (
        validate_post_ocr_hypothesis_registry(
            registry
        )
    )

    if errors:
        _fail(
            "registry_invalid_for_consumption",
            errors=errors,
        )

    selected: list[
        dict[str, Any]
    ] = []

    for hypothesis in (
        registry[
            "hypotheses"
        ]
    ):
        if (
            state is not None
            and hypothesis.get(
                "state"
            )
            != state
        ):
            continue

        if (
            candidate_class
            is not None
            and hypothesis.get(
                "candidate_class"
            )
            != candidate_class
        ):
            continue

        selected.append(
            deepcopy(
                dict(
                    hypothesis
                )
            )
        )

    return selected


def get_registered_hypothesis(
    registry: Mapping[str, Any],
    hypothesis_id: str,
) -> dict[str, Any]:
    matches = [
        hypothesis
        for hypothesis in (
            list_registered_hypotheses(
                registry
            )
        )
        if (
            hypothesis.get(
                "hypothesis_id"
            )
            == hypothesis_id
        )
    ]

    if len(
        matches
    ) != 1:
        _fail(
            "registered_hypothesis_not_found_or_not_unique",
            hypothesis_id=(
                hypothesis_id
            ),
            count=len(
                matches
            ),
        )

    return matches[
        0
    ]


def persist_post_ocr_hypothesis_registry(
    registry: Mapping[str, Any],
    *,
    output_path: str | Path,
) -> str:
    errors = (
        validate_post_ocr_hypothesis_registry(
            registry
        )
    )

    if errors:
        _fail(
            "registry_invalid_for_persistence",
            errors=errors,
        )

    target = Path(
        output_path
    )

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    rendered = (
        json.dumps(
            dict(
                registry
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode(
        "utf-8"
    )

    descriptor, temp_name = (
        tempfile.mkstemp(
            prefix=(
                f".{target.name}."
            ),
            suffix=".tmp",
            dir=str(
                target.parent
            ),
        )
    )

    try:
        with os.fdopen(
            descriptor,
            "wb",
        ) as handle:
            handle.write(
                rendered
            )

            handle.flush()

            os.fsync(
                handle.fileno()
            )

        os.replace(
            temp_name,
            target,
        )

    except Exception:
        try:
            os.unlink(
                temp_name
            )

        except FileNotFoundError:
            pass

        raise

    return str(
        target
    )


def load_post_ocr_hypothesis_registry(
    path: str | Path,
) -> dict[str, Any]:
    payload = _load_json_object(
        path,
        code_prefix=(
            "registry"
        ),
    )

    errors = (
        validate_post_ocr_hypothesis_registry(
            payload
        )
    )

    if errors:
        _fail(
            "registry_validation_failed",
            errors=errors,
        )

    return payload


__all__ = [
    "ALLOWED_STATES",
    "EXPECTED_METHOD_FREEZE_FILE_SHA256",
    "EXPECTED_METHOD_HASH",
    "EXPECTED_NORMALIZATION_POLICY_HASH",
    "EXPECTED_PORTABLE_REGISTRY_HASH",
    "EXPECTED_QUEUE_POLICY_HASH",
    "EXPECTED_QUEUE_V2_FILE_SHA256",
    "EXPECTED_QUEUE_V2_HASH",
    "OfficialHypothesisRegistryError",
    "REGISTRY_AUTHORITY",
    "REGISTRY_VERSION",
    "REQUIRED_HYPOTHESIS_FIELDS",
    "SCHEMA_VERSION",
    "build_post_ocr_hypothesis_registry",
    "canonical_registry_payload",
    "get_registered_hypothesis",
    "list_registered_hypotheses",
    "load_candidate_queue_v2",
    "load_method_freeze",
    "load_post_ocr_hypothesis_registry",
    "method_freeze_content_sha256",
    "persist_post_ocr_hypothesis_registry",
    "queue_content_sha256",
    "registry_content_sha256",
    "sha256_file",
    "validate_candidate_queue_v2",
    "validate_method_freeze",
    "validate_post_ocr_hypothesis_registry",
]
