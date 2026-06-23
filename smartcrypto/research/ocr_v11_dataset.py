"""Read-only OCR V1.1 trade research dataset and candle alignment."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.research.reporting import build_executive_summary, render_executive_markdown


EXPECTED_MASTER_SHA256 = "83e2d17db317cc84b2bd39e00a961bd8d568c4375c5a4a113f6a26df58972e90"
EXPECTED_MASTER_ROWS = 3058
DEFAULT_WORKERS = 10
DEFAULT_MAX_RAM_GB = 16.0
ONE_MINUTE_NS = 60 * 1_000_000_000
PRICE_GUARDS = {
    "BTCUSDT": (10_000.0, 300_000.0),
    "ETHUSDT": (500.0, 20_000.0),
}
SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "runs_ocr": False,
    "imports_ocr": False,
    "promotes_quality_gated": False,
    "runs_ai_shadow_incremental": False,
    "cleans_sqlite": False,
}

CANONICAL_TRADE_COLUMNS = (
    "moeda",
    "fechar_side",
    "order_id",
    "pnl_fechado",
    "taxa_lucros_perdas_fechados_pct",
    "preco_abertura",
    "preco_fechamento",
    "volume_posicao",
    "volume_fechado",
    "horario_abertura",
    "horario_fechamento",
)
RAW_TO_CANONICAL = {
    "11_moeda": "moeda",
    "12_fechar_long_short": "fechar_side",
    "10_numero_do_pedido": "order_id",
    "1_pnl_fechado": "pnl_fechado",
    "2_taxa_lucros_perdas_fechados": "taxa_lucros_perdas_fechados_pct",
    "3_preco_de_abertura": "preco_abertura",
    "4_preco_de_fechamento": "preco_fechamento",
    "5_volume_de_posicao": "volume_posicao",
    "6_volume_fechado": "volume_fechado",
    "7_horario_de_abertura": "horario_abertura",
    "8_horario_de_fechamento": "horario_fechamento",
    "9_taxa": "taxa_1",
}

OUTPUT_COLUMNS = (
    "trade_id",
    "source_file",
    "order_id",
    "symbol",
    "side",
    "open_time",
    "close_time",
    "duration_seconds",
    "duration_minutes",
    "entry_price",
    "exit_price",
    "volume_position",
    "volume_closed",
    "fees_total",
    "net_pnl",
    "net_pnl_pct",
    "is_win",
    "entry_candle_timestamp",
    "exit_candle_timestamp",
    "entry_feature_timestamp",
    "entry_candle_found",
    "exit_candle_found",
    "candles_between_count",
    "missing_candle_count",
    "candle_alignment_status",
    "entry_open",
    "entry_high",
    "entry_low",
    "entry_close",
    "entry_volume",
    "entry_return_1m",
    "entry_return_5m",
    "entry_volatility_5m",
    "entry_volatility_15m",
    "entry_ema_fast",
    "entry_ema_slow",
    "entry_ema_distance",
    "entry_rsi",
    "entry_trend_direction",
    "entry_market_regime",
    "mfe_abs",
    "mae_abs",
    "mfe_pct",
    "mae_pct",
    "max_favorable_price",
    "max_adverse_price",
    "time_to_mfe_seconds",
    "time_to_mae_seconds",
    "opposite_side_pnl_estimate",
    "opposite_side_would_win",
    "actual_side_vs_opposite_delta",
    "has_valid_symbol",
    "has_valid_side",
    "has_valid_times",
    "has_valid_prices",
    "has_valid_pnl",
    "is_research_eligible",
    "research_block_reason",
)


@dataclass(frozen=True)
class ResearchPaths:
    project_root: Path
    master_xlsx: Path
    master_projection: Path | None
    candles_path: Path | None
    output_path: Path
    report_path: Path
    executive_summary_path: Path
    executive_markdown_path: Path


@dataclass(frozen=True)
class ResearchBuildResult:
    dataset: pd.DataFrame
    report: dict[str, Any]


def configured_workers() -> int:
    try:
        return max(1, int(os.getenv("SMARTCRYPTO_TRAINING_WORKERS", str(DEFAULT_WORKERS))))
    except ValueError:
        return DEFAULT_WORKERS


def configured_max_ram_gb() -> float:
    try:
        return max(1.0, float(os.getenv("SMARTCRYPTO_TRAINING_MAX_RAM_GB", str(DEFAULT_MAX_RAM_GB))))
    except ValueError:
        return DEFAULT_MAX_RAM_GB


def resolve_paths(
    project_root: str | Path,
    *,
    master_path: str | Path | None = None,
    master_projection_path: str | Path | None = None,
    candles_path: str | Path | None = None,
    output_path: str | Path | None = None,
    report_path: str | Path | None = None,
    executive_reports_dir: str | Path | None = None,
) -> ResearchPaths:
    root = Path(project_root).expanduser().resolve()
    master = (
        Path(master_path).expanduser().resolve()
        if master_path
        else root / "data" / "trades" / "trades_master.xlsx"
    )
    projection_candidates = (
        [Path(master_projection_path).expanduser().resolve()]
        if master_projection_path
        else [root / "data" / "trades" / "trades_master.parquet"]
    )
    candle_candidates = (
        [Path(candles_path).expanduser().resolve()]
        if candles_path
        else [
            root / "data" / "features" / "market_features_60d.parquet",
            root / "data" / "raw" / "futures_ohlcv_60d.parquet",
        ]
    )
    projection = next((path for path in projection_candidates if path.exists()), None)
    candles = next((path for path in candle_candidates if path.exists()), None)
    output = (
        Path(output_path).expanduser().resolve()
        if output_path
        else root / "data" / "research" / "ocr_v11_trade_research_dataset.parquet"
    )
    report = (
        Path(report_path).expanduser().resolve()
        if report_path
        else root / "data" / "reports" / "ocr_v11_research_dataset_audit.json"
    )
    executive_dir = (
        Path(executive_reports_dir).expanduser().resolve()
        if executive_reports_dir
        else root / "data" / "reports" / "training_reports"
    )
    return ResearchPaths(
        root,
        master,
        projection,
        candles,
        output,
        report,
        executive_dir / "ocr_v11_research_dataset_summary.json",
        executive_dir / "ocr_v11_research_dataset_executive.md",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.casefold()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    raise ValueError(f"unsupported_table_format:{path}")


def normalize_symbol(value: object) -> str:
    text = str(value or "").strip().upper().replace("_", "").replace("/", "")
    return text.replace(":USDT", "")


def normalize_side(value: object) -> str:
    text = str(value or "").strip().casefold().replace("fechar", "").strip()
    if "long" in text:
        return "long"
    if "short" in text:
        return "short"
    return "unknown"


def numeric_value(value: object) -> float:
    if value is None or value is pd.NA:
        return np.nan
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value) if np.isfinite(value) else np.nan
    text = str(value).strip()
    if not text or text.casefold() in {"nan", "none", "<na>"}:
        return np.nan
    text = re.sub(r"[^0-9,.+\-]", "", text)
    if not text:
        return np.nan
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        result = float(text)
    except ValueError:
        return np.nan
    return result if np.isfinite(result) else np.nan


def numeric_series(series: pd.Series) -> pd.Series:
    return series.map(numeric_value).astype(float)


def first_series(frame: pd.DataFrame, candidates: tuple[str, ...], default: object = pd.NA) -> pd.Series:
    result = pd.Series(default, index=frame.index, dtype="object")
    for column in candidates:
        if column not in frame.columns:
            continue
        candidate = frame[column]
        present = candidate.notna() & candidate.astype(str).str.strip().ne("")
        result = result.where(~present, candidate)
    return result


def canonical_projection(authority: pd.DataFrame, projection: pd.DataFrame | None) -> pd.DataFrame:
    if projection is not None and len(projection) == len(authority):
        if set(CANONICAL_TRADE_COLUMNS).issubset(projection.columns):
            return projection.reset_index(drop=True).copy()
    if set(CANONICAL_TRADE_COLUMNS).issubset(authority.columns):
        return authority.reset_index(drop=True).copy()
    missing_raw = sorted(set(RAW_TO_CANONICAL) - set(authority.columns))
    if missing_raw:
        raise ValueError("missing_master_columns:" + ",".join(missing_raw))
    canonical = pd.DataFrame(index=authority.index)
    for source, destination in RAW_TO_CANONICAL.items():
        canonical[destination] = authority[source]
    canonical["source_file"] = first_series(
        authority,
        ("imagem", "candidate_source", "source_full_run_xlsx"),
    )
    canonical["_dedup_key"] = first_series(
        authority,
        ("fingerprint_operacional", "imagem_sha256", "10_numero_do_pedido"),
    )
    canonical["taxa_2"] = pd.NA
    return canonical.reset_index(drop=True)


def deterministic_trade_ids(frame: pd.DataFrame) -> pd.Series:
    preferred = first_series(frame, ("_dedup_key", "fingerprint_operacional", "order_id"))
    output: list[str] = []
    for index, value in preferred.items():
        text = "" if pd.isna(value) else str(value).strip()
        if text:
            output.append(text)
            continue
        payload = "|".join(
            str(frame.at[index, column]) if column in frame.columns else ""
            for column in (
                "moeda",
                "fechar_side",
                "horario_abertura",
                "horario_fechamento",
                "preco_abertura",
                "preco_fechamento",
            )
        )
        output.append("research-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24])
    return pd.Series(output, index=frame.index, dtype="string")


def normalize_trades(authority: pd.DataFrame, projection: pd.DataFrame | None) -> pd.DataFrame:
    source = canonical_projection(authority, projection)
    trades = pd.DataFrame(index=source.index)
    trades["trade_id"] = deterministic_trade_ids(source)
    trades["source_file"] = first_series(source, ("source_file", "imagem"), "")
    trades["order_id"] = first_series(source, ("order_id", "10_numero_do_pedido"), "")
    trades["symbol"] = first_series(source, ("symbol", "moeda", "11_moeda"), "").map(normalize_symbol)
    trades["side"] = first_series(source, ("side", "fechar_side", "12_fechar_long_short"), "").map(normalize_side)
    trades["open_time"] = pd.to_datetime(
        first_series(source, ("open_time", "open_ts", "horario_abertura", "7_horario_de_abertura")),
        errors="coerce",
        utc=True,
    )
    trades["close_time"] = pd.to_datetime(
        first_series(source, ("close_time", "close_ts", "horario_fechamento", "8_horario_de_fechamento")),
        errors="coerce",
        utc=True,
    )
    trades["entry_price"] = numeric_series(
        first_series(source, ("entry_price", "preco_abertura", "3_preco_de_abertura"))
    )
    trades["exit_price"] = numeric_series(
        first_series(source, ("exit_price", "preco_fechamento", "4_preco_de_fechamento"))
    )
    trades["volume_position"] = numeric_series(
        first_series(source, ("volume_position", "volume_posicao", "5_volume_de_posicao"))
    )
    trades["volume_closed"] = numeric_series(
        first_series(source, ("volume_closed", "volume_fechado", "6_volume_fechado"))
    )
    fee_one = numeric_series(first_series(source, ("taxa_1", "9_taxa")))
    fee_two = numeric_series(first_series(source, ("taxa_2",)))
    trades["fees_total"] = pd.concat([fee_one, fee_two], axis=1).sum(axis=1, min_count=1)
    trades["net_pnl"] = numeric_series(
        first_series(source, ("net_pnl", "pnl_fechado", "1_pnl_fechado"))
    )
    trades["net_pnl_pct"] = numeric_series(
        first_series(
            source,
            (
                "net_pnl_pct",
                "taxa_lucros_perdas_fechados_pct",
                "2_taxa_lucros_perdas_fechados",
            ),
        )
    )
    trades["duration_seconds"] = (
        trades["close_time"] - trades["open_time"]
    ).dt.total_seconds()
    trades["duration_minutes"] = trades["duration_seconds"] / 60.0
    trades["is_win"] = trades["net_pnl"].gt(0).astype("Int64")
    trades["has_valid_symbol"] = trades["symbol"].isin(PRICE_GUARDS)
    trades["has_valid_side"] = trades["side"].isin({"long", "short"})
    trades["has_valid_times"] = (
        trades["open_time"].notna()
        & trades["close_time"].notna()
        & trades["close_time"].ge(trades["open_time"])
    )
    trades["has_valid_prices"] = (
        trades["entry_price"].gt(0) & trades["exit_price"].gt(0)
    )
    trades["has_valid_pnl"] = trades["net_pnl"].notna()
    return trades


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window, min_periods=window).mean()
    loss = (-delta.clip(upper=0)).rolling(window, min_periods=window).mean()
    relative_strength = gain / loss.replace(0, np.nan)
    result = 100.0 - (100.0 / (1.0 + relative_strength))
    return result.where(loss.ne(0), 100.0)


def normalize_candles(raw: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    required = {"symbol", "ts", "open", "high", "low", "close", "volume"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError("missing_candle_columns:" + ",".join(missing))
    candles = raw.copy()
    if "tf" in candles.columns:
        candles = candles[candles["tf"].astype(str).str.casefold().eq("1m")].copy()
    candles["symbol"] = candles["symbol"].map(normalize_symbol)
    candles["ts"] = pd.to_datetime(candles["ts"], errors="coerce", utc=True)
    numeric_columns = ["open", "high", "low", "close", "volume"]
    for column in numeric_columns:
        candles[column] = pd.to_numeric(candles[column], errors="coerce")
    candles = candles[candles["symbol"].isin(PRICE_GUARDS)].copy()
    valid = candles["ts"].notna()
    for symbol, (minimum, maximum) in PRICE_GUARDS.items():
        symbol_rows = candles["symbol"].eq(symbol)
        price_valid = pd.Series(True, index=candles.index)
        for column in ("open", "high", "low", "close"):
            price_valid &= candles[column].between(minimum, maximum, inclusive="both")
        valid &= ~symbol_rows | price_valid
    valid &= candles["volume"].ge(0)
    invalid_rows = int((~valid).sum())
    candles = candles.loc[valid].copy()
    score_columns = [
        column
        for column in ("ret_1", "ret_5", "ema_20", "ema_50", "rsi_14", "market_regime")
        if column in candles.columns
    ]
    candles["_quality_score"] = candles[score_columns].notna().sum(axis=1) if score_columns else 0
    candles = candles.sort_values(["symbol", "ts", "_quality_score"])
    candles = candles.drop_duplicates(["symbol", "ts"], keep="last")
    groups: list[pd.DataFrame] = []
    for _, group in candles.groupby("symbol", sort=True):
        group = group.sort_values("ts").copy()
        close = group["close"]
        returns = close.pct_change()
        group["_return_1m"] = returns
        group["_return_5m"] = close.pct_change(5)
        group["_volatility_5m"] = returns.rolling(5, min_periods=5).std(ddof=0)
        group["_volatility_15m"] = returns.rolling(15, min_periods=15).std(ddof=0)
        group["_ema_fast"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
        group["_ema_slow"] = close.ewm(span=50, adjust=False, min_periods=50).mean()
        group["_ema_distance"] = group["_ema_fast"] / group["_ema_slow"] - 1.0
        group["_rsi"] = rsi(close)
        distance = group["_ema_distance"]
        group["_trend_direction"] = np.select(
            [distance.gt(0.001), distance.lt(-0.001)],
            ["up", "down"],
            default="flat",
        )
        if "market_regime" in group.columns:
            provided = group["market_regime"].fillna("").astype(str).str.strip()
        else:
            provided = pd.Series("", index=group.index)
        derived = np.where(group["_trend_direction"].eq("flat"), "range", "trend")
        group["_market_regime"] = provided.where(provided.ne(""), derived)
        groups.append(group)
    normalized = pd.concat(groups, ignore_index=True) if groups else candles.iloc[0:0].copy()
    return normalized, invalid_rows


def _timestamp_ns(value: pd.Timestamp) -> int:
    return int(value.value)


def _exact_index(timestamps_ns: np.ndarray, timestamp: pd.Timestamp) -> int | None:
    target = _timestamp_ns(timestamp.floor("min"))
    index = int(np.searchsorted(timestamps_ns, target, side="left"))
    if index < len(timestamps_ns) and int(timestamps_ns[index]) == target:
        return index
    return None


def _feature_index(timestamps_ns: np.ndarray, open_time: pd.Timestamp) -> int | None:
    available_ns = timestamps_ns + ONE_MINUTE_NS
    index = int(np.searchsorted(available_ns, _timestamp_ns(open_time), side="right") - 1)
    return index if index >= 0 else None


def _path_bounds(
    timestamps_ns: np.ndarray,
    open_time: pd.Timestamp,
    close_time: pd.Timestamp,
) -> tuple[int, int]:
    start = _timestamp_ns(open_time.floor("min"))
    end = _timestamp_ns(close_time.floor("min"))
    left = int(np.searchsorted(timestamps_ns, start, side="left"))
    right = int(np.searchsorted(timestamps_ns, end, side="right"))
    return left, right


def _path_metrics(
    path: pd.DataFrame,
    *,
    side: str,
    entry_price: float,
    open_time: pd.Timestamp,
) -> dict[str, Any]:
    empty = {
        "mfe_abs": np.nan,
        "mae_abs": np.nan,
        "mfe_pct": np.nan,
        "mae_pct": np.nan,
        "max_favorable_price": np.nan,
        "max_adverse_price": np.nan,
        "time_to_mfe_seconds": np.nan,
        "time_to_mae_seconds": np.nan,
    }
    if path.empty or not np.isfinite(entry_price) or side not in {"long", "short"}:
        return empty
    if side == "long":
        favorable_position = int(path["high"].to_numpy().argmax())
        adverse_position = int(path["low"].to_numpy().argmin())
        favorable_price = float(path.iloc[favorable_position]["high"])
        adverse_price = float(path.iloc[adverse_position]["low"])
        mfe_abs = favorable_price - entry_price
        mae_abs = adverse_price - entry_price
    else:
        favorable_position = int(path["low"].to_numpy().argmin())
        adverse_position = int(path["high"].to_numpy().argmax())
        favorable_price = float(path.iloc[favorable_position]["low"])
        adverse_price = float(path.iloc[adverse_position]["high"])
        mfe_abs = entry_price - favorable_price
        mae_abs = entry_price - adverse_price
    favorable_time = max(pd.Timestamp(path.iloc[favorable_position]["ts"]), open_time)
    adverse_time = max(pd.Timestamp(path.iloc[adverse_position]["ts"]), open_time)
    return {
        "mfe_abs": mfe_abs,
        "mae_abs": mae_abs,
        "mfe_pct": mfe_abs / entry_price * 100.0,
        "mae_pct": mae_abs / entry_price * 100.0,
        "max_favorable_price": favorable_price,
        "max_adverse_price": adverse_price,
        "time_to_mfe_seconds": (favorable_time - open_time).total_seconds(),
        "time_to_mae_seconds": (adverse_time - open_time).total_seconds(),
    }


def _counterfactual(
    *,
    side: str,
    entry_price: float,
    exit_price: float,
    volume_closed: float,
    fees_total: float,
    net_pnl: float,
) -> tuple[float, Any, float]:
    if side not in {"long", "short"} or not all(
        np.isfinite(value) for value in (entry_price, exit_price, volume_closed)
    ):
        return np.nan, pd.NA, np.nan
    actual_gross = (
        (exit_price - entry_price) * volume_closed
        if side == "long"
        else (entry_price - exit_price) * volume_closed
    )
    fee_cost = abs(fees_total) if np.isfinite(fees_total) else 0.0
    opposite = -actual_gross - fee_cost
    delta = net_pnl - opposite if np.isfinite(net_pnl) else np.nan
    return opposite, int(opposite > 0), delta


def build_research_dataset(trades: pd.DataFrame, candles: pd.DataFrame) -> pd.DataFrame:
    output = trades.copy()
    for column in OUTPUT_COLUMNS:
        if column not in output.columns:
            output[column] = pd.NA
    candle_groups = {
        symbol: group.sort_values("ts").reset_index(drop=True)
        for symbol, group in candles.groupby("symbol", sort=True)
    }

    for index, trade in output.iterrows():
        symbol = str(trade["symbol"])
        open_time = trade["open_time"]
        close_time = trade["close_time"]
        side = str(trade["side"])
        entry_price = float(trade["entry_price"]) if pd.notna(trade["entry_price"]) else np.nan
        exit_price = float(trade["exit_price"]) if pd.notna(trade["exit_price"]) else np.nan
        volume_closed = (
            float(trade["volume_closed"]) if pd.notna(trade["volume_closed"]) else np.nan
        )
        fees_total = float(trade["fees_total"]) if pd.notna(trade["fees_total"]) else np.nan
        net_pnl = float(trade["net_pnl"]) if pd.notna(trade["net_pnl"]) else np.nan
        base_valid = bool(
            trade["has_valid_symbol"]
            and trade["has_valid_side"]
            and trade["has_valid_times"]
            and trade["has_valid_prices"]
            and trade["has_valid_pnl"]
        )
        group = candle_groups.get(symbol)
        entry_exact = exit_exact = feature_index = None
        path = pd.DataFrame()
        expected_candles = 0
        if group is not None and pd.notna(open_time) and pd.notna(close_time):
            timestamps_ns = group["ts"].astype("int64").to_numpy()
            entry_exact = _exact_index(timestamps_ns, open_time)
            exit_exact = _exact_index(timestamps_ns, close_time)
            feature_index = _feature_index(timestamps_ns, open_time)
            left, right = _path_bounds(timestamps_ns, open_time, close_time)
            path = group.iloc[left:right]
            expected_candles = int(
                (close_time.floor("min") - open_time.floor("min")).total_seconds() // 60 + 1
            )
        entry_found = entry_exact is not None
        exit_found = exit_exact is not None
        path_count = int(len(path))
        missing_count = max(0, expected_candles - path_count)
        aligned = entry_found and exit_found and missing_count == 0
        output.at[index, "entry_candle_timestamp"] = (
            group.iloc[entry_exact]["ts"] if group is not None and entry_found else pd.NaT
        )
        output.at[index, "exit_candle_timestamp"] = (
            group.iloc[exit_exact]["ts"] if group is not None and exit_found else pd.NaT
        )
        output.at[index, "entry_candle_found"] = entry_found
        output.at[index, "exit_candle_found"] = exit_found
        output.at[index, "candles_between_count"] = path_count
        output.at[index, "missing_candle_count"] = missing_count
        output.at[index, "candle_alignment_status"] = (
            "aligned" if aligned else "missing_or_partial"
        )
        if group is not None and feature_index is not None:
            feature = group.iloc[feature_index]
            output.at[index, "entry_feature_timestamp"] = feature["ts"]
            for destination, source in (
                ("entry_open", "open"),
                ("entry_high", "high"),
                ("entry_low", "low"),
                ("entry_close", "close"),
                ("entry_volume", "volume"),
                ("entry_return_1m", "_return_1m"),
                ("entry_return_5m", "_return_5m"),
                ("entry_volatility_5m", "_volatility_5m"),
                ("entry_volatility_15m", "_volatility_15m"),
                ("entry_ema_fast", "_ema_fast"),
                ("entry_ema_slow", "_ema_slow"),
                ("entry_ema_distance", "_ema_distance"),
                ("entry_rsi", "_rsi"),
                ("entry_trend_direction", "_trend_direction"),
                ("entry_market_regime", "_market_regime"),
            ):
                output.at[index, destination] = feature[source]
        metrics = _path_metrics(
            path,
            side=side,
            entry_price=entry_price,
            open_time=open_time if pd.notna(open_time) else pd.Timestamp("1970-01-01", tz="UTC"),
        )
        for column, value in metrics.items():
            output.at[index, column] = value
        opposite, opposite_win, delta = _counterfactual(
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            volume_closed=volume_closed,
            fees_total=fees_total,
            net_pnl=net_pnl,
        )
        output.at[index, "opposite_side_pnl_estimate"] = opposite
        output.at[index, "opposite_side_would_win"] = opposite_win
        output.at[index, "actual_side_vs_opposite_delta"] = delta
        reasons: list[str] = []
        for field, reason in (
            ("has_valid_symbol", "invalid_symbol"),
            ("has_valid_side", "invalid_side"),
            ("has_valid_times", "invalid_times"),
            ("has_valid_prices", "invalid_prices"),
            ("has_valid_pnl", "invalid_pnl"),
        ):
            if not bool(trade[field]):
                reasons.append(reason)
        if not aligned:
            reasons.append("missing_or_partial_candles")
        if feature_index is None:
            reasons.append("entry_context_missing")
        output.at[index, "is_research_eligible"] = base_valid and aligned and feature_index is not None
        output.at[index, "research_block_reason"] = "ok" if not reasons else "|".join(reasons)

    return output.loc[:, list(OUTPUT_COLUMNS)].copy()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def atomic_write_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}.tmp.parquet")
    try:
        frame.to_parquet(temporary, index=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def base_report(paths: ResearchPaths, write: bool) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": "not_started",
        "source_master_path": str(paths.master_xlsx),
        "source_master_rows": 0,
        "source_master_sha256": None,
        "expected_master_sha256": EXPECTED_MASTER_SHA256,
        "master_sha256_matches_expected": False,
        "source_trade_projection_path": (
            str(paths.master_projection) if paths.master_projection else None
        ),
        "candles_source_path": str(paths.candles_path) if paths.candles_path else None,
        "candles_rows": 0,
        "invalid_candle_rows": 0,
        "research_dataset_rows": 0,
        "eligible_rows": 0,
        "blocked_rows": 0,
        "missing_candle_rows": 0,
        "symbols": [],
        "sides": [],
        "min_open_time": None,
        "max_close_time": None,
        "write_requested": write,
        "write_performed": False,
        "output_path": str(paths.output_path),
        "report_path": str(paths.report_path),
        "executive_summary_path": str(paths.executive_summary_path),
        "executive_markdown_path": str(paths.executive_markdown_path),
        "configured_workers": configured_workers(),
        "configured_max_ram_gb": configured_max_ram_gb(),
        "entry_features_point_in_time": True,
        "warnings": [],
        "validation_errors": [],
        **SAFETY_FLAGS,
    }


def build_from_paths(
    paths: ResearchPaths,
    *,
    write: bool = False,
    analysis_date_utc: str | None = None,
) -> ResearchBuildResult:
    report = base_report(paths, write)
    if not paths.master_xlsx.exists():
        report.update(reason="missing_master", validation_errors=["source_master_not_found"])
        return ResearchBuildResult(pd.DataFrame(columns=OUTPUT_COLUMNS), report)
    if paths.candles_path is None or not paths.candles_path.exists():
        report.update(reason="missing_candles", validation_errors=["candles_source_not_found"])
        return ResearchBuildResult(pd.DataFrame(columns=OUTPUT_COLUMNS), report)
    try:
        authority = read_table(paths.master_xlsx)
        projection = (
            read_table(paths.master_projection)
            if paths.master_projection and paths.master_projection.exists()
            else None
        )
        raw_candles = read_table(paths.candles_path)
        trades = normalize_trades(authority, projection)
        candles, invalid_candles = normalize_candles(raw_candles)
        dataset = build_research_dataset(trades, candles)
    except (OSError, ValueError, KeyError, TypeError, pd.errors.ParserError) as exc:
        report.update(
            status="failed",
            reason="source_processing_failed",
            validation_errors=[f"{type(exc).__name__}:{exc}"],
        )
        return ResearchBuildResult(pd.DataFrame(columns=OUTPUT_COLUMNS), report)

    master_sha = sha256_file(paths.master_xlsx)
    master_matches = master_sha.casefold() == EXPECTED_MASTER_SHA256.casefold()
    if not master_matches:
        report["warnings"].append("master_sha256_differs_from_ocr_v11_production_authority")
    if len(authority) != EXPECTED_MASTER_ROWS:
        report["warnings"].append(
            f"master_rows_differ_from_expected:{len(authority)}!={EXPECTED_MASTER_ROWS}"
        )
    if projection is not None and len(projection) != len(authority):
        report["warnings"].append("master_projection_rows_do_not_match_authority")

    eligible = dataset["is_research_eligible"].eq(True)
    missing_candles = dataset["candle_alignment_status"].ne("aligned")
    report.update(
        status="ok",
        reason="research_dataset_ready" if not report["warnings"] else "research_dataset_ready_with_warnings",
        source_master_rows=int(len(authority)),
        source_master_sha256=master_sha,
        master_sha256_matches_expected=master_matches,
        candles_rows=int(len(candles)),
        invalid_candle_rows=invalid_candles,
        research_dataset_rows=int(len(dataset)),
        eligible_rows=int(eligible.sum()),
        blocked_rows=int((~eligible).sum()),
        missing_candle_rows=int(missing_candles.sum()),
        symbols=sorted(dataset["symbol"].dropna().astype(str).unique().tolist()),
        sides=sorted(dataset["side"].dropna().astype(str).unique().tolist()),
        min_open_time=json_safe(dataset["open_time"].min()),
        max_close_time=json_safe(dataset["close_time"].max()),
    )
    if write:
        atomic_write_parquet(paths.output_path, dataset)
        report["write_performed"] = True
        atomic_write_json(paths.report_path, report)
        executive = build_executive_summary(
            dataset,
            report,
            analysis_date_utc=analysis_date_utc or pd.Timestamp.now(tz="UTC").isoformat(),
        )
        atomic_write_json(paths.executive_summary_path, executive)
        atomic_write_text(paths.executive_markdown_path, render_executive_markdown(executive))
    return ResearchBuildResult(dataset, report)
