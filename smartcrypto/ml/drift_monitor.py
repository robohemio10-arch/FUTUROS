from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd


OK = "OK"
WARNING = "WARNING"
BLOCKED = "BLOCKED"


class DriftMonitorError(ValueError):
    pass


@dataclass(frozen=True)
class FeatureDrift:
    feature: str
    psi: float
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DriftReport:
    status: str
    feature_results: list[FeatureDrift]
    warning_threshold: float
    blocked_threshold: float
    checked_at: str = field(default_factory=lambda: utc_timestamp())

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "feature_results": [item.to_dict() for item in self.feature_results],
            "warning_threshold": self.warning_threshold,
            "blocked_threshold": self.blocked_threshold,
            "checked_at": self.checked_at,
            "safety": {
                "order_submission": False,
                "risk_increase": False,
                "bot_block": False,
            },
        }


class DriftMonitor:
    def __init__(
        self,
        *,
        warning_threshold: float = 0.10,
        blocked_threshold: float = 0.25,
        bins: int = 10,
    ) -> None:
        if warning_threshold < 0 or blocked_threshold <= warning_threshold:
            raise DriftMonitorError("invalid_drift_thresholds")
        self.warning_threshold = float(warning_threshold)
        self.blocked_threshold = float(blocked_threshold)
        self.bins = int(bins)

    def compare(
        self,
        baseline: pd.DataFrame,
        current: pd.DataFrame,
        *,
        features: list[str] | tuple[str, ...],
    ) -> DriftReport:
        if not isinstance(baseline, pd.DataFrame) or not isinstance(current, pd.DataFrame):
            raise DriftMonitorError("drift_inputs_must_be_dataframes")
        feature_results: list[FeatureDrift] = []
        for feature in features:
            if feature not in baseline.columns or feature not in current.columns:
                raise DriftMonitorError(f"drift_feature_missing:{feature}")
            psi_value = population_stability_index(baseline[feature], current[feature], self.bins)
            if psi_value >= self.blocked_threshold:
                status = BLOCKED
            elif psi_value >= self.warning_threshold:
                status = WARNING
            else:
                status = OK
            feature_results.append(FeatureDrift(feature=feature, psi=psi_value, status=status))
        overall = OK
        if any(item.status == BLOCKED for item in feature_results):
            overall = BLOCKED
        elif any(item.status == WARNING for item in feature_results):
            overall = WARNING
        return DriftReport(
            status=overall,
            feature_results=feature_results,
            warning_threshold=self.warning_threshold,
            blocked_threshold=self.blocked_threshold,
        )


def population_stability_index(
    baseline: pd.Series,
    current: pd.Series,
    bins: int = 10,
) -> float:
    base = pd.to_numeric(baseline, errors="coerce").dropna().to_numpy(dtype=float)
    curr = pd.to_numeric(current, errors="coerce").dropna().to_numpy(dtype=float)
    if base.size == 0 or curr.size == 0:
        raise DriftMonitorError("drift_feature_has_no_numeric_values")
    quantiles = np.linspace(0, 1, max(2, bins + 1))
    edges = np.unique(np.quantile(base, quantiles))
    if edges.size < 2:
        edges = np.array([base.min() - 0.5, base.max() + 0.5], dtype=float)
    edges[0] = -np.inf
    edges[-1] = np.inf
    base_counts, _ = np.histogram(base, bins=edges)
    curr_counts, _ = np.histogram(curr, bins=edges)
    base_pct = np.clip(base_counts / max(base_counts.sum(), 1), 1e-6, None)
    curr_pct = np.clip(curr_counts / max(curr_counts.sum(), 1), 1e-6, None)
    return float(np.sum((curr_pct - base_pct) * np.log(curr_pct / base_pct)))


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
