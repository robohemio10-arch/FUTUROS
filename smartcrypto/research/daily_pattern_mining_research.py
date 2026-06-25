"""Research-only descriptive pattern mining for daily paper learning."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from itertools import combinations
from pathlib import Path
from typing import Any

from smartcrypto.research.daily_learning_contracts import SAFETY_FLAGS
from smartcrypto.research.daily_mistake_winner_catalog import (
    build_mistake_winner_catalog,
    summarize_catalog,
)


DAILY_PATTERN_MINING_RESEARCH_SCHEMA_VERSION = "daily_pattern_mining_research_v1"
DEFAULT_MIN_SUPPORT_COUNT = 2
DEFAULT_MIN_CONFIDENCE = 0.5
MAX_PATTERN_SAMPLE = 20
MAX_EXAMPLE_SAMPLE = 5

TARGETS = (
    "mistake",
    "winner",
    "stop_loss_loss",
    "fast_loss_under_30m",
    "profitable_trade",
)

PATTERN_SCOPE: dict[str, bool] = {
    "mines_patterns": True,
    "descriptive_research_only": True,
    "uses_only_in_memory_inputs": True,
    "uses_net_pnl_as_feature": False,
    "uses_net_pnl_as_label": True,
    "creates_candidate_rules": False,
    "registers_candidate_rules": False,
    "runs_oos_validation": False,
    "updates_models": False,
    "updates_risk": False,
    "updates_execution": False,
    "writes_reports": False,
}

READINESS_POLICY: dict[str, bool] = {
    "pattern_mining_is_not_readiness_evidence": True,
    "pattern_mining_outputs_do_not_release_live": True,
    "pattern_mining_outputs_do_not_release_canary": True,
    "manual_go_no_go_required": True,
    "candidate_rules_require_separate_registry_branch": True,
    "candidate_rules_require_oos_validation_branch": True,
    "thirty_day_gap_free_soak_required_for_future_canary_review": True,
}

ALLOWED_NEXT_STEPS = [
    "criar candidate shadow rule registry em branch futura",
    "criar OOS validation em branch futura",
    "criar AI Shadow feedback bridge em branch futura",
    "criar Qlib research dataset em branch futura",
    "criar daily learning orchestrator em branch futura",
]

FORBIDDEN_ACTIONS = [
    "alterar Freqtrade",
    "alterar RiskManager",
    "alterar Qlib runtime",
    "alterar IA Shadow runtime",
    "alterar modelos",
    "alterar datasets",
    "habilitar live",
    "habilitar canary",
    "enviar ordem real",
    "usar exchange privada",
    "escrever artefatos em data/runtime/reports/logs/freqtrade",
    "usar pattern mining para liberar operacao",
    "promover regra candidata",
    "promover modelo",
    "registrar candidate rules nesta branch",
    "rodar OOS validation nesta branch",
    "gerar codigo operacional de veto",
]


def build_daily_pattern_mining_research_report(
    project_root: str | Path | None = None,
    catalog_entries: Sequence[Mapping[str, Any]] | None = None,
    feature_rows: Sequence[Mapping[str, Any]] | None = None,
    trades: Sequence[Mapping[str, Any]] | None = None,
    min_support_count: int = DEFAULT_MIN_SUPPORT_COUNT,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> dict[str, Any]:
    """Build the blocked research report without reading runtime sources."""
    root = Path("." if project_root is None else project_root).expanduser().resolve()
    if catalog_entries is None and trades is None and feature_rows is None:
        entries: list[Mapping[str, Any]] = []
        input_mode = "no_runtime_rows_loaded"
    elif catalog_entries is not None:
        entries = list(catalog_entries)
        input_mode = "in_memory_pattern_inputs"
    else:
        trade_rows = [] if trades is None else list(trades)
        entries = build_mistake_winner_catalog(
            trade_rows,
            feature_rows,
        )["catalog_entries"]
        input_mode = "in_memory_pattern_inputs"

    mining = mine_descriptive_patterns(
        entries,
        feature_rows,
        min_support_count=min_support_count,
        min_confidence=min_confidence,
    )
    payload: dict[str, Any] = {
        "schema_version": DAILY_PATTERN_MINING_RESEARCH_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "pattern_mining_research_only_without_operational_authority",
        "project_root": str(root),
        **SAFETY_FLAGS,
        "input_mode": input_mode,
        "min_support_count": max(1, int(min_support_count)),
        "min_confidence": _bounded_confidence(min_confidence),
        "pattern_mining": mining,
        "pattern_summary": summarize_catalog(entries),
        "pattern_scope": dict(PATTERN_SCOPE),
        "readiness_policy": dict(READINESS_POLICY),
        "allowed_next_steps": list(ALLOWED_NEXT_STEPS),
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "operator_decision": {
            "live_release_allowed": False,
            "canary_release_allowed": False,
            "freqtrade_strategy_change_allowed": False,
            "risk_manager_change_allowed": False,
            "model_promotion_allowed": False,
            "shadow_rule_promotion_allowed": False,
            "final_decision": "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH",
        },
        "write_requested": False,
        "write_performed": False,
    }
    payload["validation_errors"] = validate_daily_pattern_mining_research_report(
        payload,
    )
    return payload


def mine_descriptive_patterns(
    catalog_entries: Sequence[Mapping[str, Any]],
    feature_rows: Sequence[Mapping[str, Any]] | None = None,
    min_support_count: int = DEFAULT_MIN_SUPPORT_COUNT,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> dict[str, Any]:
    """Mine deterministic descriptive patterns from in-memory rows."""
    entries = list(catalog_entries)
    features_by_trade_id = _features_by_trade_id(feature_rows)
    min_support = max(1, int(min_support_count))
    confidence_floor = _bounded_confidence(min_confidence)
    row_contexts = [
        _build_row_context(entry, features_by_trade_id.get(str(entry.get("trade_id"))))
        for entry in entries
    ]
    target_counts = {
        target: sum(1 for row in row_contexts if target in row["targets"])
        for target in TARGETS
    }
    total_count = len(row_contexts)
    candidates: dict[tuple[str, str, tuple[str, ...]], dict[str, Any]] = {}

    for row in row_contexts:
        buckets = sorted(row["buckets"])
        for bucket in buckets:
            _add_candidate(candidates, "single_bucket", (bucket,), row)
        for pair in combinations(buckets, 2):
            _add_candidate(candidates, "bucket_pair", pair, row)

    patterns: list[dict[str, Any]] = []
    for candidate in candidates.values():
        target = str(candidate["target"])
        scored = score_pattern(
            candidate,
            total_count=total_count,
            target_count=target_counts.get(target, 0),
        )
        if scored["support_count"] < min_support:
            continue
        if scored["confidence"] < confidence_floor:
            continue
        patterns.append(scored)

    patterns.sort(
        key=lambda item: (
            -float(item["lift"]),
            -float(item["confidence"]),
            -int(item["support_count"]),
            str(item["pattern_type"]),
            str(item["conditions"]),
            str(item["target"]),
        ),
    )
    for index, pattern in enumerate(patterns, start=1):
        pattern["pattern_id"] = f"daily_pattern_{index:04d}"

    return {
        "entry_count": total_count,
        "feature_row_count": len(feature_rows or ()),
        "pattern_count": len(patterns),
        "patterns": patterns,
        "patterns_sample": patterns[:MAX_PATTERN_SAMPLE],
        "target_counts": target_counts,
        "classification_counts": _counter(entries, "classification"),
        "pattern_quality_notes": _pattern_quality_notes(
            total_count,
            len(patterns),
            min_support,
            confidence_floor,
        ),
    }


def build_feature_bins(feature_row: Mapping[str, Any]) -> dict[str, str]:
    """Build stable descriptive feature bins from a single in-memory row."""
    row = _mapping(feature_row)
    bins: dict[str, str] = {}
    rsi = _to_float(row.get("rsi_14"))
    if rsi is not None:
        if rsi < 30:
            bins["rsi_14"] = "rsi_low"
        elif rsi < 70:
            bins["rsi_14"] = "rsi_mid"
        elif rsi < 80:
            bins["rsi_14"] = "rsi_high"
        else:
            bins["rsi_14"] = "rsi_extreme"
    dist = _to_float(row.get("dist_sma_20_pct"))
    if dist is not None:
        if dist < -0.2:
            bins["dist_sma_20_pct"] = "below_sma"
        elif dist > 0.2:
            bins["dist_sma_20_pct"] = "above_sma"
        else:
            bins["dist_sma_20_pct"] = "near_sma"
    lb_10 = _to_float(row.get("lb_10m_ret_close"))
    if lb_10 is not None:
        bins["lb_10m_ret_close"] = _return_bucket(lb_10, "lb_10m")
    lb_30 = _to_float(row.get("lb_30m_ret_close"))
    if lb_30 is not None:
        bins["lb_30m_ret_close"] = _return_bucket(lb_30, "lb_30m")
    volatility = _to_float(row.get("pre_entry_volatility_20"))
    if volatility is not None:
        if volatility < 0.005:
            bins["pre_entry_volatility_20"] = "vol_low"
        elif volatility <= 0.02:
            bins["pre_entry_volatility_20"] = "vol_mid"
        else:
            bins["pre_entry_volatility_20"] = "vol_high"
    side = _normalize_side(row.get("side"))
    bins["side"] = f"side_{side or 'unknown'}"
    symbol = _normalize_symbol(row.get("symbol"))
    if symbol is not None:
        bins["symbol"] = f"symbol_{symbol}"
    return bins


def score_pattern(
    candidate: Mapping[str, Any],
    total_count: int,
    target_count: int,
) -> dict[str, Any]:
    """Score one descriptive candidate pattern."""
    support_count = int(candidate.get("support_count") or 0)
    candidate_target_count = int(candidate.get("target_count") or 0)
    non_target_count = max(0, support_count - candidate_target_count)
    confidence = (
        candidate_target_count / support_count
        if support_count > 0
        else 0.0
    )
    baseline_rate = target_count / total_count if total_count > 0 else 0.0
    lift = confidence / baseline_rate if baseline_rate > 0 else 0.0
    coverage_pct = (support_count / total_count * 100.0) if total_count > 0 else 0.0
    conditions = list(candidate.get("conditions") or [])
    target = str(candidate.get("target") or "unknown")
    return {
        "pattern_id": str(candidate.get("pattern_id") or ""),
        "pattern_type": str(candidate.get("pattern_type") or "single_bucket"),
        "target": target,
        "conditions": conditions,
        "support_count": support_count,
        "target_count": candidate_target_count,
        "non_target_count": non_target_count,
        "confidence": round(confidence, 6),
        "baseline_rate": round(baseline_rate, 6),
        "lift": round(lift, 6),
        "coverage_pct": round(coverage_pct, 6),
        "examples_sample": list(candidate.get("examples_sample") or [])[
            :MAX_EXAMPLE_SAMPLE
        ],
        "research_interpretation": _interpret_pattern(
            conditions,
            target,
            confidence,
            lift,
        ),
        "creates_candidate_rule": False,
        "operational_action_allowed": False,
        "requires_oos_validation": True,
        "promotion_allowed": False,
    }


def validate_daily_pattern_mining_research_report(
    payload: Mapping[str, Any],
) -> list[str]:
    """Validate the pattern mining report contract."""
    errors: list[str] = []
    expected_header: dict[str, Any] = {
        "schema_version": DAILY_PATTERN_MINING_RESEARCH_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "pattern_mining_research_only_without_operational_authority",
    }
    for key, expected in expected_header.items():
        if payload.get(key) != expected:
            errors.append(f"{key}_mismatch")
    for key, expected in SAFETY_FLAGS.items():
        if payload.get(key) is not expected:
            errors.append(f"{key}_must_be_{str(expected).lower()}")
    scope = _mapping(payload.get("pattern_scope"))
    for key, expected in PATTERN_SCOPE.items():
        if scope.get(key) is not expected:
            errors.append(f"pattern_scope_{key}_mismatch")
    readiness = _mapping(payload.get("readiness_policy"))
    for key, expected in READINESS_POLICY.items():
        if readiness.get(key) is not expected:
            errors.append(f"readiness_policy_{key}_mismatch")
    mining = _mapping(payload.get("pattern_mining"))
    if not mining:
        errors.append("pattern_mining_must_be_object")
        return errors
    patterns = mining.get("patterns")
    if not isinstance(patterns, Sequence) or isinstance(patterns, str):
        errors.append("patterns_must_be_list")
        return errors
    for index, pattern in enumerate(patterns):
        item = _mapping(pattern)
        if item.get("creates_candidate_rule") is not False:
            errors.append(f"pattern_{index}_creates_candidate_rule_must_be_false")
        if item.get("operational_action_allowed") is not False:
            errors.append(f"pattern_{index}_operational_action_allowed_must_be_false")
        if item.get("requires_oos_validation") is not True:
            errors.append(f"pattern_{index}_requires_oos_validation_must_be_true")
        if item.get("promotion_allowed") is not False:
            errors.append(f"pattern_{index}_promotion_allowed_must_be_false")
    return errors


def _build_row_context(
    catalog_entry: Mapping[str, Any],
    feature_row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(_mapping(feature_row))
    merged.setdefault("symbol", catalog_entry.get("symbol"))
    merged.setdefault("side", catalog_entry.get("side"))
    bins = set(build_feature_bins(merged).values())
    subclassification = str(catalog_entry.get("subclassification") or "").strip()
    if subclassification:
        bins.add(f"sub_{subclassification}")
    severity = str(catalog_entry.get("severity") or "").strip()
    if severity:
        bins.add(f"severity_{severity}")
    targets = _targets_for_entry(catalog_entry)
    return {
        "trade_id": str(catalog_entry.get("trade_id") or "unknown_trade"),
        "buckets": bins,
        "targets": targets,
    }


def _targets_for_entry(catalog_entry: Mapping[str, Any]) -> set[str]:
    targets: set[str] = set()
    classification = str(catalog_entry.get("classification") or "")
    subclassification = str(catalog_entry.get("subclassification") or "")
    evidence = {
        str(item)
        for item in catalog_entry.get("evidence", [])
        if item is not None
    }
    if classification in {"mistake", "winner"}:
        targets.add(classification)
    if subclassification in {"stop_loss_loss", "profitable_trade"}:
        targets.add(subclassification)
    if "fast_loss_under_30m" in evidence:
        targets.add("fast_loss_under_30m")
    return targets


def _add_candidate(
    candidates: dict[tuple[str, str, tuple[str, ...]], dict[str, Any]],
    pattern_type: str,
    conditions: tuple[str, ...],
    row: Mapping[str, Any],
) -> None:
    row_targets = row.get("targets")
    if not isinstance(row_targets, set):
        row_targets = set()
    actual_type = pattern_type
    if len(conditions) == 1 and conditions[0].startswith(("sub_", "severity_")):
        actual_type = "classification_concentration"
    for target in TARGETS:
        key = (actual_type, target, tuple(conditions))
        item = candidates.setdefault(
            key,
            {
                "pattern_type": actual_type,
                "target": target,
                "conditions": list(conditions),
                "support_count": 0,
                "target_count": 0,
                "examples_sample": [],
            },
        )
        item["support_count"] = int(item["support_count"]) + 1
        if target in row_targets:
            item["target_count"] = int(item["target_count"]) + 1
            examples = item["examples_sample"]
            if isinstance(examples, list) and len(examples) < MAX_EXAMPLE_SAMPLE:
                examples.append(row.get("trade_id"))


def _features_by_trade_id(
    feature_rows: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in feature_rows or ():
        trade_id = row.get("trade_id")
        if trade_id is not None:
            result[str(trade_id)] = row
    return result


def _counter(entries: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for entry in entries:
        value = entry.get(key)
        if value is None or value == "":
            continue
        counter[str(value)] += 1
    return dict(sorted(counter.items()))


def _pattern_quality_notes(
    total_count: int,
    pattern_count: int,
    min_support: int,
    min_confidence: float,
) -> list[str]:
    notes = [
        "descriptive_research_only",
        "patterns_are_not_candidate_rules",
        "requires_separate_registry_and_oos_validation",
    ]
    if total_count == 0:
        notes.append("no_entries_available")
    if pattern_count == 0:
        notes.append("no_patterns_passed_filters")
    notes.append(f"min_support_count={min_support}")
    notes.append(f"min_confidence={min_confidence:.6f}")
    return notes


def _interpret_pattern(
    conditions: Sequence[str],
    target: str,
    confidence: float,
    lift: float,
) -> str:
    joined = " + ".join(conditions)
    return (
        f"Em memoria, {joined} concentrou {target} com "
        f"confidence={confidence:.6f} e lift={lift:.6f}. "
        "Isto e evidencia descritiva, nao regra operacional."
    )


def _return_bucket(value: float, prefix: str) -> str:
    if value < -0.001:
        return f"{prefix}_negative"
    if value > 0.001:
        return f"{prefix}_positive"
    return f"{prefix}_neutral"


def _normalize_side(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"long", "buy"}:
        return "long"
    if text in {"short", "sell"}:
        return "short"
    return None


def _normalize_symbol(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper().replace("/", "")
    return text or None


def _bounded_confidence(value: float) -> float:
    number = _to_float(value)
    if number is None:
        return DEFAULT_MIN_CONFIDENCE
    return min(1.0, max(0.0, number))


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
