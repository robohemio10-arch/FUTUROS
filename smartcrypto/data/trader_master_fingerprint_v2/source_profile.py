"""Versioned source-profile contract for paper closed-trade adapters."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SOURCE_PROFILE_SCHEMA_VERSION = "freqtrade_paper_closed_trades_source_profile_v2"
REQUIRED_COLUMN_KEYS = (
    "symbol",
    "side",
    "order_id",
    "open_time",
    "close_time",
    "entry_price",
    "exit_price",
    "quantity",
    "net_pnl",
    "fee_open",
    "fee_close",
    "leverage",
)
REQUIRED_SQLITE_COLUMNS = (
    "id",
    "exchange",
    "pair",
    "is_open",
    "is_short",
    "open_rate",
    "close_rate",
    "amount",
    "contract_size",
    "leverage",
    "fee_open_cost",
    "fee_close_cost",
    "fee_open_currency",
    "fee_close_currency",
    "funding_fees",
    "close_profit_abs",
    "realized_profit",
    "open_date",
    "close_date",
)


class SourceProfileError(ValueError):
    """Raised when a source profile is missing or semantically unsafe."""


@dataclass(frozen=True)
class FinancialContract:
    gross_pnl_formula: str
    fee_source_sign: str
    zero_fee_handling: str
    funding_availability: str
    funding_column: str | None
    funding_sign: str
    fee_open_normalization: str
    fee_close_normalization: str
    funding_normalization: str
    net_reference_column: str
    epsilon_abs_fonte: str
    pnl_semantics: str


@dataclass(frozen=True)
class AuthoritativeSQLiteContract:
    snapshot_path: str
    explicitly_non_authoritative_paths: tuple[str, ...]
    table: str
    closed_trade_filter: str
    join_key_semantics: str
    access_mode: str
    required_columns: tuple[str, ...]


@dataclass(frozen=True)
class FreqtradePaperSourceProfile:
    schema_version: str
    profile_id: str
    producer_module: str
    producer_function: str
    primary_source_path: str
    replica_source_paths: tuple[str, ...]
    venue: str
    market_type: str
    contract_type: str
    settlement_currency: str
    quantity_unit: str
    contract_size_source: str
    source_namespace: str
    order_id_namespace: str
    order_id_semantics: str
    column_map: dict[str, str]
    financial_contract: FinancialContract
    authoritative_sqlite: AuthoritativeSQLiteContract
    profile_path: Path
    profile_sha256: str


def load_source_profile(path: str | Path) -> FreqtradePaperSourceProfile:
    profile_path = Path(path).resolve()
    if not profile_path.exists() or not profile_path.is_file():
        raise SourceProfileError(f"source_profile_missing:{profile_path}")
    if profile_path.is_symlink():
        raise SourceProfileError(f"source_profile_symlink_forbidden:{profile_path}")
    if profile_path.suffix.casefold() != ".json":
        raise SourceProfileError("source_profile_must_be_json")
    try:
        raw = profile_path.read_bytes()
        payload = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceProfileError(f"source_profile_unreadable:{type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise SourceProfileError("source_profile_root_must_be_object")
    profile = _parse_profile(payload, profile_path, hashlib.sha256(raw).hexdigest())
    _validate_profile(profile)
    return profile


def _parse_profile(
    payload: dict[str, Any], profile_path: Path, profile_sha256: str
) -> FreqtradePaperSourceProfile:
    source_files = _mapping(payload, "source_files")
    identity = _mapping(payload, "identity")
    columns = _mapping(payload, "column_map")
    financial = _mapping(payload, "financial_contract")
    sqlite_contract = _mapping(payload, "authoritative_sqlite")
    replicas = source_files.get("replica_source_paths")
    if not isinstance(replicas, list) or not all(isinstance(item, str) for item in replicas):
        raise SourceProfileError("invalid_replica_source_paths")
    non_authoritative = sqlite_contract.get("explicitly_non_authoritative_paths")
    if not isinstance(non_authoritative, list) or not all(
        isinstance(item, str) and item.strip() for item in non_authoritative
    ):
        raise SourceProfileError("invalid_explicitly_non_authoritative_paths")
    required_sqlite_columns = sqlite_contract.get("required_columns")
    if not isinstance(required_sqlite_columns, list) or not all(
        isinstance(item, str) and item.strip() for item in required_sqlite_columns
    ):
        raise SourceProfileError("invalid_required_sqlite_columns")
    return FreqtradePaperSourceProfile(
        schema_version=_string(payload, "schema_version"),
        profile_id=_string(payload, "profile_id"),
        producer_module=_string(payload, "producer_module"),
        producer_function=_string(payload, "producer_function"),
        primary_source_path=_string(source_files, "primary_source_path"),
        replica_source_paths=tuple(replicas),
        venue=_string(identity, "venue"),
        market_type=_string(identity, "market_type"),
        contract_type=_string(identity, "contract_type"),
        settlement_currency=_string(identity, "settlement_currency"),
        quantity_unit=_string(identity, "quantity_unit"),
        contract_size_source=_string(identity, "contract_size_source"),
        source_namespace=_string(identity, "source_namespace"),
        order_id_namespace=_string(identity, "order_id_namespace"),
        order_id_semantics=_string(identity, "order_id_semantics"),
        column_map={str(key): str(value) for key, value in columns.items()},
        financial_contract=FinancialContract(
            gross_pnl_formula=_string(financial, "gross_pnl_formula"),
            fee_source_sign=_string(financial, "fee_source_sign"),
            zero_fee_handling=_string(financial, "zero_fee_handling"),
            funding_availability=_string(financial, "funding_availability"),
            funding_column=_optional_string(financial, "funding_column"),
            funding_sign=_string(financial, "funding_sign"),
            fee_open_normalization=_string(financial, "fee_open_normalization"),
            fee_close_normalization=_string(financial, "fee_close_normalization"),
            funding_normalization=_string(financial, "funding_normalization"),
            net_reference_column=_string(financial, "net_reference_column"),
            epsilon_abs_fonte=_string(financial, "epsilon_abs_fonte"),
            pnl_semantics=_string(financial, "pnl_semantics"),
        ),
        authoritative_sqlite=AuthoritativeSQLiteContract(
            snapshot_path=_string(sqlite_contract, "snapshot_path"),
            explicitly_non_authoritative_paths=tuple(
                item.strip() for item in non_authoritative
            ),
            table=_string(sqlite_contract, "table"),
            closed_trade_filter=_string(sqlite_contract, "closed_trade_filter"),
            join_key_semantics=_string(sqlite_contract, "join_key_semantics"),
            access_mode=_string(sqlite_contract, "access_mode"),
            required_columns=tuple(item.strip() for item in required_sqlite_columns),
        ),
        profile_path=profile_path,
        profile_sha256=profile_sha256,
    )


def _validate_profile(profile: FreqtradePaperSourceProfile) -> None:
    errors: list[str] = []
    if profile.schema_version != SOURCE_PROFILE_SCHEMA_VERSION:
        errors.append("unsupported_source_profile_schema_version")
    missing_columns = [key for key in REQUIRED_COLUMN_KEYS if not profile.column_map.get(key)]
    if missing_columns:
        errors.append(f"source_profile_missing_column_map:{','.join(missing_columns)}")
    if not profile.order_id_namespace.strip():
        errors.append("source_profile_order_id_namespace_missing")
    if profile.contract_size_source != "authoritative_sqlite.trades.contract_size":
        errors.append("unsupported_contract_size_source")
    if profile.financial_contract.gross_pnl_formula != "linear_price_delta_times_quantity_contract_size":
        errors.append("unsupported_gross_pnl_formula")
    if profile.financial_contract.fee_source_sign != "positive_cost":
        errors.append("unsupported_fee_source_sign")
    if profile.financial_contract.zero_fee_handling != "allow_authoritative_sqlite_zero":
        errors.append("unsafe_zero_fee_handling")
    if profile.financial_contract.funding_sign != "source_positive_revenue_negative_cost":
        errors.append("unsupported_funding_sign")
    if profile.financial_contract.funding_availability != "authoritative_sqlite_column":
        errors.append("invalid_funding_availability")
    if not profile.financial_contract.funding_column:
        errors.append("funding_column_required")
    elif profile.financial_contract.funding_column != "funding_fees":
        errors.append("unsupported_funding_column")
    expected_financial_contract = {
        "fee_open_normalization": "fee_open_cost_times_leverage",
        "fee_close_normalization": "fee_close_cost",
        "funding_normalization": "negate_source_value_to_cost",
        "net_reference_column": "close_profit_abs",
    }
    for field, expected in expected_financial_contract.items():
        if getattr(profile.financial_contract, field) != expected:
            errors.append(f"unsupported_{field}")
    sqlite_contract = profile.authoritative_sqlite
    if sqlite_contract.table != "trades":
        errors.append("unsupported_authoritative_sqlite_table")
    if sqlite_contract.closed_trade_filter != "is_open = 0":
        errors.append("unsupported_closed_trade_filter")
    if sqlite_contract.join_key_semantics != "freqtrade-paper-{trades.id}":
        errors.append("unsupported_sqlite_join_key_semantics")
    if sqlite_contract.access_mode != "temporary_copy_query_only":
        errors.append("unsafe_authoritative_sqlite_access_mode")
    missing_sqlite_columns = sorted(
        set(REQUIRED_SQLITE_COLUMNS) - set(sqlite_contract.required_columns)
    )
    if missing_sqlite_columns:
        errors.append(
            "source_profile_missing_sqlite_columns:" + ",".join(missing_sqlite_columns)
        )
    if errors:
        raise SourceProfileError(";".join(sorted(errors)))


def _mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise SourceProfileError(f"source_profile_mapping_required:{key}")
    return value


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SourceProfileError(f"source_profile_string_required:{key}")
    return value.strip()


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SourceProfileError(f"source_profile_optional_string_invalid:{key}")
    return value.strip()
