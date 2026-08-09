"""Read-only orchestration for paper profit maximization."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.data.trader_master_fingerprint_v2.master_adapter import read_trader_master_readonly
from smartcrypto.research.profit_research.paper_analysis import (
    DEFAULT_MASTER,
    build_exit_candidates,
    load_market_candles,
)
from smartcrypto.research.profit_research_dataset import (
    build_profit_research_dataset,
    resolve_build_paths,
)

from .contracts import SAFETY_FLAGS, SCHEMA_VERSION, ProfitMaximizationResult
from .metrics import prepare_profit_dataset
from .optimizer import build_profit_maximization

DEFAULT_SCORE_SOURCES = (
    Path("data/reports/financial_label_target_store_v1.json"),
    Path("data/reports/ai_shadow_quality_veto_trainer_v1.json"),
    Path("data/reports/qlib_shadow_ensemble_threshold_calibration_v1.json"),
    Path("data/features/incremental_training_microbatch.parquet"),
)


def run_profit_maximization(
    project_root: str | Path,
    *,
    source_profile: str | Path | None = None,
    paper_db: str | Path | None = None,
    paper_snapshot_db: str | Path | None = None,
    candle_root: str | Path | None = None,
    trader_master: str | Path | None = None,
    score_sources: Sequence[str | Path] = (),
    timeframe: str = "5m",
    allow_runtime_read: bool = False,
) -> ProfitMaximizationResult:
    root = Path(project_root).resolve()
    paths = resolve_build_paths(
        root,
        source_profile=source_profile,
        paper_db=paper_db,
        paper_snapshot_db=paper_snapshot_db,
        candle_root=candle_root,
        output_root=root / "data",
    )
    dataset_result = build_profit_research_dataset(
        paths,
        timeframe=timeframe,
        allow_runtime_read=allow_runtime_read,
        write_report=False,
        write_dataset=False,
    )
    if dataset_result.report.get("status") not in {"ok", "warning"}:
        return ProfitMaximizationResult(
            dataset=dataset_result.dataset,
            report={
                "schema_version": SCHEMA_VERSION,
                "status": "blocked",
                "reason": "profit_research_dataset_unavailable",
                "dataset_report": dataset_result.report,
                "runtime_read_requested": allow_runtime_read,
                "write_performed": False,
                **SAFETY_FLAGS,
            },
        )

    master_path = _resolve(root, trader_master, DEFAULT_MASTER)
    master = read_trader_master_readonly(
        project_root=root,
        trader_master_path=master_path,
    )
    resolved_score_sources = (
        tuple(_resolve(root, item, Path(item)) for item in score_sources)
        if score_sources
        else tuple((root / item).resolve() for item in DEFAULT_SCORE_SOURCES)
    )
    score_rows, score_inventory = load_score_sources(resolved_score_sources)
    candles, candle_inventory = load_market_candles(paths.candle_root)
    prepared_for_exit, _ = prepare_profit_dataset(dataset_result.dataset)
    eligible_for_exit = prepared_for_exit.loc[
        prepared_for_exit["profit_optimization_eligible"]
    ].copy()
    exit_candidates = (
        build_exit_candidates(eligible_for_exit, candles) if not candles.empty else []
    )
    result = build_profit_maximization(
        dataset_result.dataset,
        trader_master_rows=(master.source_rows if master.report.get("status") == "ok" else ()),
        score_rows=score_rows,
        exit_candidates=exit_candidates,
    )
    report = {
        **result.report,
        "dataset_report": dataset_result.report,
        "trader_master_read": master.report,
        "trader_master_analysis_available": master.report.get("status") == "ok",
        "score_source_inventory": score_inventory,
        "candle_inventory": candle_inventory,
        "runtime_read_requested": allow_runtime_read,
        "write_performed": False,
    }
    return ProfitMaximizationResult(dataset=result.dataset, report=report)


def load_score_sources(paths: Sequence[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    for path in paths:
        item: dict[str, Any] = {
            "path": str(path),
            "exists": path.is_file() and not path.is_symlink(),
            "row_count": 0,
            "status": "missing",
        }
        if not item["exists"]:
            inventory.append(item)
            continue
        try:
            loaded = _read_score_file(path)
        except (OSError, ValueError, json.JSONDecodeError, ImportError) as exc:
            item.update(status="unreadable", error=f"{type(exc).__name__}:{exc}")
            inventory.append(item)
            continue
        rows.extend(loaded)
        item.update(status="ok", row_count=len(loaded))
        inventory.append(item)
    return rows, inventory


def _read_score_file(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return [dict(row) for row in pd.read_parquet(path).to_dict(orient="records")]
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return _extract_mapping_rows(payload)
    raise ValueError(f"unsupported_score_source:{suffix}")


def _extract_mapping_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    result: list[dict[str, Any]] = []
    for key in (
        "target_records",
        "decision_sample",
        "rows",
        "records",
        "dataset",
        "decisions",
        "calibration_rows",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            result.extend(dict(item) for item in value if isinstance(item, Mapping))
    for value in payload.values():
        if isinstance(value, Mapping):
            result.extend(_extract_mapping_rows(value))
    return result


def _resolve(root: Path, supplied: str | Path | None, default: Path) -> Path:
    candidate = Path(supplied) if supplied is not None else default
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
