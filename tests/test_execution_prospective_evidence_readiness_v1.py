from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    DecisionRecordV42,
    parse_payload_record,
    seal_decision_record,
)
from smartcrypto.research.execution_intelligence import evidence_readiness as readiness
from smartcrypto.research.execution_intelligence.contracts import MarketSlice, canonical_sha256

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config/freqtrade_paper_closed_trades_source_profile_v2.json"
DECISION_PATH = ROOT / "tests/fixtures/decision_ledger_v4_2/valid_decision.json"
NOW = datetime(2026, 7, 20, 12, 0, 10, tzinfo=timezone.utc)


def decision() -> DecisionRecordV42:
    record = parse_payload_record(DECISION_PATH.read_bytes())
    assert isinstance(record, DecisionRecordV42)
    return record


def packet() -> readiness.EvidencePacket:
    execution = readiness.Provenance(
        source_id="execution-export",
        origin="OBSERVED_EXECUTION_EXPORT",
        venue="binance",
        market="usdt-m",
        account_scope_sha256="a" * 64,
        captured_at_utc=NOW,
        clock_basis="OBSERVED_UTC",
    )
    market = readiness.Provenance(
        source_id="l1",
        origin="OBSERVED_MARKET_L1",
        venue="binance",
        market="usdt-m",
        captured_at_utc=NOW,
        clock_basis="OBSERVED_UTC",
    )
    record = decision()
    quote = MarketSlice(
        slice_id="quote-1",
        source_id="l1",
        symbol=record.symbol,
        event_time_utc=record.decision_timestamp - timedelta(seconds=1),
        available_at_utc=record.decision_timestamp,
        best_bid=100,
        best_ask=101,
        bid_quantity=10,
        ask_quantity=11,
        last_price=100.5,
        traded_volume=2,
        source_hash=canonical_sha256(market.model_dump(mode="json")),
    )
    order = readiness.OrderEvidence(
        source_id="execution-export",
        decision_id=record.event_id,
        decision_payload_sha256=record.payload_sha256,
        order_id="order-1",
        symbol=record.symbol,
        side="BUY",
        order_type="LIMIT",
        requested_price=101,
        requested_quantity=1,
        quantity_unit="BASE_ASSET",
        status="FILLED",
        cumulative_filled_quantity=1,
        remaining_quantity=0,
        submit_time_utc=record.decision_timestamp + timedelta(seconds=1),
        ack_time_utc=record.decision_timestamp + timedelta(seconds=2),
        decision_quote_id=quote.slice_id,
        submit_quote_id=quote.slice_id,
    )
    fill = readiness.FillEvidence(
        source_id="execution-export",
        order_id="order-1",
        fill_id="fill-1",
        fill_time_utc=record.decision_timestamp + timedelta(seconds=3),
        price=101,
        quantity=1,
        fee_amount=0.02,
        fee_currency="USDT",
        liquidity_role="taker",
        fee_regime_id="fee-regime-1",
    )
    return readiness.EvidencePacket(
        schema_sha256=readiness.contract_schema_sha256(),
        sources=(execution, market),
        orders=(order,),
        fills=(fill,),
        quotes=(quote,),
    )


def update_packet(value: readiness.EvidencePacket, **changes: object) -> readiness.EvidencePacket:
    payload = value.model_dump(mode="json")
    payload.update(changes)
    return readiness.EvidencePacket.model_validate(payload)


def evaluate(value: readiness.EvidencePacket | None) -> dict[str, Any]:
    return readiness.evaluate_execution_evidence((decision(),), value, as_of_utc=NOW)


def test_complete_observed_fixture_ready_without_operational_authority() -> None:
    value = packet()
    report = evaluate(value)
    assert report["decision"] == "EXECUTION_EVIDENCE_READY"
    assert report["blockers"] == []
    assert report["write_performed"] is False
    assert report["fill_improvement_claim_allowed"] is False
    assert report["l2_status"] == "UNAVAILABLE"
    assert report["safety"]["sends_orders"] is False
    assert report == evaluate(value)


