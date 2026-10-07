from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

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


def _capital_population(frame: pd.DataFrame) -> pd.DataFrame:
    capital = np.arange(100.0, 100.0 + len(frame))
    duration_hours = (
        frame["close_time_utc"] - frame["open_time_utc"]
    ).dt.total_seconds().to_numpy() / 3600.0
    return pd.DataFrame(
        {
            "trade_sequence": frame["trade_sequence"],
            "symbol": frame["symbol"],
            "side": frame["side"],
            "open_time_utc": frame["open_time_utc"],
            "close_time_utc": frame["close_time_utc"],
            "economic_net_pnl": frame["label_economic_net_pnl"],
            "capital_proxy_usdt": capital,
            "capital_hours": capital * duration_hours,
        }
    )


def test_ablation_runs_on_reconstructed_walkforward_without_operational_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_bundle(tmp_path)
    monkeypatch.setattr(
        ablation,
        "_load_official_capital_population",
        lambda manifest, *, project_root: (
            _capital_population(_dataset()),
            project_root / "master.xlsx",
        ),
    )

    report = ablation.build_market_intelligence_pnl_ablation_v1(
        project_root=tmp_path,
        model_factory=_factory,
    )

    assert report["status"] == "ok"
    assert report["decision"] == "ABLATION_READY_RESEARCH_ONLY"
    assert report["feature_block_count"] == 4
    assert set(report["ablations"]) == set(ablation.FEATURE_BLOCKS)
    assert report["full_model"]["fold_count"] == 3
    full = report["full_model"]
    assert full["control"]["capital_proxy_total_usdt"] > full["treatment"]["capital_proxy_total_usdt"]
    assert full["control"]["roi_on_deployed_capital_proxy"] == pytest.approx(
        full["control"]["net_pnl"] / full["control"]["capital_proxy_total_usdt"]
    )
    assert full["treatment"]["net_pnl_per_capital_hour"] == pytest.approx(
        full["treatment"]["net_pnl"] / full["treatment"]["capital_hours_total"]
    )
    abstention = full["abstention"]
    assert abstention["abstained_trade_count"] == (
        full["control"]["trade_count"] - full["treatment"]["trade_count"]
    )
    assert abstention["abstained_net_pnl_usdt"] == pytest.approx(
        full["control"]["net_pnl"] - full["treatment"]["net_pnl"]
    )
    assert abstention["net_pnl_foregone_per_capital_hour_released"] == pytest.approx(
        abstention["abstained_net_pnl_usdt"] / abstention["capital_hours_released"]
    )
    assert sum(fold["abstention"]["abstained_trade_count"] for fold in full["folds"]) == (
        abstention["abstained_trade_count"]
    )
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


def test_official_capital_link_rejects_identity_mismatch() -> None:
    dataset = _dataset(4)
    population = _capital_population(dataset)
    population.loc[0, "symbol"] = "ETHUSDT"
    with pytest.raises(ablation.AblationError, match="official_master_identity_mismatch:symbol"):
        ablation._attach_capital_proxy(dataset, population)


def test_official_capital_requires_frozen_master_lineage() -> None:
    with pytest.raises(ablation.AblationError, match="official_master_sha256_manifest_mismatch"):
        ablation._load_official_capital_population(
            {"official_master_sha256": "wrong"}, project_root=Path(".")
        )


@pytest.mark.parametrize("relative", [True, False])
def test_official_master_source_uses_project_root_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: bool,
) -> None:
    project_root = tmp_path / "project"
    master_path = project_root / "data" / "trades" / "trades_master.xlsx"
    master_path.parent.mkdir(parents=True)
    master_path.write_bytes(b"frozen-test-master")
    master_hash = ablation._sha256(master_path)
    source_path = "data/trades/trades_master.xlsx" if relative else str(master_path)
    manifest = {
        "official_master_sha256": master_hash,
        "source_paths": [source_path],
        "source_hashes": {source_path: master_hash},
    }
    monkeypatch.setattr(ablation, "OFFICIAL_MASTER_SHA256", master_hash)

    def load_master(path: Path) -> SimpleNamespace:
        assert path == master_path
        return SimpleNamespace(
            audit=SimpleNamespace(master_sha256=master_hash),
            frame=pd.DataFrame(),
        )

    monkeypatch.setattr(ablation, "load_official_trades_master", load_master)
    monkeypatch.setattr(
        ablation,
        "_prepare_population",
        lambda frame, *, enforce_canonical_population: (pd.DataFrame(), {}),
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    _, resolved_path = ablation._load_official_capital_population(
        manifest, project_root=project_root
    )
    assert resolved_path == master_path


def test_missing_relative_master_source_blocks_from_other_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    source_path = "data/trades/missing_master.xlsx"
    manifest = {
        "official_master_sha256": ablation.OFFICIAL_MASTER_SHA256,
        "source_paths": [source_path],
        "source_hashes": {source_path: ablation.OFFICIAL_MASTER_SHA256},
    }
    with pytest.raises(ablation.AblationError, match="official_master_source_unavailable_or_drifted"):
        ablation._load_official_capital_population(
            manifest, project_root=project_root
        )


def test_official_capital_link_rejects_missing_trade_sequence() -> None:
    dataset = _dataset(4)
    population = _capital_population(dataset).iloc[1:]
    with pytest.raises(ablation.AblationError, match="dataset_trade_sequence_absent_from_master"):
        ablation._attach_capital_proxy(dataset, population)


def test_abstention_efficiency_keeps_signed_control_outcome() -> None:
    rows = pd.DataFrame(
        {
            ablation.TARGET_COLUMN: [-5.0, 2.0],
            "capital_hours": [100.0, 100.0],
        }
    )
    metrics = ablation._abstention_metrics(rows)
    assert metrics["abstained_trade_count"] == 2
    assert metrics["abstained_net_pnl_usdt"] == -3.0
    assert metrics["capital_hours_released"] == 200.0
    assert metrics["net_pnl_foregone_per_capital_hour_released"] == pytest.approx(-0.015)
    assert metrics["outcome_basis"] == "closed_control_pnl_signed"
