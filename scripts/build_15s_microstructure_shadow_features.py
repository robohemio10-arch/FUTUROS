from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(".")
V4_DIR = ROOT / "data" / "reports" / "binance_bitradex_15s_complete_minutes_v4"
V5_SUMMARY = ROOT / "data" / "reports" / "binance_bitradex_15s_close_only_v5" / "summary.json"

FEATURE_DIR = ROOT / "data" / "features"
REPORT_DIR = ROOT / "data" / "reports"

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


def require_v5_approved(symbols: list[str], force: bool, v5_summary: Path = V5_SUMMARY) -> dict:
    if not v5_summary.exists():
        if force:
            return {"status": "missing_v5_summary_force_enabled", "path": str(v5_summary)}
        raise FileNotFoundError(f"V5 summary não encontrado: {v5_summary}")

    summary = json.loads(v5_summary.read_text(encoding="utf-8"))

    final_verdict = summary.get("final_verdict")
    symbol_status = {
        symbol: summary.get("symbols", {}).get(symbol, {}).get("status")
        for symbol in symbols
    }

    approved = (
        final_verdict == "approved_close_only_shadow_for_all_symbols"
        and all(status == "approved_close_only_shadow" for status in symbol_status.values())
    )

    if not approved and not force:
        raise RuntimeError(
            "V5 não está aprovado para todos os símbolos. "
            f"final_verdict={final_verdict}, symbol_status={symbol_status}. "
            "Use --force somente para diagnóstico."
        )

    return {
        "status": "approved" if approved else "not_approved_force_enabled",
        "final_verdict": final_verdict,
        "symbol_status": symbol_status,
        "path": str(v5_summary),
    }


def read_clean_15s(symbol: str, start_utc: str | None, v4_dir: Path = V4_DIR) -> pd.DataFrame:
    path = v4_dir / f"{symbol}_bitradex_15s_clean_window.csv"

    if not path.exists():
        raise FileNotFoundError(f"Arquivo clean 15s do V4 não encontrado: {path}")

    df = pd.read_csv(path)

    required = ["timestamp", "captured_at", "close"]
    missing = [col for col in required if col not in df.columns]

    if missing:
        raise RuntimeError(f"Colunas obrigatórias ausentes em {path}: {missing}")

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    df["captured_at"] = pd.to_datetime(df["captured_at"], errors="coerce", utc=True)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")

    df = df.dropna(subset=["timestamp", "captured_at", "close"]).copy()
    df = df[df["close"] > 0].copy()

    if start_utc:
        start_ts = pd.to_datetime(start_utc, errors="coerce", utc=True)
        if pd.isna(start_ts):
            raise RuntimeError(f"start_utc inválido: {start_utc}")
        df = df[df["timestamp"] >= start_ts].copy()

    df["symbol"] = symbol
    df = df.sort_values(["timestamp", "captured_at"])
    df = df.drop_duplicates("timestamp", keep="last").reset_index(drop=True)

    return df


def signed_direction_run(sign: pd.Series) -> pd.Series:
    values = sign.fillna(0).astype(int).to_numpy()

    out = []
    previous = 0
    run = 0

    for value in values:
        if value == 0:
            previous = 0
            run = 0
            out.append(0)
            continue

        if value == previous:
            run += 1
        else:
            run = 1
            previous = value

        out.append(run * value)

    return pd.Series(out, index=sign.index, dtype="int64")


