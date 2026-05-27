from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


STANDARD_COLUMNS = [
    "moeda",
    "fechar_side",
    "leverage",
    "order_id",
    "pnl_fechado",
    "taxa_lucros_perdas_fechados_pct",
    "preco_abertura",
    "preco_fechamento",
    "volume_posicao",
    "volume_fechado",
    "horario_abertura",
    "horario_fechamento",
    "taxa_1",
    "preco_transacao",
    "volume_transacao",
    "direcao_liquidez",
    "taxa_2",
    "horario_transacao",
]


SNAPSHOT_COLUMNS = [
    "ts",
    "close",
    "ret_1",
    "ret_3",
    "ret_5",
    "ret_10",
    "ret_15",
    "ema_20",
    "ema_50",
    "ema_200",
    "dist_ema20",
    "dist_ema50",
    "dist_ema200",
    "rsi_14",
    "macd_hist",
    "atr_pct_14",
    "vol_30",
    "vol_120",
    "volume_rel_30",
    "volume_z_30",
    "trend_score",
    "market_regime",
]


def read_table(path: str | Path) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(target)
    suffix = target.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(target)
    if suffix == ".csv":
        return pd.read_csv(target)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(target)
    raise ValueError(f"Unsupported file: {target}")


def ensure_parent(path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def write_parquet(frame: pd.DataFrame, path: str | Path) -> None:
    ensure_parent(path)
    frame.to_parquet(path, index=False)


def write_sqlite(frame: pd.DataFrame, sqlite_path: str | Path, table: str) -> None:
    ensure_parent(sqlite_path)
    sqlite_frame = frame.copy()
    for column in sqlite_frame.columns:
        if pd.api.types.is_datetime64_any_dtype(sqlite_frame[column]):
            sqlite_frame[column] = sqlite_frame[column].astype(str)
    with sqlite3.connect(sqlite_path) as connection:
        sqlite_frame.to_sql(table, connection, if_exists="replace", index=False)


def coerce_float(value: Any) -> float:
    if value is None:
        return float("nan")
    try:
        if pd.isna(value):
            return float("nan")
    except Exception:
        pass
    if isinstance(value, str):
        value = value.strip().replace("%", "").replace(",", ".")
    try:
        result = float(value)
    except Exception:
        return float("nan")
    return result if math.isfinite(result) else float("nan")


def coerce_datetime(value: Any) -> pd.Timestamp:
    parsed = parse_trade_timestamp_series(pd.Series([value]))
    return parsed.iloc[0]


ISO_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$"
)
BR_TIMESTAMP_RE = re.compile(
    r"^\d{2}[/-]\d{2}[/-]\d{4}[ T]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?$"
)


def parse_trade_timestamp_series(series: pd.Series) -> pd.Series:
    values = pd.Series(series).copy()
    result = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns, UTC]")

    text = values.astype("string").str.strip()
    present = text.notna() & text.ne("") & text.str.lower().ne("nan")

    iso_mask = present & text.map(lambda value: bool(ISO_TIMESTAMP_RE.match(str(value))))
    br_mask = present & ~iso_mask & text.map(lambda value: bool(BR_TIMESTAMP_RE.match(str(value))))
    other_mask = present & ~iso_mask & ~br_mask

    if iso_mask.any():
        result.loc[iso_mask] = pd.to_datetime(
            text.loc[iso_mask],
            errors="coerce",
            utc=True,
            dayfirst=False,
            format="mixed",
        )
    if br_mask.any():
        result.loc[br_mask] = pd.to_datetime(
            text.loc[br_mask],
            errors="coerce",
            utc=True,
            dayfirst=True,
            format="mixed",
        )
    if other_mask.any():
        result.loc[other_mask] = pd.to_datetime(
            values.loc[other_mask],
            errors="coerce",
            utc=True,
            dayfirst=False,
        )

    return result


def normalize_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text or text == "NAN":
        return ""
    text = text.replace("/USDT:USDT", "USDT").replace("/", "").replace(":", "").replace("-", "")
    text = text.replace("PERP", "").replace("USDTUSDT", "USDT")
    if text.endswith("USDT"):
        return text
    return f"{text}USDT"


def make_trade_fingerprint(row: pd.Series) -> str:
    parts = [
        str(row.get("moeda", "")),
        str(row.get("fechar_side", "")),
        str(row.get("preco_abertura", "")),
        str(row.get("preco_fechamento", "")),
        str(row.get("horario_abertura", "")),
        str(row.get("horario_fechamento", "")),
        str(row.get("volume_posicao", "")),
        str(row.get("pnl_fechado", "")),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def normalize_trades(frame: pd.DataFrame) -> pd.DataFrame:
    trades = frame.copy()

    for column in STANDARD_COLUMNS:
        if column not in trades.columns:
            trades[column] = pd.NA

    trades["symbol"] = trades["moeda"].map(normalize_symbol)
    trades["open_ts"] = parse_trade_timestamp_series(trades["horario_abertura"])
    trades["close_ts"] = parse_trade_timestamp_series(trades["horario_fechamento"])
    trades["entry_price"] = trades["preco_abertura"].map(coerce_float)
    trades["exit_price"] = trades["preco_fechamento"].map(coerce_float)
    trades["pnl"] = trades["pnl_fechado"].map(coerce_float)
    trades["return_pct"] = trades["taxa_lucros_perdas_fechados_pct"].map(coerce_float)
    trades["duration_seconds"] = (trades["close_ts"] - trades["open_ts"]).dt.total_seconds()
    trades["target_win"] = (trades["pnl"].fillna(0.0) > 0).astype(int)

    raw_id = trades["order_id"].astype(str).str.strip()
    missing_id = raw_id.eq("") | raw_id.str.lower().eq("nan") | raw_id.isna()
    generated_id = trades.apply(make_trade_fingerprint, axis=1)
    trades["trade_id"] = raw_id.where(~missing_id, generated_id).astype(str)

    valid = (
        trades["symbol"].astype(str).str.len().gt(0)
        & trades["open_ts"].notna()
        & trades["close_ts"].notna()
        & trades["entry_price"].notna()
        & trades["exit_price"].notna()
    )
    return trades.loc[valid].reset_index(drop=True)


def normalize_features(frame: pd.DataFrame) -> pd.DataFrame:
    features = frame.copy()

    if "symbol" not in features.columns:
        if "pair" in features.columns:
            features["symbol"] = features["pair"].map(normalize_symbol)
        else:
            features["symbol"] = ""
    else:
        features["symbol"] = features["symbol"].map(normalize_symbol)

    if "tf" not in features.columns:
        features["tf"] = "5m"

    if "ts" not in features.columns and "date" in features.columns:
        features["ts"] = features["date"]

    features["ts"] = parse_trade_timestamp_series(features["ts"])
    features = features[features["symbol"].astype(str).str.len().gt(0) & features["ts"].notna()].copy()
    return features.sort_values(["symbol", "tf", "ts"]).reset_index(drop=True)


def snapshot_before(features: pd.DataFrame, symbol: str, tf: str, ts: pd.Timestamp) -> dict[str, Any]:
    if not symbol or pd.isna(ts):
        return {}
    subset = features[(features["symbol"] == symbol) & (features["tf"].astype(str) == tf)]
    if subset.empty:
        return {}
    subset = subset[subset["ts"] <= ts]
    if subset.empty:
        return {}
    row = subset.iloc[-1]
    return {column: row[column] for column in SNAPSHOT_COLUMNS if column in subset.columns}


def add_feature_snapshots(trades: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    enriched = trades.copy()

    for tf in ("1m", "5m"):
        for moment, ts_column in (("open", "open_ts"), ("close", "close_ts")):
            rows = []
            for _, trade in enriched.iterrows():
                snap = snapshot_before(features, str(trade["symbol"]), tf, trade[ts_column])
                rows.append({f"{moment}_{tf}_{key}": value for key, value in snap.items()})
            enriched = pd.concat([enriched.reset_index(drop=True), pd.DataFrame(rows).reset_index(drop=True)], axis=1)

    return enriched


def compute_path_metrics(trades: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    columns = ["trade_id", "mfe_pct", "mae_pct", "path_candles"]

    if trades.empty:
        return pd.DataFrame(columns=columns)

    one_minute = features[features["tf"].astype(str) == "1m"].copy()
    results: list[dict[str, Any]] = []

    for _, trade in trades.iterrows():
        result = {
            "trade_id": str(trade.get("trade_id", "")),
            "mfe_pct": pd.NA,
            "mae_pct": pd.NA,
            "path_candles": 0,
        }

        symbol = str(trade.get("symbol", ""))
        open_ts = trade.get("open_ts", pd.NaT)
        close_ts = trade.get("close_ts", pd.NaT)
        entry_price = coerce_float(trade.get("entry_price"))

        if not symbol or pd.isna(open_ts) or pd.isna(close_ts) or not math.isfinite(entry_price) or entry_price == 0:
            results.append(result)
            continue

        path = one_minute[
            (one_minute["symbol"] == symbol)
            & (one_minute["ts"] >= open_ts)
            & (one_minute["ts"] <= close_ts)
        ]

        if path.empty or "close" not in path.columns:
            results.append(result)
            continue

        closes = pd.to_numeric(path["close"], errors="coerce").dropna()
        if closes.empty:
            results.append(result)
            continue

        returns = (closes / entry_price) - 1.0
        side = str(trade.get("fechar_side", "")).lower()
        if "short" in side or "sell" in side or "venda" in side:
            returns = -returns

        result["mfe_pct"] = float(returns.max() * 100.0)
        result["mae_pct"] = float(returns.min() * 100.0)
        result["path_candles"] = int(len(path))
        results.append(result)

    return pd.DataFrame(results, columns=columns)


def build_trade_enriched(
    trades_path: str | Path,
    features_path: str | Path,
    output_path: str | Path,
    sqlite_path: str | Path,
) -> pd.DataFrame:
    trades = normalize_trades(read_table(trades_path))
    features = normalize_features(read_table(features_path))

    enriched = add_feature_snapshots(trades, features)

    if "trade_id" not in enriched.columns:
        enriched["trade_id"] = pd.Series(dtype="string")

    path_metrics = compute_path_metrics(trades, features)
    if "trade_id" not in path_metrics.columns:
        path_metrics = pd.DataFrame(columns=["trade_id", "mfe_pct", "mae_pct", "path_candles"])

    enriched = enriched.merge(path_metrics, on="trade_id", how="left")
    enriched["created_at"] = datetime.now(timezone.utc).isoformat()

    write_parquet(enriched, output_path)
    write_sqlite(enriched, sqlite_path, "trade_enriched")
    return enriched


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", default="data/trades/trades_excel.xlsx")
    parser.add_argument("--features", default="data/features/market_features_60d.parquet")
    parser.add_argument("--output", default="data/features/trade_enriched.parquet")
    parser.add_argument("--sqlite", default="data/sqlite/trading_dataset.sqlite")
    args = parser.parse_args()

    enriched = build_trade_enriched(args.trades, args.features, args.output, args.sqlite)
    print(json.dumps({
        "status": "ok",
        "rows": int(len(enriched)),
        "columns": int(len(enriched.columns)),
        "output": args.output,
        "sqlite_table": "trade_enriched",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
