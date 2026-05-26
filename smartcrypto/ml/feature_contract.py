from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd


class FeatureContractError(ValueError):
    pass


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    dtype: str = "numeric"
    min_value: float | None = None
    max_value: float | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | str) -> "FeatureSpec":
        if isinstance(payload, str):
            return cls(name=payload)
        return cls(
            name=str(payload["name"]),
            dtype=str(payload.get("dtype", "numeric")),
            min_value=optional_float(payload.get("min_value")),
            max_value=optional_float(payload.get("max_value")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureContractResult:
    valid: bool
    contract_version: str
    errors: list[str] = field(default_factory=list)
    checked_at: str = field(default_factory=lambda: utc_timestamp())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureContract:
    contract_version: str
    features: tuple[FeatureSpec, ...]
    strict_order: bool = True
    allow_extra_columns: bool = True
    allow_nan: bool = False
    allow_infinite: bool = False

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FeatureContract":
        if not isinstance(payload, dict):
            raise FeatureContractError("feature_contract_root_must_be_mapping")
        features = payload.get("features")
        if not isinstance(features, list) or not features:
            raise FeatureContractError("feature_contract_features_required")
        return cls(
            contract_version=str(payload.get("contract_version") or "").strip(),
            features=tuple(FeatureSpec.from_dict(item) for item in features),
            strict_order=bool(payload.get("strict_order", True)),
            allow_extra_columns=bool(payload.get("allow_extra_columns", True)),
            allow_nan=bool(payload.get("allow_nan", False)),
            allow_infinite=bool(payload.get("allow_infinite", False)),
        ).validate_contract()

    @property
    def feature_names(self) -> tuple[str, ...]:
        return tuple(feature.name for feature in self.features)

    def validate_contract(self) -> "FeatureContract":
        if not self.contract_version:
            raise FeatureContractError("feature_contract_version_required")
        names = self.feature_names
        if len(names) != len(set(names)):
            raise FeatureContractError("feature_contract_duplicate_features")
        return self

    def validate(self, frame: pd.DataFrame) -> FeatureContractResult:
        if not isinstance(frame, pd.DataFrame):
            raise FeatureContractError("feature_contract_input_must_be_dataframe")
        errors: list[str] = []
        columns = tuple(str(column) for column in frame.columns)
        expected = self.feature_names

        missing = [name for name in expected if name not in columns]
        if missing:
            errors.append(f"missing_features:{missing}")

        extra = [name for name in columns if name not in expected]
        if extra and not self.allow_extra_columns:
            errors.append(f"unexpected_features:{extra}")

        if self.strict_order and not missing:
            actual_order = tuple(name for name in columns if name in expected)
            if actual_order[: len(expected)] != expected:
                errors.append(
                    f"feature_order_mismatch:expected={list(expected)}:"
                    f"actual={list(actual_order)}"
                )

        for spec in self.features:
            if spec.name not in frame.columns:
                continue
            series = frame[spec.name]
            if isinstance(series, pd.DataFrame):
                errors.append(f"feature_not_1d:{spec.name}")
                continue
            if spec.dtype == "numeric" and not pd.api.types.is_numeric_dtype(series):
                errors.append(f"feature_not_numeric:{spec.name}")
                continue
            if not self.allow_nan and bool(series.isna().any()):
                errors.append(f"feature_contains_nan:{spec.name}")
            if pd.api.types.is_numeric_dtype(series):
                values = series.to_numpy(dtype=float, copy=False)
                infinite_mask = np.isinf(values)
                if not self.allow_infinite and bool(infinite_mask.any()):
                    errors.append(f"feature_contains_infinite:{spec.name}")
                finite_mask = np.isfinite(values)
                finite_values = values[finite_mask]
                if spec.min_value is not None and finite_values.size:
                    if float(np.nanmin(finite_values)) < spec.min_value:
                        errors.append(f"feature_below_min:{spec.name}:{spec.min_value}")
                if spec.max_value is not None and finite_values.size:
                    if float(np.nanmax(finite_values)) > spec.max_value:
                        errors.append(f"feature_above_max:{spec.name}:{spec.max_value}")

        return FeatureContractResult(
            valid=not errors,
            contract_version=self.contract_version,
            errors=errors,
        )

    def assert_valid(self, frame: pd.DataFrame) -> FeatureContractResult:
        result = self.validate(frame)
        if not result.valid:
            raise FeatureContractError(";".join(result.errors))
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "features": [feature.to_dict() for feature in self.features],
            "strict_order": self.strict_order,
            "allow_extra_columns": self.allow_extra_columns,
            "allow_nan": self.allow_nan,
            "allow_infinite": self.allow_infinite,
        }


def optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
