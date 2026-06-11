from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from smartcrypto.dashboard.ui.cards import render_metric_card
from smartcrypto.dashboard.ui.status import normalize_status

from .status_badges import render_status_badge


MetricSpec = tuple[str, str, str]


def render_snapshot_header(title: str, snapshot: Mapping[str, Any], *, ui: Any) -> None:
    ui.title(title)
    status_summary = snapshot.get("status_summary")
    status = status_summary.get("status") if isinstance(status_summary, Mapping) else None
    status = status or snapshot.get("overall_status") or snapshot.get("status") or "UNKNOWN"
    render_status_badge(status, ui=ui)
    ui.caption(f"Last updated UTC: {snapshot.get('last_updated_utc') or 'UNKNOWN'}")
    ui.caption(f"Schema: {snapshot.get('schema_version') or 'UNKNOWN'}")


def render_metric_cards(
    snapshot: Mapping[str, Any],
    metric_specs: Sequence[MetricSpec],
    *,
    ui: Any,
) -> None:
    if not metric_specs:
        return
    values = [(_label, _snapshot_value(snapshot, section, key)) for _label, section, key in metric_specs]
    for start in range(0, len(values), 4):
        chunk = values[start : start + 4]
        columns = ui.columns(len(chunk))
        for column, (label, value) in zip(columns, chunk, strict=True):
            status = normalize_status(value) if isinstance(value, str) else "neutral"
            column.markdown(
                render_metric_card(label, format_display_value(value), status=status),
                unsafe_allow_html=True,
            )


def render_key_value_grid(data: Any, *, ui: Any) -> None:
    if not isinstance(data, Mapping) or not data:
        ui.info("Sem dados estruturados disponíveis neste bloco.")
        return
    rows = [
        {"Campo": str(key), "Valor": format_display_value(value)}
        for key, value in data.items()
        if key not in {"status", "reason"}
    ]
    if rows:
        ui.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        ui.info("Sem métricas adicionais disponíveis neste bloco.")


def format_display_value(value: Any) -> str:
    if value is None or value == "":
        return "UNKNOWN"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:,.4f}"
    if isinstance(value, (list, tuple, set)):
        return f"{len(value)} item(s)"
    if isinstance(value, Mapping):
        return f"{len(value)} field(s)"
    return str(value)


def _snapshot_value(snapshot: Mapping[str, Any], section_name: str, key: str) -> Any:
    sections = snapshot.get("sections")
    if not isinstance(sections, Mapping):
        return None
    section = sections.get(section_name)
    if not isinstance(section, Mapping):
        return None
    return section.get(key)
