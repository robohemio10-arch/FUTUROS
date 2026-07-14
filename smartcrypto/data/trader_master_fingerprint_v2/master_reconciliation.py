"""Institutional read-only reconciliation preview for Trader Master V2."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .fingerprint_spec import (
    FINGERPRINT_SPEC_VERSION,
    FingerprintValidationError,
    Sha256Hasher,
    canonical_json,
    canonical_trade_id_for,
    normalize_trade_row,
    primary_identity_for,
    row_fingerprint_for,
    sha256_hex,
)
from .freqtrade_adapter import (
    FreqtradePaperAdapterBundle,
    build_freqtrade_paper_closed_trades_adapter_bundle,
)
from .master_adapter import (
    AfterReadHook,
    MasterCanonicalRecord,
    MasterReadBundle,
    file_sha256,
    read_trader_master_readonly,
)
from .source_profile import SourceProfileError, load_source_profile


SCHEMA_VERSION = "trader_master_readonly_reconciliation_v2"
DEFAULT_MASTER = Path("data/trades/trades_master.parquet")
DEFAULT_JSON_REPORT = Path("data/reports/trader_master_readonly_reconciliation_v2.json")
DEFAULT_MARKDOWN_REPORT = Path("data/reports/trader_master_readonly_reconciliation_v2.md")
FINANCIAL_DIFF_FIELDS = (
    "symbol",
    "side",
    "open_time",
    "close_time",
    "entry_price",
    "exit_price",
    "quantity",
    "contract_size",
    "gross_pnl",
    "trading_fee",
    "funding_fee",
    "net_pnl",
)

AdapterBuilder = Callable[..., FreqtradePaperAdapterBundle]
MasterReader = Callable[..., MasterReadBundle]
ArtifactSnapshotter = Callable[[Sequence[Path], Path], dict[str, dict[str, Any]]]

SAFETY_FLAGS: dict[str, bool] = {
    "preview_only": True,
    "import_requested": False,
    "import_performed": False,
    "backup_performed": False,
    "writes_trader_master": False,
    "writes_parquet": False,
    "writes_xlsx": False,
    "writes_csv": False,
    "writes_sqlite": False,
    "writes_runtime": False,
    "changes_fingerprint_spec": False,
    "changes_risk": False,
    "changes_model": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
}


def build_trader_master_reconciliation_report(
    *,
    project_root: str | Path,
    source_profile_path: str | Path,
    account_scope_hash: str | None,
    authoritative_sqlite_path: str | Path | None = None,
    trader_master_path: str | Path = DEFAULT_MASTER,
    write_report: bool = False,
    output_json: str | Path = DEFAULT_JSON_REPORT,
    output_markdown: str | Path = DEFAULT_MARKDOWN_REPORT,
    generated_at_utc: str | None = None,
    row_hasher: Sha256Hasher = sha256_hex,
    adapter_builder: AdapterBuilder = build_freqtrade_paper_closed_trades_adapter_bundle,
    master_reader: MasterReader = read_trader_master_readonly,
    artifact_snapshotter: ArtifactSnapshotter | None = None,
    after_master_read_hook: AfterReadHook | None = None,
) -> dict[str, Any]:
    """Build a deterministic preview without importing or mutating either source."""

    root = Path(project_root).resolve()
    json_path = _resolve(root, output_json)
    markdown_path = _resolve(root, output_markdown)
    report = _base_report(
        root=root,
        source_profile_path=source_profile_path,
        trader_master_path=trader_master_path,
        json_path=json_path,
        markdown_path=markdown_path,
        write_report=write_report,
        generated_at_utc=generated_at_utc,
    )
    output_errors = _validate_report_paths(root, json_path, markdown_path) if write_report else []
    if output_errors:
        return _blocked(report, "unsafe_report_output_path", output_errors)

    try:
        profile = load_source_profile(_resolve(root, source_profile_path))
    except SourceProfileError as exc:
        return _blocked(report, "source_profile_invalid", str(exc).split(";"))

    paper_source = _resolve(root, profile.primary_source_path)
    sqlite_source = _resolve(
        root,
        authoritative_sqlite_path or profile.authoritative_sqlite.snapshot_path,
    )
    paper_artifacts = [
        paper_source,
        *[_resolve(root, path) for path in profile.replica_source_paths],
        sqlite_source,
        Path(f"{sqlite_source}-wal"),
        Path(f"{sqlite_source}-shm"),
    ]
    snapshotter = artifact_snapshotter or snapshot_artifacts
    paper_hashes_before = snapshotter(paper_artifacts, root)

    bundle = adapter_builder(
        project_root=root,
        source_profile_path=source_profile_path,
        account_scope_hash=account_scope_hash,
        authoritative_sqlite_path=authoritative_sqlite_path,
        apply_authoritative_forensic_recovery=True,
    )
    adapter_report = bundle.report
    report.update(_paper_report_fields(adapter_report, bundle.batch_identity))
    if not _adapter_bundle_is_reconcilable(bundle):
        return _blocked(
            report,
            "paper_adapter_not_reconcilable",
            list(adapter_report.get("blockers", [])) or [str(adapter_report.get("reason"))],
        )

    master_bundle = master_reader(
        project_root=root,
        trader_master_path=trader_master_path,
        row_hasher=row_hasher,
        after_read_hook=after_master_read_hook,
    )
    report.update(master_bundle.report)
    if master_bundle.report.get("status") != "ok":
        return _blocked(
            report,
            str(master_bundle.report.get("reason", "trader_master_unreadable")),
        )

    reconciliation = reconcile_canonical_records(
        bundle.accepted_canonical_records,
        master_bundle.canonical_records,
        master_bundle.unverifiable_rows,
        row_hasher=row_hasher,
    )
    report.update(reconciliation)

    paper_hashes_after = snapshotter(paper_artifacts, root)
    hashes_preserved = paper_hashes_before == paper_hashes_after
    report.update(
        paper_artifact_hashes_before=paper_hashes_before,
        paper_artifact_hashes_after=paper_hashes_after,
        paper_batch_hashes_preserved=hashes_preserved,
        batch_identity={
            **bundle.batch_identity,
            "paper_artifact_hashes_before": paper_hashes_before,
            "paper_artifact_hashes_after": paper_hashes_after,
        },
    )
    if not hashes_preserved:
        return _blocked(report, "paper_batch_changed_during_reconciliation")

    decision = reconciliation_decision(report)
    projection_calculable = decision in {
        "READY_FOR_CONTROLLED_IMPORT_REVIEW",
        "NO_NEW_TRADES",
    }
    report.update(
        status="ok",
        reason="reconciliation_preview_completed",
        decision=decision,
        projected_master_row_count_after_hypothetical_import=(
            int(report["trader_master_row_count"])
            + int(report["new_trade_candidate_count"])
            if projection_calculable
            else None
        ),
        projected_master_row_count_calculable=projection_calculable,
        validation_errors=[],
        blockers=_decision_blockers(decision),
        **SAFETY_FLAGS,
        safety_flags=dict(SAFETY_FLAGS),
    )
    return _maybe_write(report, write_report, json_path, markdown_path)


def reconcile_canonical_records(
    incoming_records: Sequence[Mapping[str, Any]],
    master_records: Sequence[MasterCanonicalRecord],
    master_unverifiable_rows: Sequence[Mapping[str, Any]],
    *,
    row_hasher: Sha256Hasher = sha256_hex,
) -> dict[str, Any]:
    """Classify each incoming row once using V2 identity and fingerprint evidence."""

    incoming = [
        _canonicalize_incoming(index, row, row_hasher)
        for index, row in enumerate(incoming_records)
    ]
    fingerprint_index: dict[str, list[MasterCanonicalRecord]] = defaultdict(list)
    identity_index: dict[tuple[str, ...], list[MasterCanonicalRecord]] = defaultdict(list)
    all_payloads: dict[str, set[str]] = defaultdict(set)
    for master_record in master_records:
        fingerprint_index[master_record.row_fingerprint].append(master_record)
        all_payloads[master_record.row_fingerprint].add(master_record.canonical_json)
        identity_key = _identity_key(master_record.primary_identity)
        if identity_key is not None:
            identity_index[identity_key].append(master_record)
    for incoming_record in incoming:
        if incoming_record.get("validation_status") == "valid":
            all_payloads[str(incoming_record["row_fingerprint"])].add(
                str(incoming_record["canonical_json"])
            )

    collision_fingerprints = {key for key, payloads in all_payloads.items() if len(payloads) > 1}
    duplicate_identity_groups = {
        key: rows for key, rows in identity_index.items() if len(rows) > 1
    }
    duplicate_fingerprint_groups = {
        key: rows
        for key, rows in fingerprint_index.items()
        if len(rows) > 1 and len({row.canonical_json for row in rows}) == 1
    }
    legacy_order_index: dict[str, list[int]] = defaultdict(list)
    for row in master_unverifiable_rows:
        evidence = row.get("legacy_identity")
        if isinstance(evidence, Mapping):
            for field in ("order_id", "source_trade_id"):
                value = evidence.get(field)
                if value:
                    legacy_order_index[str(value)].append(int(row.get("row_index", -1)))

    results: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for incoming_record in incoming:
        result = _classify_incoming(
            incoming_record,
            fingerprint_index=fingerprint_index,
            identity_index=identity_index,
            duplicate_identity_groups=duplicate_identity_groups,
            collision_fingerprints=collision_fingerprints,
            legacy_order_index=legacy_order_index,
            master_unverifiable_row_count=len(master_unverifiable_rows),
        )
        results.append(result)
        if result["classification"] == "primary_identity_financial_conflict":
            conflicts.append(result)

    counts = Counter(str(result["classification"]) for result in results)
    return {
        "master_valid_fingerprint_row_count": len(master_records),
        "master_verifiable_row_count": len(master_records),
        "master_unverifiable_row_count": len(master_unverifiable_rows),
        "exact_fingerprint_duplicate_count": counts["exact_fingerprint_duplicate"],
        "primary_identity_exact_duplicate_count": counts["primary_identity_exact_duplicate"],
        "primary_identity_financial_conflict_count": counts[
            "primary_identity_financial_conflict"
        ],
        "observed_fingerprint_collision_count": len(collision_fingerprints),
        "duplicate_master_primary_identity_count": len(duplicate_identity_groups),
        "duplicate_master_fingerprint_count": len(duplicate_fingerprint_groups),
        "ambiguous_legacy_identity_match_count": counts["ambiguous_legacy_identity_match"],
        "new_trade_candidate_count": counts["new_trade_candidate"],
        "incoming_blocked_by_unverifiable_master_count": counts[
            "incoming_blocked_by_unverifiable_master"
        ],
        "incoming_row_unverifiable_count": counts["incoming_row_unverifiable"],
        "reconciliation_results": results,
        "conflict_results": conflicts,
        "master_unverifiable_rows": [dict(item) for item in master_unverifiable_rows],
        "duplicate_master_primary_identity_results": [
            {
                "primary_identity": _identity_from_key(key),
                "master_row_indices": sorted(row.row_index for row in rows),
            }
            for key, rows in sorted(duplicate_identity_groups.items())
        ],
        "duplicate_master_fingerprint_results": [
            {
                "row_fingerprint": fingerprint,
                "master_row_indices": sorted(row.row_index for row in rows),
            }
            for fingerprint, rows in sorted(duplicate_fingerprint_groups.items())
        ],
        "observed_fingerprint_collision_fingerprints": sorted(collision_fingerprints),
    }


def reconciliation_decision(report: Mapping[str, Any]) -> str:
    if int(report.get("observed_fingerprint_collision_count", 0)):
        return "BLOCKED_BY_FINGERPRINT_COLLISION"
    if int(report.get("primary_identity_financial_conflict_count", 0)) or int(
        report.get("duplicate_master_primary_identity_count", 0)
    ):
        return "BLOCKED_BY_MASTER_IDENTITY_CONFLICTS"
    if (
        int(report.get("master_unverifiable_row_count", 0))
        or int(report.get("incoming_row_unverifiable_count", 0))
        or int(report.get("ambiguous_legacy_identity_match_count", 0))
    ):
        return "BLOCKED_BY_UNVERIFIABLE_MASTER_ROWS"
    if int(report.get("new_trade_candidate_count", 0)):
        return "READY_FOR_CONTROLLED_IMPORT_REVIEW"
    return "NO_NEW_TRADES"


def snapshot_artifacts(paths: Sequence[Path], root: Path) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for path in paths:
        key = _display_path(path, root)
        exists = path.exists() and path.is_file()
        try:
            evidence[key] = {
                "exists": exists,
                "sha256": file_sha256(path) if exists else None,
                "size_bytes": path.stat().st_size if exists else None,
                "error": None,
            }
        except OSError as exc:
            evidence[key] = {
                "exists": path.exists(),
                "sha256": None,
                "size_bytes": None,
                "error": type(exc).__name__,
            }
    return dict(sorted(evidence.items()))


def render_markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Trader Master Read-only Reconciliation V2",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Paper accepted rows: `{report.get('paper_accepted_row_count', 0)}`",
            f"- Paper quarantined rows: `{report.get('paper_quarantined_row_count', 0)}`",
            f"- Trader Master rows: `{report.get('trader_master_row_count', 0)}`",
            f"- Exact fingerprint duplicates: `{report.get('exact_fingerprint_duplicate_count', 0)}`",
            f"- Identity conflicts: `{report.get('primary_identity_financial_conflict_count', 0)}`",
            f"- Legacy ambiguities: `{report.get('ambiguous_legacy_identity_match_count', 0)}`",
            f"- New candidates: `{report.get('new_trade_candidate_count', 0)}`",
            "- Incoming blocked by unverifiable Master: "
            f"`{report.get('incoming_blocked_by_unverifiable_master_count', 0)}`",
            f"- Unverifiable Master rows: `{report.get('master_unverifiable_row_count', 0)}`",
            "- Projected rows calculable: "
            f"`{str(bool(report.get('projected_master_row_count_calculable'))).lower()}`",
            "- Projected rows: "
            f"`{report.get('projected_master_row_count_after_hypothetical_import')}`",
            "",
            "This preview has no import authority and never writes the Trader Master.",
            "",
        ]
    )


def _canonicalize_incoming(
    row_index: int,
    row: Mapping[str, Any],
    row_hasher: Sha256Hasher,
) -> dict[str, Any]:
    try:
        normalized = normalize_trade_row(row)
        serialized = canonical_json(normalized)
        fingerprint = row_fingerprint_for(normalized, hasher=row_hasher)
        identity = primary_identity_for(normalized)
        canonical_id = canonical_trade_id_for(normalized, row_fingerprint=fingerprint)
    except FingerprintValidationError as exc:
        return {
            "incoming_row_index": row_index,
            "validation_status": "unverifiable",
            "validation_errors": sorted(set(str(exc).split(";"))),
            "order_id": row.get("order_id"),
        }
    return {
        "incoming_row_index": row_index,
        "validation_status": "valid",
        "validation_errors": [],
        "normalized": normalized,
        "canonical_json": serialized,
        "row_fingerprint": fingerprint,
        "primary_identity": identity,
        "canonical_trade_id": canonical_id,
        "order_id": normalized.get("order_id"),
    }


def _classify_incoming(
    record: Mapping[str, Any],
    *,
    fingerprint_index: Mapping[str, list[MasterCanonicalRecord]],
    identity_index: Mapping[tuple[str, ...], list[MasterCanonicalRecord]],
    duplicate_identity_groups: Mapping[tuple[str, ...], list[MasterCanonicalRecord]],
    collision_fingerprints: set[str],
    legacy_order_index: Mapping[str, list[int]],
    master_unverifiable_row_count: int,
) -> dict[str, Any]:
    base = {
        "incoming_row_index": record["incoming_row_index"],
        "order_id": record.get("order_id"),
        "row_fingerprint": record.get("row_fingerprint"),
        "canonical_trade_id": record.get("canonical_trade_id"),
        "primary_identity": record.get("primary_identity"),
        "financial_diff": [],
        "material_conflict": False,
        "matched_master_row_indices": [],
        "import_eligible": False,
    }
    if record.get("validation_status") != "valid":
        return {
            **base,
            "classification": "incoming_row_unverifiable",
            "reasons": list(record.get("validation_errors", [])),
        }

    fingerprint = str(record["row_fingerprint"])
    identity_key = _identity_key(record.get("primary_identity"))
    fingerprint_matches = fingerprint_index.get(fingerprint, [])
    identity_matches = identity_index.get(identity_key, []) if identity_key is not None else []
    if fingerprint in collision_fingerprints:
        return {
            **base,
            "classification": "observed_fingerprint_collision",
            "reasons": ["same_fingerprint_different_canonical_json"],
            "matched_master_row_indices": sorted(row.row_index for row in fingerprint_matches),
        }
    if identity_key is not None and identity_key in duplicate_identity_groups:
        return {
            **base,
            "classification": "duplicate_master_primary_identity",
            "reasons": ["multiple_master_rows_share_primary_identity"],
            "matched_master_row_indices": sorted(row.row_index for row in identity_matches),
        }
    if identity_matches:
        master = identity_matches[0]
        if master.canonical_json != record["canonical_json"]:
            diff = financial_diff(record["normalized"], master.normalized)
            return {
                **base,
                "classification": "primary_identity_financial_conflict",
                "reasons": ["primary_identity_payload_divergence"],
                "financial_diff": diff,
                "material_conflict": True,
                "matched_master_row_indices": [master.row_index],
            }
    exact_matches = [
        row for row in fingerprint_matches if row.canonical_json == record["canonical_json"]
    ]
    if exact_matches:
        return {
            **base,
            "classification": "exact_fingerprint_duplicate",
            "reasons": [],
            "matched_master_row_indices": sorted(row.row_index for row in exact_matches),
        }
    if identity_matches and identity_matches[0].canonical_json == record["canonical_json"]:
        return {
            **base,
            "classification": "primary_identity_exact_duplicate",
            "reasons": [],
            "matched_master_row_indices": [identity_matches[0].row_index],
        }
    native_id = str(record.get("order_id") or "")
    if native_id and native_id in legacy_order_index:
        return {
            **base,
            "classification": "ambiguous_legacy_identity_match",
            "reasons": ["legacy_order_id_match_without_v2_identity"],
            "matched_master_row_indices": sorted(legacy_order_index[native_id]),
        }
    if master_unverifiable_row_count:
        return {
            **base,
            "classification": "incoming_blocked_by_unverifiable_master",
            "reasons": ["absence_not_provable_against_unverifiable_master"],
        }
    return {
        **base,
        "classification": "new_trade_candidate",
        "reasons": [],
        "import_eligible": True,
    }


def financial_diff(
    incoming: Mapping[str, str | None],
    master: Mapping[str, str | None],
) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    numeric_fields = {
        "entry_price",
        "exit_price",
        "quantity",
        "contract_size",
        "gross_pnl",
        "trading_fee",
        "funding_fee",
        "net_pnl",
    }
    for field in FINANCIAL_DIFF_FIELDS:
        left = incoming.get(field)
        right = master.get(field)
        if left == right:
            continue
        delta: str | None = None
        if field in numeric_fields and left is not None and right is not None:
            try:
                delta = format(abs(Decimal(left) - Decimal(right)), "f")
            except InvalidOperation:
                delta = None
        differences.append(
            {
                "field": field,
                "incoming_normalized_value": left,
                "master_normalized_value": right,
                "absolute_numeric_delta": delta,
                "material_conflict": True,
            }
        )
    return differences


def _adapter_bundle_is_reconcilable(bundle: FreqtradePaperAdapterBundle) -> bool:
    report = bundle.report
    return (
        bool(bundle.accepted_canonical_records)
        and len(bundle.accepted_canonical_records)
        == int(report.get("accepted_row_count", -1))
        and bool(
        report.get("snapshot_source_hashes_preserved")
        )
        and report.get("source_status") == "ok"
        and not report.get("structural_errors")
    )


def _paper_report_fields(
    report: Mapping[str, Any], batch_identity: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "source_profile_id": report.get("source_profile_id"),
        "account_scope_hash_present": bool(report.get("account_scope_hash_present")),
        "paper_source_path": report.get("source_file"),
        "paper_source_hash": report.get("primary_source_sha256"),
        "sqlite_hashes_before": report.get("snapshot_source_hashes_before", {}),
        "sqlite_hashes_after": report.get("snapshot_source_hashes_after", {}),
        "paper_raw_row_count": int(report.get("raw_row_count", 0)),
        "paper_accepted_row_count": int(report.get("accepted_row_count", 0)),
        "paper_quarantined_row_count": int(report.get("quarantined_row_count", 0)),
        "paper_quarantined_order_ids": list(report.get("quarantined_order_ids", [])),
        "forensic_recovery_applied_count": int(
            report.get("forensic_recovery_applied_count", 0)
        ),
        "batch_identity": dict(batch_identity),
    }


def _decision_blockers(decision: str) -> list[str]:
    return [] if decision in {"READY_FOR_CONTROLLED_IMPORT_REVIEW", "NO_NEW_TRADES"} else [decision]


def _identity_key(identity: object) -> tuple[str, ...] | None:
    if not isinstance(identity, Mapping):
        return None
    return tuple(
        str(identity.get(field, ""))
        for field in (
            "venue",
            "account_scope_hash",
            "order_id_namespace",
            "native_id_type",
            "native_id",
        )
    )


def _identity_from_key(key: tuple[str, ...]) -> dict[str, str]:
    fields = (
        "venue",
        "account_scope_hash",
        "order_id_namespace",
        "native_id_type",
        "native_id",
    )
    return dict(zip(fields, key, strict=True))


def _base_report(
    *,
    root: Path,
    source_profile_path: str | Path,
    trader_master_path: str | Path,
    json_path: Path,
    markdown_path: Path,
    write_report: bool,
    generated_at_utc: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "fingerprint_spec_version": FINGERPRINT_SPEC_VERSION,
        "generated_at_utc": generated_at_utc or datetime.now(UTC).isoformat(),
        "status": "blocked",
        "reason": "not_evaluated",
        "decision": "BLOCKED_BY_UNVERIFIABLE_MASTER_ROWS",
        "project_root": str(root),
        "source_profile_path": _display_path(_resolve(root, source_profile_path), root),
        "source_profile_id": None,
        "account_scope_hash_present": False,
        "paper_source_path": None,
        "paper_source_hash": None,
        "sqlite_hashes_before": {},
        "sqlite_hashes_after": {},
        "paper_raw_row_count": 0,
        "paper_accepted_row_count": 0,
        "paper_quarantined_row_count": 0,
        "paper_quarantined_order_ids": [],
        "forensic_recovery_applied_count": 0,
        "trader_master_path": str(trader_master_path),
        "trader_master_sha256_before": None,
        "trader_master_sha256_after": None,
        "trader_master_hash_preserved": False,
        "trader_master_temp_copy_used": False,
        "trader_master_row_count": 0,
        "trader_master_schema_columns": [],
        "master_valid_fingerprint_row_count": 0,
        "master_verifiable_row_count": 0,
        "master_unverifiable_row_count": 0,
        "exact_fingerprint_duplicate_count": 0,
        "primary_identity_exact_duplicate_count": 0,
        "primary_identity_financial_conflict_count": 0,
        "observed_fingerprint_collision_count": 0,
        "duplicate_master_primary_identity_count": 0,
        "duplicate_master_fingerprint_count": 0,
        "ambiguous_legacy_identity_match_count": 0,
        "new_trade_candidate_count": 0,
        "incoming_blocked_by_unverifiable_master_count": 0,
        "incoming_row_unverifiable_count": 0,
        "projected_master_row_count_after_hypothetical_import": None,
        "projected_master_row_count_calculable": False,
        "reconciliation_results": [],
        "conflict_results": [],
        "master_unverifiable_rows": [],
        "batch_identity": {},
        "write_requested": bool(write_report),
        "write_performed": False,
        "output_paths": {
            "json": _display_path(json_path, root),
            "markdown": _display_path(markdown_path, root),
        },
        "validation_errors": [],
        "blockers": [],
        **SAFETY_FLAGS,
        "safety_flags": dict(SAFETY_FLAGS),
    }


def _blocked(
    report: Mapping[str, Any], reason: str, errors: Sequence[str] | None = None
) -> dict[str, Any]:
    result = dict(report)
    result.update(
        status="blocked",
        reason=reason,
        decision="BLOCKED_BY_UNVERIFIABLE_MASTER_ROWS",
        validation_errors=sorted(set(errors or [reason])),
        blockers=sorted(set(errors or [reason])),
        write_performed=False,
        **SAFETY_FLAGS,
        safety_flags=dict(SAFETY_FLAGS),
    )
    return result


def _maybe_write(
    report: dict[str, Any], write_report: bool, json_path: Path, markdown_path: Path
) -> dict[str, Any]:
    if not write_report:
        return report
    written = {**report, "write_performed": True}
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(written, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(written), encoding="utf-8")
    return written


def _validate_report_paths(root: Path, json_path: Path, markdown_path: Path) -> list[str]:
    allowed = (root / "data" / "reports").resolve()
    errors: list[str] = []
    for path, extension in ((json_path, ".json"), (markdown_path, ".md")):
        if path.suffix.casefold() != extension:
            errors.append(f"invalid_report_extension:{_display_path(path, root)}")
        if path.is_symlink():
            errors.append(f"report_symlink_forbidden:{_display_path(path, root)}")
        try:
            path.resolve().relative_to(allowed)
        except ValueError:
            errors.append(f"report_outside_data_reports:{_display_path(path, root)}")
    return sorted(set(errors))


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path.resolve())
