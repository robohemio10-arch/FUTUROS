from __future__ import annotations

from typing import Any

from smartcrypto.ops.dashboard_snapshots.build_context import DashboardBuildContext
from smartcrypto.ops.dashboard_snapshots.builder_common import (
    all_source_payloads,
    age_seconds,
    build_snapshot_envelope,
    finite_float,
    first_payload,
    first_value,
    load_page_sources,
    section,
)
from smartcrypto.ops.dashboard_snapshots.contracts import (
    DASHBOARD_INFRASTRUCTURE_SCHEMA_VERSION,
    DashboardPageId,
    DashboardSectionStatus,
)
from smartcrypto.ops.dashboard_snapshots.safe_math import safe_div, safe_quantile, safe_std


REQUIRED_SECTIONS = (
    "status_summary",
    "host",
    "docker",
    "redis",
    "latency",
    "websockets",
    "rate_limits",
    "market_data_health",
    "events",
    "audit",
)


def calculate_market_microstructure(
    best_bid: float,
    best_ask: float,
    best_bid_size: float = 0.0,
    best_ask_size: float = 0.0,
) -> dict[str, float]:
    mid_price = safe_div(best_bid + best_ask, 2.0)
    return {
        "mid_price": mid_price,
        "spread_bps": safe_div(best_ask - best_bid, mid_price) * 10000.0,
        "top_of_book_depth_usdt": (best_bid_size * best_bid) + (best_ask_size * best_ask),
    }


def calculate_latency_metrics(latency_ms: list[float]) -> dict[str, float]:
    return {
        "latency_p50_ms": safe_quantile(latency_ms, 0.50),
        "latency_p90_ms": safe_quantile(latency_ms, 0.90),
        "latency_p99_ms": safe_quantile(latency_ms, 0.99),
        "jitter_ms": safe_std(latency_ms),
    }


def classify_rate_limit(used_weight: float, max_weight: float, error_code: int | None = None) -> str:
    if error_code == 418:
        return DashboardSectionStatus.ERROR.value
    usage = safe_div(used_weight, max_weight) * 100.0
    if usage > 85.0:
        return DashboardSectionStatus.BLOCKED.value
    if usage >= 60.0:
        return DashboardSectionStatus.WARNING.value
    return DashboardSectionStatus.OK.value


def classify_host_resources(cpu_pct: float, ram_pct: float, disk_pct: float) -> str:
    peak = max(cpu_pct, ram_pct, disk_pct)
    if peak > 95.0:
        return DashboardSectionStatus.BLOCKED.value
    if peak > 80.0:
        return DashboardSectionStatus.WARNING.value
    return DashboardSectionStatus.OK.value


