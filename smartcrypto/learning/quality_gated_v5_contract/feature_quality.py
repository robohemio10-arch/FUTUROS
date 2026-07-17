"""Model feature construction, quality validation and market lineage diagnostics."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .contracts import META_FEATURES, MODEL_FEATURES, PRIOR_FEATURE_SUFFIXES, V13_FEATURE_SUFFIXES


def normalize_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    return (
        text.replace("/USDT:USDT", "USDT")
        .replace("/", "")
        .replace(":", "")
        .replace("-", "")
        .replace("_", "")
        .replace("USDTUSDT", "USDT")
    )


def normalize_side(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "long" in text:
        return "long"
    if "short" in text:
        return "short"
    return "unknown"


def prepare_market_features(raw: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "tf", "ts", "open", "high", "low", "close", "volume"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(f"market_features_missing_columns:{','.join(missing)}")

    frame = raw.copy()
    frame["symbol_norm"] = frame["symbol"].map(normalize_symbol)
    frame["tf"] = frame["tf"].astype(str).str.lower()
    frame["ts"] = pd.to_datetime(frame["ts"], errors="coerce", utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["ts", "open", "high", "low", "close"]).copy()
    frame = frame.sort_values(["symbol_norm", "tf", "ts"]).reset_index(drop=True)

    range_abs = (frame["high"] - frame["low"]).replace(0, np.nan)
    close = frame["close"].replace(0, np.nan)
    frame["v13_range_pct_calc"] = range_abs / close
    frame["v13_body_pct_calc"] = (frame["close"] - frame["open"]).abs() / close
    frame["v13_body_to_range_calc"] = (frame["close"] - frame["open"]).abs() / range_abs
    frame["v13_upper_wick_pct_calc"] = (
        frame["high"] - frame[["open", "close"]].max(axis=1)
    ) / close
    frame["v13_lower_wick_pct_calc"] = (
        frame[["open", "close"]].min(axis=1) - frame["low"]
    ) / close
    frame["v13_close_pos_calc"] = (frame["close"] - frame["low"]) / range_abs
    frame["v13_is_green_calc"] = (frame["close"] >= frame["open"]).astype(float)

    grouped = frame.groupby(["symbol_norm", "tf"], sort=False)
    frame["v13_ret_20_calc"] = grouped["close"].pct_change(20)
    frame["v13_ret_50_calc"] = grouped["close"].pct_change(50)
    high_20 = grouped["high"].transform(lambda series: series.rolling(20, min_periods=5).max())
    low_20 = grouped["low"].transform(lambda series: series.rolling(20, min_periods=5).min())
    frame["v13_dist_high_20_calc"] = (
        frame["close"] - high_20
    ) / high_20.replace(0, np.nan)
    frame["v13_dist_low_20_calc"] = (
        frame["close"] - low_20
    ) / low_20.replace(0, np.nan)
    range_mean_50 = grouped["v13_range_pct_calc"].transform(
        lambda series: series.rolling(50, min_periods=10).mean()
    )
    range_std_50 = grouped["v13_range_pct_calc"].transform(
        lambda series: series.rolling(50, min_periods=10).std()
    )
    volume_mean_50 = grouped["volume"].transform(
        lambda series: series.rolling(50, min_periods=10).mean()
    )
    volume_std_50 = grouped["volume"].transform(
        lambda series: series.rolling(50, min_periods=10).std()
    )
    frame["v13_range_z_50_calc"] = (
        frame["v13_range_pct_calc"] - range_mean_50
    ) / range_std_50.replace(0, np.nan)
    frame["v13_volume_z_50_calc"] = (
        frame["volume"] - volume_mean_50
    ) / volume_std_50.replace(0, np.nan)
    return frame


def add_meta_features(output: pd.DataFrame, trades: pd.DataFrame) -> None:
    symbol_source = trades["symbol"] if "symbol" in trades.columns else trades.get("moeda", "")
    side_source = (
        trades["fechar_side"]
        if "fechar_side" in trades.columns
        else trades.get("side", "")
    )
    open_source = trades["open_ts"] if "open_ts" in trades.columns else trades.get("open_time_utc")

    symbol = pd.Series(symbol_source, index=trades.index).map(normalize_symbol)
    side = pd.Series(side_source, index=trades.index).map(normalize_side)
    timestamp = pd.to_datetime(open_source, errors="coerce", utc=True)
    hour = timestamp.dt.hour.fillna(0).astype(float)
    day_of_week = timestamp.dt.dayofweek.fillna(0).astype(float)
    month = timestamp.dt.month.fillna(1).astype(float)

    output["meta_symbol_btcusdt"] = symbol.eq("BTCUSDT").astype(float)
    output["meta_symbol_ethusdt"] = symbol.eq("ETHUSDT").astype(float)
    output["meta_side_long"] = side.eq("long").astype(float)
    output["meta_side_short"] = side.eq("short").astype(float)
    output["meta_side_unknown"] = ~side.isin(["long", "short"])
    output["meta_side_unknown"] = output["meta_side_unknown"].astype(float)
    output["meta_hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    output["meta_hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
    output["meta_dow_sin"] = np.sin(2 * np.pi * day_of_week / 7.0)
    output["meta_dow_cos"] = np.cos(2 * np.pi * day_of_week / 7.0)
    output["meta_month_sin"] = np.sin(2 * np.pi * month / 12.0)
    output["meta_month_cos"] = np.cos(2 * np.pi * month / 12.0)
    output["meta_session_asia"] = ((hour >= 0) & (hour < 8)).astype(float)
    output["meta_session_europe"] = ((hour >= 7) & (hour < 16)).astype(float)
    output["meta_session_newyork"] = ((hour >= 13) & (hour < 22)).astype(float)
    output["meta_session_europe_newyork_overlap"] = (
        (hour >= 13) & (hour < 16)
    ).astype(float)
    output["meta_is_weekend"] = day_of_week.ge(5).astype(float)


def exact_market_snapshot_lookup(
    trades: pd.DataFrame,
    market: pd.DataFrame,
    timeframe: str,
) -> pd.DataFrame:
    symbol_source = trades["symbol"] if "symbol" in trades.columns else trades.get("moeda", "")
    left = pd.DataFrame(
        {
            "_row_id": np.arange(len(trades)),
            "symbol_norm": pd.Series(symbol_source, index=trades.index).map(normalize_symbol),
            "ts": pd.to_datetime(
                trades.get(f"open_{timeframe}_ts"), errors="coerce", utc=True
            ),
        }
    )
    right = market.loc[market["tf"].eq(timeframe)].copy()
    columns = [column for column in right.columns if column not in {"symbol", "pair"}]
    right = right[columns].drop_duplicates(["symbol_norm", "ts"], keep="last")
    merged = left.merge(right, on=["symbol_norm", "ts"], how="left", sort=False)
    return merged.set_index("_row_id").reindex(range(len(trades)))


def build_model_feature_frame(
    trades: pd.DataFrame,
    market_features: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    output = pd.DataFrame(index=range(len(trades)))
    trade = trades.reset_index(drop=True).copy()
    market = prepare_market_features(market_features)

    for timeframe in ("1m", "5m"):
        for suffix in PRIOR_FEATURE_SUFFIXES:
            source = f"open_{timeframe}_{suffix}"
            destination = f"prior_{timeframe}_{suffix}"
            output[destination] = (
                pd.to_numeric(trade[source], errors="coerce")
                if source in trade.columns
                else np.nan
            )

    add_meta_features(output, trade)

    snapshots: dict[str, pd.DataFrame] = {}
    for timeframe in ("1m", "5m"):
        snapshot = exact_market_snapshot_lookup(trade, market, timeframe)
        snapshots[timeframe] = snapshot
        for suffix in V13_FEATURE_SUFFIXES:
            source = f"v13_{suffix}_calc"
            destination = f"v13_{timeframe}_{suffix}"
            output[destination] = (
                pd.to_numeric(snapshot[source], errors="coerce")
                if source in snapshot.columns
                else np.nan
            )

    return output.loc[:, list(MODEL_FEATURES)].copy(), snapshots


def model_feature_family(feature: str) -> str:
    if feature.startswith("prior_1m_"):
        return "prior_1m"
    if feature.startswith("prior_5m_"):
        return "prior_5m"
    if feature.startswith("v13_1m_"):
        return "v13_1m"
    if feature.startswith("v13_5m_"):
        return "v13_5m"
    if feature.startswith("meta_"):
        return "meta"
    return "unknown"


def audit_feature_quality(features: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    missing_schema = [feature for feature in MODEL_FEATURES if feature not in features.columns]
    records: list[dict[str, Any]] = []
    for index in features.index:
        row = features.loc[index]
        missing: list[str] = []
        non_numeric: list[str] = []
        non_finite: list[str] = []
        for feature in MODEL_FEATURES:
            if feature not in features.columns:
                missing.append(feature)
                continue
            raw = row[feature]
            converted = pd.to_numeric(pd.Series([raw]), errors="coerce").iloc[0]
            if pd.isna(converted):
                if pd.isna(raw):
                    missing.append(feature)
                else:
                    non_numeric.append(feature)
                continue
            if not np.isfinite(float(converted)):
                non_finite.append(feature)

        family_missing = {
            family: sorted(feature for feature in missing if model_feature_family(feature) == family)
            for family in ("prior_1m", "prior_5m", "meta", "v13_1m", "v13_5m")
        }
        block_reasons: list[str] = []
        if missing_schema:
            block_reasons.append("BLOCKED_MODEL_FEATURE_SCHEMA")
        if non_numeric:
            block_reasons.append("BLOCKED_NON_NUMERIC_MODEL_FEATURES")
        if non_finite:
            block_reasons.append("BLOCKED_NON_FINITE_MODEL_FEATURES")
        if family_missing["prior_1m"]:
            block_reasons.append("BLOCKED_MISSING_PRIOR_1M_FEATURES")
        if family_missing["prior_5m"]:
            block_reasons.append("BLOCKED_MISSING_PRIOR_5M_FEATURES")
        if family_missing["v13_1m"]:
            block_reasons.append("BLOCKED_MISSING_V13_1M_FEATURES")
        if family_missing["v13_5m"]:
            block_reasons.append("BLOCKED_MISSING_V13_5M_FEATURES")

        records.append(
            {
                "missing_model_features": sorted(missing),
                "non_numeric_model_features": sorted(non_numeric),
                "non_finite_model_features": sorted(non_finite),
                "feature_block_reasons": block_reasons,
                "model_features_finite": not (missing or non_numeric or non_finite or missing_schema),
            }
        )

    detail = pd.DataFrame(records, index=features.index)
    numeric = features.reindex(columns=MODEL_FEATURES).apply(pd.to_numeric, errors="coerce")
    null_rates = numeric.isna().mean().sort_values(ascending=False)
    summary = {
        "model_schema_feature_count": len(MODEL_FEATURES),
        "model_schema_features": list(MODEL_FEATURES),
        "schema_missing_features": missing_schema,
        "fully_finite_rows": int(detail["model_features_finite"].sum()),
        "blocked_rows": int((~detail["model_features_finite"]).sum()),
        "feature_null_rate_global": {
            str(feature): float(rate) for feature, rate in null_rates.items()
        },
    }
    return detail, summary


def audit_prior_feature_lineage(
    trades: pd.DataFrame,
    snapshots: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    trade = trades.reset_index(drop=True)
    records: list[dict[str, Any]] = []
    for index in range(len(trade)):
        row_record: dict[str, Any] = {}
        for timeframe in ("1m", "5m"):
            snapshot = snapshots[timeframe].loc[index]
            missing_materialized: list[str] = []
            source_missing: list[str] = []
            source_non_finite: list[str] = []
            materialized_ok: list[str] = []
            for suffix in PRIOR_FEATURE_SUFFIXES:
                trade_column = f"open_{timeframe}_{suffix}"
                source_column = suffix
                source_value = snapshot.get(source_column)
                trade_value = trade.at[index, trade_column] if trade_column in trade.columns else np.nan
                source_number = pd.to_numeric(pd.Series([source_value]), errors="coerce").iloc[0]
                trade_number = pd.to_numeric(pd.Series([trade_value]), errors="coerce").iloc[0]
                source_finite = pd.notna(source_number) and np.isfinite(float(source_number))
                trade_finite = pd.notna(trade_number) and np.isfinite(float(trade_number))
                if pd.isna(snapshot.get("ts")):
                    source_missing.append(source_column)
                elif not source_finite:
                    source_non_finite.append(source_column)
                elif not trade_finite:
                    missing_materialized.append(trade_column)
                else:
                    materialized_ok.append(trade_column)
            row_record.update(
                {
                    f"raw_{timeframe}_snapshot_available": not pd.isna(snapshot.get("ts")),
                    f"source_{timeframe}_features_missing": source_missing,
                    f"source_{timeframe}_features_non_finite": source_non_finite,
                    f"materialized_{timeframe}_features_missing": missing_materialized,
                    f"materialized_{timeframe}_features_ok": materialized_ok,
                }
            )
        records.append(row_record)
    return pd.DataFrame(records)
