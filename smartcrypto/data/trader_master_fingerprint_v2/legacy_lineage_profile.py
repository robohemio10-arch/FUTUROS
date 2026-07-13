"""Read-only lineage and adaptability profile for the legacy Trader Master."""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .fingerprint_spec import (
    FINGERPRINT_FIELD_ORDER,
    FINGERPRINT_SPEC_VERSION,
    FIELD_RULES,
    HEX_SHA256,
    FingerprintValidationError,
    Sha256Hasher,
    decimal_from_value,
    is_null,
    normalize_decimal,
    normalize_text,
    normalize_timestamp,
    normalize_trade_row,
    row_fingerprint_for,
    sha256_hex,
)
from .freqtrade_adapter import (
    FreqtradePaperAdapterBundle,
    build_freqtrade_paper_closed_trades_adapter_bundle,
)
from .master_adapter import (
    AfterReadHook,
    DIRECT_MAPPING_ALIASES,
    MasterReadBundle,
    file_sha256,
    map_master_row,
    read_trader_master_readonly,
)
from .source_profile import SourceProfileError, load_source_profile


SCHEMA_VERSION = "trader_master_legacy_lineage_profile_v2"
LEGACY_OBSERVATION_KEY_VERSION = "legacy_observation_key_v1"
DEFAULT_MASTER = Path("data/trades/trades_master.parquet")
DEFAULT_JSON_REPORT = Path("data/reports/trader_master_legacy_lineage_profile_v2.json")
DEFAULT_MARKDOWN_REPORT = Path("data/reports/trader_master_legacy_lineage_profile_v2.md")

LINEAGE_CLASSIFICATIONS = frozenset(
    {
        "direct_authoritative_column",
        "direct_column_null",
        "direct_column_invalid",
        "deterministic_derivation_available",
        "versioned_source_contract_required",
        "external_authoritative_evidence_required",
        "mathematically_underdetermined",
        "conflicting_source_evidence",
        "unavailable",
    }
)
ROW_CLASSIFICATIONS = (
    "v2_directly_verifiable",
    "v2_deterministically_adaptable",
    "conditionally_adaptable_with_versioned_source_contract",
    "blocked_by_native_identity_lineage",
    "blocked_by_financial_decomposition",
    "blocked_by_multiple_lineage_gaps",
    "irreducibly_unverifiable",
)
FINANCIAL_CLASSIFICATIONS = (
    "financial_identity_fully_verifiable",
    "gross_pnl_reconstructable_but_costs_missing",
    "net_pnl_only_decomposition_underdetermined",
    "price_quantity_inputs_incomplete",
    "financial_fields_conflicting",
    "financial_identity_unverifiable",
)
OVERLAP_CLASSIFICATIONS = (
    "unique_exact_legacy_overlap_candidate",
    "multiple_exact_legacy_overlap_candidate",
    "no_exact_legacy_overlap_observed",
    "legacy_overlap_unverifiable",
)
OBSERVATION_FIELDS = (
    "symbol",
    "side",
    "open_time",
    "close_time",
    "entry_price",
    "exit_price",
    "quantity",
    "net_pnl",
)
CONTRACT_REQUIRED_FIELDS = frozenset(
    {
        "market_type",
        "contract_type",
        "settlement_currency",
        "quantity_unit",
        "order_id_namespace",
        "epsilon_abs_fonte",
    }
)
EXTERNAL_EVIDENCE_FIELDS = frozenset(
    {"venue", "contract_size", "account_scope_hash"}
)
UNDERDETERMINED_FIELDS = frozenset({"gross_pnl", "trading_fee", "funding_fee"})
NORMALIZABLE_FIELDS = frozenset(
    {
        "symbol",
        "side",
        "open_time",
        "close_time",
        "entry_price",
        "exit_price",
        "quantity",
        "gross_pnl",
        "trading_fee",
        "funding_fee",
        "net_pnl",
        "contract_size",
        "epsilon_abs_fonte",
    }
)
SENSITIVE_SAMPLE_FIELDS = frozenset(
    {"account_scope_hash", "order_id", "source_trade_id", "order_id_namespace"}
)
SOURCE_COHORT_COLUMNS = ("source_file", "source", "exchange_source")
ORDER_ID_FREQTRADE_PATTERN = re.compile(r"^freqtrade-paper-[1-9][0-9]*$")
ORDER_ID_NUMERIC_PATTERN = re.compile(r"^[0-9]+(?:\.0)?$")
FIELD_RULES_BY_NAME = {rule.name: rule for rule in FIELD_RULES}

AdapterBuilder = Callable[..., FreqtradePaperAdapterBundle]
MasterReader = Callable[..., MasterReadBundle]
ArtifactSnapshotter = Callable[[Sequence[Path], Path], dict[str, dict[str, Any]]]

SAFETY_FLAGS: dict[str, bool] = {
    "preview_only": True,
    "writes_trader_master": False,
    "writes_parquet": False,
    "writes_xlsx": False,
    "writes_csv": False,
    "writes_sqlite": False,
    "changes_fingerprint_spec": False,
    "import_performed": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "changes_model": False,
    "writes_runtime": False,
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
}


