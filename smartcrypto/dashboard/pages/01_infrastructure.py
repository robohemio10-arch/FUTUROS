from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from html import escape
from pathlib import Path
from typing import Any

from smartcrypto.dashboard.components.read_only import get_streamlit, render_snapshot_page
from smartcrypto.dashboard.components.runtime_blockers_closeout_evidence import (
    render_runtime_blockers_closeout_evidence,
)
from smartcrypto.dashboard.components.runtime_blockers_operator_pack import (
    render_runtime_blockers_operator_pack,
)
from smartcrypto.dashboard.components.runtime_blockers_remediation import (
    render_runtime_blockers_remediation,
)
from smartcrypto.dashboard.components.runtime_evidence_freshness_remediation_producers import (
    render_runtime_evidence_freshness_remediation_producers,
)
from smartcrypto.dashboard.components.runtime_evidence_panel import render_runtime_evidence_panel
from smartcrypto.dashboard.components.runtime_freshness_governance_closeout_index import (
    render_runtime_freshness_governance_closeout_index,
)
from smartcrypto.dashboard.components.runtime_freshness_post_refresh_evidence_gate import (
    render_runtime_freshness_post_refresh_evidence_gate,
)
from smartcrypto.dashboard.components.runtime_freshness_producer_contracts import (
    render_runtime_freshness_producer_contracts,
)
from smartcrypto.dashboard.components.runtime_freshness_producer_entrypoint_static_safety import (
    render_runtime_freshness_producer_entrypoint_static_safety,
)
from smartcrypto.dashboard.components.runtime_source_health import render_runtime_source_health
from smartcrypto.dashboard.services.page_snapshot_loader import load_page_snapshot
from smartcrypto.dashboard.ui import (
    inject_smart_futuros_command_center_css,
    render_card_grid,
    render_compact_kpi,
    render_depth_preview,
    render_footer_audit_bar,
    render_global_topbar,
    render_grid_channel_preview,
    render_latency_scatter_svg,
    render_mini_bar_stack,
    render_mini_donut_css,
    render_mini_panel_card,
    render_page_title,
    render_sidebar,
    render_sparkline_svg,
    status_to_label,
    worst_status,
)
from smartcrypto.ops.dashboard_snapshots.contracts import DashboardPageId


PAGE_TITLE = "01. Infraestrutura"
PAGE_NUMBER = "01"
PAGE_NAME = "Infraestrutura"
PAGE_SUBTITLE = "Telemetria de infraestrutura, conectividade, fontes e evidências do runtime paper."
ACTIVE_PAGE = "01_infrastructure"
SNAPSHOT_PATH = "data/reports/dashboard_infrastructure_snapshot.json"
REAL_PAPER_SNAPSHOT_PATH = "data/reports/dashboard_real_paper_sources_snapshot.json"
EXPECTED_SCHEMA_VERSION = "dashboard_infrastructure_snapshot_v1"
REQUIRED_SECTIONS = (
    "status_summary",
    "host",
    "docker",
    "redis",
    "latency",
    "websockets",
    "rate_limits",
    "market_data_health",
    "runtime_evidence_integration",
    "runtime_blockers_remediation",
    "runtime_blockers_operator_pack",
    "runtime_blockers_closeout_evidence",
    "runtime_evidence_freshness_remediation_producers",
    "runtime_freshness_producer_contracts",
    "runtime_freshness_producer_entrypoint_static_safety",
    "runtime_freshness_post_refresh_evidence_gate",
    "runtime_freshness_governance_closeout_index",
    "events",
    "runtime_source_health",
    "audit",
)
METRICS = (
    ("Runtime Mode", "status_summary", "component_status"),
    ("Redis Status", "redis", "status"),
    ("WebSocket Status", "websockets", "stale_ws"),
    ("Latency p50", "latency", "latency_p50_ms"),
    ("Latency p90", "latency", "latency_p90_ms"),
    ("Latency p99", "latency", "latency_p99_ms"),
    ("Rate Limit Used", "rate_limits", "api_weight_pct"),
    ("Market Data Age", "market_data_health", "data_age_seconds"),
)

