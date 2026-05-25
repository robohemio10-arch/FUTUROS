from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import joblib
import pandas as pd

from smartcrypto.execution.freqtrade_contract import freqtrade_pair


def export_predictions(
    market_features_path: str | Path,
    model_path: str | Path,
    output_path: str | Path,
) -> pd.DataFrame:
    features = pd.read_parquet(market_features_path)
    features = features[features["tf"].eq("5m")].sort_values(["symbol", "ts"])
    latest = features.groupby("symbol", as_index=False).tail(1).copy()

    payload = joblib.load(model_path)
    model = payload["model"]
    feature_columns = payload["feature_columns"]
    model_version = payload.get("model_version", "unknown")

    for column in feature_columns:
        if column.startswith("entry_"):
            source = column.replace("entry_", "")
            latest[column] = latest.get(source)

    scores = model.predict_proba(latest[feature_columns].fillna(0))[:, 1]
    now = datetime.now(timezone.utc)
    valid_until = now + timedelta(minutes=5)

    predictions = pd.DataFrame(
        {
            "ts": latest["ts"].values,
            "pair": latest["symbol"].map(freqtrade_pair).values,
            "symbol": latest["symbol"].values,
            "score": scores,
            "confidence": abs(scores - 0.5) * 2,
            "timeframe": "5m",
            "valid_until": valid_until.isoformat().replace("+00:00", "Z"),
            "model_version": model_version,
        }
    )

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination.with_suffix(".tmp.parquet")
    predictions.to_parquet(temporary_path, index=False)
    temporary_path.replace(destination)
    return predictions
