from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.market.market_feature_schema import lookahead_columns  # noqa: E402


DEFAULT_FEEDBACK_PATH = Path("data/feedback/paper_closed_trades_incremental.parquet")
DEFAULT_FEATURES_PATH = Path("data/features/market_features_60d.parquet")
DEFAULT_OUTPUT_PATH = Path("data/features/incremental_training_microbatch.parquet")
DEFAULT_REPORT_PATH = Path("data/reports/incremental_training_microbatch_report.json")

FEEDBACK_REQUIRED_COLUMNS = [
    "moeda",
    "fechar_side",
    "horario_abertura",
    "horario_fechamento",
    "pnl_fechado",
    "taxa_lucros_perdas_fechados_pct",
]
FEATURE_SYMBOL_COLUMNS = ("symbol", "moeda", "pair")
FEATURE_TIMESTAMP_COLUMNS = ("ts", "timestamp", "open_time_utc", "open_time", "date", "datetime", "open_1m_ts")
FEATURE_EXCLUDED_COLUMNS = {
    "symbol",
    "moeda",
    "pair",
    "tf",
    "timeframe",
    "ts",
    "timestamp",
    "open_time_utc",
    "open_time",
    "date",
    "datetime",
    "open_1m_ts",
    "ts_ms",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safety_payload() -> dict[str, Any]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "runtime_mode": "paper",
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
    }


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def normalize_symbol(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().upper()
    return text.replace("/USDT:USDT", "USDT").replace("/", "").replace(":", "").replace("_", "")


def normalize_side(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().lower()
    if "short" in text or "sell" in text:
        return "short"
    if "long" in text or "buy" in text:
        return "long"
    return text


def parse_number_series(series: pd.Series) -> pd.Series:
    def normalize(value: object) -> str:
        if pd.isna(value):
            return ""
        text = str(value).strip().replace("R$", "").replace("%", "").replace(" ", "")
        if "," in text:
            return text.replace(".", "").replace(",", ".")
        return text

    return pd.to_numeric(series.map(normalize), errors="coerce")


def read_frame(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"unsupported_extension:{path.suffix}")


def first_present(columns: pd.Index, candidates: tuple[str, ...]) -> str | None:
    existing = {str(column).lower(): str(column) for column in columns}
    for candidate in candidates:
        if candidate.lower() in existing:
            return existing[candidate.lower()]
    return None


def blocking_report(
    *,
    reason: str,
    feedback_path: Path,
    features_path: Path,
    output_path: Path,
    report_path: Path,
    feedback_rows: int = 0,
    features_rows: int = 0,
    invalid_feedback_rows: int = 0,
    lookahead: list[str] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "status": "blocked",
        "reason": reason,
        "feedback_path": str(feedback_path),
        "features_path": str(features_path),
        "output_path": str(output_path),
        "report_path": str(report_path),
        "feedback_rows": int(feedback_rows),
        "features_rows": int(features_rows),
        "output_rows": 0,
        "missing_feature_rows": 0,
        "invalid_feedback_rows": int(invalid_feedback_rows),
        "lookahead_columns": sorted(lookahead or []),
        "lookahead_columns_count": len(lookahead or []),
        "min_open_time_utc": None,
        "max_open_time_utc": None,
        "min_feature_timestamp_utc": None,
        "max_feature_timestamp_utc": None,
        "max_feature_age_seconds": None,
        "symbols": [],
        "sides": [],
        "blocking_errors": errors or [],
        "write_performed": False,
        "built_at_utc": utc_now(),
        **safety_payload(),
    }
    write_json(report_path, payload)
    return payload


def prepare_feedback(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    feedback = frame.copy()
    if "is_open" in feedback.columns:
        is_open = pd.to_numeric(feedback["is_open"], errors="coerce").fillna(0).astype(int)
        feedback = feedback.loc[is_open.eq(0)].copy()
    open_time = pd.to_datetime(feedback["horario_abertura"], utc=True, errors="coerce")
    close_time = pd.to_datetime(feedback["horario_fechamento"], utc=True, errors="coerce")
    pnl = parse_number_series(feedback["pnl_fechado"])
    target_return = parse_number_series(feedback["taxa_lucros_perdas_fechados_pct"])
    valid = open_time.notna() & close_time.notna() & pnl.notna() & (close_time >= open_time)
    invalid_rows = int((~valid).sum())

    prepared = feedback.loc[valid].copy()
    prepared["symbol"] = prepared["moeda"].map(normalize_symbol)
    prepared["side"] = prepared["fechar_side"].map(normalize_side)
    prepared["open_time_utc"] = open_time.loc[valid]
    prepared["close_time_utc"] = close_time.loc[valid]
    prepared["pnl_fechado"] = pnl.loc[valid].astype(float)
    prepared["target_return"] = target_return.loc[valid].astype(float)
    prepared["target_profitable"] = (prepared["pnl_fechado"] > 0).astype(int)
    prepared = prepared.loc[prepared["symbol"].ne("") & prepared["side"].ne("")].copy()
    return prepared, invalid_rows + int(valid.sum() - len(prepared))


def prepare_features(frame: pd.DataFrame) -> tuple[pd.DataFrame, str | None, str | None, list[str]]:
    symbol_column = first_present(frame.columns, FEATURE_SYMBOL_COLUMNS)
    timestamp_column = first_present(frame.columns, FEATURE_TIMESTAMP_COLUMNS)
    if symbol_column is None or timestamp_column is None:
        return pd.DataFrame(), symbol_column, timestamp_column, []

    features = frame.copy()
    features["symbol"] = features[symbol_column].map(normalize_symbol)
    features["feature_timestamp_utc"] = pd.to_datetime(features[timestamp_column], utc=True, errors="coerce")
    features = features.loc[features["symbol"].ne("") & features["feature_timestamp_utc"].notna()].copy()
    numeric_columns = []
    for column in features.columns:
        name = str(column)
        if name in FEATURE_EXCLUDED_COLUMNS or name in {"symbol", "feature_timestamp_utc"}:
            continue
        if name.startswith("target_") or name.startswith("future_ret_"):
            continue
        numeric = pd.to_numeric(features[column], errors="coerce")
        if numeric.notna().any():
            feature_name = f"feature_{name}" if not name.startswith("feature_") else name
            features[feature_name] = numeric.astype(float)
            numeric_columns.append(feature_name)
    return features[["symbol", "feature_timestamp_utc", *numeric_columns]].copy(), symbol_column, timestamp_column, numeric_columns


def temporal_join(feedback: pd.DataFrame, features: pd.DataFrame, numeric_columns: list[str]) -> tuple[pd.DataFrame, int]:
    outputs = []
    missing = 0
    for _, trade in feedback.sort_values(["symbol", "open_time_utc"]).iterrows():
        candidates = features.loc[
            features["symbol"].eq(trade["symbol"]) & (features["feature_timestamp_utc"] <= trade["open_time_utc"])
        ]
        if candidates.empty:
            missing += 1
            continue
        feature = candidates.sort_values("feature_timestamp_utc").iloc[-1]
        row = {
            "order_id": str(trade.get("order_id", "") if not pd.isna(trade.get("order_id", "")) else "").strip(),
            "symbol": trade["symbol"],
            "side": trade["side"],
            "open_time_utc": trade["open_time_utc"],
            "close_time_utc": trade["close_time_utc"],
            "pnl_fechado": float(trade["pnl_fechado"]),
            "target_return": float(trade["target_return"]) if not pd.isna(trade["target_return"]) else None,
            "target_profitable": int(trade["target_profitable"]),
            "feature_timestamp_utc": feature["feature_timestamp_utc"],
            "feature_age_seconds": float((trade["open_time_utc"] - feature["feature_timestamp_utc"]).total_seconds()),
        }
        for column in numeric_columns:
            row[column] = feature[column]
        outputs.append(row)
    return pd.DataFrame(outputs), missing


def build_record_hash(row: pd.Series) -> str:
    material = json.dumps({str(key): str(value) for key, value in row.items()}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_microbatch(
    *,
    feedback_path: Path = DEFAULT_FEEDBACK_PATH,
    features_path: Path = DEFAULT_FEATURES_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    report_path: Path = DEFAULT_REPORT_PATH,
    strict: bool = False,
) -> dict[str, Any]:
    if not feedback_path.exists():
        return blocking_report(
            reason="missing_feedback",
            feedback_path=feedback_path,
            features_path=features_path,
            output_path=output_path,
            report_path=report_path,
            errors=[f"feedback_missing:{feedback_path}"],
        )
    if not features_path.exists():
        return blocking_report(
            reason="missing_features",
            feedback_path=feedback_path,
            features_path=features_path,
            output_path=output_path,
            report_path=report_path,
            errors=[f"features_missing:{features_path}"],
        )

    feedback_raw = read_frame(feedback_path)
    features_raw = read_frame(features_path)
    lookahead = lookahead_columns(features_raw)
    if lookahead:
        return blocking_report(
            reason="lookahead_columns_detected",
            feedback_path=feedback_path,
            features_path=features_path,
            output_path=output_path,
            report_path=report_path,
            feedback_rows=len(feedback_raw),
            features_rows=len(features_raw),
            lookahead=lookahead,
            errors=[f"lookahead_columns:{lookahead}"],
        )

    missing_feedback_columns = [column for column in FEEDBACK_REQUIRED_COLUMNS if column not in feedback_raw.columns]
    if missing_feedback_columns:
        return blocking_report(
            reason="invalid_feedback_schema",
            feedback_path=feedback_path,
            features_path=features_path,
            output_path=output_path,
            report_path=report_path,
            feedback_rows=len(feedback_raw),
            features_rows=len(features_raw),
            errors=[f"missing_feedback_columns:{missing_feedback_columns}"],
        )

    feedback, invalid_feedback_rows = prepare_feedback(feedback_raw)
    if strict and invalid_feedback_rows:
        return blocking_report(
            reason="invalid_feedback_rows",
            feedback_path=feedback_path,
            features_path=features_path,
            output_path=output_path,
            report_path=report_path,
            feedback_rows=len(feedback_raw),
            features_rows=len(features_raw),
            invalid_feedback_rows=invalid_feedback_rows,
            errors=[f"invalid_feedback_rows:{invalid_feedback_rows}"],
        )

    features, symbol_column, timestamp_column, numeric_columns = prepare_features(features_raw)
    if symbol_column is None or timestamp_column is None:
        return blocking_report(
            reason="invalid_feature_schema",
            feedback_path=feedback_path,
            features_path=features_path,
            output_path=output_path,
            report_path=report_path,
            feedback_rows=len(feedback_raw),
            features_rows=len(features_raw),
            invalid_feedback_rows=invalid_feedback_rows,
            errors=[f"missing_feature_schema:symbol={symbol_column}:timestamp={timestamp_column}"],
        )

    joined, missing_feature_rows = temporal_join(feedback, features, numeric_columns)
    built_at = utc_now()
    if not joined.empty:
        point_in_time_violations = pd.to_datetime(joined["feature_timestamp_utc"], utc=True, errors="coerce") > pd.to_datetime(
            joined["open_time_utc"],
            utc=True,
            errors="coerce",
        )
        if strict and bool(point_in_time_violations.any()):
            return blocking_report(
                reason="point_in_time_violation",
                feedback_path=feedback_path,
                features_path=features_path,
                output_path=output_path,
                report_path=report_path,
                feedback_rows=len(feedback_raw),
                features_rows=len(features_raw),
                invalid_feedback_rows=invalid_feedback_rows,
                errors=[f"feature_timestamp_after_open_time:{int(point_in_time_violations.sum())}"],
            )
        joined["source_feedback_path"] = str(feedback_path)
        joined["source_features_path"] = str(features_path)
        joined["built_at_utc"] = built_at
        joined["record_hash"] = joined.apply(build_record_hash, axis=1)

    if strict and joined.empty:
        return blocking_report(
            reason="empty_output",
            feedback_path=feedback_path,
            features_path=features_path,
            output_path=output_path,
            report_path=report_path,
            feedback_rows=len(feedback_raw),
            features_rows=len(features_raw),
            invalid_feedback_rows=invalid_feedback_rows,
            errors=["empty_output"],
        )

    ensure_parent(output_path)
    joined.to_parquet(output_path, index=False)

    open_times = pd.to_datetime(joined["open_time_utc"], utc=True, errors="coerce") if "open_time_utc" in joined else pd.Series(dtype="datetime64[ns, UTC]")
    feature_times = pd.to_datetime(joined["feature_timestamp_utc"], utc=True, errors="coerce") if "feature_timestamp_utc" in joined else pd.Series(dtype="datetime64[ns, UTC]")
    report = {
        "status": "ok",
        "reason": "ok" if len(joined) else "empty_output",
        "feedback_path": str(feedback_path),
        "features_path": str(features_path),
        "output_path": str(output_path),
        "report_path": str(report_path),
        "feedback_rows": int(len(feedback_raw)),
        "features_rows": int(len(features_raw)),
        "output_rows": int(len(joined)),
        "missing_feature_rows": int(missing_feature_rows),
        "invalid_feedback_rows": int(invalid_feedback_rows),
        "lookahead_columns": [],
        "lookahead_columns_count": 0,
        "min_open_time_utc": open_times.dropna().min().isoformat() if not open_times.dropna().empty else None,
        "max_open_time_utc": open_times.dropna().max().isoformat() if not open_times.dropna().empty else None,
        "min_feature_timestamp_utc": feature_times.dropna().min().isoformat() if not feature_times.dropna().empty else None,
        "max_feature_timestamp_utc": feature_times.dropna().max().isoformat() if not feature_times.dropna().empty else None,
        "max_feature_age_seconds": float(joined["feature_age_seconds"].max()) if "feature_age_seconds" in joined and len(joined) else None,
        "symbols": sorted(joined["symbol"].dropna().astype(str).unique().tolist()) if "symbol" in joined else [],
        "sides": sorted(joined["side"].dropna().astype(str).unique().tolist()) if "side" in joined else [],
        "feature_symbol_column": symbol_column,
        "feature_timestamp_column": timestamp_column,
        "feature_columns": numeric_columns,
        "write_performed": True,
        "built_at_utc": built_at,
        **safety_payload(),
    }
    write_json(report_path, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build incremental paper training microbatch without training a model.")
    parser.add_argument("--feedback", default=str(DEFAULT_FEEDBACK_PATH))
    parser.add_argument("--features", default=str(DEFAULT_FEATURES_PATH))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--strict", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_microbatch(
        feedback_path=Path(args.feedback),
        features_path=Path(args.features),
        output_path=Path(args.output),
        report_path=Path(args.report),
        strict=bool(args.strict),
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
