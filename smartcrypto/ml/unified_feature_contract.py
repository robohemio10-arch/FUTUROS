from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

Status = Literal["ok", "blocked"]
FeatureRole = Literal["qlib", "shadow", "shared"]
FeatureDType = Literal["numeric", "categorical", "datetime", "boolean"]

CONTRACT_ID = "ai_unified_feature_contract"
CONTRACT_VERSION = "v1"

SAFETY_FLAGS: dict[str, Any] = {
    "paper_only": True,
    "shadow_only": True,
    "runtime_mode": "paper",
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "changes_training_dataset": False,
    "writes_trades_master": False,
}

CONTEXT_COLUMNS = {
    "date",
    "datetime",
    "timestamp",
    "timestamp_utc",
    "ts",
    "ts_ms",
    "open_time",
    "open_ts",
    "open_time_utc",
    "close_time",
    "close_time_utc",
    "feature_timestamp_utc",
    "generated_at",
    "generated_at_utc",
    "model_backend",
    "prediction_model_backend",
    "prediction_timestamp",
    "prediction_timestamp_utc",
    "symbol",
    "symbol_norm",
    "pair",
    "tf",
    "timeframe",
    "side",
    "segment",
    "trade_id",
    "trade_index",
    "order_id",
    "source",
    "source_file",
    "venue",
    "exchange",
    "strategy",
    "strategy_id",
    "trade_data_quality_status",
    "train_allowed",
    "row_status",
    "is_compatible",
    "is_exact_compatible",
    "quality_reason",
}

RUNTIME_STATE_COLUMNS = {
    "feature_age_seconds",
    "age_seconds",
    "model_id",
    "model_version",
    "decision_id",
    "risk_decision_id",
}

ALWAYS_BLOCKED_COLUMN_PATTERNS = (
    "future_ret_*",
)

FORBIDDEN_FEATURE_PATTERNS = (
    "future_ret_*",
    "target_*",
    "label_*",
    "pnl_*",
    "profit_*",
    "exit_*",
)

DEFAULT_TIMESTAMP_CANDIDATES = (
    "timestamp_utc",
    "timestamp",
    "datetime",
    "date",
    "ts",
    "ts_ms",
    "open_time_utc",
    "open_time",
    "open_ts",
    "feature_timestamp_utc",
    "generated_at_utc",
    "generated_at",
    "prediction_timestamp_utc",
    "prediction_timestamp",
)


