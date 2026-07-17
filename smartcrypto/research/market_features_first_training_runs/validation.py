"""Input normalization and fail-closed validation for the research pipeline."""

from __future__ import annotations

import re
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .contracts import (
    FORBIDDEN_EXACT_FEATURES,
    FORBIDDEN_FEATURE_PREFIXES,
    TIMEFRAME,
    TIMEFRAME_SECONDS,
)


_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?")


def normalize_symbol(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    for token in ("/", "_", "-", ":USDT"):
        text = text.replace(token, "")
    if text in {"BTCUSDT", "ETHUSDT"}:
        return text
    return None


def normalize_side(value: Any) -> str | None:
    text = str(value or "").strip().casefold()
    if "short" in text or "venda" in text:
        return "short"
    if "long" in text or "compra" in text:
        return "long"
    return None


def parse_number(value: Any) -> float:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return float("nan")
    if isinstance(value, (int, float, np.number)):
        return float(value)
    match = _NUMBER.search(str(value).replace(" ", ""))
    if match is None:
        return float("nan")
    token = match.group(0)
    if token.count(",") == 1 and token.count(".") == 0:
        token = token.replace(",", ".")
    return float(token)


def utc_series(values: pd.Series) -> pd.Series:
    """Parse mixed ISO and legacy timestamps deterministically as UTC."""

    try:
        return pd.to_datetime(values, utc=True, errors="coerce", format="mixed")
    except TypeError:  # pandas < 2 compatibility
        return pd.to_datetime(values, utc=True, errors="coerce")


def normalize_master(master: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Normalize all canonical Master rows without silently removing any row."""

    required = {
        "moeda",
        "fechar_side",
        "pnl_fechado",
        "horario_abertura",
        "horario_fechamento",
    }
    missing = sorted(required - set(master.columns))
    if missing:
        raise ValueError("master_required_columns_missing:" + ",".join(missing))

    frame = pd.DataFrame(index=master.index)
    frame["source_row_number"] = np.arange(len(master), dtype=int)
    frame["trade_id"] = _stable_trade_ids(master)
    frame["symbol"] = master["moeda"].map(normalize_symbol)
    frame["side"] = master["fechar_side"].map(normalize_side)
    frame["open_time_utc"] = utc_series(master["horario_abertura"])
    frame["close_time_utc"] = utc_series(master["horario_fechamento"])
    frame["net_pnl"] = master["pnl_fechado"].map(parse_number)
    frame["target_profitable"] = frame["net_pnl"].gt(0).astype("Int64")
    fee_one = master.get("taxa_1", pd.Series(index=master.index, dtype=object)).map(
        parse_number
    )
    fee_two = master.get("taxa_2", pd.Series(index=master.index, dtype=object)).map(
        parse_number
    )
    frame["observed_cost"] = fee_one.abs().fillna(0.0) + fee_two.abs().fillna(0.0)
    frame["dataset_partition"] = "master_research_fit_candidate"

    blockers: list[dict[str, Any]] = []
    reasons_by_row: dict[int, list[str]] = {int(index): [] for index in frame.index}
    _record_missing(frame, "symbol", "invalid_symbol", reasons_by_row)
    _record_missing(frame, "side", "invalid_side", reasons_by_row)
    _record_missing(frame, "open_time_utc", "invalid_open_time", reasons_by_row)
    _record_missing(frame, "close_time_utc", "invalid_close_time", reasons_by_row)
    _record_nonfinite(frame, "net_pnl", "invalid_net_pnl", reasons_by_row)
    invalid_interval = (
        frame["open_time_utc"].notna()
        & frame["close_time_utc"].notna()
        & frame["close_time_utc"].lt(frame["open_time_utc"])
    )
    for index in frame.index[invalid_interval]:
        reasons_by_row[int(index)].append("close_before_open")
    frame["validation_block_reasons"] = [
        tuple(sorted(set(reasons_by_row[int(index)]))) for index in frame.index
    ]
    frame["row_status"] = frame["validation_block_reasons"].map(
        lambda reasons: "blocked" if reasons else "eligible_for_alignment"
    )
    for index, row in frame.iterrows():
        for reason in row["validation_block_reasons"]:
            blockers.append(_blocker("master", row, str(reason)))
    return frame, blockers


def normalize_paper(paper: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Normalize the authoritative local paper snapshot as holdout only."""

    aliases = {
        "trade_id": ("stable_trade_id", "trade_id", "id"),
        "symbol": ("symbol", "pair"),
        "side": ("side",),
        "open_time_utc": ("open_time_utc", "open_date"),
        "close_time_utc": ("close_time_utc", "close_date"),
        "net_pnl": ("net_pnl", "close_profit_abs", "realized_profit"),
    }
    frame = pd.DataFrame(index=paper.index)
    for target, candidates in aliases.items():
        source = next((name for name in candidates if name in paper.columns), None)
        frame[target] = paper[source] if source else pd.NA
    frame["source_row_number"] = np.arange(len(paper), dtype=int)
    frame["trade_id"] = frame["trade_id"].map(lambda value: str(value) if pd.notna(value) else None)
    frame["symbol"] = frame["symbol"].map(normalize_symbol)
    if frame["side"].isna().all() and "is_short" in paper.columns:
        frame["side"] = paper["is_short"].map({True: "short", False: "long"})
    frame["side"] = frame["side"].map(normalize_side)
    frame["open_time_utc"] = utc_series(frame["open_time_utc"])
    frame["close_time_utc"] = utc_series(frame["close_time_utc"])
    frame["net_pnl"] = frame["net_pnl"].map(parse_number)
    frame["target_profitable"] = frame["net_pnl"].gt(0).astype("Int64")
    open_fee = paper.get("fee_open_cost", pd.Series(index=paper.index, dtype=float)).map(
        parse_number
    )
    close_fee = paper.get("fee_close_cost", pd.Series(index=paper.index, dtype=float)).map(
        parse_number
    )
    funding = paper.get("funding_fees", pd.Series(index=paper.index, dtype=float)).map(
        parse_number
    )
    frame["observed_cost"] = (
        open_fee.abs().fillna(0.0)
        + close_fee.abs().fillna(0.0)
        + funding.abs().fillna(0.0)
    )
    frame["dataset_partition"] = "paper_external_holdout"
    inherited_eligible = paper.get(
        "analysis_eligible", pd.Series(True, index=paper.index, dtype=bool)
    ).fillna(False)
    inherited_reason = paper.get(
        "analysis_block_reason", pd.Series(pd.NA, index=paper.index, dtype="string")
    )
    reasons_by_row: dict[int, list[str]] = {int(index): [] for index in frame.index}
    for index in frame.index[~inherited_eligible.astype(bool)]:
        value = inherited_reason.loc[index]
        reasons_by_row[int(index)].append(
            str(value) if pd.notna(value) else "paper_snapshot_ineligible"
        )
    _record_missing(frame, "symbol", "invalid_symbol", reasons_by_row)
    _record_missing(frame, "side", "invalid_side", reasons_by_row)
    _record_missing(frame, "open_time_utc", "invalid_open_time", reasons_by_row)
    _record_missing(frame, "close_time_utc", "invalid_close_time", reasons_by_row)
    _record_nonfinite(frame, "net_pnl", "invalid_net_pnl", reasons_by_row)
    frame["validation_block_reasons"] = [
        tuple(sorted(set(reasons_by_row[int(index)]))) for index in frame.index
    ]
    frame["row_status"] = frame["validation_block_reasons"].map(
        lambda reasons: "blocked" if reasons else "eligible_for_alignment"
    )
    blockers = [
        _blocker("paper", row, str(reason))
        for _, row in frame.iterrows()
        for reason in row["validation_block_reasons"]
    ]
    return frame, blockers


def normalize_5m_features(features: pd.DataFrame) -> pd.DataFrame:
    """Rematerialize indicators from deterministic, contiguous 5m OHLCV rows."""

    required = {"symbol", "tf", "ts", "open", "high", "low", "close", "volume"}
    missing = sorted(required - set(features.columns))
    if missing:
        raise ValueError("market_feature_columns_missing:" + ",".join(missing))
    lookahead = forbidden_feature_columns(features.columns)
    if lookahead:
        raise ValueError("market_feature_lookahead_columns:" + ",".join(lookahead))
    frame = features.loc[
        features["tf"].astype(str).str.casefold().eq(TIMEFRAME),
        ["symbol", "tf", "ts", "open", "high", "low", "close", "volume"],
    ].copy()
    frame["symbol"] = frame["symbol"].map(normalize_symbol)
    frame["candle_timestamp_utc"] = utc_series(frame.pop("ts"))
    frame["available_at_utc"] = frame["candle_timestamp_utc"] + pd.Timedelta(
        seconds=TIMEFRAME_SECONDS
    )
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(
        subset=[
            "symbol",
            "candle_timestamp_utc",
            "available_at_utc",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    )
    valid_ohlc = (
        frame["high"].ge(frame[["open", "close", "low"]].max(axis=1))
        & frame["low"].le(frame[["open", "close", "high"]].min(axis=1))
    )
    frame = frame.loc[valid_ohlc]
    frame = frame.sort_values(["symbol", "candle_timestamp_utc"], kind="mergesort")
    frame = frame.drop_duplicates(["symbol", "candle_timestamp_utc"], keep="last")
    frame = frame.reset_index(drop=True)
    frame["contiguous_segment_id"] = frame.groupby("symbol", sort=False)[
        "candle_timestamp_utc"
    ].transform(
        lambda values: values.diff().ne(pd.Timedelta(seconds=TIMEFRAME_SECONDS)).cumsum()
    )
    rematerialized = [
        _rematerialize_segment(segment.copy())
        for _, segment in frame.groupby(
            ["symbol", "contiguous_segment_id"], sort=False, dropna=False
        )
    ]
    return pd.concat(rematerialized, ignore_index=True) if rematerialized else frame


def forbidden_feature_columns(columns: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for column in columns:
        name = str(column).casefold()
        if name in FORBIDDEN_EXACT_FEATURES or name.startswith(FORBIDDEN_FEATURE_PREFIXES):
            result.append(str(column))
    return sorted(set(result))


def _rematerialize_segment(segment: pd.DataFrame) -> pd.DataFrame:
    """Compute only backward-looking indicators inside one gap-free segment."""

    close = segment["close"].astype(float)
    high = segment["high"].astype(float)
    low = segment["low"].astype(float)
    volume = segment["volume"].astype(float)
    for periods in (1, 3, 5, 10, 15):
        segment[f"ret_{periods}"] = close.pct_change(periods, fill_method=None)
    ema_20 = close.ewm(span=20, adjust=False, min_periods=20).mean()
    ema_50 = close.ewm(span=50, adjust=False, min_periods=50).mean()
    ema_200 = close.ewm(span=200, adjust=False, min_periods=200).mean()
    segment["dist_ema20"] = close.div(ema_20).sub(1.0)
    segment["dist_ema50"] = close.div(ema_50).sub(1.0)
    segment["dist_ema200"] = close.div(ema_200).sub(1.0)

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=14).mean()
    loss = -delta.clip(upper=0).rolling(14, min_periods=14).mean()
    relative_strength = gain.div(loss.replace(0.0, np.nan))
    rsi = 100.0 - (100.0 / (1.0 + relative_strength))
    segment["rsi_14"] = rsi.mask(loss.eq(0.0) & gain.gt(0.0), 100.0).mask(
        loss.eq(0.0) & gain.eq(0.0), 50.0
    )

    ema_12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema_26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    macd = ema_12 - ema_26
    macd_signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    segment["macd_hist"] = macd - macd_signal

    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    segment["atr_pct_14"] = true_range.rolling(14, min_periods=14).mean().div(close)
    returns = close.pct_change(fill_method=None)
    segment["vol_30"] = returns.rolling(30, min_periods=30).std(ddof=0)
    segment["vol_120"] = returns.rolling(120, min_periods=120).std(ddof=0)
    volume_mean = volume.rolling(30, min_periods=30).mean()
    volume_std = volume.rolling(30, min_periods=30).std(ddof=0).replace(0.0, np.nan)
    segment["volume_rel_30"] = volume.div(volume_mean)
    segment["volume_z_30"] = volume.sub(volume_mean).div(volume_std)
    segment["trend_score"] = np.sign(ema_20 - ema_50)
    return segment


def _stable_trade_ids(master: pd.DataFrame) -> pd.Series:
    candidates = ("order_id", "_dedup_key", "_relaxed_dedup_key")
    values: list[str] = []
    for index, row in master.iterrows():
        selected = next(
            (
                str(row[name]).strip()
                for name in candidates
                if name in master.columns
                and pd.notna(row[name])
                and str(row[name]).strip()
            ),
            f"master-row-{int(index)}",
        )
        values.append(selected)
    return pd.Series(values, index=master.index, dtype="string")


def _record_missing(
    frame: pd.DataFrame,
    column: str,
    reason: str,
    output: dict[int, list[str]],
) -> None:
    for index in frame.index[frame[column].isna()]:
        output[int(index)].append(reason)


def _record_nonfinite(
    frame: pd.DataFrame,
    column: str,
    reason: str,
    output: dict[int, list[str]],
) -> None:
    values = pd.to_numeric(frame[column], errors="coerce")
    mask = ~np.isfinite(values.to_numpy(dtype=float, na_value=np.nan))
    for index in frame.index[mask]:
        output[int(index)].append(reason)


def _blocker(dataset: str, row: pd.Series, reason: str) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "source_row_number": int(row["source_row_number"]),
        "trade_id": str(row["trade_id"]) if pd.notna(row["trade_id"]) else None,
        "reason": reason,
    }
