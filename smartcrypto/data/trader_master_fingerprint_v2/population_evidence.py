"""Hash-bound, single-scope population evidence; no recovery or economic inference."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, DecimalException, localcontext
from pathlib import Path

from .authoritative_sqlite import read_authoritative_closed_trades, snapshot_artifact_hashes
from .financial_lifecycle import Record, TradeResult, reconcile_financial_lifecycle
from .fingerprint_spec import FingerprintValidationError, datetime_from_value, decimal_from_value
from .source_profile import SourceProfileError, load_source_profile


SCHEMA_VERSION = "paper_financial_population_evidence_f04_v1"
AUTHORIZATION_SCHEMA = "paper_financial_population_source_authorization_f04_v1"
HASH = re.compile(r"[0-9a-f]{64}")
LABEL = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}")
ARTIFACT_SUFFIXES = {"db": "", "wal": "-wal", "shm": "-shm"}
SCOPE_FIELDS = ("source_id", "account_scope_hash", "paper_scope", "epoch_id", "cohort_id")


class EvidenceError(ValueError):
    """A deterministic reason that must suppress financial population metrics."""


@dataclass(frozen=True)
class Authorization:
    scope: tuple[tuple[str, str], ...]
    trade_ids: tuple[int, ...]
    artifacts: tuple[tuple[str, str | None], ...]
    profile_sha256: str


def audit_paper_financial_population(
    *,
    project_root: str | Path,
    sqlite_path: str | Path,
    source_profile_path: str | Path,
    authorization_path: str | Path,
) -> dict[str, object]:
    """Only explicit, sealed closed-trade membership is analyzed; never discover sources."""
    source: dict[str, object] = {}
    report: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "status": "BLOCKED",
        "reason": "NOT_EVALUATED",
        "source": source,
        "analysis": None,
        "write_performed": False,
        "source_writes_performed": False,
        "recovery_applied": False,
        "economic_inference_performed": False,
        "operational_authority": False,
        "sends_orders": False,
        "runtime_parity_certified": False,
    }
    try:
        root = _path(project_root, directory=True)
        snapshot = _path(sqlite_path)
        snapshot.relative_to(root)
        profile_path = _path(source_profile_path)
        permission_path = _path(authorization_path)
        if len({snapshot, profile_path, permission_path}) != 3:
            raise EvidenceError("SOURCE_INPUT_IDENTITIES_OVERLAP")
        for suffix in ARTIFACT_SUFFIXES.values():
            _reject_links(Path(f"{snapshot}{suffix}"))
        permission_bytes = _read_authorization(permission_path)
        permission = _authorization(permission_bytes, snapshot, profile_path)
        scope = dict(permission.scope)
        source.update(
            sqlite_path=str(snapshot), authorization_sha256=_sha(permission_bytes),
            **scope,
            scope_basis="EXPLICIT_HASH_BOUND_DECLARATION_NOT_RUNTIME_ATTESTATION",
            expected_closed_trade_ids=list(permission.trade_ids),
        )
        profile = load_source_profile(profile_path)
        if profile.profile_sha256 != permission.profile_sha256:
            raise EvidenceError("SOURCE_PROFILE_DRIFT")
        source.update(source_profile_sha256=profile.profile_sha256,
                      source_profile_id=profile.profile_id,
                      financial_contract_epsilon=profile.financial_contract.epsilon_abs_fonte)
        expected = dict(permission.artifacts)
        before = snapshot_artifact_hashes(snapshot, root)
        source["artifact_hashes_before"] = before
        if _hashes(before, snapshot, root) != expected:
            raise EvidenceError("SOURCE_HASH_MISMATCH")
        evidence = read_authoritative_closed_trades(
            project_root=root, snapshot_path=snapshot, profile=profile,
            include_orders=True, include_population_metadata=True,
        )
        source.update({
            key: evidence.get(key) for key in (
                "snapshot_temp_copy_used", "snapshot_query_only", "snapshot_copy_hashes_verified",
                "snapshot_source_hashes_preserved", "validation_errors",
            )
        })
        if evidence.get("status") != "ok":
            source["reader_reason"] = evidence.get("reason")
            raise EvidenceError("SOURCE_EVIDENCE_UNAVAILABLE")
        if any(evidence.get(key) is not True for key in (
            "snapshot_temp_copy_used", "snapshot_query_only", "snapshot_copy_hashes_verified",
            "snapshot_source_hashes_preserved",
        )):
            raise EvidenceError("SOURCE_CONSISTENCY_UNPROVEN")
        for key in ("snapshot_source_hashes_before", "snapshot_source_hashes_after"):
            if _hashes(_object(evidence.get(key)), snapshot, root) != expected:
                raise EvidenceError("SOURCE_DRIFT")
        population = _object(evidence.get("snapshot_population"))
        total, closed, opened = _population_contract(population, scope)
        trades = _records(evidence.get("rows"))
        orders = _records(evidence.get("orders"))
        ids = [row.get("id") for row in trades]
        if len(trades) != closed or any(type(key) is not int for key in ids):
            raise EvidenceError("SOURCE_POPULATION_INCOMPLETE")
        if sorted(ids) != list(permission.trade_ids):
            raise EvidenceError("CLOSED_COHORT_MEMBERSHIP_MISMATCH")
        lifecycle = reconcile_financial_lifecycle(trades, orders, profile=profile)
        if lifecycle["structural_errors"]:
            source["lifecycle_structural_errors"] = lifecycle["structural_errors"]
            raise EvidenceError("SOURCE_IDENTITIES_AMBIGUOUS_OR_EMPTY")
        analysis = _summarize(trades, lifecycle["trade_results"], total, closed, opened)
        for suffix in ARTIFACT_SUFFIXES.values():
            _reject_links(Path(f"{snapshot}{suffix}"))
        after = snapshot_artifact_hashes(snapshot, root)
        source["artifact_hashes_after"] = after
        if before != after or _hashes(after, snapshot, root) != expected:
            raise EvidenceError("SOURCE_DRIFT")
        _reject_links(permission_path)
        _reject_links(profile_path)
        if _read_authorization(permission_path) != permission_bytes:
            raise EvidenceError("SOURCE_AUTHORIZATION_DRIFT")
        if _sha(profile_path.read_bytes()) != profile.profile_sha256:
            raise EvidenceError("SOURCE_PROFILE_DRIFT")
        report.update(
            analysis=analysis,
            status="OK" if lifecycle["accepted_count"] == closed else "BLOCKED",
            reason=("CLOSED_POPULATION_RECONCILED" if lifecycle["accepted_count"] == closed
                    else "FINANCIAL_QUARANTINES_PRESENT"),
        )
    except EvidenceError as exc:
        report.update(status="BLOCKED", reason=str(exc), analysis=None)
    except (OSError, ValueError, SourceProfileError, DecimalException, RuntimeError) as exc:
        # Only exception type is safe: I/O messages can expose paths or sensitive payloads.
        report.update(status="BLOCKED", reason="SOURCE_INVALID_OR_UNREADABLE",
                      error_type=type(exc).__name__, analysis=None)
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False)
    report["evidence_sha256"] = _sha(encoded.encode("utf-8"))
    return report


def _summarize(
    trades: Sequence[Record], results: Sequence[TradeResult], total: int, closed: int, opened: int,
) -> dict[str, object]:
    accepted = [row for row in results if row["status"] == "accepted"]
    quarantined = [row for row in results if row["status"] != "accepted"]
    if len(results) != closed or len(accepted) + len(quarantined) + opened != total:
        raise EvidenceError("POPULATION_PARTITION_MISMATCH")
    reasons = Counter(reason for row in quarantined for reason in set(row["reasons"]))
    by_id = {row.get("id"): row for row in trades}
    known_reported: list[Decimal] = []
    unknown_reported = 0
    for row in quarantined:
        try:
            known_reported.append(decimal_from_value(by_id[row["trade_id"]].get("close_profit_abs")))
        except FingerprintValidationError:
            unknown_reported += 1
    audited: list[Decimal] = []
    for row in accepted:
        value = row["financial_metrics"].get("reconstructed_net_pnl")
        if value is None:
            raise EvidenceError("F03_ACCEPTED_PNL_UNAVAILABLE")
        audited.append(decimal_from_value(value))
    with localcontext() as context:
        context.prec = 50
        audited_pnl = _sum_pnl(audited)
        unverified_pnl = _sum_pnl(known_reported)
        return {
            "source_total_trade_count": total,
            "analyzed_closed_trade_count": closed,
            "partition_counts": {"accepted": len(accepted), "quarantined": len(quarantined),
                                 "open_not_audited": opened},
            "partition_sum_matches_source": True,
            "closed_reconciliation_coverage_pct": format(Decimal(len(accepted)) * 100 / closed, "f"),
            "whole_source_reconciliation_coverage_pct": format(Decimal(len(accepted)) * 100 / total, "f"),
            "whole_source_fully_reconciled": not quarantined and opened == 0,
            "quarantine_reason_counts_nonexclusive": dict(sorted(reasons.items())),
            "accepted_trade_ids": [row["trade_id"] for row in accepted],
            "quarantined_trade_ids": [row["trade_id"] for row in quarantined],
            "reconciled_subset_net_pnl_usdt": audited_pnl,
            "reconciled_subset_trade_count": len(audited),
            "pnl_semantics": "SUM_F03_RECONSTRUCTED_NET_PNL_ACCEPTED_CLOSED_TRADES_ONLY",
            "quarantined_reported_known_net_pnl_usdt_NOT_CERTIFIED": unverified_pnl,
            "quarantined_reported_pnl_known_count": len(known_reported),
            "quarantined_reported_pnl_unavailable_count": unknown_reported,
            "quarantined_pnl_included_in_reconciled_sum": False,
            "economic_readiness": "NOT_ASSESSED",
            "profitability_inferred": False,
            "time_interval": _interval(trades),
            "trade_results": list(results),
        }


def _population_contract(population: Mapping[str, object], scope: Mapping[str, str]) -> tuple[int, int, int]:
    values = [population.get(key) for key in (
        "total_trade_count", "closed_trade_count", "open_trade_count",
        "unclassified_trade_count", "distinct_trade_id_count", "valid_trade_id_count",
    )]
    if any(type(value) is not int or value < 0 for value in values):
        raise EvidenceError("SOURCE_POPULATION_COUNTS_INVALID")
    total, closed, opened, unclassified, distinct, valid = (int(str(value)) for value in values)
    if unclassified != 0 or total != distinct or total != valid or total != closed + opened:
        raise EvidenceError("SOURCE_POPULATION_AMBIGUOUS")
    if closed == 0:
        raise EvidenceError("CLOSED_POPULATION_EMPTY")
    for field, observed in _object(population.get("scope_values")).items():
        if field not in scope or observed != [scope[field]]:
            raise EvidenceError("SOURCE_SCOPE_CONTRADICTION")
    return total, closed, opened


def _sum_pnl(values: Sequence[Decimal]) -> str | None:
    if not values:
        return None
    minimum_exponent = min(int(str(value.as_tuple().exponent)) for value in values)
    with localcontext() as context:
        context.prec = max(
            50, max(value.adjusted() for value in values) - minimum_exponent
            + len(str(len(values))) + 2,
        )
        return format(sum(values, Decimal(0)), "f")


def _authorization(raw: bytes, snapshot: Path, profile_path: Path) -> Authorization:
    payload = _object(json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_keys))
    fields = {
        "schema_version", "read_authorized", "sqlite_path", "source_profile_sha256",
        "closed_trade_ids", "artifact_sha256", *SCOPE_FIELDS,
    }
    if set(payload) != fields or payload.get("schema_version") != AUTHORIZATION_SCHEMA:
        raise EvidenceError("SOURCE_AUTHORIZATION_SCHEMA_INVALID")
    if payload.get("read_authorized") is not True:
        raise EvidenceError("SOURCE_READ_NOT_AUTHORIZED")
    if payload.get("sqlite_path") != str(snapshot):
        raise EvidenceError("AUTHORIZED_SOURCE_PATH_MISMATCH")
    if payload.get("source_profile_sha256") != _sha(profile_path.read_bytes()):
        raise EvidenceError("AUTHORIZED_PROFILE_HASH_MISMATCH")
    scope: list[tuple[str, str]] = []
    for field in SCOPE_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or LABEL.fullmatch(value) is None:
            raise EvidenceError("SOURCE_SCOPE_INVALID")
        scope.append((field, value))
    if payload["paper_scope"] not in ("CONTROL_A", "TREATMENT_B"):
        raise EvidenceError("SINGLE_PAPER_SCOPE_REQUIRED")
    if HASH.fullmatch(str(payload["account_scope_hash"])) is None:
        raise EvidenceError("ACCOUNT_SCOPE_HASH_INVALID")
    ids = payload.get("closed_trade_ids")
    if not isinstance(ids, list) or any(
        type(key) is not int or not 0 < key <= 9223372036854775807 for key in ids
    ):
        raise EvidenceError("AUTHORIZED_TRADE_IDS_INVALID")
    if len(set(ids)) != len(ids):
        raise EvidenceError("AUTHORIZED_TRADE_IDS_DUPLICATED")
    artifacts = _object(payload.get("artifact_sha256"))
    if set(artifacts) != set(ARTIFACT_SUFFIXES) or artifacts["db"] is None:
        raise EvidenceError("AUTHORIZED_ARTIFACT_SET_INCOMPLETE")
    validated: list[tuple[str, str | None]] = []
    for role in ARTIFACT_SUFFIXES:
        digest = artifacts[role]
        if digest is not None and (not isinstance(digest, str) or HASH.fullmatch(digest) is None):
            raise EvidenceError("AUTHORIZED_ARTIFACT_HASH_INVALID")
        validated.append((role, digest))
    return Authorization(tuple(scope), tuple(sorted(ids)), tuple(validated),
                         str(payload["source_profile_sha256"]))


def _hashes(raw: Mapping[str, object], snapshot: Path, root: Path) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for role, suffix in ARTIFACT_SUFFIXES.items():
        key = Path(f"{snapshot}{suffix}").relative_to(root).as_posix()
        record = _object(raw.get(key))
        digest = record.get("sha256")
        if record.get("exists") is False and digest is None:
            result[role] = None
        elif record.get("exists") is True and isinstance(digest, str) and HASH.fullmatch(digest):
            result[role] = digest
        else:
            raise EvidenceError("SOURCE_ARTIFACT_HASH_UNPROVEN")
    return result


def _interval(trades: Sequence[Record]) -> dict[str, object]:
    starts, ends = [], []
    invalid = 0
    for row in trades:
        try:
            start = datetime_from_value(row.get("open_date"))
            end = datetime_from_value(row.get("close_date"))
            if end < start:
                invalid += 1
            else:
                starts.append(start)
                ends.append(end)
        except (FingerprintValidationError, TypeError, ValueError, OverflowError):
            invalid += 1
    return {
        "semantics": "CLOSED_TRADE_OPEN_DATE_THROUGH_CLOSE_DATE_UTC_NOT_DECISION_EPOCH_BOUNDARY",
        "start_utc": min(starts).isoformat() if starts and invalid == 0 else None,
        "end_utc": max(ends).isoformat() if ends and invalid == 0 else None,
        "invalid_timestamp_trade_count": invalid,
    }


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise EvidenceError("SOURCE_OBJECT_INVALID")
    return dict(value)


def _records(value: object) -> list[Record]:
    if not isinstance(value, list):
        raise EvidenceError("SOURCE_RECORDS_INVALID")
    return [_object(row) for row in value]


def _unique_keys(items: list[tuple[str, object]]) -> dict[str, object]:
    if len({key for key, _ in items}) != len(items):
        raise EvidenceError("SOURCE_AUTHORIZATION_DUPLICATE_KEY")
    return dict(items)


def _reject_links(path: Path) -> None:
    if any(item.is_symlink() or (getattr(item, "is_junction", lambda: False)())
           for item in (path, *path.parents)):
        raise EvidenceError("SOURCE_LINK_FORBIDDEN")


def _path(value: str | Path, *, directory: bool = False) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise EvidenceError("ABSOLUTE_EXPLICIT_PATH_REQUIRED")
    _reject_links(path)
    if not (path.is_dir() if directory else path.is_file()):
        raise EvidenceError("EXPLICIT_SOURCE_INPUT_MISSING")
    return path.resolve(strict=True)


def _read_authorization(path: Path) -> bytes:
    with path.open("rb") as handle:
        raw = handle.read(1_048_577)
    if len(raw) > 1_048_576:
        raise EvidenceError("SOURCE_AUTHORIZATION_TOO_LARGE")
    return raw


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
