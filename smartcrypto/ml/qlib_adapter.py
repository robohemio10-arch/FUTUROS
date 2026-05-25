from __future__ import annotations

from pathlib import Path

import pandas as pd


class QlibAdapter:
    def __init__(self, dataset_root: str | Path) -> None:
        self.dataset_root = Path(dataset_root)

    def export_binance_features(self, features: pd.DataFrame) -> Path:
        destination = self.dataset_root / "binance_features.parquet"
        destination.parent.mkdir(parents=True, exist_ok=True)
        features.to_parquet(destination, index=False)
        return destination

    def read_predictions(self, path: str | Path) -> pd.DataFrame:
        prediction_path = Path(path)
        if not prediction_path.exists():
            return pd.DataFrame(columns=["ts", "pair", "score", "confidence", "model_version"])

        return pd.read_parquet(prediction_path)
