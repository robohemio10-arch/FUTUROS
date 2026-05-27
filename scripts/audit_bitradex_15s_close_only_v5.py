from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(".")
V4_DIR = ROOT / "data" / "reports" / "binance_bitradex_15s_complete_minutes_v4"
OUT_DIR = ROOT / "data" / "reports" / "binance_bitradex_15s_close_only_v5"

SYMBOLS = ["BTCUSDT", "ETHUSDT"]


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
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if obj is pd.NaT:
        return None
    return obj


def load_v4_comparison(symbol: str, v4_dir: Path = V4_DIR) -> pd.DataFrame:
    path = v4_dir / f"{symbol}_complete15s_agg1m_vs_binance1m.csv"

    if not path.exists():
        raise FileNotFoundError(f"Arquivo V4 não encontrado: {path}")

    df = pd.read_csv(path)

    required = [
        "timestamp",
        "binance_close",
        "bitradex_close",
    ]

    missing = [col for col in required if col not in df.columns]

    if missing:
        raise RuntimeError(f"Colunas obrigatórias ausentes em {path}: {missing}")

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    df["binance_close"] = pd.to_numeric(df["binance_close"], errors="coerce")
    df["bitradex_close"] = pd.to_numeric(df["bitradex_close"], errors="coerce")

    df = df.dropna(subset=["timestamp", "binance_close", "bitradex_close"]).copy()
    df = df.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)

    df["close_diff_abs"] = (df["bitradex_close"] - df["binance_close"]).abs()
    df["close_diff_bps"] = df["close_diff_abs"] / df["binance_close"] * 10000.0

    df["binance_ret_1m"] = df["binance_close"].pct_change()
    df["bitradex_15s_agg_ret_1m"] = df["bitradex_close"].pct_change()

    df["ret_diff_abs"] = (df["bitradex_15s_agg_ret_1m"] - df["binance_ret_1m"]).abs()

    df["binance_ret_sign"] = np.sign(df["binance_ret_1m"])
    df["bitradex_ret_sign"] = np.sign(df["bitradex_15s_agg_ret_1m"])

    valid_direction = (
        df["binance_ret_sign"].ne(0)
        & df["bitradex_ret_sign"].ne(0)
        & df["binance_ret_sign"].notna()
        & df["bitradex_ret_sign"].notna()
    )

    df["ret_direction_match"] = np.where(
        valid_direction,
        df["binance_ret_sign"].eq(df["bitradex_ret_sign"]),
        np.nan,
    )

    return df


def corr_safe(a: pd.Series, b: pd.Series) -> float | None:
    pair = pd.concat([a, b], axis=1).replace([np.inf, -np.inf], np.nan).dropna()

    if len(pair) < 3:
        return None

    if pair.iloc[:, 0].nunique() < 2 or pair.iloc[:, 1].nunique() < 2:
        return None

    return float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))


