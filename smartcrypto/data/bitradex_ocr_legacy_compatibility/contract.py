"""Strict, versioned contract for historical Bitradex OCR compatibility."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, NoReturn


LEGACY_CONTRACT_SCHEMA_VERSION = "bitradex_ocr_legacy_contract_v1"
LEGACY_CONTRACT_ID = "bitradex_ocr_legacy_20260714_151816_v1"
LEGACY_BATCH_ID = "20260714_151816"
LEGACY_CONTRACT_MODE = "legacy_historical_append_compatibility"
LEGACY_MASTER_SCHEMA_VERSION = "trader_master_legacy_ocr_v1"
FUNDING_STATUS = "unknown_not_available_in_source"
FUNDING_REPORTED_STATUS = "unknown"
SYNTHETIC_ORDER_ID_ROLE = "legacy_dedup_alias_evidence_only"
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class LegacyContractError(ValueError):
    """Raised when the legacy compatibility contract is unsafe or malformed."""


@dataclass(frozen=True)
class LegacySources:
    staging_package: str
    preview_summary: str
    preview_csv: str
    master_xlsx: str
    master_parquet: str


@dataclass(frozen=True)
class ExpectedCounts:
    ocr_input_rows: int
    excluded_exact_duplicates: int
    retained_candidate_rows: int
    master_rows_before: int
    master_rows_after_authorized_append: int
    sidecar_auto_matched_rows: int
    sidecar_residual_equivalence_rows: int
    legacy_novel_candidate_rows: int
    ambiguous_master_match_rows: int
    invalid_identity_rows: int
    internal_duplicate_excess_rows: int
    fallback_collision_rows: int
    source_image_conflict_rows: int


@dataclass(frozen=True)
class ExpectedMasterHashes:
    xlsx_sha256: str
    parquet_sha256: str


@dataclass(frozen=True)
class HistoricalMasterSchema:
    schema_version: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class FundingPolicy:
    funding_required_for_legacy_contract: bool
    funding_fee_value: None
    funding_status: str
    funding_assumed_zero: bool
    funding_derived_as_residual: bool
    funding_included_in_reported_net_pnl: str
    v2_financial_decomposition_eligible: bool


@dataclass(frozen=True)
class IdentityPolicy:
    synthetic_order_id_present: bool
    synthetic_order_id_authoritative: bool
    synthetic_order_id_role: str
    native_exchange_order_identity_available: bool
    account_scope_required_for_legacy_contract: bool
    v2_primary_identity_eligible: bool


@dataclass(frozen=True)
class AuthorityPolicy:
    operational_authority: bool
    import_authorized: bool
    write_authorized: bool
    manual_authorization_required: bool
    official_import_allowed: bool


@dataclass(frozen=True)
class LegacyContract:
    schema_version: str
    contract_id: str
    batch_id: str
    contract_mode: str
    source_profile_path: str
    sources: LegacySources
    expected_counts: ExpectedCounts
    expected_master_hashes: ExpectedMasterHashes
    historical_master_schema: HistoricalMasterSchema
    funding_policy: FundingPolicy
    identity_policy: IdentityPolicy
    authority: AuthorityPolicy


def load_legacy_contract(path: str | Path) -> LegacyContract:
    """Load and validate a JSON contract without applying financial defaults."""

    source = Path(path)
    if source.suffix.casefold() != ".json":
        _fail("contract_extension_must_be_json")
    if source.is_symlink():
        _fail("contract_symlink_rejected")
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        _fail("contract_missing")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"contract_unreadable:{type(exc).__name__}")
    if not isinstance(payload, dict):
        _fail("contract_root_must_be_object")
    return parse_legacy_contract(payload)


def parse_legacy_contract(payload: Mapping[str, Any]) -> LegacyContract:
    schema_version = _required_string(payload, "schema_version")
    contract_id = _required_string(payload, "contract_id")
    batch_id = _required_string(payload, "batch_id")
    contract_mode = _required_string(payload, "contract_mode")
    _require_equal(schema_version, LEGACY_CONTRACT_SCHEMA_VERSION, "schema_version_invalid")
    _require_equal(contract_id, LEGACY_CONTRACT_ID, "contract_id_invalid")
    _require_equal(batch_id, LEGACY_BATCH_ID, "batch_id_invalid")
    _require_equal(contract_mode, LEGACY_CONTRACT_MODE, "contract_mode_invalid")

    sources_payload = _required_object(payload, "sources")
    sources = LegacySources(
        **{name: _required_string(sources_payload, name) for name in LegacySources.__annotations__}
    )
    counts_payload = _required_object(payload, "expected_counts")
    counts = ExpectedCounts(
        **{name: _required_integer(counts_payload, name) for name in ExpectedCounts.__annotations__}
    )
    hashes_payload = _required_object(payload, "expected_master_hashes")
    hashes = ExpectedMasterHashes(
        xlsx_sha256=_required_hash(hashes_payload, "xlsx_sha256"),
        parquet_sha256=_required_hash(hashes_payload, "parquet_sha256"),
    )
    schema_payload = _required_object(payload, "historical_master_schema")
    historical_schema = HistoricalMasterSchema(
        schema_version=_required_string(schema_payload, "schema_version"),
        columns=_required_string_tuple(schema_payload, "columns"),
    )
    _require_equal(
        historical_schema.schema_version,
        LEGACY_MASTER_SCHEMA_VERSION,
        "historical_master_schema_version_invalid",
    )
    if len(historical_schema.columns) != 25 or len(set(historical_schema.columns)) != 25:
        _fail("historical_master_schema_columns_invalid")

    funding_payload = _required_object(payload, "funding_policy")
    if "funding_fee_value" not in funding_payload:
        _fail("contract_field_missing:funding_fee_value")
    if funding_payload["funding_fee_value"] is not None:
        _fail("funding_fee_value_must_be_null")
    funding = FundingPolicy(
        funding_required_for_legacy_contract=_required_boolean(
            funding_payload, "funding_required_for_legacy_contract"
        ),
        funding_fee_value=None,
        funding_status=_required_string(funding_payload, "funding_status"),
        funding_assumed_zero=_required_boolean(funding_payload, "funding_assumed_zero"),
        funding_derived_as_residual=_required_boolean(
            funding_payload, "funding_derived_as_residual"
        ),
        funding_included_in_reported_net_pnl=_required_string(
            funding_payload, "funding_included_in_reported_net_pnl"
        ),
        v2_financial_decomposition_eligible=_required_boolean(
            funding_payload, "v2_financial_decomposition_eligible"
        ),
    )
    _require_equal(funding.funding_status, FUNDING_STATUS, "funding_status_invalid")
    _require_equal(
        funding.funding_included_in_reported_net_pnl,
        FUNDING_REPORTED_STATUS,
        "funding_reported_net_pnl_status_invalid",
    )
    _require_false(
        funding.funding_required_for_legacy_contract,
        "funding_required_for_legacy_contract_must_be_false",
    )
    _require_false(funding.funding_assumed_zero, "funding_assumed_zero_must_be_false")
    _require_false(
        funding.funding_derived_as_residual,
        "funding_derived_as_residual_must_be_false",
    )
    _require_false(
        funding.v2_financial_decomposition_eligible,
        "v2_financial_decomposition_eligible_must_be_false",
    )

    identity_payload = _required_object(payload, "identity_policy")
    identity = IdentityPolicy(
        synthetic_order_id_present=_required_boolean(identity_payload, "synthetic_order_id_present"),
        synthetic_order_id_authoritative=_required_boolean(
            identity_payload, "synthetic_order_id_authoritative"
        ),
        synthetic_order_id_role=_required_string(identity_payload, "synthetic_order_id_role"),
        native_exchange_order_identity_available=_required_boolean(
            identity_payload, "native_exchange_order_identity_available"
        ),
        account_scope_required_for_legacy_contract=_required_boolean(
            identity_payload, "account_scope_required_for_legacy_contract"
        ),
        v2_primary_identity_eligible=_required_boolean(
            identity_payload, "v2_primary_identity_eligible"
        ),
    )
    _require_equal(
        identity.synthetic_order_id_role,
        SYNTHETIC_ORDER_ID_ROLE,
        "synthetic_order_id_role_invalid",
    )
    _require_false(
        identity.synthetic_order_id_authoritative,
        "synthetic_order_id_authoritative_must_be_false",
    )
    _require_false(
        identity.account_scope_required_for_legacy_contract,
        "account_scope_required_for_legacy_contract_must_be_false",
    )
    _require_false(
        identity.v2_primary_identity_eligible,
        "v2_primary_identity_eligible_must_be_false",
    )

    authority_payload = _required_object(payload, "authority")
    authority = AuthorityPolicy(
        operational_authority=_required_boolean(authority_payload, "operational_authority"),
        import_authorized=_required_boolean(authority_payload, "import_authorized"),
        write_authorized=_required_boolean(authority_payload, "write_authorized"),
        manual_authorization_required=_required_boolean(
            authority_payload, "manual_authorization_required"
        ),
        official_import_allowed=_required_boolean(authority_payload, "official_import_allowed"),
    )
    _require_false(authority.operational_authority, "operational_authority_must_be_false")
    _require_false(authority.import_authorized, "import_authorized_must_be_false")
    _require_false(authority.write_authorized, "write_authorized_must_be_false")
    if authority.manual_authorization_required is not True:
        _fail("manual_authorization_required_must_be_true")
    _require_false(authority.official_import_allowed, "official_import_allowed_must_be_false")

    if counts.ocr_input_rows - counts.excluded_exact_duplicates != counts.retained_candidate_rows:
        _fail("ocr_duplicate_retained_count_inconsistent")
    if counts.master_rows_before + counts.retained_candidate_rows != (
        counts.master_rows_after_authorized_append
    ):
        _fail("master_authorized_append_count_inconsistent")
    if counts.sidecar_auto_matched_rows + counts.sidecar_residual_equivalence_rows != (
        counts.master_rows_before
    ):
        _fail("sidecar_reconciliation_count_inconsistent")
    if counts.legacy_novel_candidate_rows != counts.retained_candidate_rows:
        _fail("legacy_novel_candidate_count_inconsistent")

    return LegacyContract(
        schema_version=schema_version,
        contract_id=contract_id,
        batch_id=batch_id,
        contract_mode=contract_mode,
        source_profile_path=_required_string(payload, "source_profile_path"),
        sources=sources,
        expected_counts=counts,
        expected_master_hashes=hashes,
        historical_master_schema=historical_schema,
        funding_policy=funding,
        identity_policy=identity,
        authority=authority,
    )


def _required_object(payload: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = payload.get(field)
    if not isinstance(value, dict):
        _fail(f"contract_object_required:{field}")
    return value


def _required_string(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        _fail(f"contract_string_required:{field}")
    return value.strip()


def _required_integer(payload: Mapping[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(f"contract_nonnegative_integer_required:{field}")
    return value


def _required_boolean(payload: Mapping[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        _fail(f"contract_boolean_required:{field}")
    return value


def _required_hash(payload: Mapping[str, Any], field: str) -> str:
    value = _required_string(payload, field)
    if SHA256_RE.fullmatch(value) is None:
        _fail(f"contract_sha256_invalid:{field}")
    return value.casefold()


def _required_string_tuple(payload: Mapping[str, Any], field: str) -> tuple[str, ...]:
    value = payload.get(field)
    if not isinstance(value, list) or not value:
        _fail(f"contract_string_array_required:{field}")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        _fail(f"contract_string_array_required:{field}")
    return tuple(item.strip() for item in value)


def _require_equal(actual: str, expected: str, code: str) -> None:
    if actual != expected:
        _fail(code)


def _require_false(value: bool, code: str) -> None:
    if value is not False:
        _fail(code)


def _fail(code: str) -> NoReturn:
    raise LegacyContractError(code)
