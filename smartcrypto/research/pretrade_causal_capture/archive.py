"""Explicit isolated writer and no-write coverage over immutable evidence."""

from __future__ import annotations

import json
import time
from collections import Counter
from itertools import islice
from pathlib import Path
from typing import Literal, TypedDict

from smartcrypto.ops.execution_decision_l1.archive import validate_external_root
from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import (
    AtomicWriteError,
    AtomicWritePolicy,
    _InterProcessFileLock,
    atomic_write_json,
)

from .capture import CaptureConfig, CaptureSession, causal_reasons
from .contracts import (
    CaptureRecord,
    Evidence,
    LifecycleRecord,
    decision_identity_matches,
    digest,
    lineage_aliases,
    parse_record,
    trade_identity_matches,
)


class CoverageGroup(TypedDict):
    epoch_id: str
    boundary: str
    symbol: str
    side: str
    counts: dict[str, int]


class CoverageReport(TypedDict):
    schema_version: Literal["paper_pretrade_causal_coverage_v1"]
    status: Literal["blocked", "structural_only", "no_data"]
    runtime_integration: Literal["BLOCKED_FOR_RUNTIME_INTEGRATION"]
    totals: dict[str, int]
    quarantine_reasons: dict[str, int]
    groups: list[CoverageGroup]
    synthetic_records_excluded: int
    test_evidence_included: bool
    coverage_denominator: Literal["captured_opportunities_at_named_boundary_only"]
    pre_boundary_rejections_proven: Literal[False]
    operational_authority: Literal[False]
    financial_certified: Literal[False]
    write_performed: Literal[False]
    real_prospective_collection_proven: Literal[False]


def identity_key(record: Evidence) -> tuple[str, str, str]:
    stage = (
        "decision"
        if record.stage in {"selector_rejected", "risk_rejected", "allow"}
        else record.stage
    )
    return record.epoch_id, record.opportunity_id, stage