def add_15s_features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy().sort_values("timestamp").reset_index(drop=True)

    x["micro15s_close"] = x["close"]

    x["micro15s_ret_15s"] = x["close"].pct_change(1)
    x["micro15s_ret_30s"] = x["close"].pct_change(2)
    x["micro15s_ret_45s"] = x["close"].pct_change(3)
    x["micro15s_ret_60s"] = x["close"].pct_change(4)
    x["micro15s_ret_120s"] = x["close"].pct_change(8)
    x["micro15s_ret_300s"] = x["close"].pct_change(20)

    x["micro15s_logret_15s"] = np.log(x["close"] / x["close"].shift(1))
    x["micro15s_logret_60s"] = np.log(x["close"] / x["close"].shift(4))
    x["micro15s_logret_300s"] = np.log(x["close"] / x["close"].shift(20))

    ret = x["micro15s_ret_15s"]

    x["micro15s_vol_ret_60s"] = ret.rolling(4, min_periods=4).std()
    x["micro15s_vol_ret_120s"] = ret.rolling(8, min_periods=6).std()
    x["micro15s_vol_ret_300s"] = ret.rolling(20, min_periods=12).std()
    x["micro15s_vol_ret_900s"] = ret.rolling(60, min_periods=30).std()

    x["micro15s_absret_sum_60s"] = ret.abs().rolling(4, min_periods=4).sum()
    x["micro15s_absret_sum_300s"] = ret.abs().rolling(20, min_periods=12).sum()
    x["micro15s_absret_sum_900s"] = ret.abs().rolling(60, min_periods=30).sum()

    x["micro15s_ema_60s"] = x["close"].ewm(span=4, adjust=False).mean()
    x["micro15s_ema_120s"] = x["close"].ewm(span=8, adjust=False).mean()
    x["micro15s_ema_300s"] = x["close"].ewm(span=20, adjust=False).mean()
    x["micro15s_ema_900s"] = x["close"].ewm(span=60, adjust=False).mean()

    x["micro15s_dist_ema_60s"] = (x["close"] / x["micro15s_ema_60s"]) - 1.0
    x["micro15s_dist_ema_120s"] = (x["close"] / x["micro15s_ema_120s"]) - 1.0
    x["micro15s_dist_ema_300s"] = (x["close"] / x["micro15s_ema_300s"]) - 1.0
    x["micro15s_dist_ema_900s"] = (x["close"] / x["micro15s_ema_900s"]) - 1.0

    x["micro15s_ema_60s_slope_60s"] = x["micro15s_ema_60s"].pct_change(4)
    x["micro15s_ema_300s_slope_300s"] = x["micro15s_ema_300s"].pct_change(20)

    x["micro15s_ret_60s_z_300s"] = (
        x["micro15s_ret_60s"]
        / x["micro15s_ret_15s"].rolling(20, min_periods=12).std()
    )

    x["micro15s_ret_300s_z_900s"] = (
        x["micro15s_ret_300s"]
        / x["micro15s_ret_15s"].rolling(60, min_periods=30).std()
    )

    sign = np.sign(x["micro15s_ret_15s"]).replace([np.inf, -np.inf], np.nan)
    x["micro15s_direction_run_signed"] = signed_direction_run(sign)

    x["micro15s_momentum_accel_15s"] = x["micro15s_ret_15s"] - x["micro15s_ret_15s"].shift(1)
    x["micro15s_momentum_accel_60s"] = x["micro15s_ret_60s"] - x["micro15s_ret_60s"].shift(4)

    x["feature_source"] = "bitradex_15s_close_only_shadow"
    x["feature_policy"] = "close_returns_micro_momentum_only"
    x["allow_live_execution"] = False
    x["allow_shadow_only"] = True

    return x


