from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from smartcrypto.dashboard.security.dashboard_readonly_guard import (
    assert_dashboard_readonly,
    build_readonly_audit_footer,
    get_global_banners,
)

from .snapshot_cards import MetricSpec, render_metric_cards, render_snapshot_header
from .snapshot_tables import render_section_details, render_section_status_table


READONLY_FOOTER_LINES = (
    "Dashboard read-only",
    "Sem ccxt",
    "Sem create_order",
    "Sem OrderManager direto",
    "Sem live trading",
    "Sem private exchange read",
    "Sem alteração de risco/modelo/active signals",
)


def get_streamlit() -> Any:
    import streamlit

    return streamlit


def render_readonly_banner(*, ui: Any) -> None:
    ui.warning("PAPER / SHADOW ONLY | SMART FUTUROS Command Center em modo read-only")


def render_global_safety_badges(*, ui: Any) -> None:
    ui.caption(" | ".join(get_global_banners()))


def render_unknown_state(message: str, *, ui: Any) -> None:
    ui.info(f"UNKNOWN: {message}")


def render_missing_snapshot_state(snapshot_path: str, *, ui: Any) -> None:
    render_unknown_state(
        f"Snapshot ausente ou inválido: {snapshot_path}. Execute o pipeline externo de snapshots.",
        ui=ui,
    )


def render_disabled_control_stub(label: str, reason: str, *, ui: Any) -> None:
    ui.info(f"{label}: disabled ({reason})")


def render_audit_footer(audit: Any, *, ui: Any) -> None:
    safe_audit = dict(audit) if isinstance(audit, Mapping) else build_readonly_audit_footer()
    ui.divider()
    ui.caption(" | ".join(READONLY_FOOTER_LINES))
    with ui.expander("Audit read-only", expanded=False):
        ui.json(safe_audit)


def render_snapshot_page(
    *,
    title: str,
    snapshot_path: str,
    snapshot: Mapping[str, Any],
    section_order: Sequence[str],
    metric_specs: Sequence[MetricSpec] = (),
    ui: Any | None = None,
) -> None:
    target_ui = ui or get_streamlit()
    assert_dashboard_readonly(snapshot)
    render_readonly_banner(ui=target_ui)
    render_global_safety_badges(ui=target_ui)
    render_snapshot_header(title, snapshot, ui=target_ui)
    if str(snapshot.get("status", "")).upper() == "UNKNOWN":
        render_missing_snapshot_state(snapshot_path, ui=target_ui)
    render_metric_cards(snapshot, metric_specs, ui=target_ui)
    sections = snapshot.get("sections")
    target_ui.subheader("Status das seções")
    render_section_status_table(sections, ui=target_ui, section_order=section_order)
    render_section_details(sections, ui=target_ui, section_order=section_order)
    render_audit_footer(snapshot.get("audit"), ui=target_ui)
