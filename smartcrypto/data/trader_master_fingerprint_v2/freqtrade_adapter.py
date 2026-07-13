"""In-memory adapter for Phase14 Freqtrade paper closed-trade CSV replicas."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from smartcrypto.data.trades_importer import read_trade_file

from .fingerprint_spec import HEX_SHA256, FingerprintValidationError, decimal_from_value
from .source_profile import (
    FreqtradePaperSourceProfile,
    SourceProfileError,
    load_source_profile,
)
from .staging_runner import (
    DEFAULT_JSON_REPORT,
    DEFAULT_MARKDOWN_REPORT,
    maybe_write_validation_report,
    resolve_path,
    validate_report_output_paths,
)
from .staging_validator import SAFETY_FLAGS, validate_staging_records


ADAPTER_SCHEMA_VERSION = "freqtrade_paper_closed_trades_readonly_adapter_v2"


def build_freqtrade_paper_closed_trades_adapter_report(
    *,
    project_root: str | Path,
    source_profile_path: str | Path,
    account_scope_hash: str | None,
    staging_file: str | Path | None = None,
    write_report: bool = False,
    output_json: str | Path = DEFAULT_JSON_REPORT,
    output_markdown: str | Path = DEFAULT_MARKDOWN_REPORT,
    write_to_master_requested: bool = False,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    json_report = resolve_path(root, output_json)
    markdown_report = resolve_path(root, output_markdown)
    base = _base_report(
        source_profile_path=source_profile_path,
        account_scope_hash=account_scope_hash,
        write_report=write_report,
        json_report=json_report,
        markdown_report=markdown_report,
    )
    if write_to_master_requested:
        return _blocked(base, "write_to_master_forbidden")
    write_errors = validate_report_output_paths(root, json_report, markdown_report) if write_report else []
    if write_errors:
        return _blocked(base, "unsafe_report_output_path", write_errors)
    normalized_account_hash = (account_scope_hash or "").strip().casefold()
    if not normalized_account_hash:
        return _blocked(base, "account_scope_hash_missing")
    if HEX_SHA256.fullmatch(normalized_account_hash) is None:
        return _blocked(base, "account_scope_hash_invalid")
    try:
        profile = load_source_profile(resolve_path(root, source_profile_path))
    except SourceProfileError as exc:
        return _blocked(base, "source_profile_invalid", str(exc).split(";"))

    source = resolve_path(root, staging_file or profile.primary_source_path)
    replica_paths = [resolve_path(root, item) for item in profile.replica_source_paths]
    source_summary = profile_sources(source, replica_paths, root)
    report = {
        **base,
        "source_profile_id": profile.profile_id,
        "source_profile_schema_version": profile.schema_version,
        "source_profile_sha256": profile.profile_sha256,
        "producer_module": profile.producer_module,
        "producer_function": profile.producer_function,
        "source_file": display_path(source, root),
        **source_summary,
    }
    if source_summary["source_status"] != "ok":
        return _blocked(report, str(source_summary["source_reason"]))

    try:
        frame = read_trade_file(source)
    except Exception as exc:
        return _blocked(report, "source_unreadable", [f"source_unreadable:{type(exc).__name__}"])
    duplicate_columns = sorted(
        {str(column) for column in frame.columns[frame.columns.duplicated(keep=False)]}
    )
    if duplicate_columns:
        return _blocked(report, "duplicate_source_columns", [f"duplicate_source_columns:{duplicate_columns}"])

    records = frame.to_dict(orient="records")
    adapted = adapt_records(records, profile=profile, account_scope_hash=normalized_account_hash)
    validator = validate_staging_records(
        adapted["canonical_records"],
        source_file=display_path(source, root),
        source_sha256=str(source_summary["primary_source_sha256"]),
        ingestion_run_id=f"freqtrade-paper-v2-{str(source_summary['primary_source_sha256'])[:16]}",
    ) if adapted["canonical_records"] else _empty_validator_report()
    for item, original_index in zip(
        validator.get("row_results", []), adapted["candidate_source_row_indices"], strict=True
    ):
        item["source_row_index"] = original_index

    reason_counts = Counter(adapted["quarantine_reason_counts"])
    for item in validator.get("row_results", []):
        if item.get("status") == "quarantined":
            reason_counts.update(item.get("reasons", []))
    quarantined_count = int(adapted["adapter_quarantined_row_count"]) + int(
        validator.get("quarantined_row_count", 0)
    )
    accepted_count = int(validator.get("accepted_row_count", 0))
    status = "blocked" if quarantined_count or validator.get("status") != "ok" else "ok"
    reason = "rows_quarantined_by_source_contract" if quarantined_count else str(
        validator.get("reason", "ok")
    )
    combined = {
        **report,
        **validator,
        "status": status,
        "reason": reason,
        "raw_row_count": len(records),
        "adapter_candidate_row_count": len(adapted["canonical_records"]),
        "canonical_records_delivered_to_validator_count": len(adapted["canonical_records"]),
        "adapter_quarantined_row_count": int(adapted["adapter_quarantined_row_count"]),
        "accepted_row_count": accepted_count,
        "quarantined_row_count": quarantined_count,
        "quarantined_reason_counts": dict(sorted(reason_counts.items())),
        "adapter_row_results": adapted["adapter_row_results"],
        "account_scope_hash_present": True,
        "account_scope_hash_valid": True,
        "account_scope_original_identifier_persisted": False,
        "gross_pnl_reconstruction": profile.financial_contract.gross_pnl_formula,
        "gross_pnl_independent_from_net_pnl": True,
        "funding_availability": profile.financial_contract.funding_availability,
        "write_requested": bool(write_report),
        "write_performed": False,
        **SAFETY_FLAGS,
        "safety_flags": dict(SAFETY_FLAGS),
    }
    return maybe_write_validation_report(
        combined,
        write_report=write_report,
        json_report=json_report,
        markdown_report=markdown_report,
    )


def adapt_records(
    records: Sequence[Mapping[str, Any]],
    *,
    profile: FreqtradePaperSourceProfile,
    account_scope_hash: str,
) -> dict[str, Any]:
    canonical_records: list[dict[str, Any]] = []
    candidate_source_row_indices: list[int] = []
    adapter_row_results: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    for index, row in enumerate(records):
        canonical, reasons = adapt_record(
            row,
            profile=profile,
            account_scope_hash=account_scope_hash,
        )
        if reasons:
            unique_reasons = sorted(set(reasons))
            reason_counts.update(unique_reasons)
            adapter_row_results.append(
                {"source_row_index": index, "status": "quarantined", "reasons": unique_reasons}
            )
            continue
        assert canonical is not None
        canonical_records.append(canonical)
        candidate_source_row_indices.append(index)
        adapter_row_results.append(
            {"source_row_index": index, "status": "candidate", "reasons": []}
        )
    return {
        "canonical_records": canonical_records,
        "candidate_source_row_indices": candidate_source_row_indices,
        "adapter_quarantined_row_count": sum(
            item["status"] == "quarantined" for item in adapter_row_results
        ),
        "quarantine_reason_counts": reason_counts,
        "adapter_row_results": adapter_row_results,
    }


def adapt_record(
    row: Mapping[str, Any],
    *,
    profile: FreqtradePaperSourceProfile,
    account_scope_hash: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    reasons: list[str] = []
    values = {key: row.get(column) for key, column in profile.column_map.items()}
    order_id = _text(values.get("order_id"))
    if order_id and not profile.order_id_namespace:
        reasons.append("order_id_namespace_missing")
    side = (_text(values.get("side")) or "").casefold()
    if side not in {"long", "short"}:
        reasons.append("side_invalid_for_gross_pnl")

    decimals: dict[str, Decimal] = {}
    for field in ("entry_price", "exit_price", "quantity", "net_pnl", "fee_open", "fee_close"):
        try:
            decimals[field] = decimal_from_value(values.get(field))
        except (FingerprintValidationError, TypeError):
            reasons.append(f"{field}_unavailable")
    for fee_field in ("fee_open", "fee_close"):
        if fee_field in decimals and decimals[fee_field] < 0:
            reasons.append("trading_fee_sign_invalid")
        if fee_field in decimals and decimals[fee_field] == 0:
            reasons.append("trading_fee_unverifiable_zero_from_producer_default")

    funding: Decimal | None = None
    funding_contract = profile.financial_contract
    if funding_contract.funding_availability != "column":
        reasons.append("funding_fee_unavailable")
    else:
        try:
            funding = decimal_from_value(row.get(funding_contract.funding_column or ""))
        except (FingerprintValidationError, TypeError):
            reasons.append("funding_fee_unavailable")

    contract_size: Decimal | None = None
    try:
        contract_size = decimal_from_value(profile.contract_size)
    except FingerprintValidationError:
        reasons.append("contract_size_invalid")
    gross: Decimal | None = None
    gross_inputs = {"entry_price", "exit_price", "quantity"}
    if side in {"long", "short"} and gross_inputs <= decimals.keys() and contract_size is not None:
        price_delta = decimals["exit_price"] - decimals["entry_price"]
        gross = price_delta * decimals["quantity"] * contract_size
        if side == "short":
            gross = -gross
    else:
        reasons.append("gross_pnl_inputs_unavailable")

    if reasons:
        return None, sorted(set(reasons))
    assert gross is not None and funding is not None
    trading_fee = decimals["fee_open"] + decimals["fee_close"]
    canonical = {
        "venue": profile.venue,
        "market_type": profile.market_type,
        "contract_type": profile.contract_type,
        "settlement_currency": profile.settlement_currency,
        "quantity_unit": profile.quantity_unit,
        "contract_size": profile.contract_size,
        "account_scope_hash": account_scope_hash,
        "order_id_namespace": profile.order_id_namespace if order_id else None,
        "source_trade_id": None,
        "order_id": order_id,
        "source": profile.source_namespace,
        "symbol": values.get("symbol"),
        "side": side,
        "open_time": values.get("open_time"),
        "close_time": values.get("close_time"),
        "entry_price": str(decimals["entry_price"]),
        "exit_price": str(decimals["exit_price"]),
        "quantity": str(decimals["quantity"]),
        "gross_pnl": str(gross),
        "trading_fee": str(trading_fee),
        "funding_fee": str(funding),
        "net_pnl": str(decimals["net_pnl"]),
        "epsilon_abs_fonte": profile.financial_contract.epsilon_abs_fonte,
    }
    return canonical, []


def profile_sources(primary: Path, replicas: Sequence[Path], root: Path) -> dict[str, Any]:
    paths = [primary, *replicas]
    entries: list[dict[str, Any]] = []
    for path in paths:
        exists = path.exists() and path.is_file()
        entries.append(
            {
                "path": display_path(path, root),
                "exists": exists,
                "sha256": file_sha256(path) if exists else None,
                "role": "primary" if path == primary else "replica",
            }
        )
    primary_hash = entries[0]["sha256"]
    missing = [entry["path"] for entry in entries if not entry["exists"]]
    divergent = [
        entry["path"]
        for entry in entries[1:]
        if entry["sha256"] is not None and entry["sha256"] != primary_hash
    ]
    replica_count = sum(
        entry["role"] == "replica" and entry["sha256"] == primary_hash
        for entry in entries
    )
    if not entries[0]["exists"]:
        source_status, source_reason = "blocked", "primary_source_missing"
    elif divergent:
        source_status, source_reason = "blocked", "source_replica_hash_mismatch"
    else:
        source_status, source_reason = "ok", "source_replicas_classified"
    unique_hashes = sorted({str(entry["sha256"]) for entry in entries if entry["sha256"]})
    return {
        "source_status": source_status,
        "source_reason": source_reason,
        "source_files": entries,
        "source_file_count": len(entries),
        "source_replica_count": replica_count,
        "source_replica_paths": [
            entry["path"]
            for entry in entries
            if entry["role"] == "replica" and entry["sha256"] == primary_hash
        ],
        "source_replica_hash_identical": bool(replicas) and replica_count == len(replicas),
        "unique_source_batch_count": len(unique_hashes),
        "primary_source_sha256": primary_hash,
        "missing_source_paths": missing,
        "divergent_replica_paths": divergent,
    }


def _base_report(
    *,
    source_profile_path: str | Path,
    account_scope_hash: str | None,
    write_report: bool,
    json_report: Path,
    markdown_report: Path,
) -> dict[str, Any]:
    return {
        "schema_version": ADAPTER_SCHEMA_VERSION,
        "status": "blocked",
        "reason": "not_evaluated",
        "source_profile_path": str(source_profile_path),
        "account_scope_hash_present": bool((account_scope_hash or "").strip()),
        "account_scope_hash_valid": False,
        "account_scope_original_identifier_persisted": False,
        "raw_row_count": 0,
        "accepted_row_count": 0,
        "quarantined_row_count": 0,
        "quarantined_reason_counts": {},
        "write_requested": bool(write_report),
        "write_performed": False,
        "output_paths": {"json": str(json_report), "markdown": str(markdown_report)},
        **SAFETY_FLAGS,
        "safety_flags": dict(SAFETY_FLAGS),
    }


def _blocked(
    report: dict[str, Any], reason: str, errors: Sequence[str] | None = None
) -> dict[str, Any]:
    result = dict(report)
    result.update(
        {
            "status": "blocked",
            "reason": reason,
            "validation_errors": sorted(set(errors or [reason])),
            "blockers": sorted(set(errors or [reason])),
            "write_performed": False,
        }
    )
    return result


def _empty_validator_report() -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": "no_canonical_records_delivered_to_validator",
        "accepted_row_count": 0,
        "quarantined_row_count": 0,
        "row_results": [],
        "validation_errors": [],
        "blockers": ["no_canonical_records_delivered_to_validator"],
        "warnings": [],
        "observed_fingerprint_collision_count": 0,
        "canonical_trade_id_coverage": 0.0,
        "fingerprint_deterministic": True,
        "accepted_rows_accounting_identity_violations": 0,
        "quarantined_rows_promoted_to_master": 0,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.casefold() in {"", "nan", "none", "null", "<na>"} else text
