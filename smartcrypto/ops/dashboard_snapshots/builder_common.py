from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.ops.dashboard_snapshots.build_context import DashboardBuildContext
from smartcrypto.ops.dashboard_snapshots.contracts import (
    DashboardAuditContract,
    DashboardPageId,
    DashboardSectionStatus,
    SourceKind,
)
from smartcrypto.ops.dashboard_snapshots.file_loader import load_dashboard_file
from smartcrypto.ops.dashboard_snapshots.source_catalog import sources_for_page
from smartcrypto.ops.dashboard_snapshots.status import merge_section_statuses


SAFETY_FLAGS = {
    "paper_only": True,
    "shadow_only": True,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "changes_active_signals": False,
    "uses_private_exchange": False,
    "uses_ccxt": False,
}


def iso_utc(value: datetime) -> str:
    current = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def section(
    status: DashboardSectionStatus | str = DashboardSectionStatus.OK,
    reason: str = "ok",
    /,
    **values: Any,
) -> dict[str, Any]:
    normalized = status if isinstance(status, DashboardSectionStatus) else DashboardSectionStatus(status)
    return {"status": normalized.value, "reason": reason, **json_safe(values)}


def load_page_sources(
    context: DashboardBuildContext,
    page_id: DashboardPageId,
) -> dict[str, Any]:
    inventory: list[dict[str, Any]] = []
    payloads: dict[str, list[Any]] = {}
    missing_required: list[str] = []
    missing_optional: list[str] = []
    future_pending: list[str] = []
    errors: list[str] = []

    for source in sources_for_page(page_id):
        if source.source_kind is SourceKind.GENERATED_BY_THIS_BRANCH:
            continue
        matches = resolve_source_paths(context.project_root, source.path)
        if not matches:
            status = missing_status(source.source_kind)
            inventory.append(
                {
                    **source.to_dict(),
                    "status": status.value,
                    "exists": False,
                    "resolved_paths": [],
                    "error": "file_not_found",
                }
            )
            if source.source_kind is SourceKind.REQUIRED_EXISTING_SOURCE:
                missing_required.append(source.path)
            elif source.source_kind is SourceKind.OPTIONAL_EXISTING_SOURCE:
                missing_optional.append(source.path)
            elif source.source_kind is SourceKind.FUTURE_SOURCE:
                future_pending.append(source.path)
            continue

        results = [load_dashboard_file(path, source.source_kind) for path in matches]
        key = source_key(source.path)
        payloads.setdefault(key, []).extend(result.data for result in results if result.data is not None)
        result_status = merge_section_statuses(result.status for result in results)
        result_errors = [result.error for result in results if result.error]
        if result_status is DashboardSectionStatus.ERROR:
            errors.extend(f"{source.path}:{error}" for error in result_errors)
        inventory.append(
            {
                **source.to_dict(),
                "status": result_status.value,
                "exists": any(result.exists for result in results),
                "resolved_paths": [path_to_posix(path, context.project_root) for path in matches],
                "error": ";".join(result_errors) or None,
            }
        )

    return {
        "inventory": inventory,
        "payloads": payloads,
        "missing_required_sources": sorted(set(missing_required)),
        "missing_optional_sources": sorted(set(missing_optional)),
        "future_sources_pending": sorted(set(future_pending)),
        "errors": sorted(set(errors)),
    }


def build_snapshot_envelope(
    *,
    context: DashboardBuildContext,
    page_id: DashboardPageId,
    schema_version: str,
    sections: Mapping[str, Any],
    source_state: Mapping[str, Any],
) -> dict[str, Any]:
    missing_required = list(source_state.get("missing_required_sources", []))
    missing_optional = list(source_state.get("missing_optional_sources", []))
    future_pending = list(source_state.get("future_sources_pending", []))
    errors = list(source_state.get("errors", []))
    section_statuses = [
        value.get("status", DashboardSectionStatus.UNKNOWN.value)
        for value in sections.values()
        if isinstance(value, Mapping)
    ]
    source_overall = source_overall_status(
        missing_required=missing_required,
        missing_optional=missing_optional,
        errors=errors,
    )
    overall = merge_section_statuses([*section_statuses, source_overall])
    audit = DashboardAuditContract(snapshot_source=page_id.value).to_dict()
    return {
        "schema_version": schema_version,
        "runtime_mode": context.runtime_mode.value,
        "dashboard_readonly": True,
        "paper_only": True,
        "shadow_only": True,
        "live_locked": True,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "last_updated_utc": iso_utc(context.now_utc),
        "page_id": page_id.value,
        "status_summary": {
            "status": overall.value,
            "strict": context.strict,
            "missing_required_sources_count": len(missing_required),
            "missing_optional_sources_count": len(missing_optional),
            "future_sources_pending_count": len(future_pending),
            "errors_count": len(errors),
        },
        "sections": json_safe(dict(sections)),
        "source_inventory": json_safe(source_state.get("inventory", [])),
        "missing_required_sources": missing_required,
        "missing_optional_sources": missing_optional,
        "future_sources_pending": future_pending,
        "errors": errors,
        "safety": dict(SAFETY_FLAGS),
        "audit": audit,
    }