def aggregate_to_causal_minute(
    df15: pd.DataFrame,
    min_15s_per_minute: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    x = df15.copy()
    x["feature_minute_utc"] = x["timestamp"].dt.floor("min")

    grouped = x.groupby(["symbol", "feature_minute_utc"], dropna=False)

    counts = grouped.size().rename("micro15s_subcandles_in_minute").reset_index()

    last_rows = grouped.tail(1).copy()
    last_rows = last_rows.merge(counts, on=["symbol", "feature_minute_utc"], how="left")

    first_close = grouped["close"].first().rename("micro15s_first_close_in_minute").reset_index()
    last_rows = last_rows.merge(first_close, on=["symbol", "feature_minute_utc"], how="left")

    last_rows["micro15s_intra_minute_ret_close_only"] = (
        last_rows["close"] / last_rows["micro15s_first_close_in_minute"] - 1.0
    )

    complete = last_rows[last_rows["micro15s_subcandles_in_minute"] >= min_15s_per_minute].copy()
    incomplete = last_rows[last_rows["micro15s_subcandles_in_minute"] < min_15s_per_minute].copy()

    complete["join_time_utc"] = complete["feature_minute_utc"] + pd.Timedelta(minutes=1)
    complete["usable_from_utc"] = complete["join_time_utc"]

    keep_cols = [
        "symbol",
        "feature_minute_utc",
        "join_time_utc",
        "usable_from_utc",
        "timestamp",
        "captured_at",
        "micro15s_subcandles_in_minute",
        "micro15s_close",
        "micro15s_intra_minute_ret_close_only",
        "micro15s_ret_15s",
        "micro15s_ret_30s",
        "micro15s_ret_45s",
        "micro15s_ret_60s",
        "micro15s_ret_120s",
        "micro15s_ret_300s",
        "micro15s_logret_15s",
        "micro15s_logret_60s",
        "micro15s_logret_300s",
        "micro15s_vol_ret_60s",
        "micro15s_vol_ret_120s",
        "micro15s_vol_ret_300s",
        "micro15s_vol_ret_900s",
        "micro15s_absret_sum_60s",
        "micro15s_absret_sum_300s",
        "micro15s_absret_sum_900s",
        "micro15s_dist_ema_60s",
        "micro15s_dist_ema_120s",
        "micro15s_dist_ema_300s",
        "micro15s_dist_ema_900s",
        "micro15s_ema_60s_slope_60s",
        "micro15s_ema_300s_slope_300s",
        "micro15s_ret_60s_z_300s",
        "micro15s_ret_300s_z_900s",
        "micro15s_direction_run_signed",
        "micro15s_momentum_accel_15s",
        "micro15s_momentum_accel_60s",
        "feature_source",
        "feature_policy",
        "allow_live_execution",
        "allow_shadow_only",
    ]

    complete = complete[keep_cols].copy()

    return complete.reset_index(drop=True), incomplete.reset_index(drop=True)


def feature_manifest(df: pd.DataFrame) -> pd.DataFrame:
    rows = []

    blocked_tokens = ["high", "low", "range", "wick", "ohlc"]

    for col in df.columns:
        if col in [
            "symbol",
            "feature_minute_utc",
            "join_time_utc",
            "usable_from_utc",
            "timestamp",
            "captured_at",
            "feature_source",
            "feature_policy",
            "allow_live_execution",
            "allow_shadow_only",
        ]:
            kind = "metadata"
        elif any(token in col.lower() for token in blocked_tokens):
            kind = "blocked_should_not_exist"
        else:
            kind = "close_only_microstructure"

        rows.append({
            "column": col,
            "kind": kind,
            "allowed_for_shadow": kind in ["metadata", "close_only_microstructure"],
            "allowed_for_live_execution": False,
        })

    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--v4-dir", type=str, default=str(V4_DIR))
    parser.add_argument("--v5-summary", type=str, default=str(V5_SUMMARY))
    parser.add_argument("--feature-dir", type=str, default=str(FEATURE_DIR))
    parser.add_argument("--report-dir", type=str, default=str(REPORT_DIR))
    parser.add_argument("--start-utc", type=str, default="2026-05-26T16:00:00Z")
    parser.add_argument("--min-15s-per-minute", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    v4_dir = Path(args.v4_dir)
    feature_dir = Path(args.feature_dir)
    report_dir = Path(args.report_dir)
    feature_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    v5_gate = require_v5_approved(args.symbols, force=args.force, v5_summary=Path(args.v5_summary))

    complete_frames = []
    incomplete_frames = []
    source_rows = {}

    for symbol in args.symbols:
        raw = read_clean_15s(symbol=symbol, start_utc=args.start_utc, v4_dir=v4_dir)
        with_features = add_15s_features(raw)

        complete, incomplete = aggregate_to_causal_minute(
            with_features,
            min_15s_per_minute=args.min_15s_per_minute,
        )

        complete_frames.append(complete)
        incomplete_frames.append(incomplete)

        source_rows[symbol] = {
            "clean_15s_rows": int(len(raw)),
            "feature_15s_rows": int(len(with_features)),
            "complete_feature_minutes": int(len(complete)),
            "incomplete_feature_minutes": int(len(incomplete)),
            "first_feature_minute": complete["feature_minute_utc"].min().isoformat() if len(complete) else None,
            "last_feature_minute": complete["feature_minute_utc"].max().isoformat() if len(complete) else None,
        }

    features = pd.concat(complete_frames, ignore_index=True) if complete_frames else pd.DataFrame()
    incomplete_all = pd.concat(incomplete_frames, ignore_index=True) if incomplete_frames else pd.DataFrame()

    features = features.replace([np.inf, -np.inf], np.nan)
    features = features.sort_values(["symbol", "join_time_utc"]).reset_index(drop=True)

    manifest = feature_manifest(features)

    forbidden_manifest = manifest[manifest["kind"] == "blocked_should_not_exist"]

    if len(forbidden_manifest):
        raise RuntimeError(
            "Feature bloqueada encontrada no dataset close-only: "
            f"{forbidden_manifest['column'].tolist()}"
        )

    output_parquet = feature_dir / "bitradex_15s_microstructure_shadow_features.parquet"
    output_csv = feature_dir / "bitradex_15s_microstructure_shadow_features.csv"
    incomplete_csv = report_dir / "bitradex_15s_microstructure_shadow_incomplete_minutes.csv"
    manifest_csv = report_dir / "bitradex_15s_microstructure_shadow_feature_manifest.csv"
    summary_json = report_dir / "bitradex_15s_microstructure_shadow_features_summary.json"

    features.to_csv(output_csv, index=False, encoding="utf-8-sig")

    parquet_written = False
    try:
        features.to_parquet(output_parquet, index=False)
        parquet_written = True
    except Exception:
        parquet_written = False

    incomplete_all.to_csv(incomplete_csv, index=False, encoding="utf-8-sig")
    manifest.to_csv(manifest_csv, index=False, encoding="utf-8-sig")

    summary = {
        "status": "ok",
        "mode": "build_15s_microstructure_shadow_features",
        "safety": {
            "sends_orders": False,
            "changes_risk": False,
            "live_trading": False,
            "shadow_only": True,
            "official_ai_dataset_modified": False,
        },
        "v5_gate": v5_gate,
        "parameters": vars(args),
        "symbols": source_rows,
        "dataset": {
            "rows": int(len(features)),
            "columns": int(len(features.columns)),
            "symbols": features["symbol"].value_counts().to_dict() if len(features) else {},
            "first_join_time_utc": features["join_time_utc"].min().isoformat() if len(features) else None,
            "last_join_time_utc": features["join_time_utc"].max().isoformat() if len(features) else None,
            "parquet_written": parquet_written,
        },
        "policy": {
            "allowed": [
                "close",
                "close_returns",
                "micro_momentum",
                "micro_volatility_from_close",
                "causal_join_time_shift_plus_1m",
            ],
            "blocked": [
                "high",
                "low",
                "range",
                "wicks",
                "ohlc_candle_patterns",
                "live_execution",
                "risk_change",
            ],
        },
        "outputs": {
            "features_parquet": str(output_parquet) if parquet_written else None,
            "features_csv": str(output_csv),
            "incomplete_minutes_csv": str(incomplete_csv),
            "manifest_csv": str(manifest_csv),
            "summary_json": str(summary_json),
        },
    }

    summary_json.write_text(
        json.dumps(json_safe(summary), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(json.dumps(json_safe(summary), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
