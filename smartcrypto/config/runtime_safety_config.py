from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    import yaml
except Exception:  # pragma: no cover - minimal runtimes can still use JSON/simple YAML.
    yaml = None  # type: ignore[assignment]


DEFAULT_REPORT_PATH = Path("data/reports/runtime_safety_config_validation_report.json")
SUPPORTED_ENVIRONMENTS = {"paper", "shadow", "backtest", "research", "live.example"}
ALLOWED_RUNTIME_BY_ENVIRONMENT = {
    "paper": {"paper"},
    "shadow": {"shadow"},
    "backtest": {"backtest", "research"},
    "research": {"research"},
    "live.example": {"paper", "shadow"},
}
LOOKUP_SECTIONS = (
    "safety",
    "runtime",
    "execution",
    "risk",
    "risk_limits",
    "limits",
    "market",
    "data",
    "latency",
    "ai",
    "dashboard",
    "model_governance",
)
REQUIRED_TOP_LEVEL_KEYS = ("schema_version", "config_version", "runtime_mode")
REQUIRED_TRUE_FLAGS = ("dry_run", "paper_only", "shadow_only", "kill_switch_enabled")
UNSAFE_TRUE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
    "ai_can_increase_risk",
    "ai_can_change_leverage",
    "ai_can_change_stake",
    "dashboard_can_change_risk",
    "dashboard_can_promote_model",
    "dashboard_can_enable_live",
)
REQUIRED_RISK_LIMITS = (
    "max_drawdown_pct",
    "max_daily_loss_pct",
    "max_weekly_loss_pct",
    "max_consecutive_losses",
    "max_spread_bps",
    "max_slippage_bps",
    "max_latency_ms",
    "max_data_age_seconds",
    "stale_prediction_max_age_seconds",
)
OPTIONAL_LIMITS = ("max_leverage", "leverage", "max_stake_pct", "stake_pct")
ABSOLUTE_LIMITS = {
    "max_drawdown_pct": 50.0,
    "max_daily_loss_pct": 20.0,
    "max_weekly_loss_pct": 40.0,
    "max_consecutive_losses": 30.0,
    "max_spread_bps": 500.0,
    "max_slippage_bps": 300.0,
    "max_latency_ms": 10000.0,
    "max_data_age_seconds": 7200.0,
    "stale_prediction_max_age_seconds": 7200.0,
    "max_leverage": 10.0,
    "leverage": 10.0,
    "max_stake_pct": 25.0,
    "stake_pct": 25.0,
}
RECOMMENDED_LIMITS = {
    "max_drawdown_pct": 10.0,
    "max_daily_loss_pct": 5.0,
    "max_weekly_loss_pct": 12.0,
    "max_consecutive_losses": 8.0,
    "max_spread_bps": 100.0,
    "max_slippage_bps": 50.0,
    "max_latency_ms": 2000.0,
    "max_data_age_seconds": 900.0,
    "stale_prediction_max_age_seconds": 900.0,
    "max_leverage": 3.0,
    "leverage": 3.0,
    "max_stake_pct": 5.0,
    "stake_pct": 5.0,
}
TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off", ""}


class RuntimeSafetyConfigError(ValueError):
    pass


def load_runtime_config(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"Runtime safety config not found: {target}")

    text = target.read_text(encoding="utf-8")
    suffix = target.suffix.lower()
    if suffix == ".json":
        payload = json.loads(text or "{}")
    elif suffix in {".yml", ".yaml"}:
        payload = yaml.safe_load(text) if yaml is not None else _load_simple_yaml(text)
        payload = payload or {}
    else:
        raise RuntimeSafetyConfigError(f"unsupported_config_format:{target.suffix}")

    if not isinstance(payload, dict):
        raise RuntimeSafetyConfigError("config_root_must_be_object")
    return payload


