"""Institutional read-only adapter for the locked Bitradex OCR batch."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

from .fingerprint_spec import (
    FINGERPRINT_SPEC_VERSION,
    canonical_trade_id_for,
    normalize_trade_row,
    row_fingerprint_for,
)
from .master_adapter import file_sha256, read_trader_master_readonly
from .master_reconciliation import reconcile_canonical_records, reconciliation_decision
from .staging_validator import validate_staging_records


SCHEMA_VERSION: Final = "bitradex_ocr_batch_readonly_adapter_v2"
PROFILE_SCHEMA_VERSION: Final = "bitradex_ocr_locked_candidates_source_profile_v2"
DEFAULT_PROFILE: Final = Path("config/bitradex_ocr_locked_candidates_source_profile_v2.json")
DEFAULT_MASTER: Final = Path("data/trades/trades_master.parquet")
DEFAULT_JSON_REPORT: Final = Path("data/reports/bitradex_ocr_batch_readonly_adapter_v2.json")
DEFAULT_MARKDOWN_REPORT: Final = Path("data/reports/bitradex_ocr_batch_readonly_adapter_v2.md")
HEX64_RE: Final = re.compile(r"^[0-9a-f]{64}$")

CLASSIFICATIONS: Final = (
    "VERIFIED_NOVEL",
    "VERIFIED_EXISTING",
    "PRIMARY_IDENTITY_CONFLICT",
    "LEGACY_OVERLAP_AMBIGUOUS",
    "MASTER_COMPARISON_UNAVAILABLE",
    "ACCOUNTING_CONTRACT_BLOCKED",
)

REQUIRED_V4_COLUMNS: Final = (
    "source_file_name",
    "source_image_path",
    "source_sha256",
    "order_id",
    "symbol",
    "position_side",
    "entry_price",
    "exit_price",
    "closed_volume",
    "open_time_utc",
    "close_time_utc",
    "pnl_fechado",
    "taxa_1",
    "taxa_2",
)
REQUIRED_EXCLUSION_COLUMNS: Final = (
    "excluded_source_file_name",
    "excluded_source_sha256",
    "retained_source_file_name",
    "retained_source_sha256",
    "financial_trade_fingerprint",
    "exclusion_reason",
)
REQUIRED_GROUP_COLUMNS: Final = (
    "duplicate_group_id",
    "financial_trade_fingerprint",
    "disposition",
    "source_file_name",
    "source_sha256",
)
REQUIRED_MAPPING_COLUMNS: Final = (
    "raw_order_id",
    "synthetic_order_id",
    "financial_trade_fingerprint",
    "source_file_name",
    "source_image_path",
    "source_sha256",
)

SAFETY_FLAGS: Final[dict[str, bool]] = {
    "writes_trader_master": False,
    "writes_parquet": False,
    "writes_xlsx": False,
    "writes_sqlite": False,
    "changes_training_dataset": False,
    "changes_model": False,
    "changes_risk": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "operational_authority": False,
    "runs_preview": False,
    "runs_import": False,
    "runs_sidecar_rebuild": False,
    "runs_qlib": False,
    "runs_ai_shadow": False,
    "runs_strategy_factory": False,
}


class BitradexProfileError(ValueError):
    """Raised when the versioned source profile is incomplete or unsafe."""


class BitradexSourceError(ValueError):
    """Raised when V4/V5 source evidence is incomplete or inconsistent."""


@dataclass(frozen=True)
class BitradexSourceFiles:
    package_v4: str
    package_v5: str
    canonical_v4_csv: str
    excluded_duplicates_v5_csv: str
    duplicate_groups_v5_csv: str
    synthetic_mapping_v5_csv: str


@dataclass(frozen=True)
class BitradexIdentityContract:
    venue: str
    market_type: str
    contract_type: str
    settlement_currency: str
    quantity_unit: str
    contract_size: str
    source_namespace: str
    native_order_identity_policy: str


@dataclass(frozen=True)
class BitradexFinancialContract:
    gross_pnl_formula: str
    taxa_total_column: str
    taxa_execucao_column: str
    fee_relation: str
    fee_source_sign: str
    trading_fee_formula: str
    fee_contract_approved: bool
    funding_source_column: str | None
    funding_source_rule: str
    funding_contract_approved: bool
    net_pnl_column: str
    epsilon_abs_fonte: str


@dataclass(frozen=True)
class BitradexSourceProfile:
    schema_version: str
    profile_id: str
    batch_id: str
    source_files: BitradexSourceFiles
    identity: BitradexIdentityContract
    financial: BitradexFinancialContract
    profile_path: Path
    profile_sha256: str


@dataclass(frozen=True)
class SourceBundle:
    input_rows: tuple[dict[str, str], ...]
    retained_rows: tuple[dict[str, str], ...]
    excluded_rows: tuple[dict[str, str], ...]
    duplicate_groups: tuple[dict[str, str], ...]
    mapping_rows: tuple[dict[str, str], ...]
    mapping_by_source: dict[tuple[str, str], dict[str, str]]
    source_paths: dict[str, Path]
    hashes_before: dict[str, str]


@dataclass(frozen=True)
class PreparedRecord:
    source_row_index: int
    canonical_row: dict[str, Any] | None
    lineage: dict[str, Any]
    accounting: dict[str, Any]
    blockers: tuple[str, ...]


def load_bitradex_source_profile(path: str | Path) -> BitradexSourceProfile:
    profile_path = Path(path).resolve()
    if profile_path.is_symlink():
        raise BitradexProfileError("source_profile_symlink_forbidden")
    if profile_path.suffix.casefold() != ".json":
        raise BitradexProfileError("source_profile_extension_invalid")
    try:
        raw = profile_path.read_bytes()
        payload = json.loads(raw.decode("utf-8-sig"))
    except FileNotFoundError as exc:
        raise BitradexProfileError("source_profile_missing") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BitradexProfileError(f"source_profile_unreadable:{type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise BitradexProfileError("source_profile_root_invalid")

    source_files = _mapping(payload, "source_files")
    identity = _mapping(payload, "identity")
    financial = _mapping(payload, "financial_contract")
    profile = BitradexSourceProfile(
        schema_version=_text(payload, "schema_version"),
        profile_id=_text(payload, "profile_id"),
        batch_id=_text(payload, "batch_id"),
        source_files=BitradexSourceFiles(
            package_v4=_text(source_files, "package_v4"),
            package_v5=_text(source_files, "package_v5"),
            canonical_v4_csv=_text(source_files, "canonical_v4_csv"),
            excluded_duplicates_v5_csv=_text(source_files, "excluded_duplicates_v5_csv"),
            duplicate_groups_v5_csv=_text(source_files, "duplicate_groups_v5_csv"),
            synthetic_mapping_v5_csv=_text(source_files, "synthetic_mapping_v5_csv"),
        ),
        identity=BitradexIdentityContract(
            venue=_text(identity, "venue"),
            market_type=_text(identity, "market_type"),
            contract_type=_text(identity, "contract_type"),
            settlement_currency=_text(identity, "settlement_currency"),
            quantity_unit=_text(identity, "quantity_unit"),
            contract_size=_decimal_text(identity.get("contract_size"), "contract_size"),
            source_namespace=_text(identity, "source_namespace"),
            native_order_identity_policy=_text(identity, "native_order_identity_policy"),
        ),
        financial=BitradexFinancialContract(
            gross_pnl_formula=_text(financial, "gross_pnl_formula"),
            taxa_total_column=_text(financial, "taxa_total_column"),
            taxa_execucao_column=_text(financial, "taxa_execucao_column"),
            fee_relation=_text(financial, "fee_relation"),
            fee_source_sign=_text(financial, "fee_source_sign"),
            trading_fee_formula=_text(financial, "trading_fee_formula"),
            fee_contract_approved=_boolean(financial, "fee_contract_approved"),
            funding_source_column=_optional_text(financial.get("funding_source_column")),
            funding_source_rule=_text(financial, "funding_source_rule"),
            funding_contract_approved=_boolean(financial, "funding_contract_approved"),
            net_pnl_column=_text(financial, "net_pnl_column"),
            epsilon_abs_fonte=_decimal_text(
                financial.get("epsilon_abs_fonte"), "epsilon_abs_fonte"
            ),
        ),
        profile_path=profile_path,
        profile_sha256=hashlib.sha256(raw).hexdigest(),
    )
    _validate_profile(profile)
    return profile


def build_bitradex_ocr_readonly_adapter_report(
    *,
    project_root: str | Path,
    source_profile_path: str | Path = DEFAULT_PROFILE,
    account_scope_hash: str | None,
    package_v4: str | Path | None = None,
    package_v5: str | Path | None = None,
    trader_master_path: str | Path = DEFAULT_MASTER,
    write_report: bool = False,
    output_json: str | Path = DEFAULT_JSON_REPORT,
    output_markdown: str | Path = DEFAULT_MARKDOWN_REPORT,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    json_path = _resolve(root, output_json)
    markdown_path = _resolve(root, output_markdown)
    report = _base_report(
        source_profile_path=source_profile_path,
        trader_master_path=trader_master_path,
        write_report=write_report,
        output_json=json_path,
        output_markdown=markdown_path,
        generated_at_utc=generated_at_utc,
    )
    if write_report and not _reports_are_safe(root, json_path, markdown_path):
        return _finish(
            report,
            status="blocked",
            reason="unsafe_report_output_path",
            write_report=False,
            json_path=json_path,
            markdown_path=markdown_path,
        )

    try:
        profile = load_bitradex_source_profile(_resolve(root, source_profile_path))
    except BitradexProfileError as exc:
        report["validation_errors"] = [str(exc)]
        return _finish(
            report,
            status="blocked",
            reason="source_profile_invalid",
            write_report=write_report,
            json_path=json_path,
            markdown_path=markdown_path,
        )

    report.update(
        source_profile_id=profile.profile_id,
        source_profile_schema_version=profile.schema_version,
        source_profile_sha256=profile.profile_sha256,
        batch_id=profile.batch_id,
        fee_relation=profile.financial.fee_relation,
        fee_contract_approved=profile.financial.fee_contract_approved,
        funding_source_rule=profile.financial.funding_source_rule,
        funding_contract_approved=profile.financial.funding_contract_approved,
        epsilon_abs_fonte=profile.financial.epsilon_abs_fonte,
    )

    try:
        sources = _load_source_bundle(
            root=root,
            profile=profile,
            package_v4=package_v4,
            package_v5=package_v5,
        )
    except BitradexSourceError as exc:
        report["validation_errors"] = sorted(set(str(exc).split(";")))
        return _finish(
            report,
            status="blocked",
            reason="source_evidence_invalid",
            write_report=write_report,
            json_path=json_path,
            markdown_path=markdown_path,
        )

    report.update(
        input_rows=len(sources.input_rows),
        excluded_duplicate_rows=len(sources.excluded_rows),
        source_record_count=len(sources.retained_rows),
        duplicate_group_count=len(
            {row["duplicate_group_id"] for row in sources.duplicate_groups}
        ),
        duplicate_group_rows=len(sources.duplicate_groups),
        synthetic_mapping_rows=len(sources.mapping_rows),
        source_paths={key: str(path) for key, path in sources.source_paths.items()},
        source_hashes_before=sources.hashes_before,
    )

    account_hash = str(account_scope_hash or "").strip().casefold()
    if HEX64_RE.fullmatch(account_hash) is None:
        report["validation_errors"] = [
            "account_scope_hash_missing" if not account_hash else "account_scope_hash_invalid"
        ]
        return _finish(
            report,
            status="blocked",
            reason=report["validation_errors"][0],
            write_report=write_report,
            json_path=json_path,
            markdown_path=markdown_path,
        )

    prepared = [
        _prepare_record(index, row, sources.mapping_by_source, profile, account_hash)
        for index, row in enumerate(sources.retained_rows)
    ]
    canonical_prevalidation = [
        item.canonical_row for item in prepared if item.canonical_row is not None
    ]
    validation = validate_staging_records(
        canonical_prevalidation,
        source_file=str(sources.source_paths["canonical_v4_csv"]),
        source_sha256=sources.hashes_before["canonical_v4_csv"],
        ingestion_run_id=f"{profile.profile_id}:{sources.hashes_before['canonical_v4_csv'][:16]}",
    )
    report["staging_validation"] = validation

    accepted_canonical, staging_blocked_indices = _accepted_after_staging(
        canonical_prevalidation,
        validation,
    )
    accepted_prepared = [item for item in prepared if item.canonical_row is not None]
    accepted_pairs = [
        (item, canonical)
        for local_index, (item, canonical) in enumerate(
            zip(accepted_prepared, canonical_prevalidation, strict=True)
        )
        if local_index not in staging_blocked_indices
    ]
    accepted_canonical = [canonical for _, canonical in accepted_pairs]

    master_bundle = read_trader_master_readonly(
        project_root=root,
        trader_master_path=trader_master_path,
    )
    report["trader_master_read"] = master_bundle.report
    report.update(
        trader_master_row_count=master_bundle.report.get("trader_master_row_count", 0),
        master_canonical_record_count=len(master_bundle.canonical_records),
        master_unverifiable_row_count=len(master_bundle.unverifiable_rows),
        trader_master_temp_copy_used=master_bundle.report.get(
            "trader_master_temp_copy_used", False
        ),
        trader_master_sha256_before=master_bundle.report.get("trader_master_sha256_before"),
        trader_master_sha256_after=master_bundle.report.get("trader_master_sha256_after"),
        trader_master_hash_preserved=master_bundle.report.get(
            "trader_master_hash_preserved", False
        ),
    )

    reconciliation: dict[str, Any] = {}
    reconciled_by_source: dict[int, dict[str, Any]] = {}
    if master_bundle.report.get("status") == "ok" and accepted_canonical:
        reconciliation = reconcile_canonical_records(
            accepted_canonical,
            master_bundle.canonical_records,
            master_bundle.unverifiable_rows,
        )
        for pair, result in zip(
            accepted_pairs,
            reconciliation["reconciliation_results"],
            strict=True,
        ):
            reconciled_by_source[pair[0].source_row_index] = result
    report["reconciliation"] = reconciliation

    record_results = _record_results(
        prepared=prepared,
        staging_blocked_indices=staging_blocked_indices,
        accepted_prepared=accepted_prepared,
        reconciled_by_source=reconciled_by_source,
        master_available=master_bundle.report.get("status") == "ok",
    )
    counts = Counter(row["classification"] for row in record_results)
    classification_counts = {name: counts[name] for name in CLASSIFICATIONS}
    report.update(
        source_record_count=len(record_results),
        canonical_trade_id_count=sum(bool(row["canonical_trade_id"]) for row in record_results),
        raw_ocr_order_id_lineage_count=sum(
            row["raw_ocr_order_id"] not in {None, ""} for row in record_results
        ),
        synthetic_identity_usage_count=0,
        order_id_non_null_count=sum(row["order_id"] is not None for row in record_results),
        source_trade_id_non_null_count=sum(
            row["source_trade_id"] is not None for row in record_results
        ),
        classification_counts=classification_counts,
        record_results=record_results,
        master_reconciliation_decision=(
            reconciliation_decision(reconciliation) if reconciliation else None
        ),
    )

    hashes_after = {key: file_sha256(path) for key, path in sources.source_paths.items()}
    report["source_hashes_after"] = hashes_after
    report["source_hashes_preserved"] = hashes_after == sources.hashes_before
    if not report["source_hashes_preserved"]:
        report["validation_errors"] = ["source_evidence_changed_during_read"]
        status, reason = "blocked", "source_evidence_changed_during_read"
    elif classification_counts["ACCOUNTING_CONTRACT_BLOCKED"]:
        status, reason = "blocked", "accounting_contract_blocked"
    elif classification_counts["MASTER_COMPARISON_UNAVAILABLE"]:
        status, reason = "blocked", "master_comparison_unavailable"
    elif classification_counts["PRIMARY_IDENTITY_CONFLICT"]:
        status, reason = "blocked", "primary_identity_conflict"
    elif classification_counts["LEGACY_OVERLAP_AMBIGUOUS"]:
        status, reason = "blocked", "legacy_overlap_ambiguous"
    else:
        status, reason = "ok", "readonly_reconciliation_completed"

    return _finish(
        report,
        status=status,
        reason=reason,
        write_report=write_report,
        json_path=json_path,
        markdown_path=markdown_path,
    )


def _load_source_bundle(
    *,
    root: Path,
    profile: BitradexSourceProfile,
    package_v4: str | Path | None,
    package_v5: str | Path | None,
) -> SourceBundle:
    v4_dir = _resolve(root, package_v4 or profile.source_files.package_v4)
    v5_dir = _resolve(root, package_v5 or profile.source_files.package_v5)
    paths = {
        "canonical_v4_csv": v4_dir / profile.source_files.canonical_v4_csv,
        "excluded_duplicates_v5_csv": v5_dir
        / profile.source_files.excluded_duplicates_v5_csv,
        "duplicate_groups_v5_csv": v5_dir / profile.source_files.duplicate_groups_v5_csv,
        "synthetic_mapping_v5_csv": v5_dir / profile.source_files.synthetic_mapping_v5_csv,
    }
    for name, path in paths.items():
        _validate_csv_source(path, name)

    input_rows = _read_csv(paths["canonical_v4_csv"])
    exclusions = _read_csv(paths["excluded_duplicates_v5_csv"])
    groups = _read_csv(paths["duplicate_groups_v5_csv"])
    mapping = _read_csv(paths["synthetic_mapping_v5_csv"])
    _require_columns(input_rows, REQUIRED_V4_COLUMNS, "canonical_v4")
    _require_columns(exclusions, REQUIRED_EXCLUSION_COLUMNS, "excluded_duplicates_v5")
    _require_columns(groups, REQUIRED_GROUP_COLUMNS, "duplicate_groups_v5")
    _require_columns(mapping, REQUIRED_MAPPING_COLUMNS, "synthetic_mapping_v5")

    exclusion_keys = [
        (
            row["excluded_source_file_name"].strip(),
            row["excluded_source_sha256"].strip().casefold(),
        )
        for row in exclusions
    ]
    if len(exclusion_keys) != len(set(exclusion_keys)):
        raise BitradexSourceError("duplicate_exclusion_evidence")
    if any(row["exclusion_reason"] != "DUPLICATE_REAL_TRADE_EXCLUDED" for row in exclusions):
        raise BitradexSourceError("unsupported_exclusion_reason")

    input_counter = Counter(_source_key(row) for row in input_rows)
    if any(input_counter[key] != 1 for key in exclusion_keys):
        raise BitradexSourceError("excluded_source_not_unique_in_v4")
    retained = [row for row in input_rows if _source_key(row) not in set(exclusion_keys)]

    group_excluded = {
        _source_key(row) for row in groups if row["disposition"].strip().upper() == "EXCLUDED"
    }
    if group_excluded != set(exclusion_keys):
        raise BitradexSourceError("duplicate_group_exclusions_mismatch")
    group_counts = Counter(row["duplicate_group_id"] for row in groups)
    if any(count < 2 for count in group_counts.values()):
        raise BitradexSourceError("duplicate_group_without_multiple_members")

    mapping_by_source: dict[tuple[str, str], dict[str, str]] = {}
    for row in mapping:
        key = _source_key(row)
        if key in mapping_by_source:
            raise BitradexSourceError("duplicate_mapping_source_identity")
        mapping_by_source[key] = row
    retained_keys = {_source_key(row) for row in retained}
    if set(mapping_by_source) != retained_keys:
        raise BitradexSourceError("mapping_retained_source_mismatch")
    for row in mapping:
        synthetic_id = row["synthetic_order_id"].strip().casefold()
        if re.fullmatch(r"[0-9a-f]{24}", synthetic_id) is None:
            raise BitradexSourceError("mapping_synthetic_order_id_invalid")

    hashes = {key: file_sha256(path) for key, path in paths.items()}
    return SourceBundle(
        input_rows=tuple(input_rows),
        retained_rows=tuple(retained),
        excluded_rows=tuple(exclusions),
        duplicate_groups=tuple(groups),
        mapping_rows=tuple(mapping),
        mapping_by_source=mapping_by_source,
        source_paths=paths,
        hashes_before=hashes,
    )


def _prepare_record(
    source_row_index: int,
    row: Mapping[str, str],
    mapping_by_source: Mapping[tuple[str, str], Mapping[str, str]],
    profile: BitradexSourceProfile,
    account_scope_hash: str,
) -> PreparedRecord:
    mapping = mapping_by_source[_source_key(row)]
    lineage = {
        "source_row_index": source_row_index,
        "source_file_name": row["source_file_name"].strip(),
        "source_image_path": row["source_image_path"].strip(),
        "source_sha256": row["source_sha256"].strip().casefold(),
        "raw_ocr_order_id": row.get("order_id", "").strip() or None,
        "synthetic_order_id_evidence": mapping["synthetic_order_id"].strip().casefold(),
        "synthetic_id_used_as_native_identity": False,
    }
    blockers: list[str] = []
    accounting: dict[str, Any] = {
        "taxa_total_source": row.get(profile.financial.taxa_total_column),
        "taxa_execucao_source": row.get(profile.financial.taxa_execucao_column),
        "fee_relation": profile.financial.fee_relation,
        "funding_source_rule": profile.financial.funding_source_rule,
    }
    required = (
        "symbol",
        "position_side",
        "entry_price",
        "exit_price",
        "closed_volume",
        "open_time_utc",
        "close_time_utc",
        profile.financial.net_pnl_column,
        profile.financial.taxa_total_column,
        profile.financial.taxa_execucao_column,
    )
    missing = [name for name in required if not str(row.get(name, "")).strip()]
    if missing:
        blockers.extend(f"missing_required_source_field:{name}" for name in missing)

    try:
        entry = _decimal(row.get("entry_price"), "entry_price")
        exit_price = _decimal(row.get("exit_price"), "exit_price")
        quantity = _decimal(row.get("closed_volume"), "closed_volume")
        contract_size = _decimal(profile.identity.contract_size, "contract_size")
        net_pnl = _decimal(
            row.get(profile.financial.net_pnl_column), profile.financial.net_pnl_column
        )
        taxa_total = _decimal(
            row.get(profile.financial.taxa_total_column),
            profile.financial.taxa_total_column,
        )
        taxa_execucao = _decimal(
            row.get(profile.financial.taxa_execucao_column),
            profile.financial.taxa_execucao_column,
        )
        side = _side(row.get("position_side"))
    except BitradexSourceError as exc:
        blockers.extend(str(exc).split(";"))
        return PreparedRecord(source_row_index, None, lineage, accounting, tuple(sorted(set(blockers))))

    gross_pnl = (
        (exit_price - entry) * quantity * contract_size
        if side == "long"
        else (entry - exit_price) * quantity * contract_size
    )
    if not profile.financial.fee_contract_approved:
        blockers.append("fee_contract_not_approved")
    if profile.financial.fee_relation != "distinct_additive_negative_cost_components":
        blockers.append("fee_relation_not_approved")
    if profile.financial.fee_source_sign != "negative_cost":
        blockers.append("fee_source_sign_not_approved")
    if taxa_total > 0 or taxa_execucao > 0:
        blockers.append("fee_source_sign_violation")
    trading_fee = abs(taxa_total) + abs(taxa_execucao)

    funding_fee: Decimal | None = None
    if not profile.financial.funding_contract_approved:
        blockers.append("funding_contract_not_approved")
    elif not profile.financial.funding_source_column:
        blockers.append("funding_source_column_missing")
    elif not str(row.get(profile.financial.funding_source_column, "")).strip():
        blockers.append("funding_fee_source_evidence_missing")
    else:
        try:
            funding_fee = _decimal(
                row.get(profile.financial.funding_source_column),
                profile.financial.funding_source_column,
            )
        except BitradexSourceError as exc:
            blockers.extend(str(exc).split(";"))

    epsilon = _decimal(profile.financial.epsilon_abs_fonte, "epsilon_abs_fonte")
    accounting.update(
        gross_pnl=_format_decimal(gross_pnl),
        trading_fee=_format_decimal(trading_fee),
        funding_fee=_format_decimal(funding_fee) if funding_fee is not None else None,
        net_pnl=_format_decimal(net_pnl),
        epsilon_abs_fonte=_format_decimal(epsilon),
    )
    if funding_fee is not None:
        residual = abs(net_pnl - (gross_pnl - trading_fee - funding_fee))
        accounting["accounting_residual"] = _format_decimal(residual)
        if residual > epsilon:
            blockers.append("financial_accounting_identity_violation")
    else:
        accounting["accounting_residual"] = None

    if blockers:
        return PreparedRecord(source_row_index, None, lineage, accounting, tuple(sorted(set(blockers))))

    canonical_row = {
        "venue": profile.identity.venue,
        "market_type": profile.identity.market_type,
        "contract_type": profile.identity.contract_type,
        "settlement_currency": profile.identity.settlement_currency,
        "quantity_unit": profile.identity.quantity_unit,
        "contract_size": profile.identity.contract_size,
        "account_scope_hash": account_scope_hash,
        "order_id_namespace": None,
        "source_trade_id": None,
        "order_id": None,
        "source": profile.identity.source_namespace,
        "symbol": row["symbol"],
        "side": side,
        "open_time": row["open_time_utc"],
        "close_time": row["close_time_utc"],
        "entry_price": _format_decimal(entry),
        "exit_price": _format_decimal(exit_price),
        "quantity": _format_decimal(quantity),
        "gross_pnl": _format_decimal(gross_pnl),
        "trading_fee": _format_decimal(trading_fee),
        "funding_fee": _format_decimal(funding_fee),
        "net_pnl": _format_decimal(net_pnl),
        "epsilon_abs_fonte": _format_decimal(epsilon),
    }
    return PreparedRecord(source_row_index, canonical_row, lineage, accounting, ())


def _accepted_after_staging(
    records: Sequence[dict[str, Any]],
    validation: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], set[int]]:
    blocked_indices = {
        int(row["source_row_index"])
        for row in validation.get("row_results", [])
        if row.get("status") != "accepted"
    }
    accepted = [record for index, record in enumerate(records) if index not in blocked_indices]
    return accepted, blocked_indices


def _record_results(
    *,
    prepared: Sequence[PreparedRecord],
    staging_blocked_indices: set[int],
    accepted_prepared: Sequence[PreparedRecord],
    reconciled_by_source: Mapping[int, Mapping[str, Any]],
    master_available: bool,
) -> list[dict[str, Any]]:
    staging_local_index = {
        item.source_row_index: index for index, item in enumerate(accepted_prepared)
    }
    results: list[dict[str, Any]] = []
    for item in prepared:
        canonical_id: str | None = None
        row_fingerprint: str | None = None
        classification: str
        reasons = list(item.blockers)
        if item.canonical_row is None:
            classification = "ACCOUNTING_CONTRACT_BLOCKED"
        elif staging_local_index[item.source_row_index] in staging_blocked_indices:
            classification = "ACCOUNTING_CONTRACT_BLOCKED"
            reasons.append("staging_validator_rejected")
        else:
            normalized = normalize_trade_row(item.canonical_row)
            row_fingerprint = row_fingerprint_for(normalized)
            canonical_id = canonical_trade_id_for(normalized, row_fingerprint=row_fingerprint)
            if not master_available:
                classification = "MASTER_COMPARISON_UNAVAILABLE"
                reasons.append("trader_master_read_unavailable")
            else:
                reconciliation = reconciled_by_source.get(item.source_row_index)
                if reconciliation is None:
                    classification = "MASTER_COMPARISON_UNAVAILABLE"
                    reasons.append("reconciliation_result_missing")
                else:
                    classification = _translate_classification(
                        str(reconciliation["classification"])
                    )
                    reasons.extend(str(value) for value in reconciliation.get("reasons", []))
        results.append(
            {
                **item.lineage,
                "classification": classification,
                "reasons": sorted(set(reasons)),
                "order_id": None,
                "source_trade_id": None,
                "canonical_trade_id": canonical_id,
                "row_fingerprint": row_fingerprint,
                "accounting": item.accounting,
                "import_eligible": False,
            }
        )
    return results


def _translate_classification(value: str) -> str:
    mapping = {
        "new_trade_candidate": "VERIFIED_NOVEL",
        "exact_fingerprint_duplicate": "VERIFIED_EXISTING",
        "primary_identity_exact_duplicate": "VERIFIED_EXISTING",
        "primary_identity_financial_conflict": "PRIMARY_IDENTITY_CONFLICT",
        "duplicate_master_primary_identity": "PRIMARY_IDENTITY_CONFLICT",
        "ambiguous_legacy_identity_match": "LEGACY_OVERLAP_AMBIGUOUS",
        "incoming_blocked_by_unverifiable_master": "LEGACY_OVERLAP_AMBIGUOUS",
        "incoming_row_unverifiable": "MASTER_COMPARISON_UNAVAILABLE",
        "observed_fingerprint_collision": "MASTER_COMPARISON_UNAVAILABLE",
    }
    return mapping.get(value, "MASTER_COMPARISON_UNAVAILABLE")


def _validate_profile(profile: BitradexSourceProfile) -> None:
    errors: list[str] = []
    if profile.schema_version != PROFILE_SCHEMA_VERSION:
        errors.append("unsupported_source_profile_schema_version")
    if profile.identity.native_order_identity_policy != (
        "raw_ocr_evidence_only_no_native_identity"
    ):
        errors.append("native_order_identity_policy_unsafe")
    if profile.financial.gross_pnl_formula != (
        "long=(exit-entry)*quantity*contract_size;"
        "short=(entry-exit)*quantity*contract_size"
    ):
        errors.append("gross_pnl_formula_not_approved")
    if profile.financial.trading_fee_formula != (
        "abs(taxa_total)+abs(taxa_execucao)"
    ):
        errors.append("trading_fee_formula_not_approved")
    if _decimal(profile.identity.contract_size, "contract_size") <= 0:
        errors.append("contract_size_not_positive")
    if _decimal(profile.financial.epsilon_abs_fonte, "epsilon_abs_fonte") < 0:
        errors.append("epsilon_abs_fonte_negative")
    if errors:
        raise BitradexProfileError(";".join(sorted(set(errors))))


def _validate_csv_source(path: Path, label: str) -> None:
    if path.suffix.casefold() != ".csv":
        raise BitradexSourceError(f"{label}_extension_invalid")
    if path.is_symlink():
        raise BitradexSourceError(f"{label}_symlink_forbidden")
    if not path.exists() or not path.is_file():
        raise BitradexSourceError(f"{label}_missing")


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise BitradexSourceError(f"csv_header_missing:{path.name}")
            if len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise BitradexSourceError(f"csv_duplicate_columns:{path.name}")
            return [
                {str(key): "" if value is None else str(value) for key, value in row.items()}
                for row in reader
            ]
    except UnicodeError as exc:
        raise BitradexSourceError(f"csv_encoding_invalid:{path.name}") from exc
    except OSError as exc:
        raise BitradexSourceError(f"csv_unreadable:{path.name}:{type(exc).__name__}") from exc


def _require_columns(
    rows: Sequence[Mapping[str, str]],
    required: Sequence[str],
    label: str,
) -> None:
    if not rows:
        raise BitradexSourceError(f"{label}_empty")
    missing = sorted(set(required) - set(rows[0]))
    if missing:
        raise BitradexSourceError(f"{label}_missing_columns:{','.join(missing)}")


def _source_key(row: Mapping[str, str]) -> tuple[str, str]:
    name = str(row.get("source_file_name", row.get("excluded_source_file_name", ""))).strip()
    sha = str(row.get("source_sha256", row.get("excluded_source_sha256", ""))).strip().casefold()
    if not name or HEX64_RE.fullmatch(sha) is None:
        raise BitradexSourceError("source_provenance_invalid")
    return name, sha


def _decimal(value: Any, field: str) -> Decimal:
    text = str(value if value is not None else "").strip().replace(",", ".")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise BitradexSourceError(f"invalid_decimal:{field}") from exc
    if not number.is_finite():
        raise BitradexSourceError(f"non_finite_decimal:{field}")
    return number


def _format_decimal(value: Decimal | None) -> str:
    if value is None:
        raise BitradexSourceError("decimal_value_missing")
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _side(value: Any) -> str:
    side = str(value or "").strip().casefold()
    if side not in {"long", "short"}:
        raise BitradexSourceError("invalid_position_side")
    return side


def _mapping(payload: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = payload.get(field)
    if not isinstance(value, Mapping):
        raise BitradexProfileError(f"source_profile_mapping_required:{field}")
    return value


def _text(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise BitradexProfileError(f"source_profile_string_required:{field}")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise BitradexProfileError("source_profile_optional_string_invalid")
    return value.strip()


def _boolean(payload: Mapping[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise BitradexProfileError(f"source_profile_boolean_required:{field}")
    return value


def _decimal_text(value: Any, field: str) -> str:
    try:
        return _format_decimal(_decimal(value, field))
    except BitradexSourceError as exc:
        raise BitradexProfileError(str(exc)) from exc


def _base_report(
    *,
    source_profile_path: str | Path,
    trader_master_path: str | Path,
    write_report: bool,
    output_json: Path,
    output_markdown: Path,
    generated_at_utc: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked",
        "reason": "not_evaluated",
        "decision": "READ_ONLY_NO_IMPORT_AUTHORITY",
        "generated_at_utc": generated_at_utc or datetime.now(UTC).isoformat(),
        "fingerprint_spec_version": FINGERPRINT_SPEC_VERSION,
        "source_profile_path": str(source_profile_path),
        "trader_master_path": str(trader_master_path),
        "input_rows": 0,
        "excluded_duplicate_rows": 0,
        "source_record_count": 0,
        "canonical_trade_id_count": 0,
        "classification_counts": {name: 0 for name in CLASSIFICATIONS},
        "validation_errors": [],
        "write_requested": write_report,
        "write_performed": False,
        "output_json": str(output_json),
        "output_markdown": str(output_markdown),
        "safety": dict(SAFETY_FLAGS),
        **SAFETY_FLAGS,
    }


def _finish(
    report: dict[str, Any],
    *,
    status: str,
    reason: str,
    write_report: bool,
    json_path: Path,
    markdown_path: Path,
) -> dict[str, Any]:
    report.update(status=status, reason=reason)
    if write_report:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(
            json_path,
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, default=str)
            + "\n",
        )
        _atomic_write_text(markdown_path, render_markdown(report))
        report["write_performed"] = True
        _atomic_write_text(
            json_path,
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, default=str)
            + "\n",
        )
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    counts = report.get("classification_counts", {})
    lines = [
        "# Bitradex OCR Batch Read-only Adapter V2",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Reason: `{report.get('reason')}`",
        f"- Input rows: `{report.get('input_rows', 0)}`",
        f"- Excluded duplicate rows: `{report.get('excluded_duplicate_rows', 0)}`",
        f"- Source records: `{report.get('source_record_count', 0)}`",
        f"- Canonical trade IDs: `{report.get('canonical_trade_id_count', 0)}`",
        f"- Master canonical records: `{report.get('master_canonical_record_count', 0)}`",
        f"- Master unverifiable rows: `{report.get('master_unverifiable_row_count', 0)}`",
        "",
        "## Classifications",
        "",
    ]
    for name in CLASSIFICATIONS:
        lines.append(f"- {name}: `{counts.get(name, 0)}`")
    lines.extend(
        [
            "",
            "The OCR and synthetic order IDs are lineage evidence only. They never become",
            "`order_id` or `source_trade_id`. This report has no import authority.",
            "",
        ]
    )
    return "\n".join(lines)


def _reports_are_safe(root: Path, json_path: Path, markdown_path: Path) -> bool:
    reports = (root / "data" / "reports").resolve()
    for path, suffix in ((json_path, ".json"), (markdown_path, ".md")):
        if path.suffix.casefold() != suffix or path.is_symlink():
            return False
        try:
            path.resolve().relative_to(reports)
        except ValueError:
            return False
    return True


def _atomic_write_text(path: Path, content: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()
