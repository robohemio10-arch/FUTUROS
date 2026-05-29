from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


STATUS_OK = "ok"
STATUS_BLOCKED = "blocked"
STATUS_MISSING_DATASET = "missing_dataset"
STATUS_MISSING_DECISIONS = "missing_decisions"
STATUS_MISSING_JOIN_KEY = "missing_join_key"
STATUS_MISSING_PROBABILITY_COLUMN = "missing_probability_column"
STATUS_MISSING_OUTCOME_COLUMN = "missing_outcome_column"
STATUS_INVALID_SCHEMA = "invalid_schema"

DEFAULT_DATASET_PATH = Path("data/features/training_dataset_quality_gated_binance_1m.parquet")
DEFAULT_DECISIONS_PATH = Path("data/runtime/ai_shadow_filter_decisions.sqlite")
DEFAULT_REPORT_PATH = Path("data/reports/ai_shadow_outcome_attribution_report.json")

JOIN_KEY = "trade_id"

DECISION_COLUMNS = (
    "decision",
    "ai_decision",
    "shadow_decision",
    "filter_decision",
    "action",
)

PROBABILITY_COLUMNS = (
    "probability_win",
    "win_probability",
    "probability",
    "prob_win",
    "prediction_probability",
    "ai_score",
    "model_score",
    "score",
)

OUTCOME_COLUMNS = (
    "net_return_pct",
    "normalized_return_pct",
    "return_pct",
    "reported_pnl_usdt",
    "pnl_usdt",
    "pnl",
    "gross_return_pct",
    "leveraged_return_pct",
    "profit_abs",
    "realized_pnl",
    "realized_profit",
    "raw_return",
)

SYMBOL_COLUMNS = ("symbol", "pair")
SIDE_COLUMNS = ("side", "direction", "position_side")
TIME_COLUMNS = ("open_1m_ts", "open_time_utc", "trade_time", "timestamp", "created_at")

ACCEPT_DECISIONS = {"AI_ACCEPT", "SHADOW_ENTRY"}
REJECT_DECISIONS = {"AI_REJECT", "SHADOW_SKIP"}

SAFETY_FLAGS = {
    "runtime_mode": "shadow",
    "shadow_only": True,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
}


@dataclass(frozen=True)
class AttributionConfig:
    dataset_path: Path = DEFAULT_DATASET_PATH
    decisions_path: Path = DEFAULT_DECISIONS_PATH
    report_path: Path = DEFAULT_REPORT_PATH
    strict_alignment: bool = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clean_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and np.isnan(value):
        return ""
    return str(value).strip()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def first_existing(columns: list[str] | pd.Index, candidates: tuple[str, ...] | list[str]) -> str | None:
    existing = set(str(col) for col in columns)
    for candidate in candidates:
        if candidate in existing:
            return candidate
    return None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def blocked_payload(
    *,
    status: str,
    reason: str,
    config: AttributionConfig,
    rows: int = 0,
    decision_rows: int = 0,
    joined_rows: int = 0,
    missing_decisions: int = 0,
    extra_decisions: int = 0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "dataset_path": str(config.dataset_path),
        "decisions_path": str(config.decisions_path),
        "report_path": str(config.report_path),
        "rows": int(rows),
        "decision_rows": int(decision_rows),
        "joined_rows": int(joined_rows),
        "valid_rows": 0,
        "missing_decisions": int(missing_decisions),
        "extra_decisions": int(extra_decisions),
        "accepted_count": 0,
        "rejected_count": 0,
        "shadow_entry_count": 0,
        "shadow_skip_count": 0,
        "overall_metrics": empty_metrics(),
        "metrics_by_decision": {},
        "probability_bucket_summary": {},
        "threshold_summary": [],
        "best_threshold_by_expectancy": None,
        "best_threshold_by_profit_factor": None,
        "symbol_summary": {},
        "side_summary": {},
        "generated_at": utc_now(),
        **SAFETY_FLAGS,
    }
    if extra:
        payload.update(extra)
    return json_safe(payload)


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [str(row[1]) for row in rows]


def discover_decision_table(conn: sqlite3.Connection) -> str | None:
    preferred = ("ai_shadow_decisions", "ai_shadow_filter_decisions", "shadow_decisions", "decisions")
    tables = list_tables(conn)
    for table in preferred:
        if table in tables:
            return table

    scored: list[tuple[int, int, str]] = []
    for table in tables:
        cols = set(table_columns(conn, table))
        score = 0
        if JOIN_KEY in cols:
            score += 10
        if any(col in cols for col in DECISION_COLUMNS):
            score += 5
        if any(col in cols for col in PROBABILITY_COLUMNS):
            score += 4
        try:
            count = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        except Exception:
            count = 0
        scored.append((score, count, table))

    scored.sort(reverse=True)
    if scored and scored[0][0] > 0:
        return scored[0][2]
    return None


