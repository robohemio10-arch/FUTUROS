from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_CONTRACT_ID = "ai_shadow_feature_contract"
DEFAULT_CONTRACT_VERSION = "v1"
DEFAULT_OUTPUT_PATH = Path("data/models/shadow/ai_shadow_feature_contract.json")
NON_MODEL_FEATURE_COLUMNS = {
    "feature_timestamp_utc",
    "feature_age_seconds",
}


class FeatureContractError(ValueError):
    pass


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    dtype: str = "numeric"
    min_value: float | None = None
    max_value: float | None = None
    nullable: bool = False
    allowed_missing_ratio: float = 0.0

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | str) -> "FeatureSpec":
        if isinstance(payload, str):
            return cls(name=payload)
        return cls(
            name=str(payload["name"]),
            dtype=str(payload.get("dtype", "numeric")),
            min_value=optional_float(payload.get("min_value")),
            max_value=optional_float(payload.get("max_value")),
            nullable=bool(payload.get("nullable", False)),
            allowed_missing_ratio=float(payload.get("allowed_missing_ratio", 0.0)),
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
    contract_id: str = DEFAULT_CONTRACT_ID
    created_at_utc: str = field(default_factory=lambda: utc_timestamp())
    feature_order_hash: str | None = None
    schema_hash: str | None = None
    dtypes: dict[str, str] = field(default_factory=dict)
    nullable_policy: dict[str, bool] = field(default_factory=dict)
    finite_required: bool = True
    allowed_missing_ratio: float = 0.0
    source_dataset_path: str | None = None
    source_model_id: str | None = None
    source_model_version: str | None = None
    paper_only: bool = True
    shadow_only: bool = True
    runtime_mode: str = "paper"
    live_trading_enabled: bool = False
    order_submission_enabled: bool = False
    real_order_submission_enabled: bool = False
    exchange_private_access: bool = False

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FeatureContract":
        if not isinstance(payload, dict):
            raise FeatureContractError("feature_contract_root_must_be_mapping")

        raw_features = payload.get("features")
        if raw_features is None:
            raw_features = payload.get("feature_columns")
        if not isinstance(raw_features, list) or not raw_features:
            raise FeatureContractError("feature_contract_features_required")

        dtypes = {str(k): str(v) for k, v in dict(payload.get("dtypes") or {}).items()}
        ranges = dict(payload.get("ranges") or {})
        nullable_policy = {
            str(k): bool(v) for k, v in dict(payload.get("nullable_policy") or {}).items()
        }
        features: list[FeatureSpec] = []
        for item in raw_features:
            if isinstance(item, str):
                name = item
                limits = ranges.get(name, {}) if isinstance(ranges.get(name, {}), dict) else {}
                features.append(
                    FeatureSpec(
                        name=name,
                        dtype=dtypes.get(name, "numeric"),
                        min_value=optional_float(limits.get("min_value")),
                        max_value=optional_float(limits.get("max_value")),
                        nullable=nullable_policy.get(name, False),
                        allowed_missing_ratio=float(
                            payload.get("allowed_missing_ratio", 0.0)
                        ),
                    )
                )
            else:
                features.append(FeatureSpec.from_dict(item))

        contract = cls(
            contract_version=str(payload.get("contract_version") or "").strip(),
            features=tuple(features),
            strict_order=bool(payload.get("strict_order", True)),
            allow_extra_columns=bool(payload.get("allow_extra_columns", True)),
            allow_nan=bool(payload.get("allow_nan", False)),
            allow_infinite=bool(payload.get("allow_infinite", False)),
            contract_id=str(payload.get("contract_id", DEFAULT_CONTRACT_ID)),
            created_at_utc=str(payload.get("created_at_utc") or utc_timestamp()),
            feature_order_hash=payload.get("feature_order_hash"),
            schema_hash=payload.get("schema_hash"),
            dtypes=dtypes,
            nullable_policy=nullable_policy,
            finite_required=bool(payload.get("finite_required", True)),
            allowed_missing_ratio=float(payload.get("allowed_missing_ratio", 0.0)),
            source_dataset_path=payload.get("source_dataset_path"),
            source_model_id=payload.get("source_model_id"),
            source_model_version=payload.get("source_model_version"),
            paper_only=bool(payload.get("paper_only", True)),
            shadow_only=bool(payload.get("shadow_only", True)),
            runtime_mode=str(payload.get("runtime_mode", "paper")),
            live_trading_enabled=bool(payload.get("live_trading_enabled", False)),
            order_submission_enabled=bool(payload.get("order_submission_enabled", False)),
            real_order_submission_enabled=bool(
                payload.get("real_order_submission_enabled", False)
            ),
            exchange_private_access=bool(payload.get("exchange_private_access", False)),
        ).validate_contract()
        return contract.with_computed_hashes()

    @property
    def feature_names(self) -> tuple[str, ...]:
        return tuple(feature.name for feature in self.features)

    @property
    def feature_columns(self) -> tuple[str, ...]:
        return self.feature_names

    @property
    def feature_count(self) -> int:
        return len(self.features)

    @property
    def ranges(self) -> dict[str, dict[str, float | None]]:
        return {
            feature.name: {"min_value": feature.min_value, "max_value": feature.max_value}
            for feature in self.features
            if feature.min_value is not None or feature.max_value is not None
        }

    def validate_contract(self) -> "FeatureContract":
        if not self.contract_version:
            raise FeatureContractError("feature_contract_version_required")
        names = self.feature_names
        if not names:
            raise FeatureContractError("feature_contract_empty_features")
        if len(names) != len(set(names)):
            raise FeatureContractError("feature_contract_duplicate_features")
        lookahead = lookahead_columns(names)
        if lookahead:
            raise FeatureContractError(f"feature_contract_lookahead_columns:{lookahead}")
        targets = target_columns(names)
        if targets:
            raise FeatureContractError(f"feature_contract_target_columns:{targets}")
        return self

    def with_computed_hashes(self) -> "FeatureContract":
        feature_order_hash = hash_json({"feature_columns": list(self.feature_names)})
        schema_hash = hash_json(
            {
                "feature_columns": list(self.feature_names),
                "dtypes": self.expected_dtypes(),
                "nullable_policy": self.expected_nullable_policy(),
                "finite_required": self.finite_required,
                "allowed_missing_ratio": self.allowed_missing_ratio,
                "ranges": self.ranges,
            }
        )
        return replace(
            self,
            feature_order_hash=feature_order_hash,
            schema_hash=schema_hash,
        )

    def expected_dtypes(self) -> dict[str, str]:
        dtypes = {feature.name: feature.dtype for feature in self.features}
        dtypes.update(self.dtypes)
        return {name: dtypes.get(name, "numeric") for name in self.feature_names}

    def expected_nullable_policy(self) -> dict[str, bool]:
        policy = {feature.name: feature.nullable for feature in self.features}
        policy.update(self.nullable_policy)
        return {name: bool(policy.get(name, False)) for name in self.feature_names}

    def safety_flags(self) -> dict[str, Any]:
        return {
            "paper_only": self.paper_only,
            "shadow_only": self.shadow_only,
            "runtime_mode": self.runtime_mode,
            "live_trading_enabled": self.live_trading_enabled,
            "order_submission_enabled": self.order_submission_enabled,
            "real_order_submission_enabled": self.real_order_submission_enabled,
            "exchange_private_access": self.exchange_private_access,
        }

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

    def to_dict(self, *, include_hashes: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "created_at_utc": self.created_at_utc,
            "feature_columns": list(self.feature_names),
            "feature_count": self.feature_count,
            "features": [feature.to_dict() for feature in self.features],
            "dtypes": self.expected_dtypes(),
            "nullable_policy": self.expected_nullable_policy(),
            "finite_required": self.finite_required,
            "allowed_missing_ratio": self.allowed_missing_ratio,
            "ranges": self.ranges,
            "source_dataset_path": self.source_dataset_path,
            "source_model_id": self.source_model_id,
            "source_model_version": self.source_model_version,
            "strict_order": self.strict_order,
            "allow_extra_columns": self.allow_extra_columns,
            "allow_nan": self.allow_nan,
            "allow_infinite": self.allow_infinite,
            **self.safety_flags(),
        }
        if include_hashes:
            payload["feature_order_hash"] = self.feature_order_hash
            payload["schema_hash"] = self.schema_hash
        return payload


def build_ai_shadow_feature_contract_from_frame(
    frame: pd.DataFrame,
    *,
    feature_prefix: str = "feature_",
    output_path: str | Path | None = None,
    source_dataset_path: str | Path | None = None,
    source_model_id: str | None = None,
    source_model_version: str | None = None,
    contract_id: str = DEFAULT_CONTRACT_ID,
    contract_version: str = DEFAULT_CONTRACT_VERSION,
    allowed_missing_ratio: float = 0.0,
    strict_order: bool = True,
    strict: bool = True,
) -> FeatureContract:
    if not isinstance(frame, pd.DataFrame):
        raise FeatureContractError("input_must_be_dataframe")
    feature_columns = select_feature_columns(frame, feature_prefix=feature_prefix)
    if not feature_columns:
        raise FeatureContractError("empty_feature_columns")
    if len(feature_columns) != len(set(feature_columns)):
        raise FeatureContractError("duplicate_feature_columns")
    blocked_lookahead = lookahead_columns(feature_columns)
    if blocked_lookahead:
        raise FeatureContractError(f"lookahead_columns_detected:{blocked_lookahead}")
    blocked_targets = target_columns(feature_columns)
    if blocked_targets:
        raise FeatureContractError(f"target_columns_detected:{blocked_targets}")

    features: list[FeatureSpec] = []
    dtypes: dict[str, str] = {}
    nullable_policy: dict[str, bool] = {}
    for column in feature_columns:
        series = frame[column]
        if isinstance(series, pd.DataFrame):
            raise FeatureContractError(f"feature_not_1d:{column}")
        if strict and not pd.api.types.is_numeric_dtype(series):
            raise FeatureContractError(f"non_numeric_feature:{column}")
        numeric = pd.to_numeric(series, errors="coerce")
        nan_ratio = float(numeric.isna().mean()) if len(numeric) else 1.0
        if nan_ratio > allowed_missing_ratio:
            raise FeatureContractError(f"nan_ratio_exceeded:{column}:{nan_ratio}")
        finite_values = numeric.dropna().to_numpy(dtype=float, copy=False)
        if bool(np.isinf(finite_values).any()):
            raise FeatureContractError(f"infinite_feature_values:{column}")
        dtypes[column] = "numeric"
        nullable_policy[column] = allowed_missing_ratio > 0
        min_value = float(np.min(finite_values)) if finite_values.size else None
        max_value = float(np.max(finite_values)) if finite_values.size else None
        features.append(
            FeatureSpec(
                name=column,
                dtype="numeric",
                min_value=min_value,
                max_value=max_value,
                nullable=allowed_missing_ratio > 0,
                allowed_missing_ratio=allowed_missing_ratio,
            )
        )

    contract = FeatureContract(
        contract_id=contract_id,
        contract_version=contract_version,
        created_at_utc=utc_timestamp(),
        features=tuple(features),
        strict_order=strict_order,
        allow_extra_columns=False,
        allow_nan=allowed_missing_ratio > 0,
        allow_infinite=False,
        dtypes=dtypes,
        nullable_policy=nullable_policy,
        finite_required=True,
        allowed_missing_ratio=allowed_missing_ratio,
        source_dataset_path=str(source_dataset_path) if source_dataset_path else None,
        source_model_id=source_model_id,
        source_model_version=source_model_version,
        paper_only=True,
        shadow_only=True,
        runtime_mode="paper",
        live_trading_enabled=False,
        order_submission_enabled=False,
        real_order_submission_enabled=False,
        exchange_private_access=False,
    ).validate_contract()
    contract = contract.with_computed_hashes()
    if output_path is not None:
        write_feature_contract(contract, output_path)
    return contract


def build_ai_shadow_feature_contract_from_parquet(
    input_path: str | Path,
    **kwargs: Any,
) -> FeatureContract:
    path = Path(input_path)
    if not path.exists():
        raise FeatureContractError("missing_input")
    frame = read_table(path)
    return build_ai_shadow_feature_contract_from_frame(
        frame,
        source_dataset_path=kwargs.pop("source_dataset_path", path),
        **kwargs,
    )


def select_feature_columns(frame: pd.DataFrame, *, feature_prefix: str = "feature_") -> list[str]:
    columns: list[str] = []
    seen: set[str] = set()
    for column in frame.columns:
        name = str(column)
        if feature_prefix and not name.startswith(feature_prefix):
            continue
        if name in NON_MODEL_FEATURE_COLUMNS or name.endswith("_utc") or name.endswith("_ts"):
            continue
        if name in seen:
            columns.append(name)
            continue
        seen.add(name)
        columns.append(name)
    return columns


def read_table(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(file_path)
    if suffix == ".csv":
        return pd.read_csv(file_path)
    if suffix in {".json", ".jsonl"}:
        return pd.read_json(file_path, lines=suffix == ".jsonl")
    raise FeatureContractError(f"unsupported_input_format:{suffix}")


def write_feature_contract(contract: FeatureContract, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(contract.to_dict(), indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def load_feature_contract(path: str | Path) -> FeatureContract:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return FeatureContract.from_dict(payload)


def lookahead_columns(columns: list[str] | tuple[str, ...]) -> list[str]:
    return sorted(str(column) for column in columns if str(column).startswith("future_ret_"))


def target_columns(columns: list[str] | tuple[str, ...]) -> list[str]:
    return sorted(str(column) for column in columns if str(column).startswith("target_"))


def hash_json(payload: dict[str, Any]) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