def build_infrastructure_snapshot(context: DashboardBuildContext) -> dict[str, Any]:
    sources = load_page_sources(context, DashboardPageId.infrastructure)
    data = all_source_payloads(sources)
    health = first_payload(sources, "system_healthcheck_report")
    paper_runtime = first_payload(sources, "paper_runtime_health_and_freshness_report")
    market = first_payload(sources, "market_data_health_runtime_sources_report")
    latency_values = _numbers(data, ("latency_ms", "response_time_ms", "exchange_latency_ms"))
    bid = _number(first_value(market, ("best_bid", "bid_price")))
    ask = _number(first_value(market, ("best_ask", "ask_price")))
    bid_size = _number(first_value(market, ("best_bid_size", "bid_qty")))
    ask_size = _number(first_value(market, ("best_ask_size", "ask_qty")))
    microstructure = calculate_market_microstructure(bid, ask, bid_size, ask_size)
    source_timestamp = first_value(
        market,
        ("last_candle_timestamp_utc", "generated_at_utc", "last_updated_utc", "timestamp_utc"),
    )
    data_age = age_seconds(context.now_utc, source_timestamp)
    max_age = _number(first_value(data, ("max_data_age_seconds", "max_candle_age_seconds")), 300.0)
    market_status = (
        DashboardSectionStatus.STALE
        if data_age is not None and data_age > max_age
        else DashboardSectionStatus.OK
    )
    used_weight = _number(first_value(data, ("used_weight", "api_weight_used")))
    max_weight = _number(first_value(data, ("max_weight", "api_weight_limit")), 1.0)
    error_code = first_value(data, ("error_code", "http_status"))
    rate_status = classify_rate_limit(used_weight, max_weight, _int_or_none(error_code))
    cpu = _number(first_value(health, ("cpu_pct", "cpu_percent")))
    ram = _number(first_value(health, ("ram_pct", "memory_pct", "memory_percent")))
    disk = _number(first_value(health, ("disk_pct", "disk_percent")))
    host_status = classify_host_resources(cpu, ram, disk)
    ws_timestamp = first_value(data, ("last_ws_message_timestamp", "last_message_utc"))
    ws_age = age_seconds(context.now_utc, ws_timestamp)
    max_ws_age = _number(first_value(data, ("max_ws_age_seconds",)), 60.0)
    stale_ws = ws_age is not None and ws_age > max_ws_age

    paper_runtime_status = _paper_runtime_section_status(paper_runtime)
    container_snapshot = first_value(paper_runtime, ("container_snapshot",), {})
    container_status = str(
        first_value(paper_runtime, ("container_snapshot_status", "docker_services_status"), "disabled")
    ).lower()

    sections = {
        "status_summary": section(
            DashboardSectionStatus.OK,
            component_status=str(first_value(health, ("status", "overall_status"), "unknown")),
        ),
        "paper_runtime_health": section(
            paper_runtime_status,
            paper_runtime_health_status=first_value(paper_runtime, ("paper_runtime_health_status", "status"), "unknown"),
            paper_runtime_alive=first_value(paper_runtime, ("paper_runtime_alive",), False) is True,
            paper_runtime_fresh=first_value(paper_runtime, ("paper_runtime_fresh",), False) is True,
            critical_stale_count=int(_number(first_value(paper_runtime, ("critical_stale_count",), 0))),
            warning_stale_count=int(_number(first_value(paper_runtime, ("warning_stale_count",), 0))),
            stale_sources=first_value(paper_runtime, ("stale_sources",), []),
            container_collection_requested=first_value(
                paper_runtime,
                ("container_collection_requested",),
                False,
            ) is True,
            container_snapshot_status=container_status,
            docker_services_status=first_value(paper_runtime, ("docker_services_status",), "disabled"),
            freqtrade_paper_status=first_value(paper_runtime, ("freqtrade_paper_status",), "unknown"),
            smartcrypto_bot_status=first_value(paper_runtime, ("smartcrypto_bot_status",), "unknown"),
            missing_expected_services=first_value(
                container_snapshot,
                ("missing_expected_services",),
                [],
            ),
            unhealthy_services=first_value(container_snapshot, ("unhealthy_services",), []),
            live_release_allowed=False,
            canary_release_allowed=False,
        ),
        "host": section(host_status, cpu_pct=cpu, ram_pct=ram, disk_pct=disk),
        "docker": section(
            _container_section_status(container_status),
            container_snapshot_status=container_status,
            containers=first_value(container_snapshot, ("containers",), []),
            service_statuses=first_value(container_snapshot, ("service_statuses",), {}),
            expected_services=first_value(container_snapshot, ("expected_services",), []),
            missing_expected_services=first_value(
                container_snapshot,
                ("missing_expected_services",),
                [],
            ),
            unhealthy_services=first_value(container_snapshot, ("unhealthy_services",), []),
        ),
        "redis": section(_source_section_status(sources, "redis_health_snapshot")),
        "latency": section(DashboardSectionStatus.OK, **calculate_latency_metrics(latency_values)),
        "websockets": section(
            DashboardSectionStatus.STALE if stale_ws else DashboardSectionStatus.OK,
            stale_ws=stale_ws,
            last_message_age_seconds=ws_age,
            max_ws_age_seconds=max_ws_age,
        ),
        "rate_limits": section(
            rate_status,
            used_weight=used_weight,
            max_weight=max_weight,
            api_weight_pct=safe_div(used_weight, max_weight) * 100.0,
        ),
        "market_data_health": section(
            market_status,
            data_age_seconds=data_age,
            max_data_age_seconds=max_age,
            **microstructure,
        ),
        "events": section(
            DashboardSectionStatus.OK,
            alert_count=len(first_payload(sources, "alert_outbox") or []),
        ),
        "audit": section(DashboardSectionStatus.OK, dashboard_reads_only=True),
    }
    return build_snapshot_envelope(
        context=context,
        page_id=DashboardPageId.infrastructure,
        schema_version=DASHBOARD_INFRASTRUCTURE_SCHEMA_VERSION,
        sections=sections,
        source_state=sources,
    )


def _source_section_status(sources: dict[str, Any], key: str) -> str:
    entries = [item for item in sources["inventory"] if key in item["path"]]
    return entries[0]["status"] if entries else DashboardSectionStatus.UNKNOWN.value


def _number(value: Any, default: float = 0.0) -> float:
    return finite_float(value, default) or 0.0


def _numbers(data: Any, keys: tuple[str, ...]) -> list[float]:
    from smartcrypto.ops.dashboard_snapshots.builder_common import numeric_values

    return numeric_values(data, keys)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _paper_runtime_section_status(payload: Any) -> DashboardSectionStatus:
    if not payload:
        return DashboardSectionStatus.UNKNOWN
    status = str(first_value(payload, ("status", "paper_runtime_health_status"), "unknown")).lower()
    if status in {"blocked", "critical", "failed", "error"}:
        return DashboardSectionStatus.BLOCKED
    if status in {"degraded", "warning", "stale"}:
        return DashboardSectionStatus.WARNING
    if status == "ok":
        return DashboardSectionStatus.OK
    return DashboardSectionStatus.UNKNOWN


def _container_section_status(status: str) -> DashboardSectionStatus:
    if status == "ok":
        return DashboardSectionStatus.OK
    if status == "blocked":
        return DashboardSectionStatus.BLOCKED
    if status in {"degraded", "unavailable"}:
        return DashboardSectionStatus.WARNING
    return DashboardSectionStatus.UNKNOWN
