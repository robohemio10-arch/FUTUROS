"""In-memory adapter for authoritative Freqtrade paper closed-trade evidence."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any

from smartcrypto.data.trades_importer import read_trade_file

from .authoritative_sqlite import read_authoritative_closed_trades
from .fingerprint_spec import (
    HEX_SHA256,
    FingerprintValidationError,
    decimal_from_value,
    normalize_timestamp,
)
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
ORDER_ID_PATTERN = re.compile(r"^freqtrade-paper-([1-9][0-9]*)$")


def build_freqtrade_paper_closed_trades_adapter_report(
    *,
    project_root: str | Path,
    source_profile_path: str | Path,
    account_scope_hash: str | None,
    staging_file: str | Path | None = None,
    authoritative_sqlite_path: str | Path | None = None,
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
    replicas = [resolve_path(root, item) for item in profile.replica_source_paths]
    source_summary = profile_sources(source, replicas, root)
    snapshot = resolve_path(
        root,
        authoritative_sqlite_path or profile.authoritative_sqlite.snapshot_path,
    )
    report = {
        **base,
        "source_profile_id": profile.profile_id,
        "source_profile_schema_version": profile.schema_version,
        "source_profile_sha256": profile.profile_sha256,
        "producer_module": profile.producer_module,
        "producer_function": profile.producer_function,
        "source_file": display_path(source, root),
        "authoritative_sqlite_path": display_path(snapshot, root),
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

    snapshot_result = read_authoritative_closed_trades(
        project_root=root,
        snapshot_path=snapshot,
        profile=profile,
    )
    sqlite_rows = snapshot_result.pop("rows")
    report.update(snapshot_result)
    if snapshot_result["status"] != "ok":
        return _blocked(
            report,
            str(snapshot_result["reason"]),
            snapshot_result.get("validation_errors", []),
        )

    records = frame.to_dict(orient="records")
    adapted = reconcile_and_adapt_records(
        records,
        sqlite_rows,
        profile=profile,
        account_scope_hash=normalized_account_hash,
    )
    report.update(_without_internal_records(adapted))
    if adapted["structural_errors"]:
        report.update(
            raw_row_count=len(records),
            adapter_quarantined_row_count=len(records),
            quarantined_row_count=len(records),
            accepted_row_count=0,
        )
        return _blocked(report, "authoritative_sqlite_join_contract_violation", adapted["structural_errors"])

    canonical_records = adapted["canonical_records"]
    validator = (
        validate_staging_records(
            canonical_records,
            source_file=display_path(source, root),
            source_sha256=str(source_summary["primary_source_sha256"]),
            ingestion_run_id=f"freqtrade-paper-v2-{str(source_summary['primary_source_sha256'])[:16]}",
        )
        if canonical_records
        else _empty_validator_report()
    )
    candidate_indices = adapted["candidate_source_row_indices"]
    candidate_order_ids = adapted["candidate_order_ids"]
    for item, original_index, order_id in zip(
        validator.get("row_results", []),
        candidate_indices,
        candidate_order_ids,
        strict=True,
    ):
        item["source_row_index"] = original_index
        item["order_id"] = order_id

    reason_counts = Counter(adapted["quarantine_reason_counts"])
    validator_quarantined_ids: list[str] = []
    for item in validator.get("row_results", []):
        if item.get("status") == "quarantined":
            reason_counts.update(item.get("reasons", []))
            validator_quarantined_ids.append(str(item["order_id"]))
    quarantined_count = int(adapted["adapter_quarantined_row_count"]) + int(
        validator.get("quarantined_row_count", 0)
    )
    accepted_count = int(validator.get("accepted_row_count", 0))
    status = "blocked" if quarantined_count or validator.get("status") != "ok" else "ok"
    reason = (
        "rows_quarantined_after_authoritative_reconciliation"
        if quarantined_count
        else str(validator.get("reason", "ok"))
    )
    combined = {
        **report,
        **validator,
        "status": status,
        "reason": reason,
        "raw_row_count": len(records),
        "adapter_candidate_row_count": len(canonical_records),
        "canonical_records_delivered_to_validator_count": len(canonical_records),
        "adapter_quarantined_row_count": int(adapted["adapter_quarantined_row_count"]),
        "accepted_row_count": accepted_count,
        "quarantined_row_count": quarantined_count,
        "quarantined_order_ids": sorted(
            set(adapted["quarantined_order_ids"] + validator_quarantined_ids)
        ),
        "quarantined_reason_counts": dict(sorted(reason_counts.items())),
        "adapter_row_results": adapted["adapter_row_results"],
        "account_scope_hash_present": True,
        "account_scope_hash_valid": True,
        "account_scope_original_identifier_persisted": False,
        "gross_pnl_reconstruction": profile.financial_contract.gross_pnl_formula,
        "gross_pnl_independent_from_net_pnl": True,
        "fee_open_normalization": profile.financial_contract.fee_open_normalization,
        "fee_close_normalization": profile.financial_contract.fee_close_normalization,
        "funding_normalization": profile.financial_contract.funding_normalization,
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


def reconcile_and_adapt_records(
    csv_records: Sequence[Mapping[str, Any]],
    sqlite_records: Sequence[Mapping[str, Any]],
    *,
    profile: FreqtradePaperSourceProfile,
    account_scope_hash: str,
) -> dict[str, Any]:
    parsed_csv: list[tuple[int, str, Mapping[str, Any], int]] = []
    malformed_order_ids: list[str] = []
    for index, row in enumerate(csv_records):
        order_id = _text(row.get(profile.column_map["order_id"])) or ""
        trade_id = parse_freqtrade_order_id(order_id)
        if trade_id is None:
            malformed_order_ids.append(order_id or f"<row:{index}>")
            continue
        parsed_csv.append((trade_id, order_id, row, index))

    csv_id_counts = Counter(item[0] for item in parsed_csv)
    duplicate_csv_ids = sorted(trade_id for trade_id, count in csv_id_counts.items() if count > 1)
    sqlite_ids = [int(row["id"]) for row in sqlite_records]
    sqlite_id_counts = Counter(sqlite_ids)
    duplicate_sqlite_ids = sorted(
        trade_id for trade_id, count in sqlite_id_counts.items() if count > 1
    )
    csv_by_id = {item[0]: item for item in parsed_csv}
    sqlite_by_id = {int(row["id"]): row for row in sqlite_records}
    csv_only_ids = sorted(set(csv_by_id) - set(sqlite_by_id))
    sqlite_only_ids = sorted(set(sqlite_by_id) - set(csv_by_id))
    structural_errors: list[str] = []
    if malformed_order_ids:
        structural_errors.append("malformed_order_ids")
    if duplicate_csv_ids:
        structural_errors.append("duplicate_csv_trade_ids")
    if duplicate_sqlite_ids:
        structural_errors.append("duplicate_sqlite_trade_ids")
    if csv_only_ids:
        structural_errors.append("csv_only_trade_ids")
    if sqlite_only_ids:
        structural_errors.append("sqlite_only_trade_ids")

    base: dict[str, Any] = {
        "exact_join_count": len(set(csv_by_id) & set(sqlite_by_id)),
        "csv_only_trade_id_count": len(csv_only_ids),
        "csv_only_trade_ids": csv_only_ids,
        "sqlite_only_trade_id_count": len(sqlite_only_ids),
        "sqlite_only_trade_ids": sqlite_only_ids,
        "malformed_order_id_count": len(malformed_order_ids),
        "malformed_order_ids": sorted(malformed_order_ids),
        "duplicate_csv_trade_id_count": len(duplicate_csv_ids),
        "duplicate_csv_trade_ids": duplicate_csv_ids,
        "duplicate_sqlite_trade_id_count": len(duplicate_sqlite_ids),
        "duplicate_sqlite_trade_ids": duplicate_sqlite_ids,
        "structural_errors": sorted(structural_errors),
        "canonical_records": [],
        "candidate_source_row_indices": [],
        "candidate_order_ids": [],
        "adapter_row_results": [],
        "adapter_quarantined_row_count": 0,
        "quarantine_reason_counts": Counter(),
        "quarantined_order_ids": [],
        "formula_match_count": 0,
        "formula_mismatch_count": 0,
        "accounting_residual_observation_count": 0,
        "max_accounting_residual": None,
        "median_accounting_residual": None,
    }
    if structural_errors:
        return base

    epsilon = decimal_from_value(profile.financial_contract.epsilon_abs_fonte)
    residuals: list[Decimal] = []
    for trade_id in sorted(csv_by_id):
        _, order_id, csv_row, source_index = csv_by_id[trade_id]
        canonical, reasons, metrics = adapt_record(
            csv_row,
            sqlite_by_id[trade_id],
            profile=profile,
            account_scope_hash=account_scope_hash,
        )
        residual = metrics.get("accounting_residual")
        if isinstance(residual, Decimal):
            residuals.append(residual)
            if residual <= epsilon:
                base["formula_match_count"] += 1
            else:
                base["formula_mismatch_count"] += 1
        row_result = {
            "source_row_index": source_index,
            "order_id": order_id,
            "sqlite_trade_id": trade_id,
            "status": "quarantined" if reasons else "candidate",
            "reasons": sorted(set(reasons)),
            "effective_open_fee": _decimal_text(metrics.get("effective_open_fee")),
            "effective_close_fee": _decimal_text(metrics.get("effective_close_fee")),
            "normalized_funding_fee": _decimal_text(metrics.get("normalized_funding_fee")),
            "reconstructed_gross_pnl": _decimal_text(metrics.get("gross_pnl")),
            "reconstructed_net_pnl": _decimal_text(metrics.get("reconstructed_net_pnl")),
            "accounting_residual": _decimal_text(residual),
            "fee_open_currency": metrics.get("fee_open_currency"),
            "fee_close_currency": metrics.get("fee_close_currency"),
        }
        base["adapter_row_results"].append(row_result)
        if reasons:
            unique_reasons = sorted(set(reasons))
            base["quarantine_reason_counts"].update(unique_reasons)
            base["quarantined_order_ids"].append(order_id)
            continue
        assert canonical is not None
        base["canonical_records"].append(canonical)
        base["candidate_source_row_indices"].append(source_index)
        base["candidate_order_ids"].append(order_id)

    base["adapter_quarantined_row_count"] = len(base["quarantined_order_ids"])
    base["accounting_residual_observation_count"] = len(residuals)
    if residuals:
        base["max_accounting_residual"] = _decimal_text(max(residuals))
        base["median_accounting_residual"] = _decimal_text(median(residuals))
    return base


def adapt_record(
    csv_row: Mapping[str, Any],
    sqlite_row: Mapping[str, Any],
    *,
    profile: FreqtradePaperSourceProfile,
    account_scope_hash: str,
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    reasons = _source_divergence_reasons(csv_row, sqlite_row, profile)
    metrics: dict[str, Any] = {
        "fee_open_currency": _text(sqlite_row.get("fee_open_currency")),
        "fee_close_currency": _text(sqlite_row.get("fee_close_currency")),
    }
    decimals: dict[str, Decimal] = {}
    for field in (
        "open_rate",
        "close_rate",
        "amount",
        "contract_size",
        "leverage",
        "fee_open_cost",
        "fee_close_cost",
        "funding_fees",
        "close_profit_abs",
        "realized_profit",
    ):
        try:
            decimals[field] = decimal_from_value(sqlite_row.get(field))
        except (FingerprintValidationError, TypeError):
            reasons.append(f"{field}_unavailable")

    side = "short" if _sqlite_bool(sqlite_row.get("is_short")) else "long"
    if metrics["fee_open_currency"] != profile.settlement_currency:
        reasons.append("fee_open_currency_invalid")
    if metrics["fee_close_currency"] != profile.settlement_currency:
        reasons.append("fee_close_currency_invalid")
    for positive_field in ("amount", "contract_size", "leverage", "open_rate"):
        if positive_field in decimals and decimals[positive_field] <= 0:
            reasons.append(f"{positive_field}_not_positive")
    for fee_field in ("fee_open_cost", "fee_close_cost"):
        if fee_field in decimals and decimals[fee_field] < 0:
            reasons.append("trading_fee_sign_invalid")

    required = {
        "open_rate",
        "close_rate",
        "amount",
        "contract_size",
        "leverage",
        "fee_open_cost",
        "fee_close_cost",
        "funding_fees",
        "close_profit_abs",
    }
    if not required <= decimals.keys():
        reasons.append("accounting_reconciliation_unavailable")
        if "close_rate" not in decimals:
            reasons.extend(["exit_price_unavailable", "gross_pnl_inputs_unavailable"])
        return None, sorted(set(reasons)), metrics

    gross = (
        (decimals["open_rate"] - decimals["close_rate"])
        if side == "short"
        else (decimals["close_rate"] - decimals["open_rate"])
    ) * decimals["amount"] * decimals["contract_size"]
    effective_open_fee = decimals["fee_open_cost"] * decimals["leverage"]
    effective_close_fee = decimals["fee_close_cost"]
    trading_fee = effective_open_fee + effective_close_fee
    normalized_funding_fee = -decimals["funding_fees"]
    reconstructed_net = gross - trading_fee - normalized_funding_fee
    residual = abs(reconstructed_net - decimals["close_profit_abs"])
    epsilon = decimal_from_value(profile.financial_contract.epsilon_abs_fonte)
    metrics.update(
        gross_pnl=gross,
        effective_open_fee=effective_open_fee,
        effective_close_fee=effective_close_fee,
        normalized_funding_fee=normalized_funding_fee,
        reconstructed_net_pnl=reconstructed_net,
        accounting_residual=residual,
    )
    if residual > epsilon:
        reasons.append("financial_accounting_identity_violation")
    if reasons:
        return None, sorted(set(reasons)), metrics

    order_id = _text(csv_row.get(profile.column_map["order_id"]))
    canonical = {
        "venue": _text(sqlite_row.get("exchange")),
        "market_type": profile.market_type,
        "contract_type": profile.contract_type,
        "settlement_currency": profile.settlement_currency,
        "quantity_unit": profile.quantity_unit,
        "contract_size": str(decimals["contract_size"]),
        "account_scope_hash": account_scope_hash,
        "order_id_namespace": profile.order_id_namespace,
        "source_trade_id": None,
        "order_id": order_id,
        "source": profile.source_namespace,
        "symbol": _normalize_pair(sqlite_row.get("pair")),
        "side": side,
        "open_time": sqlite_row.get("open_date"),
        "close_time": sqlite_row.get("close_date"),
        "entry_price": str(decimals["open_rate"]),
        "exit_price": str(decimals["close_rate"]),
        "quantity": str(decimals["amount"]),
        "gross_pnl": str(gross),
        "trading_fee": str(trading_fee),
        "funding_fee": str(normalized_funding_fee),
        "net_pnl": str(decimals["close_profit_abs"]),
        "epsilon_abs_fonte": profile.financial_contract.epsilon_abs_fonte,
    }
    return canonical, [], metrics


def parse_freqtrade_order_id(value: object) -> int | None:
    text = _text(value)
    if text is None:
        return None
    match = ORDER_ID_PATTERN.fullmatch(text)
    return int(match.group(1)) if match else None


def _source_divergence_reasons(
    csv_row: Mapping[str, Any],
    sqlite_row: Mapping[str, Any],
    profile: FreqtradePaperSourceProfile,
) -> list[str]:
    columns = profile.column_map
    reasons: list[str] = []
    exact_comparisons = (
        (
            "symbol",
            _normalize_symbol(csv_row.get(columns["symbol"])),
            _normalize_pair(sqlite_row.get("pair")),
        ),
        (
            "side",
            (_text(csv_row.get(columns["side"])) or "").casefold(),
            "short" if _sqlite_bool(sqlite_row.get("is_short")) else "long",
        ),
        (
            "open_time",
            _normalized_timestamp(csv_row.get(columns["open_time"])),
            _normalized_timestamp(sqlite_row.get("open_date")),
        ),
        (
            "close_time",
            _normalized_timestamp(csv_row.get(columns["close_time"])),
            _normalized_timestamp(sqlite_row.get("close_date")),
        ),
    )
    for field, csv_value, sqlite_value in exact_comparisons:
        if csv_value != sqlite_value:
            reasons.append(f"{field}_divergence")
    epsilon = decimal_from_value(profile.financial_contract.epsilon_abs_fonte)
    decimal_comparisons = (
        ("net_pnl", csv_row.get(columns["net_pnl"]), sqlite_row.get("close_profit_abs")),
        ("entry_price", csv_row.get(columns["entry_price"]), sqlite_row.get("open_rate")),
        ("exit_price", csv_row.get(columns["exit_price"]), sqlite_row.get("close_rate")),
        ("quantity", csv_row.get(columns["quantity"]), sqlite_row.get("amount")),
        ("leverage", csv_row.get(columns["leverage"]), sqlite_row.get("leverage")),
    )
    for field, csv_value, sqlite_value in decimal_comparisons:
        if not _decimal_values_match(csv_value, sqlite_value, epsilon):
            reasons.append(f"{field}_divergence")
    if (_text(sqlite_row.get("exchange")) or "").casefold() != profile.venue.casefold():
        reasons.append("exchange_divergence")
    return reasons


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
        entry["role"] == "replica" and entry["sha256"] == primary_hash for entry in entries
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
        "exact_join_count": 0,
        "csv_only_trade_id_count": 0,
        "sqlite_only_trade_id_count": 0,
        "formula_match_count": 0,
        "max_accounting_residual": None,
        "median_accounting_residual": None,
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


def _without_internal_records(adapted: Mapping[str, Any]) -> dict[str, Any]:
    internal = {"canonical_records", "candidate_source_row_indices", "candidate_order_ids"}
    result = {key: value for key, value in adapted.items() if key not in internal}
    if isinstance(result.get("quarantine_reason_counts"), Counter):
        result["quarantine_reason_counts"] = dict(result["quarantine_reason_counts"])
    return result


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
        return str(path.resolve())


def _normalize_pair(value: object) -> str | None:
    text = _text(value)
    if text is None:
        return None
    return text.split(":", maxsplit=1)[0].replace("/", "").replace("-", "").upper()


def _normalize_symbol(value: object) -> str | None:
    text = _text(value)
    if text is None:
        return None
    return text.replace("/", "").replace("-", "").replace("_", "").upper()


def _normalized_timestamp(value: object) -> str | None:
    try:
        return normalize_timestamp(value)
    except FingerprintValidationError:
        return None


def _normalized_decimal(value: object) -> Decimal | None:
    try:
        return decimal_from_value(value)
    except (FingerprintValidationError, TypeError):
        return None


def _decimal_values_match(left: object, right: object, epsilon: Decimal) -> bool:
    normalized_left = _normalized_decimal(left)
    normalized_right = _normalized_decimal(right)
    if normalized_left is None or normalized_right is None:
        return normalized_left is normalized_right
    return abs(normalized_left - normalized_right) <= epsilon


def _sqlite_bool(value: object) -> bool:
    return value is True or value == 1 or str(value).strip().casefold() in {"true", "1"}


def _decimal_text(value: object) -> str | None:
    return format(value, "f") if isinstance(value, Decimal) else None


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.casefold() in {"", "nan", "none", "null", "<na>"} else text