_INSTITUTIONAL_AREAS = (
    ("02", "Portfólio e Risco", "Bloqueios de risco, kill switch e reconciliação.", "portfolio_risk"),
    ("03", "Grid Spot Monitor", "Sinais, canais públicos e estado de mercado.", "grid_monitor"),
    ("04", "Oportunidades", "Arbitragem, lançamentos e scanners read-only.", "opportunity_scanner"),
    ("05", "IA / Qlib Governance", "Model registry, predições e shadow governance.", "ai_governance"),
    ("06", "Controle Ativo", "Controles N1-N4 em dry-run e readiness hard-blocked.", "active_controls"),
    ("07", "Relatórios & TCA", "Backtests, TCA, Monte Carlo e evidência quantitativa.", "quantitative_reports"),
    ("08", "Alertas & Mensageria", "Roteamento e dispatch apenas simulado.", "alerts_messaging"),
)


def render_page(snapshot: dict[str, Any], *, ui: Any | None = None) -> None:
    target_ui = ui or get_streamlit()
    inject_smart_futuros_command_center_css(ui=target_ui)
    render_global_topbar(last_updated=snapshot.get("last_updated_utc"), ui=target_ui)
    render_sidebar(ACTIVE_PAGE, _environment(snapshot), ui=target_ui)
    render_page_title(PAGE_NUMBER, PAGE_NAME, PAGE_SUBTITLE, ui=target_ui)

    _render_visual_command_center(snapshot, ui=target_ui)
    target_ui.markdown(
        '<div class="sfc-readonly-banner">'
        "TABELA CANÔNICA READ-ONLY · contrato institucional preservado."
        "</div>",
        unsafe_allow_html=True,
    )
    render_snapshot_page(
        title=PAGE_TITLE,
        snapshot_path=SNAPSHOT_PATH,
        snapshot=snapshot,
        section_order=REQUIRED_SECTIONS,
        metric_specs=METRICS,
        ui=target_ui,
        render_chrome=False,
    )
    _render_runtime_evidence_stack(snapshot, ui=target_ui)
    render_footer_audit_bar(SNAPSHOT_PATH, ui=target_ui)


def _render_visual_command_center(snapshot: Mapping[str, Any], *, ui: Any) -> None:
    status_summary = _section(snapshot, "status_summary")
    runtime_view = _section(snapshot, "runtime_evidence_view")
    runtime_integration = _section(snapshot, "runtime_evidence_integration")
    safety = _safety_flags(snapshot)

    dashboard_status = _first_text(
        snapshot.get("dashboard_status"),
        status_summary.get("status"),
        status_summary.get("component_status"),
        snapshot.get("status"),
        "UNKNOWN",
    )
    source_health = _first_text(
        snapshot.get("global_source_health_status"),
        snapshot.get("source_health_global_status"),
        runtime_view.get("source_health_global_status"),
        "UNKNOWN",
    )
    runtime_status = _first_text(
        snapshot.get("runtime_evidence_integration_status"),
        runtime_integration.get("runtime_evidence_integration_status"),
        runtime_view.get("runtime_evidence_status"),
        "UNKNOWN",
    )
    readiness_status = _first_text(
        runtime_view.get("readiness_status"),
        runtime_integration.get("readiness_status"),
        snapshot.get("readiness_status"),
        "BLOCKED",
    )
    hero_status = worst_status(dashboard_status, source_health, runtime_status, readiness_status)

    ui.markdown(
        _hero_html(
            snapshot=snapshot,
            status=hero_status,
            dashboard_status=dashboard_status,
            source_health=source_health,
            runtime_status=runtime_status,
            readiness_status=readiness_status,
            safety=safety,
        ),
        unsafe_allow_html=True,
    )

    real_paper_snapshot = _load_real_paper_snapshot()
    ui.markdown(_telemetry_strip_html(snapshot), unsafe_allow_html=True)
    ui.markdown(_real_paper_wallboard_html(real_paper_snapshot), unsafe_allow_html=True)
    ui.markdown(_main_grid_html(snapshot), unsafe_allow_html=True)


