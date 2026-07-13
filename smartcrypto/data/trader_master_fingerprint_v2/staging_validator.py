"""Read-only, fail-closed validator for Trader Master staging rows."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .fingerprint_spec import (
    FINGERPRINT_SPEC_VERSION,
    NORMALIZER_VERSION,
    FingerprintValidationError,
    Sha256Hasher,
    canonical_json,
    canonical_trade_id_for,
    decimal_from_value,
    normalize_trade_row,
    primary_identity_for,
    row_fingerprint_for,
    sha256_hex,
)


CANONICAL_ALIASES: dict[str, tuple[str, ...]] = {
    "venue": ("venue", "exchange", "exchange_source"),
    "market_type": ("market_type", "market", "tipo_mercado"),
    "contract_type": ("contract_type", "tipo_contrato"),
    "settlement_currency": ("settlement_currency", "settle_currency", "moeda_liquidacao"),
    "quantity_unit": ("quantity_unit", "unidade_quantidade"),
    "contract_size": ("contract_size", "tamanho_contrato"),
    "account_scope_hash": ("account_scope_hash",),
    "order_id_namespace": ("order_id_namespace",),
    "source_trade_id": ("source_trade_id", "trade_id"),
    "order_id": ("order_id", "numero_pedido"),
    "source": ("source", "ocr_source", "candidate_source"),
    "symbol": ("symbol", "moeda", "pair"),
    "side": ("side", "fechar_side", "direction"),
    "open_time": ("open_time", "horario_abertura", "open_time_utc"),
    "close_time": ("close_time", "horario_fechamento", "close_time_utc"),
    "entry_price": ("entry_price", "preco_abertura", "open_rate"),
    "exit_price": ("exit_price", "preco_fechamento", "close_rate"),
    "quantity": ("quantity", "qty", "amount", "volume_fechado", "volume_posicao"),
    "gross_pnl": ("gross_pnl", "gross_pnl_usdt"),
    "trading_fee": ("trading_fee", "trading_fee_usdt", "fee"),
    "funding_fee": ("funding_fee", "funding_fee_usdt"),
    "net_pnl": ("net_pnl", "net_pnl_usdt", "pnl_fechado"),
    "epsilon_abs_fonte": ("epsilon_abs_fonte", "source_absolute_epsilon"),
}

INVENTED_IDENTIFIER_MARKERS = (
    "order_id_generated",
    "order_id_is_synthetic",
    "invented_order_id",
    "source_trade_id_generated",
    "source_trade_id_is_synthetic",
    "invented_source_trade_id",
)

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "paper_order_simulation_enabled": False,
    "real_order_submission_enabled": False,
    "sends_exchange_orders": False,
    "exchange_private_access": False,
    "research_pipeline_writes_runtime": False,
    "writes_active_model_runtime": False,
    "writes_operational_sqlite_outside_freqtrade": False,
    "changes_risk": False,
    "model_promotion_performed": False,
    "active_model_changed": False,
    "write_to_master_performed": False,
}


@dataclass
class KillSwitchMonitor:
    path: Path
    interval_seconds: float = 60.0
    clock: Callable[[], float] = time.monotonic
    _last_check: float | None = None
    _last_detected: bool = False

    def check(self, *, force: bool = False) -> bool:
        now = self.clock()
        if force or self._last_check is None or now - self._last_check >= self.interval_seconds:
            self._last_check = now
            self._last_detected = self.path.exists()
        return self._last_detected


@dataclass
class _Candidate:
    source_row_index: int
    canonical_row: dict[str, Any]
    normalized_row: dict[str, str | None] | None
    canonical_serialization: str | None
    row_fingerprint: str | None
    canonical_trade_id: str | None
    primary_identity: dict[str, str] | None
    accounting_tolerance: str | None
    accounting_delta: str | None
    status: str
    reasons: list[str]

    def report_row(self, *, lineage: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "source_row_index": self.source_row_index,
            "status": self.status,
            "reasons": sorted(set(self.reasons)),
            "canonical_trade_id": self.canonical_trade_id,
            "row_fingerprint": self.row_fingerprint,
            "primary_identity": self.primary_identity,
            "identity_mode": "native" if self.primary_identity else "row_fingerprint_fallback",
            "accounting_delta": self.accounting_delta,
            "accounting_tolerance": self.accounting_tolerance,
            **lineage,
        }


def validate_staging_records(
    records: Sequence[Mapping[str, Any]],
    *,
    source_file: str,
    source_sha256: str,
    ingestion_run_id: str,
    batch_size: int = 1_000,
    kill_switch: KillSwitchMonitor | None = None,
    hasher: Sha256Hasher = sha256_hex,
    batch_hook: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    """Validate staging rows without writing or promoting any record."""

    if batch_size < 1:
        raise ValueError("batch_size_must_be_positive")
    if kill_switch is not None and kill_switch.check(force=True):
        return _aborted_report(len(records), "kill_switch_active_at_boot")

    candidates: list[_Candidate] = []
    for batch_start in range(0, len(records), batch_size):
        if batch_start and kill_switch is not None and kill_switch.check():
            return _aborted_report(
                len(records),
                "kill_switch_activated_during_processing",
                processed_rows=len(candidates),
            )
        batch = records[batch_start : batch_start + batch_size]
        for offset, source_row in enumerate(batch):
            source_row_index = batch_start + offset
            candidates.append(
                _validate_one(
                    source_row,
                    source_row_index=source_row_index,
                    hasher=hasher,
                )
            )
        if batch_hook is not None:
            batch_hook(batch_start // batch_size)

    if len(records) and kill_switch is not None and kill_switch.check():
        return _aborted_report(
            len(records),
            "kill_switch_activated_during_processing",
            processed_rows=len(candidates),
        )

    collision_count = _classify_fingerprint_collisions(candidates)
    duplicate_canonical_count = _classify_canonical_duplicates(candidates)
    duplicate_fingerprint_count = _classify_exact_fingerprint_duplicates(candidates)

    lineage = {
        "source_file": source_file,
        "source_sha256": source_sha256,
        "ingestion_run_id": ingestion_run_id,
        "normalizer_version": NORMALIZER_VERSION,
        "fingerprint_spec_version": FINGERPRINT_SPEC_VERSION,
    }
    row_results = [candidate.report_row(lineage=lineage) for candidate in candidates]
    row_results.sort(key=lambda item: int(item["source_row_index"]))

    accepted_rows = sum(item["status"] == "accepted" for item in row_results)
    duplicate_rows = sum(item["status"] == "duplicate" for item in row_results)
    quarantined_rows = sum(item["status"] == "quarantined" for item in row_results)
    canonical_covered = sum(bool(item["canonical_trade_id"]) for item in row_results)
    coverage = round(100.0 * canonical_covered / len(records), 10) if records else 0.0
    validation_errors = sorted(
        {
            reason
            for item in row_results
            if item["status"] == "quarantined"
            for reason in item["reasons"]
        }
    )
    blockers: list[str] = []
    if not records:
        blockers.append("empty_staging_source")
    if collision_count:
        blockers.append("observed_fingerprint_collision")
    if quarantined_rows:
        blockers.append("staging_rows_quarantined")
    if coverage != 100.0:
        blockers.append("canonical_trade_id_coverage_below_100_percent")

    status = "blocked" if blockers else "ok"
    reason = blockers[0] if blockers else "ok_with_duplicates" if duplicate_rows else "ok"
    gates = {
        "fingerprint_deterministic": True,
        "observed_fingerprint_collision_count": collision_count,
        "canonical_trade_id_coverage": coverage,
        "canonical_trade_id_coverage_display": f"{coverage:g}%",
        "accepted_rows_accounting_identity_violations": 0,
        "quarantined_rows_promoted_to_master": 0,
        "write_to_master_performed": False,
        "sends_exchange_orders": False,
        "exchange_private_access": False,
    }
    return {
        "status": status,
        "reason": reason,
        "partial_artifact_status": "complete",
        "raw_row_count": len(records),
        "processed_row_count": len(candidates),
        "accepted_row_count": accepted_rows,
        "quarantined_row_count": quarantined_rows,
        "staging_duplicate_count": duplicate_rows,
        "duplicate_canonical_trade_id_count": duplicate_canonical_count,
        "duplicate_fingerprint_count": duplicate_fingerprint_count,
        "observed_fingerprint_collision_count": collision_count,
        "canonical_trade_id_coverage": coverage,
        "fingerprint_deterministic": True,
        "accepted_rows_accounting_identity_violations": 0,
        "quarantined_rows_promoted_to_master": 0,
        "validation_errors": validation_errors,
        "blockers": blockers,
        "warnings": ["exact_staging_duplicates_excluded"] if duplicate_rows else [],
        "gates": gates,
        "row_results": row_results,
        **SAFETY_FLAGS,
        "safety_flags": dict(SAFETY_FLAGS),
    }


def canonicalize_source_row(row: Mapping[str, Any]) -> dict[str, Any]:
    normalized_names = {_normalize_column_name(key): key for key in row}
    canonical: dict[str, Any] = {}
    for target, aliases in CANONICAL_ALIASES.items():
        canonical[target] = None
        for alias in aliases:
            original = normalized_names.get(_normalize_column_name(alias))
            if original is not None:
                canonical[target] = row.get(original)
                break
    for marker in INVENTED_IDENTIFIER_MARKERS:
        original = normalized_names.get(marker)
        if original is not None:
            canonical[marker] = row.get(original)
    return canonical


def _validate_one(
    row: Mapping[str, Any],
    *,
    source_row_index: int,
    hasher: Sha256Hasher,
) -> _Candidate:
    canonical = canonicalize_source_row(row)
    try:
        normalized = normalize_trade_row(canonical)
        financial_errors, tolerance, delta = _validate_financial_accounting(normalized)
        if financial_errors:
            return _Candidate(
                source_row_index,
                canonical,
                normalized,
                canonical_json(normalized),
                None,
                None,
                None,
                tolerance,
                delta,
                "quarantined",
                financial_errors,
            )
        fingerprint = row_fingerprint_for(normalized, hasher=hasher)
        canonical_id = canonical_trade_id_for(normalized, row_fingerprint=fingerprint)
        return _Candidate(
            source_row_index,
            canonical,
            normalized,
            canonical_json(normalized),
            fingerprint,
            canonical_id,
            primary_identity_for(normalized),
            tolerance,
            delta,
            "accepted",
            [],
        )
    except FingerprintValidationError as exc:
        return _Candidate(
            source_row_index,
            canonical,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "quarantined",
            str(exc).split(";"),
        )


def _validate_financial_accounting(
    row: Mapping[str, str | None],
) -> tuple[list[str], str, str]:
    gross = decimal_from_value(_required(row, "gross_pnl"))
    trading_fee = decimal_from_value(_required(row, "trading_fee"))
    funding_fee = decimal_from_value(_required(row, "funding_fee"))
    net = decimal_from_value(_required(row, "net_pnl"))
    source_epsilon = decimal_from_value(row.get("epsilon_abs_fonte") or "0")
    quantity = decimal_from_value(_required(row, "quantity"))
    contract_size = decimal_from_value(_required(row, "contract_size"))
    entry_price = decimal_from_value(_required(row, "entry_price"))
    exit_price = decimal_from_value(_required(row, "exit_price"))

    errors: list[str] = []
    if trading_fee < 0:
        errors.append("trading_fee_negative")
    if source_epsilon < 0:
        errors.append("epsilon_abs_fonte_negative")
    if quantity <= 0:
        errors.append("quantity_not_positive")
    if contract_size <= 0:
        errors.append("contract_size_not_positive")
    if entry_price <= 0 or exit_price <= 0:
        errors.append("trade_price_not_positive")
    expected_net = gross - trading_fee - funding_fee
    delta = abs(net - expected_net)
    tolerance = max(source_epsilon, Decimal("0.0005") * abs(gross))
    if delta > tolerance:
        errors.append("financial_accounting_identity_violation")
    return sorted(set(errors)), format(tolerance, "f"), format(delta, "f")


def _classify_fingerprint_collisions(candidates: list[_Candidate]) -> int:
    groups: dict[str, list[_Candidate]] = defaultdict(list)
    for candidate in candidates:
        if candidate.row_fingerprint:
            groups[candidate.row_fingerprint].append(candidate)
    collision_count = 0
    for group in groups.values():
        serializations = {candidate.canonical_serialization for candidate in group}
        if len(serializations) <= 1:
            continue
        collision_count += 1
        for candidate in group:
            candidate.status = "quarantined"
            candidate.reasons.append("row_fingerprint_sha256_collision")
    return collision_count


def _classify_canonical_duplicates(candidates: list[_Candidate]) -> int:
    groups: dict[str, list[_Candidate]] = defaultdict(list)
    for candidate in candidates:
        if candidate.canonical_trade_id and candidate.status != "quarantined":
            groups[candidate.canonical_trade_id].append(candidate)
    duplicate_count = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        duplicate_count += len(group) - 1
        fingerprints = {candidate.row_fingerprint for candidate in group}
        if len(fingerprints) > 1:
            for candidate in group:
                candidate.status = "quarantined"
                candidate.reasons.append("canonical_trade_id_identity_conflict")
            continue
        for candidate in group[1:]:
            candidate.status = "duplicate"
            candidate.reasons.append("duplicate_canonical_trade_id")
    return duplicate_count


def _classify_exact_fingerprint_duplicates(candidates: list[_Candidate]) -> int:
    groups: dict[str, list[_Candidate]] = defaultdict(list)
    for candidate in candidates:
        if candidate.row_fingerprint and candidate.status != "quarantined":
            groups[candidate.row_fingerprint].append(candidate)
    duplicate_count = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        duplicate_count += len(group) - 1
        for candidate in group[1:]:
            if candidate.status == "accepted":
                candidate.status = "duplicate"
            if "duplicate_row_fingerprint" not in candidate.reasons:
                candidate.reasons.append("duplicate_row_fingerprint")
    return duplicate_count


def _aborted_report(raw_rows: int, reason: str, processed_rows: int = 0) -> dict[str, Any]:
    gates = {
        "fingerprint_deterministic": True,
        "observed_fingerprint_collision_count": 0,
        "canonical_trade_id_coverage": 0.0,
        "canonical_trade_id_coverage_display": "0%",
        "accepted_rows_accounting_identity_violations": 0,
        "quarantined_rows_promoted_to_master": 0,
        "write_to_master_performed": False,
        "sends_exchange_orders": False,
        "exchange_private_access": False,
    }
    return {
        "status": "blocked",
        "reason": reason,
        "partial_artifact_status": "aborted",
        "raw_row_count": raw_rows,
        "processed_row_count": processed_rows,
        "accepted_row_count": 0,
        "quarantined_row_count": 0,
        "staging_duplicate_count": 0,
        "duplicate_canonical_trade_id_count": 0,
        "duplicate_fingerprint_count": 0,
        "observed_fingerprint_collision_count": 0,
        "canonical_trade_id_coverage": 0.0,
        "fingerprint_deterministic": True,
        "accepted_rows_accounting_identity_violations": 0,
        "quarantined_rows_promoted_to_master": 0,
        "validation_errors": [reason],
        "blockers": [reason],
        "warnings": [],
        "gates": gates,
        "row_results": [],
        **SAFETY_FLAGS,
        "safety_flags": dict(SAFETY_FLAGS),
    }


def _required(row: Mapping[str, str | None], field: str) -> str:
    value = row.get(field)
    if value is None:
        raise FingerprintValidationError(f"missing_financial_field:{field}")
    return value


def _normalize_column_name(value: object) -> str:
    return str(value).strip().casefold().replace(" ", "_").replace("-", "_")
