"""Research-only aggregate divergence and temporal alignment for Paper/Master."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from smartcrypto.research.daily_learning_contracts import SAFETY_FLAGS
from smartcrypto.research.daily_paper_master_kpi_pack import (
    build_daily_paper_master_kpi_pack,
    calculate_trade_kpis,
    compare_kpi_summaries,
    validate_daily_paper_master_kpi_pack,
)


DAILY_PAPER_MASTER_DIVERGENCE_ALIGNMENT_SCHEMA_VERSION = (
    "daily_paper_master_divergence_alignment_v1"
)
DEFAULT_ALIGNMENT_WINDOWS_MINUTES = (15, 30, 60)
MAX_MATCHED_PAIRS_SAMPLE = 20

ALIGNMENT_SCOPE: dict[str, bool] = {
    "computes_aggregate_divergence": True,
    "computes_temporal_alignment": True,
    "loads_runtime_trade_rows": False,
    "loads_excel_rows": False,
    "loads_sqlite_rows": False,
    "loads_candle_rows": False,
    "computes_candle_coverage": False,
    "computes_entry_features": False,
    "mines_patterns": False,
    "registers_candidate_rules": False,
    "updates_models": False,
    "updates_risk": False,
    "updates_execution": False,
    "writes_reports": False,
}

READINESS_POLICY: dict[str, bool] = {
    "divergence_alignment_is_not_readiness_evidence": True,
    "divergence_alignment_outputs_do_not_release_live": True,
    "divergence_alignment_outputs_do_not_release_canary": True,
    "manual_go_no_go_required": True,
    "thirty_day_gap_free_soak_required_for_future_canary_review": True,
}

ALLOWED_NEXT_STEPS = [
    "criar candle coverage/entry features em branch futura",
    "criar mistake/winner catalog em branch futura",
    "criar pattern mining research em branch futura",
    "criar candidate shadow rule registry em branch futura",
    "criar OOS validation em branch futura",
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
    "usar alignment para liberar operacao",
    "promover regra candidata",
    "promover modelo",
    "criar regras candidatas nesta branch",
]


def build_daily_paper_master_divergence_alignment_report(
    project_root: str | Path | None = None,
    paper_trades: Sequence[Mapping[str, Any]] | None = None,
    master_trades: Sequence[Mapping[str, Any]] | None = None,
    alignment_windows_minutes: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Build a blocked research-only divergence/alignment payload."""
    root = Path("." if project_root is None else project_root).expanduser().resolve()
    paper_rows = [] if paper_trades is None else list(paper_trades)
    master_rows = [] if master_trades is None else list(master_trades)
    windows = _normalize_windows(alignment_windows_minutes)
    input_mode = (
        "no_runtime_rows_loaded"
        if paper_trades is None and master_trades is None
        else "in_memory_trade_rows"
    )
    kpi_pack = build_daily_paper_master_kpi_pack(
        root,
        paper_trades=paper_rows,
        master_trades=master_rows,
    )
    kpi_validation_errors = validate_daily_paper_master_kpi_pack(kpi_pack)
    payload: dict[str, Any] = {
        "schema_version": DAILY_PAPER_MASTER_DIVERGENCE_ALIGNMENT_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "divergence_alignment_research_only_without_operational_authority",
        "project_root": str(root),
        **SAFETY_FLAGS,
        "input_mode": input_mode,
        "kpi_pack_status": kpi_pack.get("status"),
        "kpi_pack_validation_errors": kpi_validation_errors,
        "aggregate_divergence": calculate_aggregate_divergence(
            paper_rows,
            master_rows,
        ),
        "temporal_alignment": calculate_temporal_alignment(
            paper_rows,
            master_rows,
            windows,
        ),
        "alignment_windows_minutes": list(windows),
        "alignment_scope": dict(ALIGNMENT_SCOPE),
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
    payload["validation_errors"] = (
        validate_daily_paper_master_divergence_alignment_report(payload)
    )
    return payload


def calculate_aggregate_divergence(
    paper_trades: Sequence[Mapping[str, Any]],
    master_trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Calculate aggregate KPI divergence without temporal matching."""
    paper_kpis = calculate_trade_kpis(paper_trades)
    master_kpis = calculate_trade_kpis(master_trades)
    comparison = compare_kpi_summaries(paper_kpis, master_kpis)
    return {
        "paper_kpis": paper_kpis,
        "master_kpis": master_kpis,
        "kpi_comparison": comparison,
        "net_pnl_delta": comparison.get("net_pnl_delta"),
        "win_rate_pct_delta": comparison.get("win_rate_pct_delta"),
        "profit_factor_delta": comparison.get("profit_factor_delta"),
        "expectancy_delta": comparison.get("expectancy_delta"),
        "trade_count_delta": comparison.get("trade_count_delta"),
        "gross_profit_delta": _delta(
            paper_kpis.get("gross_profit"),
            master_kpis.get("gross_profit"),
        ),
        "gross_loss_abs_delta": _delta(
            paper_kpis.get("gross_loss_abs"),
            master_kpis.get("gross_loss_abs"),
        ),
        "avg_duration_minutes_delta": _delta(
            paper_kpis.get("avg_duration_minutes"),
            master_kpis.get("avg_duration_minutes"),
        ),
        "comparison_scope": "aggregate_divergence_only",
    }


def calculate_temporal_alignment(
    paper_trades: Sequence[Mapping[str, Any]],
    master_trades: Sequence[Mapping[str, Any]],
    windows_minutes: Sequence[int] = DEFAULT_ALIGNMENT_WINDOWS_MINUTES,
) -> dict[str, Any]:
    """Calculate temporal matching summaries for each requested window."""
    windows = _normalize_windows(windows_minutes)
    paper = [
        normalize_trade_for_alignment(trade, "paper", index)
        for index, trade in enumerate(paper_trades)
    ]
    master = [
        normalize_trade_for_alignment(trade, "master", index)
        for index, trade in enumerate(master_trades)
    ]
    return {
        "windows": [
            _alignment_for_window(paper, master, window)
            for window in windows
        ],
        "window_count": len(windows),
        "paper_trade_count": len(paper),
        "master_trade_count": len(master),
        "matching_policy": "nearest_master_same_symbol_if_available_no_reuse",
    }


def normalize_trade_for_alignment(
    trade: Mapping[str, Any],
    source: str,
    index: int,
) -> dict[str, Any]:
    """Normalize a trade row for deterministic alignment calculations."""
    open_time = _parse_time(trade.get("open_time", trade.get("entry_time")))
    close_time = _parse_time(trade.get("close_time", trade.get("exit_time")))
    side = _normalize_side(trade.get("side"))
    pnl = _to_float(trade.get("net_pnl"))
    return {
        "source": source,
        "index": index,
        "trade_id": str(trade.get("trade_id") or f"{source}_{index}"),
        "symbol": _normalize_symbol(trade.get("symbol")),
        "side": side,
        "open_time": open_time,
        "open_time_utc": _time_text(open_time),
        "close_time": close_time,
        "close_time_utc": _time_text(close_time),
        "net_pnl": pnl,
        "exit_reason": str(trade.get("exit_reason") or "").lower(),
        "valid_for_alignment": open_time is not None,
    }


def validate_daily_paper_master_divergence_alignment_report(
    payload: Mapping[str, Any],
) -> list[str]:
    """Validate the divergence/alignment report contract."""
    errors: list[str] = []
    expected_header: dict[str, Any] = {
        "schema_version": DAILY_PAPER_MASTER_DIVERGENCE_ALIGNMENT_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "divergence_alignment_research_only_without_operational_authority",
    }
    for key, expected in expected_header.items():
        if payload.get(key) != expected:
            errors.append(f"{key}_mismatch")
    for key, expected in SAFETY_FLAGS.items():
        if payload.get(key) is not expected:
            errors.append(f"{key}_must_be_{str(expected).lower()}")
    scope = _mapping(payload.get("alignment_scope"))
    for key, expected in ALIGNMENT_SCOPE.items():
        if scope.get(key) is not expected:
            errors.append(f"alignment_scope_{key}_mismatch")
    readiness = _mapping(payload.get("readiness_policy"))
    for key, expected in READINESS_POLICY.items():
        if readiness.get(key) is not expected:
            errors.append(f"readiness_policy_{key}_mismatch")
    for section in ("aggregate_divergence", "temporal_alignment"):
        if not isinstance(payload.get(section), Mapping):
            errors.append(f"{section}_must_be_object")
    if not isinstance(payload.get("alignment_windows_minutes"), list):
        errors.append("alignment_windows_minutes_must_be_list")
    return errors


def _alignment_for_window(
    paper: Sequence[Mapping[str, Any]],
    master: Sequence[Mapping[str, Any]],
    window_minutes: int,
) -> dict[str, Any]:
    window_seconds = window_minutes * 60
    used_master: set[int] = set()
    matches: list[tuple[Mapping[str, Any], Mapping[str, Any], float]] = []
    unmatched_paper: list[Mapping[str, Any]] = []
    for paper_trade in paper:
        match = _best_master_match(paper_trade, master, used_master, window_seconds)
        if match is None:
            unmatched_paper.append(paper_trade)
            continue
        master_index, delta_seconds = match
        used_master.add(master_index)
        matches.append((paper_trade, master[master_index], delta_seconds))
    unmatched_master = [
        trade for index, trade in enumerate(master) if index not in used_master
    ]
    same_side = [pair for pair in matches if _same_side(pair[0], pair[1])]
    opposite_side = [pair for pair in matches if _opposite_side(pair[0], pair[1])]
    paper_stop_after_master_win = [
        pair for pair in matches if _paper_stop_after_master_win(pair[0], pair[1])
    ]
    paper_entry_after_master_exit = [
        pair for pair in matches if _paper_entry_after_master_exit(pair[0], pair[1])
    ]
    master_winner_missed = [
        trade for trade in unmatched_master if _positive_pnl(trade)
    ]
    paper_loser_without_master = [
        trade for trade in unmatched_paper if _negative_pnl(trade)
    ]
    return {
        "window_minutes": window_minutes,
        "paper_trade_count": len(paper),
        "master_trade_count": len(master),
        "matched_count": len(matches),
        "unmatched_paper_count": len(unmatched_paper),
        "unmatched_master_count": len(unmatched_master),
        "same_side_match_count": len(same_side),
        "opposite_side_match_count": len(opposite_side),
        "paper_stop_after_master_win_count": len(paper_stop_after_master_win),
        "paper_entry_after_master_exit_count": len(paper_entry_after_master_exit),
        "master_winner_missed_count": len(master_winner_missed),
        "paper_loser_without_master_match_count": len(paper_loser_without_master),
        "match_rate_pct": _rate(len(matches), len(paper)),
        "opposite_side_rate_pct": _rate(len(opposite_side), len(matches)),
        "matched_pairs_sample": [
            _pair_sample(paper_trade, master_trade, delta_seconds)
            for paper_trade, master_trade, delta_seconds in matches[
                :MAX_MATCHED_PAIRS_SAMPLE
            ]
        ],
    }


def _best_master_match(
    paper_trade: Mapping[str, Any],
    master: Sequence[Mapping[str, Any]],
    used_master: set[int],
    window_seconds: int,
) -> tuple[int, float] | None:
    paper_time = paper_trade.get("open_time")
    if not isinstance(paper_time, dt.datetime):
        return None
    candidates: list[tuple[float, int]] = []
    for index, master_trade in enumerate(master):
        if index in used_master:
            continue
        master_time = master_trade.get("open_time")
        if not isinstance(master_time, dt.datetime):
            continue
        if not _symbols_compatible(paper_trade, master_trade):
            continue
        delta_seconds = abs((paper_time - master_time).total_seconds())
        if delta_seconds <= window_seconds:
            candidates.append((delta_seconds, index))
    if not candidates:
        return None
    delta_seconds, index = min(candidates, key=lambda item: (item[0], item[1]))
    return index, delta_seconds


def _pair_sample(
    paper_trade: Mapping[str, Any],
    master_trade: Mapping[str, Any],
    delta_seconds: float,
) -> dict[str, Any]:
    return {
        "paper_trade_id": paper_trade.get("trade_id"),
        "master_trade_id": master_trade.get("trade_id"),
        "symbol": paper_trade.get("symbol") or master_trade.get("symbol"),
        "paper_side": paper_trade.get("side"),
        "master_side": master_trade.get("side"),
        "delta_minutes": delta_seconds / 60.0,
        "same_side": _same_side(paper_trade, master_trade),
        "opposite_side": _opposite_side(paper_trade, master_trade),
        "paper_stop_after_master_win": _paper_stop_after_master_win(
            paper_trade,
            master_trade,
        ),
        "paper_entry_after_master_exit": _paper_entry_after_master_exit(
            paper_trade,
            master_trade,
        ),
    }


def _symbols_compatible(
    paper_trade: Mapping[str, Any],
    master_trade: Mapping[str, Any],
) -> bool:
    paper_symbol = paper_trade.get("symbol")
    master_symbol = master_trade.get("symbol")
    return not paper_symbol or not master_symbol or paper_symbol == master_symbol


def _same_side(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_side = left.get("side")
    right_side = right.get("side")
    return bool(left_side and right_side and left_side == right_side)


def _opposite_side(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    pair = {left.get("side"), right.get("side")}
    return pair == {"long", "short"}


def _paper_stop_after_master_win(
    paper_trade: Mapping[str, Any],
    master_trade: Mapping[str, Any],
) -> bool:
    reason = str(paper_trade.get("exit_reason") or "")
    return "stop" in reason and _negative_pnl(paper_trade) and _positive_pnl(master_trade)


def _paper_entry_after_master_exit(
    paper_trade: Mapping[str, Any],
    master_trade: Mapping[str, Any],
) -> bool:
    paper_open = paper_trade.get("open_time")
    master_close = master_trade.get("close_time")
    return (
        isinstance(paper_open, dt.datetime)
        and isinstance(master_close, dt.datetime)
        and paper_open > master_close
    )


def _positive_pnl(trade: Mapping[str, Any]) -> bool:
    value = _to_float(trade.get("net_pnl"))
    return value is not None and value > 0


def _negative_pnl(trade: Mapping[str, Any]) -> bool:
    value = _to_float(trade.get("net_pnl"))
    return value is not None and value < 0


def _normalize_windows(windows: Sequence[int] | None) -> tuple[int, ...]:
    raw = DEFAULT_ALIGNMENT_WINDOWS_MINUTES if windows is None else windows
    normalized = sorted({int(window) for window in raw if int(window) > 0})
    return tuple(normalized) or DEFAULT_ALIGNMENT_WINDOWS_MINUTES


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


def _parse_time(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = dt.datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _time_text(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _rate(count: int, total: int) -> float | None:
    if total <= 0:
        return None
    return (count / total) * 100.0


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _delta(paper_value: Any, master_value: Any) -> float | int | None:
    paper = _to_float(paper_value)
    master = _to_float(master_value)
    if paper is None or master is None:
        return None
    delta = paper - master
    return int(delta) if delta.is_integer() else delta


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
