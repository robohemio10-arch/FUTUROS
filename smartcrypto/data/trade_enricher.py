from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from smartcrypto.data.symbols import normalize_symbol, to_freqtrade_pair
from smartcrypto.data.trade_schema import build_column_mapping, missing_required_columns


BRAZIL_TZ = "America/Sao_Paulo"
ENTRY_FEATURE_COLUMNS = [
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
    "volume_rel_30",
    "volume_z_30",
    "hl_range",
    "body_range",
    "upper_wick",
    "lower_wick",
    "trend_score",
    "market_regime",
]


def validate_trades_excel(path: str | Path, report_path: str | Path | None = None) -> dict:
    source = Path(path)
    result = {
        "path": str(source),
        "exists": source.exists(),
        "status": "missing",
        "rows": 0,
        "columns": [],
        "missing_required": [],
        "message": "",
    }

    if not source.exists():
        result["message"] = "Coloque sua planilha OCR consolidada em data/trades/trades_excel.xlsx."
        return _write_report(result, report_path)

    frame = _read_table(source)
    result["rows"] = int(len(frame))
    result["columns"] = [str(column) for column in frame.columns]
    result["missing_required"] = missing_required_columns(result["columns"])

    if result["rows"] == 0:
        result["status"] = "empty"
        result["message"] = "A planilha existe, mas não contém linhas de trades."
        return _write_report(result, report_path)

    if result["missing_required"]:
        result["status"] = "invalid"
        result["message"] = "Colunas obrigatórias ausentes."
        return _write_report(result, report_path)

    normalized = normalize_trades(frame)
    result["valid_rows"] = int(len(normalized))
    result["invalid_open_time"] = int(normalized["open_time"].isna().sum())
    result["invalid_close_time"] = int(normalized["close_time"].isna().sum())
    result["invalid_entry_price"] = int(normalized["entry_price"].isna().sum())
    result["invalid_exit_price"] = int(normalized["exit_price"].isna().sum())
    result["symbols"] = sorted(normalized["symbol"].dropna().unique().tolist())

    blocking = [
        result["invalid_open_time"],
        result["invalid_close_time"],
        result["invalid_entry_price"],
        result["invalid_exit_price"],
    ]

    result["status"] = "ok" if sum(blocking) == 0 else "warning"
    result["message"] = "Planilha validada." if result["status"] == "ok" else "Planilha lida com pendências de parsing."
    return _write_report(result, report_path)


def build_trade_enriched(
    trades_path: str | Path,
    market_features_path: str | Path,
    output_path: str | Path,
    sqlite_path: str | Path | None = None,
    report_path: str | Path | None = None,
) -> pd.DataFrame:
    trades_source = Path(trades_path)
    features_source = Path(market_features_path)

    if not trades_source.exists():
        raise FileNotFoundError(f"Planilha não encontrada: {trades_source}")

    if not features_source.exists():
        raise FileNotFoundError(f"market_features não encontrado: {features_source}")

    trades = normalize_trades(_read_table(trades_source))
    features = pd.read_parquet(features_source)
    features = _prepare_features(features)

    enriched = trades.copy()
    enriched["freqtrade_pair"] = enriched["symbol"].map(to_freqtrade_pair)
    enriched["duration_seconds"] = (enriched["close_time"] - enriched["open_time"]).dt.total_seconds()
    enriched["return_pct"] = _trade_return(enriched)
    enriched["is_win"] = _build_win_label(enriched)
    enriched["signed_pnl"] = enriched["pnl"].fillna(enriched["return_pct"])

    for timeframe in ["1m", "5m"]:
        enriched = _merge_asof_features(enriched, features, timeframe, "entry")
        enriched = _merge_asof_features(enriched, features, timeframe, "exit")

    one_minute = features[features["tf"].eq("1m")].copy()
    excursions = [_compute_excursion(row, one_minute) for _, row in enriched.iterrows()]
    excursion_frame = pd.DataFrame(excursions)
    enriched = pd.concat([enriched.reset_index(drop=True), excursion_frame.reset_index(drop=True)], axis=1)

    enriched["quality_flag"] = _quality_flag(enriched)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    enriched.to_parquet(output, index=False)

    if sqlite_path:
        sqlite_target = Path(sqlite_path)
        sqlite_target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(sqlite_target) as con:
            enriched.to_sql("trade_enriched", con, if_exists="replace", index=False)

    summary = {
        "status": "ok",
        "rows": int(len(enriched)),
        "columns": int(len(enriched.columns)),
        "symbols": sorted(enriched["symbol"].dropna().unique().tolist()),
        "wins": int(enriched["is_win"].fillna(False).sum()),
        "losses": int((enriched["is_win"] == False).sum()),
        "output": str(output),
        "sqlite": str(sqlite_path) if sqlite_path else None,
    }
    _write_report(summary, report_path)

    return enriched