def test_paper_packet_never_becomes_observed_even_when_complete() -> None:
    value = packet()
    sources = [source.model_dump(mode="json") for source in value.sources]
    sources[0]["origin"] = "PAPER_SIMULATION"
    report = evaluate(update_packet(value, sources=sources))
    assert report["decision"] == "BLOCKED_MISSING_EXECUTION_EVIDENCE"
    assert "execution_not_observed:order-1" in report["blockers"]
    assert report["field_inventory"]["fill_price_quantity_time"]["classification"] == "MODELLED"
    assert report["field_inventory"]["observed_decision_order_link"]["present_count"] == 0


@pytest.mark.parametrize(
    "field", ["submit_time_utc", "ack_time_utc", "decision_quote_id", "submit_quote_id"]
)
def test_missing_clock_or_quote_blocks(field: str) -> None:
    value = packet()
    row = value.orders[0].model_dump(mode="json")
    row[field] = None
    report = evaluate(update_packet(value, orders=[row]))
    assert report["status"] == "blocked"


@pytest.mark.parametrize("field", ["fee_amount", "fee_currency", "liquidity_role", "fee_regime_id"])
def test_missing_actual_fee_or_role_never_defaults_to_zero(field: str) -> None:
    value = packet()
    row = value.fills[0].model_dump(mode="json")
    row[field] = None
    report = evaluate(update_packet(value, fills=[row]))
    assert report["status"] == "blocked"


@pytest.mark.parametrize("fee", [0, -0.01])
def test_authoritative_zero_or_rebate_is_not_missing(fee: float) -> None:
    value = packet()
    row = value.fills[0].model_dump(mode="json")
    row["fee_amount"] = fee
    assert evaluate(update_packet(value, fills=[row]))["status"] == "ok"


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision_id", "unrelated"),
        ("decision_payload_sha256", "f" * 64),
        ("symbol", "ETHUSDT"),
        ("side", "SELL"),
    ],
)
def test_exact_identity_no_fuzzy_matching(field: str, value: str) -> None:
    evidence = packet()
    row = evidence.orders[0].model_dump(mode="json")
    row[field] = value
    report = evaluate(update_packet(evidence, orders=[row]))
    assert "decision_order_identity_mismatch:order-1" in report["blockers"]


@pytest.mark.parametrize(
    "field,offset",
    [
        ("event_time_utc", -10),
        ("available_at_utc", 1),
        ("symbol", None),
    ],
)
def test_stale_future_or_other_symbol_l1_blocks(field: str, offset: int | None) -> None:
    value = packet()
    quote = value.quotes[0].model_dump(mode="json")
    quote[field] = (
        "ETHUSDT"
        if offset is None
        else (decision().decision_timestamp + timedelta(seconds=offset)).isoformat()
    )
    report = evaluate(update_packet(value, quotes=[quote]))
    assert "decision_l1_pit_or_provenance_invalid:order-1" in report["blockers"]


def test_schema_and_quote_provenance_hashes_required() -> None:
    value = packet()
    assert (
        "execution_schema_hash_mismatch"
        in evaluate(update_packet(value, schema_sha256="f" * 64))["blockers"]
    )
    row = value.quotes[0].model_dump(mode="json")
    row["source_hash"] = "f" * 64
    assert evaluate(update_packet(value, quotes=[row]))["status"] == "blocked"


def test_duplicates_orphan_fill_and_bad_quantity_block() -> None:
    value = packet()
    assert (
        "execution_identity_collision"
        in evaluate(
            update_packet(
                value,
                fills=[value.fills[0].model_dump(mode="json")] * 2,
            )
        )["blockers"]
    )
    row = value.fills[0].model_dump(mode="json")
    row["order_id"] = "orphan"
    assert "orphan_fill:fill-1" in evaluate(update_packet(value, fills=[row]))["blockers"]
    row["order_id"] = "order-1"
    row["quantity"] = 0.1
    assert (
        "fill_quantity_reconciliation_failed:order-1"
        in evaluate(update_packet(value, fills=[row]))["blockers"]
    )


def test_two_exporters_cannot_hide_same_account_order_collision() -> None:
    value = packet()
    source = value.sources[0].model_dump(mode="json")
    source["source_id"] = "second-exporter"
    order = value.orders[0].model_dump(mode="json")
    order["source_id"] = source["source_id"]
    report = evaluate(
        update_packet(
            value,
            sources=[*[row.model_dump(mode="json") for row in value.sources], source],
            orders=[value.orders[0].model_dump(mode="json"), order],
        )
    )
    assert "account_scoped_identity_collision" in report["blockers"]


