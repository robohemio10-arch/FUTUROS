from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from smartcrypto.ml.market_dataset import load_market_model_config, prepare_feature_matrix


@dataclass(frozen=True)
class MarketPredictionReport:
    status: str
    reason: str | None
    rows: int
    output_path: str | None
    model_path: str
    created_at: str


def export_latest_market_predictions(
    config_path: str | Path = "config/market_model.yml",
    report_path: str | Path = "data/reports/phase6_market_prediction_report.json",
) -> MarketPredictionReport:
    config = load_market_model_config(config_path)
    model_config = config["market_model"]
    model_path = Path(model_config["model_output_path"])
    source_path = Path(model_config["source_path"])
    output_path = Path(model_config["predictions_output_path"])
    timeframe = str(model_config.get("timeframe", "5m"))

    if not model_path.exists():
        report = MarketPredictionReport(
            status="blocked",
            reason="model_file_not_found",
            rows=0,
            output_path=None,
            model_path=str(model_path),
            created_at=_utc_now(),
        )
        _write_json(report_path, asdict(report))
        return report

    if not source_path.exists():
        raise FileNotFoundError(source_path)

    bundle = joblib.load(model_path)
    frame = pd.read_parquet(source_path)
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True, errors="coerce")
    frame = frame.loc[frame["tf"].astype(str).eq(timeframe)].dropna(subset=["ts"]).copy()
    if frame.empty:
        report = MarketPredictionReport(
            status="blocked",
            reason=f"no_rows_for_timeframe_{timeframe}",
            rows=0,
            output_path=None,
            model_path=str(model_path),
            created_at=_utc_now(),
        )
        _write_json(report_path, asdict(report))
        return report

    latest = frame.sort_values("ts").groupby(["symbol", "pair", "tf"], as_index=False).tail(1).copy()
    x_latest, _ = prepare_feature_matrix(latest, bundle["config"], bundle["feature_columns"])
    model = bundle["model"]
    prob_up = model.predict_proba(x_latest)[:, 1]
    latest["prob_up"] = prob_up
    latest["score"] = (latest["prob_up"] - 0.5) * 2.0
    latest["predicted_direction"] = latest["prob_up"].ge(0.5).astype(int)
    latest["model_version"] = model_config.get("version", "market_direction_rf_v1")
    latest["generated_at"] = pd.Timestamp.now(tz="UTC")
    latest["date"] = latest["ts"]

    output = latest[
        [
            "date",
            "generated_at",
            "symbol",
            "pair",
            "tf",
            "prob_up",
            "score",
            "predicted_direction",
            "model_version",
        ]
    ].copy()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_parquet(output_path, index=False)

    report = MarketPredictionReport(
        status="ok",
        reason=None,
        rows=int(len(output)),
        output_path=str(output_path),
        model_path=str(model_path),
        created_at=_utc_now(),
    )
    _write_json(report_path, asdict(report))
    return report


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    import json

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
