"""Independent Paper ledger tail and public snapshots with bounded loss accounting."""

from __future__ import annotations

import os
import logging
import stat
import threading
import time
import traceback
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any, Callable, TypeVar

from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    DecisionRecordV42,
    parse_payload_record,
)

from .archive import ExternalArchive
from .contracts import (
    CollectorConfig,
    DecisionL1Association,
    EventRecord,
    GapEvidence,
    L1Quote,
    RawBookTicker,
    build_quote,
    schema_sha256,
    require_utc,
)
from .transport import FetchError, PublicClient, PublicHTTPClient, ReceivedTicker

T = TypeVar("T")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Metrics:
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter(
            {
                key: 0
                for key in (
                    "public_requests",
                    "public_request_failures",
                    "reconnections",
                    "quotes_accepted",
                    "invalid_quotes",
                    "clock_regressions",
                    "market_time_regressions",
                    "gaps",
                    "polling_gaps",
                    "repeated_snapshots",
                    "history_evictions",
                    "quote_queue_drops",
                    "decision_queue_drops",
                    "notice_queue_drops",
                    "archive_queue_drops",
                    "decision_records_read",
                    "decisions_observed",
                    "matched_decisions",
                    "unavailable_decisions",
                    "duplicate_decisions",
                    "identity_collisions",
                    "pre_session_decisions_ignored",
                    "collector_failures",
                    "shutdown_workers_alive",
                    "shutdown_producers_alive",
                    "quotes_discarded_at_shutdown",
                    "decisions_discarded_at_shutdown",
                )
            }
        )
        self.lock = threading.Lock()

    def add(self, key: str, value: int = 1) -> None:
        with self.lock:
            self.counts[key] += value

    def snapshot(self) -> dict[str, int]:
        with self.lock:
            return dict(sorted(self.counts.items()))


