from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from smartcrypto.execution.signal_producer import build_active_signals, inspect_signal_file
from smartcrypto.qlib_engine.fresh_prediction_runner import run_qlib_fresh_predictions
from smartcrypto.qlib_engine.market_features_refresh import refresh_qlib_market_features
from smartcrypto.qlib_engine.prediction_freshness import inspect_qlib_prediction_freshness


OK = "ok"
BLOCKED = "blocked"
MARKET_FEATURES_FAILED = "market_features_failed"
PREDICTIONS_FAILED = "predictions_failed"
PHASE13_FAILED = "phase13_failed"
STALE_AFTER_REFRESH = "stale_after_refresh"

DEFAULT_REPORT_PATH = Path("data/reports/qlib_paper_refresh_supervisor_report.json")
DEFAULT_MARKET_FEATURES_PATH = Path("data/features/market_features_60d.parquet")
DEFAULT_PREDICTIONS_PATH = Path("data/predictions/latest_qlib_predictions.parquet")
DEFAULT_SIGNAL_CONFIG_PATH = Path("config/signal_producer.yml")
DEFAULT_NEXT_RUN_SECONDS = 900
TRUE_VALUES = {"1", "true", "yes", "y", "on"}

MarketRefreshFn = Callable[..., dict[str, Any]]
PredictionRefreshFn = Callable[..., dict[str, Any]]
Phase13Fn = Callable[..., dict[str, Any]]
FreshnessFn = Callable[..., dict[str, Any]]
SignalInspectFn = Callable[[str | os.PathLike[str]], dict[str, Any]]


@dataclass(frozen=True)
class PaperRefreshSupervisorConfig:
    report_path: str | Path = DEFAULT_REPORT_PATH
    market_source_path: str | Path = "data/raw/futures_ohlcv_60d.parquet"
    existing_market_features_path: str | Path = DEFAULT_MARKET_FEATURES_PATH
    market_features_output_path: str | Path = DEFAULT_MARKET_FEATURES_PATH
    market_features_report_path: str | Path = "data/reports/qlib_market_features_refresh_report.json"
    qlib_model_path: str | Path = "data/models/qlib_market_model.joblib"
    qlib_model_config_path: str | Path = "config/qlib_model.yml"
    predictions_output_path: str | Path = DEFAULT_PREDICTIONS_PATH
    predictions_report_path: str | Path = "data/reports/qlib_fresh_prediction_runner_report.json"
    signal_config_path: str | Path = DEFAULT_SIGNAL_CONFIG_PATH
    pinned_signals_path: str | Path = "data/runtime/active_freqtrade_signals.json"
    max_prediction_age_minutes: int = 90
    max_input_data_age_minutes: int = 15
    phase13_validity_minutes: int = 45
    next_recommended_run_seconds: int = DEFAULT_NEXT_RUN_SECONDS
    public_download_enabled: bool = True
    public_download_lookback_candles: int = 1500
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    timeframe: str = "5m"


