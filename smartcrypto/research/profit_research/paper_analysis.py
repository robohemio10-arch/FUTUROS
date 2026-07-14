"""Read-only paper-trade economic analysis with point-in-time market features."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from smartcrypto.data.trader_master_fingerprint_v2.authoritative_sqlite import (
    read_authoritative_closed_trades,
    read_authoritative_trade_evidence,
)
from smartcrypto.data.trader_master_fingerprint_v2.source_profile import (
    FreqtradePaperSourceProfile,
    load_source_profile,
)

SCHEMA_VERSION = "profit_research_paper_analysis_v1"
DEFAULT_SOURCE_PROFILE = Path("config/freqtrade_paper_closed_trades_source_profile_v2.json")
DEFAULT_SNAPSHOT_DB = Path("data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite")
DEFAULT_RUNTIME_DB = Path("freqtrade/user_data/tradesv3.paper.sqlite")
DEFAULT_CLOSED_TRADES_CSV = Path("data/trades/inbox/freqtrade_paper_closed_trades.csv")
DEFAULT_FEEDBACK = Path("data/feedback/paper_closed_trades_incremental.parquet")
DEFAULT_MICROBATCH = Path("data/features/incremental_training_microbatch.parquet")
DEFAULT_CANDLES = Path("data/features/market_features_60d.parquet")
DEFAULT_MASTER = Path("data/trades/trades_master.parquet")
DEFAULT_NEW_TRADES_SOURCE = Path(r"E:\bitradex\Bitradex prints")
DEFAULT_OCR_HANDOFF = Path(r"E:\Apoio Futuros\Handoff Canônico - Extração OCR.pdf")
DEFAULT_OUTPUT_DATASET = Path("data/research/profit_research_paper_analytical_dataset_v1.parquet")
DEFAULT_REPORT_JSON = Path("data/reports/profit_research_paper_analysis_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/profit_research_paper_analysis_v1.md")

PRE_ENTRY_FEATURE_MAP = {
    "atr_14": "pre_entry_atr",
    "atr_pct_14": "pre_entry_atr_pct",
    "rsi_14": "pre_entry_rsi",
    "ret_1": "pre_entry_return_1",
    "ret_5": "pre_entry_return_5",
    "trend_score": "pre_entry_trend_score",
    "volume": "pre_entry_volume",
    "volume_rel_30": "pre_entry_volume_rel_30",
    "vol_30": "pre_entry_volatility",
    "market_regime": "pre_entry_regime",
}

ENTRY_RULE_DIMENSIONS = frozenset(
    {
        "symbol",
        "side",
        "hour_utc",
        "day_of_week",
        "leverage",
        "regime",
        "month",
        "week",
    }
)

OCR_IMAGE_EXTENSIONS = frozenset({".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"})
OCR_CANONICAL_FIELDS = (
    "moeda",
    "mercado_limite",
    "fechar_long_short",
    "preco_abertura",
    "preco_fechamento",
    "volume_posicao",
    "volume_fechado",
    "horario_abertura",
    "horario_fechamento",
    "taxa_total",
    "numero_pedido",
    "preco_transacao",
    "volume_transacao",
    "direcao_liquidez",
    "taxa_execucao",
    "horario_transacao",
)

SAFETY_FLAGS: dict[str, bool] = {
    "research_only": True,
    "paper_only": True,
    "shadow_only": True,
    "operational_authority": False,
    "training_performed": False,
    "model_promotion_performed": False,
    "active_model_changed": False,
    "qlib_runtime_updated": False,
    "ai_shadow_runtime_updated": False,
    "freqtrade_updated": False,
    "risk_manager_updated": False,
    "stake_runtime_changed": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "writes_trader_master": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "runs_ocr": False,
}


@dataclass(frozen=True)
class ProfitResearchPaths:
    project_root: Path
    source_profile: Path
    snapshot_db: Path
    runtime_db: Path
    closed_trades_csv: Path
    feedback: Path
    microbatch: Path
    candles: Path
    trader_master: Path
    new_trades_source: Path
    ocr_handoff: Path
    output_dataset: Path
    report_json: Path
    report_markdown: Path


@dataclass(frozen=True)
class ProfitResearchResult:
    dataset: pd.DataFrame
    report: dict[str, Any]


def resolve_profit_research_paths(
    project_root: str | Path,
    *,
    source_profile: str | Path | None = None,
    snapshot_db: str | Path | None = None,
    runtime_db: str | Path | None = None,
    closed_trades_csv: str | Path | None = None,
    feedback: str | Path | None = None,
    microbatch: str | Path | None = None,
    candles: str | Path | None = None,
    trader_master: str | Path | None = None,
    new_trades_source: str | Path | None = None,
    ocr_handoff: str | Path | None = None,
    output_dataset: str | Path | None = None,
    report_json: str | Path | None = None,
    report_markdown: str | Path | None = None,
) -> ProfitResearchPaths:
    root = Path(project_root).resolve()
    return ProfitResearchPaths(
        project_root=root,
        source_profile=_resolve(root, source_profile, DEFAULT_SOURCE_PROFILE),
        snapshot_db=_resolve(root, snapshot_db, DEFAULT_SNAPSHOT_DB),
        runtime_db=_resolve(root, runtime_db, DEFAULT_RUNTIME_DB),
        closed_trades_csv=_resolve(root, closed_trades_csv, DEFAULT_CLOSED_TRADES_CSV),
        feedback=_resolve(root, feedback, DEFAULT_FEEDBACK),
        microbatch=_resolve(root, microbatch, DEFAULT_MICROBATCH),
        candles=_resolve(root, candles, DEFAULT_CANDLES),
        trader_master=_resolve(root, trader_master, DEFAULT_MASTER),
        new_trades_source=_resolve(root, new_trades_source, DEFAULT_NEW_TRADES_SOURCE),
        ocr_handoff=_resolve(root, ocr_handoff, DEFAULT_OCR_HANDOFF),
        output_dataset=_resolve(root, output_dataset, DEFAULT_OUTPUT_DATASET),
        report_json=_resolve(root, report_json, DEFAULT_REPORT_JSON),
        report_markdown=_resolve(root, report_markdown, DEFAULT_REPORT_MD),
    )


def build_profit_research(
    paths: ProfitResearchPaths,
    *,
    write: bool = False,
) -> ProfitResearchResult:
    report = _base_report(paths, write)
    master_hash_before = file_sha256(paths.trader_master)
    report["trader_master_sha256_before"] = master_hash_before
    try:
        profile = load_source_profile(paths.source_profile)
        snapshot_frame, snapshot_meta = load_snapshot_closed_trades(paths, profile)
        report["snapshot_read"] = snapshot_meta
        if snapshot_frame.empty:
            report.update(
                reason="authoritative_snapshot_has_no_closed_trades",
                validation_errors=["authoritative_snapshot_has_no_closed_trades"],
            )
            return ProfitResearchResult(pd.DataFrame(), _finalize_master_hash(report, paths))

        dataset = normalize_snapshot_trades(snapshot_frame, profile)
        candles, candle_inventory = load_market_candles(paths.candles)
        report["candles_inventory"] = candle_inventory
        if candles.empty:
            report["warnings"].append("candles_unavailable_mfe_mae_and_entry_features_missing")
        else:
            dataset = attach_market_context(dataset, candles)
        dataset = add_analysis_buckets(dataset)

        source_inventory = build_source_inventory(
            paths=paths,
            snapshot_frame=snapshot_frame,
            snapshot_meta=snapshot_meta,
            candles_inventory=candle_inventory,
        )
        reconciliation = reconcile_sources(paths, dataset)
        eligible = dataset.loc[dataset["analysis_eligible"]].copy()
        eligible = eligible.sort_values(["open_time_utc", "trade_id"]).reset_index(drop=True)
        if eligible.empty:
            report.update(
                reason="no_financially_valid_closed_trades",
                source_inventory=source_inventory,
                source_reconciliation=reconciliation,
                validation_errors=["no_financially_valid_closed_trades"],
            )
            return ProfitResearchResult(dataset, _finalize_master_hash(report, paths))

        global_metrics = financial_metrics(eligible)
        path_analysis = build_path_analysis(eligible)
        segments = build_segment_analysis(eligible)
        block_candidates = build_block_candidates(eligible, segments)
        entry_timing_scenarios = build_entry_timing_scenarios(eligible, candles)
        exit_candidates = build_exit_candidates(eligible, candles)
        stake_candidates = build_stake_candidates(eligible)
        conclusion = build_economic_conclusion(
            global_metrics,
            block_candidates,
            entry_timing_scenarios,
            exit_candidates,
            stake_candidates,
        )
        report.update(
            status="ok",
            reason="paper_profit_research_completed",
            decision="MANTER_EM_RESEARCH",
            source_inventory=source_inventory,
            source_reconciliation=reconciliation,
            analytical_dataset_rows=int(len(dataset)),
            eligible_trade_count=int(len(eligible)),
            blocked_trade_count=int((~dataset["analysis_eligible"]).sum()),
            blocked_trade_reason_counts={
                str(reason): int(count)
                for reason, count in dataset.loc[
                    ~dataset["analysis_eligible"], "analysis_block_reason"
                ]
                .fillna("unknown")
                .value_counts()
                .sort_index()
                .items()
            },
            min_open_time_utc=_iso_min(eligible["open_time_utc"]),
            max_close_time_utc=_iso_max(eligible["close_time_utc"]),
            symbols=sorted(eligible["symbol"].dropna().astype(str).unique().tolist()),
            sides=sorted(eligible["side"].dropna().astype(str).unique().tolist()),
            global_metrics=global_metrics,
            path_analysis=path_analysis,
            segment_count=len(segments),
            segment_analysis=segments,
            top_profitable_segments=_top_segments(segments, profitable=True),
            top_harmful_segments=_top_segments(segments, profitable=False),
            candidate_block_rules=block_candidates[:5],
            entry_timing_scenarios=entry_timing_scenarios,
            candidate_exit_changes=exit_candidates[:5],
            candidate_stake_policies=stake_candidates[:3],
            economic_conclusion=conclusion,
            data_limitations=build_limitations(dataset, candles, source_inventory),
        )
        report = _finalize_master_hash(report, paths)
        if report["trader_master_hash_preserved"] is not True:
            report.update(
                status="blocked",
                reason="protected_trader_master_hash_changed",
                validation_errors=["protected_trader_master_hash_changed"],
            )
            return ProfitResearchResult(dataset, report)
        if write:
            materialized_report = {**report, "write_performed": True}
            write_research_outputs(paths, dataset, materialized_report)
            report["write_performed"] = True
        return ProfitResearchResult(dataset, report)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, pd.errors.ParserError) as exc:
        report.update(
            status="blocked",
            reason="profit_research_build_failed",
            validation_errors=[f"{type(exc).__name__}:{exc}"],
        )
        return ProfitResearchResult(pd.DataFrame(), _finalize_master_hash(report, paths))


def load_snapshot_closed_trades(
    paths: ProfitResearchPaths,
    profile: FreqtradePaperSourceProfile,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    closed = read_authoritative_closed_trades(
        project_root=paths.project_root,
        snapshot_path=paths.snapshot_db,
        profile=profile,
    )
    closed_rows = closed.pop("rows", [])
    if closed.get("status") != "ok":
        return pd.DataFrame(), closed
    trade_ids = frozenset(int(row["id"]) for row in closed_rows)
    evidence = read_authoritative_trade_evidence(
        project_root=paths.project_root,
        snapshot_path=paths.snapshot_db,
        profile=profile,
        trade_ids=trade_ids,
    )
    trades = evidence.pop("trades", [])
    orders = evidence.pop("orders", [])
    evidence.pop("trade_custom_data", None)
    metadata = {
        **closed,
        "closed_trade_count": len(closed_rows),
        "full_trade_row_count": len(trades),
        "related_order_row_count": len(orders),
        "full_evidence_status": evidence.get("status"),
        "full_evidence_reason": evidence.get("reason"),
        "full_evidence_hash_preserved": evidence.get("snapshot_source_hashes_preserved"),
    }
    if evidence.get("status") != "ok" or len(trades) != len(trade_ids):
        metadata.update(
            status="blocked",
            reason="authoritative_snapshot_full_evidence_incomplete",
        )
        return pd.DataFrame(), metadata
    return pd.DataFrame(trades), metadata


def normalize_snapshot_trades(
    frame: pd.DataFrame,
    profile: FreqtradePaperSourceProfile,
) -> pd.DataFrame:
    data = frame.copy()
    numeric_columns = (
        "amount",
        "close_profit",
        "close_profit_abs",
        "close_rate",
        "contract_size",
        "fee_close_cost",
        "fee_open_cost",
        "funding_fees",
        "leverage",
        "open_rate",
        "stake_amount",
    )
    for column in numeric_columns:
        data[column] = pd.to_numeric(data.get(column), errors="coerce")
    data["trade_id"] = pd.to_numeric(data["id"], errors="coerce").astype("Int64")
    data["order_id"] = data["trade_id"].map(
        lambda value: f"freqtrade-paper-{int(value)}" if pd.notna(value) else None
    )
    data["symbol"] = data["pair"].map(normalize_symbol)
    data["side"] = np.where(data["is_short"].astype(bool), "short", "long")
    data["open_time_utc"] = pd.to_datetime(data["open_date"], utc=True, errors="coerce")
    data["close_time_utc"] = pd.to_datetime(data["close_date"], utc=True, errors="coerce")
    data["duration_seconds"] = (data["close_time_utc"] - data["open_time_utc"]).dt.total_seconds()
    data["quantity"] = data["amount"]
    data["gross_pnl"] = np.where(
        data["side"].eq("long"),
        (data["close_rate"] - data["open_rate"]) * data["amount"] * data["contract_size"],
        (data["open_rate"] - data["close_rate"]) * data["amount"] * data["contract_size"],
    )
    data["effective_open_fee"] = data["fee_open_cost"] * data["leverage"]
    data["effective_close_fee"] = data["fee_close_cost"]
    data["fees"] = data["effective_open_fee"] + data["effective_close_fee"]
    data["funding"] = -data["funding_fees"]
    data["net_pnl"] = data["close_profit_abs"]
    data["profit_ratio"] = data["close_profit"]
    data["net_pnl_reconstructed"] = data["gross_pnl"] - data["fees"] - data["funding"]
    data["accounting_residual"] = data["net_pnl_reconstructed"] - data["net_pnl"]
    tolerance = float(profile.financial_contract.epsilon_abs_fonte)
    required_valid = (
        data[
            [
                "trade_id",
                "symbol",
                "open_time_utc",
                "close_time_utc",
                "open_rate",
                "close_rate",
                "quantity",
                "net_pnl",
                "gross_pnl",
                "fees",
                "funding",
            ]
        ]
        .notna()
        .all(axis=1)
    )
    chronological = data["close_time_utc"].ge(data["open_time_utc"])
    reconciled = data["accounting_residual"].abs().le(tolerance)
    data["accounting_reconciled"] = reconciled
    data["analysis_eligible"] = required_valid & chronological & reconciled
    block_reason = pd.Series(pd.NA, index=data.index, dtype="string")
    block_reason = block_reason.mask(~reconciled, "accounting_identity_residual")
    block_reason = block_reason.mask(~chronological, "invalid_trade_time_order")
    data["analysis_block_reason"] = block_reason.mask(
        ~required_valid, "missing_required_financial_field"
    )
    data["entry_price"] = data["open_rate"]
    data["exit_price"] = data["close_rate"]
    data["entry_tag"] = data.get("enter_tag")
    data["exit_tag"] = None
    selected = [
        "trade_id",
        "order_id",
        "symbol",
        "side",
        "open_time_utc",
        "close_time_utc",
        "duration_seconds",
        "open_rate",
        "close_rate",
        "entry_price",
        "exit_price",
        "stake_amount",
        "leverage",
        "quantity",
        "contract_size",
        "gross_pnl",
        "effective_open_fee",
        "effective_close_fee",
        "fees",
        "funding",
        "net_pnl",
        "profit_ratio",
        "net_pnl_reconstructed",
        "accounting_residual",
        "accounting_reconciled",
        "exit_reason",
        "strategy",
        "timeframe",
        "entry_tag",
        "exit_tag",
        "analysis_eligible",
        "analysis_block_reason",
    ]
    return (
        data.reindex(columns=selected)
        .sort_values(["open_time_utc", "trade_id"], na_position="last")
        .reset_index(drop=True)
    )


def load_market_candles(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    inventory = inventory_tabular(path)
    if not path.exists():
        return pd.DataFrame(), inventory
    frame = pd.read_parquet(path)
    prohibited = sorted(
        str(column)
        for column in frame.columns
        if str(column).casefold().startswith(("future_ret_", "target_", "label_"))
    )
    if prohibited:
        raise ValueError("operational_candle_lookahead_columns:" + ",".join(prohibited))
    if "tf" not in frame.columns:
        frame["tf"] = "unknown"
    frame["tf"] = frame["tf"].astype("string").str.casefold()
    frame["symbol"] = frame["symbol"].map(normalize_symbol)
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True, errors="coerce")
    for column in {"open", "high", "low", "close", *PRE_ENTRY_FEATURE_MAP}:
        if column in frame.columns and column != "market_regime":
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = (
        frame.dropna(subset=["symbol", "ts", "open", "high", "low", "close"])
        .sort_values(["symbol", "tf", "ts"])
        .drop_duplicates(["symbol", "tf", "ts"], keep="last")
        .reset_index(drop=True)
    )
    inventory["analysis_candle_rows"] = int(len(frame))
    inventory["analysis_timeframes"] = sorted(frame["tf"].dropna().unique().tolist())
    inventory["analysis_min_timestamp_utc"] = _iso_min(frame["ts"])
    inventory["analysis_max_timestamp_utc"] = _iso_max(frame["ts"])
    inventory["analysis_timeframe_bounds"] = [
        {
            "symbol": str(symbol),
            "timeframe": str(timeframe),
            "rows": int(len(group)),
            "min_timestamp_utc": _iso_min(group["ts"]),
            "max_timestamp_utc": _iso_max(group["ts"]),
        }
        for (symbol, timeframe), group in frame.groupby(["symbol", "tf"], sort=True)
    ]
    return frame, inventory


def attach_market_context(trades: pd.DataFrame, candles: pd.DataFrame) -> pd.DataFrame:
    enriched = trades.copy()
    candles = candles.copy()
    if "tf" not in candles.columns:
        candles["tf"] = "unknown"
    for target in PRE_ENTRY_FEATURE_MAP.values():
        enriched[target] = np.nan
    enriched["pre_entry_regime"] = None
    enriched["feature_timestamp_utc"] = pd.Series(
        pd.NaT, index=enriched.index, dtype="datetime64[ns, UTC]"
    )
    enriched["feature_age_seconds"] = np.nan
    for column in (
        "mfe_pct",
        "mae_pct",
        "max_unrealized_profit",
        "max_unrealized_loss",
        "profit_giveback",
        "max_favorable_price",
        "max_adverse_price",
        "time_to_mfe_seconds",
        "time_to_mae_seconds",
        "candles_between_count",
    ):
        enriched[column] = np.nan
    enriched["candle_alignment_status"] = "missing"
    enriched["candle_timeframe"] = None

    by_symbol = _timeframe_groups(candles)
    for index, trade in enriched.iterrows():
        open_time = trade["open_time_utc"]
        close_time = trade["close_time_utc"]
        if pd.isna(open_time) or pd.isna(close_time):
            continue
        symbol_candles = _select_covering_candles(
            by_symbol.get(str(trade["symbol"]), []), open_time, close_time
        )
        if symbol_candles is None or symbol_candles.empty:
            continue
        timestamps = symbol_candles["ts"]
        timeframe_minutes = _timeframe_minutes(str(symbol_candles.iloc[0]["tf"]))
        availability = timestamps + pd.Timedelta(minutes=timeframe_minutes)
        before_position = int(availability.searchsorted(open_time, side="right")) - 1
        if before_position >= 0:
            feature_row = symbol_candles.iloc[before_position]
            feature_ts = availability.iloc[before_position]
            enriched.at[index, "feature_timestamp_utc"] = feature_ts
            enriched.at[index, "feature_age_seconds"] = (open_time - feature_ts).total_seconds()
            for source, target in PRE_ENTRY_FEATURE_MAP.items():
                if source in feature_row.index:
                    enriched.at[index, target] = feature_row[source]

        start = int(timestamps.searchsorted(open_time, side="left"))
        end = int(timestamps.searchsorted(close_time, side="right"))
        path = symbol_candles.iloc[start:end]
        if path.empty:
            continue
        enriched.at[index, "candle_timeframe"] = str(symbol_candles.iloc[0]["tf"])
        _assign_path_metrics(enriched, index, trade, path)
    if enriched["feature_timestamp_utc"].notna().any():
        lookahead = enriched["feature_timestamp_utc"].gt(enriched["open_time_utc"])
        if lookahead.any():
            raise ValueError("pre_entry_feature_lookahead_detected")
    return enriched


def _assign_path_metrics(
    target: pd.DataFrame,
    index: int,
    trade: pd.Series,
    path: pd.DataFrame,
) -> None:
    entry = float(trade["entry_price"])
    quantity = float(trade["quantity"])
    contract_size = float(trade["contract_size"])
    side = str(trade["side"])
    if side == "long":
        favorable_index = path["high"].idxmax()
        adverse_index = path["low"].idxmin()
        favorable_price = float(path.loc[favorable_index, "high"])
        adverse_price = float(path.loc[adverse_index, "low"])
        mfe_pct = (favorable_price - entry) / entry
        mae_pct = (adverse_price - entry) / entry
        max_profit = (favorable_price - entry) * quantity * contract_size
        max_loss = (adverse_price - entry) * quantity * contract_size
    else:
        favorable_index = path["low"].idxmin()
        adverse_index = path["high"].idxmax()
        favorable_price = float(path.loc[favorable_index, "low"])
        adverse_price = float(path.loc[adverse_index, "high"])
        mfe_pct = (entry - favorable_price) / entry
        mae_pct = (entry - adverse_price) / entry
        max_profit = (entry - favorable_price) * quantity * contract_size
        max_loss = (entry - adverse_price) * quantity * contract_size
    open_time = trade["open_time_utc"]
    target.at[index, "mfe_pct"] = mfe_pct
    target.at[index, "mae_pct"] = mae_pct
    target.at[index, "max_unrealized_profit"] = max_profit
    target.at[index, "max_unrealized_loss"] = max_loss
    target.at[index, "profit_giveback"] = max_profit - float(trade["gross_pnl"])
    target.at[index, "max_favorable_price"] = favorable_price
    target.at[index, "max_adverse_price"] = adverse_price
    target.at[index, "time_to_mfe_seconds"] = (
        path.loc[favorable_index, "ts"] - open_time
    ).total_seconds()
    target.at[index, "time_to_mae_seconds"] = (
        path.loc[adverse_index, "ts"] - open_time
    ).total_seconds()
    target.at[index, "candles_between_count"] = int(len(path))
    target.at[index, "candle_alignment_status"] = "aligned"


def add_analysis_buckets(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    data["hour_utc"] = data["open_time_utc"].dt.hour.astype("Int64")
    data["day_of_week"] = data["open_time_utc"].dt.day_name()
    data["month"] = data["open_time_utc"].dt.strftime("%Y-%m")
    data["week"] = data["open_time_utc"].dt.strftime("%G-W%V")
    duration_minutes = data["duration_seconds"] / 60.0
    data["duration_bucket"] = pd.cut(
        duration_minutes,
        bins=[-np.inf, 15, 60, 240, 1440, np.inf],
        labels=["lte_15m", "15m_1h", "1h_4h", "4h_24h", "gt_24h"],
    ).astype("string")
    data["stake_bucket"] = _quantile_bucket(data["stake_amount"], "stake")
    data["volatility_bucket"] = _quantile_bucket(
        data.get("pre_entry_atr_pct", pd.Series(index=data.index, dtype=float)),
        "volatility",
    )
    data["regime"] = data.get("pre_entry_regime", pd.Series("unknown", index=data.index)).fillna(
        "unknown"
    )
    return data


def financial_metrics(
    frame: pd.DataFrame,
    *,
    pnl_column: str = "net_pnl",
) -> dict[str, Any]:
    if frame.empty or pnl_column not in frame.columns:
        return _empty_financial_metrics()
    ordered = frame.sort_values(["close_time_utc", "trade_id"])
    pnl = pd.to_numeric(ordered[pnl_column], errors="coerce").dropna()
    if pnl.empty:
        return _empty_financial_metrics()
    winners = pnl[pnl > 0]
    losers = pnl[pnl < 0]
    gross_profit = float(winners.sum())
    gross_loss_abs = float(-losers.sum())
    average_win = float(winners.mean()) if not winners.empty else 0.0
    average_loss = float(losers.mean()) if not losers.empty else 0.0
    profit_factor = gross_profit / gross_loss_abs if gross_loss_abs > 0 else None
    payoff = average_win / abs(average_loss) if average_loss < 0 else None
    cumulative = pnl.cumsum()
    peak = cumulative.cummax().clip(lower=0.0)
    drawdown = peak - cumulative
    downside = pnl[pnl < 0]
    pnl_std = float(pnl.std(ddof=1)) if len(pnl) > 1 else 0.0
    downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    stake = pd.to_numeric(ordered.get("stake_amount"), errors="coerce").dropna()
    duration = pd.to_numeric(ordered.get("duration_seconds"), errors="coerce").dropna()
    return {
        "total_trades": int(len(pnl)),
        "net_pnl": float(pnl.sum()),
        "gross_pnl": _sum_column(ordered, "gross_pnl"),
        "fees": _sum_column(ordered, "fees"),
        "funding": _sum_column(ordered, "funding"),
        "gross_profit": gross_profit,
        "gross_loss": -gross_loss_abs,
        "win_rate": float((pnl > 0).mean()),
        "loss_rate": float((pnl < 0).mean()),
        "profit_factor": _finite_or_none(profit_factor),
        "expectancy": float(pnl.mean()),
        "payoff_ratio": _finite_or_none(payoff),
        "average_win": average_win,
        "average_loss": average_loss,
        "median_pnl": float(pnl.median()),
        "maximum_drawdown": float(drawdown.max()),
        "longest_losing_streak": longest_losing_streak(pnl),
        "approximate_sharpe_per_trade": (
            float(pnl.mean() / pnl_std * math.sqrt(len(pnl))) if pnl_std > 0 else None
        ),
        "approximate_sortino_per_trade": (
            float(pnl.mean() / downside_std * math.sqrt(len(pnl))) if downside_std > 0 else None
        ),
        "average_duration_seconds": float(duration.mean()) if not duration.empty else None,
        "capital_turnover": (
            float(stake.sum() / stake.max()) if not stake.empty and stake.max() > 0 else None
        ),
        "average_stake": float(stake.mean()) if not stake.empty else None,
        "maximum_stake": float(stake.max()) if not stake.empty else None,
    }


def build_path_analysis(frame: pd.DataFrame) -> dict[str, Any]:
    status = frame.get("candle_alignment_status", pd.Series("missing", index=frame.index)).astype(
        "string"
    )
    aligned = frame.loc[status.eq("aligned")].copy()
    timeframe_counts = (
        aligned.get("candle_timeframe", pd.Series(dtype="string"))
        .astype("string")
        .fillna("unknown")
        .value_counts()
        .sort_index()
        .to_dict()
    )
    if aligned.empty:
        return {
            "aligned_trade_count": 0,
            "missing_candle_path_count": int(len(frame)),
            "timeframe_counts": {},
            "average_mfe_pct": None,
            "average_mae_pct": None,
            "average_profit_giveback": None,
            "winners_that_finished_as_loss_count": 0,
            "losses_without_observed_recovery_count": 0,
            "average_time_to_mfe_seconds": None,
            "average_time_to_mae_seconds": None,
        }
    mfe = pd.to_numeric(aligned["mfe_pct"], errors="coerce")
    mae = pd.to_numeric(aligned["mae_pct"], errors="coerce")
    net = pd.to_numeric(aligned["net_pnl"], errors="coerce")
    max_profit = pd.to_numeric(aligned["max_unrealized_profit"], errors="coerce")
    return {
        "aligned_trade_count": int(len(aligned)),
        "missing_candle_path_count": int(len(frame) - len(aligned)),
        "timeframe_counts": {str(key): int(value) for key, value in timeframe_counts.items()},
        "average_mfe_pct": float(mfe.mean()) if mfe.notna().any() else None,
        "average_mae_pct": float(mae.mean()) if mae.notna().any() else None,
        "average_profit_giveback": _mean_column(aligned, "profit_giveback"),
        "winners_that_finished_as_loss_count": int(((max_profit > 0) & (net < 0)).sum()),
        "losses_without_observed_recovery_count": int(((max_profit <= 0) & (net < 0)).sum()),
        "average_time_to_mfe_seconds": _mean_column(aligned, "time_to_mfe_seconds"),
        "average_time_to_mae_seconds": _mean_column(aligned, "time_to_mae_seconds"),
    }


def build_segment_analysis(frame: pd.DataFrame) -> list[dict[str, Any]]:
    dimensions = (
        "symbol",
        "side",
        "hour_utc",
        "day_of_week",
        "duration_bucket",
        "stake_bucket",
        "leverage",
        "exit_reason",
        "regime",
        "volatility_bucket",
        "month",
        "week",
    )
    rows: list[dict[str, Any]] = []
    for dimension in dimensions:
        if dimension not in frame.columns:
            continue
        values = frame[dimension].astype("string").fillna("unknown")
        for value in sorted(values.unique().tolist()):
            subset = frame.loc[values.eq(value)]
            metrics = financial_metrics(subset)
            rows.append(
                {
                    "segment_dimension": dimension,
                    "segment_value": str(value),
                    "trade_count": int(len(subset)),
                    "net_pnl": metrics["net_pnl"],
                    "profit_factor": metrics["profit_factor"],
                    "win_rate": metrics["win_rate"],
                    "expectancy": metrics["expectancy"],
                    "maximum_drawdown": metrics["maximum_drawdown"],
                }
            )
    return rows


def build_block_candidates(
    frame: pd.DataFrame,
    segments: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    baseline = financial_metrics(frame)
    split_index = max(1, int(len(frame) * 0.70))
    train = frame.iloc[:split_index]
    oos = frame.iloc[split_index:]
    minimum_trades = max(5, int(math.ceil(len(frame) * 0.02)))
    candidates: list[dict[str, Any]] = []
    for segment in segments:
        trade_count = int(segment["trade_count"])
        segment_pnl = float(segment["net_pnl"])
        if trade_count < minimum_trades or segment_pnl >= 0:
            continue
        dimension = str(segment["segment_dimension"])
        value = str(segment["segment_value"])
        if dimension not in ENTRY_RULE_DIMENSIONS:
            continue
        if value.casefold().endswith("unknown") or trade_count > int(len(frame) * 0.80):
            continue
        full_mask = _segment_mask(frame, dimension, value)
        train_mask = _segment_mask(train, dimension, value)
        oos_mask = _segment_mask(oos, dimension, value)
        candidate = frame.loc[~full_mask]
        candidate_metrics = financial_metrics(candidate)
        train_removed_pnl = _sum_pnl(train.loc[train_mask])
        oos_removed_pnl = _sum_pnl(oos.loc[oos_mask])
        oos_baseline = financial_metrics(oos)
        oos_candidate = financial_metrics(oos.loc[~oos_mask])
        stable = bool(
            train_mask.sum() >= 2
            and oos_mask.sum() >= 2
            and train_removed_pnl < 0
            and oos_removed_pnl < 0
        )
        decision = (
            "PROMOVER_PARA_BACKTEST"
            if stable and float(oos_candidate["net_pnl"]) > float(oos_baseline["net_pnl"])
            else "MANTER_EM_RESEARCH"
        )
        candidates.append(
            {
                "rule_id": f"block_{dimension}_{_slug(value)}",
                "hypothesis": f"Block {dimension}={value} before entry.",
                "condition": {"field": dimension, "operator": "equals", "value": value},
                "segment": f"{dimension}={value}",
                "trades_affected": int(full_mask.sum()),
                "baseline_net_pnl": baseline["net_pnl"],
                "removed_segment_pnl": segment_pnl,
                "candidate_net_pnl": candidate_metrics["net_pnl"],
                "delta_pnl": float(candidate_metrics["net_pnl"]) - float(baseline["net_pnl"]),
                "baseline_profit_factor": baseline["profit_factor"],
                "candidate_profit_factor": candidate_metrics["profit_factor"],
                "baseline_maximum_drawdown": baseline["maximum_drawdown"],
                "candidate_maximum_drawdown": candidate_metrics["maximum_drawdown"],
                "drawdown_delta": float(candidate_metrics["maximum_drawdown"])
                - float(baseline["maximum_drawdown"]),
                "in_sample_removed_pnl": train_removed_pnl,
                "out_of_sample_removed_pnl": oos_removed_pnl,
                "out_of_sample_trades_affected": int(oos_mask.sum()),
                "out_of_sample_delta_pnl": float(oos_candidate["net_pnl"])
                - float(oos_baseline["net_pnl"]),
                "stable_across_temporal_split": stable,
                "decision": decision,
            }
        )
    return sorted(
        candidates,
        key=lambda row: (
            not bool(row["stable_across_temporal_split"]),
            -float(row["delta_pnl"]),
            str(row["rule_id"]),
        ),
    )


def build_entry_timing_scenarios(
    frame: pd.DataFrame,
    candles: pd.DataFrame,
) -> list[dict[str, Any]]:
    if candles.empty:
        return []
    baseline = financial_metrics(frame)
    candle_groups = _candle_groups(candles)
    scenarios: list[dict[str, Any]] = [
        {
            "scenario_id": "entry_advance_1_candle",
            "offset_candles": -1,
            "status": "blocked",
            "reason": "decision_not_available_before_original_entry",
            "lookahead_allowed": False,
            "trades_affected": 0,
        }
    ]
    for offset in (1, 2, 3, 5):
        simulated = _simulate_delayed_entries(frame, candle_groups, offset)
        valid = simulated["candidate_net_pnl"].notna()
        candidate = frame.copy()
        candidate["candidate_net_pnl"] = np.where(
            valid,
            simulated["candidate_net_pnl"],
            candidate["net_pnl"],
        )
        metrics = financial_metrics(candidate, pnl_column="candidate_net_pnl")
        split = max(1, int(len(candidate) * 0.70))
        oos_baseline = financial_metrics(candidate.iloc[split:])
        oos_candidate = financial_metrics(candidate.iloc[split:], pnl_column="candidate_net_pnl")
        scenarios.append(
            {
                "scenario_id": f"entry_delay_{offset}_candles",
                "offset_candles": offset,
                "status": "ok",
                "reason": "counterfactual_entry_delay_evaluated",
                "lookahead_allowed": False,
                "trades_affected": int(valid.sum()),
                "baseline_net_pnl": baseline["net_pnl"],
                "candidate_net_pnl": metrics["net_pnl"],
                "delta_pnl": float(metrics["net_pnl"]) - float(baseline["net_pnl"]),
                "baseline_maximum_drawdown": baseline["maximum_drawdown"],
                "candidate_maximum_drawdown": metrics["maximum_drawdown"],
                "baseline_profit_factor": baseline["profit_factor"],
                "candidate_profit_factor": metrics["profit_factor"],
                "out_of_sample_delta_pnl": float(oos_candidate["net_pnl"])
                - float(oos_baseline["net_pnl"]),
            }
        )
    return scenarios


def build_exit_candidates(
    frame: pd.DataFrame,
    candles: pd.DataFrame,
) -> list[dict[str, Any]]:
    if candles.empty:
        return []
    configs: tuple[dict[str, Any], ...] = (
        {"strategy_id": "fixed_tp_100_sl_50_bps", "kind": "fixed_tp_sl", "tp": 0.010, "sl": 0.005},
        {"strategy_id": "fixed_tp_150_sl_100_bps", "kind": "fixed_tp_sl", "tp": 0.015, "sl": 0.010},
        {"strategy_id": "time_stop_60m", "kind": "time_stop", "minutes": 60},
        {"strategy_id": "time_stop_240m", "kind": "time_stop", "minutes": 240},
        {"strategy_id": "break_even_after_50_bps", "kind": "break_even", "trigger": 0.005},
        {
            "strategy_id": "trailing_75_50_bps",
            "kind": "trailing",
            "activation": 0.0075,
            "distance": 0.005,
        },
    )
    baseline = financial_metrics(frame)
    candle_groups = _candle_groups(candles)
    split = max(1, int(len(frame) * 0.70))
    results: list[dict[str, Any]] = []
    for config in configs:
        simulated = _simulate_exit_config(frame, candle_groups, config)
        candidate = frame.copy()
        valid = simulated["candidate_net_pnl"].notna()
        candidate["candidate_net_pnl"] = np.where(
            valid,
            simulated["candidate_net_pnl"],
            candidate["net_pnl"],
        )
        metrics = financial_metrics(candidate, pnl_column="candidate_net_pnl")
        oos = candidate.iloc[split:]
        oos_baseline = financial_metrics(oos)
        oos_candidate = financial_metrics(oos, pnl_column="candidate_net_pnl")
        delta = float(metrics["net_pnl"]) - float(baseline["net_pnl"])
        oos_delta = float(oos_candidate["net_pnl"]) - float(oos_baseline["net_pnl"])
        results.append(
            {
                "strategy_id": config["strategy_id"],
                "configuration": config,
                "trades_affected": int(valid.sum()),
                "baseline_net_pnl": baseline["net_pnl"],
                "candidate_net_pnl": metrics["net_pnl"],
                "delta_pnl": delta,
                "baseline_profit_factor": baseline["profit_factor"],
                "candidate_profit_factor": metrics["profit_factor"],
                "baseline_maximum_drawdown": baseline["maximum_drawdown"],
                "candidate_maximum_drawdown": metrics["maximum_drawdown"],
                "drawdown_delta": float(metrics["maximum_drawdown"])
                - float(baseline["maximum_drawdown"]),
                "out_of_sample_delta_pnl": oos_delta,
                "same_candle_rule": "stop_loss_first",
                "decision": (
                    "PROMOVER_PARA_BACKTEST"
                    if delta > 0 and oos_delta > 0 and valid.sum() >= 10
                    else "MANTER_EM_RESEARCH"
                ),
            }
        )
    return sorted(
        results,
        key=lambda row: (
            -float(row["out_of_sample_delta_pnl"]),
            -float(row["delta_pnl"]),
            str(row["strategy_id"]),
        ),
    )


def build_stake_candidates(frame: pd.DataFrame) -> list[dict[str, Any]]:
    baseline = financial_metrics(frame)
    stake = pd.to_numeric(frame["stake_amount"], errors="coerce")
    valid_stake = stake.where(stake > 0)
    train_end = max(1, int(len(frame) * 0.70))
    train_stake = valid_stake.iloc[:train_end]
    conservative_fixed = float(train_stake.quantile(0.25)) if train_stake.notna().any() else 0.0
    fixed_factor = (conservative_fixed / valid_stake).clip(lower=0.0, upper=1.0).fillna(0.0)
    high_volatility = frame["volatility_bucket"].astype("string").eq("volatility_high")
    volatility_factor = pd.Series(np.where(high_volatility, 0.5, 1.0), index=frame.index)
    loss_streak_factor = _loss_streak_factors(frame["net_pnl"], reduction=0.5)
    policies = (
        ("fixed_stake_conservative_q25", fixed_factor),
        ("reduce_high_volatility_50pct", volatility_factor),
        ("reduce_after_two_losses_50pct", loss_streak_factor),
    )
    rows: list[dict[str, Any]] = []
    for policy_id, factor in policies:
        candidate = frame.copy()
        candidate["candidate_net_pnl"] = candidate["net_pnl"] * factor
        candidate["candidate_stake"] = candidate["stake_amount"] * factor
        metrics = financial_metrics(candidate, pnl_column="candidate_net_pnl")
        candidate_stake = pd.to_numeric(candidate["candidate_stake"], errors="coerce")
        symbol_exposure = candidate.groupby("symbol")["candidate_stake"].sum()
        total_exposure = float(symbol_exposure.sum())
        concentration = (
            float(symbol_exposure.max() / total_exposure) if total_exposure > 0 else None
        )
        split = max(1, int(len(candidate) * 0.70))
        oos = candidate.iloc[split:]
        oos_baseline = financial_metrics(oos)
        oos_candidate = financial_metrics(oos, pnl_column="candidate_net_pnl")
        initial_capital = max(float(stake.max()) if stake.notna().any() else 0.0, 1.0)
        cumulative = (
            pd.to_numeric(candidate["candidate_net_pnl"], errors="coerce").fillna(0).cumsum()
        )
        rows.append(
            {
                "policy_id": policy_id,
                "baseline_net_pnl": baseline["net_pnl"],
                "candidate_net_pnl": metrics["net_pnl"],
                "delta_pnl": float(metrics["net_pnl"]) - float(baseline["net_pnl"]),
                "baseline_maximum_drawdown": baseline["maximum_drawdown"],
                "candidate_maximum_drawdown": metrics["maximum_drawdown"],
                "out_of_sample_delta_pnl": float(oos_candidate["net_pnl"])
                - float(oos_baseline["net_pnl"]),
                "historical_risk_of_ruin": bool(cumulative.min() <= -initial_capital),
                "average_exposure": float(candidate_stake.mean()),
                "maximum_exposure": float(candidate_stake.max()),
                "symbol_concentration": concentration,
                "capital_turnover": (
                    float(candidate_stake.sum() / candidate_stake.max())
                    if candidate_stake.max() > 0
                    else None
                ),
                "worst_losing_streak": metrics["longest_losing_streak"],
                "maximum_stake_multiplier": float(factor.max()),
                "increases_operational_stake": False,
                "decision": "MANTER_EM_RESEARCH",
            }
        )
    return sorted(
        rows, key=lambda row: (float(row["candidate_maximum_drawdown"]), str(row["policy_id"]))
    )


def _simulate_delayed_entries(
    frame: pd.DataFrame,
    candle_groups: Mapping[tuple[str, str], pd.DataFrame],
    offset: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, trade in frame.iterrows():
        path = _trade_candle_path(trade, candle_groups)
        if len(path) <= offset:
            rows.append({"candidate_net_pnl": None})
            continue
        delayed_entry = float(path.iloc[offset]["open"])
        exit_price = float(trade["exit_price"])
        gross = _gross_from_prices(trade, delayed_entry, exit_price)
        rows.append({"candidate_net_pnl": gross - float(trade["fees"]) - float(trade["funding"])})
    return pd.DataFrame(rows, index=frame.index)


def _simulate_exit_config(
    frame: pd.DataFrame,
    candle_groups: Mapping[tuple[str, str], pd.DataFrame],
    config: Mapping[str, Any],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, trade in frame.iterrows():
        path = _trade_candle_path(trade, candle_groups)
        if path.empty:
            rows.append({"candidate_net_pnl": None})
            continue
        exit_price = _candidate_exit_price(trade, path, config)
        gross = _gross_from_prices(trade, float(trade["entry_price"]), exit_price)
        rows.append({"candidate_net_pnl": gross - float(trade["fees"]) - float(trade["funding"])})
    return pd.DataFrame(rows, index=frame.index)


def _candidate_exit_price(
    trade: pd.Series,
    path: pd.DataFrame,
    config: Mapping[str, Any],
) -> float:
    kind = str(config["kind"])
    entry = float(trade["entry_price"])
    side = str(trade["side"])
    if kind == "time_stop":
        target = trade["open_time_utc"] + pd.Timedelta(minutes=int(config["minutes"]))
        eligible = path.loc[path["ts"].ge(target)]
        return (
            float(eligible.iloc[0]["close"]) if not eligible.empty else float(trade["exit_price"])
        )
    if kind == "fixed_tp_sl":
        tp = float(config["tp"])
        sl = float(config["sl"])
        tp_price = entry * (1 + tp if side == "long" else 1 - tp)
        sl_price = entry * (1 - sl if side == "long" else 1 + sl)
        for _, candle in path.iterrows():
            tp_hit = (
                float(candle["high"]) >= tp_price
                if side == "long"
                else float(candle["low"]) <= tp_price
            )
            sl_hit = (
                float(candle["low"]) <= sl_price
                if side == "long"
                else float(candle["high"]) >= sl_price
            )
            if sl_hit:
                return sl_price
            if tp_hit:
                return tp_price
        return float(trade["exit_price"])
    if kind == "break_even":
        trigger = float(config["trigger"])
        active = False
        for _, candle in path.iterrows():
            high = float(candle["high"])
            low = float(candle["low"])
            trigger_hit = (
                high >= entry * (1 + trigger) if side == "long" else low <= entry * (1 - trigger)
            )
            stop_hit = low <= entry if side == "long" else high >= entry
            if active and stop_hit:
                return entry
            if trigger_hit:
                if stop_hit:
                    return entry
                active = True
        return float(trade["exit_price"])
    if kind == "trailing":
        activation = float(config["activation"])
        distance = float(config["distance"])
        active = False
        extreme = entry
        for _, candle in path.iterrows():
            high = float(candle["high"])
            low = float(candle["low"])
            extreme = max(extreme, high) if side == "long" else min(extreme, low)
            favorable = (extreme - entry) / entry if side == "long" else (entry - extreme) / entry
            if favorable >= activation:
                active = True
            if active:
                stop = extreme * (1 - distance if side == "long" else 1 + distance)
                if (side == "long" and low <= stop) or (side == "short" and high >= stop):
                    return stop
        return float(trade["exit_price"])
    raise ValueError(f"unknown_exit_candidate_kind:{kind}")


def build_source_inventory(
    *,
    paths: ProfitResearchPaths,
    snapshot_frame: pd.DataFrame,
    snapshot_meta: Mapping[str, Any],
    candles_inventory: Mapping[str, Any],
) -> list[dict[str, Any]]:
    snapshot = inventory_frame(
        paths.snapshot_db,
        snapshot_frame,
        source_role="primary_analytical_source",
        source_format="sqlite_snapshot",
    )
    snapshot.update(
        query_only=bool(snapshot_meta.get("snapshot_query_only")),
        temporary_copy_used=bool(snapshot_meta.get("snapshot_temp_copy_used")),
        source_hash_preserved=bool(snapshot_meta.get("snapshot_source_hashes_preserved")),
    )
    runtime = inventory_sqlite_readonly(
        paths.runtime_db,
        source_role="current_operational_source",
    )
    csv_inventory = inventory_tabular(paths.closed_trades_csv)
    csv_inventory["source_role"] = "analytical_replica"
    feedback_inventory = inventory_tabular(paths.feedback)
    feedback_inventory["source_role"] = "auxiliary_feedback_source"
    microbatch_inventory = inventory_tabular(paths.microbatch)
    microbatch_inventory["source_role"] = "incomplete_training_evidence"
    master_inventory = inventory_tabular(paths.trader_master, metadata_only=True)
    master_inventory["source_role"] = "protected_legacy_reference_not_loaded"
    candle_item = dict(candles_inventory)
    candle_item["source_role"] = "market_context_source"
    image_inventory = inventory_image_directory(paths.new_trades_source)
    image_inventory["source_role"] = "raw_unstructured_trade_candidate_source"
    image_inventory["canonical_ocr_handoff"] = inventory_ocr_handoff(paths.ocr_handoff)
    return [
        runtime,
        snapshot,
        csv_inventory,
        feedback_inventory,
        microbatch_inventory,
        candle_item,
        master_inventory,
        image_inventory,
    ]


def reconcile_sources(
    paths: ProfitResearchPaths,
    analytical: pd.DataFrame,
) -> dict[str, Any]:
    source_ids = set(analytical["order_id"].dropna().astype(str))
    result: dict[str, Any] = {
        "authoritative_snapshot_order_id_count": len(source_ids),
        "csv_order_id_count": 0,
        "feedback_order_id_count": 0,
        "microbatch_order_id_count": 0,
        "snapshot_missing_from_csv_count": None,
        "csv_missing_from_snapshot_count": None,
        "snapshot_missing_from_feedback_count": None,
        "snapshot_missing_from_microbatch_count": None,
        "duplicate_order_id_counts": {},
    }
    for label, path in (
        ("csv", paths.closed_trades_csv),
        ("feedback", paths.feedback),
        ("microbatch", paths.microbatch),
    ):
        frame = read_optional_table(path)
        if frame.empty or "order_id" not in frame.columns:
            continue
        ids = frame["order_id"].dropna().astype(str).str.strip()
        id_set = set(ids[ids.ne("")])
        result[f"{label}_order_id_count"] = len(id_set)
        result["duplicate_order_id_counts"][label] = int(ids.duplicated(keep=False).sum())
        result[f"snapshot_missing_from_{label}_count"] = len(source_ids - id_set)
        result[f"{label}_missing_from_snapshot_count"] = len(id_set - source_ids)
    result["sources_diverge"] = any(
        isinstance(value, int) and value > 0
        for key, value in result.items()
        if "missing_from" in key
    )
    return result


def inventory_tabular(path: Path, *, metadata_only: bool = False) -> dict[str, Any]:
    item: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists() and path.is_file(),
        "format": path.suffix.lower().lstrip(".") or "unknown",
        "size_bytes": path.stat().st_size if path.exists() and path.is_file() else None,
        "row_count": None,
        "columns": [],
        "min_timestamp_utc": None,
        "max_timestamp_utc": None,
        "symbols": [],
        "duplicate_order_id_count": None,
        "integrity_status": "missing",
        "freshness_seconds": None,
        "metadata_only": metadata_only,
    }
    if not item["exists"]:
        return item
    if metadata_only:
        item["integrity_status"] = "protected_not_loaded"
        item["sha256"] = file_sha256(path)
        return item
    try:
        frame = read_optional_table(path)
    except (OSError, ValueError, ImportError, pd.errors.ParserError) as exc:
        item.update(
            integrity_status="unreadable",
            error=f"{type(exc).__name__}:{exc}",
        )
        return item
    return inventory_frame(path, frame, source_role=None, source_format=item["format"])


def inventory_frame(
    path: Path,
    frame: pd.DataFrame,
    *,
    source_role: str | None,
    source_format: str,
) -> dict[str, Any]:
    min_ts, max_ts = timestamp_bounds(frame)
    order_ids = (
        frame["order_id"].dropna().astype(str).str.strip()
        if "order_id" in frame.columns
        else pd.Series(dtype="string")
    )
    symbols = values_for_column(frame, ("symbol", "moeda", "pair"), normalize_symbol)
    closed_count: int | None = None
    open_count: int | None = None
    if "is_open" in frame.columns:
        is_open = frame["is_open"].astype(bool)
        open_count = int(is_open.sum())
        closed_count = int((~is_open).sum())
    elif any(
        column in frame.columns for column in ("close_time_utc", "close_date", "horario_fechamento")
    ):
        closed_count = int(len(frame))
        open_count = 0
    return {
        "path": str(path),
        "exists": True,
        "format": source_format,
        "size_bytes": path.stat().st_size if path.exists() and path.is_file() else None,
        "row_count": int(len(frame)),
        "columns": [str(column) for column in frame.columns],
        "min_timestamp_utc": min_ts,
        "max_timestamp_utc": max_ts,
        "symbols": symbols,
        "open_trade_count": open_count,
        "closed_trade_count": closed_count,
        "pnl_column": first_existing(frame, ("net_pnl", "close_profit_abs", "pnl_fechado")),
        "stake_column": first_existing(frame, ("stake_amount", "volume_posicao")),
        "fees_available": any(
            column in frame.columns for column in ("fees", "fee_open_cost", "taxa_1")
        ),
        "funding_available": any(column in frame.columns for column in ("funding", "funding_fees")),
        "exit_reason_available": "exit_reason" in frame.columns,
        "strategy_available": "strategy" in frame.columns,
        "duplicate_order_id_count": int(order_ids.duplicated(keep=False).sum()),
        "integrity_status": "ok",
        "freshness_seconds": _freshness_seconds(max_ts),
        "source_role": source_role,
        "metadata_only": False,
    }


def inventory_sqlite_readonly(path: Path, *, source_role: str) -> dict[str, Any]:
    item: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists() and path.is_file(),
        "format": "sqlite",
        "source_role": source_role,
        "row_count": None,
        "open_trade_count": None,
        "closed_trade_count": None,
        "columns": [],
        "integrity_status": "missing",
        "query_only": False,
        "source_hash_preserved": None,
    }
    if not item["exists"]:
        return item
    before = file_sha256(path)
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=2.0)
        try:
            connection.execute("PRAGMA query_only = ON")
            item["query_only"] = connection.execute("PRAGMA query_only").fetchone()[0] == 1
            item["columns"] = [
                str(row[1]) for row in connection.execute("PRAGMA table_info(trades)").fetchall()
            ]
            total, open_count = connection.execute(
                "SELECT COUNT(*), SUM(CASE WHEN is_open = 1 THEN 1 ELSE 0 END) FROM trades"
            ).fetchone()
            item["row_count"] = int(total)
            item["open_trade_count"] = int(open_count or 0)
            item["closed_trade_count"] = int(total) - int(open_count or 0)
            bounds = connection.execute(
                "SELECT MIN(open_date), MAX(close_date) FROM trades"
            ).fetchone()
            item["min_timestamp_utc"] = _iso_value(bounds[0])
            item["max_timestamp_utc"] = _iso_value(bounds[1])
            item["integrity_status"] = "ok"
        finally:
            connection.close()
    except (OSError, sqlite3.Error, ValueError) as exc:
        item.update(
            integrity_status="unreadable",
            error=f"{type(exc).__name__}:{exc}",
        )
    item["source_hash_preserved"] = before == file_sha256(path)
    return item


def inventory_image_directory(path: Path) -> dict[str, Any]:
    item: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists() and path.is_dir(),
        "format": "image_directory",
        "file_count": 0,
        "total_size_bytes": 0,
        "extensions": {},
        "normalized_trade_row_count": 0,
        "integrity_status": "missing",
        "requires_separate_ocr_normalization": True,
        "incorporated_into_analysis": False,
        "discovery_scope": "top_level_only_explicit_lot",
        "nested_files_ignored": 0,
        "duplicate_image_rows": 0,
        "order_id_policy": "optional_not_authoritative_for_deduplication",
        "deduplication_policy": ("image_sha256_then_timestamp_symbol_side_prices_volumes_pnl_fees"),
        "ocr_policy": "black_rectangle_rois_only_red_top_ignored",
        "ocr_fields": list(OCR_CANONICAL_FIELDS),
        "authorized_outputs": [
            "raw_ocr_package",
            "normalized_package",
            "staging_review",
            "validation_report",
            "candidate_import_ready",
            "master_preview_only",
            "research_only_snapshot",
        ],
    }
    if not item["exists"]:
        return item
    files = sorted(
        candidate
        for candidate in path.iterdir()
        if candidate.is_file() and candidate.suffix.casefold() in OCR_IMAGE_EXTENSIONS
    )
    nested_files = sum(
        1 for candidate in path.rglob("*") if candidate.is_file() and candidate.parent != path
    )
    extensions: dict[str, int] = {}
    manifest_rows: list[str] = []
    content_hashes: list[str] = []
    total = 0
    for file_path in files:
        size = file_path.stat().st_size
        total += size
        suffix = file_path.suffix.casefold() or "<none>"
        extensions[suffix] = extensions.get(suffix, 0) + 1
        content_hash = file_sha256(file_path)
        content_hashes.append(str(content_hash))
        manifest_rows.append(f"{file_path.relative_to(path).as_posix()}|{size}|{content_hash}")
    item.update(
        file_count=len(files),
        total_size_bytes=total,
        extensions=dict(sorted(extensions.items())),
        nested_files_ignored=nested_files,
        duplicate_image_rows=len(content_hashes) - len(set(content_hashes)),
        filename_size_manifest_sha256=hashlib.sha256(
            "\n".join(manifest_rows).encode("utf-8")
        ).hexdigest(),
        integrity_status="raw_unstructured",
    )
    return item


def inventory_ocr_handoff(path: Path) -> dict[str, Any]:
    exists = path.exists() and path.is_file()
    return {
        "path": str(path),
        "exists": exists,
        "format": "pdf",
        "size_bytes": path.stat().st_size if exists else None,
        "sha256": file_sha256(path),
        "contract_status": "resolved" if exists else "missing",
        "historical_counts_require_revalidation": True,
        "order_id_rule_override": {
            "empty_order_id_is_extraction_error": False,
            "duplicate_order_id_alone_is_blocking": False,
            "image_duplicate_is_batch_duplicate": True,
        },
    }


def build_economic_conclusion(
    global_metrics: Mapping[str, Any],
    block_candidates: Sequence[Mapping[str, Any]],
    entry_timing_scenarios: Sequence[Mapping[str, Any]],
    exit_candidates: Sequence[Mapping[str, Any]],
    stake_candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    best_block = block_candidates[0] if block_candidates else None
    best_exit = exit_candidates[0] if exit_candidates else None
    evaluated_entries = [row for row in entry_timing_scenarios if row.get("status") == "ok"]
    best_entry = max(
        evaluated_entries,
        key=lambda row: float(row.get("out_of_sample_delta_pnl", float("-inf"))),
        default=None,
    )
    lowest_drawdown_stake = stake_candidates[0] if stake_candidates else None
    actions: list[str] = []
    if best_block is not None:
        actions.append(
            "Backtest the pre-entry block "
            f"{best_block['segment']} affecting {best_block['trades_affected']} trades; "
            f"historical PnL delta {float(best_block['delta_pnl']):.4f}."
        )
    if best_entry is not None:
        if float(best_entry["delta_pnl"]) > 0 and float(best_entry["out_of_sample_delta_pnl"]) > 0:
            actions.append(
                "Backtest entry timing candidate "
                f"{best_entry['scenario_id']}; full-sample delta "
                f"{float(best_entry['delta_pnl']):.4f} and OOS delta "
                f"{float(best_entry['out_of_sample_delta_pnl']):.4f}."
            )
        else:
            actions.append(
                "Do not change entry timing: no tested delay improved both the full "
                "sample and the temporal OOS segment."
            )
    if best_exit is not None and float(best_exit["out_of_sample_delta_pnl"]) > 0:
        actions.append(
            "Backtest exit candidate "
            f"{best_exit['strategy_id']}; historical PnL delta "
            f"{float(best_exit['delta_pnl']):.4f} and OOS delta "
            f"{float(best_exit['out_of_sample_delta_pnl']):.4f}."
        )
    elif best_exit is not None:
        actions.append(
            "Do not promote an exit change: the best tested exit OOS delta was "
            f"{float(best_exit['out_of_sample_delta_pnl']):.4f}."
        )
    if lowest_drawdown_stake is not None:
        actions.append(
            "Keep stake unchanged operationally and research "
            f"{lowest_drawdown_stake['policy_id']}; simulated maximum drawdown "
            f"{float(lowest_drawdown_stake['candidate_maximum_drawdown']):.4f}."
        )
    return {
        "baseline_net_pnl": global_metrics.get("net_pnl"),
        "baseline_maximum_drawdown": global_metrics.get("maximum_drawdown"),
        "best_block_candidate": best_block,
        "best_entry_timing_candidate": best_entry,
        "best_exit_candidate": best_exit,
        "best_conservative_stake_candidate": lowest_drawdown_stake,
        "recommended_actions": actions,
        "promotion_authorized": False,
        "paper_change_authorized": False,
        "stake_change_authorized": False,
    }


def build_limitations(
    dataset: pd.DataFrame,
    candles: pd.DataFrame,
    source_inventory: Sequence[Mapping[str, Any]],
) -> list[str]:
    limitations: list[str] = []
    blocked = int((~dataset["analysis_eligible"]).sum())
    if blocked:
        limitations.append(f"financially_unreconciled_or_incomplete_trades:{blocked}")
    missing_candles = int(
        dataset.get("candle_alignment_status", pd.Series("missing", index=dataset.index))
        .ne("aligned")
        .sum()
    )
    if missing_candles:
        limitations.append(f"trades_without_complete_candle_path:{missing_candles}")
    if candles.empty:
        limitations.append("market_candles_unavailable")
    image_source = next(
        (
            item
            for item in source_inventory
            if item.get("source_role") == "raw_unstructured_trade_candidate_source"
        ),
        None,
    )
    if image_source and int(image_source.get("file_count", 0)) > 0:
        limitations.append("new_trade_source_is_raw_images_and_was_not_normalized_or_incorporated")
    limitations.extend(
        [
            "counterfactual_exit_costs_reuse_observed_fee_and_funding_totals",
            "out_of_sample_evidence_uses_one_strict_70_30_temporal_split",
            "candidate_rules_require_event_driven_and_walk_forward_backtests",
            "no_causal_claim_is_made_from_observational_segments",
        ]
    )
    return limitations


def render_markdown(report: Mapping[str, Any]) -> str:
    global_metrics = report.get("global_metrics", {})
    conclusion = report.get("economic_conclusion", {})
    actions = conclusion.get("recommended_actions", []) if isinstance(conclusion, Mapping) else []
    lines = [
        "# Profit Research: Paper Trades, Candles and Economic Actions V1",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Eligible trades: `{report.get('eligible_trade_count')}`",
        f"- Net PnL: `{global_metrics.get('net_pnl') if isinstance(global_metrics, Mapping) else None}`",
        f"- Profit factor: `{global_metrics.get('profit_factor') if isinstance(global_metrics, Mapping) else None}`",
        f"- Maximum drawdown: `{global_metrics.get('maximum_drawdown') if isinstance(global_metrics, Mapping) else None}`",
        "",
        "## Economic actions",
        "",
    ]
    lines.extend(f"- {action}" for action in actions)
    lines.extend(
        [
            "",
            "## Restrictions",
            "",
            "No rule, exit, or stake policy is authorized for paper/live runtime. Candidates require independent backtest and walk-forward evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def write_research_outputs(
    paths: ProfitResearchPaths,
    dataset: pd.DataFrame,
    report: Mapping[str, Any],
) -> None:
    paths.output_dataset.parent.mkdir(parents=True, exist_ok=True)
    paths.report_json.parent.mkdir(parents=True, exist_ok=True)
    paths.report_markdown.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_parquet(paths.output_dataset, dataset)
    _atomic_write_text(paths.report_json, stable_json(report, pretty=True))
    _atomic_write_text(paths.report_markdown, render_markdown(report))


def _base_report(paths: ProfitResearchPaths, write: bool) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked",
        "reason": "not_evaluated",
        "decision": "MANTER_EM_RESEARCH",
        "write_requested": bool(write),
        "write_performed": False,
        "output_paths": {
            "dataset": str(paths.output_dataset),
            "json": str(paths.report_json),
            "markdown": str(paths.report_markdown),
        },
        "source_inventory": [],
        "source_reconciliation": {},
        "analytical_dataset_rows": 0,
        "eligible_trade_count": 0,
        "blocked_trade_count": 0,
        "global_metrics": _empty_financial_metrics(),
        "path_analysis": {},
        "blocked_trade_reason_counts": {},
        "segment_analysis": [],
        "top_profitable_segments": [],
        "top_harmful_segments": [],
        "candidate_block_rules": [],
        "entry_timing_scenarios": [],
        "candidate_exit_changes": [],
        "candidate_stake_policies": [],
        "economic_conclusion": {},
        "data_limitations": [],
        "warnings": [],
        "validation_errors": [],
        "trader_master_sha256_before": None,
        "trader_master_sha256_after": None,
        "trader_master_hash_preserved": None,
        **SAFETY_FLAGS,
    }


def _finalize_master_hash(
    report: dict[str, Any],
    paths: ProfitResearchPaths,
) -> dict[str, Any]:
    after = file_sha256(paths.trader_master)
    report["trader_master_sha256_after"] = after
    report["trader_master_hash_preserved"] = report.get("trader_master_sha256_before") == after
    return report


def _empty_financial_metrics() -> dict[str, Any]:
    return {
        "total_trades": 0,
        "net_pnl": 0.0,
        "gross_pnl": 0.0,
        "fees": 0.0,
        "funding": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "win_rate": 0.0,
        "loss_rate": 0.0,
        "profit_factor": None,
        "expectancy": 0.0,
        "payoff_ratio": None,
        "average_win": 0.0,
        "average_loss": 0.0,
        "median_pnl": 0.0,
        "maximum_drawdown": 0.0,
        "longest_losing_streak": 0,
        "approximate_sharpe_per_trade": None,
        "approximate_sortino_per_trade": None,
        "average_duration_seconds": None,
        "capital_turnover": None,
        "average_stake": None,
        "maximum_stake": None,
    }


def _candle_groups(candles: pd.DataFrame) -> dict[tuple[str, str], pd.DataFrame]:
    return {
        (str(symbol), str(timeframe)): group.sort_values("ts").reset_index(drop=True)
        for (symbol, timeframe), group in candles.groupby(["symbol", "tf"], sort=True)
    }


def _trade_candle_path(
    trade: pd.Series,
    groups: Mapping[tuple[str, str], pd.DataFrame],
) -> pd.DataFrame:
    key = (str(trade["symbol"]), str(trade.get("candle_timeframe") or "unknown"))
    group = groups.get(key)
    if group is None or group.empty:
        return pd.DataFrame()
    timestamps = group["ts"]
    start = int(timestamps.searchsorted(trade["open_time_utc"], side="left"))
    end = int(timestamps.searchsorted(trade["close_time_utc"], side="right"))
    return group.iloc[start:end]


def _timeframe_groups(candles: pd.DataFrame) -> dict[str, list[pd.DataFrame]]:
    grouped: dict[str, list[pd.DataFrame]] = {}
    for (symbol, timeframe), frame in candles.groupby(["symbol", "tf"], sort=True):
        grouped.setdefault(str(symbol), []).append(frame.sort_values("ts").reset_index(drop=True))
    for symbol in grouped:
        grouped[symbol].sort(key=lambda frame: _timeframe_minutes(str(frame.iloc[0]["tf"])))
    return grouped


def _select_covering_candles(
    candidates: Sequence[pd.DataFrame],
    open_time: pd.Timestamp,
    close_time: pd.Timestamp,
) -> pd.DataFrame | None:
    for frame in candidates:
        if frame.empty:
            continue
        minutes = _timeframe_minutes(str(frame.iloc[0]["tf"]))
        final_availability = frame["ts"].iloc[-1] + pd.Timedelta(minutes=minutes)
        if frame["ts"].iloc[0] <= open_time and final_availability >= close_time:
            return frame
    return None


def _timeframe_minutes(value: str) -> int:
    text = value.strip().casefold()
    if text.endswith("m") and text[:-1].isdigit():
        return int(text[:-1])
    if text.endswith("h") and text[:-1].isdigit():
        return int(text[:-1]) * 60
    return 10**9


def _gross_from_prices(trade: pd.Series, entry: float, exit_price: float) -> float:
    quantity = float(trade["quantity"])
    contract_size = float(trade["contract_size"])
    if str(trade["side"]) == "long":
        return (exit_price - entry) * quantity * contract_size
    return (entry - exit_price) * quantity * contract_size


def _loss_streak_factors(pnl: pd.Series, *, reduction: float) -> pd.Series:
    values = pd.to_numeric(pnl, errors="coerce").fillna(0.0).tolist()
    factors: list[float] = []
    streak = 0
    for value in values:
        factors.append(reduction if streak >= 2 else 1.0)
        streak = streak + 1 if value < 0 else 0
    return pd.Series(factors, index=pnl.index, dtype=float)


def _quantile_bucket(series: pd.Series, prefix: str) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().sum() < 3 or numeric.nunique(dropna=True) < 3:
        return pd.Series(f"{prefix}_unknown", index=series.index, dtype="string")
    low = float(numeric.quantile(1 / 3))
    high = float(numeric.quantile(2 / 3))
    return pd.Series(
        np.select(
            [numeric.le(low), numeric.le(high), numeric.gt(high)],
            [f"{prefix}_low", f"{prefix}_medium", f"{prefix}_high"],
            default=f"{prefix}_unknown",
        ),
        index=series.index,
        dtype="string",
    )


def _segment_mask(frame: pd.DataFrame, dimension: str, value: str) -> pd.Series:
    return frame[dimension].astype("string").fillna("unknown").eq(value)


def _top_segments(
    segments: Sequence[Mapping[str, Any]],
    *,
    profitable: bool,
) -> list[dict[str, Any]]:
    filtered = [
        dict(row)
        for row in segments
        if (float(row["net_pnl"]) > 0 if profitable else float(row["net_pnl"]) < 0)
    ]
    return sorted(
        filtered,
        key=lambda row: (
            -float(row["net_pnl"]) if profitable else float(row["net_pnl"]),
            str(row["segment_dimension"]),
            str(row["segment_value"]),
        ),
    )[:5]


def longest_losing_streak(pnl: pd.Series) -> int:
    maximum = current = 0
    for value in pnl.tolist():
        current = current + 1 if float(value) < 0 else 0
        maximum = max(maximum, current)
    return maximum


def read_optional_table(path: Path) -> pd.DataFrame:
    if not path.exists() or not path.is_file():
        return pd.DataFrame()
    suffix = path.suffix.casefold()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return pd.DataFrame(payload if isinstance(payload, list) else [payload])
    raise ValueError(f"unsupported_tabular_format:{suffix}")


def timestamp_bounds(frame: pd.DataFrame) -> tuple[str | None, str | None]:
    for column in (
        "close_time_utc",
        "close_date",
        "horario_fechamento",
        "open_time_utc",
        "open_date",
        "horario_abertura",
        "ts",
    ):
        if column not in frame.columns:
            continue
        values = pd.to_datetime(frame[column], utc=True, errors="coerce").dropna()
        if not values.empty:
            return values.min().isoformat(), values.max().isoformat()
    return None, None


def values_for_column(
    frame: pd.DataFrame,
    candidates: Sequence[str],
    transform: Callable[[Any], object],
) -> list[str]:
    for column in candidates:
        if column in frame.columns:
            return sorted(
                {
                    str(transform(value))
                    for value in frame[column].dropna().tolist()
                    if str(value).strip()
                }
            )
    return []


def first_existing(frame: pd.DataFrame, candidates: Sequence[str]) -> str | None:
    return next((column for column in candidates if column in frame.columns), None)


def normalize_symbol(value: Any) -> str:
    text = str(value or "").strip().upper().replace("_", "").replace("/", "")
    if ":" in text:
        text = text.split(":", 1)[0]
    return text


def file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json(payload: Mapping[str, Any], *, pretty: bool = False) -> str:
    return json.dumps(
        payload,
        indent=2 if pretty else None,
        sort_keys=True,
        ensure_ascii=False,
        default=json_safe,
    ) + ("\n" if pretty else "")


def json_safe(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _sum_column(frame: pd.DataFrame, column: str) -> float:
    if column not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[column], errors="coerce").fillna(0.0).sum())


def _sum_pnl(frame: pd.DataFrame) -> float:
    return _sum_column(frame, "net_pnl")


def _mean_column(frame: pd.DataFrame, column: str) -> float | None:
    if column not in frame.columns:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.mean()) if not values.empty else None


def _finite_or_none(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def _iso_min(series: pd.Series) -> str | None:
    values = pd.to_datetime(series, utc=True, errors="coerce").dropna()
    return values.min().isoformat() if not values.empty else None


def _iso_max(series: pd.Series) -> str | None:
    values = pd.to_datetime(series, utc=True, errors="coerce").dropna()
    return values.max().isoformat() if not values.empty else None


def _iso_value(value: Any) -> str | None:
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    return parsed.isoformat() if pd.notna(parsed) else None


def _freshness_seconds(max_timestamp: str | None) -> float | None:
    if max_timestamp is None:
        return None
    value = pd.Timestamp(max_timestamp)
    return float((pd.Timestamp.now(tz="UTC") - value).total_seconds())


def _slug(value: str) -> str:
    return "".join(character.lower() if character.isalnum() else "_" for character in value).strip(
        "_"
    )


def _atomic_write_parquet(path: Path, frame: pd.DataFrame) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp.parquet",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        frame.to_parquet(temporary, index=False)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _atomic_write_text(path: Path, content: str) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