class L1State:
    def __init__(self, config: CollectorConfig, started: datetime, metrics: Metrics) -> None:
        self.config = config
        self.started = require_utc(started)
        self.metrics = metrics
        self.history: dict[str, deque[L1Quote]] = {
            symbol: deque(maxlen=config.history_per_symbol) for symbol in config.symbols
        }
        self.gaps: dict[str, deque[datetime]] = {
            symbol: deque(maxlen=config.history_per_symbol) for symbol in config.symbols
        }
        self.gap_eviction_floor: dict[str, datetime] = {}
        self.decisions: dict[str, str] = {}
        self.idempotency: dict[str, str] = {}
        self.clock_fault = False
        self.last_quote_clock: tuple[datetime, int] | None = None

    def gap(self, at: datetime, reason: str, symbol: str | None = None) -> GapEvidence:
        for key in [symbol] if symbol else self.history:
            rows = self.gaps.get(key)
            if rows is None:
                continue
            if len(rows) == rows.maxlen:
                self.gap_eviction_floor[key] = max(
                    self.gap_eviction_floor.get(key, rows[0]), rows[0]
                )
            rows.append(at)
        self.metrics.add("gaps")
        return GapEvidence(observed_at_utc=at, symbol=symbol, reason=reason)

    def accept(self, quote: L1Quote) -> bool:
        if quote.symbol not in self.history:
            self.metrics.add("unsupported_quotes")
            return False
        if self.last_quote_clock and (
            quote.available_at_utc < self.last_quote_clock[0]
            or quote.available_monotonic_ns <= self.last_quote_clock[1]
        ):
            self.clock_fault = True
            self.metrics.add("clock_regressions")
            return False
        self.last_quote_clock = quote.available_at_utc, quote.available_monotonic_ns
        rows = self.history[quote.symbol]
        if rows:
            previous = rows[-1]
            if (
                quote.event_time_utc
                and previous.event_time_utc
                and quote.event_time_utc < previous.event_time_utc
            ):
                self.metrics.add("market_time_regressions")
                return False
            if (
                quote.available_at_utc - previous.available_at_utc
            ).total_seconds() > self.config.gap_seconds:
                self.metrics.add("polling_gaps")
            if quote.raw_sha256 == previous.raw_sha256:
                self.metrics.add("repeated_snapshots")
            if len(rows) == rows.maxlen:
                self.metrics.add("history_evictions")
        rows.append(quote)
        self.metrics.add("quotes_accepted")
        return True

    def associate(
        self, decision: DecisionRecordV42, observed: datetime
    ) -> DecisionL1Association | None:
        if decision.decision_timestamp < self.started:
            self.metrics.add("pre_session_decisions_ignored")
            return None
        previous = self.decisions.get(decision.event_id)
        if previous == decision.payload_sha256:
            self.metrics.add("duplicate_decisions")
            return None
        if previous is not None or (
            decision.idempotency_key in self.idempotency
            and self.idempotency[decision.idempotency_key] != decision.event_id
        ):
            self.metrics.add("identity_collisions")
            raise ValueError("decision_identity_collision")
        if len(self.decisions) >= self.config.max_decisions:
            raise ValueError("decision_identity_budget_exceeded")
        self.decisions[decision.event_id] = decision.payload_sha256
        self.idempotency[decision.idempotency_key] = decision.event_id
        self.metrics.add("decisions_observed")
        reason = "NO_CAUSAL_QUOTE"
        selected: L1Quote | None = None
        age: float | None = None
        if self.clock_fault:
            reason = "LOCAL_CLOCK_REGRESSION"
        else:
            for quote in reversed(self.history.get(decision.symbol, ())):
                if quote.available_at_utc > decision.decision_timestamp:
                    continue
                if quote.event_time_utc is None:
                    reason = "EVENT_TIME_UNAVAILABLE"
                    break
                floor = self.gap_eviction_floor.get(decision.symbol)
                if floor and quote.receive_time_utc <= floor:
                    reason = "GAP_HISTORY_EVICTED"
                    break
                if any(
                    quote.receive_time_utc <= gap <= decision.decision_timestamp
                    for gap in self.gaps.get(decision.symbol, ())
                ):
                    reason = "SOURCE_GAP_AFTER_QUOTE"
                    break
                candidate_age = (decision.decision_timestamp - quote.event_time_utc).total_seconds()
                local_age = (decision.decision_timestamp - quote.available_at_utc).total_seconds()
                if (
                    not 0 <= candidate_age <= self.config.max_quote_age_seconds
                    or local_age > self.config.max_quote_age_seconds
                ):
                    reason = "QUOTE_STALE"
                    break
                selected, age, reason = quote, candidate_age, "STRICT_PIT_MATCH"
                break
        self.metrics.add("matched_decisions" if selected else "unavailable_decisions")
        return DecisionL1Association(
            decision_id=decision.event_id,
            candidate_id=decision.candidate_id,
            signal_id=decision.signal_id,
            decision_payload_sha256=decision.payload_sha256,
            symbol=decision.symbol,
            decision_timestamp_utc=decision.decision_timestamp,
            observed_at_utc=observed,
            quote=selected,
            status="MATCHED_PIT" if selected else "UNAVAILABLE",
            reason=reason,
            feature_age_seconds=age,
        )


