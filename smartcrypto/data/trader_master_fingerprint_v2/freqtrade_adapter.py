"""In-memory adapter for authoritative Freqtrade paper closed-trade evidence."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any

from smartcrypto.data.trade_file_readonly import read_trade_file

from .authoritative_sqlite import read_authoritative_closed_trades
from .fingerprint_spec import (
    HEX_SHA256,
    FingerprintValidationError,
    decimal_from_value,
    normalize_timestamp,
)
from .quarantine_forensics import (
    RECOVERED as FORENSIC_RECOVERED,
    TARGET_TRADE_IDS as FORENSIC_TARGET_TRADE_IDS,
    build_targeted_quarantine_forensics_report,
)
from .quarantine_recovery import (
    RECOVERY_METADATA_KEY,
    RECOVERY_TRADE_IDS,
    RecoveryValidationError,
    apply_authoritative_recoveries,
    assess_authoritative_recovery_map,
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
HISTORICAL_FORENSIC_TARGET_TRADE_IDS = frozenset({141, 221, 234, 258, 561})
HISTORICAL_EXPECTED_RECOVERED_TRADE_IDS = frozenset({221, 234})
HISTORICAL_EXPECTED_REMAINING_TRADE_IDS = frozenset({141, 258, 561})


@dataclass(frozen=True)
class FreqtradePaperAdapterBundle:
    """Sanitized report plus in-memory records for read-only downstream checks."""

    report: dict[str, Any]
    accepted_canonical_records: tuple[dict[str, Any], ...]
    quarantined_row_summaries: tuple[dict[str, Any], ...]
    batch_identity: dict[str, Any]


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
    apply_authoritative_forensic_recovery: bool = False,
    _internal_capture: dict[str, Any] | None = None,
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
        apply_authoritative_forensic_recovery=apply_authoritative_forensic_recovery,
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
    baseline_adapted = reconcile_and_adapt_records(
        records,
        sqlite_rows,
        profile=profile,
        account_scope_hash=normalized_account_hash,
    )
    report.update(_without_internal_records(baseline_adapted))
    if baseline_adapted["structural_errors"]:
        report.update(
            raw_row_count=len(records),
            adapter_quarantined_row_count=len(records),
            quarantined_row_count=len(records),
            accepted_row_count=0,
        )
        return _blocked(
            report,
            "authoritative_sqlite_join_contract_violation",
            baseline_adapted["structural_errors"],
        )

    baseline_validator = _validate_adapted_records(
        baseline_adapted,
        source=source,
        root=root,
        source_sha256=str(source_summary["primary_source_sha256"]),
    )
    baseline_quarantined_count = int(baseline_adapted["adapter_quarantined_row_count"]) + int(
        baseline_validator.get("quarantined_row_count", 0)
    )
    forensic_context = _default_forensic_context(apply_authoritative_forensic_recovery)
    adapted = baseline_adapted
    validator = baseline_validator
    if apply_authoritative_forensic_recovery:
        forensic_context, recovered_sqlite_rows, forensic_results = _execute_forensic_recovery(
            root=root,
            source_profile_path=source_profile_path,
            snapshot=snapshot,
            profile=profile,
            sqlite_rows=sqlite_rows,
            adapter_snapshot_hashes=report.get("snapshot_source_hashes_before"),
        )
        adapted = reconcile_and_adapt_records(
            records,
            recovered_sqlite_rows,
            profile=profile,
            account_scope_hash=normalized_account_hash,
            forensic_trade_results=forensic_results,
        )
        validator = _validate_adapted_records(
            adapted,
            source=source,
            root=root,
            source_sha256=str(source_summary["primary_source_sha256"]),
        )
        report.update(_without_internal_records(adapted))

    canonical_records = adapted["canonical_records"]

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
    quarantined_order_ids = sorted(
        set(adapted["quarantined_order_ids"] + validator_quarantined_ids)
    )
    observed_order_ids = {
        str(item["order_id"])
        for item in adapted["adapter_row_results"]
        if item.get("order_id")
    }
    historical_closeout = evaluate_historical_batch_closeout(
        requested=apply_authoritative_forensic_recovery,
        observed_order_ids=observed_order_ids,
        recovered_order_ids=forensic_context["forensic_recovered_order_ids"],
        quarantined_order_ids=quarantined_order_ids,
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
        "quarantined_order_ids": quarantined_order_ids,
        "remaining_quarantined_order_ids": quarantined_order_ids,
        "quarantined_reason_counts": dict(sorted(reason_counts.items())),
        "adapter_row_results": adapted["adapter_row_results"],
        "pre_forensic_accepted_row_count": int(
            baseline_validator.get("accepted_row_count", 0)
        ),
        "pre_forensic_quarantined_row_count": baseline_quarantined_count,
        **historical_closeout,
        "recovery_writes_performed": False,
        "recovery_changes_fingerprint_spec": False,
        "recovery_changes_epsilon": False,
        **forensic_context,
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
    if _internal_capture is not None:
        accepted_records = [
            dict(record)
            for record, row_result in zip(
                canonical_records,
                validator.get("row_results", []),
                strict=True,
            )
            if row_result.get("status") == "accepted"
        ]
        _internal_capture.update(
            accepted_canonical_records=accepted_records,
            quarantined_row_summaries=[
                dict(item)
                for item in combined.get("adapter_row_results", [])
                if item.get("status") == "quarantined"
            ],
        )
    return maybe_write_validation_report(
        combined,
        write_report=write_report,
        json_report=json_report,
        markdown_report=markdown_report,
    )


def evaluate_historical_batch_closeout(
    *,
    requested: bool,
    observed_order_ids: Sequence[str] | set[str],
    recovered_order_ids: Sequence[str] | set[str],
    quarantined_order_ids: Sequence[str] | set[str],
) -> dict[str, Any]:
    """Evaluate the fixed forensic cohort independently from current quarantine health."""

    historical_targets = _order_ids(HISTORICAL_FORENSIC_TARGET_TRADE_IDS)
    expected_recovered = _order_ids(HISTORICAL_EXPECTED_RECOVERED_TRADE_IDS)
    expected_remaining = _order_ids(HISTORICAL_EXPECTED_REMAINING_TRADE_IDS)
    observed = set(observed_order_ids)
    recovered = set(recovered_order_ids)
    quarantined = set(quarantined_order_ids)
    historical_quarantined = quarantined & historical_targets

    contract_definition_errors: list[str] = []
    if HISTORICAL_FORENSIC_TARGET_TRADE_IDS != FORENSIC_TARGET_TRADE_IDS:
        contract_definition_errors.append("historical_target_scope_changed")
    if HISTORICAL_EXPECTED_RECOVERED_TRADE_IDS != RECOVERY_TRADE_IDS:
        contract_definition_errors.append("historical_recovery_allowlist_changed")
    if (
        HISTORICAL_EXPECTED_RECOVERED_TRADE_IDS
        | HISTORICAL_EXPECTED_REMAINING_TRADE_IDS
    ) != HISTORICAL_FORENSIC_TARGET_TRADE_IDS:
        contract_definition_errors.append("historical_target_partition_invalid")

    missing_targets = historical_targets - observed
    missing_recovered = expected_recovered - recovered
    unexpected_recovered = recovered - expected_recovered
    missing_remaining = expected_remaining - historical_quarantined
    unexpected_historical_quarantined = historical_quarantined - expected_remaining
    additional_runtime_quarantined = quarantined - historical_targets
    closeout_complete = bool(
        requested
        and not contract_definition_errors
        and not missing_targets
        and not missing_recovered
        and not unexpected_recovered
        and not missing_remaining
        and not unexpected_historical_quarantined
    )

    if not requested:
        closeout_status = "not_requested"
        closeout_reason = "authoritative_forensic_recovery_not_requested"
    elif closeout_complete:
        closeout_status = "completed_with_quarantine"
        closeout_reason = "fixed_historical_batch_recovered_with_expected_quarantine"
    else:
        closeout_status = "blocked"
        closeout_reason = "historical_forensic_recovery_contract_not_fully_satisfied"

    runtime_status = "blocked" if quarantined else "ok"
    runtime_reason = (
        "current_runtime_quarantines_present"
        if quarantined
        else "no_current_runtime_quarantines"
    )
    return {
        # Backward-compatible fields retain their original historical-batch intent.
        "batch_closeout_status": closeout_status,
        "batch_closeout_reason": closeout_reason,
        "historical_batch_closeout_status": closeout_status,
        "historical_batch_closeout_reason": closeout_reason,
        "historical_batch_closeout_complete": closeout_complete,
        "historical_batch_contract_definition_errors": sorted(contract_definition_errors),
        "historical_target_order_ids": sorted(historical_targets),
        "historical_expected_recovered_order_ids": sorted(expected_recovered),
        "historical_recovered_order_ids": sorted(recovered & historical_targets),
        "historical_missing_recovered_order_ids": sorted(missing_recovered),
        "historical_unexpected_recovered_order_ids": sorted(unexpected_recovered),
        "historical_expected_remaining_quarantined_order_ids": sorted(expected_remaining),
        "historical_remaining_quarantined_order_ids": sorted(
            historical_quarantined & expected_remaining
        ),
        "historical_unexpected_quarantined_order_ids": sorted(
            unexpected_historical_quarantined
        ),
        "historical_missing_expected_quarantined_order_ids": sorted(missing_remaining),
        "historical_missing_target_order_ids": sorted(missing_targets),
        "additional_runtime_quarantined_order_ids": sorted(additional_runtime_quarantined),
        "runtime_quarantine_status": runtime_status,
        "runtime_quarantine_reason": runtime_reason,
    }


def build_freqtrade_paper_closed_trades_adapter_bundle(
    *,
    project_root: str | Path,
    source_profile_path: str | Path,
    account_scope_hash: str | None,
    staging_file: str | Path | None = None,
    authoritative_sqlite_path: str | Path | None = None,
    apply_authoritative_forensic_recovery: bool = False,
) -> FreqtradePaperAdapterBundle:
    """Return an in-memory adapter bundle without enabling report or Master writes."""

    capture: dict[str, Any] = {}
    report = build_freqtrade_paper_closed_trades_adapter_report(
        project_root=project_root,
        source_profile_path=source_profile_path,
        account_scope_hash=account_scope_hash,
        staging_file=staging_file,
        authoritative_sqlite_path=authoritative_sqlite_path,
        write_report=False,
        write_to_master_requested=False,
        apply_authoritative_forensic_recovery=apply_authoritative_forensic_recovery,
        _internal_capture=capture,
    )
    quarantined = capture.get("quarantined_row_summaries")
    if not isinstance(quarantined, list):
        quarantined = [
            dict(item)
            for item in report.get("adapter_row_results", [])
            if isinstance(item, Mapping) and item.get("status") == "quarantined"
        ]
    batch_identity = {
        "source_profile_id": report.get("source_profile_id"),
        "paper_source_path": report.get("source_file"),
        "paper_source_hash": report.get("primary_source_sha256"),
        "sqlite_hashes_before": report.get("snapshot_source_hashes_before", {}),
        "sqlite_hashes_after": report.get("snapshot_source_hashes_after", {}),
        "raw_row_count": int(report.get("raw_row_count", 0)),
        "accepted_row_count": int(report.get("accepted_row_count", 0)),
        "quarantined_row_count": int(report.get("quarantined_row_count", 0)),
        "quarantined_order_ids": list(report.get("quarantined_order_ids", [])),
        "forensic_recovery_applied_count": int(
            report.get("forensic_recovery_applied_count", 0)
        ),
    }
    return FreqtradePaperAdapterBundle(
        report=report,
        accepted_canonical_records=tuple(
            dict(item) for item in capture.get("accepted_canonical_records", [])
        ),
        quarantined_row_summaries=tuple(dict(item) for item in quarantined),
        batch_identity=batch_identity,
    )


def reconcile_and_adapt_records(
    csv_records: Sequence[Mapping[str, Any]],
    sqlite_records: Sequence[Mapping[str, Any]],
    *,
    profile: FreqtradePaperSourceProfile,
    account_scope_hash: str,
    forensic_trade_results: Mapping[int, Mapping[str, Any]] | None = None,
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
    forensic_results = forensic_trade_results or {}
    residuals: list[Decimal] = []
    for trade_id in sorted(csv_by_id):
        _, order_id, csv_row, source_index = csv_by_id[trade_id]
        sqlite_row = sqlite_by_id[trade_id]
        canonical, reasons, metrics = adapt_record(
            csv_row,
            sqlite_row,
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
            **_forensic_row_provenance(
                sqlite_row,
                forensic_results.get(trade_id),
            ),
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


def _order_ids(trade_ids: Sequence[int] | set[int] | frozenset[int]) -> set[str]:
    return {f"freqtrade-paper-{trade_id}" for trade_id in trade_ids}


def _validate_adapted_records(
    adapted: Mapping[str, Any],
    *,
    source: Path,
    root: Path,
    source_sha256: str,
) -> dict[str, Any]:
    canonical_records = adapted["canonical_records"]
    validator = (
        validate_staging_records(
            canonical_records,
            source_file=display_path(source, root),
            source_sha256=source_sha256,
            ingestion_run_id=f"freqtrade-paper-v2-{source_sha256[:16]}",
        )
        if canonical_records
        else _empty_validator_report()
    )
    for item, original_index, order_id in zip(
        validator.get("row_results", []),
        adapted["candidate_source_row_indices"],
        adapted["candidate_order_ids"],
        strict=True,
    ):
        item["source_row_index"] = original_index
        item["order_id"] = order_id
    return validator


def _execute_forensic_recovery(
    *,
    root: Path,
    source_profile_path: str | Path,
    snapshot: Path,
    profile: FreqtradePaperSourceProfile,
    sqlite_rows: Sequence[Mapping[str, Any]],
    adapter_snapshot_hashes: object,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[int, Mapping[str, Any]]]:
    forensic_report = build_targeted_quarantine_forensics_report(
        project_root=root,
        source_profile_path=source_profile_path,
        authoritative_sqlite_path=snapshot,
    )
    raw_results = forensic_report.get("trade_results")
    forensic_results: dict[int, Mapping[str, Any]] = {}
    if isinstance(raw_results, Sequence) and not isinstance(raw_results, (str, bytes)):
        for item in raw_results:
            if isinstance(item, Mapping):
                trade_id = parse_freqtrade_order_id(item.get("order_id"))
                if trade_id is not None:
                    forensic_results[trade_id] = item
    candidate_ids = sorted(RECOVERY_TRADE_IDS & set(forensic_results))
    forensic_hashes = forensic_report.get("snapshot_source_hashes_before")
    hash_match = (
        isinstance(adapter_snapshot_hashes, Mapping)
        and bool(adapter_snapshot_hashes)
        and adapter_snapshot_hashes == forensic_hashes
    )
    context = _default_forensic_context(True)
    context.update(
        authoritative_forensic_recovery_executed=True,
        forensic_report_status=str(forensic_report.get("status", "blocked")),
        forensic_snapshot_hash_match=hash_match,
        forensic_recovery_candidate_count=len(candidate_ids),
    )
    untouched = [dict(row) for row in sqlite_rows]
    if forensic_report.get("status") != "ok":
        context.update(
            forensic_recovery_rejected_count=len(candidate_ids),
            forensic_recovery_rejected_order_ids=[
                f"freqtrade-paper-{trade_id}" for trade_id in candidate_ids
            ],
            forensic_recovery_gate_errors=["forensic_report_not_ok"],
        )
        return context, untouched, forensic_results
    if not hash_match:
        context.update(
            forensic_recovery_rejected_count=len(candidate_ids),
            forensic_recovery_rejected_order_ids=[
                f"freqtrade-paper-{trade_id}" for trade_id in candidate_ids
            ],
            forensic_recovery_gate_errors=["forensic_snapshot_hash_mismatch"],
        )
        return context, untouched, forensic_results

    epsilon = decimal_from_value(profile.financial_contract.epsilon_abs_fonte)
    try:
        assessment = assess_authoritative_recovery_map(forensic_report, epsilon=epsilon)
        recovered_rows, application = apply_authoritative_recoveries(
            sqlite_rows,
            assessment.recoveries,
        )
    except RecoveryValidationError as exc:
        context.update(
            forensic_recovery_rejected_count=len(candidate_ids),
            forensic_recovery_rejected_order_ids=[
                f"freqtrade-paper-{trade_id}" for trade_id in candidate_ids
            ],
            forensic_recovery_gate_errors=[str(exc)],
        )
        return context, untouched, forensic_results

    applied_ids = set(application["applied_trade_ids"])
    rejected_ids = (set(candidate_ids) - applied_ids) | set(application["rejected_trade_ids"])
    rejected_reasons = {
        str(trade_id): list(reasons)
        for trade_id, reasons in assessment.rejected_reasons.items()
        if trade_id in rejected_ids
    }
    rejected_reasons.update(application["rejected_reasons"])
    context.update(
        forensic_recovery_applied_count=len(applied_ids),
        forensic_recovered_order_ids=[
            f"freqtrade-paper-{trade_id}" for trade_id in sorted(applied_ids)
        ],
        forensic_recovery_rejected_count=len(rejected_ids),
        forensic_recovery_rejected_order_ids=[
            f"freqtrade-paper-{trade_id}" for trade_id in sorted(rejected_ids)
        ],
        forensic_recovery_rejected_reasons=rejected_reasons,
        forensic_recovery_gate_errors=[],
    )
    return context, recovered_rows, forensic_results


def _default_forensic_context(requested: bool) -> dict[str, Any]:
    return {
        "authoritative_forensic_recovery_requested": bool(requested),
        "authoritative_forensic_recovery_executed": False,
        "forensic_report_status": "not_requested",
        "forensic_snapshot_hash_match": None,
        "forensic_recovery_candidate_count": 0,
        "forensic_recovery_applied_count": 0,
        "forensic_recovered_order_ids": [],
        "forensic_recovery_rejected_count": 0,
        "forensic_recovery_rejected_order_ids": [],
        "forensic_recovery_rejected_reasons": {},
        "forensic_recovery_gate_errors": [],
    }


def _forensic_row_provenance(
    sqlite_row: Mapping[str, Any],
    forensic_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    metadata = sqlite_row.get(RECOVERY_METADATA_KEY)
    applied = isinstance(metadata, Mapping)
    candidate = bool(
        forensic_result is not None
        and forensic_result.get("recovery_decision") == FORENSIC_RECOVERED
    )
    provenance: dict[str, Any] = {
        "forensic_recovery_candidate": candidate,
        "forensic_recovery_applied": applied,
        "remains_quarantined_after_forensics": bool(forensic_result is not None and not applied),
    }
    if isinstance(metadata, Mapping):
        provenance.update(
            original_close_rate=metadata.get("original_close_rate"),
            recovered_close_rate=metadata.get("recovered_close_rate"),
            recovery_source=metadata.get("recovery_source"),
            recovery_formula_version=metadata.get("formula_version"),
            forensic_recovered_residual=metadata.get("recovered_residual"),
            forensic_evidence_tables=metadata.get("evidence_tables"),
            forensic_evidence_row_ids=metadata.get("evidence_row_ids"),
            close_rate_requested_used=False,
        )
    elif forensic_result is not None:
        provenance.update(
            forensic_recovery_decision=forensic_result.get("recovery_decision"),
            forensic_remaining_blockers=forensic_result.get("remaining_blockers", []),
        )
    return provenance


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
    recovery_metadata = sqlite_row.get(RECOVERY_METADATA_KEY)
    forensic_exit_recovery_applied = isinstance(recovery_metadata, Mapping)
    for field, csv_value, sqlite_value in decimal_comparisons:
        if (
            field == "exit_price"
            and forensic_exit_recovery_applied
            and _normalized_decimal(csv_value) is None
        ):
            continue
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
    apply_authoritative_forensic_recovery: bool,
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
        "authoritative_forensic_recovery_requested": bool(
            apply_authoritative_forensic_recovery
        ),
        "authoritative_forensic_recovery_executed": False,
        "forensic_report_status": "not_requested",
        "forensic_snapshot_hash_match": None,
        "forensic_recovery_candidate_count": 0,
        "forensic_recovery_applied_count": 0,
        "forensic_recovered_order_ids": [],
        "forensic_recovery_rejected_count": 0,
        "forensic_recovery_rejected_order_ids": [],
        "pre_forensic_accepted_row_count": 0,
        "pre_forensic_quarantined_row_count": 0,
        "remaining_quarantined_order_ids": [],
        "batch_closeout_status": "not_requested",
        "batch_closeout_reason": "authoritative_forensic_recovery_not_requested",
        "historical_batch_closeout_status": "not_requested",
        "historical_batch_closeout_reason": "authoritative_forensic_recovery_not_requested",
        "historical_batch_closeout_complete": False,
        "historical_batch_contract_definition_errors": [],
        "historical_target_order_ids": sorted(
            _order_ids(HISTORICAL_FORENSIC_TARGET_TRADE_IDS)
        ),
        "historical_expected_recovered_order_ids": sorted(
            _order_ids(HISTORICAL_EXPECTED_RECOVERED_TRADE_IDS)
        ),
        "historical_recovered_order_ids": [],
        "historical_missing_recovered_order_ids": sorted(
            _order_ids(HISTORICAL_EXPECTED_RECOVERED_TRADE_IDS)
        ),
        "historical_unexpected_recovered_order_ids": [],
        "historical_expected_remaining_quarantined_order_ids": sorted(
            _order_ids(HISTORICAL_EXPECTED_REMAINING_TRADE_IDS)
        ),
        "historical_remaining_quarantined_order_ids": [],
        "historical_unexpected_quarantined_order_ids": [],
        "historical_missing_expected_quarantined_order_ids": sorted(
            _order_ids(HISTORICAL_EXPECTED_REMAINING_TRADE_IDS)
        ),
        "historical_missing_target_order_ids": sorted(
            _order_ids(HISTORICAL_FORENSIC_TARGET_TRADE_IDS)
        ),
        "additional_runtime_quarantined_order_ids": [],
        "runtime_quarantine_status": "not_evaluated",
        "runtime_quarantine_reason": "not_evaluated",
        "recovery_writes_performed": False,
        "recovery_changes_fingerprint_spec": False,
        "recovery_changes_epsilon": False,
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