def build_trader_master_legacy_lineage_profile_report(
    *,
    project_root: str | Path,
    trader_master_path: str | Path = DEFAULT_MASTER,
    source_profile_path: str | Path,
    account_scope_hash: str | None,
    authoritative_sqlite_path: str | Path | None = None,
    write_report: bool = False,
    output_json: str | Path = DEFAULT_JSON_REPORT,
    output_markdown: str | Path = DEFAULT_MARKDOWN_REPORT,
    generated_at_utc: str | None = None,
    row_hasher: Sha256Hasher = sha256_hex,
    master_reader: MasterReader = read_trader_master_readonly,
    adapter_builder: AdapterBuilder = build_freqtrade_paper_closed_trades_adapter_bundle,
    artifact_snapshotter: ArtifactSnapshotter | None = None,
    after_master_read_hook: AfterReadHook | None = None,
    after_overlap_hook: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Profile legacy lineage without adapting, fingerprinting, or importing rows."""

    root = Path(project_root).resolve()
    json_path = _resolve(root, output_json)
    markdown_path = _resolve(root, output_markdown)
    report = _base_report(
        root=root,
        trader_master_path=trader_master_path,
        source_profile_path=source_profile_path,
        write_report=write_report,
        json_path=json_path,
        markdown_path=markdown_path,
        generated_at_utc=generated_at_utc,
    )
    output_errors = _validate_report_paths(root, json_path, markdown_path) if write_report else []
    if output_errors:
        return _blocked(report, "unsafe_report_output_path", output_errors)

    try:
        source_profile = load_source_profile(_resolve(root, source_profile_path))
    except SourceProfileError as exc:
        return _blocked(report, "source_profile_invalid", str(exc).split(";"))

    normalized_account_hash = (account_scope_hash or "").strip().casefold()
    if not normalized_account_hash:
        return _blocked(report, "account_scope_hash_missing")
    if HEX_SHA256.fullmatch(normalized_account_hash) is None:
        return _blocked(report, "account_scope_hash_invalid")

    paper_artifacts = _paper_artifact_paths(
        root,
        source_profile.primary_source_path,
        source_profile.replica_source_paths,
        authoritative_sqlite_path or source_profile.authoritative_sqlite.snapshot_path,
    )
    snapshotter = artifact_snapshotter or snapshot_artifacts
    paper_hashes_before = snapshotter(paper_artifacts, root)

    master_bundle = master_reader(
        project_root=root,
        trader_master_path=trader_master_path,
        after_read_hook=after_master_read_hook,
    )
    report.update(master_bundle.report)
    if master_bundle.report.get("status") != "ok":
        return _blocked(
            report,
            str(master_bundle.report.get("reason", "trader_master_unreadable")),
        )
    if len(master_bundle.source_rows) != int(
        master_bundle.report.get("trader_master_row_count", -1)
    ):
        return _blocked(report, "trader_master_source_rows_incomplete")

    paper_bundle = adapter_builder(
        project_root=root,
        source_profile_path=source_profile_path,
        account_scope_hash=normalized_account_hash,
        authoritative_sqlite_path=authoritative_sqlite_path,
        apply_authoritative_forensic_recovery=True,
    )
    if not _paper_bundle_is_profileable(paper_bundle):
        errors = list(paper_bundle.report.get("blockers", []))
        return _blocked(
            report,
            "paper_adapter_not_profileable",
            errors or [str(paper_bundle.report.get("reason", "paper_adapter_not_profileable"))],
        )

    rows = list(master_bundle.source_rows)
    field_profile = build_field_lineage_profile(rows)
    row_profiles = [profile_legacy_master_row(index, row) for index, row in enumerate(rows)]
    cohort_profiles = build_source_cohort_profiles(rows, row_profiles)
    overlap = build_legacy_overlap_profile(
        rows,
        paper_bundle.accepted_canonical_records,
        row_hasher=row_hasher,
    )
    if after_overlap_hook is not None:
        after_overlap_hook()
    paper_hashes_after = snapshotter(paper_artifacts, root)
    paper_hashes_preserved = paper_hashes_before == paper_hashes_after
    if not paper_hashes_preserved:
        report.update(
            paper_artifact_hashes_before=paper_hashes_before,
            paper_artifact_hashes_after=paper_hashes_after,
            paper_batch_hashes_preserved=False,
        )
        return _blocked(report, "paper_batch_changed_during_overlap")

    row_counts = Counter(str(item["final_adaptability_classification"]) for item in row_profiles)
    financial_counts = Counter(str(item["financial_classification"]) for item in row_profiles)
    financial_count_fields = {
        f"{name}_count": (
            len(rows) - int(financial_counts["financial_identity_fully_verifiable"])
            if name == "financial_identity_unverifiable"
            else int(financial_counts[name])
        )
        for name in FINANCIAL_CLASSIFICATIONS
    }
    versioned_improvable = sum(bool(item["contract_required_fields"]) for item in row_profiles)
    external_required = sum(
        bool(item["external_evidence_required_fields"]) for item in row_profiles
    )
    fingerprint_allowed = sum(bool(item["fingerprint_generation_allowed"]) for item in row_profiles)
    decision = lineage_decision(row_counts, financial_counts, external_required, len(rows))
    report.update(
        status="ok",
        reason="legacy_lineage_profile_completed",
        decision=decision,
        account_scope_hash_present=True,
        account_scope_hash_valid=True,
        account_scope_original_identifier_persisted=False,
        field_lineage_profile=field_profile,
        source_cohort_count=len(cohort_profiles),
        source_cohort_profiles=cohort_profiles,
        row_profiles=row_profiles,
        row_profile_count=len(row_profiles),
        **{f"{name}_count": int(row_counts[name]) for name in ROW_CLASSIFICATIONS},
        **financial_count_fields,
        **overlap,
        paper_raw_row_count=int(paper_bundle.report.get("raw_row_count", 0)),
        paper_accepted_row_count=len(paper_bundle.accepted_canonical_records),
        paper_quarantined_row_count=int(
            paper_bundle.report.get("quarantined_row_count", 0)
        ),
        paper_artifact_hashes_before=paper_hashes_before,
        paper_artifact_hashes_after=paper_hashes_after,
        paper_batch_hashes_preserved=True,
        versioned_contract_could_improve_row_count=versioned_improvable,
        external_evidence_still_required_row_count=external_required,
        fingerprint_generation_allowed_count=fingerprint_allowed,
        import_eligible_true_count=0,
        recommended_next_action=_recommended_next_action(decision),
        validation_errors=[],
        blockers=[],
        warnings=_profile_warnings(field_profile, row_counts),
        **SAFETY_FLAGS,
        safety_flags=dict(SAFETY_FLAGS),
    )
    return _maybe_write(report, write_report, json_path, markdown_path)


def build_field_lineage_profile(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    columns = list(dict.fromkeys(str(column) for row in rows for column in row))
    return [_profile_field(field, rows, columns) for field in FINGERPRINT_FIELD_ORDER]


def profile_legacy_master_row(row_index: int, row: Mapping[str, Any]) -> dict[str, Any]:
    candidate, mapping = map_master_row(row)
    normalized_values: dict[str, str | None] = {}
    invalid_fields: list[str] = []
    missing_fields: list[str] = []
    deterministic_fields: list[str] = []
    for field in FINGERPRINT_FIELD_ORDER:
        source_column = mapping.get(field)
        if source_column is None or is_null(candidate.get(field)):
            normalized_values[field] = None
            if _field_required_for_row(field, candidate):
                missing_fields.append(field)
            continue
        try:
            normalized = _normalize_field(field, candidate[field])
        except FingerprintValidationError:
            normalized = None
            invalid_fields.append(field)
        normalized_values[field] = normalized
        if normalized is not None and _is_deterministic_mapping(field, source_column, candidate[field], normalized):
            deterministic_fields.append(field)

    financial = classify_financial_lineage(normalized_values)
    if financial["gross_pnl_reconstructable"] and normalized_values.get("gross_pnl") is None:
        deterministic_fields.append("gross_pnl")
    contract_fields = sorted(
        field for field in missing_fields if field in CONTRACT_REQUIRED_FIELDS
    )
    external_fields = sorted(
        field for field in missing_fields if field in EXTERNAL_EVIDENCE_FIELDS
    )
    if "contract_size" in invalid_fields and "contract_size" not in external_fields:
        external_fields.append("contract_size")
    financial_gap_fields = sorted(
        field
        for field in set(missing_fields + invalid_fields)
        if field in UNDERDETERMINED_FIELDS
    )
    native_identity_classification = _native_identity_classification(
        normalized_values,
        contract_fields,
        external_fields,
        invalid_fields,
    )
    full_normalizable = False
    if not missing_fields and not invalid_fields:
        try:
            normalized_row = normalize_trade_row(candidate)
            row_fingerprint_for(normalized_row)
            full_normalizable = True
        except FingerprintValidationError:
            full_normalizable = False
    final_classification = _row_classification(
        full_normalizable=full_normalizable,
        deterministic_fields=deterministic_fields,
        contract_fields=contract_fields,
        external_fields=external_fields,
        invalid_fields=invalid_fields,
        financial_gap_fields=financial_gap_fields,
        financial_classification=str(financial["financial_classification"]),
        native_identity_classification=native_identity_classification,
    )
    fingerprint_allowed = final_classification in {
        "v2_directly_verifiable",
        "v2_deterministically_adaptable",
    } and full_normalizable
    return {
        "row_index": row_index,
        "source_cohort": _cohort_values(row),
        "mapped_fields": dict(sorted(mapping.items())),
        "missing_v2_fields": sorted(set(missing_fields)),
        "invalid_v2_fields": sorted(set(invalid_fields)),
        "deterministic_fields": sorted(set(deterministic_fields)),
        "contract_required_fields": contract_fields,
        "external_evidence_required_fields": sorted(set(external_fields)),
        "financial_decomposition_gap_fields": financial_gap_fields,
        "financial_classification": financial["financial_classification"],
        "financial_evidence": financial,
        "native_identity_classification": native_identity_classification,
        "final_adaptability_classification": final_classification,
        "fingerprint_generation_allowed": fingerprint_allowed,
        "import_eligible": False,
    }


def classify_financial_lineage(values: Mapping[str, str | None]) -> dict[str, Any]:
    required_prices = ("side", "entry_price", "exit_price", "quantity", "contract_size")
    price_inputs_complete = all(values.get(field) is not None for field in required_prices)
    gross_reconstructable = price_inputs_complete
    direct_components_complete = all(
        values.get(field) is not None
        for field in ("gross_pnl", "trading_fee", "funding_fee", "net_pnl")
    )
    epsilon = _decimal_or_none(values.get("epsilon_abs_fonte"))
    gross_reconstructed: Decimal | None = None
    gross_residual: Decimal | None = None
    identity_residual: Decimal | None = None
    if gross_reconstructable:
        side = values.get("side")
        entry = decimal_from_value(values["entry_price"])
        exit_price = decimal_from_value(values["exit_price"])
        quantity = decimal_from_value(values["quantity"])
        contract_size = decimal_from_value(values["contract_size"])
        delta = entry - exit_price if side == "short" else exit_price - entry
        gross_reconstructed = delta * quantity * contract_size
        if values.get("gross_pnl") is not None:
            gross_residual = abs(gross_reconstructed - decimal_from_value(values["gross_pnl"]))
    if direct_components_complete:
        identity_residual = abs(
            decimal_from_value(values["gross_pnl"])
            - decimal_from_value(values["trading_fee"])
            - decimal_from_value(values["funding_fee"])
            - decimal_from_value(values["net_pnl"])
        )

    if direct_components_complete and gross_reconstructable and epsilon is not None:
        if gross_residual is not None and gross_residual <= epsilon and identity_residual is not None and identity_residual <= epsilon:
            classification = "financial_identity_fully_verifiable"
        else:
            classification = "financial_fields_conflicting"
    elif gross_reconstructable and (
        values.get("trading_fee") is None or values.get("funding_fee") is None
    ):
        classification = "gross_pnl_reconstructable_but_costs_missing"
    elif values.get("net_pnl") is not None and any(
        values.get(field) is None for field in ("gross_pnl", "trading_fee", "funding_fee")
    ):
        classification = "net_pnl_only_decomposition_underdetermined"
    elif not price_inputs_complete:
        classification = "price_quantity_inputs_incomplete"
    else:
        classification = "financial_identity_unverifiable"
    return {
        "financial_classification": classification,
        "entry_price_available": values.get("entry_price") is not None,
        "exit_price_available": values.get("exit_price") is not None,
        "quantity_available": values.get("quantity") is not None,
        "contract_size_available": values.get("contract_size") is not None,
        "gross_pnl_directly_available": values.get("gross_pnl") is not None,
        "gross_pnl_reconstructable": gross_reconstructable,
        "trading_fee_available": values.get("trading_fee") is not None,
        "funding_fee_available": values.get("funding_fee") is not None,
        "net_pnl_available": values.get("net_pnl") is not None,
        "epsilon_abs_fonte_available": epsilon is not None,
        "gross_reconstruction_residual": _decimal_text(gross_residual),
        "financial_identity_residual": _decimal_text(identity_residual),
    }


def build_source_cohort_profiles(
    rows: Sequence[Mapping[str, Any]],
    row_profiles: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[str, list[int]] = defaultdict(list)
    values_by_key: dict[str, dict[str, str]] = {}
    for index, row in enumerate(rows):
        values = _cohort_values(row)
        serialized = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        groups[serialized].append(index)
        values_by_key[serialized] = values

    profiles: list[dict[str, Any]] = []
    for serialized in sorted(groups):
        indices = groups[serialized]
        cohort_rows = [rows[index] for index in indices]
        cohort_row_profiles = [row_profiles[index] for index in indices]
        order_distribution = Counter(_order_id_pattern(row.get("order_id")) for row in cohort_rows)
        timestamps = [
            value
            for row in cohort_rows
            for value in [_safe_timestamp(row.get("horario_fechamento") or row.get("close_time"))]
            if value is not None
        ]
        symbols = Counter(
            value
            for row in cohort_rows
            for value in [_safe_text(row.get("symbol") or row.get("moeda"), casefold=True)]
            if value is not None
        )
        sides = Counter(
            value
            for row in cohort_rows
            for value in [_safe_side(row.get("side") or row.get("fechar_side"))]
            if value is not None
        )
        missing_fields = sorted(
            {field for profile in cohort_row_profiles for field in profile["missing_v2_fields"]}
        )
        contract_fields = sorted(
            {
                field
                for profile in cohort_row_profiles
                for field in (
                    list(profile["contract_required_fields"])
                    + [
                        invalid
                        for invalid in profile["invalid_v2_fields"]
                        if invalid in NORMALIZABLE_FIELDS
                    ]
                )
            }
        )
        external_fields = sorted(
            {
                field
                for profile in cohort_row_profiles
                for field in profile["external_evidence_required_fields"]
            }
        )
        classifications = Counter(
            str(profile["final_adaptability_classification"])
            for profile in cohort_row_profiles
        )
        profiles.append(
            {
                "cohort_id": f"cohort-{sha256_hex(serialized.encode('utf-8'))[:16]}",
                "cohort_values": values_by_key[serialized],
                "row_count": len(indices),
                "schema_coverage": {
                    field: sum(field in profile["mapped_fields"] for profile in cohort_row_profiles)
                    for field in FINGERPRINT_FIELD_ORDER
                },
                "null_profile": {
                    str(column): sum(is_null(row.get(column)) for row in cohort_rows)
                    for column in sorted({str(column) for row in cohort_rows for column in row})
                },
                "order_id_presence_count": sum(not is_null(row.get("order_id")) for row in cohort_rows),
                "order_id_format_distribution": dict(sorted(order_distribution.items())),
                "timestamp_range": {
                    "min": min(timestamps) if timestamps else None,
                    "max": max(timestamps) if timestamps else None,
                },
                "symbol_distribution": dict(sorted(symbols.items())),
                "side_distribution": dict(sorted(sides.items())),
                "financial_coverage": {
                    field: sum(field in profile["mapped_fields"] for profile in cohort_row_profiles)
                    for field in (
                        "entry_price",
                        "exit_price",
                        "quantity",
                        "contract_size",
                        "gross_pnl",
                        "trading_fee",
                        "funding_fee",
                        "net_pnl",
                    )
                },
                "missing_v2_fields": missing_fields,
                "potential_source_contract_fields": contract_fields,
                "external_evidence_still_required": external_fields,
                "adaptability_classification_counts": dict(sorted(classifications.items())),
                "cohort_adaptability_decision": _cohort_decision(
                    classifications,
                    contract_fields,
                    external_fields,
                ),
                "filename_is_not_identity_authority": True,
            }
        )
    return profiles


def legacy_observation_key_for(
    row: Mapping[str, Any],
    *,
    hasher: Sha256Hasher = sha256_hex,
) -> dict[str, Any]:
    candidate, _ = map_master_row(row)
    if all(field in row for field in OBSERVATION_FIELDS):
        candidate = {field: row.get(field) for field in OBSERVATION_FIELDS}
    normalized: dict[str, str] = {}
    errors: list[str] = []
    for field in OBSERVATION_FIELDS:
        value = candidate.get(field)
        try:
            item = _normalize_field(field, value)
        except FingerprintValidationError as exc:
            errors.append(f"invalid_{field}:{exc}")
            continue
        if item is None:
            errors.append(f"missing_{field}")
        else:
            normalized[field] = item
    if "open_time" in normalized and "close_time" in normalized:
        if normalized["close_time"] < normalized["open_time"]:
            errors.append("close_time_before_open_time")
    if errors:
        return {
            "status": "unverifiable",
            "reasons": sorted(set(errors)),
            "payload": None,
            "observation_hash": None,
        }
    payload = json.dumps(
        {
            "schema_version": LEGACY_OBSERVATION_KEY_VERSION,
            **{field: normalized[field] for field in OBSERVATION_FIELDS},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "status": "valid",
        "reasons": [],
        "payload": payload,
        "observation_hash": hasher(payload.encode("utf-8")),
    }


def build_legacy_overlap_profile(
    master_rows: Sequence[Mapping[str, Any]],
    paper_rows: Sequence[Mapping[str, Any]],
    *,
    row_hasher: Sha256Hasher = sha256_hex,
) -> dict[str, Any]:
    master_index: dict[str, list[tuple[int, str]]] = defaultdict(list)
    master_valid_count = 0
    for index, row in enumerate(master_rows):
        key = legacy_observation_key_for(row, hasher=row_hasher)
        if key["status"] != "valid":
            continue
        master_valid_count += 1
        master_index[str(key["observation_hash"])].append((index, str(key["payload"])))
    master_unverifiable_count = len(master_rows) - master_valid_count

    results: list[dict[str, Any]] = []
    collision_count = 0
    counts: Counter[str] = Counter()
    for index, row in enumerate(paper_rows):
        key = legacy_observation_key_for(row, hasher=row_hasher)
        record_ref = _sanitized_record_ref(row, index)
        if key["status"] != "valid":
            classification = "legacy_overlap_unverifiable"
            reasons = list(key["reasons"])
            exact_indices: list[int] = []
        else:
            hash_matches = master_index.get(str(key["observation_hash"]), [])
            exact_indices = [
                master_index_value
                for master_index_value, payload in hash_matches
                if payload == key["payload"]
            ]
            nonmatching_payloads = [payload for _, payload in hash_matches if payload != key["payload"]]
            if nonmatching_payloads:
                collision_count += 1
            if len(exact_indices) == 1:
                classification = "unique_exact_legacy_overlap_candidate"
                reasons = ["diagnostic_overlap_only_not_duplicate_authority"]
            elif len(exact_indices) > 1:
                classification = "multiple_exact_legacy_overlap_candidate"
                reasons = ["multiple_legacy_rows_share_observation_payload"]
            elif nonmatching_payloads:
                classification = "legacy_overlap_unverifiable"
                reasons = ["legacy_observation_hash_collision_detected"]
            elif master_unverifiable_count:
                classification = "legacy_overlap_unverifiable"
                reasons = ["master_observation_coverage_incomplete"]
            else:
                classification = "no_exact_legacy_overlap_observed"
                reasons = ["absence_of_overlap_is_not_proof_of_new_trade"]
        counts[classification] += 1
        results.append(
            {
                "paper_row_index": index,
                "paper_record_ref": record_ref,
                "classification": classification,
                "reasons": reasons,
                "master_exact_match_count": len(exact_indices),
                "master_indices_internal_count": len(exact_indices),
                "legacy_observation_key_generated": key["status"] == "valid",
                "fingerprint_v2_generated": False,
                "duplicate_confirmed": False,
                "new_trade_confirmed": False,
                "import_eligible": False,
            }
        )
    return {
        "legacy_observation_key_version": LEGACY_OBSERVATION_KEY_VERSION,
        "legacy_observation_key_has_deduplication_authority": False,
        "master_legacy_observation_key_valid_count": master_valid_count,
        "master_legacy_observation_key_unverifiable_count": master_unverifiable_count,
        "legacy_overlap_evaluated_count": len(paper_rows),
        **{f"{name}_count": int(counts[name]) for name in OVERLAP_CLASSIFICATIONS},
        "legacy_observation_hash_collision_count": collision_count,
        "legacy_overlap_results": results,
    }


def lineage_decision(
    row_counts: Mapping[str, int],
    financial_counts: Mapping[str, int],
    external_required: int,
    total_rows: int,
) -> str:
    if total_rows and int(row_counts.get("v2_directly_verifiable", 0)) == total_rows:
        return "LEGACY_MASTER_ALREADY_V2_VERIFIABLE"
    if external_required:
        return "EXTERNAL_AUTHORITATIVE_EVIDENCE_REQUIRED"
    adaptable = sum(
        int(row_counts.get(name, 0))
        for name in (
            "v2_directly_verifiable",
            "v2_deterministically_adaptable",
            "conditionally_adaptable_with_versioned_source_contract",
        )
    )
    if total_rows and adaptable == total_rows:
        return "VERSIONED_SOURCE_CONTRACT_DESIGN_FEASIBLE"
    if int(financial_counts.get("net_pnl_only_decomposition_underdetermined", 0)) or int(
        financial_counts.get("gross_pnl_reconstructable_but_costs_missing", 0)
    ):
        return "LEGACY_FINANCIAL_DECOMPOSITION_UNDERDETERMINED"
    return "LEGACY_MASTER_IRREDUCIBLY_UNVERIFIABLE"


def render_markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Trader Master Legacy Lineage Profile V2",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Trader Master rows: `{report.get('trader_master_row_count')}`",
            f"- Source cohorts: `{report.get('source_cohort_count')}`",
            f"- V2 directly verifiable: `{report.get('v2_directly_verifiable_count')}`",
            f"- V2 deterministically adaptable: `{report.get('v2_deterministically_adaptable_count')}`",
            f"- External evidence required: `{report.get('external_evidence_still_required_row_count')}`",
            f"- Financial identity unverifiable: `{report.get('financial_identity_unverifiable_count')}`",
            f"- Legacy overlap evaluated: `{report.get('legacy_overlap_evaluated_count')}`",
            f"- Fingerprint generation allowed: `{report.get('fingerprint_generation_allowed_count')}`",
            f"- Import eligible: `{report.get('import_eligible_true_count')}`",
            "",
            "## Institutional boundary",
            "",
            "The legacy observation key is descriptive only. It is not Fingerprint V2, does not prove duplication or novelty, and never authorizes import.",
            "",
            f"Recommended next action: `{report.get('recommended_next_action')}`",
            "",
        ]
    )


def snapshot_artifacts(paths: Sequence[Path], root: Path) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for path in paths:
        display = _display_path(path, root)
        exists = path.exists() and path.is_file() and not path.is_symlink()
        snapshot[display] = {
            "exists": exists,
            "size": path.stat().st_size if exists else None,
            "sha256": file_sha256(path) if exists else None,
        }
    return snapshot


def _profile_field(
    field: str,
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
) -> dict[str, Any]:
    aliases = DIRECT_MAPPING_ALIASES[field]
    aliases_present = [alias for alias in aliases if alias in columns]
    source_column = aliases_present[0] if aliases_present else None
    values = [row.get(source_column) for row in rows] if source_column else []
    non_null = [value for value in values if not is_null(value)]
    invalid_count = 0
    normalized_values: list[str] = []
    deterministic_formula: str | None = None
    for value in non_null:
        try:
            normalized = _normalize_field(field, value)
        except FingerprintValidationError:
            invalid_count += 1
            continue
        if normalized is not None:
            normalized_values.append(normalized)
    conflicting = _aliases_conflict(field, aliases_present, rows)
    if conflicting:
        classification = "conflicting_source_evidence"
    elif source_column is None:
        classification = _missing_field_classification(field, rows)
    elif invalid_count:
        classification = "direct_column_invalid"
    elif len(non_null) < len(rows):
        classification = "direct_column_null"
    elif field in NORMALIZABLE_FIELDS:
        classification = "deterministic_derivation_available"
        deterministic_formula = _normalization_formula(field)
    else:
        classification = "direct_authoritative_column"
    if classification == "deterministic_derivation_available" and deterministic_formula is None:
        deterministic_formula = _normalization_formula(field)
    required_evidence = _required_external_evidence(field, classification)
    return {
        "canonical_field": field,
        "source_column": source_column,
        "source_aliases_present": aliases_present,
        "source_value_present_count": len(non_null),
        "source_value_null_count": len(rows) - len(non_null),
        "source_value_invalid_count": invalid_count,
        "distinct_non_null_value_count": len(set(normalized_values)),
        "sample_sanitized_values": _sanitized_samples(field, non_null),
        "lineage_classification": classification,
        "deterministic_formula": deterministic_formula,
        "required_external_evidence": required_evidence,
        "blocks_fingerprint_v2": classification
        not in {"direct_authoritative_column", "deterministic_derivation_available"},
        "fabrication_forbidden": classification
        in {
            "versioned_source_contract_required",
            "external_authoritative_evidence_required",
            "mathematically_underdetermined",
            "unavailable",
        },
    }


def _normalize_field(field: str, value: object) -> str | None:
    rule = FIELD_RULES_BY_NAME[field]
    if rule.kind == "text":
        normalized = normalize_text(value, casefold=rule.casefold)
    elif rule.kind == "timestamp":
        normalized = normalize_timestamp(value)
    else:
        if rule.quantum is None:
            raise FingerprintValidationError("decimal_quantum_missing")
        normalized = normalize_decimal(value, rule.quantum)
    if field == "side" and normalized is not None:
        normalized = {
            "buy": "long",
            "comprado": "long",
            "sell": "short",
            "vendido": "short",
        }.get(normalized, normalized)
        if normalized not in {"long", "short"}:
            raise FingerprintValidationError("invalid_side")
    if field == "account_scope_hash" and normalized is not None:
        if HEX_SHA256.fullmatch(normalized) is None:
            raise FingerprintValidationError("invalid_account_scope_hash")
    return normalized


def _missing_field_classification(field: str, rows: Sequence[Mapping[str, Any]]) -> str:
    if field == "gross_pnl" and rows and all(_row_has_gross_inputs(row) for row in rows):
        return "deterministic_derivation_available"
    if field in CONTRACT_REQUIRED_FIELDS:
        return "versioned_source_contract_required"
    if field in EXTERNAL_EVIDENCE_FIELDS:
        return "external_authoritative_evidence_required"
    if field in UNDERDETERMINED_FIELDS:
        return "mathematically_underdetermined"
    return "unavailable"


def _required_external_evidence(field: str, classification: str) -> list[str]:
    if classification == "external_authoritative_evidence_required":
        return [f"authoritative_{field}_evidence"]
    if classification == "mathematically_underdetermined":
        return ["authoritative_gross_pnl_trading_fee_funding_fee_decomposition"]
    if classification == "versioned_source_contract_required":
        return [f"versioned_source_contract:{field}"]
    if classification == "direct_column_invalid" and field in NORMALIZABLE_FIELDS:
        return [f"versioned_source_normalization_contract:{field}"]
    return []


def _aliases_conflict(
    field: str,
    aliases: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> bool:
    if len(aliases) < 2:
        return False
    for row in rows:
        values: set[str] = set()
        for alias in aliases:
            value = row.get(alias)
            if is_null(value):
                continue
            try:
                normalized = _normalize_field(field, value)
            except FingerprintValidationError:
                continue
            if normalized is not None:
                values.add(normalized)
        if len(values) > 1:
            return True
    return False


def _field_required_for_row(field: str, candidate: Mapping[str, Any]) -> bool:
    if field in {"source_trade_id", "order_id", "epsilon_abs_fonte"}:
        return False
    if field == "order_id_namespace":
        return not is_null(candidate.get("order_id")) or not is_null(
            candidate.get("source_trade_id")
        )
    return True


def _is_deterministic_mapping(
    field: str,
    source_column: str,
    raw_value: object,
    normalized: str,
) -> bool:
    if field in NORMALIZABLE_FIELDS:
        return source_column != field or str(raw_value).strip() != normalized
    return source_column != field


def _native_identity_classification(
    values: Mapping[str, str | None],
    contract_fields: Sequence[str],
    external_fields: Sequence[str],
    invalid_fields: Sequence[str],
) -> str:
    native_id = values.get("order_id") or values.get("source_trade_id")
    if not native_id:
        return "native_identity_missing"
    if any(field in invalid_fields for field in ("venue", "account_scope_hash", "order_id_namespace")):
        return "native_identity_invalid"
    if "account_scope_hash" in external_fields or "venue" in external_fields:
        return "native_identity_external_evidence_required"
    if "order_id_namespace" in contract_fields:
        return "native_identity_versioned_contract_required"
    if values.get("venue") and values.get("account_scope_hash") and values.get("order_id_namespace"):
        return "native_identity_fully_verifiable"
    return "native_identity_unverifiable"


def _row_classification(
    *,
    full_normalizable: bool,
    deterministic_fields: Sequence[str],
    contract_fields: Sequence[str],
    external_fields: Sequence[str],
    invalid_fields: Sequence[str],
    financial_gap_fields: Sequence[str],
    financial_classification: str,
    native_identity_classification: str,
) -> str:
    if full_normalizable and not deterministic_fields:
        return "v2_directly_verifiable"
    if full_normalizable:
        return "v2_deterministically_adaptable"
    financial_blocked = financial_classification != "financial_identity_fully_verifiable"
    native_blocked = native_identity_classification != "native_identity_fully_verifiable"
    if contract_fields and not external_fields and not financial_blocked and not invalid_fields:
        return "conditionally_adaptable_with_versioned_source_contract"
    if native_blocked and not financial_blocked and not financial_gap_fields:
        return "blocked_by_native_identity_lineage"
    if financial_blocked and not native_blocked and not external_fields and not contract_fields:
        return "blocked_by_financial_decomposition"
    if sum(bool(value) for value in (native_blocked, financial_blocked, external_fields, contract_fields, invalid_fields)) > 1:
        return "blocked_by_multiple_lineage_gaps"
    return "irreducibly_unverifiable"


def _cohort_values(row: Mapping[str, Any]) -> dict[str, str]:
    available = list(
        dict.fromkeys(
            [column for column in SOURCE_COHORT_COLUMNS if column in row]
            + sorted(
                str(column)
                for column in row
                if str(column).endswith("_source")
                and str(column) not in SOURCE_COHORT_COLUMNS
            )
        )
    )
    if not available:
        return {"source_columns": "<UNAVAILABLE>"}
    return {
        column: "<NULL>" if is_null(row.get(column)) else str(row.get(column)).strip()
        for column in available
    }


def _cohort_decision(
    classifications: Mapping[str, int],
    contract_fields: Sequence[str],
    external_fields: Sequence[str],
) -> str:
    if external_fields:
        return "external_authoritative_evidence_required"
    if contract_fields and not any(name.startswith("blocked_") for name in classifications):
        return "versioned_source_contract_design_feasible"
    if set(classifications) <= {
        "v2_directly_verifiable",
        "v2_deterministically_adaptable",
    }:
        return "v2_verifiable"
    return "cohort_remains_unverifiable"


def _row_has_gross_inputs(row: Mapping[str, Any]) -> bool:
    candidate, _ = map_master_row(row)
    try:
        return all(
            _normalize_field(field, candidate.get(field)) is not None
            for field in ("side", "entry_price", "exit_price", "quantity", "contract_size")
        )
    except FingerprintValidationError:
        return False


def _sanitized_samples(field: str, values: Sequence[object], limit: int = 5) -> list[str]:
    samples: list[str] = []
    for value in values:
        rendered = str(value).strip()
        if field in SENSITIVE_SAMPLE_FIELDS:
            rendered = f"<redacted:{sha256_hex(rendered.encode('utf-8'))[:12]}>"
        elif field == "source":
            rendered = Path(rendered).name[:80]
        else:
            rendered = rendered[:80]
        if rendered not in samples:
            samples.append(rendered)
        if len(samples) >= limit:
            break
    return samples


def _sanitized_record_ref(row: Mapping[str, Any], index: int) -> str:
    native = row.get("order_id") or row.get("source_trade_id") or f"paper-row-{index}"
    return f"paper-ref-{sha256_hex(str(native).encode('utf-8'))[:16]}"


def _order_id_pattern(value: object) -> str:
    if is_null(value):
        return "missing"
    text = str(value).strip()
    if ORDER_ID_FREQTRADE_PATTERN.fullmatch(text):
        return "freqtrade_paper_local_trade_id"
    if ORDER_ID_NUMERIC_PATTERN.fullmatch(text):
        return "numeric_like"
    return "opaque_non_secret_identifier"


def _safe_timestamp(value: object) -> str | None:
    try:
        return normalize_timestamp(value)
    except FingerprintValidationError:
        return None


def _safe_text(value: object, *, casefold: bool) -> str | None:
    return normalize_text(value, casefold=casefold)


def _safe_side(value: object) -> str | None:
    try:
        return _normalize_field("side", value)
    except FingerprintValidationError:
        return None


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return decimal_from_value(value)
    except FingerprintValidationError:
        return None


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _normalization_formula(field: str) -> str:
    if field == "side":
        return "fingerprint_spec_v2.normalize_text_then_side_mapping"
    if field in {"open_time", "close_time"}:
        return "fingerprint_spec_v2.normalize_timestamp_utc"
    if FIELD_RULES_BY_NAME[field].kind == "decimal":
        return "fingerprint_spec_v2.normalize_decimal"
    return "fingerprint_spec_v2.normalize_text"


def _recommended_next_action(decision: str) -> str:
    actions = {
        "LEGACY_MASTER_ALREADY_V2_VERIFIABLE": "retain_readonly_evidence_and_review_bridge_design_separately",
        "VERSIONED_SOURCE_CONTRACT_DESIGN_FEASIBLE": "design_versioned_source_contract_without_writing_master",
        "EXTERNAL_AUTHORITATIVE_EVIDENCE_REQUIRED": "inventory_authoritative_identity_and_financial_sources_by_cohort",
        "LEGACY_FINANCIAL_DECOMPOSITION_UNDERDETERMINED": "obtain_authoritative_gross_fee_and_funding_evidence",
        "LEGACY_MASTER_IRREDUCIBLY_UNVERIFIABLE": "retain_legacy_rows_outside_fingerprint_v2_import_authority",
    }
    return actions[decision]


def _profile_warnings(
    field_profile: Sequence[Mapping[str, Any]],
    row_counts: Mapping[str, int],
) -> list[str]:
    warnings: list[str] = []
    if any(item["lineage_classification"] == "direct_column_invalid" for item in field_profile):
        warnings.append("legacy_columns_require_versioned_normalization_review")
    if int(row_counts.get("blocked_by_multiple_lineage_gaps", 0)):
        warnings.append("legacy_rows_have_multiple_independent_lineage_gaps")
    return warnings


def _paper_bundle_is_profileable(bundle: FreqtradePaperAdapterBundle) -> bool:
    report = bundle.report
    return (
        bool(bundle.accepted_canonical_records)
        and len(bundle.accepted_canonical_records)
        == int(report.get("accepted_row_count", -1))
        and bool(report.get("snapshot_source_hashes_preserved"))
        and report.get("source_status") == "ok"
        and not report.get("structural_errors")
    )


def _paper_artifact_paths(
    root: Path,
    primary: str | Path,
    replicas: Sequence[str],
    sqlite_path: str | Path,
) -> list[Path]:
    sqlite = _resolve(root, sqlite_path)
    return [
        _resolve(root, primary),
        *[_resolve(root, replica) for replica in replicas],
        sqlite,
        Path(f"{sqlite}-wal"),
        Path(f"{sqlite}-shm"),
    ]


def _base_report(
    *,
    root: Path,
    trader_master_path: str | Path,
    source_profile_path: str | Path,
    write_report: bool,
    json_path: Path,
    markdown_path: Path,
    generated_at_utc: str | None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at_utc or datetime.now(UTC).isoformat(),
        "status": "blocked",
        "reason": "not_evaluated",
        "decision": "LEGACY_MASTER_IRREDUCIBLY_UNVERIFIABLE",
        "fingerprint_spec_version": FINGERPRINT_SPEC_VERSION,
        "trader_master_path": _display_path(_resolve(root, trader_master_path), root),
        "source_profile_path": _display_path(_resolve(root, source_profile_path), root),
        "account_scope_hash_present": False,
        "account_scope_hash_valid": False,
        "account_scope_original_identifier_persisted": False,
        "field_lineage_profile": [],
        "source_cohort_count": 0,
        "source_cohort_profiles": [],
        "row_profiles": [],
        "row_profile_count": 0,
        "legacy_overlap_results": [],
        "legacy_overlap_evaluated_count": 0,
        "versioned_contract_could_improve_row_count": 0,
        "external_evidence_still_required_row_count": 0,
        "fingerprint_generation_allowed_count": 0,
        "import_eligible_true_count": 0,
        "recommended_next_action": "resolve_structural_blocker",
        "write_requested": bool(write_report),
        "write_performed": False,
        "output_paths": {
            "json": _display_path(json_path, root),
            "markdown": _display_path(markdown_path, root),
        },
        "validation_errors": [],
        "blockers": [],
        "warnings": [],
        **SAFETY_FLAGS,
        "safety_flags": dict(SAFETY_FLAGS),
    }
    for name in (*ROW_CLASSIFICATIONS, *FINANCIAL_CLASSIFICATIONS, *OVERLAP_CLASSIFICATIONS):
        report[f"{name}_count"] = 0
    return report


def _blocked(
    report: dict[str, Any],
    reason: str,
    errors: Sequence[str] | None = None,
) -> dict[str, Any]:
    result = dict(report)
    blockers = sorted(set(errors or [reason]))
    result.update(
        status="blocked",
        reason=reason,
        validation_errors=blockers,
        blockers=blockers,
        write_performed=False,
        **SAFETY_FLAGS,
        safety_flags=dict(SAFETY_FLAGS),
    )
    return result


def _maybe_write(
    report: dict[str, Any],
    write_report: bool,
    json_path: Path,
    markdown_path: Path,
) -> dict[str, Any]:
    if not write_report:
        return report
    final = dict(report)
    final["write_performed"] = True
    _atomic_write(
        json_path,
        json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
    )
    _atomic_write(markdown_path, render_markdown(final))
    return final


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _validate_report_paths(root: Path, *paths: Path) -> list[str]:
    allowed_root = (root / "data/reports").resolve()
    errors: list[str] = []
    for path in paths:
        try:
            path.resolve().relative_to(allowed_root)
        except ValueError:
            errors.append(f"report_path_outside_data_reports:{path}")
        if path.suffix.casefold() not in {".json", ".md"}:
            errors.append(f"unsupported_report_extension:{path.suffix}")
    return sorted(set(errors))


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path.resolve())