@pytest.mark.parametrize(
    "change",
    ["old_capture", "future_capture", "modelled_clock", "capture_before_fill", "ack_after_fill"],
)
def test_source_freshness_and_clock_order_fail_closed(change: str) -> None:
    value = packet()
    sources = [row.model_dump(mode="json") for row in value.sources]
    orders = [row.model_dump(mode="json") for row in value.orders]
    if change == "old_capture":
        sources[0]["captured_at_utc"] = (NOW - timedelta(seconds=301)).isoformat()
    elif change == "future_capture":
        sources[0]["captured_at_utc"] = (NOW + timedelta(seconds=1)).isoformat()
    elif change == "modelled_clock":
        sources[0]["clock_basis"] = "MODELLED"
    elif change == "capture_before_fill":
        sources[0]["captured_at_utc"] = (NOW - timedelta(seconds=3)).isoformat()
    else:
        orders[0]["ack_time_utc"] = (NOW - timedelta(seconds=1)).isoformat()
    assert evaluate(update_packet(value, sources=sources, orders=orders))["status"] == "blocked"


def test_partial_fills_and_cancellations_reconcile_without_synthetic_fill() -> None:
    value = packet()
    order = value.orders[0].model_dump(mode="json")
    order.update(
        status="CANCELED",
        cumulative_filled_quantity=0.4,
        remaining_quantity=0.6,
        cancel_time_utc=NOW.isoformat(),
        cancel_reason="timeout",
    )
    fill = value.fills[0].model_dump(mode="json")
    fill["quantity"] = 0.4
    assert evaluate(update_packet(value, orders=[order], fills=[fill]))["status"] == "ok"
    order["cancel_reason"] = None
    assert (
        "cancellation_evidence_missing:order-1"
        in evaluate(update_packet(value, orders=[order], fills=[fill]))["blockers"]
    )


def test_exit_cannot_substitute_for_entry_and_open_status_cannot_hide_fill() -> None:
    value = packet()
    order = value.orders[0].model_dump(mode="json")
    order.update(order_intent="EXIT", side="SELL")
    assert "observed_order_missing" in evaluate(update_packet(value, orders=[order]))["blockers"]
    order.update(order_intent="ENTRY", side="BUY", status="OPEN")
    assert (
        "open_order_contains_fills_use_partial_or_filled:order-1"
        in evaluate(update_packet(value, orders=[order]))["blockers"]
    )


def test_empty_cohort_null_coverage_and_block_decisions_do_not_need_orders() -> None:
    report = readiness.evaluate_execution_evidence((), None, as_of_utc=NOW)
    assert "empty_eligible_cohort" in report["blockers"]
    assert report["field_inventory"]["decision_id"]["coverage_pct"] is None
    row = decision().model_dump(mode="json", exclude={"payload_sha256"})
    row.update(event_id="blocked-decision", final_decision="BLOCK")
    blocked = seal_decision_record(row)
    report = readiness.evaluate_execution_evidence((decision(), blocked), packet(), as_of_utc=NOW)
    assert report["eligible_count"] == 1
    assert report["status"] == "ok"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0, -1])
def test_invalid_freshness_policy_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="freshness_limits_must_be_positive"):
        readiness.evaluate_execution_evidence(
            (decision(),), packet(), as_of_utc=NOW, max_source_age_seconds=value
        )


def test_contract_rejects_naive_time_nonfinite_and_extra_fields() -> None:
    value = packet().model_dump(mode="json")
    value["fills"][0]["fill_time_utc"] = "2026-07-20T12:00:08"
    with pytest.raises(ValidationError):
        readiness.EvidencePacket.model_validate(value)
    value = packet().model_dump(mode="json")
    value["fills"][0]["price"] = float("nan")
    with pytest.raises(ValidationError):
        readiness.EvidencePacket.model_validate(value)
    value = packet().model_dump(mode="json")
    value["secret"] = "not-allowed"
    with pytest.raises(ValidationError):
        readiness.EvidencePacket.model_validate(value)