class LedgerTail:
    """EOF-only reader; rotation/truncation halts rather than replaying old records."""

    MAX_LINE_BYTES = 65536

    def __init__(self, path: Path) -> None:
        if any(
            part.is_symlink()
            or (
                part.exists()
                and getattr(part.lstat(), "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
            )
            for part in (path, *path.parents)
        ):
            raise ValueError("ledger_symlink_forbidden")
        if not path.is_file():
            raise ValueError("ledger_file_required")
        self.path = path
        self.handle = path.open("rb")
        info = os.fstat(self.handle.fileno())
        self.identity = info.st_dev, info.st_ino
        self.handle.seek(0, os.SEEK_END)
        end = self.handle.tell()
        self.skip_partial = False
        if end:
            self.handle.seek(end - 1)
            self.skip_partial = self.handle.read(1) != b"\n"
        self.handle.seek(end)
        self.partial = b""

    def poll(self) -> list[DecisionRecordV42]:
        info = self.path.stat()
        if (info.st_dev, info.st_ino) != self.identity or info.st_size < self.handle.tell():
            raise ValueError("ledger_rotation_or_truncation")
        decisions: list[DecisionRecordV42] = []
        for _ in range(32):
            line = self.handle.readline(self.MAX_LINE_BYTES + 1)
            if not line:
                break
            if len(line) + len(self.partial) > self.MAX_LINE_BYTES:
                raise ValueError("ledger_line_budget_exceeded")
            self.partial += line
            if not self.partial.endswith(b"\n"):
                break
            raw, self.partial = self.partial, b""
            if self.skip_partial:
                self.skip_partial = False
                continue
            record = parse_payload_record(raw)
            if isinstance(record, DecisionRecordV42):
                decisions.append(record)
        return decisions

    def close(self) -> None:
        self.handle.close()


@dataclass(frozen=True)
class Notice:
    at: datetime
    reason: str
    symbol: str | None = None


class Collector:
    def __init__(
        self,
        ledger: Path,
        config: CollectorConfig,
        *,
        client: PublicClient | None = None,
        archive: ExternalArchive | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.ledger = ledger
        self.config = config
        self.client = client if client is not None else PublicHTTPClient()
        self.archive = archive
        self.now = now
        self.metrics = Metrics()
        self.stop_event = threading.Event()
        self.consumer_done = threading.Event()
        self.producers_done = threading.Event()
        self.quotes: Queue[ReceivedTicker] = Queue(config.queue_capacity)
        self.decisions: Queue[DecisionRecordV42] = Queue(config.queue_capacity)
        self.notices: Queue[Notice] = Queue(config.queue_capacity)
        self.records: Queue[EventRecord] = Queue(config.queue_capacity)
        self.failure: str | None = None
        self.failure_lock = threading.Lock()
        self.started: datetime | None = None

    def enqueue(self, queue: Queue[T], item: T, counter: str) -> bool:
        try:
            queue.put_nowait(item)
            return True
        except Full:
            self.metrics.add(counter)
            return False

    def halt(self, reason: str) -> None:
        with self.failure_lock:
            if self.failure is None:
                self.failure = reason
        self.metrics.add("collector_failures")
        self.stop_event.set()

    def record(self, value: EventRecord) -> None:
        if self.archive is not None:
            self.enqueue(self.records, value, "archive_queue_drops")

    def _guarded(self, operation: Callable[[], None]) -> None:
        try:
            operation()
        except Exception as exc:
            # Preserve stack location, not untrusted exception text or payload values.
            logging.getLogger(__name__).error(
                "collector_worker_failed",
                extra={
                    "exception_type": type(exc).__name__,
                    "traceback_frames": [
                        (frame.filename, frame.name, frame.lineno)
                        for frame in traceback.extract_tb(exc.__traceback__)
                    ],
                },
            )
            self.halt(f"unexpected_worker_{type(exc).__name__}")

    def _network(self) -> None:
        failures = 0
        while not self.stop_event.is_set():
            wait = self.config.poll_seconds
            for symbol in self.config.symbols:
                if self.stop_event.is_set():
                    break
                try:
                    self.metrics.add("public_requests")
                    received = self.client.fetch(symbol, self.config.request_timeout_seconds)
                    if self.stop_event.is_set():
                        self.metrics.add("quotes_discarded_at_shutdown")
                        break
                    if not self.enqueue(self.quotes, received, "quote_queue_drops"):
                        self.enqueue(
                            self.notices,
                            Notice(self.now(), "QUOTE_QUEUE_DROP", symbol),
                            "notice_queue_drops",
                        )
                    if failures:
                        self.metrics.add("reconnections")
                    failures = 0
                except FetchError as exc:
                    failures += 1
                    self.metrics.add("public_request_failures")
                    self.enqueue(
                        self.notices, Notice(self.now(), exc.reason, symbol), "notice_queue_drops"
                    )
                    wait = max(
                        min(self.config.backoff_max_seconds, 2 ** min(failures, 12)),
                        exc.retry_after_seconds,
                    )
                    break
            self.stop_event.wait(wait)

    def _ledger(self, tail: LedgerTail) -> None:
        try:
            while not self.stop_event.is_set():
                for decision in tail.poll():
                    self.metrics.add("decision_records_read")
                    if self.stop_event.is_set():
                        self.metrics.add("decisions_discarded_at_shutdown")
                        continue
                    self.enqueue(self.decisions, decision, "decision_queue_drops")
                self.stop_event.wait(self.config.ledger_poll_seconds)
        except (OSError, ValueError) as exc:
            self.halt(f"ledger_{type(exc).__name__}")
        finally:
            tail.close()

    def _consume(self, state: L1State) -> None:
        last_loss_count = 0
        try:
            while not self.producers_done.is_set() or any(
                not queue.empty() for queue in (self.notices, self.quotes, self.decisions)
            ):
                metrics = self.metrics.snapshot()
                loss_count = metrics.get("quote_queue_drops", 0) + metrics.get(
                    "notice_queue_drops", 0
                )
                if loss_count > last_loss_count:
                    self.record(state.gap(self.now(), "INGESTION_LOSS_BARRIER"))
                    last_loss_count = loss_count
                for _ in range(32):
                    try:
                        notice = self.notices.get_nowait()
                    except Empty:
                        break
                    self.record(state.gap(notice.at, notice.reason, notice.symbol))
                for _ in range(32):
                    try:
                        received = self.quotes.get_nowait()
                    except Empty:
                        break
                    try:
                        RawBookTicker.model_validate_json(received.raw_body)
                        available = self.now()
                        if available < received.receive_time_utc:
                            state.clock_fault = True
                            self.metrics.add("clock_regressions")
                            raise ValueError("local_receive_clock_regression")
                        quote = build_quote(
                            received.raw_body,
                            received.receive_time_utc,
                            available,
                            time.monotonic_ns(),
                        )
                        if quote.symbol != received.symbol:
                            raise ValueError("requested_symbol_mismatch")
                        previous = state.history.get(quote.symbol)
                        if (
                            previous
                            and (
                                quote.available_at_utc - previous[-1].available_at_utc
                            ).total_seconds()
                            > self.config.gap_seconds
                        ):
                            self.record(
                                GapEvidence(
                                    observed_at_utc=quote.available_at_utc,
                                    symbol=quote.symbol,
                                    reason="POLLING_GAP",
                                )
                            )
                        if state.accept(quote):
                            self.record(quote)
                        else:
                            self.record(
                                state.gap(available, "QUOTE_MONOTONICITY_FAILURE", received.symbol)
                            )
                    except ValueError:
                        self.metrics.add("invalid_quotes")
                        self.record(state.gap(self.now(), "INVALID_QUOTE", received.symbol))
                for _ in range(32):
                    try:
                        decision = self.decisions.get_nowait()
                    except Empty:
                        break
                    association = state.associate(decision, self.now())
                    if association is not None:
                        self.record(association)
                time.sleep(0.01)
        except ValueError as exc:
            self.halt(f"consumer_{type(exc).__name__}")
        finally:
            self.consumer_done.set()

    def _write(self) -> None:
        if self.archive is None:
            return
        try:
            while not self.consumer_done.is_set() or not self.records.empty():
                try:
                    value = self.records.get(timeout=0.1)
                except Empty:
                    continue
                self.archive.record(value)
            self.archive.flush()
            summary = self.report()
            # This worker cannot certify its own bounded join in the main thread.
            summary["collector_gate"] = "PENDING_FINAL_SHUTDOWN_REPORT"
            summary["shutdown_validation"] = "PROCESS_REPORT_REQUIRED"
            self.archive.close(summary)
        except (OSError, ValueError, RuntimeError) as exc:
            self.halt(f"archive_{type(exc).__name__}")

    def run(self, duration_seconds: float) -> dict[str, Any]:
        if not 0 < duration_seconds <= 86400:
            raise ValueError("duration_must_be_between_zero_and_86400")
        if self.started is not None:
            raise ValueError("collector_session_is_single_use")
        tail = LedgerTail(self.ledger)
        self.started = self.now()
        state = L1State(self.config, self.started, self.metrics)
        producer_threads = [
            threading.Thread(target=self._guarded, args=(operation,), daemon=True)
            for operation in (self._network, lambda: self._ledger(tail))
        ]
        consumer = threading.Thread(
            target=self._guarded, args=(lambda: self._consume(state),), daemon=True
        )
        writer = threading.Thread(target=self._guarded, args=(self._write,), daemon=True)
        threads = [*producer_threads, consumer, writer]
        launched: list[threading.Thread] = []
        try:
            for thread in threads:
                thread.start()
                launched.append(thread)
            self.stop_event.wait(duration_seconds)
        except KeyboardInterrupt:
            self.metrics.add("keyboard_interrupts")
        except RuntimeError:
            self.halt("thread_start_failure")
        finally:
            self.stop_event.set()
            deadline = time.monotonic() + self.config.shutdown_seconds
            for thread in producer_threads:
                if thread in launched:
                    thread.join(max(0, deadline - time.monotonic()))
            if producer_threads[1] not in launched:
                tail.close()
            producer_live = sum(thread.is_alive() for thread in producer_threads)
            if producer_live:
                self.metrics.add("shutdown_producers_alive", producer_live)
                self.failure = "shutdown_incomplete"
            self.producers_done.set()
            if consumer in launched:
                consumer.join(max(0, deadline - time.monotonic()))
            else:
                self.consumer_done.set()
            if writer in launched:
                writer.join(max(0, deadline - time.monotonic()))
            live = sum(thread.is_alive() for thread in threads)
            if live:
                self.metrics.add("shutdown_workers_alive", live)
                self.failure = "shutdown_incomplete"
        return self.report()

    def report(self) -> dict[str, Any]:
        metrics = self.metrics.snapshot()
        observed = metrics.get("decisions_observed", 0)
        loss = any(
            value
            for key, value in metrics.items()
            if key.endswith("_drops") or key.endswith("_at_shutdown")
        )
        undrained = (
            self.decisions.qsize()
            + self.quotes.qsize()
            + self.notices.qsize()
            + self.records.qsize()
        )
        loss = loss or undrained > 0
        return {
            "schema_version": "execution_decision_l1_collector_report_v1",
            "schema_sha256": schema_sha256(),
            "status": "blocked" if self.failure else "ok",
            "reason": self.failure or "collector_session_completed",
            "collector_gate": "BLOCKED_COLLECTOR_SESSION"
            if self.failure
            else "COLLECTOR_READY_FOR_OPT_IN",
            "execution_readiness": "BLOCKED_MISSING_EXECUTION_EVIDENCE",
            "started_at_utc": self.started.isoformat() if self.started else None,
            "metrics": metrics,
            "pit_coverage_pct": metrics.get("matched_decisions", 0) / observed * 100
            if observed and not loss and not self.failure
            else None,
            "coverage_status": "PARTIAL_LOSS" if loss else "CONSUMED_UNIQUE_DECISIONS_ONLY",
            "loss_detected": loss,
            "network_calls_executed": metrics.get("public_requests", 0) > 0,
            "source_status": "OBSERVED" if metrics.get("quotes_accepted", 0) else "UNAVAILABLE",
            "shutdown_complete": not metrics.get("shutdown_workers_alive", 0)
            if self.producers_done.is_set() and self.consumer_done.is_set()
            else False,
            "sampling": "PUBLIC_REST_SNAPSHOTS_NOT_CONTINUOUS_ORDER_BOOK",
            "clock_synchronization": "UNPROVEN",
            "write_performed": self.archive is not None and self.archive.write_performed,
            "archive_records_undrained": self.records.qsize(),
            "ingestion_records_undrained": undrained - self.records.qsize(),
            "archive_session": str(self.archive.session) if self.archive else None,
            "safety": {
                "research_only": True,
                "paper_only": True,
                "shadow_only": True,
                "operational_authority": False,
                "sends_orders": False,
                "exchange_private_access": False,
                "changes_paper_treatment": False,
                "changes_strategy": False,
                "changes_risk": False,
                "changes_pnl": False,
                "observed_fills_provided": False,
                "economic_uplift_claim_allowed": False,
                "runtime_activation_performed": False,
            },
        }
