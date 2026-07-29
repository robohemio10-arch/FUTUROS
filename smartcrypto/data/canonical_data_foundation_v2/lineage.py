"""Terminal financial lineage for immutable Trader Master rows."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from smartcrypto.data.trader_master_fingerprint_v2.master_adapter import (
    MasterReadBundle,
    read_trader_master_readonly,
)

from .contracts import (
    FieldEvidence,
    FieldVerificationStatus,
    TerminalLineageStatus,
    canonical_json,
    json_safe,
    stable_hash,
)

LINEAGE_SCHEMA_VERSION = "trader_master_financial_lineage_v2"
MANDATORY_FINANCIAL_FIELDS = (
    "venue",
    "market_type",
    "account_scope",
    "order_id_namespace",
    "trade_id",
    "entry_order_id",
    "exit_order_id",
    "entry_price",
    "exit_price",
    "quantity",
    "contract_size",
    "gross_pnl",
    "trading_fee",
    "funding_fee",
    "net_pnl",
    "margin_mode",
    "leverage",
    "open_time_utc",
    "close_time_utc",
)
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "venue": ("venue", "exchange_source", "exchange"),
    "market_type": ("market_type",),
    "account_scope": ("account_scope_hash",),
    "order_id_namespace": ("order_id_namespace",),
    "trade_id": ("source_trade_id", "trade_id"),
    "entry_order_id": ("entry_order_id",),
    "exit_order_id": ("exit_order_id",),
    "entry_price": ("entry_price", "preco_abertura"),
    "exit_price": ("exit_price", "preco_fechamento"),
    "quantity": ("quantity", "volume_fechado"),
    "contract_size": ("contract_size",),
    "gross_pnl": ("gross_pnl",),
    "trading_fee": ("trading_fee",),
    "funding_fee": ("funding_fee",),
    "net_pnl": ("net_pnl", "pnl_fechado"),
    "margin_mode": ("margin_mode",),
    "leverage": ("leverage",),
    "open_time_utc": ("open_time_utc", "open_time", "horario_abertura"),
    "close_time_utc": ("close_time_utc", "close_time", "horario_fechamento"),
}
DEFAULT_SECONDARY_EVIDENCE_PATHS = (
    "config/trader_master_legacy_research_only_policy_v1.json",
    "config/bitradex_ocr_locked_candidates_source_profile_v2.json",
)

BundleReader = Callable[..., MasterReadBundle]


@dataclass(frozen=True)
class LineageRecord:
    source_record_reference: str
    source_row_index: int
    source_row_hash: str
    source_attempt_ids: tuple[str, ...]
    fields: Mapping[str, FieldEvidence]
    verification_status: TerminalLineageStatus
    terminal_reason_codes: tuple[str, ...]
    financial_reconciliation_residual: str | None
    original_row_immutable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_record_reference": self.source_record_reference,
            "source_row_index": self.source_row_index,
            "source_row_hash": self.source_row_hash,
            "source_attempt_ids": list(self.source_attempt_ids),
            "fields": {name: evidence.to_dict() for name, evidence in self.fields.items()},
            "verification_status": self.verification_status,
            "terminal_reason_codes": list(self.terminal_reason_codes),
            "financial_reconciliation_residual": self.financial_reconciliation_residual,
            "original_row_immutable": self.original_row_immutable,
        }


@dataclass(frozen=True)
class LineageResult:
    report: Mapping[str, Any]
    records: tuple[LineageRecord, ...]


def build_trader_master_lineage(
    *,
    project_root: str | Path,
    trader_master_path: str | Path = "data/trades/trades_master.parquet",
    secondary_evidence_paths: Sequence[str | Path] = DEFAULT_SECONDARY_EVIDENCE_PATHS,
    bundle_reader: BundleReader = read_trader_master_readonly,
) -> LineageResult:
    """Close every source row as VERIFIED or PERMANENT_QUARANTINE."""

    root = Path(project_root).resolve()
    bundle = bundle_reader(
        project_root=root,
        trader_master_path=trader_master_path,
    )
    source_hash = str(bundle.report.get("trader_master_sha256_before") or "")
    evidence_inventory = _build_evidence_inventory(
        root=root,
        master_report=bundle.report,
        secondary_paths=secondary_evidence_paths,
    )
    attempt_ids = tuple(item["attempt_id"] for item in evidence_inventory)
    base = {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "trader_master_path": bundle.report.get("trader_master_path"),
        "trader_master_sha256": source_hash or None,
        "trader_master_hash_preserved": bool(
            bundle.report.get("trader_master_hash_preserved")
        ),
        "source_evidence_inventory": evidence_inventory,
        "source_attempts_exhausted": True,
        "evidence_boundary": "registered_primary_and_secondary_sources_v2",
        "legacy_master_immutable": True,
        "legacy_master_research_only": True,
        "operational_authority": False,
        "write_performed": False,
        "writes_trader_master": False,
    }
    if bundle.report.get("status") != "ok":
        return LineageResult(
            report={
                **base,
                "status": "blocked",
                "reason": str(bundle.report.get("reason") or "trader_master_read_failed"),
                "total_rows": 0,
                "verified_rows": 0,
                "quarantined_rows": 0,
                "unresolved_rows": 0,
                "source_conflict_rows": 0,
            },
            records=(),
        )

    canonical_by_index = {record.row_index: record for record in bundle.canonical_records}
    unverifiable_by_index = {
        int(record["row_index"]): record for record in bundle.unverifiable_rows
    }
    duplicate_context = _identity_conflicts(bundle.source_rows)
    occurrence_counter: Counter[str] = Counter()
    records: list[LineageRecord] = []

    for row_index, original in enumerate(bundle.source_rows):
        normalized_original = json_safe(dict(original))
        original_before = canonical_json(normalized_original)
        source_row_hash = stable_hash(
            {
                "schema_version": LINEAGE_SCHEMA_VERSION,
                "source_hash": source_hash,
                "row": normalized_original,
            }
        )
        occurrence = occurrence_counter[source_row_hash]
        occurrence_counter[source_row_hash] += 1
        source_reference = stable_hash(
            {
                "schema_version": LINEAGE_SCHEMA_VERSION,
                "source_hash": source_hash,
                "source_row_hash": source_row_hash,
                "duplicate_occurrence": occurrence,
            }
        )
        canonical = canonical_by_index.get(row_index)
        unverifiable = unverifiable_by_index.get(row_index)
        fields = _field_evidence(
            original=original,
            source_reference=source_reference,
            source_hash=source_hash or None,
            canonical_normalized=(canonical.normalized if canonical is not None else None),
        )
        reasons = set(
            str(reason)
            for reason in (unverifiable or {}).get("reasons", ())
            if str(reason)
        )
        reasons.update(duplicate_context.get(row_index, ()))
        residual, reconciliation_reason = _financial_reconciliation(fields)
        if reconciliation_reason is not None:
            reasons.add(reconciliation_reason)

        verified = (
            canonical is not None
            and not reasons
            and all(
                fields[name].verification_status == "VERIFIED"
                for name in MANDATORY_FINANCIAL_FIELDS
            )
        )
        terminal_status: TerminalLineageStatus = (
            "VERIFIED" if verified else "PERMANENT_QUARANTINE"
        )
        if not verified:
            reasons.add("authoritative_lineage_not_fully_resolved")
            reasons.add("registered_primary_and_secondary_sources_exhausted")
            fields = _terminalize_fields(fields)
        original_after = canonical_json(json_safe(dict(original)))
        records.append(
            LineageRecord(
                source_record_reference=source_reference,
                source_row_index=row_index,
                source_row_hash=source_row_hash,
                source_attempt_ids=attempt_ids,
                fields=fields,
                verification_status=terminal_status,
                terminal_reason_codes=tuple(sorted(reasons)),
                financial_reconciliation_residual=residual,
                original_row_immutable=original_before == original_after,
            )
        )

    report = _summarize_lineage(records, bundle=bundle, base=base)
    return LineageResult(report=report, records=tuple(records))


def _field_evidence(
    *,
    original: Mapping[str, Any],
    source_reference: str,
    source_hash: str | None,
    canonical_normalized: Mapping[str, Any] | None,
) -> dict[str, FieldEvidence]:
    fields: dict[str, FieldEvidence] = {}
    for field_name in MANDATORY_FINANCIAL_FIELDS:
        aliases = FIELD_ALIASES[field_name]
        value, source_column = _first_present(original, aliases)
        normalized_value = (
            canonical_normalized.get(_canonical_field_name(field_name))
            if canonical_normalized is not None
            else None
        )
        if normalized_value is not None:
            value = normalized_value
        status: FieldVerificationStatus
        if canonical_normalized is not None and value is not None:
            status = "VERIFIED"
            reason = None
            confidence = "AUTHORITATIVE_CANONICAL"
        elif value is None:
            status = "SOURCE_MISSING"
            reason = f"missing_authoritative_field:{field_name}"
            confidence = "MISSING"
        else:
            status = "SOURCE_MISSING"
            reason = f"legacy_master_value_not_authoritative:{source_column}"
            confidence = "OBSERVED_UNVERIFIED"
        fields[field_name] = FieldEvidence(
            value=json_safe(value),
            source_type=(
                "canonical_master_adapter"
                if canonical_normalized is not None and value is not None
                else "immutable_legacy_master"
            ),
            source_reference=source_reference,
            source_hash=source_hash,
            confidence_class=confidence,
            verification_status=status,
            reason_code=reason,
        )
    return fields


def _financial_reconciliation(
    fields: Mapping[str, FieldEvidence],
) -> tuple[str | None, str | None]:
    names = ("gross_pnl", "trading_fee", "funding_fee", "net_pnl")
    if any(fields[name].verification_status != "VERIFIED" for name in names):
        return None, "financial_components_not_authoritatively_complete"
    try:
        gross, trading, funding, net = (
            Decimal(str(fields[name].value)) for name in names
        )
    except (InvalidOperation, TypeError, ValueError):
        return None, "financial_component_not_decimal"
    residual = gross - trading - funding - net
    tolerance = Decimal("0.00000001")
    if abs(residual) > tolerance:
        return format(residual, "f"), "financial_reconciliation_failed"
    return format(residual, "f"), None


def _terminalize_fields(
    fields: Mapping[str, FieldEvidence],
) -> dict[str, FieldEvidence]:
    terminal: dict[str, FieldEvidence] = {}
    for name, evidence in fields.items():
        if evidence.verification_status == "VERIFIED":
            terminal[name] = evidence
            continue
        terminal[name] = FieldEvidence(
            value=evidence.value,
            source_type=evidence.source_type,
            source_reference=evidence.source_reference,
            source_hash=evidence.source_hash,
            confidence_class=evidence.confidence_class,
            verification_status="PERMANENT_QUARANTINE",
            reason_code=evidence.reason_code,
        )
    return terminal


def _identity_conflicts(
    rows: Sequence[Mapping[str, Any]],
) -> dict[int, set[str]]:
    conflicts: dict[int, set[str]] = defaultdict(set)
    order_groups: dict[str, list[int]] = defaultdict(list)
    trade_groups: dict[str, list[int]] = defaultdict(list)
    namespace_venues: dict[tuple[str, str], set[str]] = defaultdict(set)
    namespace_indices: dict[tuple[str, str], list[int]] = defaultdict(list)

    for index, row in enumerate(rows):
        order_id = _clean(row.get("order_id"))
        trade_id = _clean(row.get("source_trade_id") or row.get("trade_id"))
        namespace = _clean(row.get("order_id_namespace"))
        venue = _clean(row.get("venue") or row.get("exchange_source") or row.get("exchange"))
        if order_id:
            order_groups[order_id].append(index)
        if trade_id:
            trade_groups[trade_id].append(index)
        if namespace and order_id:
            key = (namespace, order_id)
            namespace_venues[key].add(venue or "UNKNOWN")
            namespace_indices[key].append(index)
        conflicts[index].update(_row_contract_conflicts(row))

    for indices in order_groups.values():
        if len(indices) > 1:
            for index in indices:
                conflicts[index].add("duplicate_order_id")
    for indices in trade_groups.values():
        if len(indices) > 1:
            for index in indices:
                conflicts[index].add("duplicate_trade_id")
    for key, venues in namespace_venues.items():
        if len(venues) > 1:
            for index in namespace_indices[key]:
                conflicts[index].add("order_id_namespace_collision_across_venues")

    return conflicts


def _row_contract_conflicts(row: Mapping[str, Any]) -> set[str]:
    reasons: set[str] = set()
    opened = _explicit_datetime(
        row.get("open_time_utc") or row.get("open_time") or row.get("horario_abertura")
    )
    closed = _explicit_datetime(
        row.get("close_time_utc")
        or row.get("close_time")
        or row.get("horario_fechamento")
    )
    if _clean(
        row.get("open_time_utc") or row.get("open_time") or row.get("horario_abertura")
    ) and opened is None:
        reasons.add("open_time_timezone_missing_or_ambiguous")
    if _clean(
        row.get("close_time_utc")
        or row.get("close_time")
        or row.get("horario_fechamento")
    ) and closed is None:
        reasons.add("close_time_timezone_missing_or_ambiguous")
    if opened is not None and closed is not None and opened > closed:
        reasons.add("open_time_after_close_time")

    quantity = _decimal(
        row.get("quantity")
        if _clean(row.get("quantity")) is not None
        else row.get("volume_fechado")
    )
    if quantity is not None and quantity <= 0:
        reasons.add("quantity_incompatible")
    leverage = _decimal(row.get("leverage"))
    if leverage is not None and leverage <= 0:
        reasons.add("leverage_incompatible")

    side = _normalized_side(row.get("side") or row.get("fechar_side"))
    if _clean(row.get("side") or row.get("fechar_side")) and side is None:
        reasons.add("side_inconsistent")
    symbol = _normalized_symbol(row.get("symbol") or row.get("moeda"))
    if _clean(row.get("symbol") or row.get("moeda")) and symbol is None:
        reasons.add("symbol_incompatible")

    if _alias_values_conflict(row, ("quantity", "volume_fechado")):
        reasons.add("quantity_source_conflict")
    if _alias_values_conflict(row, ("trading_fee", "fee_total", "taxa_total")):
        reasons.add("trading_fee_source_conflict")
    if _alias_values_conflict(row, ("funding_fee", "funding_fees")):
        reasons.add("funding_fee_source_conflict")
    if _alias_values_conflict(row, ("net_pnl", "pnl_fechado", "close_profit_abs")):
        reasons.add("net_pnl_source_conflict")

    entry_order = _clean(row.get("entry_order_id"))
    exit_order = _clean(row.get("exit_order_id"))
    if entry_order and exit_order and entry_order == exit_order:
        reasons.add("entry_exit_order_id_collision")
    return reasons


def _summarize_lineage(
    records: Sequence[LineageRecord],
    *,
    bundle: MasterReadBundle,
    base: Mapping[str, Any],
) -> dict[str, Any]:
    statuses = Counter(record.verification_status for record in records)
    reasons: Counter[str] = Counter()
    by_symbol: dict[str, Counter[str]] = defaultdict(Counter)
    by_side: dict[str, Counter[str]] = defaultdict(Counter)
    by_source: dict[str, Counter[str]] = defaultdict(Counter)
    coverage: dict[str, dict[str, int]] = {}
    conflict_rows = 0
    reconciliation_passed = 0
    reconciliation_failed = 0

    for record, source_row in zip(records, bundle.source_rows):
        reasons.update(record.terminal_reason_codes)
        symbol = _clean(source_row.get("symbol") or source_row.get("moeda")) or "UNKNOWN"
        side = _clean(source_row.get("side") or source_row.get("fechar_side")) or "UNKNOWN"
        source = _clean(source_row.get("source_file") or source_row.get("source")) or "UNKNOWN"
        by_symbol[symbol][record.verification_status] += 1
        by_side[side][record.verification_status] += 1
        by_source[source][record.verification_status] += 1
        if any("conflict" in reason or "collision" in reason for reason in record.terminal_reason_codes):
            conflict_rows += 1
        if record.financial_reconciliation_residual is not None:
            if "financial_reconciliation_failed" in record.terminal_reason_codes:
                reconciliation_failed += 1
            else:
                reconciliation_passed += 1

    for name in MANDATORY_FINANCIAL_FIELDS:
        values = [record.fields[name] for record in records]
        coverage[name] = {
            "observed_count": sum(item.value is not None for item in values),
            "verified_count": sum(
                item.verification_status == "VERIFIED" for item in values
            ),
            "missing_or_blocked_count": sum(
                item.verification_status != "VERIFIED" for item in values
            ),
        }

    all_terminal = len(records) == statuses["VERIFIED"] + statuses["PERMANENT_QUARANTINE"]
    immutable = all(record.original_row_immutable for record in records)
    record_set_hash = stable_hash([record.to_dict() for record in records])
    return {
        **base,
        "status": "ok" if all_terminal and immutable else "blocked",
        "reason": (
            "all_master_rows_terminally_classified"
            if all_terminal and immutable
            else "lineage_terminal_classification_failed"
        ),
        "total_rows": len(records),
        "verified_rows": statuses["VERIFIED"],
        "quarantined_rows": statuses["PERMANENT_QUARANTINE"],
        "unresolved_rows": len(records)
        - statuses["VERIFIED"]
        - statuses["PERMANENT_QUARANTINE"],
        "source_conflict_rows": conflict_rows,
        "namespace_collision_rows": sum(
            "order_id_namespace_collision_across_venues"
            in record.terminal_reason_codes
            for record in records
        ),
        "financial_reconciliation_passed": reconciliation_passed,
        "financial_reconciliation_failed": reconciliation_failed,
        "field_coverage_by_name": coverage,
        "status_by_symbol": _counter_map(by_symbol),
        "status_by_side": _counter_map(by_side),
        "status_by_source": _counter_map(by_source),
        "quarantine_reason_counts": dict(sorted(reasons.items())),
        "record_set_hash": record_set_hash,
        "all_rows_terminal": all_terminal,
        "original_rows_immutable": immutable,
        "fabricated_identity_count": 0,
        "fabricated_financial_component_count": 0,
        "record_sample": [
            {
                "source_record_reference": record.source_record_reference,
                "verification_status": record.verification_status,
                "terminal_reason_codes": list(record.terminal_reason_codes),
                "financial_reconciliation_residual": (
                    record.financial_reconciliation_residual
                ),
            }
            for record in records[:3]
        ],
    }


def _build_evidence_inventory(
    *,
    root: Path,
    master_report: Mapping[str, Any],
    secondary_paths: Sequence[str | Path],
) -> list[dict[str, Any]]:
    inventory = [
        {
            "attempt_id": "primary_immutable_trader_master",
            "source_type": "primary",
            "source_reference": master_report.get("trader_master_path"),
            "source_hash": master_report.get("trader_master_sha256_before"),
            "status": (
                "inspected"
                if master_report.get("trader_master_hash_preserved")
                else "blocked"
            ),
            "authoritative_for_missing_financial_components": False,
        }
    ]
    for index, value in enumerate(secondary_paths, start=1):
        requested = Path(value)
        path = requested if requested.is_absolute() else root / requested
        safe = _safe_existing_file(path, root)
        source_hash = _file_sha256(path) if safe else None
        inventory.append(
            {
                "attempt_id": f"secondary_registered_evidence_{index}",
                "source_type": "secondary",
                "source_reference": _display(path, root),
                "source_hash": source_hash,
                "status": "inspected" if safe else "missing_or_unsafe",
                "authoritative_for_missing_financial_components": False,
            }
        )
    return inventory


def _first_present(
    row: Mapping[str, Any],
    aliases: Sequence[str],
) -> tuple[Any, str | None]:
    for alias in aliases:
        if alias not in row:
            continue
        value = row.get(alias)
        if _clean(value) is not None:
            return value, alias
    return None, None


def _canonical_field_name(name: str) -> str:
    return {
        "account_scope": "account_scope_hash",
        "trade_id": "source_trade_id",
        "open_time_utc": "open_time",
        "close_time_utc": "close_time",
    }.get(name, name)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none", "<na>"}:
        return None
    return text


def _explicit_datetime(value: Any) -> datetime | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _decimal(value: Any) -> Decimal | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _normalized_side(value: Any) -> str | None:
    text = (_clean(value) or "").lower()
    if "long" in text:
        return "long"
    if "short" in text:
        return "short"
    return None


def _normalized_symbol(value: Any) -> str | None:
    text = (_clean(value) or "").upper()
    normalized = text.replace("/", "").replace("_", "").replace(":USDT", "")
    if not normalized or not normalized.isalnum() or len(normalized) < 6:
        return None
    return normalized


def _alias_values_conflict(
    row: Mapping[str, Any],
    aliases: Sequence[str],
) -> bool:
    values = [
        parsed
        for alias in aliases
        if (parsed := _decimal(row.get(alias))) is not None
    ]
    if len(values) < 2:
        return False
    return max(values) - min(values) > Decimal("0.00000001")


def _counter_map(value: Mapping[str, Counter[str]]) -> dict[str, dict[str, int]]:
    return {
        key: dict(sorted(counter.items()))
        for key, counter in sorted(value.items())
    }


def _safe_existing_file(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        return resolved.is_file() and not path.is_symlink()
    except (FileNotFoundError, OSError, ValueError):
        return False


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display(path: Path, root: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root).as_posix()
    except ValueError:
        return path.resolve(strict=False).as_posix()
