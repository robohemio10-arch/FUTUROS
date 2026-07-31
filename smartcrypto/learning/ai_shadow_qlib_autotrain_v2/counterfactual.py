"""Same-candle counterfactual shadow decision harness."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from .calibration import rank_percentile_probabilities
from .contracts import StrategyPolicy, canonical_hash

REQUIRED_ROW_FIELDS = (
    "event_id",
    "candle_time_utc",
    "symbol",
    "side",
    "expected_entry",
    "expected_exit",
    "net_pnl",
    "label",
    "qlib_score",
    "ai_shadow_probability",
)


def build_counterfactual_harness(
    rows: Sequence[Mapping[str, Any]],
    policies: Sequence[StrategyPolicy],
) -> dict[str, Any]:
    normalized_rows, blockers = normalize_rows(rows)
    if blockers:
        return {
            "status": "blocked",
            "blockers": blockers,
            "decision_count": 0,
            "same_candle_group_count": 0,
            "decisions": [],
            "required_log_fields_present": False,
            "sends_orders": False,
        }
    if not policies:
        return {
            "status": "blocked",
            "blockers": ["strategy_policies_missing"],
            "decision_count": 0,
            "same_candle_group_count": 0,
            "decisions": [],
            "required_log_fields_present": False,
            "sends_orders": False,
        }
    qlib_probabilities = rank_percentile_probabilities(
        [float(row["qlib_score"]) for row in normalized_rows]
    )
    decisions: list[dict[str, Any]] = []
    for row, qlib_probability in zip(normalized_rows, qlib_probabilities, strict=True):
        shadow_probability = float(row["ai_shadow_probability"])
        ensemble_probability = round((qlib_probability + shadow_probability) / 2.0, 12)
        score_sources = {
            "qlib_rank_probability": qlib_probability,
            "ai_shadow_probability": shadow_probability,
            "ensemble_probability": ensemble_probability,
        }
        for policy in policies:
            if policy.score_source not in score_sources:
                return {
                    "status": "blocked",
                    "blockers": [f"unknown_score_source:{policy.policy_id}:{policy.score_source}"],
                    "decision_count": 0,
                    "same_candle_group_count": 0,
                    "decisions": [],
                    "required_log_fields_present": False,
                    "sends_orders": False,
                }
            selected_score = score_sources[policy.score_source]
            would_reject = bool(
                policy.ai_shadow_reject_below is not None
                and shadow_probability < policy.ai_shadow_reject_below
            )
            would_enter = bool(selected_score >= policy.enter_threshold and not would_reject)
            counterfactual_pnl = float(row["net_pnl"]) if would_enter else 0.0
            identity = {
                "event_id": row["event_id"],
                "policy_id": policy.policy_id,
                "candle_time_utc": row["candle_time_utc"],
            }
            decisions.append(
                {
                    "decision_id": canonical_hash(identity),
                    "event_id": row["event_id"],
                    "policy_id": policy.policy_id,
                    "candle_time_utc": row["candle_time_utc"],
                    "symbol": row["symbol"],
                    "side": row["side"],
                    "would_enter": would_enter,
                    "would_reject": would_reject,
                    "expected_entry": row["expected_entry"],
                    "expected_exit": row["expected_exit"],
                    "counterfactual_pnl": round(counterfactual_pnl, 12),
                    "selected_score": round(selected_score, 12),
                    "qlib_rank_probability": round(qlib_probability, 12),
                    "ai_shadow_probability": round(shadow_probability, 12),
                    "ensemble_probability": ensemble_probability,
                    "research_only": True,
                    "operational_authority": False,
                    "sends_orders": False,
                }
            )
    expected_decisions = len(normalized_rows) * len(policies)
    event_counts = Counter(str(item["event_id"]) for item in decisions)
    same_candle_valid = all(count == len(policies) for count in event_counts.values())
    required_fields = {
        "would_enter",
        "would_reject",
        "expected_entry",
        "expected_exit",
        "counterfactual_pnl",
    }
    required_present = all(required_fields.issubset(decision) for decision in decisions)
    blockers = []
    if len(decisions) != expected_decisions:
        blockers.append("counterfactual_decision_count_mismatch")
    if not same_candle_valid:
        blockers.append("same_candle_policy_coverage_mismatch")
    if not required_present:
        blockers.append("required_counterfactual_log_fields_missing")
    return {
        "status": "ok" if not blockers else "blocked",
        "blockers": blockers,
        "input_event_count": len(normalized_rows),
        "policy_count": len(policies),
        "decision_count": len(decisions),
        "same_candle_group_count": len(event_counts),
        "same_candle_policy_coverage_valid": same_candle_valid,
        "required_log_fields_present": required_present,
        "decisions": decisions,
        "sends_orders": False,
        "operational_authority": False,
    }


def normalize_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    blockers: list[str] = []
    normalized: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    for index, row in enumerate(rows):
        missing = [field for field in REQUIRED_ROW_FIELDS if field not in row]
        if missing:
            blockers.append(f"row_{index}_missing_fields:{','.join(sorted(missing))}")
            continue
        event_id = str(row["event_id"]).strip()
        if not event_id:
            blockers.append(f"row_{index}_event_id_empty")
            continue
        if event_id in seen_event_ids:
            blockers.append(f"duplicate_event_id:{event_id}")
            continue
        seen_event_ids.add(event_id)
        try:
            timestamp = _utc_iso(row["candle_time_utc"])
            symbol = str(row["symbol"]).strip().upper()
            side = str(row["side"]).strip().lower()
            expected_entry = _positive_float(row["expected_entry"], "expected_entry")
            expected_exit = _positive_float(row["expected_exit"], "expected_exit")
            net_pnl = _finite_float(row["net_pnl"], "net_pnl")
            label = _binary_label(row["label"])
            qlib_score = _finite_float(row["qlib_score"], "qlib_score")
            shadow_probability = _probability(
                row["ai_shadow_probability"],
                "ai_shadow_probability",
            )
        except ValueError as exc:
            blockers.append(f"row_{index}_invalid:{exc}")
            continue
        if not symbol:
            blockers.append(f"row_{index}_symbol_empty")
            continue
        if side not in {"long", "short"}:
            blockers.append(f"row_{index}_side_invalid")
            continue
        normalized.append(
            {
                "event_id": event_id,
                "candle_time_utc": timestamp,
                "symbol": symbol,
                "side": side,
                "expected_entry": expected_entry,
                "expected_exit": expected_exit,
                "net_pnl": net_pnl,
                "label": label,
                "qlib_score": qlib_score,
                "ai_shadow_probability": shadow_probability,
            }
        )
    return normalized, sorted(set(blockers))


def _utc_iso(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("candle_time_utc must be a non-empty string")
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("candle_time_utc must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("candle_time_utc must be timezone-aware")
    return parsed.astimezone(UTC).isoformat()


def _binary_label(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("label must be binary") from exc
    if parsed not in {0, 1}:
        raise ValueError("label must be binary")
    return parsed


def _probability(value: Any, field: str) -> float:
    parsed = _finite_float(value, field)
    if parsed < 0.0 or parsed > 1.0:
        raise ValueError(f"{field} must be between 0 and 1")
    return parsed


def _positive_float(value: Any, field: str) -> float:
    parsed = _finite_float(value, field)
    if parsed <= 0.0:
        raise ValueError(f"{field} must be positive")
    return parsed


def _finite_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    return parsed
