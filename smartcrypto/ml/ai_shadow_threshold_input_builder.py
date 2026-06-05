from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_OUTPUT_PATH = Path("data/reports/ai_shadow_threshold_evaluation_input.parquet")
DEFAULT_REPORT_PATH = Path("data/reports/ai_shadow_threshold_evaluation_input_report.json")
PROBABILITY_CANDIDATES = ("probability_or_confidence", "probability", "probability_win", "confidence", "proba", "score", "model_confidence", "ai_score")
DECISION_CANDIDATES = ("decision", "action_shadow", "ai_decision", "shadow_decision", "action")
TIME_CANDIDATES = ("opened_at_utc", "open_time_utc", "horario_abertura", "timestamp_utc", "timestamp", "created_at_utc", "generated_at_utc", "created_at")
OUTCOME_TIME_CANDIDATES = ("opened_at_utc", "open_time_utc", "horario_abertura", "timestamp_utc", "timestamp", "created_at_utc", "generated_at_utc", "created_at")
RETURN_CANDIDATES = ("target_return", "return_pct", "pnl_fechado", "pnl_net", "pnl_usdt", "pnl", "profit_abs")
EMBEDDED_OUTCOME_CANDIDATES = (
    "shadow_filtered_pnl_usdt",
    "base_policy_pnl_usdt",
    "raw_pnl_usdt",
    "pnl_usdt",
    "pnl_net",
    "pnl_fechado",
)
TARGET_CANDIDATES = ("target_profitable", "target_win")
SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
)