def _hero_html(
    *,
    snapshot: Mapping[str, Any],
    status: str,
    dashboard_status: str,
    source_health: str,
    runtime_status: str,
    readiness_status: str,
    safety: Mapping[str, Any],
) -> str:
    runtime_mode = _first_text(snapshot.get("runtime_mode"), "paper")
    updated = _first_text(snapshot.get("last_updated_utc"), snapshot.get("generated_at_utc"), "UNKNOWN")
    blocker_count = _count_list(
        snapshot.get("combined_blocking_reasons"),
        snapshot.get("global_blocking_reasons"),
        snapshot.get("runtime_evidence_blocking_reasons"),
    )
    guards = {
        "Runtime": runtime_mode,
        "Dashboard": dashboard_status,
        "Source Health": source_health,
        "Runtime Evidence": runtime_status,
        "Readiness": readiness_status,
        "Blockers": blocker_count,
        "Orders": "disabled" if not _truthy(safety.get("order_submission_enabled")) else "ENABLED",
        "Live": "locked" if not _truthy(safety.get("live_trading_enabled")) else "ENABLED",
    }
    guard_tiles = "".join(
        '<div class="sfc-guard-tile">'
        f"<span>{escape(str(label))}</span><strong>{escape(str(value))}</strong>"
        "</div>"
        for label, value in guards.items()
    )
    return (
        '<section class="sfc-infra-hero">'
        f'<div class="sfc-infra-hero-panel sfc-card-status-{escape(status)}">'
        '<div class="sfc-infra-hero-kicker">Aba 01 · Telemetria Institucional</div>'
        '<div class="sfc-infra-hero-title">Infraestrutura e conexão sob leitura operacional bloqueada</div>'
        '<div class="sfc-infra-hero-subtitle">'
        "Visão snapshot-first. A página consolida saúde de fontes, conectividade pública, "
        "runtime paper, freshness e bloqueios sem acionar produtores, ordens, risco, modelo ou notificações."
        "</div>"
        f'<div class="sfc-card-helper">Última atualização UTC: {escape(updated)}</div>'
        f'<div class="sfc-status-pill sfc-status-{escape(status)}">{escape(status_to_label(status))}</div>'
        "</div>"
        '<div class="sfc-infra-hero-panel">'
        '<div class="sfc-infra-hero-kicker">Guardrails permanentes</div>'
        f'<div class="sfc-infra-guard-grid">{guard_tiles}</div>'
        "</div>"
        "</section>"
    )


def _telemetry_strip_html(snapshot: Mapping[str, Any]) -> str:
    latency = _section(snapshot, "latency")
    websockets = _section(snapshot, "websockets")
    rate_limits = _section(snapshot, "rate_limits")
    market = _section(snapshot, "market_data_health")
    redis = _section(snapshot, "redis")
    docker = _section(snapshot, "docker")
    host = _section(snapshot, "host")

    cards = (
        render_compact_kpi(
            "Redis",
            _first_text(redis.get("status"), "UNKNOWN"),
            helper="bridge/cache",
            status=_status_from(redis),
        ),
        render_compact_kpi(
            "Docker",
            _first_text(docker.get("status"), docker.get("container_status"), "UNKNOWN"),
            helper="paper stack",
            status=_status_from(docker),
        ),
        render_compact_kpi(
            "Host",
            _resource_summary(host),
            helper="CPU/RAM/DISK",
            status=_status_from(host),
        ),
        render_compact_kpi(
            "WS stale",
            _first_text(websockets.get("stale_ws"), "UNKNOWN"),
            helper="market stream",
            status=_bool_bad_status(websockets.get("stale_ws")),
        ),
        render_compact_kpi(
            "p50",
            _format_ms(latency.get("latency_p50_ms")),
            helper="latência",
            status=_status_from(latency),
        ),
        render_compact_kpi(
            "p90",
            _format_ms(latency.get("latency_p90_ms")),
            helper="latência",
            status=_status_from(latency),
        ),
        render_compact_kpi(
            "Rate limit",
            _format_pct(rate_limits.get("api_weight_pct")),
            helper="peso API pública",
            status=_rate_limit_status(rate_limits.get("api_weight_pct")),
        ),
        render_compact_kpi(
            "Market age",
            _format_seconds(market.get("data_age_seconds")),
            helper="freshness",
            status=_status_from(market),
        ),
    )
    return f'<section class="sfc-telemetry-strip">{render_card_grid(cards, css_class="sfc-telemetry-strip")}</section>'


def _main_grid_html(snapshot: Mapping[str, Any]) -> str:
    return (
        '<section class="sfc-aba01-grid">'
        f"{_connectivity_panel(snapshot)}"
        f"{_runtime_panel(snapshot)}"
        f"{_rate_limit_panel(snapshot)}"
        f"{_market_data_panel(snapshot)}"
        f"{_host_panel(snapshot)}"
        f"{_institutional_area_panel(snapshot)}"
        f"{_events_panel(snapshot)}"
        f"{_source_health_panel(snapshot)}"
        "</section>"
    )


