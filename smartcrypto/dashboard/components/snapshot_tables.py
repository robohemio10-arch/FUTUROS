from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

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
        with ui.expander(name.replace("_", " ").title(), expanded=False):
            render_key_value_grid(payload if isinstance(payload, Mapping) else {}, ui=ui)
