from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - exercised in minimal runtimes without PyYAML.
    yaml = None  # type: ignore[assignment]


SAFE_RUNTIME_MODES = {"paper", "research", "shadow"}
REQUIRED_LIMITS = (
    "max_drawdown_pct",
    "max_data_age_seconds",
    "max_spread_bps",
    "max_order_notional",
    "max_capital_global",
)
FALSE_BY_DEFAULT_FLAGS = (
    "live_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "allow_ai_to_increase_size",
    "allow_dashboard_direct_order",
)
TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off", ""}


class ConfigValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("Invalid SmartCrypto config: " + "; ".join(errors))


@dataclass(frozen=True)
class SafeConfig:
    runtime_mode: str
    live_enabled: bool
    order_submission_enabled: bool
    real_order_submission_enabled: bool
    allow_ai_to_increase_size: bool
    allow_dashboard_direct_order: bool
    max_drawdown_pct: float
    max_data_age_seconds: int
    max_spread_bps: float
    max_order_notional: float
    max_capital_global: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_config_file(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"Config file not found: {target}")

    suffix = target.suffix.lower()
    text = target.read_text(encoding="utf-8")
    if suffix in {".yml", ".yaml"}:
        payload = yaml.safe_load(text) if yaml is not None else _load_simple_yaml(text)
        payload = payload or {}
    elif suffix == ".json":
        payload = json.loads(text or "{}")
    else:
        raise ConfigValidationError([f"unsupported_config_format:{target.suffix}"])

    if not isinstance(payload, dict):
        raise ConfigValidationError(["config_root_must_be_object"])
    return payload


def validate_config(config: dict[str, Any]) -> SafeConfig:
    if not isinstance(config, dict):
        raise ConfigValidationError(["config_root_must_be_object"])

    errors: list[str] = []
    runtime_mode = str(_lookup(config, "runtime_mode", default="paper")).strip().lower()
    if runtime_mode not in SAFE_RUNTIME_MODES:
        errors.append(
            f"runtime_mode_not_allowed:{runtime_mode}; allowed={sorted(SAFE_RUNTIME_MODES)}"
        )

    flag_values: dict[str, bool] = {}
    for flag_name in FALSE_BY_DEFAULT_FLAGS:
        try:
            flag_values[flag_name] = _as_bool(
                _lookup(config, flag_name, default=False),
                field_name=flag_name,
            )
        except ConfigValidationError as exc:
            errors.extend(exc.errors)
            flag_values[flag_name] = False

    for flag_name in (
        "live_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "allow_ai_to_increase_size",
        "allow_dashboard_direct_order",
    ):
        if flag_values[flag_name]:
            errors.append(f"{flag_name}_must_be_false")

    limit_values: dict[str, float | int] = {}
    for limit_name in REQUIRED_LIMITS:
        raw_value = _lookup(config, limit_name, default=None)
        if raw_value is None:
            errors.append(f"missing_required_limit:{limit_name}")
            continue
        try:
            numeric_value = _as_positive_number(raw_value, field_name=limit_name)
        except ConfigValidationError as exc:
            errors.extend(exc.errors)
            continue
        if limit_name == "max_data_age_seconds":
            limit_values[limit_name] = int(numeric_value)
        else:
            limit_values[limit_name] = float(numeric_value)

    if errors:
        raise ConfigValidationError(errors)

    return SafeConfig(
        runtime_mode=runtime_mode,
        live_enabled=flag_values["live_enabled"],
        order_submission_enabled=flag_values["order_submission_enabled"],
        real_order_submission_enabled=flag_values["real_order_submission_enabled"],
        allow_ai_to_increase_size=flag_values["allow_ai_to_increase_size"],
        allow_dashboard_direct_order=flag_values["allow_dashboard_direct_order"],
        max_drawdown_pct=float(limit_values["max_drawdown_pct"]),
        max_data_age_seconds=int(limit_values["max_data_age_seconds"]),
        max_spread_bps=float(limit_values["max_spread_bps"]),
        max_order_notional=float(limit_values["max_order_notional"]),
        max_capital_global=float(limit_values["max_capital_global"]),
    )


def validate_config_file(path: str | Path) -> SafeConfig:
    return validate_config(load_config_file(path))


def assert_config_safe(config: dict[str, Any]) -> None:
    validate_config(config)


def _lookup(config: dict[str, Any], key: str, default: Any = None) -> Any:
    if key in config:
        return config[key]

    for section_name in ("safety", "execution", "runtime", "limits", "risk", "risk_limits"):
        section = config.get(section_name)
        if isinstance(section, dict) and key in section:
            return section[key]
    return default


def _as_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in TRUE_VALUES:
            return True
        if normalized in FALSE_VALUES:
            return False
    raise ConfigValidationError([f"{field_name}_must_be_boolean"])


def _as_positive_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise ConfigValidationError([f"{field_name}_must_be_positive_number"])
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise ConfigValidationError([f"{field_name}_must_be_positive_number"]) from None
    if numeric <= 0:
        raise ConfigValidationError([f"{field_name}_must_be_positive_number"])
    return numeric


def _load_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()
        if ":" not in stripped or stripped.startswith("- "):
            raise ConfigValidationError(["yaml_fallback_supports_only_simple_mappings"])

        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        value_text = raw_value.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ConfigValidationError(["invalid_yaml_indentation"])
        parent = stack[-1][1]

        if value_text == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_yaml_scalar(value_text)

    return root


def _parse_yaml_scalar(value: str) -> Any:
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]

    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    if normalized in {"null", "none", "~"}:
        return None

    try:
        if any(char in value for char in (".", "e", "E")):
            return float(value)
        return int(value)
    except ValueError:
        return value