def _connectivity_panel(snapshot: Mapping[str, Any]) -> str:
    latency = _section(snapshot, "latency")
    websockets = _section(snapshot, "websockets")
    redis = _section(snapshot, "redis")
    status = worst_status(_status_from(latency), _status_from(websockets), _status_from(redis))
    values = _latency_series(latency)
    kpis = (
        render_compact_kpi("p50", _format_ms(latency.get("latency_p50_ms")), status=_status_from(latency)),
        render_compact_kpi("p90", _format_ms(latency.get("latency_p90_ms")), status=_status_from(latency)),
        render_compact_kpi("p99", _format_ms(latency.get("latency_p99_ms")), status=_status_from(latency)),
        render_compact_kpi("Jitter", _format_ms(latency.get("jitter_ms")), status=_status_from(latency)),
    )
    body = (
        render_card_grid(kpis)
        + render_latency_scatter_svg(values, label="Distribuição visual de latência", status=status)
        + render_grid_channel_preview(status=_status_from(websockets))
    )
    return _panel_html("Latência e conectividade", "REST público, Redis e WebSocket.", status, body, primary=True)


def _runtime_panel(snapshot: Mapping[str, Any]) -> str:
    runtime_view = _section(snapshot, "runtime_evidence_view")
    status = _first_text(
        runtime_view.get("runtime_evidence_status"),
        runtime_view.get("dashboard_status"),
        snapshot.get("runtime_evidence_integration_status"),
        "BLOCKED",
    )
    blockers = runtime_view.get("blocking_evidence_sources") or snapshot.get("runtime_evidence_blocking_reasons") or []
    rows = {
        "Paper alive": _first_text(runtime_view.get("paper_runtime_alive"), "UNKNOWN"),
        "Paper fresh": _first_text(runtime_view.get("paper_runtime_fresh"), "UNKNOWN"),
        "Soak dias": _format_float(runtime_view.get("continuous_valid_soak_days")),
        "Gaps críticos": _first_text(runtime_view.get("critical_gap_count"), "UNKNOWN"),
    }
    body = render_card_grid(
        render_compact_kpi(label, value, status=status)
        for label, value in rows.items()
    )
    body += _status_rows_html(
        ("Readiness", runtime_view.get("readiness_status", "BLOCKED")),
        ("Canary", runtime_view.get("canary_release_allowed", False)),
        ("Live", runtime_view.get("live_release_allowed", False)),
        ("Orders", runtime_view.get("order_submission_enabled", False)),
    )
    body += _list_preview_html("Blockers autoritativos", blockers, empty="Nenhum blocker informado.")
    return _panel_html("Runtime evidence", "Readiness, soak e evidências paper/shadow.", status, body)


def _rate_limit_panel(snapshot: Mapping[str, Any]) -> str:
    rate_limits = _section(snapshot, "rate_limits")
    pct = rate_limits.get("api_weight_pct")
    status = _rate_limit_status(pct)
    body = (
        render_mini_donut_css(pct, label="API weight usado", status=status)
        + _status_rows_html(
            ("Used weight", _first_text(rate_limits.get("used_weight"), "UNKNOWN")),
            ("Max weight", _first_text(rate_limits.get("max_weight"), "UNKNOWN")),
            ("Backoff", _first_text(rate_limits.get("backoff"), "UNKNOWN")),
            ("Ban/418", _first_text(rate_limits.get("ban"), rate_limits.get("http_418"), "UNKNOWN")),
        )
    )
    return _panel_html("Rate limits", "Uso de limite público e backoff.", status, body)


def _market_data_panel(snapshot: Mapping[str, Any]) -> str:
    market = _section(snapshot, "market_data_health")
    status = _status_from(market)
    spread = _first_text(market.get("spread_bps"), "UNKNOWN")
    depth = _first_text(market.get("top_of_book_depth_usdt"), "UNKNOWN")
    body = render_card_grid(
        (
            render_compact_kpi("Status", _first_text(market.get("status"), "UNKNOWN"), status=status),
            render_compact_kpi("Data age", _format_seconds(market.get("data_age_seconds")), status=status),
            render_compact_kpi("Spread bps", spread, status=status),
            render_compact_kpi("Depth USDT", depth, status=status),
        )
    )
    body += render_depth_preview(status=status)
    return _panel_html("Market data health", "Freshness, spread e top-of-book público.", status, body)


def _host_panel(snapshot: Mapping[str, Any]) -> str:
    host = _section(snapshot, "host")
    docker = _section(snapshot, "docker")
    status = worst_status(_status_from(host), _status_from(docker))
    resource_values = {
        "CPU": _numeric_or_zero(host.get("cpu_pct")),
        "RAM": _numeric_or_zero(host.get("memory_pct")),
        "DISK": _numeric_or_zero(host.get("disk_pct")),
    }
    body = render_mini_bar_stack(resource_values, label="Recursos do host", status=status)
    body += _status_rows_html(
        ("Host status", _first_text(host.get("status"), "UNKNOWN")),
        ("Docker status", _first_text(docker.get("status"), docker.get("container_status"), "UNKNOWN")),
        ("Containers", _first_text(docker.get("container_count"), docker.get("containers_total"), "UNKNOWN")),
        ("Runtime alive", _first_text(docker.get("paper_runtime_alive"), "UNKNOWN")),
    )
    return _panel_html("Host e containers", "Capacidade local e stack Docker paper.", status, body)


