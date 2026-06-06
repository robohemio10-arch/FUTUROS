from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd


ROOT = Path(os.getenv("SMARTCRYPTO_PROJECT_ROOT") or Path.cwd()).resolve()

MODEL_PATH = ROOT / "data/models/ai_shadow_filter_extratrees_050.joblib"
TRADE_ENRICHED_PATH = ROOT / "data/features/trade_enriched.parquet"
MARKET_FEATURES_PATH = ROOT / "data/features/market_features_60d.parquet"

OUTPUT_CANDIDATE = ROOT / "data/features/training_dataset_quality_gated_binance_1m_shadow_compatible_candidate_v1.parquet"
OUTPUT_FULL_AUDIT = ROOT / "data/features/training_dataset_quality_gated_binance_1m_shadow_compatible_full_audit_v1.parquet"
SUMMARY_JSON = ROOT / "data/reports/build_quality_gated_shadow_compatible_dataset_v1_summary.json"

OCR_SOURCE = "bitradex_ocr_locked_candidates_20260528_090243"

BASE_COLUMNS = [
    "trade_id",
    "symbol",
    "side",
    "segment",
    "open_time_utc",
    "target_win",
    "pnl_sign_label",
    "reported_pnl_usdt",
]

AUX_COLUMNS = [
    "source_file",
    "order_id",
    "symbol_norm",
    "trade_index",
    "trade_data_quality_status",
    "train_allowed",
    "row_status",
    "is_compatible",
    "is_exact_compatible",
    "quality_reason",
]


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


def normalize_symbol(value: object) -> str:
    text = clean_text(value).upper().replace("/", "").replace("_", "")
    if text in {"BTCUSDT", "ETHUSDT"}:
        return text
    return text


def normalize_side(value: object) -> str:
    text = clean_text(value).lower()
    if "long" in text:
        return "long"
    if "short" in text:
        return "short"
    return "unknown"


def to_num(value: object) -> float:
    if value is None:
        return np.nan
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)

    text = clean_text(value)
    if not text:
        return np.nan

    text = (
        text.replace("USDT", "")
        .replace("BTC", "")
        .replace("ETH", "")
        .replace("$", "")
        .replace(" ", "")
    )

    import re

    text = re.sub(r"[^0-9,.\-+]", "", text)

    if not text:
        return np.nan

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")

    try:
        return float(text)
    except Exception:
        return np.nan


def extract_model_features(model: object) -> list[str]:
    if isinstance(model, dict):
        for key in ["feature_columns", "features", "feature_names", "model_features"]:
            if key in model:
                return [str(x) for x in list(model[key])]

        for key in ["model", "estimator", "pipeline"]:
            if key in model:
                nested = extract_model_features(model[key])
                if nested:
                    return nested

    for attr in ["feature_names_in_", "feature_name_", "feature_names"]:
        if hasattr(model, attr):
            return [str(x) for x in list(getattr(model, attr))]

    if hasattr(model, "steps"):
        for _, step in model.steps:
            nested = extract_model_features(step)
            if nested:
                return nested

    if hasattr(model, "named_steps"):
        for step in model.named_steps.values():
            nested = extract_model_features(step)
            if nested:
                return nested

    return []


