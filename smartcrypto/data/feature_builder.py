from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from smartcrypto.data.indicators import (
    atr,
    ema,
    macd,
    rolling_volatility,
    rsi,
    safe_divide,
    zscore,
)
from smartcrypto.execution.freqtrade_contract import freqtrade_pair, internal_symbol
from smartcrypto.market.market_feature_schema import (
    sanitize_operational_market_features,
    write_operational_market_features,
)

RAW_MARKET_COLUMNS = (
    "symbol",
    "pair",
    "tf",
    "ts",
    "open",
    "high",
    "low",
    "close",
    "volume",
)

DERIVED_MARKET_FEATURE_COLUMNS = (
    "ret_1",
    "ret_3",
    "ret_5",
    "ret_10",
    "ret_15",
    "ret_30",
    "ema_20",
    "ema_50",
    "ema_200",
    "dist_ema20",
    "dist_ema50",
    "dist_ema200",
    "rsi_14",
    "macd_line",
    "macd_signal",
    "macd_hist",
    "atr_14",
    "atr_pct_14",
    "vol_30",
    "vol_120",
    "volume_mean_30",
    "volume_mean_120",
    "volume_rel_30",
    "volume_z_30",
    "hl_range",
    "body_range",
    "upper_wick",
    "lower_wick",
    "trend_score",
    "market_regime",
)

LOOKAHEAD_LABEL_COLUMNS = (
    "future_ret_1",
    "future_ret_3",
    "future_ret_5",
)

def build_market_features(
    input_path: str | Path,
    output_path: str | Path,
    *,
    labels_output_path: str | Path | None = None,
) -> pd.DataFrame:
    """Build and persist operational market features from an OHLCV parquet file.

    The calculation itself is delegated to the pure in-memory builder below. This
    keeps runtime refreshes from having to serialize temporary parquet files just to
    calculate features, while preserving the centralized no-lookahead writer.
    """

    source = Path(input_path)
    if not source.exists():
        raise FileNotFoundError(source)

    raw = pd.read_parquet(source)
    features_with_labels = build_market_feature_frame(
        raw,
        include_lookahead_labels=True,
    )
    features, _ = write_operational_market_features(
        features_with_labels,
        output_path,
        labels_output_path=labels_output_path,
    )
    return features


def build_market_feature_frame(
    frame: pd.DataFrame,
    *,
    include_lookahead_labels: bool = False,
) -> pd.DataFrame:
    """Build deterministic market features from an in-memory OHLCV frame.

    By default this returns the operational, no-lookahead feature contract. Research
    callers that explicitly need forward-return labels may request them and must keep
    that frame outside operational runtime paths.
    """

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("market_feature_input_must_be_dataframe")
    if frame.empty:
        raise RuntimeError("market feature source is empty")

    normalized = _normalize_ohlcv(frame)
    output = [
        _build_group_features(group)
        for _, group in normalized.groupby(["symbol", "tf"], sort=False)
    ]
    if not output:
        raise RuntimeError("market feature source is empty")

    features = (
        pd.concat(output, ignore_index=True)
        .sort_values(["symbol", "tf", "ts"], kind="mergesort")
        .reset_index(drop=True)
    )
    if include_lookahead_labels:
        return features

    sanitized, _ = sanitize_operational_market_features(features)
    return sanitized


def _normalize_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "tf", "ts", "open", "high", "low", "close", "volume"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"missing_raw_columns:{missing}")

    result = frame.copy()
    result["symbol"] = result["symbol"].map(internal_symbol)
    result["pair"] = result["symbol"].map(freqtrade_pair)
    result["tf"] = result["tf"].astype(str)
    result["ts"] = pd.to_datetime(result["ts"], utc=True, errors="coerce")

    numeric_columns = ["open", "high", "low", "close", "volume"]
    for column in numeric_columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")

    result = result.dropna(
        subset=["symbol", "tf", "ts", *numeric_columns]
    ).copy()
    if result.empty:
        raise RuntimeError("market feature source is empty")

    # Duplicate OHLCV keys are resolved deterministically before rolling indicators
    # are calculated. Keeping duplicate candles in a rolling window distorts every
    # downstream indicator, so dedupe belongs at the raw boundary.
    return (
        result.sort_values(["symbol", "tf", "ts"], kind="mergesort")
        .drop_duplicates(subset=["symbol", "tf", "ts"], keep="last")
        .reset_index(drop=True)
    )


