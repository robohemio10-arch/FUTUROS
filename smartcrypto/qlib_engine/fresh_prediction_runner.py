from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.qlib_engine.common import load_config, write_json
from smartcrypto.qlib_engine.prediction_freshness import inspect_qlib_prediction_freshness
from smartcrypto.qlib_engine.predictor import export_latest_qlib_predictions


def run_qlib_fresh_predictions(
    *,
    market_features_path: str | Path = "data/features/market_features_60d.parquet",
    model_path: str | Path = "data/models/qlib_market_model.joblib",
    output_path: str | Path = "data/predictions/latest_qlib_predictions.parquet",
    report_path: str | Path = "data/reports/qlib_fresh_prediction_runner_report.json",
    config_path: str | Path = "config/qlib_model.yml",
    max_allowed_age_minutes: int | float = 90,
) -> dict[str, Any]:
    """Generate fresh Qlib predictions for the paper/shadow signal pipeline."""
    started_at = datetime.now(timezone.utc)
    config = load_config(config_path)
    export_report = export_latest_qlib_predictions(
        market_features_path=market_features_path,
        model_path=model_path,
        output_path=output_path,
        report_path=report_path,
        config=config,
    )

    freshness = inspect_qlib_prediction_freshness(
        output_path,
        max_allowed_age_minutes=max_allowed_age_minutes,
        now=started_at,
    )
    export_ok = export_report.get("status") == "ok"
    fresh = freshness.get("freshness_status") == "fresh"
    status = "ok" if export_ok and fresh else "blocked"
    reason = export_report.get("reason")
    if status == "blocked" and not reason:
        reason = freshness.get("reason") or "qlib_fresh_prediction_generation_failed"

    report = {
        "status": status,
        "reason": reason,
        "rows": int(export_report.get("rows") or 0),
        "pairs": export_report.get("pairs", []),
        "symbols": export_report.get("symbols", []),
        "generated_at": export_report.get("generated_at") or export_report.get("created_at"),
        "created_at": started_at.isoformat(),
        "market_features_path": str(market_features_path),
        "model_path": str(model_path),
        "output_path": str(output_path),
        "report_path": str(report_path),
        "model_version": config.model_version,
        "timeframe": config.timeframe,
        "prediction_freshness": freshness,
        "runtime_mode": "paper",
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
    }
    write_json(report_path, report)
    return report