class EvidenceArchive:
    def __init__(
        self,
        root: Path,
        *,
        authorized: bool = False,
        forbidden_roots: tuple[Path, ...],
        max_records: int = 4096,
        max_bytes: int = 32 * 1024**2,
    ) -> None:
        if not 1 <= max_records <= 16384 or not 65536 <= max_bytes <= 128 * 1024**2:
            raise ValueError("archive_bounds_invalid")
        self.root = root
        self.authorized = authorized
        self.forbidden_roots = forbidden_roots
        self.max_records = max_records
        self.max_bytes = max_bytes
        self.write_performed = False

    def read(self) -> tuple[Evidence, ...]:
        root = validate_external_root(self.root, self.forbidden_roots)
        if not root.exists():
            return ()
        paths = list(islice(root.glob("*/*.json"), self.max_records + 1))
        if len(paths) > self.max_records:
            raise ValueError("archive_record_budget_exceeded")
        records: list[Evidence] = []
        total = 0
        for path in sorted(paths):
            validate_external_root(path, self.forbidden_roots)
            size = path.stat().st_size
            total += size
            if size > 65536 or total > self.max_bytes:
                raise ValueError("archive_byte_budget_exceeded")
            with path.open("rb") as handle:
                raw = handle.read(65537)
            with path.open("rb") as handle:
                repeated = handle.read(65537)
            if len(raw) != size or repeated != raw:
                raise ValueError("archive_changed_during_read")
            record = parse_record(json.loads(raw))
            expected = root / digest(record.epoch_id) / (digest(identity_key(record)) + ".json")
            if path != expected:
                raise ValueError("archive_identity_path_mismatch")
            records.append(record)
        return tuple(records)

    def persist(self, record: Evidence) -> bool:
        if not self.authorized or not self.forbidden_roots:
            raise ValueError("explicit_external_write_authorization_required")
        record = parse_record(record.model_dump(mode="json"))
        root = validate_external_root(self.root, self.forbidden_roots)
        self.write_performed = True
        root.mkdir(parents=True, exist_ok=True)
        lock = _InterProcessFileLock(root / ".capture.guard", timeout_seconds=0.05)
        lock.acquire()
        try:
            existing = self.read()
            for previous in existing:
                if identity_key(previous) == identity_key(record):
                    if previous.record_sha256 != record.record_sha256:
                        raise ValueError("persisted_identity_conflict")
                    return False
            rendered_size = len(
                (
                    json.dumps(record.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
                ).encode()
            )
            used = sum(p.stat().st_size for p in root.glob("*/*.json"))
            if (
                len(existing) >= self.max_records
                or rendered_size > 65536
                or used + rendered_size > self.max_bytes
            ):
                raise ValueError("archive_budget_exceeded")
            path = root / digest(record.epoch_id) / (digest(identity_key(record)) + ".json")
            validate_external_root(path, self.forbidden_roots)
            policy = AtomicWritePolicy.restricted([root], lock_timeout_seconds=0.05)
            atomic_write_json(path, record.model_dump(mode="json"), policy=policy, allow_nan=False)
            return True
        finally:
            lock.release()

    def drain(
        self, session: CaptureSession, *, max_records: int = 8, deadline_seconds: float = 0.1
    ) -> int:
        """Only the isolated consumer calls this; deadline is checked between I/O calls."""
        if not session.config.enabled:
            return 0
        if not 1 <= max_records <= 256 or not 0 < deadline_seconds <= 1:
            raise ValueError("drain_bounds_invalid")
        deadline = time.monotonic() + deadline_seconds
        count = 0
        for _ in range(max_records):
            if time.monotonic() >= deadline:
                session.fail("writer_deadline_exceeded")
                break
            record = session.take_pending()
            if record is None:
                break
            try:
                self.persist(record)
            except (OSError, ValueError, AtomicWriteError):
                session.fail("archive_persistence_failed")
                break
            count += 1
            if time.monotonic() > deadline:
                session.fail("writer_deadline_exceeded")
                break
        return count


def coverage(records: tuple[Evidence, ...], *, include_synthetic: bool = False) -> CoverageReport:
    """Order-independent structural coverage; never financial/runtime certification."""
    unique: dict[tuple[str, str, str], Evidence] = {}
    conflicts: set[tuple[str, str]] = set()
    excluded = 0
    for value in records:
        record = parse_record(value.model_dump(mode="json"))
        if record.synthetic and not include_synthetic:
            excluded += 1
            continue
        key = identity_key(record)
        previous = unique.get(key)
        if previous is not None and previous.record_sha256 != record.record_sha256:
            conflicts.add(key[:2])
        if previous is None or record.record_sha256 < previous.record_sha256:
            unique[key] = record
    totals: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    groups: dict[tuple[str, str, str, str], Counter[str]] = {}
    captures = [r for r in unique.values() if isinstance(r, CaptureRecord)]
    owners: dict[tuple[str, str, str], set[tuple[str, str]]] = {}
    for record in unique.values():
        for kind, alias_value in lineage_aliases(record):
            owners.setdefault((record.epoch_id, kind, alias_value), set()).add(
                (record.epoch_id, record.opportunity_id)
            )
    for identities in owners.values():
        if len(identities) > 1:
            conflicts.update(identities)
    totals["orphan_lifecycle_records"] = sum(
        isinstance(r, LifecycleRecord) and (r.epoch_id, r.opportunity_id, "generated") not in unique
        for r in unique.values()
    )
    for capture in captures:
        opportunity_key = capture.epoch_id, capture.opportunity_id
        group_key = capture.epoch_id, capture.boundary, capture.symbol, capture.side
        group = groups.setdefault(group_key, Counter())
        counts: Counter[str] = Counter(opportunities=1, valid_identity=1, vectors_captured=1)
        decision = unique.get((*opportunity_key, "decision"))
        trade = unique.get((*opportunity_key, "trade_linked"))
        outcome = unique.get((*opportunity_key, "outcome"))
        bad: list[str] = list(
            causal_reasons(
                capture,
                decision.decision_timestamp_utc if isinstance(decision, LifecycleRecord) else None,
                CaptureConfig(),
            )
        )
        if opportunity_key in conflicts:
            bad.append("IDENTITY_CONFLICT")
        if not isinstance(decision, LifecycleRecord):
            counts["pending_decision"] += 1
        else:
            if (
                decision.capture_sha256 != capture.record_sha256
                or decision.synthetic != capture.synthetic
            ):
                bad.append("IDENTITY_MISMATCH")
            bad.extend(decision.quarantine_reasons)
            if decision.decision_timestamp_utc > decision.observed_at_utc:
                bad.append("DECISION_FUTURE")
            if decision.decision is not None:
                d = decision.decision
                if not decision_identity_matches(capture, d):
                    bad.append("IDENTITY_MISMATCH")
            counts[decision.stage] += 1
            if not bad:
                counts["causally_complete"] += 1
        if isinstance(trade, LifecycleRecord):
            if (
                not isinstance(decision, LifecycleRecord)
                or decision.stage != "allow"
                or trade.capture_sha256 != capture.record_sha256
                or trade.decision != decision.decision
                or trade.trade_link is None
                or decision.decision is None
                or not trade_identity_matches(trade.trade_link, decision.decision)
                or trade.synthetic != capture.synthetic
                or trade.observed_at_utc < decision.observed_at_utc
                or trade.trade_link.execution_timestamp > trade.observed_at_utc
                or trade.quarantine_reasons
            ):
                bad.append("TRADE_LINK_INVALID")
            else:
                counts["trade_linked"] += 1
        if isinstance(outcome, LifecycleRecord):
            if (
                not counts["trade_linked"]
                or not isinstance(trade, LifecycleRecord)
                or outcome.trade_link != trade.trade_link
                or outcome.capture_sha256 != capture.record_sha256
                or outcome.decision != trade.decision
                or outcome.synthetic != capture.synthetic
                or outcome.observed_at_utc < trade.observed_at_utc
                or outcome.quarantine_reasons
                or outcome.close_time_utc is None
                or outcome.trade_link is None
                or not outcome.trade_link.execution_timestamp
                <= outcome.close_time_utc
                <= outcome.observed_at_utc
            ):
                bad.append("OUTCOME_LINK_INVALID")
            else:
                counts["outcome_known"] += 1
        counts["outcome_unknown"] = 1 - counts["outcome_known"]
        if bad:
            counts["quarantined"] += 1
            counts["causally_complete"] = 0
            if any("IDENTITY" in reason or "LINK_INVALID" in reason for reason in bad):
                counts["valid_identity"] = 0
            reasons.update(set(bad))
        totals.update(counts)
        group.update(counts)
    return {
        "schema_version": "paper_pretrade_causal_coverage_v1",
        "status": "blocked"
        if reasons or totals["orphan_lifecycle_records"]
        else "structural_only"
        if captures
        else "no_data",
        "runtime_integration": "BLOCKED_FOR_RUNTIME_INTEGRATION",
        "totals": dict(totals),
        "quarantine_reasons": dict(sorted(reasons.items())),
        "groups": [
            dict(epoch_id=k[0], boundary=k[1], symbol=k[2], side=k[3], counts=dict(v))
            for k, v in sorted(groups.items())
        ],
        "synthetic_records_excluded": excluded,
        "test_evidence_included": include_synthetic,
        "coverage_denominator": "captured_opportunities_at_named_boundary_only",
        "pre_boundary_rejections_proven": False,
        "operational_authority": False,
        "financial_certified": False,
        "write_performed": False,
        "real_prospective_collection_proven": False,
    }