def _build_group_features(group: pd.DataFrame) -> pd.DataFrame:
    frame = group.sort_values("ts", kind="mergesort").copy()

    frame["ret_1"] = frame["close"].pct_change(fill_method=None)
    frame["ret_3"] = frame["close"].pct_change(3, fill_method=None)
    frame["ret_5"] = frame["close"].pct_change(5, fill_method=None)
    frame["ret_10"] = frame["close"].pct_change(10, fill_method=None)
    frame["ret_15"] = frame["close"].pct_change(15, fill_method=None)
    frame["ret_30"] = frame["close"].pct_change(30, fill_method=None)

    frame["future_ret_1"] = frame["close"].shift(-1) / frame["close"] - 1
    frame["future_ret_3"] = frame["close"].shift(-3) / frame["close"] - 1
    frame["future_ret_5"] = frame["close"].shift(-5) / frame["close"] - 1

    frame["ema_20"] = ema(frame["close"], 20)
    frame["ema_50"] = ema(frame["close"], 50)
    frame["ema_200"] = ema(frame["close"], 200)

    frame["dist_ema20"] = safe_divide(
        frame["close"] - frame["ema_20"],
        frame["ema_20"],
    )
    frame["dist_ema50"] = safe_divide(
        frame["close"] - frame["ema_50"],
        frame["ema_50"],
    )
    frame["dist_ema200"] = safe_divide(
        frame["close"] - frame["ema_200"],
        frame["ema_200"],
    )

    frame["rsi_14"] = rsi(frame["close"], 14)

    macd_frame = macd(frame["close"])
    frame["macd_line"] = macd_frame["macd_line"]
    frame["macd_signal"] = macd_frame["macd_signal"]
    frame["macd_hist"] = macd_frame["macd_hist"]

    frame["atr_14"] = atr(frame, 14)
    frame["atr_pct_14"] = safe_divide(frame["atr_14"], frame["close"])

    frame["vol_30"] = rolling_volatility(frame["close"], 30)
    frame["vol_120"] = rolling_volatility(frame["close"], 120)

    frame["volume_mean_30"] = frame["volume"].rolling(30).mean()
    frame["volume_mean_120"] = frame["volume"].rolling(120).mean()
    frame["volume_rel_30"] = safe_divide(frame["volume"], frame["volume_mean_30"])
    frame["volume_z_30"] = zscore(frame["volume"], 30)

    frame["hl_range"] = safe_divide(
        frame["high"] - frame["low"],
        frame["close"],
    )
    frame["body_range"] = safe_divide(
        (frame["close"] - frame["open"]).abs(),
        frame["close"],
    )
    frame["upper_wick"] = safe_divide(
        frame["high"] - frame[["open", "close"]].max(axis=1),
        frame["close"],
    )
    frame["lower_wick"] = safe_divide(
        frame[["open", "close"]].min(axis=1) - frame["low"],
        frame["close"],
    )

    frame["trend_score"] = _trend_score(frame)
    frame["market_regime"] = _market_regime(frame)

    return frame


def _trend_score(frame: pd.DataFrame) -> pd.Series:
    above_20 = np.sign(frame["close"] - frame["ema_20"])
    above_50 = np.sign(frame["close"] - frame["ema_50"])
    above_200 = np.sign(frame["close"] - frame["ema_200"])
    ema_stack = np.sign(frame["ema_20"] - frame["ema_50"]) + np.sign(
        frame["ema_50"] - frame["ema_200"]
    )
    return above_20 + above_50 + above_200 + ema_stack


def _market_regime(frame: pd.DataFrame) -> pd.Series:
    trend = _trend_score(frame)
    volatility = frame["atr_pct_14"]
    high_volatility = volatility > volatility.rolling(200).quantile(0.70)

    regime = pd.Series("range", index=frame.index, dtype="object")
    regime.loc[(trend >= 3) & high_volatility.fillna(False)] = "trend_up_high_vol"
    regime.loc[(trend >= 3) & ~high_volatility.fillna(False)] = "trend_up"
    regime.loc[(trend <= -3) & high_volatility.fillna(False)] = "trend_down_high_vol"
    regime.loc[(trend <= -3) & ~high_volatility.fillna(False)] = "trend_down"
    regime.loc[
        high_volatility.fillna(False) & trend.between(-2, 2)
    ] = "range_high_vol"
    return regime