def validate_runtime_config(
    config: Mapping[str, Any],
    environment: str,
    strict: bool = False,
) -> dict[str, Any]:
    generated_at = utc_now()
    env = str(environment or "").strip().lower()
    report = empty_report(
        generated_at_utc=generated_at,
        config_path=None,
        environment=env,
        strict=strict,
    )

    if not isinstance(config, Mapping):
        report["status"] = "invalid_schema"
        report["reason"] = "config_root_must_be_object"
        report["blocking_findings"].append("config_root_must_be_object")
        return report

    if env not in SUPPORTED_ENVIRONMENTS:
        report["status"] = "invalid_schema"
        report["reason"] = f"unsupported_environment:{env}"
        report["environment_findings"].append(f"unsupported_environment:{env}")
        report["blocking_findings"].append(f"unsupported_environment:{env}")
        return report

    schema_version = _lookup(config, "schema_version")
    config_version = _lookup(config, "config_version")
    runtime_mode = _normalize_text(_lookup(config, "runtime_mode"))
    report["schema_version"] = schema_version
    report["config_version"] = config_version
    report["runtime_mode"] = runtime_mode

    for key, value in (
        ("schema_version", schema_version),
        ("config_version", config_version),
        ("runtime_mode", runtime_mode),
    ):
        if value in (None, ""):
            report["missing_required_keys"].append(key)
            report["blocking_findings"].append(f"missing_required_key:{key}")

    _validate_environment(runtime_mode, env, report)
    _validate_required_true_flags(config, report)
    _validate_unsafe_flags(config, report)
    _validate_risk_limits(config, report)

    if report["warnings"] and strict:
        report["blocking_findings"].extend(f"strict_warning:{warning}" for warning in report["warnings"])

    if report["blocking_findings"]:
        report["status"] = "blocked"
        report["reason"] = "runtime_safety_violations"
    elif report["warnings"]:
        report["status"] = "warning"
        report["reason"] = "runtime_safety_warnings"
    else:
        report["status"] = "ok"
        report["reason"] = "runtime_safety_config_ok"

    return report


def build_runtime_safety_report(
    *,
    config: Mapping[str, Any] | None = None,
    config_path: str | Path | None = None,
    environment: str = "paper",
    report_path: str | Path | None = DEFAULT_REPORT_PATH,
    strict: bool = False,
) -> dict[str, Any]:
    if config is None and config_path is None:
        report = empty_report(
            generated_at_utc=utc_now(),
            config_path=None,
            environment=str(environment or "").strip().lower(),
            strict=strict,
        )
        report["status"] = "missing_config"
        report["reason"] = "missing_config"
        report["blocking_findings"].append("missing_config")
        write_report(report, report_path)
        return report

    loaded_config = config
    if loaded_config is None:
        try:
            loaded_config = load_runtime_config(Path(config_path))  # type: ignore[arg-type]
        except FileNotFoundError:
            report = empty_report(
                generated_at_utc=utc_now(),
                config_path=str(config_path),
                environment=str(environment or "").strip().lower(),
                strict=strict,
            )
            report["status"] = "missing_config"
            report["reason"] = "missing_config"
            report["blocking_findings"].append(f"missing_config:{config_path}")
            write_report(report, report_path)
            return report
        except (RuntimeSafetyConfigError, json.JSONDecodeError) as exc:
            report = empty_report(
                generated_at_utc=utc_now(),
                config_path=str(config_path),
                environment=str(environment or "").strip().lower(),
                strict=strict,
            )
            report["status"] = "invalid_schema"
            report["reason"] = str(exc)
            report["blocking_findings"].append(str(exc))
            write_report(report, report_path)
            return report

    report = validate_runtime_config(loaded_config, environment=environment, strict=strict)
    report["config_path"] = str(config_path) if config_path is not None else None
    write_report(report, report_path)
    return report


def empty_report(
    *,
    generated_at_utc: str,
    config_path: str | None,
    environment: str,
    strict: bool,
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": None,
        "generated_at_utc": generated_at_utc,
        "config_path": config_path,
        "environment": environment,
        "strict": bool(strict),
        "schema_version": None,
        "config_version": None,
        "runtime_mode": None,
        "blocking_findings": [],
        "warnings": [],
        "missing_required_keys": [],
        "unsafe_flags": [],
        "risk_limit_findings": [],
        "environment_findings": [],
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }


def write_report(report: Mapping[str, Any], report_path: str | Path | None) -> None:
    if report_path is None:
        return
    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(report), indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_environment(runtime_mode: str | None, environment: str, report: dict[str, Any]) -> None:
    if not runtime_mode:
        return
    allowed = ALLOWED_RUNTIME_BY_ENVIRONMENT[environment]
    if runtime_mode not in allowed:
        finding = f"runtime_mode_incompatible:{runtime_mode}:environment:{environment}:allowed:{','.join(sorted(allowed))}"
        report["environment_findings"].append(finding)
        report["blocking_findings"].append(finding)
    if runtime_mode == "live":
        finding = "runtime_mode_live_not_allowed"
        report["environment_findings"].append(finding)
        report["blocking_findings"].append(finding)


def _validate_required_true_flags(config: Mapping[str, Any], report: dict[str, Any]) -> None:
    for flag in REQUIRED_TRUE_FLAGS:
        value = _lookup(config, flag)
        if value is None:
            report["missing_required_keys"].append(flag)
            report["blocking_findings"].append(f"missing_required_key:{flag}")
            continue
        parsed = _as_bool(value)
        if parsed is None:
            report["blocking_findings"].append(f"invalid_boolean:{flag}")
            continue
        report[flag] = parsed
        if parsed is not True:
            report["unsafe_flags"].append(flag)
            report["blocking_findings"].append(f"unsafe_flag:{flag}=false")


def _validate_unsafe_flags(config: Mapping[str, Any], report: dict[str, Any]) -> None:
    for flag in UNSAFE_TRUE_FLAGS:
        value = _lookup(config, flag, default=False)
        parsed = _as_bool(value)
        if parsed is None:
            report["blocking_findings"].append(f"invalid_boolean:{flag}")
            continue
        report[flag] = parsed
        if parsed:
            report["unsafe_flags"].append(flag)
            report["blocking_findings"].append(f"unsafe_flag:{flag}=true")


def _validate_risk_limits(config: Mapping[str, Any], report: dict[str, Any]) -> None:
    for key in REQUIRED_RISK_LIMITS:
        _validate_numeric_limit(config, key, required=True, report=report)
    for key in OPTIONAL_LIMITS:
        _validate_numeric_limit(config, key, required=False, report=report)


def _validate_numeric_limit(
    config: Mapping[str, Any],
    key: str,
    *,
    required: bool,
    report: dict[str, Any],
) -> None:
    value = _lookup(config, key)
    if value is None:
        if required:
            report["missing_required_keys"].append(key)
            report["blocking_findings"].append(f"missing_required_key:{key}")
        return
    numeric = _as_positive_number(value)
    if numeric is None:
        finding = f"invalid_risk_limit:{key}"
        report["risk_limit_findings"].append(finding)
        report["blocking_findings"].append(finding)
        return
    if numeric > ABSOLUTE_LIMITS[key]:
        finding = f"absurdly_permissive_risk_limit:{key}={_format_number(numeric)}>{_format_number(ABSOLUTE_LIMITS[key])}"
        report["risk_limit_findings"].append(finding)
        report["blocking_findings"].append(finding)
    elif numeric > RECOMMENDED_LIMITS[key]:
        warning = f"permissive_risk_limit:{key}={_format_number(numeric)}>{_format_number(RECOMMENDED_LIMITS[key])}"
        report["risk_limit_findings"].append(warning)
        report["warnings"].append(warning)


def _lookup(config: Mapping[str, Any], key: str, default: Any = None) -> Any:
    if key in config:
        return config[key]
    for section_name in LOOKUP_SECTIONS:
        section = config.get(section_name)
        if isinstance(section, Mapping) and key in section:
            return section[key]
    return default


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in TRUE_VALUES:
            return True
        if normalized in FALSE_VALUES:
            return False
    return None


def _as_positive_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or numeric <= 0:
        return None
    return numeric


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def _load_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()
        if ":" not in stripped or stripped.startswith("- "):
            raise RuntimeSafetyConfigError("yaml_fallback_supports_only_simple_mappings")
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        value_text = raw_value.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise RuntimeSafetyConfigError("invalid_yaml_indentation")
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
