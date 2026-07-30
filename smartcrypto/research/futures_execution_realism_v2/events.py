"""Deterministic event ordering, replay detection, and injected simulation clock."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from .contracts import ContractViolation, MarketEvent, parse_utc


@dataclass(frozen=True)
class EventStreamValidation:
    ordered_events: tuple[MarketEvent, ...]
    replay_event_ids: tuple[str, ...]
    duplicate_sequence_count: int
    input_out_of_order: bool
    deterministic_hash: str


class SimulationClock:
    """Monotonic injected clock; it never sleeps or reads wall-clock time."""

    def __init__(self, start_time_utc: datetime) -> None:
        self._now = parse_utc(start_time_utc)

    @property
    def now(self) -> datetime:
        return self._now

    def advance_to(self, value: datetime) -> datetime:
        target = parse_utc(value)
        if target < self._now:
            raise ContractViolation("simulation_clock_regression")
        self._now = target
        return self._now


def event_sort_key(event: MarketEvent) -> tuple[datetime, int, datetime, str]:
    return (
        event.event_time_utc,
        event.sequence,
        event.receive_time_utc,
        event.event_id,
    )


def order_events(events: Iterable[MarketEvent]) -> tuple[MarketEvent, ...]:
    return tuple(sorted(events, key=event_sort_key))


def validate_event_stream(
    events: Sequence[MarketEvent],
    *,
    reject_out_of_order: bool = True,
) -> EventStreamValidation:
    if not events:
        raise ContractViolation("event_stream_empty")
    ordered = order_events(events)
    input_out_of_order = tuple(events) != ordered
    if reject_out_of_order and input_out_of_order:
        raise ContractViolation("event_stream_out_of_order")

    replay_ids: list[str] = []
    deduplicated: list[MarketEvent] = []
    event_hash_by_id: dict[str, str] = {}
    sequence_hashes: dict[tuple[str, str, int], str] = {}
    sequence_times: dict[tuple[str, str], tuple[int, datetime]] = {}
    duplicate_sequence_count = 0

    for event in ordered:
        previous_hash = event_hash_by_id.get(event.event_id)
        if previous_hash is not None:
            if previous_hash != event.content_hash:
                raise ContractViolation("event_id_payload_conflict")
            replay_ids.append(event.event_id)
            continue
        event_hash_by_id[event.event_id] = event.content_hash

        sequence_key = (event.source, event.symbol, event.sequence)
        sequence_hash = sequence_hashes.get(sequence_key)
        if sequence_hash is not None:
            duplicate_sequence_count += 1
            if sequence_hash != event.content_hash:
                raise ContractViolation("duplicate_sequence_conflict")
            replay_ids.append(event.event_id)
            continue
        sequence_hashes[sequence_key] = event.content_hash

        stream_key = (event.source, event.symbol)
        previous = sequence_times.get(stream_key)
        if previous is not None:
            previous_sequence, previous_time = previous
            if event.event_time_utc > previous_time and event.sequence <= previous_sequence:
                raise ContractViolation("regressive_event_sequence")
        sequence_times[stream_key] = (event.sequence, event.event_time_utc)
        deduplicated.append(event)

    from smartcrypto.data.canonical_data_foundation_v2.contracts import stable_hash

    deterministic_hash = stable_hash([event.to_dict() for event in deduplicated])
    return EventStreamValidation(
        ordered_events=tuple(deduplicated),
        replay_event_ids=tuple(sorted(replay_ids)),
        duplicate_sequence_count=duplicate_sequence_count,
        input_out_of_order=input_out_of_order,
        deterministic_hash=deterministic_hash,
    )


def events_available_at(
    events: Iterable[MarketEvent],
    decision_time_utc: datetime,
) -> tuple[MarketEvent, ...]:
    decision_time = parse_utc(decision_time_utc)
    return tuple(
        event
        for event in order_events(events)
        if event.receive_time_utc <= decision_time
        and event.event_time_utc <= decision_time
    )
