from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


@dataclass(frozen=True)
class GuardrailDecision:
    status: str
    trainable: bool
    reason: str
    rows: int
    target_column: str
    target_classes: int
    min_trades_for_training: int
    min_trades_for_walk_forward: int
    missing_columns: list[str]
    class_distribution: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_phase4_config(path: str | Path = "config/model.yml") -> dict[str, Any]:
    config_path = Path(path)

    if not config_path.exists():
        return {
            "target_column": "target_win",
            "min_trades_for_training": 50,
            "min_trades_for_walk_forward": 100,
            "min_classes": 2,
            "test_size_fraction": 0.25,
            "random_state": 42,
            "model_name": "baseline_random_forest",
            "allow_model_export": False,
            "production_enabled": False,
            "reports_dir": "data/reports",
            "models_dir": "data/models",
            "training_dataset_path": "data/features/training_dataset.parquet",
        }

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    phase4 = payload.get("phase4", payload)

    defaults = {
        "target_column": "target_win",
        "min_trades_for_training": 50,
        "min_trades_for_walk_forward": 100,
        "min_classes": 2,
        "test_size_fraction": 0.25,
        "random_state": 42,
        "model_name": "baseline_random_forest",
        "allow_model_export": False,
        "production_enabled": False,
        "reports_dir": "data/reports",
        "models_dir": "data/models",
        "training_dataset_path": "data/features/training_dataset.parquet",
    }

    return {**defaults, **phase4}


def load_training_dataset(path: str | Path) -> pd.DataFrame:
    dataset_path = Path(path)

    if not dataset_path.exists():
        raise FileNotFoundError(dataset_path)

    if dataset_path.suffix.lower() == ".parquet":
        return pd.read_parquet(dataset_path)

    if dataset_path.suffix.lower() in {".csv", ".txt"}:
        return pd.read_csv(dataset_path)

    raise ValueError(f"Formato não suportado: {dataset_path}")


def evaluate_training_guardrails(
    frame: pd.DataFrame,
    target_column: str,
    min_trades_for_training: int,
    min_trades_for_walk_forward: int,
    min_classes: int = 2,
) -> GuardrailDecision:
    missing_columns = [column for column in [target_column] if column not in frame.columns]
    rows = int(len(frame))

    if missing_columns:
        return GuardrailDecision(
            status="blocked",
            trainable=False,
            reason="missing_target_column",
            rows=rows,
            target_column=target_column,
            target_classes=0,
            min_trades_for_training=min_trades_for_training,
            min_trades_for_walk_forward=min_trades_for_walk_forward,
            missing_columns=missing_columns,
            class_distribution={},
        )

    target = frame[target_column].dropna()
    class_distribution = {
        str(key): int(value)
        for key, value in target.value_counts(dropna=False).sort_index().to_dict().items()
    }
    target_classes = int(target.nunique(dropna=True))

    if rows < min_trades_for_training:
        return GuardrailDecision(
            status="blocked",
            trainable=False,
            reason="insufficient_trades_for_training",
            rows=rows,
            target_column=target_column,
            target_classes=target_classes,
            min_trades_for_training=min_trades_for_training,
            min_trades_for_walk_forward=min_trades_for_walk_forward,
            missing_columns=[],
            class_distribution=class_distribution,
        )

    if target_classes < min_classes:
        return GuardrailDecision(
            status="blocked",
            trainable=False,
            reason="insufficient_target_classes",
            rows=rows,
            target_column=target_column,
            target_classes=target_classes,
            min_trades_for_training=min_trades_for_training,
            min_trades_for_walk_forward=min_trades_for_walk_forward,
            missing_columns=[],
            class_distribution=class_distribution,
        )

    return GuardrailDecision(
        status="ready",
        trainable=True,
        reason="ok",
        rows=rows,
        target_column=target_column,
        target_classes=target_classes,
        min_trades_for_training=min_trades_for_training,
        min_trades_for_walk_forward=min_trades_for_walk_forward,
        missing_columns=[],
        class_distribution=class_distribution,
    )


def require_report_dir(path: str | Path) -> Path:
    report_dir = Path(path)
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir
