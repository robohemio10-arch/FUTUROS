from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from smartcrypto.learning.feature_contracts.dataset_manifest import frame_hash
from smartcrypto.learning.walkforward.purged_split_engine import (
    build_walkforward_splits,
    public_split,
)
from smartcrypto.research.aibot_parity import market_intelligence_pnl_ablation as ablation


class _MeanProjection:
    def fit(self, x: np.ndarray, y: np.ndarray) -> "_MeanProjection":
        self.width = x.shape[1]
        self.mean = float(np.mean(y))
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        # Deterministic score with variation, independent of outcomes at prediction time.
        return self.mean + x[:, 0] * 0.01 + np.arange(len(x), dtype=float) * 1e-6


def _factory() -> _MeanProjection:
    return _MeanProjection()


def _dataset(rows: int = 180) -> pd.DataFrame:
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    data: dict[str, object] = {
        "trade_sequence": np.arange(1, rows + 1),
        "order_id": [f"order-{index}" for index in range(rows)],
        "order_id_raw": [f"order-{index}" for index in range(rows)],
        "symbol": ["BTCUSDT" if index % 2 == 0 else "ETHUSDT" for index in range(rows)],
        "side": ["long" if index % 2 == 0 else "short" for index in range(rows)],
        "open_time_utc": [start + pd.Timedelta(hours=index * 3) for index in range(rows)],
        "close_time_utc": [
            start + pd.Timedelta(hours=index * 3, minutes=10 + (index % 8))
            for index in range(rows)
        ],
        "feature_cutoff_utc": [
            start + pd.Timedelta(hours=index * 3)
            for index in range(rows)
        ],
        "market_source": ["fixture"] * rows,
        "label_economic_net_pnl": [
            2.0 if index % 4 else -1.5 for index in range(rows)
        ],
        "label_is_profitable": [0 if index % 4 == 0 else 1 for index in range(rows)],
    }
    for position, feature in enumerate(ablation.EXPECTED_FEATURES):
        data[feature] = (
            np.sin(np.arange(rows, dtype=float) / (position + 3.0))
            + position * 0.001
        )
    return pd.DataFrame(data)


def _write_bundle(root: Path, *, drift_split: bool = False) -> None:
    frame = _dataset()
    dataset_path = root / ablation.DATASET_FILE
    frame.to_parquet(dataset_path, index=False)

    contract = {
        "validation_status": "ok",
        "feature_columns": list(ablation.EXPECTED_FEATURES),
        "contract_hash": "c" * 64,
    }
    manifest = {
        "validation_status": "ok",
        "row_count": len(frame),
        "feature_order": list(ablation.EXPECTED_FEATURES),
        "feature_contract_hash": contract["contract_hash"],
        "dataset_hash": frame_hash(frame),
        "materialized_dataset_sha256": ablation._sha256(dataset_path),
    }
    embargo_seconds = 86_400
    splits = build_walkforward_splits(frame, embargo_seconds=embargo_seconds)
    public = [public_split(split) for split in splits]
    if drift_split:
        public[0] = dict(public[0])
        public[0]["test_row_count"] += 1
    split_manifest = {
        "validation_status": "ok",
        "leakage_status": "ok",
        "feature_contract_hash": contract["contract_hash"],
        "dataset_hash": manifest["dataset_hash"],
        "split_manifest_hash": "d" * 64,
        "embargo_seconds": embargo_seconds,
        "splits": public,
    }

    (root / ablation.FEATURE_CONTRACT_FILE).write_text(
        json.dumps(contract), encoding="utf-8"
    )
    (root / ablation.DATASET_MANIFEST_FILE).write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (root / ablation.SPLIT_MANIFEST_FILE).write_text(
        json.dumps(split_manifest), encoding="utf-8"
    )


def test_feature_blocks_partition_exact_frozen_features() -> None:
    flattened = [
        feature
        for values in ablation.FEATURE_BLOCKS.values()
        for feature in values
    ]
    assert len(flattened) == 21
    assert len(flattened) == len(set(flattened))
    assert set(flattened) == set(ablation.EXPECTED_FEATURES)
    assert not any("duration" in feature for feature in flattened)
    assert not any(feature.startswith("label_") for feature in flattened)


def test_ablation_runs_on_reconstructed_walkforward_without_operational_authority(
    tmp_path: Path,
) -> None:
    _write_bundle(tmp_path)

    report = ablation.build_market_intelligence_pnl_ablation_v1(
        project_root=tmp_path,
        model_factory=_factory,
    )

    assert report["status"] == "ok"
    assert report["decision"] == "ABLATION_READY_RESEARCH_ONLY"
    assert report["feature_block_count"] == 4
    assert set(report["ablations"]) == set(ablation.FEATURE_BLOCKS)
    assert report["full_model"]["fold_count"] == 3
    assert report["priority_segment"]["future_duration_used_as_feature"] is False
    assert report["operational_authority"] is False
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["model_promotion_performed"] is False
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["writes_data"] is False


def test_split_manifest_drift_blocks_fail_closed(tmp_path: Path) -> None:
    _write_bundle(tmp_path, drift_split=True)

    report = ablation.build_market_intelligence_pnl_ablation_v1(
        project_root=tmp_path,
        model_factory=_factory,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "split_manifest_reconstruction_mismatch"
    assert report["training_performed"] is False


def test_duplicate_artifact_discovery_is_ambiguous_and_blocks(tmp_path: Path) -> None:
    first = tmp_path / "data" / "a"
    second = tmp_path / "data" / "b"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / ablation.DATASET_FILE).write_bytes(b"x")
    (second / ablation.DATASET_FILE).write_bytes(b"x")

    report = ablation.build_market_intelligence_pnl_ablation_v1(
        project_root=tmp_path,
        model_factory=_factory,
    )

    assert report["status"] == "blocked"
    assert report["reason"].startswith(
        f"artifact_ambiguous:{ablation.DATASET_FILE}:"
    )