def prepare_market_features(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()

    df["symbol_norm"] = df["symbol"].map(normalize_symbol)
    df["tf"] = df["tf"].astype(str).str.lower()
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce", utc=True)

    numeric_cols = ["open", "high", "low", "close", "volume"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df[df["symbol_norm"].isin(["BTCUSDT", "ETHUSDT"])].copy()
    df = df[df["tf"].isin(["1m", "5m"])].copy()
    df = df.dropna(subset=["ts", "open", "high", "low", "close"]).copy()
    df = df.sort_values(["symbol_norm", "tf", "ts"]).reset_index(drop=True)

    range_abs = (df["high"] - df["low"]).replace(0, np.nan)
    close = df["close"].replace(0, np.nan)

    df["v13_range_pct_calc"] = range_abs / close
    df["v13_body_pct_calc"] = (df["close"] - df["open"]).abs() / close
    df["v13_body_to_range_calc"] = (df["close"] - df["open"]).abs() / range_abs
    df["v13_upper_wick_pct_calc"] = (df["high"] - df[["open", "close"]].max(axis=1)) / close
    df["v13_lower_wick_pct_calc"] = (df[["open", "close"]].min(axis=1) - df["low"]) / close
    df["v13_close_pos_calc"] = (df["close"] - df["low"]) / range_abs
    df["v13_is_green_calc"] = (df["close"] >= df["open"]).astype(float)

    grouped = df.groupby(["symbol_norm", "tf"], sort=False)

    df["v13_ret_20_calc"] = grouped["close"].pct_change(20)
    df["v13_ret_50_calc"] = grouped["close"].pct_change(50)

    high_20 = grouped["high"].transform(lambda s: s.rolling(20, min_periods=5).max())
    low_20 = grouped["low"].transform(lambda s: s.rolling(20, min_periods=5).min())

    df["v13_dist_high_20_calc"] = (df["close"] - high_20) / high_20.replace(0, np.nan)
    df["v13_dist_low_20_calc"] = (df["close"] - low_20) / low_20.replace(0, np.nan)

    range_mean_50 = grouped["v13_range_pct_calc"].transform(lambda s: s.rolling(50, min_periods=10).mean())
    range_std_50 = grouped["v13_range_pct_calc"].transform(lambda s: s.rolling(50, min_periods=10).std())
    volume_mean_50 = grouped["volume"].transform(lambda s: s.rolling(50, min_periods=10).mean())
    volume_std_50 = grouped["volume"].transform(lambda s: s.rolling(50, min_periods=10).std())

    df["v13_range_z_50_calc"] = (df["v13_range_pct_calc"] - range_mean_50) / range_std_50.replace(0, np.nan)
    df["v13_volume_z_50_calc"] = (df["volume"] - volume_mean_50) / volume_std_50.replace(0, np.nan)

    return df


def add_prior_features(out: pd.DataFrame, trade: pd.DataFrame) -> pd.DataFrame:
    mappings = {
        "prior_1m_ret_1": "open_1m_ret_1",
        "prior_1m_ret_3": "open_1m_ret_3",
        "prior_1m_ret_5": "open_1m_ret_5",
        "prior_1m_ret_10": "open_1m_ret_10",
        "prior_1m_ret_15": "open_1m_ret_15",
        "prior_1m_dist_ema20": "open_1m_dist_ema20",
        "prior_1m_dist_ema50": "open_1m_dist_ema50",
        "prior_1m_dist_ema200": "open_1m_dist_ema200",
        "prior_1m_rsi_14": "open_1m_rsi_14",
        "prior_1m_macd_hist": "open_1m_macd_hist",
        "prior_1m_atr_pct_14": "open_1m_atr_pct_14",
        "prior_1m_vol_30": "open_1m_vol_30",
        "prior_1m_vol_120": "open_1m_vol_120",
        "prior_1m_volume_rel_30": "open_1m_volume_rel_30",
        "prior_1m_volume_z_30": "open_1m_volume_z_30",
        "prior_1m_trend_score": "open_1m_trend_score",
        "prior_5m_ret_1": "open_5m_ret_1",
        "prior_5m_ret_3": "open_5m_ret_3",
        "prior_5m_ret_5": "open_5m_ret_5",
        "prior_5m_ret_10": "open_5m_ret_10",
        "prior_5m_ret_15": "open_5m_ret_15",
        "prior_5m_dist_ema20": "open_5m_dist_ema20",
        "prior_5m_dist_ema50": "open_5m_dist_ema50",
        "prior_5m_dist_ema200": "open_5m_dist_ema200",
        "prior_5m_rsi_14": "open_5m_rsi_14",
        "prior_5m_macd_hist": "open_5m_macd_hist",
        "prior_5m_atr_pct_14": "open_5m_atr_pct_14",
        "prior_5m_vol_30": "open_5m_vol_30",
        "prior_5m_vol_120": "open_5m_vol_120",
        "prior_5m_volume_rel_30": "open_5m_volume_rel_30",
        "prior_5m_volume_z_30": "open_5m_volume_z_30",
        "prior_5m_trend_score": "open_5m_trend_score",
    }

    for dst, src in mappings.items():
        out[dst] = pd.to_numeric(trade[src], errors="coerce") if src in trade.columns else np.nan

    return out


def add_meta_features(out: pd.DataFrame) -> pd.DataFrame:
    symbol = out["symbol"].map(normalize_symbol)
    side = out["side"].map(normalize_side)

    ts = pd.to_datetime(out["open_time_utc"], errors="coerce", utc=True)

    hour = ts.dt.hour.fillna(0).astype(float)
    dow = ts.dt.dayofweek.fillna(0).astype(float)
    month = ts.dt.month.fillna(1).astype(float)

    out["meta_symbol_btcusdt"] = (symbol == "BTCUSDT").astype(float)
    out["meta_symbol_ethusdt"] = (symbol == "ETHUSDT").astype(float)

    out["meta_side_long"] = (side == "long").astype(float)
    out["meta_side_short"] = (side == "short").astype(float)
    out["meta_side_unknown"] = (~side.isin(["long", "short"])).astype(float)

    out["meta_hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    out["meta_hour_cos"] = np.cos(2 * np.pi * hour / 24.0)

    out["meta_dow_sin"] = np.sin(2 * np.pi * dow / 7.0)
    out["meta_dow_cos"] = np.cos(2 * np.pi * dow / 7.0)

    out["meta_month_sin"] = np.sin(2 * np.pi * month / 12.0)
    out["meta_month_cos"] = np.cos(2 * np.pi * month / 12.0)

    out["meta_session_asia"] = ((hour >= 0) & (hour < 8)).astype(float)
    out["meta_session_europe"] = ((hour >= 7) & (hour < 16)).astype(float)
    out["meta_session_newyork"] = ((hour >= 13) & (hour < 22)).astype(float)
    out["meta_session_europe_newyork_overlap"] = ((hour >= 13) & (hour < 16)).astype(float)
    out["meta_is_weekend"] = (dow >= 5).astype(float)

    return out


def v13_source_column(tf: str, name: str) -> str:
    return f"v13_{name}_calc"


def add_v13_features(out: pd.DataFrame, trade: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    market_cols = [
        "symbol_norm",
        "tf",
        "ts",
        "v13_range_pct_calc",
        "v13_body_pct_calc",
        "v13_body_to_range_calc",
        "v13_upper_wick_pct_calc",
        "v13_lower_wick_pct_calc",
        "v13_close_pos_calc",
        "v13_is_green_calc",
        "v13_ret_20_calc",
        "v13_ret_50_calc",
        "v13_dist_high_20_calc",
        "v13_dist_low_20_calc",
        "v13_range_z_50_calc",
        "v13_volume_z_50_calc",
    ]

    feature_names = [
        "range_pct",
        "body_pct",
        "body_to_range",
        "upper_wick_pct",
        "lower_wick_pct",
        "close_pos",
        "is_green",
        "ret_20",
        "ret_50",
        "dist_high_20",
        "dist_low_20",
        "range_z_50",
        "volume_z_50",
    ]

    base = pd.DataFrame({
        "trade_row_id": np.arange(len(trade)),
        "symbol_norm": trade["symbol"].map(normalize_symbol),
        "open_1m_ts": pd.to_datetime(trade.get("open_1m_ts"), errors="coerce", utc=True),
        "open_5m_ts": pd.to_datetime(trade.get("open_5m_ts"), errors="coerce", utc=True),
    })

    for tf in ["1m", "5m"]:
        time_col = f"open_{tf}_ts"
        left = base[["trade_row_id", "symbol_norm", time_col]].rename(columns={time_col: "ts"}).copy()
        left = left.dropna(subset=["ts"]).sort_values(["symbol_norm", "ts"])

        right = market[market["tf"].eq(tf)][market_cols].copy()
        right = right.sort_values(["symbol_norm", "ts"])

        joined_parts = []

        for symbol_value, left_group in left.groupby("symbol_norm", sort=False):
            right_group = right[right["symbol_norm"].eq(symbol_value)].copy()

            if right_group.empty:
                continue

            merged = pd.merge_asof(
                left_group.sort_values("ts"),
                right_group.sort_values("ts"),
                on="ts",
                by="symbol_norm",
                direction="backward",
                tolerance=pd.Timedelta("15min") if tf == "1m" else pd.Timedelta("30min"),
            )

            joined_parts.append(merged)

        if joined_parts:
            joined = pd.concat(joined_parts, ignore_index=True).set_index("trade_row_id")
        else:
            joined = pd.DataFrame(index=base["trade_row_id"])

        for name in feature_names:
            src = v13_source_column(tf, name)
            dst = f"v13_{tf}_{name}"
            out[dst] = joined[src].reindex(base["trade_row_id"]).to_numpy() if src in joined.columns else np.nan

    return out


def build_dataset(model_features: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    trade = pd.read_parquet(TRADE_ENRICHED_PATH)
    market = prepare_market_features(pd.read_parquet(MARKET_FEATURES_PATH))

    out = pd.DataFrame(index=trade.index)

    out["trade_id"] = trade["trade_id"].astype(str) if "trade_id" in trade.columns else trade.index.astype(str)
    out["symbol"] = trade["symbol"].map(normalize_symbol) if "symbol" in trade.columns else trade["moeda"].map(normalize_symbol)
    out["side"] = trade["fechar_side"].map(normalize_side) if "fechar_side" in trade.columns else "unknown"
    out["segment"] = np.where(trade.get("source_file", "").astype(str).eq(OCR_SOURCE), "BITRADEX_OCR", "HISTORICAL")
    out["open_time_utc"] = pd.to_datetime(trade["open_ts"], errors="coerce", utc=True) if "open_ts" in trade.columns else pd.to_datetime(trade["horario_abertura"], errors="coerce", utc=True)

    out["target_win"] = pd.to_numeric(trade["target_win"], errors="coerce") if "target_win" in trade.columns else (pd.to_numeric(trade["pnl"], errors="coerce") > 0).astype(int)
    out["reported_pnl_usdt"] = pd.to_numeric(trade["pnl"], errors="coerce") if "pnl" in trade.columns else trade["pnl_fechado"].map(to_num)
    out["pnl_sign_label"] = np.where(out["reported_pnl_usdt"] > 0, 1, 0)

    out = add_prior_features(out, trade)
    out = add_meta_features(out)
    out = add_v13_features(out, trade, market)

    out["source_file"] = trade["source_file"].astype(str) if "source_file" in trade.columns else ""
    out["order_id"] = trade["order_id"].astype(str) if "order_id" in trade.columns else ""
    out["symbol_norm"] = out["symbol"].map(normalize_symbol)
    out["trade_index"] = np.arange(len(out), dtype=int)

    for feature in model_features:
        if feature not in out.columns:
            out[feature] = np.nan

    model_numeric = out[model_features].apply(pd.to_numeric, errors="coerce")
    finite_mask = np.isfinite(model_numeric.to_numpy()).all(axis=1)

    required_base_mask = (
        out["trade_id"].map(clean_text).ne("")
        & out["symbol"].isin(["BTCUSDT", "ETHUSDT"])
        & out["side"].isin(["long", "short"])
        & pd.to_datetime(out["open_time_utc"], errors="coerce", utc=True).notna()
        & pd.to_numeric(out["target_win"], errors="coerce").notna()
        & pd.to_numeric(out["reported_pnl_usdt"], errors="coerce").notna()
    )

    compatible_mask = finite_mask & required_base_mask.to_numpy()

    quality_reasons = []

    for idx in range(len(out)):
        reasons = []

        if not required_base_mask.iloc[idx]:
            reasons.append("base_required_field_missing_or_invalid")

        missing_features = [
            f
            for f in model_features
            if not np.isfinite(pd.to_numeric(out.at[idx, f], errors="coerce"))
        ]

        if missing_features:
            reasons.append("missing_model_features:" + ",".join(missing_features[:20]))

        quality_reasons.append("ok" if not reasons else "|".join(reasons))

    out["trade_data_quality_status"] = np.where(compatible_mask, "OK", "BLOCKED")
    out["train_allowed"] = compatible_mask
    out["row_status"] = np.where(compatible_mask, "COMPATIBLE", "INCOMPATIBLE")
    out["is_compatible"] = compatible_mask
    out["is_exact_compatible"] = compatible_mask
    out["quality_reason"] = quality_reasons

    ordered_cols = BASE_COLUMNS + model_features + AUX_COLUMNS
    full = out[ordered_cols].copy()
    candidate = full[full["is_exact_compatible"].eq(True)].copy()

    summary = {
        "input_trade_enriched_rows": int(len(trade)),
        "full_rows": int(len(full)),
        "candidate_rows": int(len(candidate)),
        "blocked_rows": int(len(full) - len(candidate)),
        "ocr_rows_full": int(full["source_file"].astype(str).eq(OCR_SOURCE).sum()),
        "ocr_rows_candidate": int(candidate["source_file"].astype(str).eq(OCR_SOURCE).sum()),
        "candidate_symbol_counts": candidate["symbol"].value_counts(dropna=False).to_dict(),
        "candidate_side_counts": candidate["side"].value_counts(dropna=False).to_dict(),
        "blocked_reason_counts": full.loc[~full["is_exact_compatible"], "quality_reason"].value_counts(dropna=False).head(20).to_dict(),
    }

    return full, candidate, summary


def main() -> None:
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_JSON.parent.mkdir(parents=True, exist_ok=True)

    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Modelo n├úo encontrado: {MODEL_PATH}")
    if not TRADE_ENRICHED_PATH.exists():
        raise FileNotFoundError(f"trade_enriched n├úo encontrado: {TRADE_ENRICHED_PATH}")
    if not MARKET_FEATURES_PATH.exists():
        raise FileNotFoundError(f"market_features n├úo encontrado: {MARKET_FEATURES_PATH}")

    model = joblib.load(MODEL_PATH)
    model_features = extract_model_features(model)

    if len(model_features) != 74:
        raise RuntimeError(f"Esperado 74 features do modelo, recebido {len(model_features)}")

    full, candidate, stats = build_dataset(model_features)

    full.to_parquet(OUTPUT_FULL_AUDIT, index=False)
    candidate.to_parquet(OUTPUT_CANDIDATE, index=False)

    missing_model_features_candidate = [f for f in model_features if f not in candidate.columns]

    validation_errors = []

    if missing_model_features_candidate:
        validation_errors.append(f"candidate_missing_model_features:{missing_model_features_candidate}")

    if len(candidate) <= 2377:
        validation_errors.append(f"candidate_rows_not_above_old_quality_gated:{len(candidate)}")

    if stats["ocr_rows_candidate"] <= 0:
        validation_errors.append("no_ocr_rows_in_candidate")

    if int(candidate["trade_id"].astype(str).duplicated(keep=False).sum()) != 0:
        validation_errors.append("duplicate_trade_id_in_candidate")

    status = "ok" if not validation_errors else "blocked"

    summary = {
        "status": status,
        "mode": "build_quality_gated_shadow_compatible_dataset_v1",
        "safety": {
            "writes_official_quality_gated_dataset": False,
            "writes_candidate_only": True,
            "changes_model": False,
            "sends_orders": False,
        },
        "model": str(MODEL_PATH),
        "model_features_count": int(len(model_features)),
        "model_feature_family_counts": {
            "prior": sum(1 for c in model_features if c.startswith("prior_")),
            "meta": sum(1 for c in model_features if c.startswith("meta_")),
            "v13": sum(1 for c in model_features if c.startswith("v13_")),
        },
        "input": {
            "trade_enriched": str(TRADE_ENRICHED_PATH),
            "market_features": str(MARKET_FEATURES_PATH),
        },
        "stats": stats,
        "outputs": {
            "candidate_parquet": str(OUTPUT_CANDIDATE),
            "full_audit_parquet": str(OUTPUT_FULL_AUDIT),
            "summary_json": str(SUMMARY_JSON),
        },
        "validation_errors": validation_errors,
    }

    SUMMARY_JSON.write_text(
        json.dumps(json_safe(summary), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(json.dumps(json_safe(summary), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
