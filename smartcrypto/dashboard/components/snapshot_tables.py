from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from smartcrypto.dashboard.ui.tables import render_html_table

from .snapshot_cards import render_key_value_grid


def render_section_status_table(
    sections: Any,
    *,
    ui: Any,
    section_order: Sequence[str] = (),
) -> None:
    section_map = sections if isinstance(sections, Mapping) else {}
    names = list(section_order) or [str(name) for name in section_map]
    rows = []
    for name in names:
        payload = section_map.get(name)
        row = payload if isinstance(payload, Mapping) else {}
        rows.append(
            {
                "Section": name,
                "Status": str(row.get("status") or "UNKNOWN"),
                "Reason": str(row.get("reason") or "not_available"),
            }
        )
    if rows:
        ui.markdown(
            render_html_table(
                rows,
                columns=["Section", "Status", "Reason"],
                status_columns=["Status"],
            ),
            unsafe_allow_html=True,
        )
        with ui.expander("Raw section status", expanded=False):
            ui.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        ui.info("Nenhuma seção disponível no snapshot.")


def render_section_details(
    sections: Any,
    *,
    ui: Any,
    section_order: Sequence[str] = (),
) -> None:
    section_map = sections if isinstance(sections, Mapping) else {}
    names = list(section_order) or [str(name) for name in section_map]
    for name in names:
        payload = section_map.get(name)
        label = name.translate(str.maketrans({"_": " "})).title()
        with ui.expander(label, expanded=False):
            render_key_value_grid(payload if isinstance(payload, Mapping) else {}, ui=ui)
