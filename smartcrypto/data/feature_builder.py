from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from smartcrypto.data.indicators import atr, ema, macd, rolling_volatility, rsi, safe_divide, zscore
from smartcrypto.market.market_feature_schema import write_operational_market_features
from smartcrypto.execution.freqtrade_contract import freqtrade_pair, internal_symbol


def build_market_features(
    input_path: str | Path,
    output_path: str | Path,
    *,
    labels_output_path: str | Path | None = None,
) -> pd.DataFrame:
    source = Path(input_path)
    if not source.exists():
        raise FileNotFoundError(source)

    frame = pd.read_parquet(source)
    frame = _normalize_ohlcv(frame)

    output = []
    for _, group in frame.groupby(["symbol", "tf"], sort=False):
        output.append(_build_group_features(group))

    if not output:
        raise RuntimeError("market feature source is empty")

    features = pd.concat(output, ignore_index=True).sort_values(["symbol", "tf", "ts"]).reset_index(drop=True)
    features, _ = write_operational_market_features(
        features,
        output_path,
        labels_output_path=labels_output_path,
    )
    return features


def _normalize_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["symbol"] = result["symbol"].map(internal_symbol)
    result["pair"] = result["symbol"].map(freqtrade_pair)
    result["tf"] = result["tf"].astype(str)
    result["ts"] = pd.to_datetime(result["ts"], utc=True, errors="coerce")

    numeric_columns = ["open", "high", "low", "close", "volume"]
    for column in numeric_columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")

    return result.dropna(subset=["symbol", "tf", "ts", *numeric_columns]).sort_values(["symbol", "tf", "ts"])


def _build_group_features(group: pd.DataFrame) -> pd.DataFrame:
    frame = group.sort_values("ts").copy()

    frame["ret_1"] = frame["close"].pct_change()
    frame["ret_3"] = frame["close"].pct_change(3)
    frame["ret_5"] = frame["close"].pct_change(5)
    frame["ret_10"] = frame["close"].pct_change(10)
    frame["ret_15"] = frame["close"].pct_change(15)
    frame["ret_30"] = frame["close"].pct_change(30)

    frame["future_ret_1"] = frame["close"].shift(-1) / frame["close"] - 1
    frame["future_ret_3"] = frame["close"].shift(-3) / frame["close"] - 1
    frame["future_ret_5"] = frame["close"].shift(-5) / frame["close"] - 1

    frame["ema_20"] = ema(frame["close"], 20)
    frame["ema_50"] = ema(frame["close"], 50)
    frame["ema_200"] = ema(frame["close"], 200)

    frame["dist_ema20"] = safe_divide(frame["close"] - frame["ema_20"], frame["ema_20"])
    frame["dist_ema50"] = safe_divide(frame["close"] - frame["ema_50"], frame["ema_50"])
    frame["dist_ema200"] = safe_divide(frame["close"] - frame["ema_200"], frame["ema_200"])

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

    frame["hl_range"] = safe_divide(frame["high"] - frame["low"], frame["close"])
    frame["body_range"] = safe_divide((frame["close"] - frame["open"]).abs(), frame["close"])
    frame["upper_wick"] = safe_divide(frame["high"] - frame[["open", "close"]].max(axis=1), frame["close"])
    frame["lower_wick"] = safe_divide(frame[["open", "close"]].min(axis=1) - frame["low"], frame["close"])

    frame["trend_score"] = _trend_score(frame)
    frame["market_regime"] = _market_regime(frame)

    return frame


def _trend_score(frame: pd.DataFrame) -> pd.Series:
    above_20 = np.sign(frame["close"] - frame["ema_20"])
    above_50 = np.sign(frame["close"] - frame["ema_50"])
    above_200 = np.sign(frame["close"] - frame["ema_200"])
    ema_stack = np.sign(frame["ema_20"] - frame["ema_50"]) + np.sign(frame["ema_50"] - frame["ema_200"])
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
    regime.loc[high_volatility.fillna(False) & trend.between(-2, 2)] = "range_high_vol"
    return regime