def run_paper_refresh_supervisor(
    config: PaperRefreshSupervisorConfig | None = None,
    *,
    market_refresh_fn: MarketRefreshFn = refresh_qlib_market_features,
    prediction_refresh_fn: PredictionRefreshFn = run_qlib_fresh_predictions,
    phase13_fn: Phase13Fn = build_active_signals,
    freshness_fn: FreshnessFn = inspect_qlib_prediction_freshness,
    signal_inspect_fn: SignalInspectFn = inspect_signal_file,
    write_report: bool = True,
) -> dict[str, Any]:
    cfg = config or PaperRefreshSupervisorConfig()
    unsafe = unsafe_runtime_flags()
    if unsafe:
        report = base_report(
            cfg,
            status=BLOCKED,
            reason=f"unsafe_runtime_flags:{','.join(unsafe)}",
        )
        if write_report:
            write_json(cfg.report_path, report)
        return report

    market_report = market_refresh_fn(
        source_path=cfg.market_source_path,
        existing_features_path=cfg.existing_market_features_path,
        output_path=cfg.market_features_output_path,
        report_path=cfg.market_features_report_path,
        symbols=list(cfg.symbols),
        timeframe=cfg.timeframe,
        max_source_age_minutes=cfg.max_input_data_age_minutes,
        public_download_enabled=cfg.public_download_enabled,
        public_download_lookback_candles=cfg.public_download_lookback_candles,
    )
    if market_report.get("status") != OK:
        report = base_report(
            cfg,
            status=MARKET_FEATURES_FAILED,
            reason=str(market_report.get("reason") or "market_features_refresh_failed"),
            market_report=market_report,
        )
        if write_report:
            write_json(cfg.report_path, report)
        return report
    if market_report.get("operational_feature_schema_ok") is False:
        report = base_report(
            cfg,
            status=MARKET_FEATURES_FAILED,
            reason="operational_feature_schema_invalid",
            market_report=market_report,
        )
        if write_report:
            write_json(cfg.report_path, report)
        return report

    prediction_report = prediction_refresh_fn(
        market_features_path=cfg.market_features_output_path,
        model_path=cfg.qlib_model_path,
        output_path=cfg.predictions_output_path,
        report_path=cfg.predictions_report_path,
        config_path=cfg.qlib_model_config_path,
        max_allowed_age_minutes=cfg.max_prediction_age_minutes,
        max_input_data_age_minutes=cfg.max_input_data_age_minutes,
    )
    if prediction_report.get("status") != OK:
        report = base_report(
            cfg,
            status=PREDICTIONS_FAILED,
            reason=str(prediction_report.get("reason") or "fresh_predictions_failed"),
            market_report=market_report,
            prediction_report=prediction_report,
        )
        if write_report:
            write_json(cfg.report_path, report)
        return report

    phase13_report = phase13_fn(
        config_path=cfg.signal_config_path,
        force_from_predictions=True,
        validity_minutes=cfg.phase13_validity_minutes,
    )
    if phase13_report.get("status") not in {OK, "empty"}:
        report = base_report(
            cfg,
            status=PHASE13_FAILED,
            reason=str(phase13_report.get("reason") or "phase13_signal_generation_failed"),
            market_report=market_report,
            prediction_report=prediction_report,
            phase13_report=phase13_report,
        )
        if write_report:
            write_json(cfg.report_path, report)
        return report

    freshness = freshness_fn(
        cfg.predictions_output_path,
        max_allowed_age_minutes=cfg.max_prediction_age_minutes,
        max_input_data_age_minutes=cfg.max_input_data_age_minutes,
    )
    signals_after = signal_inspect_fn(cfg.pinned_signals_path)
    stale_after_refresh = bool(freshness.get("stale")) or freshness.get("freshness_status") != "fresh"
    input_status = freshness.get("input_data_status")
    if stale_after_refresh or input_status != "input_data_fresh":
        report = base_report(
            cfg,
            status=STALE_AFTER_REFRESH,
            reason=str(freshness.get("reason") or input_status or "stale_after_refresh"),
            market_report=market_report,
            prediction_report=prediction_report,
            phase13_report=phase13_report,
            freshness=freshness,
            signals_after=signals_after,
        )
        if write_report:
            write_json(cfg.report_path, report)
        return report

    report = base_report(
        cfg,
        status=OK,
        reason=None,
        market_report=market_report,
        prediction_report=prediction_report,
        phase13_report=phase13_report,
        freshness=freshness,
        signals_after=signals_after,
    )
    if write_report:
        write_json(cfg.report_path, report)
    return report


def run_supervisor_loop(
    config: PaperRefreshSupervisorConfig,
    *,
    interval_seconds: int,
    once: bool = True,
) -> dict[str, Any]:
    last_report = run_paper_refresh_supervisor(config)
    if once:
        return last_report
    while True:
        time.sleep(max(1, int(interval_seconds)))
        last_report = run_paper_refresh_supervisor(config)


def base_report(
    cfg: PaperRefreshSupervisorConfig,
    *,
    status: str,
    reason: str | None,
    market_report: dict[str, Any] | None = None,
    prediction_report: dict[str, Any] | None = None,
    phase13_report: dict[str, Any] | None = None,
    freshness: dict[str, Any] | None = None,
    signals_after: dict[str, Any] | None = None,
) -> dict[str, Any]:
    market_report = market_report or {}
    prediction_report = prediction_report or {}
    phase13_report = phase13_report or {}
    freshness = freshness or prediction_report.get("prediction_freshness") or {}
    return {
        "status": status,
        "reason": reason,
        "report_path": str(cfg.report_path),
        "market_features_status": market_report.get("status"),
        "predictions_status": prediction_report.get("status"),
        "phase13_status": phase13_report.get("status"),
        "input_data_status": freshness.get("input_data_status") or prediction_report.get("input_data_status"),
        "prediction_freshness": freshness,
        "signals_after": signals_after or {"path": str(cfg.pinned_signals_path), "exists": Path(cfg.pinned_signals_path).exists()},
        "next_recommended_run_seconds": int(cfg.next_recommended_run_seconds),
        "market_features_report": market_report,
        "predictions_report": prediction_report,
        "phase13_report": phase13_report,
        "paths": {
            "market_features": str(cfg.market_features_output_path),
            "predictions": str(cfg.predictions_output_path),
            "pinned_signals": str(cfg.pinned_signals_path),
        },
        "runtime_mode": "paper",
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "generated_at": utc_now(),
    }


def unsafe_runtime_flags() -> list[str]:
    unsafe = []
    for name in ("LIVE_ENABLED", "ORDER_SUBMISSION_ENABLED", "REAL_ORDER_SUBMISSION_ENABLED"):
        if str(os.getenv(name, "")).strip().lower() in TRUE_VALUES:
            unsafe.append(f"{name}=true")
    return unsafe


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(target)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
