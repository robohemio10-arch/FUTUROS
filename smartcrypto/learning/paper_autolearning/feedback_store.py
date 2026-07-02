"""Closed paper trade normalization and incremental outcome event store."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from .outcome_schema import (
    DEFAULT_CLOSED_TRADES_CSV,
    DEFAULT_FEEDBACK_STORE,
    DEFAULT_OUTCOME_EVENTS,
    DEFAULT_SOURCE_CONTRACT,
    FUTURES_COVERAGE_FIELDS,
    OUTCOME_EVENT_COLUMNS,
    coverage_ratio,
    utc_now_iso,
)

FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "order_id": ("order_id", "orderId", "exchange_order_id"),
    "internal_order_id": ("internal_order_id", "client_order_id", "clientOrderId"),
    "trade_id": ("trade_id", "ft_trade_id", "id"),
    "symbol": ("symbol", "pair", "moeda", "asset"),
    "side": ("side", "fechar_side", "trade_side", "position_side", "direction"),
    "open_time_utc": ("open_time_utc", "open_time", "horario_abertura", "opened_at", "date_open"),
    "close_time_utc": ("close_time_utc", "close_time", "horario_fechamento", "closed_at", "date_close"),
    "entry_price": ("entry_price", "open_rate", "preco_abertura", "open_price"),
    "exit_price": ("exit_price", "close_rate", "preco_fechamento", "close_price"),
    "quantity": ("quantity", "amount", "qty", "contracts"),
    "notional": ("notional", "stake_amount", "stake", "cost"),
    "gross_pnl": ("gross_pnl", "profit_abs", "pnl", "pnl_fechado", "reported_pnl_usdt"),
    "trading_fee": ("trading_fee", "fee", "fees", "total_fee"),
    "funding_fee": ("funding_fee", "funding", "funding_cost"),
    "net_pnl": ("net_pnl", "pnl_fechado", "profit_abs", "pnl", "reported_pnl_usdt", "realized_pnl"),
    "profit_ratio": (
        "profit_ratio",
        "close_profit",
        "return_pct",
        "taxa_lucros_perdas_fechados_pct",
        "normalized_return_pct",
    ),
    "margin_mode": ("margin_mode", "marginMode"),
    "leverage": ("leverage", "leverage_used"),
    "liquidation_price": ("liquidation_price", "liq_price"),
    "exit_reason": ("exit_reason", "sell_reason", "close_reason"),
    "strategy_id": ("strategy_id", "strategy"),
    "paper_candidate_filter_called": ("paper_candidate_filter_called",),
    "paper_candidate_filter_decision": ("paper_candidate_filter_decision",),
    "qlib_prediction_id": ("qlib_prediction_id",),
    "ai_shadow_decision_id": ("ai_shadow_decision_id",),
}


@dataclass(frozen=True)
class FeedbackBuildResult:
    closed_rows: list[dict[str, Any]]
    valid_events: list[dict[str, Any]]
    rejected_rows: list[dict[str, Any]]
    new_events: list[dict[str, Any]]
    duplicate_events: list[dict[str, Any]]
    source_path: Path | None
    source_sha256: str | None
    input_mode: str
    source_reason: str
    futures_fields_coverage: dict[str, float]


def build_feedback_events(
    *,
    project_root: str | Path,
    source_path: str | Path | None = None,
    existing_outcome_path: str | Path | None = None,
    closed_trade_rows: Sequence[Mapping[str, Any]] | None = None,
) -> FeedbackBuildResult:
    root = Path(project_root).resolve()
    source, rows, input_mode, source_reason = load_closed_trade_rows(
        project_root=root,
        source_path=source_path,
        closed_trade_rows=closed_trade_rows,
    )
    source_sha = _sha256_file(source) if source is not None else None
    ingestion_run_id = _sha256_text(f"{source or '<in_memory>'}|{source_sha}|{len(rows)}")[:16]
    created_at = utc_now_iso()
    valid_events: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        event = normalize_closed_trade_row(
            row,
            source_file=str(source) if source is not None else "<in_memory_closed_trades>",
            source_sha256=source_sha,
            ingestion_run_id=ingestion_run_id,
            source_row_index=index,
            created_at_utc=created_at,
        )
        if event["validation_status"] == "ok":
            valid_events.append(event)
        else:
            rejected_rows.append(
                {
                    "source_row_index": index,
                    "validation_errors": list(event["validation_errors"]),
                    "row_fingerprint": event["row_fingerprint"],
                }
            )
    existing_path = _resolve(root, existing_outcome_path, DEFAULT_OUTCOME_EVENTS)
    existing_events = read_existing_outcome_events(existing_path)
    new_events, duplicate_events = split_new_and_duplicate_events(valid_events, existing_events)
    return FeedbackBuildResult(
        closed_rows=[dict(row) for row in rows],
        valid_events=valid_events,
        rejected_rows=rejected_rows,
        new_events=new_events,
        duplicate_events=duplicate_events,
        source_path=source,
        source_sha256=source_sha,
        input_mode=input_mode,
        source_reason=source_reason,
        futures_fields_coverage={field: coverage_ratio(valid_events, field) for field in FUTURES_COVERAGE_FIELDS},
    )


def load_closed_trade_rows(
    *,
    project_root: Path,
    source_path: str | Path | None = None,
    closed_trade_rows: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[Path | None, list[dict[str, Any]], str, str]:
    if closed_trade_rows is not None:
        return None, [dict(row) for row in closed_trade_rows], "in_memory_closed_trade_rows", "in_memory_rows_supplied"
    candidates = [
        _resolve(project_root, source_path, DEFAULT_CLOSED_TRADES_CSV),
        project_root / DEFAULT_FEEDBACK_STORE,
        project_root / DEFAULT_SOURCE_CONTRACT,
    ]
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if not path.exists() or not path.is_file():
            continue
        rows = read_rows(path)
        if rows:
            return path, rows, "runtime_read_requested", "source_loaded_read_only"
    return candidates[0], [], "runtime_read_requested", "no_closed_trade_source_found"


def read_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return [dict(row) for row in pd.read_csv(path).to_dict(orient="records")]
    if suffix == ".parquet":
        return [dict(row) for row in pd.read_parquet(path).to_dict(orient="records")]
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, Mapping):
            for key in ("normalized_rows", "normalized_rows_sample", "closed_trades", "rows", "data"):
                rows = payload.get(key)
                if isinstance(rows, list):
                    return [dict(item) for item in rows if isinstance(item, Mapping)]
        if isinstance(payload, list):
            return [dict(item) for item in payload if isinstance(item, Mapping)]
    return []


def normalize_closed_trade_row(
    row: Mapping[str, Any],
    *,
    source_file: str,
    source_sha256: str | None,
    ingestion_run_id: str,
    source_row_index: int,
    created_at_utc: str,
) -> dict[str, Any]:
    mapped = {field: _first_value(row, candidates) for field, candidates in FIELD_CANDIDATES.items()}
    symbol_norm = normalize_symbol(mapped["symbol"])
    side = normalize_side(mapped["side"])
    open_time = normalize_time(mapped["open_time_utc"])
    close_time = normalize_time(mapped["close_time_utc"])
    entry_price = safe_float(mapped["entry_price"])
    exit_price = safe_float(mapped["exit_price"])
    quantity = safe_float(mapped["quantity"])
    notional = safe_float(mapped["notional"])
    if notional is None and entry_price is not None and quantity is not None:
        notional = abs(entry_price * quantity)
    gross_pnl = safe_float(mapped["gross_pnl"])
    net_pnl = safe_float(mapped["net_pnl"])
    trading_fee = safe_float(mapped["trading_fee"])
    funding_fee = safe_float(mapped["funding_fee"])
    if net_pnl is None and gross_pnl is not None:
        net_pnl = gross_pnl - (trading_fee or 0.0) - (funding_fee or 0.0)
    profit_ratio = safe_float(mapped["profit_ratio"])
    leverage = safe_float(mapped["leverage"])
    margin_mode = clean_text(mapped["margin_mode"])
    liquidation_price = safe_float(mapped["liquidation_price"])
    duration_seconds = _duration_seconds(open_time, close_time)
    row_fingerprint = row_fingerprint_for(row)
    order_id = normalize_identity(mapped["order_id"])
    internal_order_id = normalize_identity(mapped["internal_order_id"])
    trade_id = normalize_identity(mapped["trade_id"])
    validation_errors = validate_event_inputs(
        symbol_norm=symbol_norm,
        side=side,
        close_time_utc=close_time,
        net_pnl=net_pnl,
    )
    is_closed = close_time is not None
    label_sign = 1 if (net_pnl or 0.0) > 0 else -1 if (net_pnl or 0.0) < 0 else 0
    event = {
        "event_id": f"outcome_{dedup_key(order_id, internal_order_id, trade_id, row_fingerprint).replace('::', '_')}",
        "source": "paper_closed_trade",
        "source_file": source_file,
        "source_sha256": source_sha256,
        "ingestion_run_id": ingestion_run_id,
        "order_id": order_id,
        "internal_order_id": internal_order_id,
        "trade_id": trade_id,
        "row_fingerprint": row_fingerprint,
        "symbol": clean_text(mapped["symbol"]),
        "symbol_norm": symbol_norm,
        "market_type": "futures_perpetual",
        "side": side,
        "position_side": side,
        "margin_mode": margin_mode,
        "leverage": leverage,
        "open_time_utc": open_time,
        "close_time_utc": close_time,
        "duration_seconds": duration_seconds,
        "is_closed": is_closed,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "quantity": quantity,
        "notional": notional,
        "gross_pnl": gross_pnl if gross_pnl is not None else net_pnl,
        "trading_fee": trading_fee,
        "funding_fee": funding_fee,
        "net_pnl": net_pnl,
        "profit_ratio": profit_ratio,
        "pnl_on_margin_pct": _pnl_on_margin_pct(net_pnl, notional, leverage),
        "pnl_on_notional_pct": _ratio_pct(net_pnl, notional),
        "liquidation_price": liquidation_price,
        "distance_to_liquidation_pct": _distance_to_liquidation(entry_price, liquidation_price, side),
        "exit_reason": clean_text(mapped["exit_reason"]),
        "roi_hit": _contains(mapped["exit_reason"], "roi"),
        "stoploss_hit": _contains(mapped["exit_reason"], "stop"),
        "forced_exit": _contains(mapped["exit_reason"], "force"),
        "liquidation_flag": _contains(mapped["exit_reason"], "liquid"),
        "label_win_loss": "win" if label_sign > 0 else "loss" if label_sign < 0 else "breakeven",
        "label_sign": label_sign,
        "label_net_pnl_bucket": net_pnl_bucket(net_pnl),
        "label_holding_time_bucket": holding_time_bucket(duration_seconds),
        "label_quality_bucket": "valid" if not validation_errors else "rejected",
        "paper_candidate_filter_called": bool(mapped["paper_candidate_filter_called"] is True),
        "paper_candidate_filter_decision": clean_text(mapped["paper_candidate_filter_decision"]),
        "qlib_prediction_id": clean_text(mapped["qlib_prediction_id"]),
        "ai_shadow_decision_id": clean_text(mapped["ai_shadow_decision_id"]),
        "strategy_id": clean_text(mapped["strategy_id"]),
        "validation_status": "ok" if not validation_errors else "rejected",
        "validation_errors": validation_errors,
        "created_at_utc": created_at_utc,
        "_dedup_key": dedup_key(order_id, internal_order_id, trade_id, row_fingerprint),
        "_source_row_index": source_row_index,
    }
    for column in OUTCOME_EVENT_COLUMNS:
        event.setdefault(column, None)
    return event


def validate_event_inputs(
    *,
    symbol_norm: str,
    side: str,
    close_time_utc: str | None,
    net_pnl: float | None,
) -> list[str]:
    errors: list[str] = []
    if not symbol_norm:
        errors.append("missing_symbol")
    if side not in {"long", "short"}:
        errors.append("missing_side")
    if close_time_utc is None:
        errors.append("missing_close_time")
    if net_pnl is None:
        errors.append("missing_net_pnl")
    return errors


def split_new_and_duplicate_events(
    events: Sequence[Mapping[str, Any]],
    existing_events: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    existing_keys = {event_dedup_key(event) for event in existing_events}
    seen: set[str] = set()
    new_events: list[dict[str, Any]] = []
    duplicate_events: list[dict[str, Any]] = []
    for raw in events:
        event = dict(raw)
        key = event_dedup_key(event)
        event["_dedup_key"] = key
        if key in existing_keys or key in seen:
            duplicate_events.append(event)
            continue
        seen.add(key)
        new_events.append(event)
    return new_events, duplicate_events


def read_existing_outcome_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return [dict(row) for row in pd.read_parquet(path).to_dict(orient="records")]
    except (OSError, ValueError, ImportError):
        return []


def write_feedback_outputs(
    *,
    feedback_store_path: Path,
    outcome_events_path: Path,
    existing_events: Sequence[Mapping[str, Any]],
    new_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    feedback_store_path.parent.mkdir(parents=True, exist_ok=True)
    outcome_events_path.parent.mkdir(parents=True, exist_ok=True)
    final_events = [dict(event) for event in existing_events] + [dict(event) for event in new_events]
    clean_events = [_public_event_columns(event) for event in final_events]
    frame = pd.DataFrame(clean_events, columns=OUTCOME_EVENT_COLUMNS)
    frame.to_parquet(outcome_events_path, index=False)
    feedback_frame = frame[
        [
            "order_id",
            "symbol",
            "side",
            "open_time_utc",
            "close_time_utc",
            "entry_price",
            "exit_price",
            "net_pnl",
            "profit_ratio",
            "exit_reason",
            "source_file",
            "created_at_utc",
            "event_id",
        ]
    ].copy()
    feedback_frame.to_parquet(feedback_store_path, index=False)
    return {"outcome_events_rows": len(frame), "feedback_rows": len(feedback_frame)}


def _public_event_columns(event: Mapping[str, Any]) -> dict[str, Any]:
    return {column: event.get(column) for column in OUTCOME_EVENT_COLUMNS}


def event_dedup_key(event: Mapping[str, Any]) -> str:
    return dedup_key(
        normalize_identity(event.get("order_id")),
        normalize_identity(event.get("internal_order_id")),
        normalize_identity(event.get("trade_id")),
        str(event.get("row_fingerprint") or ""),
    )


def dedup_key(order_id: str, internal_order_id: str, trade_id: str, row_fingerprint: str) -> str:
    if order_id:
        return f"order_id::{order_id}"
    if internal_order_id:
        return f"internal_order_id::{internal_order_id}"
    if trade_id:
        return f"trade_id::{trade_id}"
    return f"row_fingerprint::{row_fingerprint}"


def row_fingerprint_for(row: Mapping[str, Any]) -> str:
    material = json.dumps({str(key): _json_safe(value) for key, value in row.items()}, sort_keys=True, ensure_ascii=False)
    return _sha256_text(material)


def normalize_identity(value: object) -> str:
    text = clean_text(value)
    if text is None:
        return ""
    excel_integer = re.fullmatch(r"([+-]?\d+)\.0+", text)
    return excel_integer.group(1) if excel_integer else text


def normalize_symbol(value: object) -> str:
    text = clean_text(value)
    if text is None:
        return ""
    return text.upper().replace("/USDT:USDT", "USDT").replace("/", "").replace(":", "").replace("_", "")


def normalize_side(value: object) -> str:
    text = clean_text(value)
    if text is None:
        return ""
    lowered = text.lower()
    if "short" in lowered or "sell" in lowered or lowered in {"vendido"}:
        return "short"
    if "long" in lowered or "buy" in lowered or lowered in {"comprado"}:
        return "long"
    return lowered


def normalize_time(value: object) -> str | None:
    text = clean_text(value)
    if text is None:
        return None
    parsed = pd.to_datetime(text, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.isoformat()


def safe_float(value: object) -> float | None:
    text = clean_text(value)
    if text is None:
        return None
    normalized = text.replace("R$", "").replace("%", "").replace(" ", "")
    if "," in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    try:
        number = float(normalized)
    except ValueError:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return float(number)


def clean_text(value: object) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return None if text == "" or text.lower() in {"nan", "none", "<na>", "nat", "null"} else text


def net_pnl_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "breakeven"


def holding_time_bucket(duration_seconds: float | None) -> str:
    if duration_seconds is None:
        return "unknown"
    if duration_seconds < 15 * 60:
        return "under_15m"
    if duration_seconds < 60 * 60:
        return "15m_to_1h"
    if duration_seconds < 4 * 60 * 60:
        return "1h_to_4h"
    return "over_4h"


def _first_value(row: Mapping[str, Any], candidates: Sequence[str]) -> object:
    lookup = {str(key).lower(): key for key in row}
    for candidate in candidates:
        key = lookup.get(candidate.lower())
        if key is not None:
            return row.get(key)
    return None


def _duration_seconds(open_time: str | None, close_time: str | None) -> float | None:
    if open_time is None or close_time is None:
        return None
    opened = pd.to_datetime(open_time, utc=True, errors="coerce")
    closed = pd.to_datetime(close_time, utc=True, errors="coerce")
    if pd.isna(opened) or pd.isna(closed):
        return None
    return float((closed - opened).total_seconds())


def _ratio_pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return round((numerator / abs(denominator)) * 100.0, 10)


def _pnl_on_margin_pct(net_pnl: float | None, notional: float | None, leverage: float | None) -> float | None:
    if net_pnl is None or notional in (None, 0):
        return None
    margin = abs(notional) / leverage if leverage not in (None, 0) else abs(notional)
    return _ratio_pct(net_pnl, margin)


def _distance_to_liquidation(entry_price: float | None, liquidation_price: float | None, side: str) -> float | None:
    if entry_price in (None, 0) or liquidation_price is None:
        return None
    if side == "short":
        return round(((liquidation_price - entry_price) / entry_price) * 100.0, 10)
    return round(((entry_price - liquidation_price) / entry_price) * 100.0, 10)


def _contains(value: object, token: str) -> bool:
    text = clean_text(value)
    return token in text.lower() if text else False


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_safe(value: object) -> object:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return str(value)


def _resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path.resolve() if path.is_absolute() else (root / path).resolve()