def normalize_trades(frame: pd.DataFrame) -> pd.DataFrame:
    mappings = build_column_mapping([str(column) for column in frame.columns])
    if not mappings:
        raise ValueError("Nenhuma coluna reconhecida na planilha de trades.")

    renamed = {}
    for mapping in mappings:
        renamed[mapping.source] = mapping.canonical

    data = frame.rename(columns=renamed).copy()
    missing = missing_required_columns([str(column) for column in frame.columns])
    if missing:
        raise ValueError(f"Colunas obrigatórias ausentes: {missing}")

    normalized = pd.DataFrame()
    normalized["source_row"] = np.arange(2, len(data) + 2)
    normalized["symbol"] = data["symbol"].map(normalize_symbol)
    normalized["open_time"] = _parse_datetime(data["open_time"])
    normalized["close_time"] = _parse_datetime(data["close_time"])
    normalized["entry_price"] = _parse_number(data["entry_price"])
    normalized["exit_price"] = _parse_number(data["exit_price"])
    normalized["side"] = _derive_side(data)
    normalized["pnl"] = _parse_number(data["pnl"]) if "pnl" in data else np.nan
    normalized["pnl_pct"] = _parse_number(data["pnl_pct"]) if "pnl_pct" in data else np.nan
    normalized["leverage"] = _parse_number(data["leverage"]) if "leverage" in data else np.nan
    normalized["order_id"] = data["order_id"].astype(str) if "order_id" in data else ""
    normalized["position_volume"] = _parse_number(data["position_volume"]) if "position_volume" in data else np.nan
    normalized["closed_volume"] = _parse_number(data["closed_volume"]) if "closed_volume" in data else np.nan
    normalized["close_side"] = data["close_side"].astype(str) if "close_side" in data else ""

    normalized = normalized[normalized["symbol"].isin(["BTCUSDT", "ETHUSDT"])].copy()
    normalized = normalized.sort_values(["symbol", "open_time"]).reset_index(drop=True)
    return normalized