def build_ai_shadow_threshold_evaluation_input(
    *,
    decisions: str | Path | None = None,
    outcomes: str | Path | None = None,
    microbatch: str | Path | None = None,
    paper_feedback: str | Path | None = None,
    sqlite_decisions: str | Path | None = None,
    output: str | Path = DEFAULT_OUTPUT_PATH,
    report: str | Path | None = DEFAULT_REPORT_PATH,
    max_time_delta_minutes: float = 60.0,
    strict: bool = False,
    now: datetime | None = None,
    safety_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    generated_at = ensure_utc(now or datetime.now(timezone.utc))
    safety = safety_payload(safety_overrides)
    output_path = Path(output)
    report_path = Path(report) if report is not None else None
    decision_sources = [source for source in [decisions, sqlite_decisions] if source is not None]
    outcome_sources = [source for source in [outcomes, microbatch, paper_feedback] if source is not None]

    blocking_errors = [f"unsafe_safety_flag:{flag}" for flag in unsafe_safety_flags(safety)]
    warnings: list[str] = []
    source_errors: list[str] = []
    decision_frames = []
    outcome_frames = []
    source_hashes: dict[str, str | None] = {}

    for source in decision_sources:
        frame, error = load_any_source(source, sqlite=Path(source) == Path(sqlite_decisions) if sqlite_decisions else False)
        source_hashes[str(source)] = file_hash(Path(source))
        if error:
            source_errors.append(f"decision_source:{source}:{error}")
        elif not frame.empty:
            frame = frame.copy()
            frame["source_decision_path"] = str(source)
            decision_frames.append(frame)

    for source in outcome_sources:
        frame, error = load_any_source(source)
        source_hashes[str(source)] = file_hash(Path(source))
        if error:
            source_errors.append(f"outcome_source:{source}:{error}")
        elif not frame.empty:
            frame = frame.copy()
            frame["source_outcome_path"] = str(source)
            outcome_frames.append(frame)

    decisions_frame = pd.concat(decision_frames, ignore_index=True, sort=False) if decision_frames else pd.DataFrame()
    outcomes_frame = pd.concat(outcome_frames, ignore_index=True, sort=False) if outcome_frames else pd.DataFrame()
    if decisions_frame.empty:
        blocking_errors.append("missing_decisions")

    probability_column = first_existing(decisions_frame, PROBABILITY_CANDIDATES)
    decision_column = first_existing(decisions_frame, DECISION_CANDIDATES)
    if probability_column is None and not decisions_frame.empty:
        blocking_errors.append("missing_probability")
    if decision_column is None and not decisions_frame.empty:
        blocking_errors.append("missing_decision")
    if source_errors:
        warnings.extend(source_errors)

    if blocking_errors:
        payload = base_report(
            status="blocked",
            reason=";".join(sorted(set(blocking_errors))),
            generated_at=generated_at,
            output_path=output_path,
            source_hashes=source_hashes,
            decisions_rows=len(decisions_frame),
            outcomes_rows=len(outcomes_frame),
            safety=safety,
        )
        payload["blocking_errors"] = sorted(set(blocking_errors))
        payload["warnings"] = sorted(set(warnings))
        write_json(payload, report_path)
        return payload

    normalized_decisions = normalize_decisions(decisions_frame, decision_column=decision_column)
    missing_probability_rows = int(normalized_decisions["probability_or_confidence"].isna().sum())
    missing_decision_rows = int(normalized_decisions["decision"].eq("").sum())
    if missing_probability_rows == len(normalized_decisions):
        blocking_errors.append("missing_probability")
    if missing_decision_rows == len(normalized_decisions):
        blocking_errors.append("missing_decision")
    if blocking_errors:
        payload = base_report(
            status="blocked",
            reason=";".join(sorted(set(blocking_errors))),
            generated_at=generated_at,
            output_path=output_path,
            source_hashes=source_hashes,
            decisions_rows=len(decisions_frame),
            outcomes_rows=len(outcomes_frame),
            safety=safety,
        )
        payload["missing_probability_rows"] = missing_probability_rows
        payload["missing_decision_rows"] = missing_decision_rows
        payload["blocking_errors"] = sorted(set(blocking_errors))
        payload["warnings"] = sorted(set(warnings))
        write_json(payload, report_path)
        return payload

    normalized_outcomes = normalize_outcomes(outcomes_frame)
    rows, match_stats = match_decisions_to_outcomes(
        normalized_decisions,
        normalized_outcomes,
        max_time_delta_minutes=max_time_delta_minutes,
    )
    output_frame = pd.DataFrame(rows)
    if output_frame.empty:
        output_frame = empty_output_frame()
    output_frame["input_row_hash"] = output_frame.apply(row_hash, axis=1)
    output_frame = ensure_output_columns(output_frame)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_frame.to_parquet(output_path, index=False)
    output_hash = file_hash(output_path)

    matched_rows = int(output_frame["matched"].sum()) if "matched" in output_frame else 0
    unmatched_rows = int(len(output_frame) - matched_rows)
    accepted_count = int(output_frame["decision"].isin(["AI_ACCEPT", "SHADOW_ENTRY", "ACCEPT"]).sum())
    rejected_count = int(output_frame["decision"].isin(["AI_REJECT", "SHADOW_SKIP", "REJECT"]).sum())
    missing_outcome_rows = unmatched_rows
    if len(output_frame) == 0:
        status = "blocked" if strict else "warning"
        reason = "empty_output"
    elif missing_outcome_rows > 0:
        status = "warning"
        reason = "partial_unmatched_outcomes"
    else:
        status = "ok"
        reason = "ok"

    payload = {
        "status": status,
        "reason": reason,
        "generated_at_utc": iso(generated_at),
        "output_path": str(output_path),
        "report_path": str(report_path) if report_path is not None else None,
        "source_decision_paths": [str(source) for source in decision_sources],
        "source_outcome_paths": [str(source) for source in outcome_sources],
        "source_hashes": source_hashes,
        "output_hash": output_hash,
        "input_decisions_rows": int(len(decisions_frame)),
        "input_outcomes_rows": int(len(outcomes_frame)),
        "output_rows": int(len(output_frame)),
        "matched_rows": matched_rows,
        "unmatched_rows": unmatched_rows,
        "external_matched_rows": match_stats["external_matched_rows"],
        "embedded_outcome_rows": match_stats["embedded_outcome_rows"],
        "embedded_matched_rows": match_stats["embedded_matched_rows"],
        "embedded_outcome_column_used": match_stats["embedded_outcome_column_used"],
        "embedded_outcome_columns_used": match_stats["embedded_outcome_columns_used"],
        "unmatched_reason_counts": match_stats["unmatched_reason_counts"],
        "missing_probability_rows": missing_probability_rows,
        "missing_decision_rows": missing_decision_rows,
        "missing_outcome_rows": missing_outcome_rows,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "symbols": sorted(value for value in output_frame.get("symbol", pd.Series(dtype=str)).dropna().astype(str).unique() if value),
        "sides": sorted(value for value in output_frame.get("side", pd.Series(dtype=str)).dropna().astype(str).unique() if value),
        "probability_column": probability_column,
        "probability_columns_used": [column for column in PROBABILITY_CANDIDATES if column in decisions_frame.columns],
        "decision_column": decision_column,
        "max_time_delta_minutes": float(max_time_delta_minutes),
        "blocking_errors": [],
        "warnings": sorted(set(warnings)),
        **safety,
    }
    write_json(payload, report_path)
    return payload


def load_any_source(path: str | Path, *, sqlite: bool = False) -> tuple[pd.DataFrame, str | None]:
    source = Path(path)
    if not source.exists():
        return pd.DataFrame(), "missing_source"
    try:
        if sqlite or source.suffix.lower() in {".sqlite", ".db"}:
            return read_sqlite_read_only(source), None
        return read_table(source), None
    except Exception as exc:
        return pd.DataFrame(), f"read_failed:{type(exc).__name__}:{exc}"


def read_sqlite_read_only(path: Path) -> pd.DataFrame:
    uri = f"file:{path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        tables = pd.read_sql_query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name", conn)
        for table in tables["name"].astype(str).tolist():
            frame = pd.read_sql_query(f'SELECT * FROM "{table}"', conn)
            if first_existing(frame, DECISION_CANDIDATES) and first_existing(frame, PROBABILITY_CANDIDATES):
                frame["source_sqlite_table"] = table
                return frame
        if not tables.empty:
            table = str(tables.iloc[0]["name"])
            frame = pd.read_sql_query(f'SELECT * FROM "{table}"', conn)
            frame["source_sqlite_table"] = table
            return frame
    return pd.DataFrame()


