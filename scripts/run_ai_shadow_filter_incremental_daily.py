from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd


DEFAULT_INPUT = "data/features/training_dataset_quality_gated_binance_1m.parquet"
DEFAULT_MODEL = "data/models/ai_shadow_filter_extratrees_050.joblib"
DEFAULT_SQLITE = "data/runtime/ai_shadow_filter_decisions.sqlite"
DEFAULT_SUMMARY = "data/reports/ai_shadow_filter_incremental_daily_summary.json"
DEFAULT_NEW_DECISIONS = "data/reports/ai_shadow_filter_incremental_daily_new_decisions.csv"


def json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    if isinstance(obj, tuple):
        return [json_safe(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return float(obj)
    return obj


def clean_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and np.isnan(value):
        return ""
    return str(value).strip()


def find_model_object(bundle: object) -> object:
    if hasattr(bundle, "predict_proba") or hasattr(bundle, "predict"):
        return bundle

    if isinstance(bundle, dict):
        for key in ["model", "pipeline", "estimator", "classifier", "clf"]:
            value = bundle.get(key)
            if value is not None and (hasattr(value, "predict_proba") or hasattr(value, "predict")):
                return value

    raise RuntimeError("Modelo n├úo cont├®m estimator/pipeline com predict_proba ou predict.")


def extract_features(bundle: object) -> list[str]:
    if isinstance(bundle, dict):
        for key in ["feature_columns", "features", "feature_names", "model_features"]:
            if key in bundle:
                return [str(x) for x in list(bundle[key])]

        for key in ["model", "pipeline", "estimator", "classifier", "clf"]:
            if key in bundle:
                nested = extract_features(bundle[key])
                if nested:
                    return nested

    for attr in ["feature_names_in_", "feature_name_", "feature_names"]:
        if hasattr(bundle, attr):
            return [str(x) for x in list(getattr(bundle, attr))]

    if hasattr(bundle, "steps"):
        for _, step in bundle.steps:
            nested = extract_features(step)
            if nested:
                return nested

    if hasattr(bundle, "named_steps"):
        for step in bundle.named_steps.values():
            nested = extract_features(step)
            if nested:
                return nested

    return []


def resolve_threshold(bundle: object, explicit: float | None) -> float:
    if explicit is not None:
        return float(explicit)

    if isinstance(bundle, dict):
        for key in ["threshold", "model_threshold", "decision_threshold", "probability_threshold"]:
            if key in bundle:
                try:
                    return float(bundle[key])
                except Exception:
                    pass

    return 0.50


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [str(r[0]) for r in rows]


def table_info(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [
        {
            "cid": int(r[0]),
            "name": str(r[1]),
            "type": str(r[2] or ""),
            "notnull": bool(r[3]),
            "default": r[4],
            "pk": int(r[5]),
        }
        for r in rows
    ]


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [c["name"] for c in table_info(conn, table)]


def ensure_default_table(conn: sqlite3.Connection) -> str:
    table = "ai_shadow_decisions"
    conn.execute(
        f'''
        CREATE TABLE IF NOT EXISTS "{table}" (
            trade_id TEXT PRIMARY KEY,
            logged_at_utc TEXT NOT NULL,
            trade_time TEXT,
            open_time_utc TEXT,
            symbol TEXT,
            side TEXT,
            target_win INTEGER,
            probability REAL,
            win_probability REAL,
            score REAL,
            decision TEXT,
            ai_decision TEXT,
            model_threshold REAL,
            model_path TEXT,
            source_file TEXT,
            created_at_utc TEXT
        )
        '''
    )
    conn.commit()
    return table


def discover_table(conn: sqlite3.Connection) -> str:
    tables = list_tables(conn)

    for table in ["ai_shadow_decisions", "ai_shadow_filter_decisions", "shadow_decisions", "decisions"]:
        if table in tables:
            return table

    if not tables:
        return ensure_default_table(conn)

    scored: list[tuple[int, int, str]] = []

    for table in tables:
        cols = set(table_columns(conn, table))
        row_count = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])

        score = 0
        if "trade_id" in cols:
            score += 10
        if "logged_at_utc" in cols:
            score += 4
        if any(c in cols for c in ["decision", "ai_decision", "shadow_decision"]):
            score += 5
        if any(c in cols for c in ["probability", "win_probability", "score", "ai_score"]):
            score += 3

        scored.append((score, row_count, table))

    scored.sort(reverse=True)

    if scored and scored[0][0] > 0:
        return scored[0][2]

    return ensure_default_table(conn)


def existing_trade_ids(conn: sqlite3.Connection, table: str) -> set[str]:
    cols = set(table_columns(conn, table))
    if "trade_id" not in cols:
        return set()

    rows = conn.execute(f'SELECT trade_id FROM "{table}" WHERE trade_id IS NOT NULL').fetchall()
    return {clean_text(r[0]) for r in rows if clean_text(r[0])}


def predict_probabilities(estimator: object, x: pd.DataFrame) -> np.ndarray:
    if hasattr(estimator, "predict_proba"):
        arr = np.asarray(estimator.predict_proba(x))
        if arr.ndim == 2 and arr.shape[1] >= 2:
            return arr[:, 1].astype(float)
        if arr.ndim == 1:
            return arr.astype(float)

    if hasattr(estimator, "decision_function"):
        raw = np.asarray(estimator.decision_function(x)).astype(float)
        return 1.0 / (1.0 + np.exp(-raw))

    return np.asarray(estimator.predict(x)).astype(float)


def build_base_record(row: pd.Series, probability: float, threshold: float, model_path: str, now: str) -> dict[str, Any]:
    decision = "AI_ACCEPT" if probability >= threshold else "AI_REJECT"

    open_time = pd.to_datetime(row.get("open_time_utc"), errors="coerce", utc=True)
    open_time_text = open_time.isoformat() if pd.notna(open_time) else ""

    reported_pnl = pd.to_numeric(pd.Series([row.get("reported_pnl_usdt")]), errors="coerce").iloc[0]
    if pd.isna(reported_pnl):
        reported_pnl = np.nan

    return {
        "trade_id": clean_text(row.get("trade_id")),
        "logged_at_utc": now,
        "created_at_utc": now,
        "updated_at_utc": now,
        "trade_time": open_time_text,
        "open_time_utc": open_time_text,
        "timestamp": open_time_text,
        "symbol": clean_text(row.get("symbol")),
        "pair": clean_text(row.get("symbol")),
        "side": clean_text(row.get("side")),
        "target_win": int(pd.to_numeric(pd.Series([row.get("target_win")]), errors="coerce").fillna(0).iloc[0]),
        "reported_pnl_usdt": reported_pnl,
        "pnl_usdt": reported_pnl,
        "pnl": reported_pnl,
        "return_pct": pd.to_numeric(pd.Series([row.get("return_pct")]), errors="coerce").iloc[0] if "return_pct" in row.index else np.nan,
        "probability": float(probability),
        "win_probability": float(probability),
        "score": float(probability),
        "ai_score": float(probability),
        "model_score": float(probability),
        "model_threshold": float(threshold),
        "threshold": float(threshold),
        "decision": decision,
        "ai_decision": decision,
        "shadow_decision": decision,
        "model_path": model_path,
        "model_name": Path(model_path).name,
        "source_file": clean_text(row.get("source_file")),
    }


def default_value_for_column(col: dict[str, Any], base: dict[str, Any], now: str) -> Any:
    name = col["name"]
    lower = name.lower()
    typ = col["type"].upper()

    if name in base:
        value = base[name]
        if isinstance(value, float) and np.isnan(value):
            return None
        return value

    if lower.endswith("_utc") or lower.endswith("_at") or "time" in lower or "timestamp" in lower:
        return now

    if "decision" in lower:
        return base.get("decision", "")

    if "prob" in lower or "score" in lower or "threshold" in lower:
        return base.get("probability", 0.0)

    if "symbol" in lower or "pair" in lower:
        return base.get("symbol", "")

    if "side" in lower:
        return base.get("side", "")

    if "trade_id" in lower:
        return base.get("trade_id", "")

    if "INT" in typ:
        return 0

    if "REAL" in typ or "FLOA" in typ or "DOUB" in typ or "NUM" in typ:
        return 0.0

    return ""


def insert_records(conn: sqlite3.Connection, table: str, records: list[dict[str, Any]]) -> int:
    info = table_info(conn, table)
    cols = [c["name"] for c in info]

    if "trade_id" not in cols:
        raise RuntimeError(f"Tabela {table} n├úo possui coluna trade_id.")

    now = datetime.now(timezone.utc).isoformat()
    insert_cols: list[str] = []

    for c in info:
        name = c["name"]

        if c["pk"] and "INT" in c["type"].upper() and name not in records[0]:
            continue

        if name in records[0]:
            insert_cols.append(name)
        elif c["notnull"] and c["default"] is None and not c["pk"]:
            insert_cols.append(name)

    placeholders = ",".join(["?"] * len(insert_cols))
    quoted = ",".join([f'"{c}"' for c in insert_cols])
    sql = f'INSERT OR IGNORE INTO "{table}" ({quoted}) VALUES ({placeholders})'

    values = []
    for record in records:
        row_values = []
        for col_name in insert_cols:
            col_meta = next(c for c in info if c["name"] == col_name)
            row_values.append(default_value_for_column(col_meta, record, now))
        values.append(row_values)

    before = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    conn.executemany(sql, values)
    conn.commit()
    after = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])

    return after - before


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--sqlite", default=DEFAULT_SQLITE)
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--summary-json", default=DEFAULT_SUMMARY)
    parser.add_argument("--new-decisions-csv", default=DEFAULT_NEW_DECISIONS)
    args = parser.parse_args()

    input_path = Path(args.input)
    model_path = Path(args.model)
    sqlite_path = Path(args.sqlite)
    summary_path = Path(args.summary_json)
    new_decisions_path = Path(args.new_decisions_csv)

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    new_decisions_path.parent.mkdir(parents=True, exist_ok=True)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    df = pd.read_parquet(input_path)
    bundle = joblib.load(model_path)

    estimator = find_model_object(bundle)
    features = extract_features(bundle)
    threshold = resolve_threshold(bundle, args.threshold)

    if not features:
        raise RuntimeError("N├úo foi poss├¡vel extrair feature_columns do modelo.")

    missing = [f for f in features if f not in df.columns]
    if missing:
        raise RuntimeError(f"Dataset sem features do modelo: {missing}")

    x = df[features].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(x.to_numpy()).all():
        raise RuntimeError("Dataset cont├®m valores n├úo finitos nas features do modelo.")

    with sqlite3.connect(sqlite_path) as conn:
        table = discover_table(conn)
        existing = existing_trade_ids(conn, table)

        new_df = df.loc[~df["trade_id"].astype(str).isin(existing)].copy()
        existing_rows_for_model_threshold = len(existing)

        if new_df.empty:
            new_decisions_path.write_text("", encoding="utf-8")
            inserted = 0
            records_df = pd.DataFrame()
        else:
            proba = predict_probabilities(estimator, new_df[features].apply(pd.to_numeric, errors="coerce"))
            now = datetime.now(timezone.utc).isoformat()
            records = [
                build_base_record(row, float(prob), threshold, str(model_path), now)
                for (_, row), prob in zip(new_df.iterrows(), proba)
            ]
            inserted = insert_records(conn, table, records)
            records_df = pd.DataFrame(records)
            records_df.to_csv(new_decisions_path, index=False, encoding="utf-8-sig")

    summary = {
        "status": "ok",
        "mode": "incremental_daily_shadow_logger",
        "input": str(input_path),
        "model": str(model_path),
        "sqlite": str(sqlite_path),
        "sqlite_table": table,
        "total_input_rows": int(len(df)),
        "existing_rows_for_model_threshold": int(existing_rows_for_model_threshold),
        "new_rows_scored": int(len(new_df)),
        "inserted": int(inserted),
        "skipped_duplicate": int(len(new_df) - inserted),
        "decision_counts_new": records_df["decision"].value_counts(dropna=False).to_dict() if not records_df.empty else {},
        "message": "Pontua├º├úo incremental conclu├¡da." if len(new_df) else "Nenhum trade novo para pontuar.",
        "safety": {
            "sends_orders": False,
            "changes_risk": False,
            "allowed_action": "score_and_log_only",
        },
        "outputs": {
            "summary_json": str(summary_path),
            "new_decisions_csv": str(new_decisions_path),
        },
    }

    summary_path.write_text(json.dumps(json_safe(summary), indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(json_safe(summary), indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