def _prepare_features(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    data["symbol"] = data["symbol"].map(normalize_symbol)
    data["ts"] = pd.to_datetime(data["ts"], utc=True, errors="coerce")
    data = data.dropna(subset=["ts", "symbol", "tf"])
    return data.sort_values(["symbol", "tf", "ts"]).reset_index(drop=True)


def _merge_asof_features(trades: pd.DataFrame, features: pd.DataFrame, timeframe: str, moment: str) -> pd.DataFrame:
    time_column = "open_time" if moment == "entry" else "close_time"
    suffix = f"_{moment}_{timeframe}"
    selected_columns = ["symbol", "ts"] + [column for column in ENTRY_FEATURE_COLUMNS if column in features.columns]
    market = features[features["tf"].eq(timeframe)][selected_columns].copy()
    market = market.sort_values(["symbol", "ts"])

    parts = []
    for symbol, trade_group in trades.groupby("symbol", sort=False):
        trade_part = trade_group.copy().sort_values(time_column)
        market_part = market[market["symbol"].eq(symbol)].sort_values("ts")

        if market_part.empty:
            for column in selected_columns[2:]:
                trade_part[f"{column}{suffix}"] = np.nan
            parts.append(trade_part)
            continue

        merged = pd.merge_asof(
            trade_part,
            market_part.drop(columns=["symbol"]),
            left_on=time_column,
            right_on="ts",
            direction="backward",
            tolerance=pd.Timedelta("30min"),
        )
        merged = merged.drop(columns=["ts"], errors="ignore")
        merged = merged.rename(columns={column: f"{column}{suffix}" for column in selected_columns[2:]})
        parts.append(merged)

    return pd.concat(parts, ignore_index=True).sort_values(["symbol", "open_time"]).reset_index(drop=True)


def _compute_excursion(trade: pd.Series, one_minute_features: pd.DataFrame) -> dict:
    symbol = trade.get("symbol")
    open_time = trade.get("open_time")
    close_time = trade.get("close_time")
    entry = trade.get("entry_price")
    side = str(trade.get("side", "")).lower()

    empty = {
        "mfe_pct": np.nan,
        "mae_pct": np.nan,
        "max_drawdown_pct": np.nan,
        "bars_in_trade": 0,
    }

    if pd.isna(open_time) or pd.isna(close_time) or pd.isna(entry) or not symbol:
        return empty

    market = one_minute_features[
        one_minute_features["symbol"].eq(symbol)
        & one_minute_features["ts"].ge(open_time)
        & one_minute_features["ts"].le(close_time)
    ]

    if market.empty:
        return empty

    if side == "short":
        favorable = (entry - market["low"]) / entry
        adverse = (entry - market["high"]) / entry
        equity_curve = (entry - market["close"]) / entry
    else:
        favorable = (market["high"] - entry) / entry
        adverse = (market["low"] - entry) / entry
        equity_curve = (market["close"] - entry) / entry

    running_max = equity_curve.cummax()
    drawdown = equity_curve - running_max

    return {
        "mfe_pct": float(favorable.max()),
        "mae_pct": float(adverse.min()),
        "max_drawdown_pct": float(drawdown.min()),
        "bars_in_trade": int(len(market)),
    }


def _trade_return(frame: pd.DataFrame) -> pd.Series:
    raw = (frame["exit_price"] - frame["entry_price"]) / frame["entry_price"]
    side = frame["side"].fillna("long").astype(str).str.lower()
    return np.where(side.eq("short"), -raw, raw)


def _build_win_label(frame: pd.DataFrame) -> pd.Series:
    if "pnl" in frame and frame["pnl"].notna().any():
        return frame["pnl"] > 0
    return frame["return_pct"] > 0


def _quality_flag(frame: pd.DataFrame) -> pd.Series:
    flags = []
    for _, row in frame.iterrows():
        issues = []
        if pd.isna(row.get("open_time")):
            issues.append("invalid_open_time")
        if pd.isna(row.get("close_time")):
            issues.append("invalid_close_time")
        if row.get("duration_seconds", 0) < 0:
            issues.append("negative_duration")
        if pd.isna(row.get("entry_price")):
            issues.append("invalid_entry_price")
        if pd.isna(row.get("exit_price")):
            issues.append("invalid_exit_price")
        flags.append("ok" if not issues else ",".join(issues))
    return pd.Series(flags)


def _derive_side(frame: pd.DataFrame) -> pd.Series:
    if "side" in frame:
        raw = frame["side"].astype(str)
        side = raw.map(_normalize_side)
        if side.notna().any():
            return side.fillna("long")

    if "close_side" in frame:
        close_side = frame["close_side"].astype(str).str.lower()
        short_close = close_side.str.contains("buy|compr|short", regex=True, na=False)
        long_close = close_side.str.contains("sell|vend|long", regex=True, na=False)
        return np.where(short_close, "short", np.where(long_close, "long", "long"))

    return pd.Series(["long"] * len(frame))


def _normalize_side(value: object) -> str | None:
    text = str(value).strip().lower()
    if any(token in text for token in ["short", "vendido", "sell"]):
        return "short"
    if any(token in text for token in ["long", "comprado", "buy"]):
        return "long"
    return None


def _parse_datetime(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce", dayfirst=True)
    if parsed.dt.tz is None:
        parsed = parsed.dt.tz_localize(BRAZIL_TZ, nonexistent="shift_forward", ambiguous="NaT")
    return parsed.dt.tz_convert("UTC")


def _parse_number(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip()
    text = text.str.replace("\u00a0", "", regex=False)
    text = text.str.replace("%", "", regex=False)
    text = text.str.replace("USDT", "", regex=False)
    text = text.str.replace("$", "", regex=False)
    text = text.str.replace("R$", "", regex=False)
    text = text.str.replace(" ", "", regex=False)
    text = text.str.replace(".", "", regex=False)
    text = text.str.replace(",", ".", regex=False)
    text = text.replace({"": np.nan, "nan": np.nan, "None": np.nan})
    return pd.to_numeric(text, errors="coerce")


def _read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        return pd.read_excel(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    raise ValueError(f"Formato não suportado: {path}")


def _write_report(payload: dict, report_path: str | Path | None) -> dict:
    if report_path:
        target = Path(report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload
