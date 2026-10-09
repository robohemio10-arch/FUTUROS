"""Offline execution evidence contract; no collectors or execution authority."""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator

from smartcrypto.data.trader_master_fingerprint_v2.authoritative_sqlite import (
    inspect_sqlite_schema_readonly,
)
from smartcrypto.data.trader_master_fingerprint_v2.source_profile import load_source_profile
from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    DecisionRecordV42,
    FinalDecision,
    PayloadRecordV42,
    TradeLinkRecordV42,
    parse_payload_record,
)
from smartcrypto.execution.decision_ledger_v4_2.schema import build_payload_json_schema
from smartcrypto.research.paper_closed_trades_readonly_source_contract.source_contract import (
    load_closed_trade_source_candidates,
)

from .contracts import (
    FrozenContract,
    Identifier,
    LiquidityRole,
    MarketSlice,
    SafetyContract,
    Sha256Hex,
    canonical_sha256,
    require_utc,
)

SCHEMA_VERSION: Literal["execution_prospective_evidence_v1"] = "execution_prospective_evidence_v1"
Classification = Literal["OBSERVED", "MODELLED", "UNAVAILABLE"]
Origin = Literal["OBSERVED_EXECUTION_EXPORT", "OBSERVED_MARKET_L1", "PAPER_SIMULATION"]
MAX_INPUT_BYTES = 128 * 1024 * 1024