def audit_symbol(
    symbol: str,
    min_rows: int,
    close_tolerance_bps: float,
    p95_threshold_bps: float,
    max_threshold_bps: float,
    min_close_compatible_ratio: float,
    v4_dir: Path = V4_DIR,
) -> tuple[pd.DataFrame, dict]:
    df = load_v4_comparison(symbol, v4_dir=v4_dir)

    df["close_compatible"] = df["close_diff_bps"] <= close_tolerance_bps

    close_compatible_ratio = float(df["close_compatible"].mean()) if len(df) else 0.0

    p95 = float(df["close_diff_bps"].quantile(0.95)) if len(df) else None
    max_diff = float(df["close_diff_bps"].max()) if len(df) else None

    if len(df) < min_rows:
        status = "needs_more_rows"
    elif (
        p95 is not None
        and max_diff is not None
        and p95 <= p95_threshold_bps
        and max_diff <= max_threshold_bps
        and close_compatible_ratio >= min_close_compatible_ratio
    ):
        status = "approved_close_only_shadow"
    elif (
        p95 is not None
        and p95 <= p95_threshold_bps
        and close_compatible_ratio >= 0.90
    ):
        status = "warning_close_only_shadow"
    else:
        status = "rejected_close_only"

    report = {
        "symbol": symbol,
        "status": status,
        "policy": "close_only_microstructure_shadow",
        "rows": int(len(df)),
        "min_rows_required": int(min_rows),
        "first_ts": df["timestamp"].min().isoformat() if len(df) else None,
        "last_ts": df["timestamp"].max().isoformat() if len(df) else None,
        "close_tolerance_bps": float(close_tolerance_bps),
        "close_compatible_rows": int(df["close_compatible"].sum()) if len(df) else 0,
        "close_compatible_ratio": close_compatible_ratio,
        "close_diff_bps": {
            "mean": float(df["close_diff_bps"].mean()) if len(df) else None,
            "median": float(df["close_diff_bps"].median()) if len(df) else None,
            "p95": p95,
            "p99": float(df["close_diff_bps"].quantile(0.99)) if len(df) else None,
            "max": max_diff,
        },
        "correlations": {
            "close_corr": corr_safe(df["binance_close"], df["bitradex_close"]),
            "ret_1m_corr": corr_safe(df["binance_ret_1m"], df["bitradex_15s_agg_ret_1m"]),
        },
        "return_direction_match_rate": (
            float(pd.Series(df["ret_direction_match"]).dropna().mean())
            if df["ret_direction_match"].notna().any()
            else None
        ),
        "feature_permission": {
            "allow_close": True,
            "allow_close_returns": True,
            "allow_micro_momentum": True,
            "allow_micro_volatility_from_close": True,
            "allow_high_low_range": False,
            "allow_wicks": False,
            "allow_ohlc_candle_patterns": False,
            "allow_live_execution": False,
            "allow_shadow_only": status in ["approved_close_only_shadow", "warning_close_only_shadow"],
        },
    }

    return df, report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--v4-dir", type=str, default=str(V4_DIR))
    parser.add_argument("--out-dir", type=str, default=str(OUT_DIR))
    parser.add_argument("--min-rows", type=int, default=300)
    parser.add_argument("--close-tolerance-bps", type=float, default=10.0)
    parser.add_argument("--p95-threshold-bps", type=float, default=10.0)
    parser.add_argument("--max-threshold-bps", type=float, default=20.0)
    parser.add_argument("--min-close-compatible-ratio", type=float, default=0.98)
    args = parser.parse_args(argv)

    v4_dir = Path(args.v4_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "status": "ok",
        "mode": "binance_bitradex_15s_close_only_v5",
        "safety": {
            "audit_only": True,
            "sends_orders": False,
            "changes_risk": False,
            "live_trading": False,
            "shadow_only": True,
        },
        "parameters": vars(args),
        "symbols": {},
        "outputs": {},
    }

    for symbol in args.symbols:
        df, report = audit_symbol(
            symbol=symbol,
            min_rows=args.min_rows,
            close_tolerance_bps=args.close_tolerance_bps,
            p95_threshold_bps=args.p95_threshold_bps,
            max_threshold_bps=args.max_threshold_bps,
            min_close_compatible_ratio=args.min_close_compatible_ratio,
            v4_dir=v4_dir,
        )

        full_path = out_dir / f"{symbol}_close_only_comparison.csv"
        anomalies_path = out_dir / f"{symbol}_close_only_anomalies.csv"

        df.to_csv(full_path, index=False, encoding="utf-8-sig")
        df[~df["close_compatible"]].to_csv(anomalies_path, index=False, encoding="utf-8-sig")

        summary["symbols"][symbol] = report
        summary["outputs"][symbol] = {
            "comparison_csv": str(full_path),
            "anomalies_csv": str(anomalies_path),
        }

    final_statuses = [
        summary["symbols"][symbol]["status"]
        for symbol in args.symbols
        if symbol in summary["symbols"]
    ]

    if all(status == "approved_close_only_shadow" for status in final_statuses):
        summary["final_verdict"] = "approved_close_only_shadow_for_all_symbols"
    elif any(status in ["approved_close_only_shadow", "warning_close_only_shadow"] for status in final_statuses):
        summary["final_verdict"] = "partial_close_only_shadow_permission"
    else:
        summary["final_verdict"] = "not_approved"

    summary_path = out_dir / "summary.json"
    summary_path.write_text(
        json.dumps(json_safe(summary), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(json.dumps(json_safe(summary), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
