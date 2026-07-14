"""Deterministic financial identity contract for Trader Master staging rows."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Any, Literal


FINGERPRINT_SPEC_VERSION = "trader_master_fingerprint_spec_v2"
NORMALIZER_VERSION = "trader_master_staging_normalizer_v2"
CANONICAL_TRADE_ID_NAMESPACE = "smart_futuros.trader_master.trade"
NULL_TOKENS = frozenset({"", "<na>", "nan", "nat", "none", "null"})
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")

FieldKind = Literal["text", "decimal", "timestamp"]
Sha256Hasher = Callable[[bytes], str]


@dataclass(frozen=True)
class FieldRule:
    name: str
    kind: FieldKind
    required: bool = True
    casefold: bool = False
    quantum: str | None = None


FIELD_RULES: tuple[FieldRule, ...] = (
    FieldRule("venue", "text", casefold=True),
    FieldRule("market_type", "text", casefold=True),
    FieldRule("contract_type", "text", casefold=True),
    FieldRule("settlement_currency", "text", casefold=True),
    FieldRule("quantity_unit", "text", casefold=True),
    FieldRule("contract_size", "decimal", quantum="0.00000001"),
    FieldRule("account_scope_hash", "text", casefold=True),
    FieldRule("order_id_namespace", "text", required=False, casefold=True),
    FieldRule("source_trade_id", "text", required=False),
    FieldRule("order_id", "text", required=False),
    FieldRule("source", "text", casefold=True),
    FieldRule("symbol", "text", casefold=True),
    FieldRule("side", "text", casefold=True),
    FieldRule("open_time", "timestamp"),
    FieldRule("close_time", "timestamp"),
    FieldRule("entry_price", "decimal", quantum="0.00000001"),
    FieldRule("exit_price", "decimal", quantum="0.00000001"),
    FieldRule("quantity", "decimal", quantum="0.00000001"),
    FieldRule("gross_pnl", "decimal", quantum="0.00000001"),
    FieldRule("trading_fee", "decimal", quantum="0.00000001"),
    FieldRule("funding_fee", "decimal", quantum="0.00000001"),
    FieldRule("net_pnl", "decimal", quantum="0.00000001"),
    FieldRule("epsilon_abs_fonte", "decimal", required=False, quantum="0.00000001"),
)

FINGERPRINT_FIELD_ORDER = tuple(rule.name for rule in FIELD_RULES)
CASEFOLDED_FIELDS = tuple(rule.name for rule in FIELD_RULES if rule.casefold)
DECIMAL_QUANTIZATION = {
    rule.name: rule.quantum for rule in FIELD_RULES if rule.kind == "decimal"
}


class FingerprintValidationError(ValueError):
    """Raised when a row cannot satisfy fingerprint_spec_v2."""


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def is_null(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float):
        return math.isnan(value)
    try:
        text = str(value).strip().casefold()
    except Exception:
        return False
    return text in NULL_TOKENS


def normalize_text(value: object, *, casefold: bool) -> str | None:
    if is_null(value):
        return None
    normalized = " ".join(str(value).strip().split())
    return normalized.casefold() if casefold else normalized


def decimal_from_value(value: object) -> Decimal:
    if isinstance(value, bool):
        raise FingerprintValidationError("boolean_is_not_decimal")
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise FingerprintValidationError("non_finite_decimal")
        number = Decimal(str(value))
    else:
        text = str(value).strip().replace("_", "")
        if "," in text and "." not in text:
            text = text.replace(",", ".")
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise FingerprintValidationError("invalid_decimal") from exc
    if not number.is_finite():
        raise FingerprintValidationError("non_finite_decimal")
    return number


def normalize_decimal(value: object, quantum: str) -> str | None:
    if is_null(value):
        return None
    quantized = decimal_from_value(value).quantize(Decimal(quantum), rounding=ROUND_HALF_EVEN)
    if quantized == 0:
        quantized = abs(quantized)
    return format(quantized, "f")


def datetime_from_value(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif hasattr(value, "to_pydatetime"):
        parsed = value.to_pydatetime()
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise FingerprintValidationError("invalid_timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def normalize_timestamp(value: object) -> str | None:
    if is_null(value):
        return None
    parsed = datetime_from_value(value)
    rendered = parsed.isoformat(timespec="microseconds")
    return rendered.replace("+00:00", "Z")


def normalize_trade_row(row: Mapping[str, Any]) -> dict[str, str | None]:
    """Normalize one canonical row without deriving native identifiers."""

    normalized: dict[str, str | None] = {}
    errors: list[str] = []
    for rule in FIELD_RULES:
        value = row.get(rule.name)
        try:
            if rule.kind == "text":
                result = normalize_text(value, casefold=rule.casefold)
            elif rule.kind == "timestamp":
                result = normalize_timestamp(value)
            else:
                assert rule.quantum is not None
                result = normalize_decimal(value, rule.quantum)
        except FingerprintValidationError as exc:
            errors.append(f"invalid_{rule.name}:{exc}")
            result = None
        if rule.required and result is None:
            errors.append(f"missing_required_identity_field:{rule.name}")
        normalized[rule.name] = result

    if normalized["side"] in {"buy", "comprado"}:
        normalized["side"] = "long"
    elif normalized["side"] in {"sell", "vendido"}:
        normalized["side"] = "short"
    if normalized["side"] not in {"long", "short"}:
        errors.append("invalid_side")

    account_scope_hash = normalized["account_scope_hash"]
    if account_scope_hash is not None and HEX_SHA256.fullmatch(account_scope_hash) is None:
        errors.append("invalid_account_scope_hash")

    if (normalized["order_id"] or normalized["source_trade_id"]) and not normalized[
        "order_id_namespace"
    ]:
        errors.append("missing_order_id_namespace_for_native_identity")

    for identifier in ("order_id", "source_trade_id"):
        generated_markers = (
            f"{identifier}_generated",
            f"{identifier}_is_synthetic",
            f"invented_{identifier}",
        )
        if normalized[identifier] and any(_truthy(row.get(marker)) for marker in generated_markers):
            errors.append(f"invented_native_identifier_forbidden:{identifier}")

    if normalized["open_time"] and normalized["close_time"]:
        if datetime_from_value(normalized["close_time"]) < datetime_from_value(
            normalized["open_time"]
        ):
            errors.append("close_time_before_open_time")

    if errors:
        raise FingerprintValidationError(";".join(sorted(set(errors))))
    return normalized


def canonical_payload(normalized_row: Mapping[str, str | None]) -> dict[str, Any]:
    payload: dict[str, Any] = {"fingerprint_spec_version": FINGERPRINT_SPEC_VERSION}
    for field in FINGERPRINT_FIELD_ORDER:
        payload[field] = normalized_row.get(field)
    return payload


def canonical_json(normalized_row: Mapping[str, str | None]) -> str:
    return json.dumps(
        canonical_payload(normalized_row),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def row_fingerprint_for(
    normalized_row: Mapping[str, str | None],
    *,
    hasher: Sha256Hasher = sha256_hex,
) -> str:
    digest = hasher(canonical_json(normalized_row).encode("utf-8"))
    if HEX_SHA256.fullmatch(digest.casefold()) is None:
        raise FingerprintValidationError("hasher_must_return_sha256_hex")
    return digest.casefold()


def primary_identity_for(normalized_row: Mapping[str, str | None]) -> dict[str, str] | None:
    native_id = normalized_row.get("source_trade_id") or normalized_row.get("order_id")
    if not native_id:
        return None
    return {
        "venue": _required(normalized_row, "venue"),
        "account_scope_hash": _required(normalized_row, "account_scope_hash"),
        "order_id_namespace": _required(normalized_row, "order_id_namespace"),
        "native_id_type": "source_trade_id"
        if normalized_row.get("source_trade_id")
        else "order_id",
        "native_id": native_id,
    }


def canonical_trade_id_for(
    normalized_row: Mapping[str, str | None],
    *,
    row_fingerprint: str,
) -> str:
    identity = primary_identity_for(normalized_row)
    material: dict[str, Any] = {
        "namespace": CANONICAL_TRADE_ID_NAMESPACE,
        "fingerprint_spec_version": FINGERPRINT_SPEC_VERSION,
        "source_namespace": normalized_row.get("source"),
        "identity_mode": "native" if identity is not None else "row_fingerprint_fallback",
        "identity": identity if identity is not None else {"row_fingerprint": row_fingerprint},
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"ctid:v2:{sha256_hex(encoded)}"


def _required(row: Mapping[str, str | None], field: str) -> str:
    value = row.get(field)
    if value is None:
        raise FingerprintValidationError(f"missing_primary_identity_field:{field}")
    return value


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().casefold() in {"1", "true", "yes", "sim"}