def read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".jsonl":
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return pd.DataFrame(rows)
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
        if isinstance(payload, list):
            return pd.DataFrame(payload)
        if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
            return pd.DataFrame(payload["rows"])
        return pd.DataFrame([payload])
    raise ValueError(f"unsupported_format:{suffix}")


def normalize_decisions(frame: pd.DataFrame, *, decision_column: str) -> pd.DataFrame:
    result = frame.copy()
    result["probability_or_confidence"] = coalesce_numeric(result, PROBABILITY_CANDIDATES)
    result["decision"] = result[decision_column].map(normalize_decision)
    result["order_id"] = normalize_id_column(result["order_id"] if "order_id" in result.columns else pd.Series([None] * len(result), index=result.index))
    result["trade_id"] = normalize_id_column(result["trade_id"] if "trade_id" in result.columns else pd.Series([None] * len(result), index=result.index))
    result["symbol"] = normalize_symbol_series(first_series(result, ("symbol", "pair", "moeda")))
    result["side"] = normalize_side_series(first_series(result, ("side", "fechar_side", "direction")))
    result["decision_time_utc"] = parse_time_series(first_series(result, TIME_CANDIDATES))
    if "model_id" not in result.columns and "ai_model_name" in result.columns:
        result["model_id"] = result["ai_model_name"]
    if "threshold" not in result.columns and "ai_threshold" in result.columns:
        result["threshold"] = result["ai_threshold"]
    if "action_shadow" not in result.columns:
        result["action_shadow"] = result["decision"]
    result["source_decision_path"] = result.get("source_decision_path")
    for column in EMBEDDED_OUTCOME_CANDIDATES:
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    return result