def read_decisions_sqlite(path: Path) -> tuple[pd.DataFrame, str | None]:
    with sqlite3.connect(path) as conn:
        table = discover_decision_table(conn)
        if not table:
            return pd.DataFrame(), None
        return pd.read_sql_query(f'SELECT * FROM "{table}"', conn), table


def normalize_trade_id(series: pd.Series) -> pd.Series:
    return series.map(clean_text)


def empty_metrics() -> dict[str, Any]:
    return {
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": None,
        "avg_return": None,
        "median_return": None,
        "total_return": 0.0,
        "expectancy": None,
        "profit_factor": None,
        "profit_factor_status": "no_trades",
        "avg_win": None,
        "avg_loss": None,
        "payoff_ratio": None,
        "max_drawdown": None,
    }


def financial_metrics(values: pd.Series | list[float] | np.ndarray) -> dict[str, Any]:
    numeric = pd.to_numeric(pd.Series(values), errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if numeric.empty:
        return empty_metrics()

    wins = numeric[numeric > 0]
    losses = numeric[numeric < 0]
    gross_profit = float(wins.sum())
    gross_loss = float(abs(losses.sum()))

    if gross_loss > 0:
        profit_factor: float | None = gross_profit / gross_loss
        pf_status = "ok"
    elif gross_profit > 0:
        profit_factor = None
        pf_status = "loss_zero"
    else:
        profit_factor = None
        pf_status = "no_gross_loss"

    avg_win = float(wins.mean()) if not wins.empty else None
    avg_loss = float(losses.mean()) if not losses.empty else None
    payoff_ratio = avg_win / abs(avg_loss) if avg_win is not None and avg_loss not in (None, 0) else None

    equity = numeric.cumsum()
    peak = equity.cummax()
    drawdown = equity - peak
    max_drawdown = float(drawdown.min()) if not drawdown.empty else None

    return {
        "trades": int(len(numeric)),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": float(len(wins) / len(numeric)),
        "avg_return": float(numeric.mean()),
        "median_return": float(numeric.median()),
        "total_return": float(numeric.sum()),
        "expectancy": float(numeric.mean()),
        "profit_factor": profit_factor,
        "profit_factor_status": pf_status,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff_ratio,
        "max_drawdown": max_drawdown,
    }


def summarize_by_group(frame: pd.DataFrame, group_col: str, outcome_col: str) -> dict[str, dict[str, Any]]:
    if group_col not in frame.columns:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, group in frame.groupby(group_col, dropna=False, sort=True):
        label = clean_text(key) or "UNKNOWN"
        result[label] = financial_metrics(group[outcome_col])
    return result


def probability_bucket_summary(frame: pd.DataFrame, probability_col: str, outcome_col: str) -> dict[str, dict[str, Any]]:
    bins = [0.0, 0.40, 0.50, 0.60, 0.70, 0.80, 1.0000001]
    labels = ["0.00-0.40", "0.40-0.50", "0.50-0.60", "0.60-0.70", "0.70-0.80", "0.80-1.00"]

    tmp = frame.copy()
    tmp["_bucket"] = pd.cut(
        pd.to_numeric(tmp[probability_col], errors="coerce"),
        bins=bins,
        labels=labels,
        include_lowest=True,
        right=False,
    )

    return {label: financial_metrics(tmp.loc[tmp["_bucket"].astype(str) == label, outcome_col]) for label in labels}


def profit_factor_sort_value(row: dict[str, Any]) -> float:
    value = row.get("profit_factor")
    if isinstance(value, (float, int)) and np.isfinite(value):
        return float(value)
    if row.get("profit_factor_status") == "loss_zero" and float(row.get("total_return") or 0.0) > 0:
        return float("inf")
    return float("-inf")


def threshold_summary(
    frame: pd.DataFrame,
    probability_col: str,
    outcome_col: str,
    thresholds: list[float] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None]:
    if thresholds is None:
        thresholds = [round(float(item), 2) for item in np.arange(0.05, 1.00, 0.05)]

    rows: list[dict[str, Any]] = []
    probabilities = pd.to_numeric(frame[probability_col], errors="coerce")
    for threshold in thresholds:
        selected = frame.loc[probabilities >= threshold]
        metrics = financial_metrics(selected[outcome_col])
        rows.append({"threshold": float(threshold), "selected_rows": int(len(selected)), **metrics})

    non_empty = [row for row in rows if int(row["selected_rows"]) > 0]
    best_expectancy = max(
        non_empty,
        key=lambda row: row["expectancy"] if row["expectancy"] is not None else float("-inf"),
    ) if non_empty else None
    best_profit_factor = max(non_empty, key=profit_factor_sort_value) if non_empty else None
    return rows, best_expectancy, best_profit_factor


def run_ai_shadow_outcome_attribution(config: AttributionConfig) -> dict[str, Any]:
    if not config.dataset_path.exists():
        payload = blocked_payload(status=STATUS_MISSING_DATASET, reason="missing_dataset", config=config)
        write_json(config.report_path, payload)
        return payload

    if not config.decisions_path.exists():
        payload = blocked_payload(status=STATUS_MISSING_DECISIONS, reason="missing_decisions", config=config)
        write_json(config.report_path, payload)
        return payload

    try:
        dataset = pd.read_parquet(config.dataset_path)
        decisions, decision_table = read_decisions_sqlite(config.decisions_path)
    except Exception as exc:
        payload = blocked_payload(
            status=STATUS_INVALID_SCHEMA,
            reason=f"read_failed:{exc}",
            config=config,
        )
        write_json(config.report_path, payload)
        return payload

    if dataset.empty:
        payload = blocked_payload(status=STATUS_INVALID_SCHEMA, reason="empty_dataset", config=config)
        write_json(config.report_path, payload)
        return payload

    if decisions.empty:
        payload = blocked_payload(
            status=STATUS_MISSING_DECISIONS,
            reason="empty_decisions",
            config=config,
            rows=len(dataset),
            extra={"decision_table": decision_table},
        )
        write_json(config.report_path, payload)
        return payload

    if JOIN_KEY not in dataset.columns or JOIN_KEY not in decisions.columns:
        payload = blocked_payload(
            status=STATUS_MISSING_JOIN_KEY,
            reason="missing_join_key",
            config=config,
            rows=len(dataset),
            decision_rows=len(decisions),
            extra={
                "dataset_columns": list(dataset.columns),
                "decision_columns": list(decisions.columns),
                "decision_table": decision_table,
            },
        )
        write_json(config.report_path, payload)
        return payload

    dataset = dataset.copy()
    decisions = decisions.copy()
    dataset[JOIN_KEY] = normalize_trade_id(dataset[JOIN_KEY])
    decisions[JOIN_KEY] = normalize_trade_id(decisions[JOIN_KEY])
    dataset = dataset.loc[dataset[JOIN_KEY] != ""].copy()
    decisions = decisions.loc[decisions[JOIN_KEY] != ""].copy()

    dataset_ids = set(dataset[JOIN_KEY])
    decision_ids = set(decisions[JOIN_KEY])
    missing_ids = sorted(dataset_ids - decision_ids)
    extra_ids = sorted(decision_ids - dataset_ids)

    decision_col = first_existing(decisions.columns, DECISION_COLUMNS)
    probability_col_decision = first_existing(decisions.columns, PROBABILITY_COLUMNS)
    probability_col_dataset = first_existing(dataset.columns, PROBABILITY_COLUMNS)
    outcome_col = first_existing(dataset.columns, OUTCOME_COLUMNS)

    if not decision_col:
        payload = blocked_payload(status=STATUS_INVALID_SCHEMA, reason="missing_decision_column", config=config, rows=len(dataset), decision_rows=len(decisions))
        write_json(config.report_path, payload)
        return payload

    if not probability_col_decision and not probability_col_dataset:
        payload = blocked_payload(status=STATUS_MISSING_PROBABILITY_COLUMN, reason="missing_probability_column", config=config, rows=len(dataset), decision_rows=len(decisions))
        write_json(config.report_path, payload)
        return payload

    if not outcome_col:
        payload = blocked_payload(status=STATUS_MISSING_OUTCOME_COLUMN, reason="missing_outcome_column", config=config, rows=len(dataset), decision_rows=len(decisions))
        write_json(config.report_path, payload)
        return payload

    decision_cols = [JOIN_KEY, decision_col]
    if probability_col_decision:
        decision_cols.append(probability_col_decision)

    decisions_small = decisions[decision_cols].copy()
    rename_map = {decision_col: "_shadow_decision"}
    if probability_col_decision:
        rename_map[probability_col_decision] = "_shadow_probability"
    decisions_small = decisions_small.rename(columns=rename_map)

    joined = dataset.merge(decisions_small, on=JOIN_KEY, how="inner", validate="one_to_one")
    if joined.empty:
        payload = blocked_payload(
            status=STATUS_BLOCKED,
            reason="no_joined_rows",
            config=config,
            rows=len(dataset),
            decision_rows=len(decisions),
            joined_rows=0,
            missing_decisions=len(missing_ids),
            extra_decisions=len(extra_ids),
            extra={"decision_table": decision_table},
        )
        write_json(config.report_path, payload)
        return payload

    probability_col = "_shadow_probability" if "_shadow_probability" in joined.columns else probability_col_dataset
    if probability_col is None:
        payload = blocked_payload(status=STATUS_MISSING_PROBABILITY_COLUMN, reason="missing_probability_column", config=config)
        write_json(config.report_path, payload)
        return payload

    joined["_probability"] = pd.to_numeric(joined[probability_col], errors="coerce")
    joined["_outcome"] = pd.to_numeric(joined[outcome_col], errors="coerce")
    joined["_decision"] = joined["_shadow_decision"].map(lambda x: clean_text(x).upper())
    joined = joined.replace([np.inf, -np.inf], np.nan)

    valid = joined.dropna(subset=["_probability", "_outcome"]).copy()
    valid = valid.loc[(valid["_probability"] >= 0) & (valid["_probability"] <= 1)].copy()

    if valid.empty:
        payload = blocked_payload(
            status=STATUS_INVALID_SCHEMA,
            reason="no_valid_probability_or_outcome_values",
            config=config,
            rows=len(dataset),
            decision_rows=len(decisions),
            joined_rows=len(joined),
            missing_decisions=len(missing_ids),
            extra_decisions=len(extra_ids),
        )
        write_json(config.report_path, payload)
        return payload

    symbol_col = first_existing(valid.columns, SYMBOL_COLUMNS)
    side_col = first_existing(valid.columns, SIDE_COLUMNS)
    if symbol_col:
        valid["_symbol"] = valid[symbol_col].map(clean_text)
    if side_col:
        valid["_side"] = valid[side_col].map(lambda x: clean_text(x).upper())

    if config.strict_alignment and (missing_ids or extra_ids):
        status = STATUS_BLOCKED
        reason = "dataset_decision_alignment_mismatch"
    else:
        status = STATUS_OK
        reason = None

    thresholds, best_expectancy, best_profit_factor = threshold_summary(valid, "_probability", "_outcome")

    payload = {
        "status": status,
        "reason": reason,
        "dataset_path": str(config.dataset_path),
        "decisions_path": str(config.decisions_path),
        "decision_table": decision_table,
        "report_path": str(config.report_path),
        "rows": int(len(dataset)),
        "decision_rows": int(len(decisions)),
        "joined_rows": int(len(joined)),
        "valid_rows": int(len(valid)),
        "missing_decisions": int(len(missing_ids)),
        "extra_decisions": int(len(extra_ids)),
        "missing_decision_ids_sample": missing_ids[:20],
        "extra_decision_ids_sample": extra_ids[:20],
        "accepted_count": int(valid["_decision"].isin(ACCEPT_DECISIONS).sum()),
        "rejected_count": int(valid["_decision"].isin(REJECT_DECISIONS).sum()),
        "shadow_entry_count": int((valid["_decision"] == "SHADOW_ENTRY").sum()),
        "shadow_skip_count": int((valid["_decision"] == "SHADOW_SKIP").sum()),
        "outcome_column": outcome_col,
        "probability_column": probability_col,
        "decision_column": decision_col,
        "symbol_column": symbol_col,
        "side_column": side_col,
        "overall_metrics": financial_metrics(valid["_outcome"]),
        "metrics_by_decision": summarize_by_group(valid, "_decision", "_outcome"),
        "probability_bucket_summary": probability_bucket_summary(valid, "_probability", "_outcome"),
        "threshold_summary": thresholds,
        "best_threshold_by_expectancy": best_expectancy,
        "best_threshold_by_profit_factor": best_profit_factor,
        "symbol_summary": summarize_by_group(valid, "_symbol", "_outcome") if symbol_col else {},
        "side_summary": summarize_by_group(valid, "_side", "_outcome") if side_col else {},
        "generated_at": utc_now(),
        **SAFETY_FLAGS,
    }

    payload = json_safe(payload)
    write_json(config.report_path, payload)
    return payload
