"""Independent, reviewed source provenance; never an F04 execution authorization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit

from . import population_evidence as f04
from .authoritative_sqlite import read_authoritative_closed_trades, snapshot_artifact_hashes
from .source_profile import SourceProfileError, load_source_profile


SCHEMA_VERSION = "paper_financial_source_authority_preflight_f05_v1"
ORIGIN_SCHEMA = "paper_financial_source_origin_f05_v1"
EXPORT_SCHEMA = "paper_financial_source_export_receipt_f05_v1"
MAX_EVIDENCE_AGE = timedelta(hours=1)


@dataclass(frozen=True)
class InstitutionalReview:
    """Out-of-band review of independent origin evidence, not a requester-supplied hash."""

    origin_sha256: str
    issuer_id: str
    producer_id: str
    reviewer_id: str
    review_reference: str
    valid_until_utc: str


# No operational origin has been independently reviewed for F05. Provisioning requires
# a separate institutional review/code change; there is deliberately no CLI/env override.
INSTITUTIONAL_REVIEWS: tuple[InstitutionalReview, ...] = ()


def audit_paper_financial_source_authority(
    *, project_root: str | Path, sqlite_path: str | Path,
    source_profile_path: str | Path, authorization_path: str | Path,
    origin_path: str | Path, effective_config_path: str | Path, export_receipt_path: str | Path,
) -> dict[str, object]:
    """Validate one provenance chain and immutable query-only snapshot; do not run F04."""
    evidence: dict[str, object] = {}
    report: dict[str, object] = {
        "schema_version": SCHEMA_VERSION, "status": "BLOCKED", "reason": "NOT_EVALUATED",
        "blockers": [], "evidence": evidence, "preflight_validated": False,
        "execution_authorized": False, "f04_executed": False,
        "write_performed": False, "source_writes_performed": False,
        "operational_authority": False, "runtime_parity_certified": False,
        "pnl_certified": False, "economic_readiness": "NOT_ASSESSED",
        "recovery_applied": False, "sends_orders": False,
    }
    try:
        # Establish trust before opening any requester-selected financial source.
        origin_file = f04._path(origin_path)
        origin_bytes = _read(origin_file)
        origin = _json(origin_bytes)
        review = _review(origin_bytes, origin, _utc_now())
        evidence.update(origin_sha256=f04._sha(origin_bytes),
                        review_reference=review.review_reference,
                        trust_basis="INDEPENDENT_ORIGIN_REVIEW_NOT_REQUESTER_DECLARATION")
        root = f04._path(project_root, directory=True)
        snapshot = f04._path(sqlite_path)
        snapshot.relative_to(root)
        documents = {
            "origin": origin_file, "config": f04._path(effective_config_path),
            "receipt": f04._path(export_receipt_path), "profile": f04._path(source_profile_path),
            "authorization": f04._path(authorization_path),
        }
        artifacts = [Path(f"{snapshot}{suffix}") for suffix in f04.ARTIFACT_SUFFIXES.values()]
        _distinct_inputs([*documents.values(), *artifacts])
        before_documents = {name: _read(path) for name, path in documents.items()}
        if before_documents["origin"] != origin_bytes:
            raise f04.EvidenceError("ORIGIN_EVIDENCE_DRIFT")
        for name, field in (
            ("config", "effective_config_sha256"), ("receipt", "export_receipt_sha256"),
            ("profile", "source_profile_sha256"), ("authorization", "f04_authorization_sha256"),
        ):
            if origin[field] != f04._sha(before_documents[name]):
                raise f04.EvidenceError(f"{name.upper()}_CHAIN_HASH_MISMATCH")
        permission = f04._authorization(
            before_documents["authorization"], snapshot, documents["profile"],
        )
        scope = dict(permission.scope)
        if origin["scope"] != scope:
            raise f04.EvidenceError("ORIGIN_F04_SCOPE_MISMATCH")
        config = _json(before_documents["config"])
        receipt = _json(before_documents["receipt"])
        _export_contract(origin, config, receipt, permission, snapshot, _utc_now())
        profile = load_source_profile(documents["profile"])
        if profile.profile_sha256 != permission.profile_sha256:
            raise f04.EvidenceError("SOURCE_PROFILE_DRIFT")
        expected = dict(permission.artifacts)
        before_hashes = snapshot_artifact_hashes(snapshot, root)
        if f04._hashes(before_hashes, snapshot, root) != expected:
            raise f04.EvidenceError("SNAPSHOT_ARTIFACT_CHAIN_MISMATCH")
        source = read_authoritative_closed_trades(
            project_root=root, snapshot_path=snapshot, profile=profile,
            include_orders=True, include_population_metadata=True,
        )
        if source.get("status") != "ok":
            raise f04.EvidenceError("SOURCE_QUERY_ONLY_EVIDENCE_UNAVAILABLE")
        if any(source.get(field) is not True for field in (
            "snapshot_temp_copy_used", "snapshot_query_only", "snapshot_copy_hashes_verified",
            "snapshot_source_hashes_preserved",
        )):
            raise f04.EvidenceError("SOURCE_CONSISTENT_READ_UNPROVEN")
        for key in ("snapshot_source_hashes_before", "snapshot_source_hashes_after"):
            if f04._hashes(f04._object(source.get(key)), snapshot, root) != expected:
                raise f04.EvidenceError("SOURCE_DRIFT")
        population = f04._object(source.get("snapshot_population"))
        total, closed, opened = f04._population_contract(population, scope)
        if (receipt["population_total_trade_count"] != total
                or receipt["population_open_trade_count"] != opened):
            raise f04.EvidenceError("EXPORT_POPULATION_MISMATCH")
        rows = f04._records(source.get("rows"))
        ids = [row.get("id") for row in rows]
        if (len(rows) != closed or any(type(key) is not int for key in ids)
                or sorted(ids) != list(permission.trade_ids)):
            raise f04.EvidenceError("EXPORT_CLOSED_MEMBERSHIP_MISMATCH")
        _order_identities(f04._records(source.get("orders")), rows)
        _distinct_inputs([*documents.values(), *artifacts])
        if snapshot_artifact_hashes(snapshot, root) != before_hashes:
            raise f04.EvidenceError("SOURCE_DRIFT")
        if any(_read(path) != before_documents[name] for name, path in documents.items()):
            raise f04.EvidenceError("PROVENANCE_INPUT_DRIFT")
        _review(origin_bytes, origin, _utc_now())
        _export_contract(origin, config, receipt, permission, snapshot, _utc_now())
        evidence.update(
            scope=scope, producer_id=origin["producer_id"],
            runtime_instance_id=origin["runtime_instance_id"],
            observed_at_utc=origin["observed_at_utc"], expires_at_utc=origin["expires_at_utc"],
            source_profile_id=profile.profile_id, source_profile_sha256=profile.profile_sha256,
            f04_authorization_sha256=origin["f04_authorization_sha256"],
            export_receipt_sha256=origin["export_receipt_sha256"],
            snapshot_artifact_sha256=expected, closed_trade_ids=list(permission.trade_ids),
            source_hashes_preserved=True, snapshot_query_only=True,
            temporary_query_copy_used=True,
        )
        report.update(status="VALIDATED", preflight_validated=True,
                      reason="SOURCE_PREFLIGHT_VALIDATED_NOT_EXECUTION_AUTHORIZATION")
    except f04.EvidenceError as exc:
        report.update(reason=str(exc), blockers=[str(exc)])
    except (OSError, ValueError, SourceProfileError, RuntimeError) as exc:
        # Exception text may contain DB credentials or private source paths.
        report.update(reason="PROVENANCE_INVALID_OR_UNREADABLE",
                      blockers=["PROVENANCE_INVALID_OR_UNREADABLE"], error_type=type(exc).__name__)
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False)
    report["evidence_sha256"] = f04._sha(encoded.encode("utf-8"))
    return report


def _review(raw: bytes, origin: dict[str, object], now: datetime) -> InstitutionalReview:
    matches = [item for item in INSTITUTIONAL_REVIEWS if item.origin_sha256 == f04._sha(raw)]
    if len(matches) != 1:
        raise f04.EvidenceError("INDEPENDENT_INSTITUTIONAL_REVIEW_MISSING_OR_AMBIGUOUS")
    review = matches[0]
    fields = {
        "schema_version", "issuer_id", "requester_id", "producer_id", "runtime_instance_id",
        "runtime_image_sha256", "observed_at_utc", "expires_at_utc", "config_resolution_complete",
        "effective_config_sha256", "export_receipt_sha256", "source_profile_sha256",
        "f04_authorization_sha256", "scope",
    }
    if set(origin) != fields or origin.get("schema_version") != ORIGIN_SCHEMA:
        raise f04.EvidenceError("ORIGIN_SCHEMA_INVALID")
    for field in ("issuer_id", "requester_id", "producer_id", "runtime_instance_id"):
        _label(origin[field])
    for value in (review.reviewer_id, review.review_reference):
        _label(value)
    if (origin["issuer_id"] != review.issuer_id or origin["producer_id"] != review.producer_id
            or len({review.reviewer_id, origin["issuer_id"], origin["requester_id"],
                    origin["producer_id"]}) != 4):
        raise f04.EvidenceError("INDEPENDENT_ORIGIN_IDENTITIES_UNPROVEN")
    for field in ("runtime_image_sha256", "effective_config_sha256", "export_receipt_sha256",
                  "source_profile_sha256", "f04_authorization_sha256"):
        _digest(origin[field])
    if origin["config_resolution_complete"] is not True:
        raise f04.EvidenceError("EFFECTIVE_CONFIG_RESOLUTION_UNPROVEN")
    observed, expires = _time(origin["observed_at_utc"]), _time(origin["expires_at_utc"])
    if not observed <= now < expires <= _time(review.valid_until_utc):
        raise f04.EvidenceError("ORIGIN_OR_REVIEW_STALE_OR_FUTURE")
    if expires - observed > MAX_EVIDENCE_AGE or now - observed > MAX_EVIDENCE_AGE:
        raise f04.EvidenceError("ORIGIN_EVIDENCE_TOO_OLD")
    return review


def _export_contract(
    origin: dict[str, object], config: dict[str, object], receipt: dict[str, object],
    permission: f04.Authorization, snapshot: Path, now: datetime,
) -> None:
    fields = {
        "schema_version", "producer_id", "runtime_instance_id", "source_db_url", "captured_at_utc",
        "export_method", "source_query_only", "consistent_export", "scope", "snapshot_path",
        "source_artifact_sha256_before", "source_artifact_sha256_after", "snapshot_artifact_sha256",
        "closed_trade_ids", "population_total_trade_count", "population_open_trade_count",
    }
    if set(receipt) != fields or receipt.get("schema_version") != EXPORT_SCHEMA:
        raise f04.EvidenceError("EXPORT_RECEIPT_SCHEMA_INVALID")
    if (config.get("dry_run") is not True or not _absolute_sqlite_url(config.get("db_url"))
            or config["db_url"] != receipt["source_db_url"]):
        raise f04.EvidenceError("EFFECTIVE_PAPER_DB_URL_UNPROVEN")
    if (receipt["producer_id"] != origin["producer_id"]
            or receipt["runtime_instance_id"] != origin["runtime_instance_id"]):
        raise f04.EvidenceError("EXPORT_PRODUCER_IDENTITY_MISMATCH")
    if receipt["scope"] != dict(permission.scope):
        raise f04.EvidenceError("EXPORT_SCOPE_MISMATCH")
    if receipt["snapshot_path"] != str(snapshot):
        raise f04.EvidenceError("EXPORT_SNAPSHOT_IDENTITY_MISMATCH")
    if (receipt["export_method"] != "SQLITE_BACKUP" or receipt["source_query_only"] is not True
            or receipt["consistent_export"] is not True):
        raise f04.EvidenceError("CONSISTENT_READ_ONLY_EXPORT_UNPROVEN")
    captured, observed = _time(receipt["captured_at_utc"]), _time(origin["observed_at_utc"])
    if not now - MAX_EVIDENCE_AGE <= captured <= observed <= now:
        raise f04.EvidenceError("SNAPSHOT_CUSTODY_STALE_OR_FUTURE")
    source_before = _artifact_set(receipt["source_artifact_sha256_before"])
    if source_before != _artifact_set(receipt["source_artifact_sha256_after"]):
        raise f04.EvidenceError("EXPORT_SOURCE_ARTIFACT_DRIFT")
    if _artifact_set(receipt["snapshot_artifact_sha256"]) != dict(permission.artifacts):
        raise f04.EvidenceError("EXPORT_F04_ARTIFACT_MISMATCH")
    ids = receipt["closed_trade_ids"]
    if (not isinstance(ids, list) or any(type(key) is not int for key in ids)
            or ids != list(permission.trade_ids)):
        raise f04.EvidenceError("EXPORT_F04_MEMBERSHIP_MISMATCH")
    for key in ("population_total_trade_count", "population_open_trade_count"):
        if type(receipt[key]) is not int or int(str(receipt[key])) < 0:
            raise f04.EvidenceError("EXPORT_POPULATION_COUNTS_INVALID")


def _order_identities(orders: list[f04.Record], trades: list[f04.Record]) -> None:
    trade_pairs: dict[int, str] = {}
    if not trades:
        raise f04.EvidenceError("SOURCE_TRADE_IDENTITIES_AMBIGUOUS")
    for trade in trades:
        trade_id, pair = trade.get("id"), trade.get("pair")
        if (type(trade_id) is not int or trade_id <= 0 or trade_id in trade_pairs
                or not isinstance(pair, str) or not pair.strip()):
            raise f04.EvidenceError("SOURCE_TRADE_IDENTITIES_AMBIGUOUS")
        trade_pairs[trade_id] = pair

    local_ids: set[int] = set()
    exchange_ids: set[tuple[str, str]] = set()
    parents: set[int] = set()
    for row in orders:
        key, external, parent = row.get("id"), row.get("order_id"), row.get("ft_trade_id")
        pair = row.get("ft_pair")
        if (type(key) is not int or key <= 0 or key in local_ids
                or not isinstance(external, str) or not external.strip()
                or type(parent) is not int or parent not in trade_pairs
                or not isinstance(pair, str) or not pair.strip() or pair != trade_pairs[parent]):
            raise f04.EvidenceError("SOURCE_ORDER_IDENTITIES_AMBIGUOUS")
        identity = (pair, external)
        if identity in exchange_ids:
            raise f04.EvidenceError("SOURCE_ORDER_IDENTITIES_AMBIGUOUS")
        local_ids.add(key)
        exchange_ids.add(identity)
        parents.add(parent)
    if parents != set(trade_pairs):
        raise f04.EvidenceError("SOURCE_TRADE_ORDER_EVIDENCE_INCOMPLETE")


def _absolute_sqlite_url(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sqlite:///"):
        return False
    url = urlsplit(value)
    path = value[len("sqlite:///"):]
    return (not url.netloc and not url.query and not url.fragment and "%" not in path
            and "\x00" not in path and ".." not in path.replace("\\", "/").split("/")
            and (PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute())
            and path.endswith((".sqlite", ".db")))


def _artifact_set(value: object) -> dict[str, object]:
    items = f04._object(value)
    if set(items) != set(f04.ARTIFACT_SUFFIXES) or items["db"] is None:
        raise f04.EvidenceError("PROVENANCE_ARTIFACT_SET_INCOMPLETE")
    for digest in items.values():
        if digest is not None:
            _digest(digest)
    return items


def _distinct_inputs(paths: list[Path]) -> None:
    for path in paths:
        f04._reject_links(path)
    if len(set(paths)) != len(paths):
        raise f04.EvidenceError("PROVENANCE_INPUT_IDENTITIES_OVERLAP")
    existing = [path for path in paths if path.exists()]
    for index, path in enumerate(existing):
        if any(path.samefile(other) for other in existing[index + 1:]):
            raise f04.EvidenceError("PROVENANCE_INPUT_IDENTITIES_OVERLAP")


def _json(raw: bytes) -> dict[str, object]:
    return f04._object(json.loads(raw.decode("utf-8-sig"), object_pairs_hook=f04._unique_keys))


def _read(path: Path) -> bytes:
    f04._reject_links(path)
    return f04._read_authorization(path)


def _digest(value: object) -> None:
    if not isinstance(value, str) or f04.HASH.fullmatch(value) is None:
        raise f04.EvidenceError("PROVENANCE_HASH_INVALID")


def _label(value: object) -> None:
    if not isinstance(value, str) or f04.LABEL.fullmatch(value) is None:
        raise f04.EvidenceError("PROVENANCE_IDENTITY_INVALID")


def _time(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise f04.EvidenceError("PROVENANCE_UTC_TIMESTAMP_INVALID")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.utcoffset() != timedelta(0):
        raise f04.EvidenceError("PROVENANCE_UTC_TIMESTAMP_INVALID")
    return parsed


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
