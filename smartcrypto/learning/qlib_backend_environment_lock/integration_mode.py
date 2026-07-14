"""Pure validation for the institutional Qlib 24/7 integration-mode ADR."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "qlib_24x7_integration_mode_v1"
ADR_ID = "ADR-001"
DEFAULT_CONTRACT_PATH = Path("config/qlib_integration_mode_v1.json")

MODE_MODEL_ZOO_PARQUET = "model_zoo_versioned_parquet"
MODE_NATIVE_PROVIDER = "native_provider_continuous_crypto"
ALLOWED_MODES = frozenset({MODE_MODEL_ZOO_PARQUET, MODE_NATIVE_PROVIDER})

CALENDAR_24X7_UTC = "continuous_crypto_24x7_utc"
TRANSPORT_VERSIONED_PARQUET = "versioned_parquet"
TRANSPORT_NATIVE_PROVIDER = "qlib_native_provider"

REQUIRED_FIELDS = (
    "schema_version",
    "adr_id",
    "adr_status",
    "selected_mode",
    "decision_rationale",
    "calendar_mode",
    "timezone",
    "dataset_transport",
    "dataset_manifest_required",
    "feature_contract_required",
    "label_contract_required",
    "cost_model_required",
    "anti_leakage_contract_required",
    "provider_runtime_authority",
    "research_only",
    "paper_only",
    "shadow_only",
    "operational_authority",
    "training_authorized",
    "model_promotion_authorized",
    "qlib_runtime_update_authorized",
    "ai_shadow_runtime_update_authorized",
    "freqtrade_update_authorized",
    "risk_manager_update_authorized",
    "writes_runtime",
    "writes_active_model",
    "sends_orders",
    "exchange_private_access",
    "rollback_mode",
    "validation_gates",
)

REQUIRED_TRUE_FIELDS = (
    "dataset_manifest_required",
    "feature_contract_required",
    "label_contract_required",
    "cost_model_required",
    "anti_leakage_contract_required",
    "research_only",
    "paper_only",
    "shadow_only",
)

REQUIRED_FALSE_FIELDS = (
    "provider_runtime_authority",
    "operational_authority",
    "training_authorized",
    "model_promotion_authorized",
    "qlib_runtime_update_authorized",
    "ai_shadow_runtime_update_authorized",
    "freqtrade_update_authorized",
    "risk_manager_update_authorized",
    "writes_runtime",
    "writes_active_model",
    "sends_orders",
    "exchange_private_access",
)

COMMON_VALIDATION_GATES = (
    "selected_mode_defined",
    "crypto_calendar_contract_defined",
    "timezone_utc_enforced",
    "dataset_manifest_required",
    "feature_contract_required",
    "label_contract_required",
    "cost_model_required",
    "anti_leakage_required",
    "timestamp_determinism_required",
    "no_silent_timezone_conversion",
    "purging_embargo_required",
    "cpcv_pbo_evidence_required_for_future_training",
    "windows_compatible",
    "linux_compatible",
    "docker_compatible",
)

MODE_B_EVIDENCE_GATES = (
    "native_provider_24x7_evidence",
    "cross_platform_equivalence_evidence",
    "dataset_manifest_preservation_evidence",
    "feature_contract_preservation_evidence",
    "timezone_determinism_evidence",
    "provider_runtime_independence_evidence",
    "anti_leakage_calendar_equivalence_evidence",
)


def load_qlib_integration_mode_contract(path: str | Path) -> dict[str, Any]:
    """Load one JSON contract without initializing Qlib or touching datasets."""

    contract_path = Path(path)
    payload = json.loads(contract_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError("contract_root_must_be_object")
    return {str(key): value for key, value in payload.items()}


def validate_qlib_integration_mode_contract(
    contract: Mapping[str, Any],
) -> list[str]:
    """Return deterministic validation errors for an ADR contract payload."""

    errors: list[str] = []
    for field in REQUIRED_FIELDS:
        if field not in contract:
            errors.append(f"missing_required_field:{field}")

    if contract.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    if contract.get("adr_id") != ADR_ID:
        errors.append("adr_id_mismatch")
    if contract.get("adr_status") != "approved":
        errors.append("adr_status_not_approved")

    selected_mode = contract.get("selected_mode")
    if selected_mode not in ALLOWED_MODES:
        errors.append("unknown_selected_mode")
    if contract.get("timezone") != "UTC":
        errors.append("timezone_must_be_utc")
    if contract.get("calendar_mode") != CALENDAR_24X7_UTC:
        errors.append("calendar_must_be_continuous_crypto_24x7_utc")
    if not _has_rationale(contract.get("decision_rationale")):
        errors.append("decision_rationale_required")

    for field in REQUIRED_TRUE_FIELDS:
        if contract.get(field) is not True:
            errors.append(f"{field}_must_be_true")
    for field in REQUIRED_FALSE_FIELDS:
        if contract.get(field) is not False:
            errors.append(f"{field}_must_be_false")

    rollback_mode = contract.get("rollback_mode")
    if rollback_mode not in ALLOWED_MODES:
        errors.append("unknown_rollback_mode")

    gates = contract.get("validation_gates")
    if not isinstance(gates, Mapping):
        errors.append("validation_gates_must_be_object")
        gates = {}
    for gate in COMMON_VALIDATION_GATES:
        if gates.get(gate) is not True:
            errors.append(f"validation_gate_not_satisfied:{gate}")

    if selected_mode == MODE_MODEL_ZOO_PARQUET:
        if contract.get("dataset_transport") != TRANSPORT_VERSIONED_PARQUET:
            errors.append("mode_a_requires_versioned_parquet")
        if rollback_mode != MODE_MODEL_ZOO_PARQUET:
            errors.append("mode_a_requires_parquet_rollback")
    elif selected_mode == MODE_NATIVE_PROVIDER:
        if contract.get("dataset_transport") != TRANSPORT_NATIVE_PROVIDER:
            errors.append("mode_b_requires_qlib_native_provider_transport")
        for gate in MODE_B_EVIDENCE_GATES:
            if gates.get(gate) is not True:
                errors.append(f"mode_b_evidence_gate_not_satisfied:{gate}")

    return sorted(set(errors))


def build_qlib_24x7_integration_mode_report(
    *,
    project_root: str | Path,
    contract_path: str | Path | None = None,
) -> dict[str, Any]:
    """Audit the ADR contract in memory and never write or initialize Qlib."""

    root = Path(project_root).resolve()
    resolved_contract = _resolve_contract_path(root, contract_path)
    contract: dict[str, Any] = {}
    load_error: str | None = None
    contract_hash: str | None = None
    try:
        contract = load_qlib_integration_mode_contract(resolved_contract)
        contract_hash = _file_sha256(resolved_contract)
    except FileNotFoundError:
        load_error = "contract_file_missing"
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        load_error = f"contract_unreadable:{type(exc).__name__}"

    errors = (
        [load_error]
        if load_error is not None
        else validate_qlib_integration_mode_contract(contract)
    )
    contract_valid = not errors
    selected_mode = contract.get("selected_mode")
    calendar_mode = contract.get("calendar_mode")
    timezone = contract.get("timezone")
    report: dict[str, Any] = {
        "status": "ok" if contract_valid else "blocked",
        "reason": (
            "qlib_24x7_integration_mode_contract_valid"
            if contract_valid
            else "qlib_24x7_integration_mode_contract_invalid"
        ),
        "decision": (
            "QLIB_MODE_A_APPROVED_RESEARCH_ONLY"
            if contract_valid and selected_mode == MODE_MODEL_ZOO_PARQUET
            else (
                "QLIB_MODE_B_APPROVED_RESEARCH_ONLY"
                if contract_valid and selected_mode == MODE_NATIVE_PROVIDER
                else "QLIB_INTEGRATION_MODE_CONTRACT_BLOCKED"
            )
        ),
        "schema_version": contract.get("schema_version"),
        "adr_id": contract.get("adr_id"),
        "adr_status": contract.get("adr_status"),
        "qlib_adr_status": contract.get("adr_status"),
        "selected_mode": selected_mode,
        "calendar_mode": calendar_mode,
        "timezone": timezone,
        "dataset_transport": contract.get("dataset_transport"),
        "contract_path": str(resolved_contract),
        "contract_sha256": contract_hash,
        "contract_valid": contract_valid,
        "selected_mode_defined": selected_mode in ALLOWED_MODES,
        "crypto_calendar_contract_defined": calendar_mode == CALENDAR_24X7_UTC,
        "timezone_utc_enforced": timezone == "UTC",
        "dataset_manifest_required": contract.get("dataset_manifest_required") is True,
        "feature_contract_required": contract.get("feature_contract_required") is True,
        "label_contract_required": contract.get("label_contract_required") is True,
        "cost_model_required": contract.get("cost_model_required") is True,
        "anti_leakage_required": contract.get("anti_leakage_contract_required") is True,
        "provider_runtime_authority": contract.get("provider_runtime_authority"),
        "research_only": contract.get("research_only"),
        "paper_only": contract.get("paper_only"),
        "shadow_only": contract.get("shadow_only"),
        "operational_authority": contract.get("operational_authority"),
        "training_authorized": contract.get("training_authorized"),
        "model_promotion_authorized": contract.get("model_promotion_authorized"),
        "qlib_runtime_update_authorized": contract.get(
            "qlib_runtime_update_authorized"
        ),
        "ai_shadow_runtime_update_authorized": contract.get(
            "ai_shadow_runtime_update_authorized"
        ),
        "freqtrade_update_authorized": contract.get("freqtrade_update_authorized"),
        "risk_manager_update_authorized": contract.get(
            "risk_manager_update_authorized"
        ),
        "writes_runtime": contract.get("writes_runtime"),
        "writes_active_model": contract.get("writes_active_model"),
        "sends_orders": contract.get("sends_orders"),
        "exchange_private_access": contract.get("exchange_private_access"),
        "write_performed": False,
        "qlib_initialized": False,
        "models_loaded": False,
        "datasets_loaded": False,
        "validation_errors": errors,
    }
    return report


def _resolve_contract_path(root: Path, value: str | Path | None) -> Path:
    path = Path(value) if value is not None else DEFAULT_CONTRACT_PATH
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _has_rationale(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value) and all(isinstance(item, str) and item.strip() for item in value)
    return False


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
