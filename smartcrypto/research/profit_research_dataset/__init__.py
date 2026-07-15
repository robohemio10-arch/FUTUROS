"""Deterministic, research-only paper trade and candle dataset."""

from .contracts import DatasetBuildPaths, DatasetBuildResult, resolve_build_paths
from .dataset_builder import build_profit_research_dataset

__all__ = [
    "DatasetBuildPaths",
    "DatasetBuildResult",
    "build_profit_research_dataset",
    "resolve_build_paths",
]