class UnifiedFeatureContractError(ValueError):
    """Raised when a feature contract cannot be built or validated."""


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    dtype: FeatureDType = "numeric"
    unit: str = "unknown"
    window: str = "unknown"
    nullable: bool = False
    min_value: float | None = None
    max_value: float | None = None
    role: FeatureRole = "shared"
    lookback_only: bool = True
    allowed_missing_ratio: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureValidationResult:
    status: Status
    reason: str
    validation_errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    feature_count: int = 0
    feature_columns: tuple[str, ...] = ()
    schema_hash: str | None = None
    feature_order_hash: str | None = None
    checked_at_utc: str = field(default_factory=lambda: utc_timestamp())
    paper_only: bool = True
    shadow_only: bool = True
    runtime_mode: str = "paper"
    live_trading_enabled: bool = False
    order_submission_enabled: bool = False
    real_order_submission_enabled: bool = False
    exchange_private_access: bool = False
    sends_orders: bool = False
    changes_risk: bool = False
    changes_model: bool = False
    changes_training_dataset: bool = False
    writes_trades_master: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UnifiedFeatureContract:
    contract_id: str
    contract_version: str
    dataset_role: str
    features: tuple[FeatureSpec, ...]
    context_columns: tuple[str, ...] = ()
    runtime_state_columns: tuple[str, ...] = ()
    strict_order: bool = True
    allow_extra_features: bool = False
    allow_nan: bool = False
    allow_infinite: bool = False
    source_dataset_path: str | None = None
    created_at_utc: str = field(default_factory=lambda: utc_timestamp())
    feature_order_hash: str | None = None
    schema_hash: str | None = None
    paper_only: bool = True
    shadow_only: bool = True
    runtime_mode: str = "paper"
    live_trading_enabled: bool = False
    order_submission_enabled: bool = False
    real_order_submission_enabled: bool = False
    exchange_private_access: bool = False
    sends_orders: bool = False
    changes_risk: bool = False
    changes_model: bool = False
    changes_training_dataset: bool = False
    writes_trades_master: bool = False

    @property
    def feature_columns(self) -> tuple[str, ...]:
        return tuple(feature.name for feature in self.features)

    @property
    def feature_count(self) -> int:
        return len(self.features)

    def with_hashes(self) -> "UnifiedFeatureContract":
        return replace(
            self,
            feature_order_hash=stable_hash({"feature_columns": list(self.feature_columns)}),
            schema_hash=stable_hash(self.schema_payload()),
        )

    def schema_payload(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "dataset_role": self.dataset_role,
            "feature_columns": list(self.feature_columns),
            "features": [feature.to_dict() for feature in self.features],
            "context_columns": list(self.context_columns),
            "runtime_state_columns": list(self.runtime_state_columns),
            "strict_order": self.strict_order,
            "allow_extra_features": self.allow_extra_features,
            "allow_nan": self.allow_nan,
            "allow_infinite": self.allow_infinite,
        }

    def safety_flags(self) -> dict[str, Any]:
        return {
            "paper_only": self.paper_only,
            "shadow_only": self.shadow_only,
            "runtime_mode": self.runtime_mode,
            "live_trading_enabled": self.live_trading_enabled,
            "order_submission_enabled": self.order_submission_enabled,
            "real_order_submission_enabled": self.real_order_submission_enabled,
            "exchange_private_access": self.exchange_private_access,
            "sends_orders": self.sends_orders,
            "changes_risk": self.changes_risk,
            "changes_model": self.changes_model,
            "changes_training_dataset": self.changes_training_dataset,
            "writes_trades_master": self.writes_trades_master,
        }

    def validate_self(self) -> "UnifiedFeatureContract":
        errors: list[str] = []
        if self.contract_id != CONTRACT_ID:
            errors.append(f"unexpected_contract_id:{self.contract_id}")
        if not self.contract_version:
            errors.append("contract_version_required")
        if not self.features:
            errors.append("empty_feature_contract")
        if len(self.feature_columns) != len(set(self.feature_columns)):
            errors.append("duplicate_features_in_contract")
        forbidden = forbidden_feature_columns(self.feature_columns)
        if forbidden:
            errors.append(f"forbidden_features_in_contract:{forbidden}")
        unsafe = unsafe_safety_flags(self.safety_flags())
        if unsafe:
            errors.extend(f"unsafe_safety_flag:{flag}" for flag in unsafe)
        if errors:
            raise UnifiedFeatureContractError(";".join(errors))
        return self

    def validate_frame(self, frame: pd.DataFrame) -> FeatureValidationResult:
        if not isinstance(frame, pd.DataFrame):
            raise UnifiedFeatureContractError("input_must_be_dataframe")

        errors: list[str] = []
        warnings: list[str] = []
        if frame.empty:
            errors.append("dataset_empty")

        if duplicate_columns := duplicated_columns(frame):
            errors.append(f"duplicate_columns:{duplicate_columns}")

        all_columns = tuple(str(column) for column in frame.columns)
        observed_features = tuple(select_feature_columns(frame, include_non_numeric=True))
        expected = self.feature_columns

        forbidden = forbidden_feature_columns(observed_features)
        if forbidden:
            errors.append(f"forbidden_feature_columns:{forbidden}")

        missing = [column for column in expected if column not in observed_features]
        if missing:
            errors.append(f"missing_features:{missing}")

        extra = [column for column in observed_features if column not in expected]
        if extra and not self.allow_extra_features:
            errors.append(f"unexpected_features:{extra}")

        if self.strict_order and not missing:
            actual_order = tuple(column for column in observed_features if column in expected)
            if actual_order != expected:
                errors.append(
                    "feature_order_mismatch:"
                    f"expected={list(expected)}:actual={list(actual_order)}"
                )

        duplicate_key = resolve_identity_key_columns(frame)
        if duplicate_key:
            duplicate_count = duplicate_key_count(frame, duplicate_key)
            if duplicate_count:
                if len(duplicate_key) == 1 and duplicate_key[0] == resolve_timestamp_column(frame):
                    errors.append(f"duplicate_timestamp:{duplicate_key[0]}:{duplicate_count}")
                else:
                    errors.append(f"duplicate_identity_key:{list(duplicate_key)}:{duplicate_count}")

        if timestamp_column := resolve_timestamp_column(frame):
            timestamps = pd.to_datetime(frame[timestamp_column], utc=True, errors="coerce")
            if timestamps.notna().any() and not timestamps.dropna().is_monotonic_increasing:
                warnings.append(f"timestamp_not_monotonic:{timestamp_column}")

        for spec in self.features:
            if spec.name not in frame.columns:
                continue
            series = frame[spec.name]
            errors.extend(validate_series_against_spec(series, spec, self))

        status: Status = "blocked" if errors else "ok"
        return FeatureValidationResult(
            status=status,
            reason="ok" if status == "ok" else ";".join(sorted(set(errors))),
            validation_errors=tuple(sorted(set(errors))),
            warnings=tuple(sorted(set(warnings))),
            feature_count=len(expected),
            feature_columns=expected,
            schema_hash=self.schema_hash,
            feature_order_hash=self.feature_order_hash,
            **self.safety_flags(),
        )

    def assert_valid_frame(self, frame: pd.DataFrame) -> FeatureValidationResult:
        result = self.validate_frame(frame)
        if result.status != "ok":
            raise UnifiedFeatureContractError(result.reason)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "dataset_role": self.dataset_role,
            "created_at_utc": self.created_at_utc,
            "source_dataset_path": self.source_dataset_path,
            "feature_count": self.feature_count,
            "feature_columns": list(self.feature_columns),
            "features": [feature.to_dict() for feature in self.features],
            "context_columns": list(self.context_columns),
            "runtime_state_columns": list(self.runtime_state_columns),
            "strict_order": self.strict_order,
            "allow_extra_features": self.allow_extra_features,
            "allow_nan": self.allow_nan,
            "allow_infinite": self.allow_infinite,
            "feature_order_hash": self.feature_order_hash,
            "schema_hash": self.schema_hash,
            **self.safety_flags(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "UnifiedFeatureContract":
        features = tuple(
            FeatureSpec(
                name=str(item["name"]),
                dtype=str(item.get("dtype", "numeric")),  # type: ignore[arg-type]
                unit=str(item.get("unit", "unknown")),
                window=str(item.get("window", "unknown")),
                nullable=bool(item.get("nullable", False)),
                min_value=optional_float(item.get("min_value")),
                max_value=optional_float(item.get("max_value")),
                role=str(item.get("role", "shared")),  # type: ignore[arg-type]
                lookback_only=bool(item.get("lookback_only", True)),
                allowed_missing_ratio=float(item.get("allowed_missing_ratio", 0.0)),
            )
            for item in payload.get("features", [])
        )
        return cls(
            contract_id=str(payload.get("contract_id", CONTRACT_ID)),
            contract_version=str(payload.get("contract_version", CONTRACT_VERSION)),
            dataset_role=str(payload.get("dataset_role", "unified")),
            features=features,
            context_columns=tuple(str(item) for item in payload.get("context_columns", [])),
            runtime_state_columns=tuple(str(item) for item in payload.get("runtime_state_columns", [])),
            strict_order=bool(payload.get("strict_order", True)),
            allow_extra_features=bool(payload.get("allow_extra_features", False)),
            allow_nan=bool(payload.get("allow_nan", False)),
            allow_infinite=bool(payload.get("allow_infinite", False)),
            source_dataset_path=payload.get("source_dataset_path"),
            created_at_utc=str(payload.get("created_at_utc", utc_timestamp())),
            feature_order_hash=payload.get("feature_order_hash"),
            schema_hash=payload.get("schema_hash"),
            paper_only=bool(payload.get("paper_only", True)),
            shadow_only=bool(payload.get("shadow_only", True)),
            runtime_mode=str(payload.get("runtime_mode", "paper")),
            live_trading_enabled=bool(payload.get("live_trading_enabled", False)),
            order_submission_enabled=bool(payload.get("order_submission_enabled", False)),
            real_order_submission_enabled=bool(payload.get("real_order_submission_enabled", False)),
            exchange_private_access=bool(payload.get("exchange_private_access", False)),
            sends_orders=bool(payload.get("sends_orders", False)),
            changes_risk=bool(payload.get("changes_risk", False)),
            changes_model=bool(payload.get("changes_model", False)),
            changes_training_dataset=bool(payload.get("changes_training_dataset", False)),
            writes_trades_master=bool(payload.get("writes_trades_master", False)),
        ).validate_self()


def build_contract_from_frame(
    frame: pd.DataFrame,
    *,
    dataset_role: str,
    source_dataset_path: str | Path | None = None,
    feature_columns: tuple[str, ...] | list[str] | None = None,
    strict_order: bool = True,
    allow_extra_features: bool = False,
    allow_nan: bool = False,
    allow_infinite: bool = False,
    role: FeatureRole = "shared",
    unit: str = "unknown",
    window: str = "lookback",
) -> UnifiedFeatureContract:
    if not isinstance(frame, pd.DataFrame):
        raise UnifiedFeatureContractError("input_must_be_dataframe")
    if frame.empty:
        raise UnifiedFeatureContractError("dataset_empty")

    if blocked := always_blocked_columns(tuple(str(column) for column in frame.columns)):
        raise UnifiedFeatureContractError(f"blocked_source_columns:{blocked}")

    selected = tuple(str(column) for column in feature_columns) if feature_columns else tuple(select_feature_columns(frame))
    if not selected:
        raise UnifiedFeatureContractError("no_feature_columns_selected")
    if duplicate := duplicated_strings(selected):
        raise UnifiedFeatureContractError(f"duplicate_feature_columns:{duplicate}")
    if forbidden := forbidden_feature_columns(selected):
        raise UnifiedFeatureContractError(f"forbidden_feature_columns:{forbidden}")

    specs: list[FeatureSpec] = []
    for column in selected:
        if column not in frame.columns:
            raise UnifiedFeatureContractError(f"selected_feature_missing:{column}")
        series = frame[column]
        dtype = infer_feature_dtype(series)
        numeric = pd.to_numeric(series, errors="coerce") if dtype == "numeric" else None
        min_value: float | None = None
        max_value: float | None = None
        if numeric is not None:
            finite_values = numeric[np.isfinite(numeric)]
            if not finite_values.empty:
                min_value = float(finite_values.min())
                max_value = float(finite_values.max())
        specs.append(
            FeatureSpec(
                name=column,
                dtype=dtype,
                unit=unit,
                window=window,
                nullable=allow_nan,
                min_value=min_value,
                max_value=max_value,
                role=role,
                lookback_only=True,
                allowed_missing_ratio=1.0 if allow_nan else 0.0,
            )
        )

    contract = UnifiedFeatureContract(
        contract_id=CONTRACT_ID,
        contract_version=CONTRACT_VERSION,
        dataset_role=dataset_role,
        features=tuple(specs),
        context_columns=tuple(column for column in frame.columns if classify_column(str(column)) == "context"),
        runtime_state_columns=tuple(
            column for column in frame.columns if classify_column(str(column)) == "runtime_state"
        ),
        strict_order=strict_order,
        allow_extra_features=allow_extra_features,
        allow_nan=allow_nan,
        allow_infinite=allow_infinite,
        source_dataset_path=str(source_dataset_path) if source_dataset_path is not None else None,
        **SAFETY_FLAGS,
    ).validate_self().with_hashes()

    validation = contract.validate_frame(frame)
    if validation.status != "ok":
        raise UnifiedFeatureContractError(validation.reason)
    return contract


def load_contract(path: str | Path) -> UnifiedFeatureContract:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return UnifiedFeatureContract.from_dict(payload)


def write_contract(contract: UnifiedFeatureContract, path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(contract.to_dict(), indent=2, ensure_ascii=False, sort_keys=True, default=safe_json),
        encoding="utf-8",
    )


def read_table(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(file_path)
    if suffix == ".csv":
        return pd.read_csv(file_path)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(file_path)
    if suffix == ".jsonl":
        rows: list[dict[str, Any]] = []
        with file_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
        return pd.DataFrame(rows)
    if suffix == ".json":
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return pd.DataFrame(payload)
        if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
            return pd.DataFrame(payload["rows"])
        return pd.DataFrame([payload])
    raise UnifiedFeatureContractError(f"unsupported_input_format:{suffix}")


def select_feature_columns(frame: pd.DataFrame, *, include_non_numeric: bool = False) -> list[str]:
    selected: list[str] = []
    for column in frame.columns:
        name = str(column)
        category = classify_column(name)
        if category in {"context", "runtime_state", "forbidden"}:
            continue
        if category == "blocked":
            selected.append(name)
            continue
        series = frame[column]
        if include_non_numeric or is_feature_like(name, series):
            selected.append(name)
    return selected


def classify_column(name: str) -> Literal["context", "runtime_state", "blocked", "forbidden", "feature"]:
    normalized = name.strip()
    lower = normalized.lower()
    if lower in CONTEXT_COLUMNS:
        return "context"
    if lower in RUNTIME_STATE_COLUMNS:
        return "runtime_state"
    if always_blocked_columns((normalized,)):
        return "blocked"
    if forbidden_feature_columns((normalized,)):
        return "forbidden"
    return "feature"


def is_feature_like(name: str, series: pd.Series) -> bool:
    lower = name.lower()
    if lower.startswith(("feature_", "qlib_", "shadow_", "market_", "ai_")):
        return True
    if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
        return True
    return False


def always_blocked_columns(columns: tuple[str, ...] | list[str]) -> list[str]:
    blocked: list[str] = []
    for column in columns:
        name = str(column)
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in ALWAYS_BLOCKED_COLUMN_PATTERNS):
            blocked.append(name)
    return sorted(blocked)


def forbidden_feature_columns(columns: tuple[str, ...] | list[str]) -> list[str]:
    blocked: list[str] = []
    for column in columns:
        name = str(column)
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in FORBIDDEN_FEATURE_PATTERNS):
            blocked.append(name)
    return sorted(blocked)


