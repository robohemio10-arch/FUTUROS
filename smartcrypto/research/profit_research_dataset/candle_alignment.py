"""Point-in-time candle loading and deterministic trade alignment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pandas as pd

from smartcrypto.research.profit_research.paper_analysis import normalize_symbol


PROHIBITED_PREFIXES: Final = ("future_ret_", "target_", "label_")


@dataclass(frozen=True)
class CandleLoadResult:
    frame: pd.DataFrame
    paths: tuple[Path, ...]
    warnings: tuple[str, ...]
    duplicate_candle_count: int


@dataclass(frozen=True)
class CandleAlignmentResult:
    frame: pd.DataFrame
    paths_by_trade: dict[str, pd.DataFrame]


def load_candles(candle_root: Path, *, timeframe: str) -> CandleLoadResult:
    paths = discover_candle_paths(candle_root)
    if not paths:
        return CandleLoadResult(pd.DataFrame(), (), ("candle_source_missing",), 0)
    frames: list[pd.DataFrame] = []
    warnings: list[str] = []
    for path in paths:
        try:
            frame = _read_table(path)
            normalized = normalize_candles(frame, default_timeframe=timeframe)
        except (OSError, ValueError, ImportError, pd.errors.ParserError) as exc:
            warnings.append(f"candle_source_unreadable:{path.name}:{type(exc).__name__}")
            continue
        normalized["source_path"] = str(path)
        frames.append(normalized)
    if not frames:
        return CandleLoadResult(pd.DataFrame(), paths, tuple(sorted(warnings)), 0)
    merged = pd.concat(frames, ignore_index=True)
    merged = merged.loc[merged["tf"].eq(timeframe.casefold())].copy()
    merged = merged.sort_values(["symbol", "tf", "ts", "source_path"])
    duplicates = int(merged.duplicated(["symbol", "tf", "ts"], keep="last").sum())
    merged = merged.drop_duplicates(["symbol", "tf", "ts"], keep="last").reset_index(drop=True)
    return CandleLoadResult(merged, paths, tuple(sorted(warnings)), duplicates)


def discover_candle_paths(root: Path) -> tuple[Path, ...]:
    if root.is_file() and root.suffix.casefold() in {".parquet", ".csv"}:
        return (root,)
    if not root.is_dir() or root.is_symlink():
        return ()
    return tuple(
        sorted(
            (
                path
                for path in root.rglob("*")
                if path.is_file()
                and not path.is_symlink()
                and path.suffix.casefold() in {".parquet", ".csv"}
            ),
            key=lambda item: item.as_posix().casefold(),
        )
    )


def normalize_candles(frame: pd.DataFrame, *, default_timeframe: str) -> pd.DataFrame:
    prohibited = sorted(
        str(column)
        for column in frame.columns
        if str(column).casefold().startswith(PROHIBITED_PREFIXES)
    )
    if prohibited:
        raise ValueError("candle_lookahead_columns:" + ",".join(prohibited))
    aliases = {
        "symbol": ("symbol", "pair", "moeda"),
        "ts": ("ts", "timestamp", "date", "datetime", "open_time"),
        "open": ("open",),
        "high": ("high",),
        "low": ("low",),
        "close": ("close",),
        "volume": ("volume", "quote_volume"),
        "tf": ("tf", "timeframe"),
    }
    output = pd.DataFrame(index=frame.index)
    for target, candidates in aliases.items():
        source = next((name for name in candidates if name in frame.columns), None)
        if source is None:
            if target == "tf":
                output[target] = default_timeframe
                continue
            if target == "volume":
                output[target] = pd.NA
                continue
            raise ValueError(f"candle_required_column_missing:{target}")
        output[target] = frame[source]
    output["symbol"] = output["symbol"].map(normalize_symbol)
    output["tf"] = output["tf"].astype("string").str.casefold().fillna(default_timeframe)
    output["ts"] = pd.to_datetime(output["ts"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        output[column] = pd.to_numeric(output[column], errors="coerce")
    output = output.dropna(subset=["symbol", "ts", "open", "high", "low", "close"])
    invalid_ohlc = (
        output["high"].lt(output[["open", "close", "low"]].max(axis=1))
        | output["low"].gt(output[["open", "close", "high"]].min(axis=1))
    )
    return output.loc[~invalid_ohlc].reset_index(drop=True)


def align_trades_to_candles(
    trades: pd.DataFrame,
    candles: pd.DataFrame,
    *,
    timeframe: str,
    tolerance_seconds: float | None = None,
) -> CandleAlignmentResult:
    output = trades.copy()
    timestamp_columns = (
        "entry_candle_timestamp_utc",
        "close_candle_timestamp_utc",
    )
    for column in timestamp_columns:
        output[column] = pd.Series(pd.NaT, index=output.index, dtype="datetime64[ns, UTC]")
    output["entry_candle_distance_seconds"] = pd.NA
    output["close_candle_distance_seconds"] = pd.NA
    output["candle_timeframe"] = timeframe.casefold()
    output["candle_alignment_status"] = "not_evaluated"
    output["candle_missing_reason"] = pd.Series(pd.NA, index=output.index, dtype="string")
    output["intratrade_candle_count"] = 0
    seconds = timeframe_seconds(timeframe)
    tolerance = float(tolerance_seconds if tolerance_seconds is not None else seconds)
    paths_by_trade: dict[str, pd.DataFrame] = {}
    groups = {
        str(symbol): group.sort_values("ts").reset_index(drop=True)
        for symbol, group in candles.groupby("symbol", sort=True)
    }
    for index, trade in output.iterrows():
        if not bool(trade.get("analysis_eligible", False)):
            output.at[index, "candle_alignment_status"] = "rejected_trade"
            output.at[index, "candle_missing_reason"] = "trade_not_eligible"
            continue
        stable_id = str(trade["stable_trade_id"])
        source = groups.get(str(trade["symbol"]))
        open_time = pd.Timestamp(trade["open_time_utc"])
        close_time = pd.Timestamp(trade["close_time_utc"])
        if source is None or source.empty:
            _mark_missing(output, index, "symbol_candles_missing")
            continue
        if open_time < source["ts"].min() or close_time > source["ts"].max() + pd.Timedelta(seconds=seconds):
            _mark_missing(output, index, "trade_outside_candle_coverage")
            continue
        entry_position = int(source["ts"].searchsorted(open_time, side="right")) - 1
        close_position = int(source["ts"].searchsorted(close_time, side="right")) - 1
        if entry_position < 0:
            _mark_missing(output, index, "entry_candle_missing")
            continue
        if close_position < entry_position:
            _mark_missing(output, index, "close_candle_missing")
            continue
        entry_ts = pd.Timestamp(source.iloc[entry_position]["ts"])
        close_ts = pd.Timestamp(source.iloc[close_position]["ts"])
        entry_distance = float((open_time - entry_ts).total_seconds())
        close_distance = float((close_time - close_ts).total_seconds())
        if entry_distance > tolerance:
            _mark_missing(output, index, "entry_candle_tolerance_exceeded")
            continue
        if close_distance > tolerance:
            _mark_missing(output, index, "close_candle_tolerance_exceeded")
            continue
        path = source.iloc[entry_position : close_position + 1].copy().reset_index(drop=True)
        if path.empty:
            _mark_missing(output, index, "intratrade_path_missing")
            continue
        output.at[index, "entry_candle_timestamp_utc"] = entry_ts
        output.at[index, "close_candle_timestamp_utc"] = close_ts
        output.at[index, "entry_candle_distance_seconds"] = entry_distance
        output.at[index, "close_candle_distance_seconds"] = close_distance
        output.at[index, "candle_alignment_status"] = "aligned"
        output.at[index, "intratrade_candle_count"] = int(len(path))
        paths_by_trade[stable_id] = path
    return CandleAlignmentResult(output, paths_by_trade)


def timeframe_seconds(value: str) -> int:
    text = value.strip().casefold()
    if text.endswith("m") and text[:-1].isdigit():
        return int(text[:-1]) * 60
    if text.endswith("h") and text[:-1].isdigit():
        return int(text[:-1]) * 3600
    if text.endswith("s") and text[:-1].isdigit():
        return int(text[:-1])
    raise ValueError(f"unsupported_timeframe:{value}")


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.casefold() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.casefold() == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"unsupported_candle_format:{path.suffix}")


def _mark_missing(frame: pd.DataFrame, index: int, reason: str) -> None:
    frame.at[index, "candle_alignment_status"] = "unaligned"
    frame.at[index, "candle_missing_reason"] = reason
