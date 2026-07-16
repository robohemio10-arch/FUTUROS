"""Strict contract for the guarded Bitradex OCR legacy append transition."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, NoReturn


SCHEMA_VERSION = "trader_master_legacy_authorized_transition_v1"
TRANSITION_ID = "bitradex_ocr_legacy_append_20260714_151816_v1"
SOURCE_CONTRACT_ID = "bitradex_ocr_legacy_20260714_151816_v1"
BATCH_ID = "20260714_151816"
TRANSITION_STATE = "planned_not_executed"
AUTHORIZATION_PHRASE = "AUTHORIZE_BITRADEX_OCR_LEGACY_APPEND_20260714_151816_504"
IMPORTED_AT_UTC = "2026-07-14T19:49:13.500939+00:00"
DEFAULT_TRANSITION_CONTRACT = Path("config/bitradex_ocr_legacy_append_transition_v1.json")
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
EXPECTED_ALLOWED_ROOTS = (
    "data/trades",
    "data/backups/bitradex_ocr_legacy_append",
    "data/reports",
    "data/locks",
)


class TransitionContractError(ValueError):
    """Raised when transition authority is absent, malformed, or drifted."""


@dataclass(frozen=True)
class PreState:
    master_xlsx_path: str
    master_parquet_path: str
    master_xlsx_sha256: str
    master_parquet_sha256: str
    master_row_count: int
    master_schema_column_count: int


@dataclass(frozen=True)
class AppendState:
    candidate_count: int
    expected_post_row_count: int
    source_preview_summary: str
    source_preview_csv: str
    xlsx_sheet: str
    preserve_existing_prefix: bool
    append_order: str


@dataclass(frozen=True)
class FundingPolicy:
    funding_fee_value: None
    funding_assumed_zero: bool
    funding_derived_as_residual: bool
    v2_financial_decomposition_eligible: bool


@dataclass(frozen=True)
class IdentityPolicy:
    synthetic_order_id_authoritative: bool
    synthetic_order_id_role: str
    use_synthetic_order_id_as_v2_identity: bool
    dedup_keys_are_collision_guards_only: bool


@dataclass(frozen=True)
class ImportedAtPolicy:
    semantic_role: str
    source_type: str
    source_relative_path: str
    source_json_path: str
    source_file_sha256: str
    value_utc: str
    applies_to_all_candidate_rows: bool
    is_trade_event_timestamp: bool
    derived_from_trade_fields: bool
    runtime_clock_allowed: bool
    filesystem_timestamp_allowed: bool
    batch_token_timestamp_allowed: bool


@dataclass(frozen=True)
class ExecutionPolicy:
    default_mode: str
    apply_requires_plan_sha256: bool
    apply_requires_authorization_phrase: bool
    authorization_phrase: str
    backup_required: bool
    rollback_required: bool
    cross_format_semantic_equality_required: bool
    post_apply_attestation_required: bool
    idempotent_reapply_forbidden: bool


@dataclass(frozen=True)
class SafetyPolicy:
    operational_authority: bool
    sends_orders: bool
    changes_risk: bool
    exchange_private_access: bool


@dataclass(frozen=True)
class TransitionContract:
    schema_version: str
    transition_id: str
    transition_state: str
    source_contract: str
    source_contract_id: str
    batch_id: str
    pre_state: PreState
    append_state: AppendState
    funding_policy: FundingPolicy
    identity_policy: IdentityPolicy
    imported_at_policy: ImportedAtPolicy
    execution_policy: ExecutionPolicy
    allowed_write_roots: tuple[str, ...]
    authorized_source_sha256: tuple[tuple[str, str], ...]
    safety: SafetyPolicy

    def source_hashes(self) -> dict[str, str]:
        return dict(self.authorized_source_sha256)


def load_transition_contract(
    path: str | Path,
    *,
    project_root: str | Path | None = None,
) -> TransitionContract:
    source = Path(path)
    if source.suffix.casefold() != ".json":
        _fail("transition_contract_extension_invalid")
    if _has_symlink_component(source):
        _fail("transition_contract_symlink_rejected")
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        _fail("transition_contract_missing")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"transition_contract_unreadable:{type(exc).__name__}")
    if not isinstance(payload, dict):
        _fail("transition_contract_root_must_be_object")
    contract = parse_transition_contract(payload)
    root = (
        Path(project_root).resolve()
        if project_root is not None
        else source.resolve().parent.parent
    )
    validate_imported_at_source(root, contract.imported_at_policy)
    return contract


def parse_transition_contract(payload: Mapping[str, Any]) -> TransitionContract:
    schema = _string(payload, "schema_version")
    transition_id = _string(payload, "transition_id")
    state = _string(payload, "transition_state")
    source_contract_id = _string(payload, "source_contract_id")
    batch_id = _string(payload, "batch_id")
    _equal(schema, SCHEMA_VERSION, "transition_schema_version_invalid")
    _equal(transition_id, TRANSITION_ID, "transition_id_invalid")
    _equal(state, TRANSITION_STATE, "transition_state_invalid")
    _equal(source_contract_id, SOURCE_CONTRACT_ID, "source_contract_id_invalid")
    _equal(batch_id, BATCH_ID, "transition_batch_id_invalid")

    pre = _object(payload, "pre_state")
    pre_state = PreState(
        master_xlsx_path=_string(pre, "master_xlsx_path"),
        master_parquet_path=_string(pre, "master_parquet_path"),
        master_xlsx_sha256=_hash(pre, "master_xlsx_sha256"),
        master_parquet_sha256=_hash(pre, "master_parquet_sha256"),
        master_row_count=_integer(pre, "master_row_count"),
        master_schema_column_count=_integer(pre, "master_schema_column_count"),
    )
    append = _object(payload, "append_state")
    append_state = AppendState(
        candidate_count=_integer(append, "candidate_count"),
        expected_post_row_count=_integer(append, "expected_post_row_count"),
        source_preview_summary=_string(append, "source_preview_summary"),
        source_preview_csv=_string(append, "source_preview_csv"),
        xlsx_sheet=_string(append, "xlsx_sheet"),
        preserve_existing_prefix=_boolean(append, "preserve_existing_prefix"),
        append_order=_string(append, "append_order"),
    )
    funding_payload = _object(payload, "funding_policy")
    if "funding_fee_value" not in funding_payload or funding_payload["funding_fee_value"] is not None:
        _fail("transition_funding_fee_must_be_null")
    funding = FundingPolicy(
        funding_fee_value=None,
        funding_assumed_zero=_boolean(funding_payload, "funding_assumed_zero"),
        funding_derived_as_residual=_boolean(funding_payload, "funding_derived_as_residual"),
        v2_financial_decomposition_eligible=_boolean(
            funding_payload, "v2_financial_decomposition_eligible"
        ),
    )
    identity_payload = _object(payload, "identity_policy")
    identity = IdentityPolicy(
        synthetic_order_id_authoritative=_boolean(
            identity_payload, "synthetic_order_id_authoritative"
        ),
        synthetic_order_id_role=_string(identity_payload, "synthetic_order_id_role"),
        use_synthetic_order_id_as_v2_identity=_boolean(
            identity_payload, "use_synthetic_order_id_as_v2_identity"
        ),
        dedup_keys_are_collision_guards_only=_boolean(
            identity_payload, "dedup_keys_are_collision_guards_only"
        ),
    )
    imported_at_payload = _object(payload, "imported_at_policy")
    imported_at = ImportedAtPolicy(
        semantic_role=_string(imported_at_payload, "semantic_role"),
        source_type=_string(imported_at_payload, "source_type"),
        source_relative_path=_string(imported_at_payload, "source_relative_path"),
        source_json_path=_string(imported_at_payload, "source_json_path"),
        source_file_sha256=_hash(imported_at_payload, "source_file_sha256"),
        value_utc=_string(imported_at_payload, "value_utc"),
        applies_to_all_candidate_rows=_boolean(
            imported_at_payload, "applies_to_all_candidate_rows"
        ),
        is_trade_event_timestamp=_boolean(
            imported_at_payload, "is_trade_event_timestamp"
        ),
        derived_from_trade_fields=_boolean(
            imported_at_payload, "derived_from_trade_fields"
        ),
        runtime_clock_allowed=_boolean(
            imported_at_payload, "runtime_clock_allowed"
        ),
        filesystem_timestamp_allowed=_boolean(
            imported_at_payload, "filesystem_timestamp_allowed"
        ),
        batch_token_timestamp_allowed=_boolean(
            imported_at_payload, "batch_token_timestamp_allowed"
        ),
    )
    execution_payload = _object(payload, "execution_policy")
    execution = ExecutionPolicy(
        default_mode=_string(execution_payload, "default_mode"),
        apply_requires_plan_sha256=_boolean(execution_payload, "apply_requires_plan_sha256"),
        apply_requires_authorization_phrase=_boolean(
            execution_payload, "apply_requires_authorization_phrase"
        ),
        authorization_phrase=_string(execution_payload, "authorization_phrase"),
        backup_required=_boolean(execution_payload, "backup_required"),
        rollback_required=_boolean(execution_payload, "rollback_required"),
        cross_format_semantic_equality_required=_boolean(
            execution_payload, "cross_format_semantic_equality_required"
        ),
        post_apply_attestation_required=_boolean(
            execution_payload, "post_apply_attestation_required"
        ),
        idempotent_reapply_forbidden=_boolean(
            execution_payload, "idempotent_reapply_forbidden"
        ),
    )
    safety_payload = _object(payload, "safety")
    safety = SafetyPolicy(
        operational_authority=_boolean(safety_payload, "operational_authority"),
        sends_orders=_boolean(safety_payload, "sends_orders"),
        changes_risk=_boolean(safety_payload, "changes_risk"),
        exchange_private_access=_boolean(safety_payload, "exchange_private_access"),
    )
    roots = _string_tuple(payload, "allowed_write_roots")
    source_hashes_payload = _object(payload, "authorized_source_sha256")
    source_hashes = tuple(sorted((str(key), _hash(source_hashes_payload, str(key))) for key in source_hashes_payload))

    if pre_state.master_row_count != 3058 or pre_state.master_schema_column_count != 25:
        _fail("transition_pre_state_invalid")
    if append_state.candidate_count != 504 or append_state.expected_post_row_count != 3562:
        _fail("transition_append_state_invalid")
    if pre_state.master_row_count + append_state.candidate_count != append_state.expected_post_row_count:
        _fail("transition_row_accounting_invalid")
    if append_state.xlsx_sheet != "trades_master_candidate":
        _fail("transition_xlsx_sheet_invalid")
    if append_state.preserve_existing_prefix is not True:
        _fail("transition_prefix_preservation_required")
    if append_state.append_order != "preview_csv_source_order":
        _fail("transition_append_order_invalid")
    if any((funding.funding_assumed_zero, funding.funding_derived_as_residual, funding.v2_financial_decomposition_eligible)):
        _fail("transition_funding_policy_unsafe")
    if identity.synthetic_order_id_authoritative or identity.use_synthetic_order_id_as_v2_identity:
        _fail("transition_synthetic_identity_unsafe")
    if identity.synthetic_order_id_role != "legacy_dedup_alias_evidence_only":
        _fail("transition_synthetic_identity_role_invalid")
    if identity.dedup_keys_are_collision_guards_only is not True:
        _fail("transition_collision_guard_policy_invalid")
    if imported_at.semantic_role != "ingestion_provenance_metadata":
        _fail("transition_imported_at_semantic_role_invalid")
    if imported_at.source_type != "package_authoritative_metadata":
        _fail("transition_imported_at_source_type_invalid")
    if imported_at.source_json_path != "finalized_at_utc":
        _fail("transition_imported_at_json_path_invalid")
    _validate_utc_timestamp(imported_at.value_utc)
    if imported_at.value_utc != IMPORTED_AT_UTC:
        _fail("transition_imported_at_value_invalid")
    if imported_at.applies_to_all_candidate_rows is not True:
        _fail("transition_imported_at_scope_invalid")
    if imported_at.is_trade_event_timestamp:
        _fail("transition_imported_at_trade_timestamp_forbidden")
    if any(
        (
            imported_at.derived_from_trade_fields,
            imported_at.runtime_clock_allowed,
            imported_at.filesystem_timestamp_allowed,
            imported_at.batch_token_timestamp_allowed,
        )
    ):
        _fail("transition_imported_at_fallback_policy_unsafe")
    if execution.default_mode != "plan":
        _fail("transition_default_mode_must_be_plan")
    if execution.authorization_phrase != AUTHORIZATION_PHRASE:
        _fail("transition_authorization_phrase_invalid")
    if not all(
        (
            execution.apply_requires_plan_sha256,
            execution.apply_requires_authorization_phrase,
            execution.backup_required,
            execution.rollback_required,
            execution.cross_format_semantic_equality_required,
            execution.post_apply_attestation_required,
            execution.idempotent_reapply_forbidden,
        )
    ):
        _fail("transition_execution_policy_unsafe")
    if roots != EXPECTED_ALLOWED_ROOTS:
        _fail("transition_allowed_write_roots_invalid")
    if any((safety.operational_authority, safety.sends_orders, safety.changes_risk, safety.exchange_private_access)):
        _fail("transition_safety_flags_unsafe")
    if not source_hashes:
        _fail("transition_authorized_sources_missing")

    return TransitionContract(
        schema_version=schema,
        transition_id=transition_id,
        transition_state=state,
        source_contract=_string(payload, "source_contract"),
        source_contract_id=source_contract_id,
        batch_id=batch_id,
        pre_state=pre_state,
        append_state=append_state,
        funding_policy=funding,
        identity_policy=identity,
        imported_at_policy=imported_at,
        execution_policy=execution,
        allowed_write_roots=roots,
        authorized_source_sha256=source_hashes,
        safety=safety,
    )


def validate_imported_at_source(root: Path, policy: ImportedAtPolicy) -> Path:
    if _unsafe_relative_path(policy.source_relative_path):
        _fail("imported_at_source_path_unsafe")
    source = root / policy.source_relative_path
    if source.suffix.casefold() != ".json":
        _fail("imported_at_source_extension_invalid")
    if _has_symlink_component(source):
        _fail("imported_at_source_symlink_rejected")
    try:
        resolved = source.resolve(strict=True)
        resolved.relative_to(root.resolve())
    except FileNotFoundError:
        _fail("imported_at_source_missing")
    except ValueError:
        _fail("imported_at_source_outside_project_root")
    if not resolved.is_file():
        _fail("imported_at_source_missing")
    if file_sha256(resolved) != policy.source_file_sha256:
        _fail("imported_at_source_hash_mismatch")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"imported_at_source_unreadable:{type(exc).__name__}")
    if not isinstance(payload, dict):
        _fail("imported_at_source_root_must_be_object")
    if policy.source_json_path not in payload:
        _fail("imported_at_source_field_missing")
    observed = payload[policy.source_json_path]
    if not isinstance(observed, str):
        _fail("imported_at_source_value_must_be_string")
    if observed != policy.value_utc:
        _fail("imported_at_source_value_mismatch")
    _validate_utc_timestamp(observed)
    return resolved


def verify_authorized_source_hashes(root: Path, contract: TransitionContract) -> tuple[str, ...]:
    errors: list[str] = []
    for relative, expected in contract.authorized_source_sha256:
        path = root / relative
        if _unsafe_relative_path(relative) or path.is_symlink() or not path.is_file():
            errors.append(f"authorized_transition_source_invalid:{relative}")
            continue
        actual = file_sha256(path)
        if actual != expected:
            errors.append(f"authorized_transition_source_hash_mismatch:{relative}")
    return tuple(sorted(errors))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unsafe_relative_path(value: str) -> bool:
    path = Path(value)
    return path.is_absolute() or ".." in path.parts


def _has_symlink_component(path: Path) -> bool:
    current = path
    while True:
        if current.is_symlink():
            return True
        if current.parent == current:
            return False
        current = current.parent


def _validate_utc_timestamp(value: str) -> None:
    if not (value.endswith("+00:00") or value.endswith("Z")):
        _fail("imported_at_source_timezone_must_be_utc")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        _fail("imported_at_source_timestamp_invalid")
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        _fail("imported_at_source_timezone_must_be_utc")


def _object(payload: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = payload.get(field)
    if not isinstance(value, dict):
        _fail(f"transition_object_required:{field}")
    return value


def _string(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        _fail(f"transition_string_required:{field}")
    return value.strip()


def _integer(payload: Mapping[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(f"transition_integer_required:{field}")
    return value


def _boolean(payload: Mapping[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        _fail(f"transition_boolean_required:{field}")
    return value


def _hash(payload: Mapping[str, Any], field: str) -> str:
    value = _string(payload, field)
    if HEX64.fullmatch(value) is None:
        _fail(f"transition_sha256_invalid:{field}")
    return value.casefold()


def _string_tuple(payload: Mapping[str, Any], field: str) -> tuple[str, ...]:
    value = payload.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        _fail(f"transition_string_array_required:{field}")
    return tuple(str(item) for item in value)


def _equal(actual: str, expected: str, code: str) -> None:
    if actual != expected:
        _fail(code)


def _fail(code: str) -> NoReturn:
    raise TransitionContractError(code)