def source_overall_status(
    *,
    missing_required: Iterable[str],
    missing_optional: Iterable[str],
    errors: Iterable[str],
) -> DashboardSectionStatus:
    if list(errors):
        return DashboardSectionStatus.ERROR
    if list(missing_required):
        return DashboardSectionStatus.MISSING_REQUIRED
    if list(missing_optional):
        return DashboardSectionStatus.MISSING_OPTIONAL
    return DashboardSectionStatus.OK


def payloads(source_state: Mapping[str, Any], key: str) -> list[Any]:
    values = source_state.get("payloads", {}).get(key, [])
    return list(values) if isinstance(values, list) else []


def first_payload(source_state: Mapping[str, Any], key: str) -> Any:
    values = payloads(source_state, key)
    return values[0] if values else {}


def all_source_payloads(source_state: Mapping[str, Any]) -> list[Any]:
    output: list[Any] = []
    mapping = source_state.get("payloads", {})
    if isinstance(mapping, Mapping):
        for values in mapping.values():
            if isinstance(values, list):
                output.extend(values)
    return output


def first_value(data: Any, keys: Iterable[str], default: Any = None) -> Any:
    wanted = tuple(keys)
    if isinstance(data, Mapping):
        for key in wanted:
            if key in data and data[key] is not None:
                return data[key]
        for value in data.values():
            found = first_value(value, wanted, default=None)
            if found is not None:
                return found
    elif isinstance(data, list | tuple):
        for value in data:
            found = first_value(value, wanted, default=None)
            if found is not None:
                return found
    return default


def numeric_values(data: Any, keys: Iterable[str]) -> list[float]:
    wanted = set(keys)
    output: list[float] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in wanted:
                    number = finite_float(child)
                    if number is not None:
                        output.append(number)
                visit(child)
        elif isinstance(value, list | tuple):
            for child in value:
                visit(child)

    visit(data)
    return output


def records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [dict(item) for item in data if isinstance(item, Mapping)]
    if isinstance(data, Mapping):
        for key in ("rows", "records", "trades", "events", "alerts", "signals", "decisions", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, Mapping)]
        return [dict(data)]
    if hasattr(data, "to_dict"):
        try:
            converted = data.to_dict(orient="records")
        except TypeError:
            converted = data.to_dict()
        return records(converted)
    return []


def finite_float(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on", "active", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "inactive", "disabled"}:
        return False
    return default


def parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_seconds(now: datetime, timestamp: Any) -> float | None:
    parsed = parse_timestamp(timestamp)
    if parsed is None:
        return None
    current = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return max((current.astimezone(timezone.utc) - parsed).total_seconds(), 0.0)


def resolve_source_paths(project_root: Path, pattern: str) -> list[Path]:
    normalized = pattern.replace("\\", "/")
    if any(character in normalized for character in "*?["):
        return sorted(path for path in project_root.glob(normalized) if path.is_file())
    target = project_root / Path(normalized)
    return [target] if target.is_file() else []


def source_key(path: str) -> str:
    name = Path(path.replace("*", "phase")).name
    for suffix in (".jsonl", ".json", ".parquet", ".csv", ".sqlite"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem


def missing_status(source_kind: SourceKind) -> DashboardSectionStatus:
    if source_kind is SourceKind.REQUIRED_EXISTING_SOURCE:
        return DashboardSectionStatus.MISSING_REQUIRED
    if source_kind is SourceKind.OPTIONAL_EXISTING_SOURCE:
        return DashboardSectionStatus.MISSING_OPTIONAL
    return DashboardSectionStatus.UNKNOWN


def path_to_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        return iso_utc(value)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        return {str(key): json_safe(child) for key, child in value.items()}
    if isinstance(value, Iterable) and not isinstance(value, bytes | bytearray):
        return [json_safe(child) for child in value]
    if hasattr(value, "item"):
        try:
            return json_safe(value.item())
        except (TypeError, ValueError):
            pass
    if hasattr(value, "to_dict"):
        return json_safe(value.to_dict())
    return str(value)