def _institutional_area_panel(snapshot: Mapping[str, Any]) -> str:
    build = _section(snapshot, "dashboard_snapshot_build_summary")
    page_matrix = snapshot.get("page_source_matrix") or build.get("page_source_matrix") or []
    by_id = {
        str(row.get("page_id") or row.get("snapshot_id")): row
        for row in page_matrix
        if isinstance(row, Mapping)
    }
    cards = []
    for number, title, description, page_id in _INSTITUTIONAL_AREAS:
        row = by_id.get(page_id, {})
        status = _first_text(row.get("current_page_status"), row.get("status"), "UNKNOWN")
        meta = {
            "blocking": len(row.get("blocking_sources") or ()),
            "degraded": len(row.get("degraded_sources") or ()),
        }
        cards.append(render_mini_panel_card(number, title, status, description=description, meta=meta))
    body = render_card_grid(cards, css_class="sfc-mini-kpi-grid")
    return _panel_html(
        "Resumo institucional das demais abas",
        "Leitura cruzada de status dos snapshots já materializados.",
        "blocked" if any("BLOCKED" in card for card in cards) else "unknown",
        body,
        wide=True,
    )


def _events_panel(snapshot: Mapping[str, Any]) -> str:
    events = snapshot.get("events")
    if isinstance(events, Mapping):
        raw_events = events.get("recent") or events.get("rows") or events.get("items") or []
    else:
        raw_events = events or []
    rows = []
    for event in raw_events[:8] if isinstance(raw_events, Sequence) and not isinstance(raw_events, str) else []:
        if isinstance(event, Mapping):
            rows.append(
                (
                    _first_text(event.get("timestamp_utc"), event.get("created_at_utc"), event.get("ts"), "UNKNOWN"),
                    _first_text(event.get("severity"), event.get("status"), "UNKNOWN"),
                    _first_text(event.get("message"), event.get("event"), event.get("reason"), "sem mensagem"),
                )
            )
    if not rows:
        rows_html = '<div class="sfc-table-empty">Sem eventos recentes materializados no snapshot.</div>'
        status = "unknown"
    else:
        rows_html = "".join(
            '<div class="sfc-status-row">'
            f"<span>{escape(ts)} · {escape(sev)}</span><strong>{escape(msg)}</strong>"
            "</div>"
            for ts, sev, msg in rows
        )
        status = worst_status(*(sev for _, sev, _ in rows))
    return _panel_html("Eventos recentes", "Eventos de infraestrutura e alertas locais.", status, rows_html)


def _source_health_panel(snapshot: Mapping[str, Any]) -> str:
    matrix = snapshot.get("source_health_matrix") or []
    if not isinstance(matrix, Sequence) or isinstance(matrix, str):
        matrix = []
    blocked = [
        row
        for row in matrix
        if isinstance(row, Mapping)
        and str(row.get("health_status") or row.get("status") or "").upper() in {"BLOCKED", "ERROR", "STALE"}
    ][:8]
    if not blocked:
        body = '<div class="sfc-table-empty">Nenhuma fonte bloqueante listada na matriz carregada.</div>'
        status = _first_text(snapshot.get("global_source_health_status"), "unknown")
    else:
        body = "".join(
            '<div class="sfc-status-row">'
            f'<span>{escape(_first_text(row.get("display_name"), row.get("source_id"), "source"))}</span>'
            f'<strong>{escape(_first_text(row.get("status"), row.get("health_status"), "UNKNOWN"))}</strong>'
            "</div>"
            for row in blocked
        )
        status = "blocked"
    return _panel_html("Source health matrix", "Fontes stale/bloqueadas relevantes para a visão operacional.", status, body, wide=True)