def runtime_fixture(root: Path) -> Path:
    ledger = root / "data/runtime/decision_ledger_paper_v1/decision_ledger_v4_2.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(DECISION_PATH.read_bytes().replace(b"\n", b" ") + b"\n")
    csv = root / "data/trades/inbox/freqtrade_paper_closed_trades.csv"
    csv.parent.mkdir(parents=True)
    csv.write_text(
        "order_id,moeda,preco_abertura,taxa_1\nfreqtrade-paper-1,BTCUSDT,101,0.02\n",
        encoding="utf-8",
    )
    snapshot = root / "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
    snapshot.parent.mkdir(parents=True)
    with sqlite3.connect(snapshot) as connection:
        connection.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, order_id TEXT)")
    return ledger


def test_builder_actual_readonly_sources_and_cli_no_write(tmp_path: Path) -> None:
    runtime_fixture(tmp_path)
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    report = readiness.build_execution_evidence_readiness(
        runtime_root=tmp_path,
        profile_path=PROFILE,
        as_of_utc=NOW,
    )
    assert report["decision"] == "BLOCKED_MISSING_EXECUTION_EVIDENCE"
    assert report["eligible_count"] == 1
    paper = report["sources"]["paper_lifecycle"]
    assert paper["classification"] == "MODELLED"
    assert paper["field_inventory"]["entry_price"]["classification"] == "MODELLED"
    assert paper["field_inventory"]["exit_price"]["classification"] == "UNAVAILABLE"
    assert paper["sqlite"]["snapshot_query_only"] is True
    assert paper["sqlite"]["snapshot_source_hashes_preserved"] is True
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "scripts/build_execution_prospective_evidence_readiness_v1.py"),
            "--runtime-root",
            str(tmp_path),
            "--as-of-utc",
            NOW.isoformat(),
            "--json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stdout)["write_performed"] is False
    assert before == {
        str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }


def test_builder_independent_seal_required_and_bad_ledger_blocks(tmp_path: Path) -> None:
    ledger = runtime_fixture(tmp_path)
    archive = tmp_path / "archive.json"
    raw = packet().model_dump_json().encode()
    archive.write_bytes(raw)
    report = readiness.build_execution_evidence_readiness(
        runtime_root=tmp_path,
        profile_path=PROFILE,
        as_of_utc=NOW,
        evidence_path=archive,
        evidence_sha256="f" * 64,
    )
    assert "execution_archive_seal_mismatch" in report["blockers"]
    report = readiness.build_execution_evidence_readiness(
        runtime_root=tmp_path,
        profile_path=PROFILE,
        as_of_utc=NOW,
        evidence_path=archive,
        evidence_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert report["status"] == "ok"
    ledger.write_text("{}\n", encoding="utf-8")
    report = readiness.build_execution_evidence_readiness(
        runtime_root=tmp_path,
        profile_path=PROFILE,
        as_of_utc=NOW,
        evidence_path=archive,
        evidence_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert report["status"] == "blocked"
    assert any(reason.startswith("decision_ledger_invalid:") for reason in report["blockers"])


def test_missing_sources_and_malformed_archive_fail_closed(tmp_path: Path) -> None:
    archive = tmp_path / "invalid.json"
    archive.write_bytes(b"{invalid")
    report = readiness.build_execution_evidence_readiness(
        runtime_root=tmp_path,
        profile_path=PROFILE,
        as_of_utc=NOW,
        evidence_path=archive,
        evidence_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
    )
    assert report["status"] == "blocked"
    assert "execution_archive_invalid:ValidationError" in report["blockers"]


def test_read_stable_detects_mutation_and_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "data.json"
    source.write_bytes(b"{}")
    original = Path.read_bytes
    count = 0

    def changing_read(path: Path) -> bytes:
        nonlocal count
        count += 1
        return original(path) if count == 1 else b"different"

    monkeypatch.setattr(Path, "read_bytes", changing_read)
    with pytest.raises(ValueError, match="source_changed_during_read"):
        readiness._read_stable(source)
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == source)
    with pytest.raises(ValueError, match="source_symlink_forbidden"):
        readiness._read_stable(source)