def resolve_identity_key_columns(frame: pd.DataFrame) -> tuple[str, ...]:
    columns_by_lower = {str(column).lower(): str(column) for column in frame.columns}
    for candidate in ("trade_id", "order_id"):
        if candidate in columns_by_lower:
            return (columns_by_lower[candidate],)

    timestamp_column = resolve_timestamp_column(frame)
    if not timestamp_column:
        return ()

    key = [timestamp_column]
    for candidate in ("symbol", "symbol_norm", "pair", "tf", "timeframe", "venue", "exchange"):
        actual = columns_by_lower.get(candidate)
        if actual and actual not in key:
            key.append(actual)
    return tuple(key)


def duplicate_key_count(frame: pd.DataFrame, key_columns: tuple[str, ...]) -> int:
    if not key_columns or any(column not in frame.columns for column in key_columns):
        return 0
    key_frame = frame.loc[:, list(key_columns)].astype("string").fillna("<NA>")
    return int(key_frame.duplicated().sum())


def validate_series_against_spec(
    series: pd.Series,
    spec: FeatureSpec,
    contract: UnifiedFeatureContract,
) -> list[str]:
    errors: list[str] = []
    if spec.dtype == "numeric":
        if not pd.api.types.is_numeric_dtype(series):
            errors.append(f"dtype_invalid:{spec.name}:expected=numeric:actual={series.dtype}")
            return errors
        numeric = pd.to_numeric(series, errors="coerce")
        if not contract.allow_nan:
            missing_ratio = float(numeric.isna().mean()) if len(numeric) else 1.0
            if bool(numeric.isna().any()) or missing_ratio > spec.allowed_missing_ratio:
                errors.append(f"nan_detected:{spec.name}")
        values = numeric.to_numpy(dtype=float, copy=False)
        if not contract.allow_infinite and bool(np.isinf(values).any()):
            errors.append(f"infinite_detected:{spec.name}")
        finite_values = values[np.isfinite(values)]
        if spec.min_value is not None and finite_values.size and float(np.nanmin(finite_values)) < spec.min_value:
            errors.append(f"range_below_min:{spec.name}:{spec.min_value}")
        if spec.max_value is not None and finite_values.size and float(np.nanmax(finite_values)) > spec.max_value:
            errors.append(f"range_above_max:{spec.name}:{spec.max_value}")
    elif spec.dtype == "datetime":
        parsed = pd.to_datetime(series, utc=True, errors="coerce")
        if not contract.allow_nan and bool(parsed.isna().any()):
            errors.append(f"datetime_invalid:{spec.name}")
    elif spec.dtype == "boolean":
        if not pd.api.types.is_bool_dtype(series):
            errors.append(f"dtype_invalid:{spec.name}:expected=boolean:actual={series.dtype}")
    return errors


def infer_feature_dtype(series: pd.Series) -> FeatureDType:
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    return "categorical"


def resolve_timestamp_column(frame: pd.DataFrame) -> str | None:
    columns = {str(column): column for column in frame.columns}
    for candidate in DEFAULT_TIMESTAMP_CANDIDATES:
        if candidate in columns:
            return str(columns[candidate])
    return None


def duplicated_columns(frame: pd.DataFrame) -> list[str]:
    return duplicated_strings(tuple(str(column) for column in frame.columns))


def duplicated_strings(values: tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def unsafe_safety_flags(flags: dict[str, Any]) -> list[str]:
    unsafe: list[str] = []
    if flags.get("paper_only") is not True:
        unsafe.append("paper_only")
    if flags.get("shadow_only") is not True:
        unsafe.append("shadow_only")
    if flags.get("runtime_mode") != "paper":
        unsafe.append("runtime_mode")
    for key in (
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "changes_model",
        "changes_training_dataset",
        "writes_trades_master",
    ):
        if flags.get(key) is True:
            unsafe.append(key)
    return unsafe


def stable_hash(payload: Any) -> str:
    material = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=safe_json)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    return float(value)


def safe_json(value: Any) -> Any:
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return str(value)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
