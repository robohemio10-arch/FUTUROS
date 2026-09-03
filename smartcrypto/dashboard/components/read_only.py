from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from smartcrypto.dashboard.security.dashboard_readonly_guard import (
    assert_dashboard_readonly,
    build_readonly_audit_footer,
    get_global_banners,
)
from smartcrypto.dashboard.ui.footer import render_footer_audit_bar
from smartcrypto.dashboard.ui.cards import render_metric_card
from smartcrypto.dashboard.ui.layout import render_readonly_banner as render_theme_readonly_banner
from smartcrypto.dashboard.ui.states import render_unknown_state as unknown_state_html

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
    render_theme_readonly_banner(ui=ui)


def render_global_safety_badges(*, ui: Any) -> None:
    ui.caption(" | ".join(get_global_banners()))


def render_unknown_state(message: str, *, ui: Any) -> None:
    ui.markdown(unknown_state_html(message), unsafe_allow_html=True)


def render_missing_snapshot_state(snapshot_path: str, *, ui: Any) -> None:
    render_unknown_state(
        f"Snapshot ausente ou inválido: {snapshot_path}. Execute o pipeline externo de snapshots.",
        ui=ui,
    )


def render_disabled_control_stub(label: str, reason: str, *, ui: Any) -> None:
    ui.markdown(
        render_metric_card(
            label,
            f"disabled ({reason})",
            status="disabled",
            helper=f"{label}: disabled ({reason})",
            size="sm",
        ),
        unsafe_allow_html=True,
    )


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
    render_chrome: bool = True,
) -> None:
    target_ui = ui or get_streamlit()
    assert_dashboard_readonly(snapshot)
    if render_chrome:
        render_readonly_banner(ui=target_ui)
        render_global_safety_badges(ui=target_ui)
        render_snapshot_header(title, snapshot, ui=target_ui)
    else:
        render_snapshot_header_metadata(snapshot, ui=target_ui)
    if str(snapshot.get("status", "")).upper() == "UNKNOWN":
        render_missing_snapshot_state(snapshot_path, ui=target_ui)
    render_metric_cards(snapshot, metric_specs, ui=target_ui)
    sections = snapshot.get("sections")
    effective_section_order = _section_order_with_aibot_parity(
        sections,
        section_order,
    )
    target_ui.subheader("Status das seções")
    render_section_status_table(
        sections,
        ui=target_ui,
        section_order=effective_section_order,
    )
    render_section_details(
        sections,
        ui=target_ui,
        section_order=effective_section_order,
    )
    if render_chrome:
        render_audit_footer(snapshot.get("audit"), ui=target_ui)
        render_footer_audit_bar(snapshot_path, ui=target_ui)


def _section_order_with_aibot_parity(
    sections: Any,
    section_order: Sequence[str],
) -> tuple[str, ...]:
    """Expose W12/W13 projection only when a builder materializes it.

    Existing pages keep their canonical order and behavior when the E2E projection is
    absent.  The dashboard remains a read-only presenter and never creates the section.
    """

    ordered = list(section_order)
    if not isinstance(sections, Mapping) or "aibot_parity" not in sections:
        return tuple(ordered)
    if "aibot_parity" in ordered:
        return tuple(ordered)
    try:
        audit_index = ordered.index("audit")
    except ValueError:
        ordered.append("aibot_parity")
    else:
        ordered.insert(audit_index, "aibot_parity")
    return tuple(ordered)


def render_snapshot_header_metadata(snapshot: Mapping[str, Any], *, ui: Any) -> None:
    status_summary = snapshot.get("status_summary")
    status = status_summary.get("status") if isinstance(status_summary, Mapping) else None
    status = status or snapshot.get("overall_status") or snapshot.get("status") or "UNKNOWN"
    from .status_badges import render_status_badge

    render_status_badge(status, ui=ui)
    ui.caption(f"Last updated UTC: {snapshot.get('last_updated_utc') or 'UNKNOWN'}")
    ui.caption(f"Schema: {snapshot.get('schema_version') or 'UNKNOWN'}")