def _panel_html(
    title: str,
    subtitle: str,
    status: str,
    body: str,
    *,
    primary: bool = False,
    wide: bool = False,
) -> str:
    classes = ["sfc-aba01-panel", f"sfc-card-status-{escape(status)}"]
    if primary:
        classes.append("sfc-aba01-panel-primary")
    if wide:
        classes.append("sfc-aba01-panel-wide")
    return (
        f'<article class="{" ".join(classes)}">'
        '<div class="sfc-aba01-panel-head">'
        "<div>"
        f'<div class="sfc-aba01-panel-title">{escape(title)}</div>'
        f'<div class="sfc-aba01-panel-subtitle">{escape(subtitle)}</div>'
        "</div>"
        f'<span class="sfc-status-pill sfc-status-{escape(status)}">{escape(status_to_label(status))}</span>'
        "</div>"
        f"{body}</article>"
    )


def _render_runtime_evidence_stack(snapshot: Mapping[str, Any], *, ui: Any) -> None:
    ui.markdown(
        '<div class="sfc-readonly-banner">'
        "DETALHAMENTO CANÔNICO ABA 01 · blocos abaixo continuam snapshot-first/read-only."
        "</div>",
        unsafe_allow_html=True,
    )
    render_runtime_evidence_panel(dict(snapshot), ui=ui)
    render_runtime_blockers_remediation(dict(snapshot), ui=ui)
    render_runtime_blockers_operator_pack(dict(snapshot), ui=ui)
    render_runtime_blockers_closeout_evidence(dict(snapshot), ui=ui)
    render_runtime_evidence_freshness_remediation_producers(dict(snapshot), ui=ui)
    render_runtime_freshness_producer_contracts(dict(snapshot), ui=ui)
    render_runtime_freshness_producer_entrypoint_static_safety(dict(snapshot), ui=ui)
    render_runtime_freshness_post_refresh_evidence_gate(dict(snapshot), ui=ui)
    render_runtime_freshness_governance_closeout_index(dict(snapshot), ui=ui)
    render_runtime_source_health(dict(snapshot), ui=ui)


def _load_real_paper_snapshot(path: str = REAL_PAPER_SNAPSHOT_PATH) -> Mapping[str, Any]:
    candidate = Path(path)
    if not candidate.exists():
        return {
            "status": "MISSING",
            "reason": "real_paper_snapshot_not_found",
            "schema_version": "dashboard_real_paper_sources_snapshot_v1",
            "safety": {
                "dashboard_readonly": True,
                "paper_only": True,
                "shadow_only": True,
                "live_trading_enabled": False,
                "order_submission_enabled": False,
                "real_order_submission_enabled": False,
                "exchange_private_access": False,
                "sends_orders": False,
                "sends_notifications": False,
            },
        }
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "ERROR",
            "reason": f"real_paper_snapshot_load_failed:{type(exc).__name__}",
            "schema_version": "dashboard_real_paper_sources_snapshot_v1",
            "safety": {
                "dashboard_readonly": True,
                "paper_only": True,
                "shadow_only": True,
                "live_trading_enabled": False,
                "order_submission_enabled": False,
                "real_order_submission_enabled": False,
                "exchange_private_access": False,
                "sends_orders": False,
                "sends_notifications": False,
            },
        }
    return payload if isinstance(payload, Mapping) else {"status": "ERROR", "reason": "invalid_real_paper_snapshot_payload"}


