from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import pandas as pd
except Exception:  # pragma: no cover - only used in minimal test runtimes.
    pd = None  # type: ignore[assignment]

try:
    import yaml
except Exception:  # pragma: no cover - only used in minimal test runtimes.
    yaml = None  # type: ignore[assignment]


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
SAFE_RUNTIME_MODES = {"paper", "research", "shadow"}


@dataclass(frozen=True)
class RiskLimits:
    runtime_mode: str = "paper"
    max_position_usdt: float = 50.0
    max_leverage: float = 2.0
    signal_ttl_seconds: int = 300
    kill_switch_enabled: bool = False
    allowed_pairs: tuple[str, ...] = ()


@dataclass(frozen=True)
class SignalRiskDecision:
    approved: bool
    status: str
    reasons: list[str]
    signal: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RiskManager:
    """Backward-compatible signal gate used by legacy tests and paper pipelines."""

    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    @classmethod
    def from_yaml(cls, path: str | Path) -> "RiskManager":
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"RiskManager config file not found: {config_path}")
        payload = load_yaml(config_path)
        limits = risk_limits_from_config(payload, source_path=config_path)
        return cls(limits)

    def approve(self, signal: dict[str, Any]) -> SignalRiskDecision:
        reasons: list[str] = []
        output = dict(signal)

        runtime_mode = str(self.limits.runtime_mode).strip().lower()
        if runtime_mode not in SAFE_RUNTIME_MODES:
            reasons.append(f"runtime_mode_not_allowed:{self.limits.runtime_mode}")

        if env_enabled("LIVE_ENABLED"):
            reasons.append("LIVE_ENABLED=true")
        if env_enabled("ORDER_SUBMISSION_ENABLED"):
            reasons.append("ORDER_SUBMISSION_ENABLED=true")
        if env_enabled("REAL_ORDER_SUBMISSION_ENABLED"):
            reasons.append("REAL_ORDER_SUBMISSION_ENABLED=true")

        if self.limits.kill_switch_enabled:
            reasons.append("kill_switch_enabled")

        pair = str(output.get("pair") or output.get("symbol") or "").strip()
        if self.limits.allowed_pairs and pair not in self.limits.allowed_pairs:
            reasons.append(f"pair_not_allowed:{pair}")

        proposed_side = str(output.get("proposed_side") or "").strip().lower()
        supplied_side = str(output.get("side") or "").strip().lower()
        if proposed_side not in {"long", "short"}:
            reasons.append("proposed_side_missing_or_invalid")
        elif supplied_side and supplied_side != proposed_side:
            reasons.append("side_does_not_match_proposed_side")
        else:
            output["proposed_side"] = proposed_side
            output["side"] = proposed_side

        if self.limits.max_position_usdt <= 0:
            reasons.append("max_position_usdt_invalid")
        if self.limits.max_leverage <= 0:
            reasons.append("max_leverage_invalid")

        approved = not reasons
        output["risk_approved"] = approved
        output["max_position_usdt"] = float(self.limits.max_position_usdt)
        output["max_leverage"] = float(self.limits.max_leverage)
        output["runtime_mode"] = runtime_mode
        output["final_decision"] = "ALLOW" if approved else "BLOCK"
        if reasons:
            output["risk_reasons"] = reasons

        return SignalRiskDecision(
            approved=approved,
            status="approved" if approved else "blocked",
            reasons=reasons,
            signal=output,
            created_at=iso_now(),
        )

    def approve_many(self, signals: list[dict[str, Any]]) -> list[SignalRiskDecision]:
        if not isinstance(signals, list):
            raise TypeError("signals must be a list of dictionaries")
        return [self.approve(signal) for signal in signals if isinstance(signal, dict)]


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    status: str
    reasons: list[str]
    warnings: list[str]
    metrics: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def load_yaml(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    text = target.read_text(encoding="utf-8")
    if yaml is None:
        return load_simple_yaml(text)
    return yaml.safe_load(text) or {}


def load_simple_yaml(text: str) -> dict[str, Any]:
    lines = [
        raw_line
        for raw_line in text.splitlines()
        if raw_line.strip() and not raw_line.lstrip().startswith("#")
    ]
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]

    for index, raw_line in enumerate(lines):
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError("invalid_yaml_indentation")
        parent = stack[-1][1]

        if stripped.startswith("- "):
            if not isinstance(parent, list):
                raise ValueError("yaml_list_without_parent")
            parent.append(parse_yaml_scalar(stripped[2:].strip()))
            continue

        if ":" not in stripped:
            raise ValueError("yaml_mapping_expected")
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        value_text = raw_value.strip()
        if not isinstance(parent, dict):
            raise ValueError("yaml_mapping_parent_expected")

        if value_text:
            parent[key] = parse_yaml_scalar(value_text)
            continue

        child: dict[str, Any] | list[Any]
        child = [] if next_yaml_child_is_list(lines, index, indent) else {}
        parent[key] = child
        stack.append((indent, child))

    return root


