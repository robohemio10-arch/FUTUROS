"""Fail-closed B06 input loading and path validation."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .contracts import (
    ALLOWED_REPORT_ROOT,
    CONFIG_SCHEMA_VERSION,
    DEFAULT_CONFIG_PATH,
    EVIDENCE_SCHEMA_VERSION,
    MANDATORY_SOAK_METRICS,
    REQUIRED_CHAOS_SCENARIOS,
    REQUIRED_TESTNET_STAGES,
    mapping,
)


def resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    """Resolve a path against the project root without creating it."""

    path = Path(value) if value is not None else default
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def finite(value: Any) -> float | None:
    """Return a finite float or ``None`` for invalid evidence."""

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def finite_or(value: Any, default: float) -> float:
    parsed = finite(value)
    return parsed if parsed is not None else default


def positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def rounded(value: float | None) -> float | None:
    return round(value, 10) if value is not None and math.isfinite(value) else None


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def read_json(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.is_file():
        return {}, f"json_file_missing:{path.name}"
    if path.is_symlink():
        return {}, f"json_file_symlink_forbidden:{path.name}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"json_file_invalid:{path.name}:{exc.__class__.__name__}"
    if not isinstance(payload, Mapping):
        return {}, f"json_root_must_be_object:{path.name}"
    return dict(payload), None


def _validate_config_bounds(config: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    paper_ab = mapping(config.get("paper_ab"))
    testnet = mapping(config.get("testnet_e2e"))
    chaos = mapping(config.get("chaos"))
    capacity = mapping(config.get("capacity"))
    soak = mapping(config.get("soak"))

    if positive_int(paper_ab.get("minimum_trades_per_strategy"), 0) < 1:
        errors.append("config_minimum_trades_invalid")
    if positive_int(paper_ab.get("minimum_stability_periods"), 0) < 2:
        errors.append("config_minimum_stability_periods_invalid")
    positive_period_ratio = finite(
        paper_ab.get("minimum_positive_period_ratio")
    )
    if (
        positive_period_ratio is None
        or not 0.0 <= positive_period_ratio <= 1.0
    ):
        errors.append("config_minimum_positive_period_ratio_invalid")
    for field in (
        "minimum_expectancy_delta",
        "minimum_profit_factor_delta",
        "maximum_drawdown_regression_ratio",
        "maximum_total_cost_bps",
    ):
        value = finite(paper_ab.get(field))
        if value is None or value < 0:
            errors.append(f"config_paper_ab_numeric_invalid:{field}")

    if positive_int(testnet.get("minimum_runs"), 0) < 3:
        errors.append("config_minimum_testnet_runs_below_three")
    recovery_seconds = finite(chaos.get("maximum_recovery_seconds"))
    if recovery_seconds is None or recovery_seconds <= 0:
        errors.append("config_maximum_recovery_seconds_invalid")

    participation = finite(capacity.get("maximum_participation_ratio"))
    if participation is None or not 0 < participation <= 1:
        errors.append("config_maximum_participation_ratio_invalid")
    for field in (
        "maximum_total_execution_cost_bps",
        "maximum_leverage",
        "minimum_liquidation_buffer_pct",
    ):
        value = finite(capacity.get(field))
        if value is None or value <= 0:
            errors.append(f"config_capacity_numeric_invalid:{field}")
    if positive_int(capacity.get("minimum_observations_per_symbol"), 0) < 1:
        errors.append("config_minimum_capacity_observations_invalid")

    if positive_int(soak.get("required_days"), 0) < 30:
        errors.append("config_soak_required_days_below_thirty")
    return errors


def load_config(
    root: Path,
    path_value: str | Path | None,
    payload: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], str, list[str]]:
    if payload is not None:
        config = dict(payload)
        source = "in_memory"
    else:
        path = resolve(root, path_value, DEFAULT_CONFIG_PATH)
        source = str(path)
        if not _under_root(path, root):
            return {}, source, ["config_path_outside_project_root"]
        config, error = read_json(path)
        if error:
            return {}, source, [error]

    errors: list[str] = []
    if config.get("schema_version") != CONFIG_SCHEMA_VERSION:
        errors.append("config_schema_version_invalid")
    for section in (
        "paper_ab",
        "testnet_e2e",
        "chaos",
        "capacity",
        "soak",
    ):
        if not isinstance(config.get(section), Mapping):
            errors.append(f"config_section_missing:{section}")

    testnet = mapping(config.get("testnet_e2e"))
    chaos = mapping(config.get("chaos"))
    capacity = mapping(config.get("capacity"))
    soak = mapping(config.get("soak"))
    stages = {str(item) for item in testnet.get("required_stages") or []}
    scenarios = {str(item) for item in chaos.get("required_scenarios") or []}
    symbols = {
        str(item).upper()
        for item in capacity.get("required_symbols") or []
    }
    soak_metrics = {
        str(item)
        for item in soak.get("required_metrics") or []
    }
    errors.extend(
        f"config_missing_testnet_stage:{item}"
        for item in sorted(set(REQUIRED_TESTNET_STAGES) - stages)
    )
    errors.extend(
        f"config_missing_chaos_scenario:{item}"
        for item in sorted(set(REQUIRED_CHAOS_SCENARIOS) - scenarios)
    )
    errors.extend(
        f"config_missing_capacity_symbol:{item}"
        for item in sorted({"BTCUSDT", "ETHUSDT"} - symbols)
    )
    errors.extend(
        f"config_missing_soak_metric:{item}"
        for item in sorted(set(MANDATORY_SOAK_METRICS) - soak_metrics)
    )
    errors.extend(_validate_config_bounds(config))
    return config, source, sorted(set(errors))


def load_evidence(
    root: Path,
    path_value: str | Path | None,
    payload: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], str | None, list[str]]:
    if payload is not None:
        evidence = dict(payload)
        source: str | None = "in_memory"
    elif path_value is not None:
        path = resolve(root, path_value, Path("."))
        source = str(path)
        if not _under_root(path, root):
            return {}, source, ["evidence_path_outside_project_root"]
        evidence, error = read_json(path)
        if error:
            return {}, source, [error]
    else:
        return {}, None, ["evidence_required"]

    errors = (
        []
        if evidence.get("schema_version") == EVIDENCE_SCHEMA_VERSION
        else ["evidence_schema_version_invalid"]
    )
    return evidence, source, errors


def report_path_errors(
    root: Path,
    json_path: Path,
    markdown_path: Path,
) -> list[str]:
    allowed = (root / ALLOWED_REPORT_ROOT).resolve()
    errors: list[str] = []
    targets = (
        (json_path, ".json", "output_json"),
        (markdown_path, ".md", "output_markdown"),
    )
    for path, suffix, name in targets:
        try:
            path.resolve().relative_to(allowed)
        except ValueError:
            errors.append(f"{name}_outside_data_reports")
        if path.suffix.lower() != suffix:
            errors.append(f"{name}_extension_invalid")
        if path.is_symlink():
            errors.append(f"{name}_symlink_forbidden")
    return sorted(set(errors))