def _real_paper_wallboard_html(real_paper: Mapping[str, Any]) -> str:
    status = _first_text(real_paper.get("status"), "UNKNOWN")
    reason = _first_text(real_paper.get("reason"), "UNKNOWN")
    freqtrade = _mapping(real_paper.get("freqtrade"))
    alerts = _mapping(real_paper.get("alerts"))
    qlib = _mapping(real_paper.get("qlib"))
    safety = _mapping(real_paper.get("safety"))

    safety_status = "ok" if _real_paper_safety_ok(safety) else "error"
    panel_status = worst_status(status, safety_status)

    hero = (
        '<article class="sfc-aba01-panel sfc-card-status-' + escape(panel_status) + ' sfc-aba01-panel-wide">'
        '<div class="sfc-aba01-panel-head">'
        '<div>'
        '<div class="sfc-aba01-panel-title">Paper real · execução observada</div>'
        '<div class="sfc-aba01-panel-subtitle">'
        'Snapshot local read-only consolidado de Freqtrade paper, Phase14, Qlib e mensageria. '
        'Não usa exchange privada, não envia ordens e não altera risco.'
        '</div>'
        '</div>'
        '<span class="sfc-status-pill sfc-status-' + escape(panel_status) + '">' + escape(status_to_label(panel_status)) + '</span>'
        '</div>'
        '<div class="sfc-card-helper">Fonte: ' + escape(REAL_PAPER_SNAPSHOT_PATH) + ' · reason=' + escape(reason) + '</div>'
        '</article>'
    )

    trading_cards = render_card_grid(
        (
            render_compact_kpi("Trades", _first_text(freqtrade.get("trades_total"), "UNKNOWN"), helper="total paper", status=status),
            render_compact_kpi("Fechados", _first_text(freqtrade.get("closed_trades"), "UNKNOWN"), helper="closed trades", status=status),
            render_compact_kpi("Abertos", _first_text(freqtrade.get("open_trades"), "UNKNOWN"), helper="open trades", status=status),
            render_compact_kpi("Ordens", _first_text(freqtrade.get("orders_total"), "UNKNOWN"), helper="dry-run/paper", status=status),
            render_compact_kpi("PnL Realizado", _format_money(freqtrade.get("realized_pnl_abs")), helper="USDT aprox.", status=_pnl_status(freqtrade.get("realized_pnl_abs"))),
            render_compact_kpi("Win rate", _format_pct(freqtrade.get("win_rate")), helper="closed trades", status=status),
            render_compact_kpi("Exposição", _format_money(freqtrade.get("open_exposure_usdt")), helper="open exposure", status=status),
            render_compact_kpi("Fees", _format_money(freqtrade.get("fees_total")), helper="custos", status=status),
        ),
        css_class="sfc-mini-kpi-grid",
    )

    qlib_cards = render_card_grid(
        (
            render_compact_kpi("Modelo", _first_text(qlib.get("model_version"), "UNKNOWN"), helper="Qlib", status=_first_text(qlib.get("status"), status)),
            render_compact_kpi("Predições", _first_text(qlib.get("prediction_rows"), "UNKNOWN"), helper="rows", status=_first_text(qlib.get("status"), status)),
            render_compact_kpi("Sinais", _first_text(qlib.get("signals_count"), "UNKNOWN"), helper="ativos", status=_first_text(qlib.get("status"), status)),
            render_compact_kpi("Input", _first_text(qlib.get("input_data_status"), "UNKNOWN"), helper="freshness", status=_first_text(qlib.get("status"), status)),
        ),
        css_class="sfc-mini-kpi-grid",
    )

    alerts_cards = render_card_grid(
        (
            render_compact_kpi("Eventos", _first_text(alerts.get("events_total"), "UNKNOWN"), helper="detectados", status=status),
            render_compact_kpi("Canais", _first_text(alerts.get("channels_total"), "UNKNOWN"), helper="telegram/ntfy audit", status=status),
            render_compact_kpi("Pendentes", _first_text(alerts.get("pending_total"), "UNKNOWN"), helper="fila", status="ok" if _numeric_or_zero(alerts.get("pending_total")) == 0 else "warning"),
            render_compact_kpi("Envio real", "disabled", helper="dashboard read-only", status=safety_status),
        ),
        css_class="sfc-mini-kpi-grid",
    )

    safety_rows = _status_rows_html(
        ("dashboard_readonly", safety.get("dashboard_readonly")),
        ("paper_only", safety.get("paper_only")),
        ("shadow_only", safety.get("shadow_only")),
        ("live_trading_enabled", safety.get("live_trading_enabled")),
        ("order_submission_enabled", safety.get("order_submission_enabled")),
        ("exchange_private_access", safety.get("exchange_private_access")),
        ("sends_orders", safety.get("sends_orders")),
        ("sends_notifications", safety.get("sends_notifications")),
    )

    return (
        '<section class="sfc-aba01-real-paper-wallboard">'
        + hero
        + _panel_html("Freqtrade paper real", "Trades, ordens, PnL e exposição a partir do SQLite snapshot.", status, trading_cards, wide=True)
        + _panel_html("Qlib e sinais ativos", "Predições e sinais paper materializados pelo pipeline.", _first_text(qlib.get("status"), status), qlib_cards)
        + _panel_html("Mensageria observada", "Eventos detectados e canais auditados sem envio pelo dashboard.", status, alerts_cards)
        + _panel_html("Safety do snapshot real paper", "Flags hard-blocked preservadas pela fonte intermediária.", safety_status, safety_rows)
        + "</section>"
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _real_paper_safety_ok(safety: Mapping[str, Any]) -> bool:
    required_true = ("dashboard_readonly", "paper_only", "shadow_only")
    required_false = (
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "sends_notifications",
    )
    return all(_truthy(safety.get(key)) for key in required_true) and not any(
        _truthy(safety.get(key)) for key in required_false
    )


def _pnl_status(value: Any) -> str:
    numeric = _numeric_or_none(value)
    if numeric is None:
        return "unknown"
    return "ok" if numeric >= 0 else "warning"


def _format_money(value: Any) -> str:
    numeric = _numeric_or_none(value)
    return "UNKNOWN" if numeric is None else f"{numeric:,.2f}"

def _environment(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "account": "PAPER / SHADOW",
        "environment": snapshot.get("runtime_mode", "paper"),
        "dashboard_version": "aba01-visual-v1",
        "snapshot": SNAPSHOT_PATH,
        "data_source": "read-only snapshot",
    }


def render_missing_snapshot(reason: str, *, ui: Any | None = None) -> None:
    target_ui = ui or get_streamlit()
    target_ui.info(f"UNKNOWN: {reason}")


def main(project_root: str | Path = ".") -> None:
    ui = get_streamlit()
    ui.set_page_config(page_title=PAGE_TITLE, layout="wide")
    render_page(load_page_snapshot(DashboardPageId.infrastructure, project_root=project_root), ui=ui)


def _section(snapshot: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = snapshot.get(key)
    return value if isinstance(value, Mapping) else {}


def _safety_flags(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("safety_flags", "safety", "audit"):
        value = snapshot.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def _status_from(section: Mapping[str, Any]) -> str:
    return _first_text(
        section.get("status"),
        section.get("health_status"),
        section.get("component_status"),
        section.get("freshness_status"),
        "UNKNOWN",
    )


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return str(value)
    return "UNKNOWN"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "enabled", "on"}


def _bool_bad_status(value: Any) -> str:
    if value is None:
        return "unknown"
    return "error" if _truthy(value) else "ok"


def _rate_limit_status(value: Any) -> str:
    pct = _numeric_or_zero(value)
    if pct > 85:
        return "error"
    if pct >= 60:
        return "warning"
    if pct > 0:
        return "ok"
    return "unknown"


def _resource_summary(host: Mapping[str, Any]) -> str:
    values = [
        _numeric_or_none(host.get("cpu_pct")),
        _numeric_or_none(host.get("memory_pct")),
        _numeric_or_none(host.get("disk_pct")),
    ]
    if all(value is None for value in values):
        return "UNKNOWN"
    return f"{max(value or 0.0 for value in values):.0f}%"


def _count_list(*values: Any) -> int:
    total = 0
    for value in values:
        if isinstance(value, Sequence) and not isinstance(value, str):
            total += len(value)
    return total


def _numeric_or_zero(value: Any) -> float:
    candidate = _numeric_or_none(value)
    return 0.0 if candidate is None else candidate


def _numeric_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_ms(value: Any) -> str:
    numeric = _numeric_or_none(value)
    return "UNKNOWN" if numeric is None else f"{numeric:.1f} ms"


def _format_pct(value: Any) -> str:
    numeric = _numeric_or_none(value)
    return "UNKNOWN" if numeric is None else f"{numeric:.1f}%"


def _format_seconds(value: Any) -> str:
    numeric = _numeric_or_none(value)
    return "UNKNOWN" if numeric is None else f"{numeric:.0f}s"


def _format_float(value: Any) -> str:
    numeric = _numeric_or_none(value)
    return "UNKNOWN" if numeric is None else f"{numeric:.3f}"


def _latency_series(latency: Mapping[str, Any]) -> list[float]:
    for key in ("series", "latency_ms", "samples", "values"):
        raw = latency.get(key)
        if isinstance(raw, Sequence) and not isinstance(raw, str):
            values = [_numeric_or_none(item) for item in raw]
            return [value for value in values if value is not None]
    return [
        _numeric_or_zero(latency.get("latency_p50_ms")),
        _numeric_or_zero(latency.get("latency_p90_ms")),
        _numeric_or_zero(latency.get("latency_p99_ms")),
    ]


def _status_rows_html(*rows: tuple[str, Any]) -> str:
    return "".join(
        '<div class="sfc-status-row">'
        f"<span>{escape(label)}</span><strong>{escape(_first_text(value))}</strong>"
        "</div>"
        for label, value in rows
    )


def _list_preview_html(title: str, values: Any, *, empty: str) -> str:
    if not isinstance(values, Sequence) or isinstance(values, str) or not values:
        return f'<div class="sfc-table-empty">{escape(empty)}</div>'
    rows = "".join(
        '<div class="sfc-status-row">'
        f"<span>{escape(str(idx).zfill(2))}</span><strong>{escape(str(value))}</strong>"
        "</div>"
        for idx, value in enumerate(values[:8], start=1)
    )
    return (
        f'<div class="sfc-aba01-panel-subtitle">{escape(title)}</div>'
        f"{rows}"
    )


if __name__ == "__main__":
    main()