def normalize_outcomes(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    result = frame.copy()
    result["order_id"] = normalize_id_column(result["order_id"] if "order_id" in result.columns else pd.Series([None] * len(result), index=result.index))
    result["trade_id"] = normalize_id_column(result["trade_id"] if "trade_id" in result.columns else pd.Series([None] * len(result), index=result.index))
    result["symbol"] = normalize_symbol_series(first_series(result, ("symbol", "pair", "moeda")))
    result["side"] = normalize_side_series(first_series(result, ("side", "fechar_side", "direction")))
    result["outcome_time_utc"] = parse_time_series(first_series(result, OUTCOME_TIME_CANDIDATES))
    for column in RETURN_CANDIDATES:
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    for column in TARGET_CANDIDATES:
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    return result


def coalesce_numeric(frame: pd.DataFrame, candidates: tuple[str, ...]) -> pd.Series:
    result = pd.Series([np.nan] * len(frame), index=frame.index, dtype="float64")
    for column in candidates:
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
        result = result.where(result.notna(), values)
    return result


def match_decisions_to_outcomes(decisions: pd.DataFrame, outcomes: pd.DataFrame, *, max_time_delta_minutes: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = []
    used_outcome_indexes: set[int] = set()
    external_matched_rows = 0
    embedded_matched_rows = 0
    embedded_outcome_rows = 0
    embedded_outcome_columns_used: list[str] = []
    unmatched_reason_counts: dict[str, int] = {}
    for decision_index, decision in decisions.iterrows():
        embedded_outcome, embedded_column = build_embedded_outcome(decision)
        if embedded_outcome is not None:
            embedded_outcome_rows += 1
        outcome, method, confidence = find_match(decision, outcomes, used_outcome_indexes, max_time_delta_minutes=max_time_delta_minutes)
        if outcome is not None:
            used_outcome_indexes.add(int(outcome.name))
            external_matched_rows += 1
        elif embedded_outcome is not None:
            outcome = embedded_outcome
            method = "embedded_decision_outcome"
            confidence = 1.0
            embedded_matched_rows += 1
            if embedded_column:
                embedded_outcome_columns_used.append(embedded_column)
        else:
            reason = "missing_embedded_outcome" if outcomes.empty else "no_external_or_embedded_outcome"
            unmatched_reason_counts[reason] = unmatched_reason_counts.get(reason, 0) + 1
        rows.append(build_output_row(decision, outcome, method, confidence))
    columns_used = sorted(set(embedded_outcome_columns_used), key=EMBEDDED_OUTCOME_CANDIDATES.index)
    stats = {
        "external_matched_rows": int(external_matched_rows),
        "embedded_outcome_rows": int(embedded_outcome_rows),
        "embedded_matched_rows": int(embedded_matched_rows),
        "embedded_outcome_column_used": columns_used[0] if columns_used else None,
        "embedded_outcome_columns_used": columns_used,
        "unmatched_reason_counts": dict(sorted(unmatched_reason_counts.items())),
    }
    return rows, stats


def build_embedded_outcome(decision: pd.Series) -> tuple[pd.Series | None, str | None]:
    for column in EMBEDDED_OUTCOME_CANDIDATES:
        if column not in decision.index:
            continue
        value = decision.get(column)
        if pd.isna(value):
            continue
        value = pd.to_numeric(pd.Series([value]), errors="coerce").replace([np.inf, -np.inf], np.nan).iloc[0]
        if pd.isna(value):
            continue
        outcome = {
            "order_id": decision.get("order_id", ""),
            "trade_id": decision.get("trade_id", ""),
            "symbol": decision.get("symbol", ""),
            "side": decision.get("side", ""),
            "outcome_time_utc": decision.get("decision_time_utc"),
            "open_time_utc": iso_timestamp(decision.get("decision_time_utc")) if pd.notna(decision.get("decision_time_utc")) else None,
            "pnl_usdt": float(value),
            "pnl_fechado": float(value),
            "target_profitable": int(float(value) > 0.0),
            "source_outcome_path": decision.get("source_decision_path"),
        }
        return pd.Series(outcome), column
    return None, None


def find_match(decision: pd.Series, outcomes: pd.DataFrame, used: set[int], *, max_time_delta_minutes: float) -> tuple[pd.Series | None, str, float]:
    if outcomes.empty:
        return None, "unmatched", 0.0
    for key, method in (("order_id", "order_id"), ("trade_id", "trade_id")):
        value = decision.get(key)
        if value:
            candidates = outcomes.loc[outcomes[key].eq(value)]
            candidates = candidates.loc[[idx for idx in candidates.index if int(idx) not in used]]
            if not candidates.empty:
                return candidates.iloc[0], method, 1.0
    if not decision.get("symbol") or not decision.get("side") or pd.isna(decision.get("decision_time_utc")):
        return None, "unmatched", 0.0
    candidates = outcomes.loc[
        outcomes["symbol"].eq(decision.get("symbol"))
        & outcomes["side"].eq(decision.get("side"))
        & outcomes["outcome_time_utc"].notna()
    ].copy()
    candidates = candidates.loc[[idx for idx in candidates.index if int(idx) not in used]]
    if candidates.empty:
        return None, "unmatched", 0.0
    deltas = (candidates["outcome_time_utc"] - decision.get("decision_time_utc")).abs()
    max_delta = pd.Timedelta(minutes=float(max_time_delta_minutes))
    candidates = candidates.assign(_delta=deltas)
    candidates = candidates.loc[candidates["_delta"] <= max_delta].sort_values("_delta")
    if candidates.empty:
        return None, "unmatched", 0.0
    delta_seconds = float(candidates.iloc[0]["_delta"].total_seconds())
    confidence = max(0.0, 1.0 - (delta_seconds / max(max_delta.total_seconds(), 1.0)))
    return candidates.drop(columns=["_delta"]).iloc[0], "symbol_side_time_window", confidence


def build_output_row(decision: pd.Series, outcome: pd.Series | None, match_method: str, match_confidence: float) -> dict[str, Any]:
    row = {column: decision.get(column) for column in decision.index if column in optional_output_columns()}
    row["matched"] = outcome is not None
    row["probability_or_confidence"] = float(decision.get("probability_or_confidence")) if pd.notna(decision.get("probability_or_confidence")) else np.nan
    row["decision"] = decision.get("decision", "")
    row["source_decision_path"] = decision.get("source_decision_path")
    row["match_method"] = match_method
    row["match_confidence"] = float(match_confidence)
    if outcome is not None:
        for column in optional_output_columns():
            if column not in {"probability_or_confidence", "decision", "source_decision_path"} and column in outcome.index and pd.notna(outcome.get(column)):
                row[column] = outcome.get(column)
        row["source_outcome_path"] = outcome.get("source_outcome_path")
        if "outcome_time_utc" in outcome.index and pd.notna(outcome.get("outcome_time_utc")):
            row.setdefault("open_time_utc", iso_timestamp(outcome.get("outcome_time_utc")))
    else:
        row["source_outcome_path"] = None
    if pd.notna(decision.get("decision_time_utc")):
        row.setdefault("open_time_utc", iso_timestamp(decision.get("decision_time_utc")))
    return row


def optional_output_columns() -> set[str]:
    return {
        "order_id",
        "trade_id",
        "symbol",
        "side",
        "opened_at_utc",
        "open_time_utc",
        "closed_at_utc",
        "close_time_utc",
        "model_id",
        "model_version",
        "threshold",
        "action_shadow",
        "reason",
        "feature_hash",
        "target_profitable",
        "target_win",
        "pnl_fechado",
        "pnl_net",
        "pnl_usdt",
        "return_pct",
        "target_return",
        "source_decision_path",
        "source_outcome_path",
    }


def ensure_output_columns(frame: pd.DataFrame) -> pd.DataFrame:
    required = ["matched", "probability_or_confidence", "decision"]
    optional = [
        "order_id",
        "trade_id",
        "symbol",
        "side",
        "opened_at_utc",
        "open_time_utc",
        "closed_at_utc",
        "close_time_utc",
        "model_id",
        "model_version",
        "threshold",
        "action_shadow",
        "reason",
        "feature_hash",
        "input_row_hash",
        "target_profitable",
        "target_win",
        "pnl_fechado",
        "pnl_net",
        "pnl_usdt",
        "return_pct",
        "target_return",
        "source_decision_path",
        "source_outcome_path",
        "match_method",
        "match_confidence",
    ]
    for column in [*required, *optional]:
        if column not in frame.columns:
            frame[column] = None
    frame["matched"] = frame["matched"].astype(bool)
    frame["probability_or_confidence"] = pd.to_numeric(frame["probability_or_confidence"], errors="coerce")
    frame["decision"] = frame["decision"].astype(str)
    return frame[[*required, *[column for column in optional if column not in required]]]


def empty_output_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["matched", "probability_or_confidence", "decision"])


def first_existing(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    if frame.empty:
        return None
    return next((candidate for candidate in candidates if candidate in frame.columns), None)


def first_series(frame: pd.DataFrame, candidates: tuple[str, ...]) -> pd.Series:
    column = first_existing(frame, candidates)
    return frame[column] if column else pd.Series([None] * len(frame), index=frame.index)


def normalize_id_column(series: pd.Series | None) -> pd.Series:
    if series is None:
        return pd.Series(dtype=object)
    return series.map(normalize_id)


def normalize_id(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") else text


def normalize_symbol_series(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.upper().str.replace("/", "", regex=False).str.replace(":USDT", "", regex=False).str.strip()


def normalize_side_series(series: pd.Series) -> pd.Series:
    aliases = {"BUY": "long", "SELL": "short", "LONG": "long", "SHORT": "short"}
    return series.fillna("").astype(str).str.strip().str.upper().map(lambda value: aliases.get(value, value.lower()))


def parse_time_series(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def normalize_decision(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().upper()
    aliases = {
        "ACCEPT": "AI_ACCEPT",
        "REJECT": "AI_REJECT",
        "ENTRY": "SHADOW_ENTRY",
        "SKIP": "SHADOW_SKIP",
    }
    return aliases.get(text, text)


def row_hash(row: pd.Series) -> str:
    payload = {
        key: str(value)
        for key, value in row.to_dict().items()
        if key != "input_row_hash" and value is not None and not (isinstance(value, float) and np.isnan(value))
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def base_report(
    *,
    status: str,
    reason: str,
    generated_at: datetime,
    output_path: Path,
    source_hashes: dict[str, str | None],
    decisions_rows: int,
    outcomes_rows: int,
    safety: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "generated_at_utc": iso(generated_at),
        "output_path": str(output_path),
        "source_hashes": source_hashes,
        "output_hash": None,
        "input_decisions_rows": int(decisions_rows),
        "input_outcomes_rows": int(outcomes_rows),
        "output_rows": 0,
        "matched_rows": 0,
        "unmatched_rows": 0,
        "missing_probability_rows": 0,
        "missing_decision_rows": 0,
        "missing_outcome_rows": 0,
        "accepted_count": 0,
        "rejected_count": 0,
        "symbols": [],
        "sides": [],
        **safety,
    }


def safety_payload(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "paper_only": True,
        "shadow_only": True,
        "runtime_mode": "paper",
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "freqtrade_db_touched": False,
        "training_dataset_touched": False,
        "trades_master_touched": False,
        "registry_updated": False,
        "model_promoted": False,
        "signal_producer_updated": False,
    }
    if overrides:
        payload.update(overrides)
    return payload


def unsafe_safety_flags(payload: dict[str, Any]) -> list[str]:
    unsafe = []
    if payload.get("paper_only") is not True:
        unsafe.append("paper_only")
    if payload.get("shadow_only") is not True:
        unsafe.append("shadow_only")
    if payload.get("runtime_mode") != "paper":
        unsafe.append("runtime_mode")
    for flag in SAFE_FALSE_FLAGS:
        if payload.get(flag) is True:
            unsafe.append(flag)
    return unsafe


def write_json(payload: dict[str, Any], path: Path | None) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")


def ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return ensure_utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_timestamp(value: Any) -> str | None:
    if pd.isna(value):
        return None
    return pd.Timestamp(value).tz_convert("UTC").isoformat().replace("+00:00", "Z")
