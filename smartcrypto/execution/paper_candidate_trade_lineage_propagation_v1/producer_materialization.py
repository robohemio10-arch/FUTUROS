"""Explicit prospective lineage materialization for the Paper signal producer.

Identity is materialized only when the concrete pre-execution source row
already carries exact candidate provenance and an explicit signal occurrence.
No candidate is inferred from symbol, side, score, threshold, list position or
timestamp proximity. Invalid or missing lineage blocks attribution only and the
operational signal is returned unchanged.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .adapter import materialize_concrete_signal_identity
from .mapper import CandidateLineageError
from .publication import attach_materialized_identity_to_signal


SCHEMA_VERSION = "paper_candidate_trade_lineage_signal_producer_materialization_v1"

_FORBIDDEN_SOURCE_FIELD_PATTERNS = (
    "label",
    "target",
    "outcome",
    "realized_pnl",
    "realized_profit",
    "win_loss",
    "future_return",
    "future_ret",
)


@dataclass(frozen=True)
class SignalProducerLineageMaterializationReportV1:
    source_signal_count: int
    materialized_count: int
    blocked_count: int
    status: str
    reason: str
    failures: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "reason": self.reason,
            "source_signal_count": self.source_signal_count,
            "materialized_count": self.materialized_count,
            "blocked_count": self.blocked_count,
            "failures": [dict(item) for item in self.failures],
            "partial_materialization_allowed": True,
            "materialization_changes_execution_decision": False,
            "missing_lineage_blocks_execution": False,
            "candidate_scope_inference_allowed": False,
            "symbol_side_candidate_inference_allowed": False,
            "timestamp_nearest_matching_allowed": False,
            "list_position_candidate_matching_allowed": False,
            "trade_id_as_candidate_id_allowed": False,
            "legacy_registry_fallback_allowed": False,
            "wall_clock_signal_instance_fallback_allowed": False,
            "synthetic_candidate_id_allowed": False,
            "synthetic_signal_id_allowed": False,
            "synthetic_correlation_id_allowed": False,
            "writes_runtime": False,
            "writes_sqlite": False,
            "changes_risk": False,
            "changes_model": False,
            "sends_orders": False,
            "live": False,
            "canary": False,
        }


@dataclass(frozen=True)
class SignalProducerLineageMaterializationOutcomeV1:
    signals: tuple[dict[str, Any], ...]
    report: SignalProducerLineageMaterializationReportV1


def materialize_signal_batch_from_explicit_provenance(
    *,
    signals: Sequence[Mapping[str, Any]],
    source_rows: Sequence[Mapping[str, Any]],
    research_report: Mapping[str, Any] | None,
    registry_report: Mapping[str, Any] | None,
    producer_id: str,
) -> SignalProducerLineageMaterializationOutcomeV1:
    baseline = tuple(dict(item) for item in signals)
    rows = tuple(dict(item) for item in source_rows)

    if len(baseline) != len(rows):
        return _all_blocked(baseline, reason="signal_source_row_count_mismatch")

    research_candidates = _list_of_mappings(
        (research_report or {}).get("signal_candidates")
    )
    registry_candidates = _list_of_mappings(
        (registry_report or {}).get("candidates")
    )

    research_by_identity, research_error = _index_research_candidates(
        research_candidates
    )
    registry_by_id, registry_error = _index_registry_candidates(
        registry_candidates
    )
    if research_error is not None:
        return _all_blocked(baseline, reason=research_error)
    if registry_error is not None:
        return _all_blocked(baseline, reason=registry_error)

    output: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    materialized_count = 0

    for index, (signal, source_row) in enumerate(
        zip(baseline, rows, strict=True)
    ):
        try:
            enriched = _materialize_one(
                signal=signal,
                source_row=source_row,
                research_by_identity=research_by_identity,
                registry_by_id=registry_by_id,
                producer_id=producer_id,
            )
        except CandidateLineageError as exc:
            output.append(dict(signal))
            failures.append(
                {
                    "signal_index": index,
                    "symbol": signal.get("symbol"),
                    "side": signal.get("side"),
                    "reason": str(exc),
                }
            )
        except Exception as exc:
            output.append(dict(signal))
            failures.append(
                {
                    "signal_index": index,
                    "symbol": signal.get("symbol"),
                    "side": signal.get("side"),
                    "reason": (
                        "unexpected_materialization_error:"
                        f"{type(exc).__name__}"
                    ),
                }
            )
        else:
            output.append(enriched)
            materialized_count += 1

    blocked_count = len(output) - materialized_count
    if materialized_count == len(output) and output:
        status = "ok"
        reason = "all_explicit_lineage_materialized"
    elif materialized_count > 0:
        status = "partial"
        reason = "partial_explicit_lineage_materialized"
    elif output:
        status = "blocked"
        reason = "no_explicit_lineage_materialized"
    else:
        status = "empty"
        reason = "no_signals"

    return SignalProducerLineageMaterializationOutcomeV1(
        signals=tuple(output),
        report=SignalProducerLineageMaterializationReportV1(
            source_signal_count=len(output),
            materialized_count=materialized_count,
            blocked_count=blocked_count,
            status=status,
            reason=reason,
            failures=tuple(failures),
        ),
    )


def _materialize_one(
    *,
    signal: Mapping[str, Any],
    source_row: Mapping[str, Any],
    research_by_identity: Mapping[tuple[str, str], Mapping[str, Any]],
    registry_by_id: Mapping[str, Mapping[str, Any]],
    producer_id: str,
) -> dict[str, Any]:
    forbidden = _first_forbidden_source_path(source_row)
    if forbidden is not None:
        raise CandidateLineageError(
            f"source_row_contains_post_outcome_field:{forbidden}"
        )

    source_candidate_id = _required_source_text(
        source_row,
        "source_candidate_id",
    )
    signal_candidate_id = _required_source_text(
        source_row,
        "signal_candidate_id",
    )
    signal_instance_id = _required_source_text(
        source_row,
        "signal_instance_id",
    )
    signal_timestamp_utc = _required_source_text(
        source_row,
        "signal_timestamp_utc",
    )
    regime = _source_regime(source_row)

    research_candidate = research_by_identity.get(
        (source_candidate_id, signal_candidate_id)
    )
    if research_candidate is None:
        raise CandidateLineageError(
            "explicit_research_candidate_identity_not_found"
        )

    registry_candidate = registry_by_id.get(source_candidate_id)
    if registry_candidate is None:
        raise CandidateLineageError(
            "explicit_registry_candidate_identity_not_found"
        )

    occurrence_source_sha256 = _source_occurrence_sha256(
        source_row=source_row,
        signal=signal,
        producer_id=producer_id,
        regime=regime,
    )

    binding = materialize_concrete_signal_identity(
        research_candidate,
        registry_candidate,
        {
            "producer_id": producer_id,
            "signal_instance_id": signal_instance_id,
            "signal_timestamp_utc": signal_timestamp_utc,
            "pair": str(signal.get("pair") or "").strip(),
            "symbol": str(signal.get("symbol") or "").strip(),
            "side": str(signal.get("side") or "").strip().lower(),
            "regime": regime,
            "occurrence_source_sha256": occurrence_source_sha256,
        },
        producer_id=producer_id,
    )
    return attach_materialized_identity_to_signal(signal, binding)


def _index_research_candidates(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[tuple[str, str], Mapping[str, Any]], str | None]:
    output: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        source_candidate_id = _optional_text(row.get("source_candidate_id"))
        signal_candidate_id = _optional_text(row.get("signal_candidate_id"))
        if source_candidate_id is None or signal_candidate_id is None:
            continue
        key = (source_candidate_id, signal_candidate_id)
        if key in output:
            return {}, "duplicate_research_candidate_identity"
        output[key] = row
    return output, None


def _index_registry_candidates(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Mapping[str, Any]], str | None]:
    output: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        candidate_id = _optional_text(row.get("candidate_id"))
        if candidate_id is None:
            continue
        if candidate_id in output:
            return {}, "duplicate_registry_candidate_identity"
        output[candidate_id] = row
    return output, None


def _all_blocked(
    baseline: Sequence[Mapping[str, Any]],
    *,
    reason: str,
) -> SignalProducerLineageMaterializationOutcomeV1:
    copied = tuple(dict(item) for item in baseline)
    failures = tuple(
        {
            "signal_index": index,
            "symbol": item.get("symbol"),
            "side": item.get("side"),
            "reason": reason,
        }
        for index, item in enumerate(copied)
    )
    return SignalProducerLineageMaterializationOutcomeV1(
        signals=copied,
        report=SignalProducerLineageMaterializationReportV1(
            source_signal_count=len(copied),
            materialized_count=0,
            blocked_count=len(copied),
            status="blocked" if copied else "empty",
            reason=reason if copied else "no_signals",
            failures=failures,
        ),
    )


def _required_source_text(
    source: Mapping[str, Any],
    field: str,
) -> str:
    value = _optional_text(source.get(field))
    if value is None:
        raise CandidateLineageError(
            f"explicit_source_lineage_field_missing:{field}"
        )
    return value


def _source_regime(source: Mapping[str, Any]) -> str:
    regime = _optional_text(source.get("regime"))
    if regime is None:
        regime = _optional_text(source.get("market_regime"))
    if regime is None:
        raise CandidateLineageError(
            "explicit_source_lineage_field_missing:regime"
        )
    return regime


def _source_occurrence_sha256(
    *,
    source_row: Mapping[str, Any],
    signal: Mapping[str, Any],
    producer_id: str,
    regime: str,
) -> str:
    basis = {
        "schema": "paper_candidate_trade_lineage_source_occurrence_v1",
        "producer_id": producer_id,
        "source_candidate_id": _required_source_text(
            source_row,
            "source_candidate_id",
        ),
        "signal_candidate_id": _required_source_text(
            source_row,
            "signal_candidate_id",
        ),
        "signal_instance_id": _required_source_text(
            source_row,
            "signal_instance_id",
        ),
        "signal_timestamp_utc": _required_source_text(
            source_row,
            "signal_timestamp_utc",
        ),
        "pair": str(signal.get("pair") or "").strip(),
        "symbol": str(signal.get("symbol") or "").strip(),
        "side": str(signal.get("side") or "").strip().lower(),
        "regime": regime,
        "model_version": str(signal.get("model_version") or "").strip(),
        "score": _stable_scalar(signal.get("score")),
        "confidence": _stable_scalar(signal.get("confidence")),
    }
    encoded = json.dumps(
        basis,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        if hasattr(value, "item"):
            return value.item()
    except Exception:
        pass
    return str(value)


def _list_of_mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _first_forbidden_source_path(
    value: Any,
    *,
    prefix: str = "",
) -> str | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}" if prefix else key_text
            normalized = key_text.strip().lower()
            if any(
                pattern in normalized
                for pattern in _FORBIDDEN_SOURCE_FIELD_PATTERNS
            ):
                return path
            found = _first_forbidden_source_path(nested, prefix=path)
            if found is not None:
                return found
    elif isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for index, nested in enumerate(value):
            found = _first_forbidden_source_path(
                nested,
                prefix=f"{prefix}[{index}]",
            )
            if found is not None:
                return found
    return None