class Provenance(FrozenContract):
    source_id: Identifier
    origin: Origin
    venue: Identifier
    market: Identifier
    account_scope_sha256: Sha256Hex | None = None
    captured_at_utc: datetime
    clock_basis: Literal["OBSERVED_UTC", "MODELLED"]

    @field_validator("captured_at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class OrderEvidence(FrozenContract):
    """Order IDs are namespaced by source/account; never derived from trade ID."""

    source_id: Identifier
    decision_id: Identifier
    decision_payload_sha256: Sha256Hex
    order_id: Identifier
    symbol: Identifier
    side: Literal["BUY", "SELL"]
    order_intent: Literal["ENTRY", "EXIT"] = "ENTRY"
    submit_time_utc: datetime | None = None
    ack_time_utc: datetime | None = None
    order_type: Literal["LIMIT", "MARKET"]
    requested_price: float | None = Field(default=None, gt=0)
    requested_quantity: float = Field(gt=0)
    quantity_unit: Literal["BASE_ASSET"]
    status: Literal["FILLED", "PARTIAL", "CANCELED", "OPEN"]
    cumulative_filled_quantity: float = Field(ge=0)
    remaining_quantity: float = Field(ge=0)
    cancel_time_utc: datetime | None = None
    cancel_reason: Identifier | None = None
    decision_quote_id: Identifier | None = None
    submit_quote_id: Identifier | None = None

    @field_validator("submit_time_utc", "ack_time_utc", "cancel_time_utc")
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        return require_utc(value) if value is not None else None


class FillEvidence(FrozenContract):
    source_id: Identifier
    order_id: Identifier
    fill_id: Identifier
    fill_time_utc: datetime
    price: float = Field(gt=0)
    quantity: float = Field(gt=0)
    fee_amount: float | None = None
    fee_currency: Identifier | None = None
    liquidity_role: LiquidityRole | None = None
    fee_regime_id: Identifier | None = None

    @field_validator("fill_time_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class EvidencePacket(FrozenContract):
    """Manually supplied, sealed archive; hashes prove integrity, not authenticity."""

    schema_version: Literal["execution_prospective_evidence_v1"] = SCHEMA_VERSION
    schema_sha256: Sha256Hex
    sources: tuple[Provenance, ...]
    orders: tuple[OrderEvidence, ...]
    fills: tuple[FillEvidence, ...]
    quotes: tuple[MarketSlice, ...]


def contract_schema_sha256() -> str:
    return canonical_sha256(EvidencePacket.model_json_schema())


def _read_stable(path: Path) -> tuple[bytes, dict[str, Any]]:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("source_symlink_forbidden")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("source_size_limit_exceeded")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) > MAX_INPUT_BYTES or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("source_changed_during_read")
    stat = path.stat()
    return raw, {
        "path": str(path.resolve()),
        "sha256": digest,
        "size_bytes": len(raw),
        "mtime_ns": stat.st_mtime_ns,
        "integrity": "PASS",
    }


def _duplicates(values: list[object]) -> bool:
    return len(values) != len(set(values))


def _coverage(present: int, total: int, classification: Classification) -> dict[str, Any]:
    return {
        "classification": classification if present else "UNAVAILABLE",
        "present_count": present,
        "denominator": total,
        "coverage_pct": round(present / total * 100, 6) if total else None,
    }


def evaluate_execution_evidence(
    decisions: tuple[DecisionRecordV42, ...],
    packet: EvidencePacket | None,
    *,
    as_of_utc: datetime,
    max_source_age_seconds: float = 300,
    max_quote_age_seconds: float = 5,
) -> dict[str, Any]:
    """Validate full ALLOW cohort without time matching or synthetic identity repair."""
    now = require_utc(as_of_utc)
    if any(
        not math.isfinite(value) or value <= 0
        for value in (
            max_source_age_seconds,
            max_quote_age_seconds,
        )
    ):
        raise ValueError("freshness_limits_must_be_positive")
    eligible = {row.event_id: row for row in decisions if row.final_decision == FinalDecision.ALLOW}
    reasons: set[str] = set()
    if _duplicates([row.event_id for row in decisions]):
        reasons.add("decision_identity_collision")
    if not eligible:
        reasons.add("empty_eligible_cohort")
    if any(row.decision_timestamp > now for row in decisions):
        reasons.add("decision_after_observation")
    sources = {row.source_id: row for row in packet.sources} if packet else {}
    orders = packet.orders if packet else ()
    fills = packet.fills if packet else ()
    quotes = {row.slice_id: row for row in packet.quotes} if packet else {}
    if packet is None:
        reasons.add("execution_archive_missing")
    elif packet.schema_sha256 != contract_schema_sha256():
        reasons.add("execution_schema_hash_mismatch")
    if packet and (
        _duplicates([row.source_id for row in packet.sources])
        or _duplicates([row.slice_id for row in packet.quotes])
        or _duplicates([(row.source_id, row.order_id) for row in orders])
        or _duplicates([(row.source_id, row.fill_id) for row in fills])
    ):
        reasons.add("execution_identity_collision")
    for provenance in sources.values():
        age = (now - provenance.captured_at_utc).total_seconds()
        if not 0 <= age <= max_source_age_seconds:
            reasons.add(f"source_stale_or_future:{provenance.source_id}")
        if provenance.clock_basis != "OBSERVED_UTC":
            reasons.add(f"clock_not_observed:{provenance.source_id}")

    def scoped_key(source_id: str, identifier: str) -> tuple[object, ...]:
        provenance = sources.get(source_id)
        if provenance is None:
            return source_id, identifier
        return provenance.venue, provenance.market, provenance.account_scope_sha256, identifier

    if _duplicates([scoped_key(row.source_id, row.order_id) for row in orders]) or _duplicates(
        [scoped_key(row.source_id, row.fill_id) for row in fills]
    ):
        reasons.add("account_scoped_identity_collision")
    linked: set[str] = set()
    observed_linked: set[str] = set()
    counts: Counter[str] = Counter()
    order_map = {(row.source_id, row.order_id): row for row in orders}
    fills_by_order: dict[tuple[str, str], list[FillEvidence]] = {}
    for fill in fills:
        key = (fill.source_id, fill.order_id)
        fills_by_order.setdefault(key, []).append(fill)
        if key not in order_map:
            reasons.add(f"orphan_fill:{fill.fill_id}")
        if fill.fill_time_utc > now:
            reasons.add(f"fill_after_observation:{fill.fill_id}")
        fill_source = sources.get(fill.source_id)
        if fill_source is None or fill_source.captured_at_utc < fill.fill_time_utc:
            reasons.add(f"fill_capture_clock_invalid:{fill.fill_id}")
        if fill.fee_amount is not None and fill.fee_currency and fill.fee_regime_id:
            counts["fee_effective"] += 1
        else:
            reasons.add(f"fill_fee_evidence_missing:{fill.fill_id}")
        if fill.liquidity_role is not None:
            counts["maker_taker"] += 1
        else:
            reasons.add(f"fill_role_missing:{fill.fill_id}")
    for order in orders:
        decision = eligible.get(order.decision_id)
        source = sources.get(order.source_id)
        order_reasons: set[str] = set()
        if source is None or source.origin != "OBSERVED_EXECUTION_EXPORT":
            order_reasons.add("execution_not_observed")
        if source is None or source.account_scope_sha256 is None:
            order_reasons.add("execution_account_scope_missing")
        if decision is None or (
            order.decision_payload_sha256 != decision.payload_sha256
            or order.symbol != decision.symbol
            or order.side
            != (
                "BUY"
                if (decision.side.value == "long") == (order.order_intent == "ENTRY")
                else "SELL"
            )
        ):
            order_reasons.add("decision_order_identity_mismatch")
        else:
            if order.order_intent == "ENTRY":
                linked.add(decision.event_id)
            if (
                order.order_intent == "ENTRY"
                and source
                and source.origin == "OBSERVED_EXECUTION_EXPORT"
            ):
                observed_linked.add(decision.event_id)
        submit, ack = order.submit_time_utc, order.ack_time_utc
        if submit is None or ack is None:
            order_reasons.add("submit_ack_missing")
        elif decision and not decision.decision_timestamp <= submit <= ack <= now:
            order_reasons.add("decision_submit_ack_clock_invalid")
        elif source is None or source.captured_at_utc < ack:
            order_reasons.add("order_capture_clock_invalid")
        else:
            counts["submit_ack"] += 1
        if order.order_type == "LIMIT" and order.requested_price is None:
            order_reasons.add("limit_price_missing")
        linked_fills = fills_by_order.get((order.source_id, order.order_id), [])
        filled = sum(fill.quantity for fill in linked_fills)
        tolerance = max(1e-12, order.requested_quantity * 1e-9)
        if abs(filled - order.cumulative_filled_quantity) > tolerance:
            order_reasons.add("fill_quantity_reconciliation_failed")
        if abs(order.remaining_quantity + filled - order.requested_quantity) > tolerance:
            order_reasons.add("order_quantity_reconciliation_failed")
        if order.status == "FILLED" and (not linked_fills or order.remaining_quantity > tolerance):
            order_reasons.add("filled_order_incomplete")
        if order.status == "PARTIAL" and not (0 < filled < order.requested_quantity):
            order_reasons.add("partial_order_invalid")
        if order.status == "OPEN" and filled > 0:
            order_reasons.add("open_order_contains_fills_use_partial_or_filled")
        if order.status == "CANCELED":
            if order.cancel_time_utc is None or order.cancel_reason is None:
                order_reasons.add("cancellation_evidence_missing")
            elif ack is None or not ack <= order.cancel_time_utc <= now:
                order_reasons.add("cancel_clock_invalid")
            elif source is None or source.captured_at_utc < order.cancel_time_utc:
                order_reasons.add("cancel_capture_clock_invalid")
        for fill in linked_fills:
            if ack is None or fill.fill_time_utc < ack:
                order_reasons.add("ack_fill_clock_invalid")
            if order.cancel_time_utc and fill.fill_time_utc > order.cancel_time_utc:
                order_reasons.add("fill_after_cancel")
        for name, quote_id, reference in (
            (
                "decision",
                order.decision_quote_id,
                decision.decision_timestamp if decision else None,
            ),
            ("submit", order.submit_quote_id, submit),
        ):
            quote = quotes.get(quote_id) if quote_id else None
            quote_source = sources.get(quote.source_id) if quote else None
            if quote is None or reference is None:
                order_reasons.add(f"{name}_l1_missing")
            elif (
                quote_source is None
                or source is None
                or quote_source.origin != "OBSERVED_MARKET_L1"
                or quote_source.venue != source.venue
                or quote_source.market != source.market
                or quote.symbol != order.symbol
                or quote.source_hash != canonical_sha256(quote_source.model_dump(mode="json"))
                or quote.available_at_utc > reference
                or quote_source.captured_at_utc < quote.available_at_utc
                or not 0
                <= (reference - quote.event_time_utc).total_seconds()
                <= max_quote_age_seconds
            ):
                order_reasons.add(f"{name}_l1_pit_or_provenance_invalid")
            else:
                counts[f"{name}_l1"] += 1
        reasons.update(f"{reason}:{order.order_id}" for reason in order_reasons)
    missing_orders = sorted(eligible.keys() - observed_linked)
    if missing_orders:
        reasons.add("observed_order_missing")
    if not fills:
        reasons.add("observed_fills_missing")
    classification: Classification = (
        "OBSERVED"
        if sources and all(source.origin != "PAPER_SIMULATION" for source in sources.values())
        else "MODELLED"
    )
    inventory = {
        "decision_id": _coverage(len(eligible), len(eligible), "OBSERVED"),
        "decision_timestamp": _coverage(len(eligible), len(eligible), "OBSERVED"),
        "decision_order_link": _coverage(len(linked), len(eligible), classification),
        "observed_decision_order_link": _coverage(len(observed_linked), len(eligible), "OBSERVED"),
        "submit_ack": _coverage(counts["submit_ack"], len(orders), classification),
        "order_id": _coverage(len(orders), len(orders), classification),
        "submit_timestamp": _coverage(
            sum(row.submit_time_utc is not None for row in orders), len(orders), classification
        ),
        "ack_timestamp": _coverage(
            sum(row.ack_time_utc is not None for row in orders), len(orders), classification
        ),
        "requested_quantity_unit": _coverage(len(orders), len(orders), classification),
        "fill_price_quantity_time": _coverage(len(fills), len(fills), classification),
        "fill_id": _coverage(len(fills), len(fills), classification),
        "fill_timestamp": _coverage(len(fills), len(fills), classification),
        "fee_effective": _coverage(counts["fee_effective"], len(fills), classification),
        "maker_taker": _coverage(counts["maker_taker"], len(fills), classification),
        "decision_spread_pit": _coverage(counts["decision_l1"], len(orders), "OBSERVED"),
        "submit_spread_pit": _coverage(counts["submit_l1"], len(orders), "OBSERVED"),
        "l2_depth": _coverage(0, len(orders), "UNAVAILABLE"),
        "queue_position": _coverage(0, len(orders), "UNAVAILABLE"),
    }
    return {
        "schema_version": "execution_prospective_evidence_readiness_v1",
        "status": "blocked" if reasons else "ok",
        "reason": sorted(reasons)[0] if reasons else "observed_execution_contract_complete",
        "decision": "BLOCKED_MISSING_EXECUTION_EVIDENCE" if reasons else "EXECUTION_EVIDENCE_READY",
        "blockers": sorted(reasons),
        "as_of_utc": now.isoformat(),
        "schema_sha256": contract_schema_sha256(),
        "eligible_count": len(eligible),
        "order_count": len(orders),
        "fill_count": len(fills),
        "field_inventory": inventory,
        "observed_order_missing_count": len(missing_orders),
        "first_missing_decision_ids": missing_orders[:20],
        "order_state_counts": dict(sorted(Counter(row.status for row in orders).items())),
        "source_freshness": {
            source.source_id: {
                "captured_at_utc": source.captured_at_utc.isoformat(),
                "age_seconds": (now - source.captured_at_utc).total_seconds(),
                "status": "PASS"
                if 0 <= (now - source.captured_at_utc).total_seconds() <= max_source_age_seconds
                else "BLOCKED",
            }
            for source in sources.values()
        },
        "freshness_limits_seconds": {"source": max_source_age_seconds, "l1": max_quote_age_seconds},
        "scope": "basic_cost_and_latency_evidence_not_fill_policy_certification",
        "l1_status": "OBSERVED"
        if orders and counts["decision_l1"] == counts["submit_l1"] == len(orders)
        else "UNAVAILABLE",
        "l2_status": "UNAVAILABLE",
        "queue_status": "UNAVAILABLE",
        "fill_improvement_claim_allowed": False,
        "economic_uplift_claim_allowed": False,
        "write_performed": False,
        "safety": {
            **SafetyContract().model_dump(mode="json"),
            "read_only": True,
            "changes_strategy": False,
            "changes_pnl": False,
            "changes_paper_treatment": False,
            "collectors_activated": False,
        },
    }


def _paper_inventory(root: Path, profile_path: Path) -> dict[str, Any]:
    """Inspect raw lifecycle exports, never invoke their producer or fill defaults."""
    _read_stable(profile_path)
    profile = load_source_profile(profile_path)
    csv_path = root / profile.primary_source_path
    before = _read_stable(csv_path)[1] if csv_path.exists() else None
    loaded = load_closed_trade_source_candidates(
        project_root=root,
        allow_runtime_read=True,
        source_paths=[csv_path],
    )
    rows = loaded.candidates[0].rows if loaded.candidates else []
    fields = {
        name: _coverage(
            sum(row.get(column) not in (None, "") for row in rows), len(rows), "MODELLED"
        )
        for name, column in profile.column_map.items()
    }
    csv_integrity = before == (_read_stable(csv_path)[1] if csv_path.exists() else None)
    snapshot = inspect_sqlite_schema_readonly(
        project_root=root,
        snapshot_path=root / profile.authoritative_sqlite.snapshot_path,
    )
    return {
        "classification": "MODELLED",
        "csv_status": loaded.source_status,
        "csv_reason": loaded.source_reason,
        "closed_trade_count": len(rows),
        "field_inventory": fields,
        "csv_provenance": before,
        "csv_schema_sha256": canonical_sha256(sorted(rows[0])) if rows else None,
        "csv_integrity": "PASS" if csv_integrity else "BLOCKED",
        "profile_sha256": profile.profile_sha256,
        "profile_id": profile.profile_id,
        "order_id_namespace": profile.order_id_namespace,
        "order_id_semantics": profile.order_id_semantics,
        "sqlite": snapshot,
        "limitations": [
            "paper_prices_fees_orders_are_simulated_not_observed_exchange_fills",
            "trade_open_close_are_not_decision_submit_ack_fill_clocks",
            "enter_tag_is_not_maker_taker_role",
            "no_trade_id_to_exchange_order_id_or_fill_id_inference",
        ],
    }


def build_execution_evidence_readiness(
    *,
    runtime_root: Path,
    profile_path: Path,
    as_of_utc: datetime,
    evidence_path: Path | None = None,
    evidence_sha256: str | None = None,
    max_source_age_seconds: float = 300,
    max_quote_age_seconds: float = 5,
) -> dict[str, Any]:
    """Audit persisted inputs only. Source failures are deterministic gate blockers."""
    root = runtime_root.resolve()
    ledger_path = root / "data/runtime/decision_ledger_paper_v1/decision_ledger_v4_2.jsonl"
    decisions: list[DecisionRecordV42] = []
    records: list[PayloadRecordV42] = []
    packet: EvidencePacket | None = None
    sources: dict[str, Any] = {}
    errors: set[str] = set()
    try:
        raw, metadata = _read_stable(ledger_path)
        sources["decision_ledger"] = {
            **metadata,
            "schema_sha256": canonical_sha256(build_payload_json_schema()),
        }
        records = [parse_payload_record(line) for line in raw.splitlines() if line.strip()]
        decisions = [row for row in records if isinstance(row, DecisionRecordV42)]
        if _duplicates([row.event_id for row in records]):
            errors.add("ledger_identity_collision")
        if _duplicates([row.idempotency_key for row in records]):
            errors.add("ledger_idempotency_collision")
        decision_map = {row.event_id: row for row in decisions}
        for row in records:
            if isinstance(row, TradeLinkRecordV42):
                parent = decision_map.get(row.parent_event_id)
                if parent is None or any(
                    (
                        row.decision_payload_sha256 != parent.payload_sha256,
                        row.signal_id != parent.signal_id,
                        row.candidate_id != parent.candidate_id,
                        row.correlation_id != parent.correlation_id,
                        row.symbol != parent.symbol,
                        row.pair != parent.pair,
                        row.side != parent.side,
                        row.decision_timestamp != parent.decision_timestamp,
                    )
                ):
                    errors.add("ledger_trade_link_parent_mismatch")
        sources["decision_ledger"].update(
            record_count=len(records),
            decision_count=len(decisions),
            trade_link_count=sum(isinstance(row, TradeLinkRecordV42) for row in records),
            classification="OBSERVED",
            observed_semantics="decisions_not_exchange_fills",
            first_decision_utc=min((row.decision_timestamp for row in decisions), default=None),
            last_decision_utc=max((row.decision_timestamp for row in decisions), default=None),
        )
    except (OSError, ValueError) as exc:
        errors.add(f"decision_ledger_invalid:{type(exc).__name__}")
        decisions = []
    try:
        sources["paper_lifecycle"] = _paper_inventory(root, profile_path)
        paper = sources["paper_lifecycle"]
        if (
            paper["csv_integrity"] != "PASS"
            or not paper["sqlite"]["snapshot_source_hashes_preserved"]
        ):
            errors.add("paper_source_integrity_failed")
    except (OSError, ValueError) as exc:
        errors.add(f"paper_source_invalid:{type(exc).__name__}")
    if evidence_path is not None:
        try:
            raw, metadata = _read_stable(evidence_path)
            sources["execution_archive"] = metadata
            if evidence_sha256 is None or metadata["sha256"] != evidence_sha256:
                errors.add("execution_archive_seal_mismatch")
            else:
                packet = EvidencePacket.model_validate_json(raw)
        except (OSError, ValueError, ValidationError) as exc:
            errors.add(f"execution_archive_invalid:{type(exc).__name__}")
    report = evaluate_execution_evidence(
        tuple(decisions),
        packet,
        as_of_utc=as_of_utc,
        max_source_age_seconds=max_source_age_seconds,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    report["sources"] = sources
    report["blockers"] = sorted(set(report["blockers"]) | errors)
    if report["blockers"]:
        report.update(
            status="blocked",
            decision="BLOCKED_MISSING_EXECUTION_EVIDENCE",
            reason=report["blockers"][0],
        )
    ledger = sources.get("decision_ledger", {})
    for field in ("first_decision_utc", "last_decision_utc"):
        value = ledger.get(field)
        if value is not None:
            ledger[field] = value.isoformat()
    report["missing_fields"] = sorted(
        name
        for name, value in report["field_inventory"].items()
        if value["classification"] == "UNAVAILABLE" or value["present_count"] < value["denominator"]
    )
    report["report_sha256"] = canonical_sha256(report)
    return report