def next_yaml_child_is_list(lines: list[str], index: int, indent: int) -> bool:
    for raw_line in lines[index + 1 :]:
        next_indent = len(raw_line) - len(raw_line.lstrip(" "))
        if next_indent <= indent:
            return False
        return raw_line.strip().startswith("- ")
    return False


def parse_yaml_scalar(value: str) -> Any:
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    lowered = value.strip().lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in {"0", "false", "no", "n", "off", ""}:
        return False
    if lowered in {"null", "none", "~"}:
        return None
    try:
        if any(char in value for char in (".", "e", "E")):
            return float(value)
        return int(value)
    except ValueError:
        return value


def risk_limits_from_config(
    config: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> RiskLimits:
    if not isinstance(config, dict):
        raise ValueError("RiskManager YAML root must be a mapping")

    runtime_mode = str(_lookup(config, "runtime_mode", "paper")).strip().lower()
    unsafe_reasons: list[str] = []
    if runtime_mode not in SAFE_RUNTIME_MODES:
        unsafe_reasons.append(f"runtime_mode_not_allowed:{runtime_mode}")

    for flag_name in (
        "live_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
    ):
        if _config_bool(config, flag_name, default=False):
            unsafe_reasons.append(f"{flag_name}_must_be_false")

    for env_name in (
        "LIVE_ENABLED",
        "ORDER_SUBMISSION_ENABLED",
        "REAL_ORDER_SUBMISSION_ENABLED",
    ):
        if env_enabled(env_name):
            unsafe_reasons.append(f"{env_name}=true")

    if unsafe_reasons:
        location = f" in {source_path}" if source_path else ""
        raise ValueError(
            "Unsafe RiskManager config" + location + ": " + ",".join(unsafe_reasons)
        )

    return RiskLimits(
        runtime_mode=runtime_mode,
        max_position_usdt=float(_lookup(config, "max_position_usdt", 50.0)),
        max_leverage=float(_lookup(config, "max_leverage", 2.0)),
        signal_ttl_seconds=int(_lookup(config, "signal_ttl_seconds", 300)),
        kill_switch_enabled=_config_bool(
            config,
            "kill_switch_enabled",
            default=False,
        ),
        allowed_pairs=tuple(
            str(item) for item in _config_list(config, "allowed_pairs")
        ),
    )


def _lookup(config: dict[str, Any], key: str, default: Any = None) -> Any:
    if key in config:
        return config[key]
    for section_name in (
        "risk_limits",
        "limits",
        "risk",
        "safety",
        "runtime",
        "execution",
    ):
        section = config.get(section_name)
        if isinstance(section, dict) and key in section:
            return section[key]
    return default


def _config_bool(config: dict[str, Any], key: str, default: bool = False) -> bool:
    value = _lookup(config, key, default)
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
        if normalized in {"0", "false", "no", "n", "off", ""}:
            return False
    raise ValueError(f"{key}_must_be_boolean")


def _config_list(config: dict[str, Any], key: str) -> list[Any]:
    value = _lookup(config, key, [])
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [value]
    raise ValueError(f"{key}_must_be_list")


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    try:
        with target.open("r", encoding="utf-8") as handle:
            return json.load(handle) or {}
    except Exception as exc:
        return {"_read_error": str(exc)}


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
    temp.replace(target)


def env_enabled(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in TRUE_VALUES


def parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def active_signals(payload: dict[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or utc_now()
    records = payload.get("signals")
    if records is None and isinstance(payload.get("data"), list):
        records = payload["data"]
    if not isinstance(records, list):
        return []
    output = []
    for record in records:
        if not isinstance(record, dict):
            continue
        valid_until = parse_dt(record.get("valid_until"))
        if valid_until and valid_until < now:
            continue
        output.append(record)
    return output


def resolve_sqlite(candidates: list[str]) -> Path | None:
    for value in candidates:
        path = Path(value)
        if path.exists():
            return path
    return None


def inspect_freqtrade_sqlite(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"exists": False, "open_rows": None, "closed_rows": None, "rows": None}
    result: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        result.update({"open_rows": None, "closed_rows": None, "rows": None})
        return result
    if pd is None:
        result["error"] = "pandas_required"
        return result
    try:
        with sqlite3.connect(str(path)) as connection:
            table_count = pd.read_sql_query("select count(*) as rows from trades", connection)
            open_count = pd.read_sql_query("select count(*) as rows from trades where is_open = 1", connection)
            closed_count = pd.read_sql_query("select count(*) as rows from trades where is_open = 0", connection)
            result.update({"rows": int(table_count.iloc[0]["rows"]), "open_rows": int(open_count.iloc[0]["rows"]), "closed_rows": int(closed_count.iloc[0]["rows"])})
            columns = pd.read_sql_query("pragma table_info(trades)", connection)["name"].tolist()
            profit_col = "close_profit_abs" if "close_profit_abs" in columns else "realized_profit"
            daily = pd.read_sql_query(f"select close_date, coalesce({profit_col}, 0) as profit from trades where is_open = 0 and close_date is not null", connection)
            if daily.empty:
                result["daily_profit_abs"] = 0.0
                result["max_drawdown_abs"] = 0.0
            else:
                daily["close_date"] = pd.to_datetime(daily["close_date"], errors="coerce", utc=True)
                today = utc_now().date()
                result["daily_profit_abs"] = float(daily.loc[daily["close_date"].dt.date == today, "profit"].sum())
                curve = daily["profit"].fillna(0).cumsum()
                result["max_drawdown_abs"] = float((curve.cummax() - curve).max()) if len(curve) else 0.0
    except Exception as exc:
        result["error"] = str(exc)
    return result


def inspect_prediction_age(path: str | Path, max_age_minutes: int) -> dict[str, Any]:
    target = Path(path)
    result: dict[str, Any] = {"path": str(target), "exists": target.exists()}
    if not target.exists():
        result["stale"] = True
        result["age_minutes"] = None
        return result
    modified = datetime.fromtimestamp(target.stat().st_mtime, tz=timezone.utc)
    age = (utc_now() - modified).total_seconds() / 60
    result.update({"modified_at": modified.isoformat(), "age_minutes": float(age), "stale": age > max_age_minutes})
    return result


def load_kill_switch(path: str | Path = "data/runtime/kill_switch.json") -> dict[str, Any]:
    payload = read_json(path)
    if not payload:
        payload = {"enabled": False, "reason": "default_created", "runtime_mode": "paper", "updated_at": iso_now()}
        write_json(path, payload)
    return payload


def set_kill_switch(enabled: bool, reason: str, path: str | Path = "data/runtime/kill_switch.json") -> dict[str, Any]:
    current = load_kill_switch(path)
    payload = {**current, "enabled": bool(enabled), "reason": reason, "runtime_mode": "paper", "updated_at": iso_now()}
    write_json(path, payload)
    return payload


def evaluate_risk(config_path: str | Path = "config/risk_manager.yml", signals_path: str | Path | None = None, write_report: bool = True) -> RiskDecision:
    config = load_yaml(config_path)
    paths = config.get("paths", {})
    limits = config.get("limits", {})
    safety = config.get("safety", {})
    reasons: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, Any] = {}

    runtime_mode = os.getenv("SMARTCRYPTO_RUNTIME_MODE", config.get("runtime_mode", "paper"))
    metrics["runtime_mode"] = runtime_mode
    if runtime_mode != safety.get("allowed_runtime_mode", "paper"):
        reasons.append(f"runtime_mode_not_allowed:{runtime_mode}")

    if safety.get("block_live_enabled", True) and env_enabled("LIVE_ENABLED"):
        reasons.append("LIVE_ENABLED=true")
    if safety.get("block_order_submission_enabled", True) and env_enabled("ORDER_SUBMISSION_ENABLED"):
        reasons.append("ORDER_SUBMISSION_ENABLED=true")
    if safety.get("block_real_order_submission_enabled", True) and env_enabled("REAL_ORDER_SUBMISSION_ENABLED"):
        reasons.append("REAL_ORDER_SUBMISSION_ENABLED=true")

    kill_path = paths.get("kill_switch", "data/runtime/kill_switch.json")
    kill = load_kill_switch(kill_path)
    metrics["kill_switch"] = kill
    if kill.get("enabled") is True:
        reasons.append(f"kill_switch_enabled:{kill.get('reason', 'no_reason')}")

    signal_file = Path(signals_path or paths.get("pinned_signals") or paths.get("primary_signals", "data/freqtrade_signals.json"))
    signal_payload = read_json(signal_file)
    signals = active_signals(signal_payload)
    metrics["signals"] = {"path": str(signal_file), "exists": signal_file.exists(), "active_count": len(signals), "pairs": sorted({str(item.get("pair") or item.get("symbol")) for item in signals if item.get("pair") or item.get("symbol")})}
    if limits.get("require_non_empty_signals", True) and len(signals) == 0:
        reasons.append("signal_empty_or_expired")

    prediction = inspect_prediction_age(paths.get("qlib_predictions", "data/predictions/latest_qlib_predictions.parquet"), int(limits.get("max_prediction_age_minutes", 90)))
    metrics["prediction"] = prediction
    if prediction.get("stale"):
        reasons.append("qlib_prediction_stale_or_missing")

    sqlite_status = inspect_freqtrade_sqlite(resolve_sqlite(paths.get("sqlite_candidates", [])))
    metrics["freqtrade_sqlite"] = sqlite_status
    if limits.get("require_sqlite_consistent", True) and sqlite_status.get("exists") is not True:
        reasons.append("freqtrade_sqlite_missing")
    if sqlite_status.get("error"):
        reasons.append(f"freqtrade_sqlite_error:{sqlite_status['error']}")

    open_rows = sqlite_status.get("open_rows")
    max_open = int(limits.get("max_open_trades", 2))
    if isinstance(open_rows, int) and open_rows > max_open:
        reasons.append(f"max_open_trades_exceeded:{open_rows}>{max_open}")

    daily_profit = float(sqlite_status.get("daily_profit_abs") or 0.0)
    max_daily_loss = float(limits.get("max_daily_loss_usdt", 10.0))
    if daily_profit < -abs(max_daily_loss):
        reasons.append(f"max_daily_loss_exceeded:{daily_profit}")

    dashboard_required = bool(limits.get("require_dashboard_readable", False))
    dashboard_report = Path("data/reports/dashboard_health.json")
    metrics["dashboard"] = {"required": dashboard_required, "health_report_exists": dashboard_report.exists()}
    if dashboard_required and not dashboard_report.exists():
        reasons.append("dashboard_health_missing")

    decision = RiskDecision(approved=not reasons, status="approved" if not reasons else "blocked", reasons=reasons, warnings=warnings, metrics=metrics, created_at=iso_now())
    if write_report:
        write_json(paths.get("report", "data/reports/phase20_risk_report.json"), decision.to_dict())
    return decision


def filter_signal_payload(payload: dict[str, Any], decision: RiskDecision) -> dict[str, Any]:
    if decision.approved:
        payload["risk_status"] = "approved"
        payload["risk_reasons"] = []
        return payload
    output = {**payload, "risk_status": "blocked", "risk_reasons": decision.reasons}
    if isinstance(output.get("signals"), list):
        for item in output["signals"]:
            if isinstance(item, dict):
                item["risk_approved"] = False
                item["risk_reasons"] = decision.reasons
    return output


def main() -> None:
    decision = evaluate_risk()
    print(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2))
    if not decision.approved:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
